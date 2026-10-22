"""PHX-046 permissioned read-only filesystem steps for the PHOENIX mission runtime.

A document step makes at most one tool call between two generations:

1. The document specialist may only propose one ``{"tool_request": ...}``.
   Both document turns use the structured (thinking off) binding (PHX-047).
2. PHOENIX checks the proposal against the read-only allowlist and builds the
   ``FilesystemCall`` and ``PermissionRequest`` itself. FilesystemTool decides
   through ToolRegistry and PermissionEngine (INV-004) and runs off the loop.
3. The answer turn sees the result only as guarded ``TOOL_OUTPUT`` data (INV-017).

Grants come only from the runtime caller, never from model text (INV-003). Only
P1 operations are reachable, so no audit rows are written (INV-007 covers P3/P4).
Tool output is never logged or traced: evidence keeps IDs, statuses, hashes and
lengths only. LEVI checks the tool evidence deterministically before any model
check (INV-008). PHX-047 grounds LEVI's model check in the same guarded
``TOOL_OUTPUT`` item the answer turn saw, and grounds the intermediate text-step
and final-answer checks in the guarded verified step outputs those steps saw.
LEVI's check objects drop unknown keys (never trusted, stored or used); the top
level stays strict. Failures are explicit (INV-021); there are no retries.

PHX-103 (ADR-087): with the mission's events, a document step publishes
tool.requested once PHOENIX has built the call, tool.started as the call enters
ToolRegistry, then exactly one of tool.completed, tool.failed or
permission.requested. A proposal rejected before a call exists publishes only
tool.failed. Payloads hold IDs, statuses, codes and timings, never the path,
the proposal or the output.
"""

import asyncio
import json
import time
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
from phoenix.agents.mission_events import MissionEvents, elapsed_ms
from phoenix.agents.responders import (
    ModelBinding,
    ModelVerificationResponder,
    ResponderErrorCode,
    ResponderOutputError,
    _generate,
    parse_json_object,
)
from phoenix.agents.verifier import (
    VerificationCheck,
    VerificationDraft,
    VerificationRequest,
)
from phoenix.core.events import EventType
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


class _LenientCheckWire(BaseModel):
    """One LEVI check: strict required keys; unknown keys are dropped unread."""

    model_config = ConfigDict(frozen=True, extra="ignore", strict=True)

    criterion: _Short
    passed: bool
    evidence_summary: _Short


class _LenientChecksWire(_WireModel):
    """Exactly one object holding a non-empty checks list; nothing else."""

    checks: list[_LenientCheckWire] = Field(min_length=1)


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
    if result.output is None:
        raise ToolStepFailed("Workspace request failed")
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
        events: MissionEvents | None = None,
    ) -> tuple[str, ToolEvidence]:
        model = UntrustedContentOrigin.MODEL_OUTPUT
        step_id = handoff.step_id

        async def emit(event_type: EventType, **payload: str | int) -> None:
            if events is not None:
                await events.emit(
                    event_type, agent_id=DOCUMENT_ACTOR_ID, step_id=step_id, **payload
                )

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
        # The caller passes the structured binding: the proposal is one JSON
        # object, and the answer turn must not spend its budget thinking.
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
        wire = parse_json_object(proposal, _ToolRequestEnvelope).tool_request
        try:
            call, intent = self._call(wire)
        except ToolStepRejected:
            await emit(EventType.TOOL_FAILED, code="tool_rejected")
            raise
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
        ids = {"tool_id": intent.tool_id, "call_id": call.call_id}
        await emit(
            EventType.TOOL_REQUESTED,
            **ids,
            operation=call.operation.value,
            permission_request_id=request.request_id,
        )
        await emit(EventType.TOOL_STARTED, **ids)
        clock = time.monotonic()
        try:
            # Synchronous, bounded and read-only; a cancelled await leaves no effect.
            result = FilesystemToolResult.model_validate(
                await asyncio.to_thread(
                    self._tool.execute, call, request, grants=grants
                )
            )
        except BaseException as interrupted:
            code = (
                "cancelled"
                if isinstance(interrupted, asyncio.CancelledError)
                else "internal_error"
            )
            await emit(
                EventType.TOOL_FAILED, **ids, code=code, duration_ms=elapsed_ms(clock)
            )
            raise
        ended = {**ids, "tool_status": result.status.value}
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
            await emit(
                EventType.PERMISSION_REQUESTED,
                **ids,
                permission_request_id=request.request_id,
                risk_level=intent.risk_level.value,
            )
            raise ToolPermissionRequired(request)
        failure: tuple[str, Exception] | None = None
        if status != PermissionStatus.ALLOWED:
            failure = "tool_rejected", ToolStepRejected("Permission was not granted")
        elif content is None or result.status != ToolResultStatus.SUCCESS:
            # FAILURE, or a truncated PARTIAL_SUCCESS listing LEVI would reject.
            failure = "tool_failed", ToolStepFailed("Workspace request failed")
        elif len(content[1]) > binding.max_output_chars:
            failure = (
                "tool_failed",
                ToolStepFailed("Tool output exceeds the configured limit"),
            )
        if failure is not None:
            code, error = failure
            await emit(
                EventType.TOOL_FAILED,
                **ended,
                code=code,
                decision=status.value,
                duration_ms=elapsed_ms(clock),
            )
            raise error
        assert content is not None
        field, text = content
        await emit(EventType.TOOL_COMPLETED, **ended, duration_ms=elapsed_ms(clock))
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
                        (objective, _tool_output_item(call, field, text), *prior),
                    ),
                )
            ),
        )
        return answer, ToolEvidence(call=call, request=request, result=result)


def _tool_output_item(call: FilesystemCall, field: str, text: str) -> _Item:
    """The one guarded TOOL_OUTPUT item, identical for the answer turn and LEVI."""
    return (
        prompts.TOOL_OUTPUT_ITEM,
        UntrustedContentOrigin.TOOL_OUTPUT,
        call.call_id,
        field,
        text,
    )


class DocumentVerificationResponder(ModelVerificationResponder):
    """LEVI's model verifier for one document step, grounded in its tool output.

    PHX-047 (ADR-083): the same deterministic plain-text and size checks run
    first, and the model is skipped if any fails. The model then sees the step's
    tool output only as the guarded TOOL_OUTPUT item the answer turn saw, next
    to the guarded objective and candidate. The verdict stays LEVI's all-checks
    rule, so the model cannot override a deterministic failure. Wrap it in
    ToolEvidenceVerificationResponder so the tool evidence is checked first.
    """

    def __init__(
        self,
        binding: ModelBinding,
        guard: PromptInjectionGuard,
        *,
        original_request: str,
        evidence: ToolEvidence,
        max_checks: int = 6,
    ) -> None:
        super().__init__(
            binding,
            guard,
            original_request=original_request,
            plain_text_required=True,
            max_checks=max_checks,
        )
        self._evidence = evidence

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        checks = self._deterministic(request.candidate_output)
        if not all(check.passed for check in checks):
            return VerificationDraft(
                verification_id=request.verification_id, checks=checks
            )
        field, text = _rendered(self._evidence.result)
        model = UntrustedContentOrigin.MODEL_OUTPUT
        system = prompts.DOCUMENT_VERIFICATION_SYSTEM.format(
            max_checks=self._max_checks
        )
        user = "\n\n".join(
            (
                f"Expected output:\n{request.expected_output_schema}",
                f"Completion rule:\n{request.completion_rule}",
                _guarded(
                    self._guard,
                    request.verification_id,
                    prompts.DOCUMENT_CANDIDATE_TASK,
                    (
                        (
                            prompts.OBJECTIVE_ITEM,
                            model,
                            prompts.OBJECTIVE_ITEM,
                            "output",
                            request.objective,
                        ),
                        _tool_output_item(self._evidence.call, field, text),
                        (
                            prompts.CANDIDATE_ITEM,
                            model,
                            prompts.CANDIDATE_ITEM,
                            "output",
                            request.candidate_output,
                        ),
                    ),
                ),
            )
        )
        return _model_checks(
            request,
            checks,
            await _generate(self._binding, "document_verifier", system, user),
            self._max_checks,
        )


class StepVerificationResponder(ModelVerificationResponder):
    """LEVI's model verifier for one intermediate text step (PHX-047, ADR-083).

    The plain-text and size checks run first and the model is skipped if any
    fails. With no earlier step outputs the prompt is exactly the text
    verifier's (VERIFICATION_SYSTEM, role "verifier"). With earlier outputs the
    step_verifier sees them only as guarded MODEL_OUTPUT items, the same items
    the step itself saw, next to the guarded objective and candidate. Either way
    the checks are parsed leniently per check, and the verdict stays LEVI's
    all-checks rule.
    """

    def __init__(
        self,
        binding: ModelBinding,
        guard: PromptInjectionGuard,
        *,
        original_request: str,
        evidence: tuple[tuple[str, str], ...],
        max_checks: int = 6,
    ) -> None:
        super().__init__(
            binding,
            guard,
            original_request=original_request,
            plain_text_required=True,
            max_checks=max_checks,
        )
        self._step_outputs = evidence

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        checks = self._deterministic(request.candidate_output)
        if not all(check.passed for check in checks):
            return VerificationDraft(
                verification_id=request.verification_id, checks=checks
            )
        model = UntrustedContentOrigin.MODEL_OUTPUT
        if self._step_outputs:
            role, system, task = (
                "step_verifier",
                prompts.STEP_VERIFICATION_SYSTEM,
                prompts.STEP_CANDIDATE_TASK,
            )
        else:
            role, system, task = (
                "verifier",
                prompts.VERIFICATION_SYSTEM,
                prompts.CANDIDATE_TASK,
            )
        user = "\n\n".join(
            (
                f"Original request:\n{self._original_request}",
                f"Expected output:\n{request.expected_output_schema}",
                f"Completion rule:\n{request.completion_rule}",
                _guarded(
                    self._guard,
                    request.verification_id,
                    task,
                    (
                        (
                            prompts.OBJECTIVE_ITEM,
                            model,
                            prompts.OBJECTIVE_ITEM,
                            "output",
                            request.objective,
                        ),
                        *(
                            (identity, model, identity, "output", text)
                            for identity, text in self._step_outputs
                        ),
                        (
                            prompts.CANDIDATE_ITEM,
                            model,
                            prompts.CANDIDATE_ITEM,
                            "output",
                            request.candidate_output,
                        ),
                    ),
                ),
            )
        )
        return _model_checks(
            request,
            checks,
            await _generate(
                self._binding,
                role,
                system.format(max_checks=self._max_checks),
                user,
            ),
            self._max_checks,
        )


class FinalVerificationResponder(ModelVerificationResponder):
    """LEVI's model verifier for the final answer, grounded in the step outputs.

    PHX-047 (ADR-083): the size check runs first and the model is skipped if it
    fails; the final answer stays exempt from the plain-text check. The model
    then sees each verified step output only as a guarded MODEL_OUTPUT item,
    the same items the final-answer turn saw, next to the guarded objective and
    candidate. The verdict stays LEVI's all-checks rule.
    """

    def __init__(
        self,
        binding: ModelBinding,
        guard: PromptInjectionGuard,
        *,
        original_request: str,
        evidence: tuple[tuple[str, str], ...],
        max_checks: int = 6,
    ) -> None:
        super().__init__(
            binding,
            guard,
            original_request=original_request,
            plain_text_required=False,
            max_checks=max_checks,
        )
        self._step_outputs = evidence

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        checks = self._deterministic(request.candidate_output)
        if not all(check.passed for check in checks):
            return VerificationDraft(
                verification_id=request.verification_id, checks=checks
            )
        model = UntrustedContentOrigin.MODEL_OUTPUT
        system = prompts.FINAL_VERIFICATION_SYSTEM.format(max_checks=self._max_checks)
        user = "\n\n".join(
            (
                f"Original request:\n{self._original_request}",
                f"Expected output:\n{request.expected_output_schema}",
                f"Completion rule:\n{request.completion_rule}",
                _guarded(
                    self._guard,
                    request.verification_id,
                    prompts.FINAL_CANDIDATE_TASK,
                    (
                        (
                            prompts.OBJECTIVE_ITEM,
                            model,
                            prompts.OBJECTIVE_ITEM,
                            "output",
                            request.objective,
                        ),
                        *(
                            (identity, model, identity, "output", text)
                            for identity, text in self._step_outputs
                        ),
                        (
                            prompts.CANDIDATE_ITEM,
                            model,
                            prompts.CANDIDATE_ITEM,
                            "output",
                            request.candidate_output,
                        ),
                    ),
                ),
            )
        )
        return _model_checks(
            request,
            checks,
            await _generate(self._binding, "final_verifier", system, user),
            self._max_checks,
        )


def _model_checks(
    request: VerificationRequest,
    checks: tuple[VerificationCheck, ...],
    reply: str,
    max_checks: int,
) -> VerificationDraft:
    """Deterministic checks first, then the strictly parsed model checks."""
    wire = parse_json_object(reply, _LenientChecksWire)
    if len(wire.checks) > max_checks:
        raise ResponderOutputError(ResponderErrorCode.SCHEMA)
    return VerificationDraft(
        verification_id=request.verification_id,
        checks=(
            *checks,
            *(
                VerificationCheck(
                    check_id=f"model.{index}",
                    criterion=check.criterion,
                    passed=check.passed,
                    evidence_summary=check.evidence_summary,
                )
                for index, check in enumerate(wire.checks, start=1)
            ),
        ),
    )


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
