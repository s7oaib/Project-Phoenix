"""Model-backed responders behind the neutral PHOENIX/AIZEN/LEVI protocols.

Responders depend only on the ModelProvider contract and the global lifecycle
lease (INV-012, INV-023). Model text is untrusted data: it is parsed strictly and
can never supply identifiers, verdicts, permissions or tool authority. Responders
never retry, never log prompts or outputs, and propagate every failure (INV-021).
Callers own deadlines; cancellation is forwarded to the provider best-effort.
"""

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

from phoenix.agents import prompts
from phoenix.agents.handoff import HandoffEnvelope
from phoenix.agents.orchestrator import DirectResponse, PhoenixRequest
from phoenix.agents.planner import AizenPlan, PlanDraft, PlanningRequest, PlanStep
from phoenix.agents.router import RoutingDomain
from phoenix.agents.verifier import (
    VerificationCheck,
    VerificationDraft,
    VerificationRequest,
)
from phoenix.models.lifecycle import ModelLifecycleManager
from phoenix.models.provider import (
    GenerationRequest,
    Identifier,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelProviderError,
    NonnegativeFloat,
    NonnegativeInt,
)
from phoenix.security.prompt_injection import (
    PromptBoundaryRequest,
    PromptInjectionGuard,
    UntrustedContentItem,
    UntrustedContentOrigin,
)


class ResponderErrorCode(StrEnum):
    TRUNCATED = "truncated"
    OVERSIZED = "oversized"
    NOT_JSON = "not_json"
    SCHEMA = "schema"
    UNKNOWN_STEP = "unknown_step"
    DISALLOWED_DOMAIN = "disallowed_domain"
    INVALID_TEXT = "invalid_text"


class ResponderOutputError(Exception):
    """Model output was rejected; the code is safe to report, the text is not."""

    def __init__(self, code: ResponderErrorCode) -> None:
        super().__init__(f"Responder output rejected: {code.value}")
        self.code = code


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


class GenerationRecord(_Value):
    """Privacy-preserving evidence of one generation: hashes and counts only."""

    role: Identifier
    agent_id: Identifier
    provider_id: Identifier
    model_id: Identifier
    operation_id: Identifier
    finish_reason: Identifier | None = None
    input_tokens: NonnegativeInt | None = None
    output_tokens: NonnegativeInt | None = None
    duration_ms: NonnegativeFloat | None = None
    output_chars: NonnegativeInt
    output_sha256: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ModelBinding:
    """One selected model for one agent role; selection belongs to the caller."""

    agent_id: str
    provider: ModelProvider
    model_id: str
    lifecycle: ModelLifecycleManager
    max_output_tokens: int
    max_output_chars: int
    record: Callable[[GenerationRecord], None]


async def _generate(binding: ModelBinding, role: str, system: str, user: str) -> str:
    provider = binding.provider
    request = GenerationRequest(
        model_id=binding.model_id,
        messages=(
            ModelMessage(role=MessageRole.SYSTEM, text=system),
            ModelMessage(role=MessageRole.USER, text=user),
        ),
        max_output_tokens=binding.max_output_tokens,
    )
    async with await binding.lifecycle.acquire_heavy(
        provider_id=provider.provider_id,
        model_id=request.model_id,
        operation_id=request.operation_id,
    ):
        try:
            response = await provider.generate(request)
        except asyncio.CancelledError:
            # Best-effort: an acknowledgement is not proof remote work stopped.
            try:
                await asyncio.shield(provider.cancel(request.operation_id))
            except (asyncio.CancelledError, Exception):
                pass
            raise
    if (response.operation_id, response.provider_id, response.model_id) != (
        request.operation_id,
        provider.provider_id,
        request.model_id,
    ):
        raise ModelProviderError("Provider response identity mismatch")
    text = response.text
    usage = response.usage
    # Lone surrogates cannot be UTF-8 encoded; hash them reversibly for evidence
    # and reject outside any handler, so no exception carries the raw text.
    try:
        text.encode("utf-8")
        encodable = True
    except UnicodeEncodeError:
        encodable = False
    binding.record(
        GenerationRecord(
            role=role,
            agent_id=binding.agent_id,
            provider_id=response.provider_id,
            model_id=response.model_id,
            operation_id=response.operation_id,
            finish_reason=response.finish_reason,
            input_tokens=None if usage is None else usage.input_tokens,
            output_tokens=None if usage is None else usage.output_tokens,
            duration_ms=None
            if response.metrics is None
            else response.metrics.duration_ms,
            output_chars=len(text),
            output_sha256=sha256(text.encode("utf-8", "surrogatepass")).hexdigest(),
        )
    )
    if response.finish_reason == "length" or not text.strip():
        raise ResponderOutputError(ResponderErrorCode.TRUNCATED)
    if not encodable:
        raise ResponderOutputError(ResponderErrorCode.INVALID_TEXT)
    if len(text) > binding.max_output_chars:
        raise ResponderOutputError(ResponderErrorCode.OVERSIZED)
    return text


_FENCE = re.compile(r"\A```(?:json)?[ \t]*\n(.*)\n```\Z", re.DOTALL)
_Wire = TypeVar("_Wire", bound=BaseModel)


def _unfenced(text: str) -> str:
    body = text.strip()
    match = _FENCE.match(body)
    return match.group(1).strip() if match else body


def parse_json_object(text: str, contract: type[_Wire]) -> _Wire:
    """Exactly one JSON object, optionally in one code fence, then strict validation."""
    try:
        value = json.loads(_unfenced(text))
    except (ValueError, RecursionError):
        raise ResponderOutputError(ResponderErrorCode.NOT_JSON) from None
    if not isinstance(value, dict):
        raise ResponderOutputError(ResponderErrorCode.NOT_JSON)
    try:
        return contract.model_validate(value)
    except ValidationError:
        raise ResponderOutputError(ResponderErrorCode.SCHEMA) from None


class _WireModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


_Short = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class _PlanStepWire(_WireModel):
    objective: _Short


class _PlanWire(_WireModel):
    steps: list[_PlanStepWire] = Field(min_length=1)


class _AssignmentWire(_WireModel):
    step_id: _Short
    domain: _Short


class _AssignmentsWire(_WireModel):
    assignments: list[_AssignmentWire] = Field(min_length=1)


class _CheckWire(_WireModel):
    criterion: _Short
    passed: bool
    evidence_summary: _Short


class _ChecksWire(_WireModel):
    checks: list[_CheckWire] = Field(min_length=1)


def _evidence(
    guard: PromptInjectionGuard,
    boundary_id: str,
    task: str,
    items: tuple[tuple[str, str], ...],
) -> str:
    """Earlier model output enters prompts only as untrusted guarded data."""
    return guard.guard(
        PromptBoundaryRequest(
            boundary_id=boundary_id,
            user_task=task,
            items=tuple(
                UntrustedContentItem(
                    content_id=identity,
                    origin=UntrustedContentOrigin.MODEL_OUTPUT,
                    source_id=identity,
                    field_name="output",
                    text=text,
                    text_sha256=sha256(text.encode("utf-8")).hexdigest(),
                )
                for identity, text in items
            ),
        )
    ).render_text()


class ModelDirectResponder:
    """DirectResponder for simple PHOENIX answers; PHOENIX still finalizes."""

    def __init__(self, binding: ModelBinding) -> None:
        self._binding = binding

    async def respond(self, request: PhoenixRequest) -> DirectResponse:
        text = await _generate(
            self._binding, "direct", prompts.DIRECT_SYSTEM, request.text
        )
        return DirectResponse(request_id=request.request_id, text=text)


class ModelPlanningResponder:
    """PlanningResponder; the responder, not the model, assigns step IDs."""

    def __init__(self, binding: ModelBinding, *, max_steps: int) -> None:
        self._binding = binding
        self._max_steps = max_steps

    async def propose(self, request: PlanningRequest) -> PlanDraft:
        system = prompts.PLANNING_SYSTEM.format(max_steps=self._max_steps)
        text = await _generate(self._binding, "planner", system, request.objective)
        wire = parse_json_object(text, _PlanWire)
        if len(wire.steps) > self._max_steps:
            raise ResponderOutputError(ResponderErrorCode.SCHEMA)
        return PlanDraft(
            request_id=request.request_id,
            steps=tuple(
                PlanStep(step_id=f"s{index}", objective=step.objective)
                for index, step in enumerate(wire.steps, start=1)
            ),
        )


class StepAssignment(_Value):
    step_id: Identifier
    domain: RoutingDomain


class StepAssignments(_Value):
    """A validated PHOENIX routing proposal in plan order; not a route decision."""

    request_id: Identifier
    assignments: tuple[StepAssignment, ...] = Field(min_length=1)


class ModelStepAssignmentResponder:
    """PHOENIX-owned step-to-domain proposal restricted to an explicit allowlist."""

    def __init__(
        self,
        binding: ModelBinding,
        guard: PromptInjectionGuard,
        *,
        allowed_domains: tuple[RoutingDomain, ...],
        descriptions: Mapping[str, str] = prompts.DOMAIN_DESCRIPTIONS,
    ) -> None:
        # PHX-120 (ADR-160): the runtime passes the v5 capability documents
        # description when PHX-113 capability steps offer that domain.
        if not allowed_domains or any(
            domain.value not in descriptions for domain in allowed_domains
        ):
            raise ValueError("Every allowed domain needs a prompt description")
        self._binding = binding
        self._guard = guard
        self._allowed = allowed_domains
        self._descriptions = descriptions

    async def assign(self, plan: AizenPlan) -> StepAssignments:
        system = prompts.ASSIGNMENT_SYSTEM.format(
            domains="\n".join(
                f"- {domain.value}: {self._descriptions[domain.value]}"
                for domain in self._allowed
            )
        )
        user = _evidence(
            self._guard,
            f"{plan.request_id}:assignment",
            "Assign one allowed domain to every plan step listed below.",
            tuple((step.step_id, step.objective) for step in plan.steps),
        )
        wire = parse_json_object(
            await _generate(self._binding, "assignment", system, user),
            _AssignmentsWire,
        )
        step_ids = [step.step_id for step in plan.steps]
        allowed = {domain.value: domain for domain in self._allowed}
        chosen: dict[str, RoutingDomain] = {}
        for item in wire.assignments:
            if item.step_id not in step_ids or item.step_id in chosen:
                raise ResponderOutputError(ResponderErrorCode.UNKNOWN_STEP)
            if item.domain not in allowed:
                raise ResponderOutputError(ResponderErrorCode.DISALLOWED_DOMAIN)
            chosen[item.step_id] = allowed[item.domain]
        if len(chosen) != len(step_ids):
            raise ResponderOutputError(ResponderErrorCode.UNKNOWN_STEP)
        return StepAssignments(
            request_id=plan.request_id,
            assignments=tuple(
                StepAssignment(step_id=identity, domain=chosen[identity])
                for identity in step_ids
            ),
        )


class ModelSpecialistResponder:
    """Step output; the planned objective and earlier outputs are guarded data.

    AIZEN objectives are model output, so they never appear as raw prompt text.
    """

    def __init__(
        self, binding: ModelBinding, guard: PromptInjectionGuard, *, final: bool
    ) -> None:
        self._binding = binding
        self._guard = guard
        self._final = final

    async def perform(
        self,
        handoff: HandoffEnvelope,
        *,
        user_request: str,
        evidence: tuple[tuple[str, str], ...],
    ) -> str:
        parts = (
            f"Original request:\n{user_request}",
            f"Expected output:\n{handoff.expected_output_schema}",
            _evidence(
                self._guard,
                f"{handoff.task_id}:{handoff.step_id}",
                prompts.STEP_TASK,
                ((prompts.OBJECTIVE_ITEM, handoff.objective), *evidence),
            ),
        )
        if self._final:
            role, system = "final_answer", prompts.FINAL_ANSWER_SYSTEM
        else:
            role, system = "specialist", prompts.SPECIALIST_SYSTEM
        return await _generate(self._binding, role, system, "\n\n".join(parts))


def _is_structured(text: str) -> bool:
    try:
        return isinstance(json.loads(_unfenced(text)), (dict, list))
    except (ValueError, RecursionError):
        return False


class ModelVerificationResponder:
    """VerificationResponder: deterministic checks first, then model checks.

    The verdict stays LEVI's all-checks rule, so model checks cannot override a
    failed deterministic check; when one fails the model is not consulted.
    The plain-text check applies to intermediate steps only: a final answer may
    use any format the original request asks for, including JSON.
    """

    def __init__(
        self,
        binding: ModelBinding,
        guard: PromptInjectionGuard,
        *,
        original_request: str,
        plain_text_required: bool,
        max_checks: int = 6,
    ) -> None:
        self._binding = binding
        self._guard = guard
        self._original_request = original_request
        self._plain_text_required = plain_text_required
        self._max_checks = max_checks

    def _deterministic(self, candidate: str) -> tuple[VerificationCheck, ...]:
        checks: list[VerificationCheck] = []
        if self._plain_text_required:
            plain = not _is_structured(candidate)
            checks.append(
                VerificationCheck(
                    check_id="deterministic.plain_text",
                    criterion="Intermediate step output is plain text, not JSON",
                    passed=plain,
                    evidence_summary="Output is plain text"
                    if plain
                    else "Output parses as a JSON value",
                )
            )
        bounded = len(candidate) <= self._binding.max_output_chars
        checks.append(
            VerificationCheck(
                check_id="deterministic.size",
                criterion="Output length is within the configured limit",
                passed=bounded,
                evidence_summary=f"{len(candidate)} characters",
            )
        )
        return tuple(checks)

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        checks = self._deterministic(request.candidate_output)
        if not all(check.passed for check in checks):
            return VerificationDraft(
                verification_id=request.verification_id, checks=checks
            )
        system = prompts.VERIFICATION_SYSTEM.format(max_checks=self._max_checks)
        user = "\n\n".join(
            (
                f"Original request:\n{self._original_request}",
                f"Expected output:\n{request.expected_output_schema}",
                f"Completion rule:\n{request.completion_rule}",
                _evidence(
                    self._guard,
                    request.verification_id,
                    prompts.CANDIDATE_TASK,
                    (
                        (prompts.OBJECTIVE_ITEM, request.objective),
                        (prompts.CANDIDATE_ITEM, request.candidate_output),
                    ),
                ),
            )
        )
        wire = parse_json_object(
            await _generate(self._binding, "verifier", system, user), _ChecksWire
        )
        if len(wire.checks) > self._max_checks:
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
