"""PHX-046 permissioned read-only filesystem steps for the PHOENIX mission runtime.

A document step makes at most one tool call between two generations:

1. The document specialist may only propose one ``{"tool_request": ...}``.
2. PHOENIX checks the proposal against the read-only allowlist and builds the
   ``FilesystemCall`` and ``PermissionRequest`` itself. FilesystemTool decides
   through ToolRegistry and PermissionEngine (INV-004) and runs off the loop.
3. The answer turn sees the result only as guarded ``TOOL_OUTPUT`` data (INV-017).

Grants come only from the runtime caller, never from model text (INV-003). Only
P1 operations are reachable, so no audit rows are written (INV-007 covers P3/P4).
Tool output is never logged or traced: evidence keeps IDs, statuses, hashes and
lengths only. LEVI checks the tool evidence deterministically before any model
check (INV-008). Failures are explicit (INV-021); there are no retries.
"""

import asyncio
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, cast
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

from phoenix.agents import prompts
from phoenix.agents.handoff import HandoffEnvelope
from phoenix.agents.responders import (
    ModelBinding,
    ModelVerificationResponder,
    _generate,
    parse_json_object,
)
from phoenix.agents.verifier import (
    VerificationCheck,
    VerificationDraft,
    VerificationRequest,
)
from phoenix.models.provider import Identifier, NonnegativeInt
from phoenix.security.permissions import (
    DecisionReason,
    GrantDuration,
    PermissionGrant,
    PermissionRequest,
    PermissionStatus,
)
from phoenix.security.prompt_injection import (
    PromptBoundaryRequest,
    PromptInjectionGuard,
    UntrustedContentItem,
    UntrustedContentOrigin,
)
from phoenix.tools.filesystem import (
    FilesystemCall,
    FilesystemInputError,
    FilesystemIntent,
    FilesystemOperation,
    FilesystemTool,
    FilesystemToolResult,
)
from phoenix.tools.registry import ToolResultStatus, ToolRiskLevel

DOCUMENT_ACTOR_ID = "document_analyst"
# Fixed so caller grants can match exactly; never derived from model text.
DOCUMENT_TOOL_REASON = "PHOENIX document step: read-only workspace access"
DOCUMENT_OPERATIONS: tuple[FilesystemOperation, ...] = (
    FilesystemOperation.READ_TEXT,
    FilesystemOperation.LIST_DIRECTORY,
    FilesystemOperation.STAT,
)
DOCUMENT_TOOL_IDS: tuple[str, ...] = tuple(
    f"filesystem.{operation.value}" for operation in DOCUMENT_OPERATIONS
)
# Bounded so a listing renders well under the guard and output caps.
MAX_LIST_ENTRIES = 50
_GRANT_DURATIONS = (GrantDuration.SESSION, GrantDuration.PERSISTENT)


class ToolStepRejected(Exception):
    """The proposal is outside the read-only P1 allowlist; nothing was executed."""


class ToolStepFailed(Exception):
    """FilesystemTool reported an explicit failure; read-only, so no effect."""


class ToolPermissionRequired(Exception):
    """PermissionEngine requires approval; the request was not attempted."""

    def __init__(self, request: PermissionRequest) -> None:
        super().__init__("Tool permission approval required")
        self.request = request


def document_grants(grants: object) -> tuple[PermissionGrant, ...]:
    """Caller grants for document steps only: SESSION or PERSISTENT, P1, exact."""
    if type(grants) not in (tuple, list):
        raise ValueError("Grants require an ordered sequence")
    accepted: list[PermissionGrant] = []
    for grant in cast(Sequence[object], grants):
        if type(grant) is not PermissionGrant:
            raise ValueError("Expected PermissionGrant instances")
        grant = PermissionGrant.model_validate(grant)
        if (
            grant.duration not in _GRANT_DURATIONS
            or grant.risk_level != ToolRiskLevel.P1
            or grant.actor_id != DOCUMENT_ACTOR_ID
            or grant.reason != DOCUMENT_TOOL_REASON
            or grant.tool_id not in DOCUMENT_TOOL_IDS
            or grant.action != grant.tool_id.removeprefix("filesystem.")
        ):
            raise ValueError("Grant is outside the document step scope")
        accepted.append(grant)
    # Mirror PermissionEngine's uniqueness rule so duplicates fail before the
    # mission starts instead of inside the tool call.
    ids = [grant.grant_id for grant in accepted]
    scopes = [
        (
            grant.tool_id,
            grant.action,
            grant.resource,
            grant.duration,
            grant.session_id,
        )
        for grant in accepted
    ]
    if len(set(ids)) != len(ids) or len(set(scopes)) != len(scopes):
        raise ValueError("Duplicate grant ID or effective scope")
    return tuple(accepted)


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


class ToolCallRecord(_Value):
    """Privacy-preserving evidence of one tool call: IDs, statuses and hashes."""

    step_id: Identifier
    call_id: Identifier
    request_id: Identifier
    tool_id: Identifier
    operation: FilesystemOperation
    decision_status: PermissionStatus
    tool_status: ToolResultStatus
    attempted: bool = Field(strict=True)
    effect_applied: bool = Field(strict=True)
    output_chars: NonnegativeInt | None = None
    output_sha256: str | None = Field(
        default=None, strict=True, pattern=r"^[0-9a-f]{64}$"
    )


@dataclass(frozen=True)
class ToolEvidence:
    """What PHOENIX issued and what the tool returned, for LEVI's correlation."""

    call: FilesystemCall
    request: PermissionRequest
    result: FilesystemToolResult


class _WireModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


_Short = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class _ToolRequestWire(_WireModel):
    operation: _Short
    relative_path: _Short


class _ToolRequestEnvelope(_WireModel):
    tool_request: _ToolRequestWire


_Item = tuple[str, UntrustedContentOrigin, str, str, str]


def _guarded(
    guard: PromptInjectionGuard, boundary_id: str, task: str, items: Sequence[_Item]
) -> str:
    """Model and tool output enter prompts only as untrusted guarded data."""
    return guard.guard(
        PromptBoundaryRequest(
            boundary_id=boundary_id,
            user_task=task,
            items=tuple(
                UntrustedContentItem(
                    content_id=identity,
                    origin=origin,
                    source_id=source,
                    field_name=field,
                    text=text,
                    text_sha256=sha256(text.encode("utf-8")).hexdigest(),
                )
                for identity, origin, source, field, text in items
            ),
        )
    ).render_text()


def _rendered(result: FilesystemToolResult) -> tuple[str, str]:
    """The tool output as (field name, text); content is never interpreted."""
    output = result.model_dump(mode="json")["output"]
    if result.operation == FilesystemOperation.READ_TEXT:
        return "content", str(output["content"])
    return "output", json.dumps(output, ensure_ascii=False, sort_keys=True)


class DocumentStepRunner:
    """One document step: propose, permission-check and run, then answer."""

    def __init__(
        self,
        tool: FilesystemTool,
        guard: PromptInjectionGuard,
        *,
        max_read_bytes: int,
    ) -> None:
        self._tool = tool
        self._guard = guard
        self._max_read_bytes = max_read_bytes

    def _call(self, wire: _ToolRequestWire) -> tuple[FilesystemCall, FilesystemIntent]:
        allowed = {operation.value: operation for operation in DOCUMENT_OPERATIONS}
        operation = allowed.get(wire.operation)
        if operation is None:
            raise ToolStepRejected("Operation is not on the read-only allowlist")
        fields: dict[str, object] = {
            "operation": operation,
            "relative_path": wire.relative_path,
        }
        if operation == FilesystemOperation.READ_TEXT:
            fields["max_bytes"] = self._max_read_bytes
        elif operation == FilesystemOperation.LIST_DIRECTORY:
            fields["max_entries"] = MAX_LIST_ENTRIES
        try:
            call = FilesystemCall.model_validate(fields)
            intent = self._tool.describe(call)
        except (ValidationError, FilesystemInputError):
            raise ToolStepRejected("Invalid workspace request") from None
        if intent.risk_level != ToolRiskLevel.P1 or intent.tool_id not in (
            DOCUMENT_TOOL_IDS
        ):
            raise ToolStepRejected("Operation is not read-only P1")
        return call, intent

    async def perform(
        self,
        binding: ModelBinding,
        handoff: HandoffEnvelope,
        *,
        user_request: str,
        evidence: tuple[tuple[str, str], ...],
        grants: tuple[PermissionGrant, ...],
        session_id: str | None,
        record: Callable[[ToolCallRecord], None],
    ) -> tuple[str, ToolEvidence]:
        model = UntrustedContentOrigin.MODEL_OUTPUT
        objective: _Item = (
            prompts.OBJECTIVE_ITEM,
            model,
            prompts.OBJECTIVE_ITEM,
            "output",
            handoff.objective,
        )
        prior = tuple(
            (identity, model, identity, "output", text) for identity, text in evidence
        )
        fixed = (
            f"Original request:\n{user_request}",
            f"Expected output:\n{handoff.expected_output_schema}",
        )
        boundary = f"{handoff.task_id}:{handoff.step_id}"
        proposal = await _generate(
            binding,
            "document_specialist",
            prompts.DOCUMENT_SPECIALIST_SYSTEM,
            "\n\n".join(
                (
                    *fixed,
                    _guarded(
                        self._guard,
                        f"{boundary}:tool",
                        prompts.DOCUMENT_TOOL_TASK,
                        (objective, *prior),
                    ),
                )
            ),
        )
        call, intent = self._call(
            parse_json_object(proposal, _ToolRequestEnvelope).tool_request
        )
        request = PermissionRequest(
            request_id=str(uuid4()),
            actor_id=DOCUMENT_ACTOR_ID,
            tool_id=intent.tool_id,
            action=intent.action,
            resource=intent.resource,
            reason=DOCUMENT_TOOL_REASON,
            requested_at=datetime.now(UTC),
            task_id=handoff.task_id,
            session_id=session_id,
        )
        # Synchronous, bounded and read-only; a cancelled await leaves no effect.
        result = FilesystemToolResult.model_validate(
            await asyncio.to_thread(self._tool.execute, call, request, grants=grants)
        )
        status = result.permission_decision.status
        content: tuple[str, str] | None = None
        if status == PermissionStatus.ALLOWED and result.output is not None:
            content = _rendered(result)
        record(
            ToolCallRecord(
                step_id=handoff.step_id,
                call_id=call.call_id,
                request_id=request.request_id,
                tool_id=intent.tool_id,
                operation=call.operation,
                decision_status=status,
                tool_status=result.status,
                attempted=result.attempted,
                effect_applied=result.effect_applied,
                output_chars=None if content is None else len(content[1]),
                output_sha256=None
                if content is None
                else sha256(content[1].encode("utf-8")).hexdigest(),
            )
        )
        if status == PermissionStatus.APPROVAL_REQUIRED:
            raise ToolPermissionRequired(request)
        if status != PermissionStatus.ALLOWED:
            raise ToolStepRejected("Permission was not granted")
        if content is None or result.status != ToolResultStatus.SUCCESS:
            # FAILURE, or a truncated PARTIAL_SUCCESS listing LEVI would reject.
            raise ToolStepFailed("Workspace request failed")
        field, text = content
        if len(text) > binding.max_output_chars:
            raise ToolStepFailed("Tool output exceeds the configured limit")
        answer = await _generate(
            binding,
            "document_answer",
            prompts.DOCUMENT_ANSWER_SYSTEM,
            "\n\n".join(
                (
                    *fixed,
                    _guarded(
                        self._guard,
                        boundary,
                        prompts.DOCUMENT_ANSWER_TASK,
                        (
                            objective,
                            (
                                prompts.TOOL_OUTPUT_ITEM,
                                UntrustedContentOrigin.TOOL_OUTPUT,
                                call.call_id,
                                field,
                                text,
                            ),
                            *prior,
                        ),
                    ),
                )
            ),
        )
        return answer, ToolEvidence(call=call, request=request, result=result)


class ToolEvidenceVerificationResponder:
    """LEVI responder for document steps: tool evidence first, then the model.

    A failed deterministic tool-evidence check is final: the model verifier is
    not consulted and cannot override it.
    """

    def __init__(
        self,
        inner: ModelVerificationResponder,
        evidence: ToolEvidence,
        grants: tuple[PermissionGrant, ...],
    ) -> None:
        self._inner = inner
        self._evidence = evidence
        self._grant_ids = frozenset(grant.grant_id for grant in grants)

    def _check(self) -> VerificationCheck:
        call, request, result = (
            self._evidence.call,
            self._evidence.request,
            self._evidence.result,
        )
        decision = result.permission_decision
        passed = (
            result.call_id == call.call_id
            and result.operation == call.operation
            and decision.request_id == request.request_id
            and decision.actor_id == request.actor_id == DOCUMENT_ACTOR_ID
            and result.tool_id == decision.tool_id == request.tool_id
            and result.tool_id in DOCUMENT_TOOL_IDS
            and result.resource == request.resource
            and decision.risk_level == ToolRiskLevel.P1
            and decision.status == PermissionStatus.ALLOWED
            and decision.reason_code == DecisionReason.MATCHING_GRANT
            and decision.matched_grant_id in self._grant_ids
            and result.attempted is True
            and result.effect_applied is False
            and result.status == ToolResultStatus.SUCCESS
        )
        return VerificationCheck(
            check_id="deterministic.tool_evidence",
            criterion="Tool evidence correlates with the PHOENIX-issued read-only call",
            passed=passed,
            evidence_summary="Allowed by a caller grant; read-only success correlates"
            if passed
            else "Tool evidence does not correlate or is not an allowed success",
        )

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        check = self._check()
        if not check.passed:
            return VerificationDraft(
                verification_id=request.verification_id, checks=(check,)
            )
        draft = await self._inner.verify(request)
        return VerificationDraft(
            verification_id=draft.verification_id, checks=(check, *draft.checks)
        )
