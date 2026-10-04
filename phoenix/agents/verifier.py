"""LEVI verification evidence and caller-owned lifetimes, without repair.

Only explicit verify() awaits a neutral collaborator. Checks are supplied evidence,
not a guarantee of global output correctness, permission or PHOENIX completion.
Graph/handoff correlation, verdict consistency and required-verification gating
are enforced here; task-state, event and recovery integration remain future work.
"""

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from phoenix.agents.handoff import HandoffEnvelope
from phoenix.agents.registry import (
    AgentDefinition,
    AgentRegistry,
    UnknownAgentError,
    build_default_agent_registry,
)
from phoenix.agents.task_graph import TaskGraph, TaskStepDefinition

_Identifier = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
_identifier = TypeAdapter(_Identifier)


class LeviVerificationError(ValueError):
    """Verification evidence, correlation or lifetime is invalid."""


class DuplicateVerificationError(LeviVerificationError):
    """The verification ID is already active on this verifier."""


def _snapshot(value: object, contract: type[BaseModel]) -> dict[str, object]:
    if not isinstance(value, BaseModel) or type(value) is not contract:
        raise LeviVerificationError("Expected an exact verification input contract")
    # model_dump may omit extra stored fields injected by model_copy(update=...).
    return {**vars(value), **(value.__pydantic_extra__ or {})}


def _entries(value: object, contract: type[BaseModel]) -> tuple[object, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError("Expected an ordered tuple or list")
    return tuple(
        _snapshot(item, contract) if isinstance(item, BaseModel) else item
        for item in value
    )


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    @model_validator(mode="wrap")
    @classmethod
    def snapshot_contract(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        if isinstance(value, BaseModel):
            value = _snapshot(value, cls)
        return handler(value)

    @field_validator(
        "objective",
        "expected_output_schema",
        "completion_rule",
        "candidate_output",
        "criterion",
        "evidence_summary",
        check_fields=False,
    )
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text must contain non-whitespace content")
        return value


class VerificationRequest(_Value):
    verification_id: _Identifier = Field(default_factory=lambda: str(uuid4()))
    task_id: _Identifier
    step_id: _Identifier
    subject_agent_id: _Identifier
    objective: _Text
    expected_output_schema: _Text
    completion_rule: _Text
    candidate_output: _Text


class VerificationCheck(_Value):
    check_id: _Identifier
    criterion: _Text
    passed: bool = Field(strict=True)
    evidence_summary: _Text


class _Checks(_Value):
    checks: tuple[VerificationCheck, ...] = Field(min_length=1)

    @field_validator("checks", mode="before")
    @classmethod
    def snapshot_checks(cls, value: object) -> object:
        return _entries(value, VerificationCheck)

    @model_validator(mode="after")
    def unique_checks(self) -> Self:
        if len({check.check_id for check in self.checks}) != len(self.checks):
            raise ValueError("Duplicate verification check ID")
        return self


class VerificationDraft(_Checks):
    """Subordinate ordered evidence only; the responder supplies no verdict."""

    verification_id: _Identifier


class VerificationResponder(Protocol):
    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        """Return correlated check evidence, or propagate failure/cancellation."""
        ...


class VerificationVerdict(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class VerificationResult(_Checks):
    """A consistent check verdict, never user completion or permission authority."""

    verification_id: _Identifier
    task_id: _Identifier
    step_id: _Identifier
    subject_agent_id: _Identifier
    verifier_id: Literal["verifier"] = "verifier"
    verdict: VerificationVerdict

    @model_validator(mode="after")
    def consistent_verdict(self) -> Self:
        if (self.verdict == VerificationVerdict.PASSED) != all(
            check.passed for check in self.checks
        ):
            raise ValueError("Verification verdict contradicts its checks")
        return self


def verification_requirement_satisfied(
    handoff: HandoffEnvelope, result: VerificationResult | None
) -> bool:
    """Only the handoff obligation; supplied evidence always needs correlation.

    With no obligation, a valid correlated FAILED result does not create one.
    This says nothing about overall success or whether execution is authorized.
    """
    handoff = HandoffEnvelope.model_validate(_snapshot(handoff, HandoffEnvelope))
    if result is None:
        return not handoff.verification_required
    result = VerificationResult.model_validate(_snapshot(result, VerificationResult))
    if (result.task_id, result.step_id, result.subject_agent_id) != (
        handoff.task_id,
        handoff.step_id,
        handoff.target_agent_id,
    ):
        raise LeviVerificationError("Verification result handoff correlation mismatch")
    return (
        not handoff.verification_required
        or result.verdict == VerificationVerdict.PASSED
    )


class CancellationStatus(StrEnum):
    ACCEPTED = "accepted"
    NOT_FOUND = "not_found"


class CancellationResult(_Value):
    """Local cancellation acknowledgement, not proof external work stopped."""

    verification_id: _Identifier
    status: CancellationStatus


@dataclass
class _ActiveVerification:
    task: asyncio.Task[object]
    cancellation_requested: bool = False


class LeviVerifier:
    """One process/event loop; caller tasks own verifications, without workers.

    A registry snapshot validates verifier and graph/subject identities by internal
    ID only. Cancellation is cooperative; callers own deadlines. No task state,
    permission, provider, tool, logging or recovery authority is assumed.
    """

    def __init__(self, agent_registry: AgentRegistry | None = None) -> None:
        registry = (
            build_default_agent_registry() if agent_registry is None else agent_registry
        )
        data = _snapshot(registry, AgentRegistry)
        data["agents"] = _entries(data.get("agents", ()), AgentDefinition)
        self._agents = AgentRegistry.model_validate(data)
        try:
            self._agents.require("verifier")
        except UnknownAgentError:
            raise LeviVerificationError("Verifier internal ID is absent") from None
        self._active: dict[str, _ActiveVerification] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def verifier_id(self) -> Literal["verifier"]:
        return "verifier"

    @property
    def active_verification_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise LeviVerificationError("Verifier belongs to another event loop")
        self._loop = loop

    async def verify(
        self,
        *,
        graph: TaskGraph,
        handoff: HandoffEnvelope,
        candidate_output: str,
        responder: VerificationResponder,
        verification_id: str | None = None,
    ) -> VerificationResult:
        """Verify once; omitted/None ID generates UUID4, other supplied IDs validate."""
        self._bind_loop()
        data = _snapshot(graph, TaskGraph)
        data["steps"] = _entries(data.get("steps"), TaskStepDefinition)
        graph = TaskGraph.model_validate(data, context={"agent_registry": self._agents})
        handoff = HandoffEnvelope.model_validate(_snapshot(handoff, HandoffEnvelope))
        selected = graph.step(handoff.step_id)
        if selected is None:
            raise LeviVerificationError("Unknown verification step ID")
        if (
            handoff.task_id,
            handoff.target_agent_id,
            handoff.objective,
            handoff.input_refs,
            handoff.timeout_seconds,
        ) != (
            graph.task_id,
            selected.owner_agent_id,
            selected.objective,
            selected.input_refs,
            selected.timeout_seconds,
        ):
            raise LeviVerificationError("Graph/handoff correlation mismatch")
        try:
            self._agents.require(handoff.source_agent_id)
            self._agents.require(handoff.target_agent_id)
        except UnknownAgentError:
            raise LeviVerificationError("Handoff agent internal ID is absent") from None
        request = VerificationRequest.model_validate(
            {
                "task_id": graph.task_id,
                "step_id": selected.step_id,
                "subject_agent_id": handoff.target_agent_id,
                "objective": selected.objective,
                "expected_output_schema": handoff.expected_output_schema,
                "completion_rule": selected.completion_rule,
                "candidate_output": candidate_output,
                **(
                    {}
                    if verification_id is None
                    else {"verification_id": verification_id}
                ),
            }
        )
        if request.verification_id in self._active:
            raise DuplicateVerificationError("Verification ID is already active")
        task = asyncio.current_task()
        if task is None:
            raise LeviVerificationError("Verification requires a caller task")
        if any(active.task is task for active in self._active.values()):
            raise LeviVerificationError(
                "Caller task already owns an active verification"
            )
        operation = _ActiveVerification(task)
        self._active[request.verification_id] = operation
        try:
            draft = await responder.verify(request)
            if operation.cancellation_requested or task.cancelling():
                raise asyncio.CancelledError
            try:
                draft = VerificationDraft.model_validate(
                    _snapshot(draft, VerificationDraft)
                )
            except ValidationError:
                raise LeviVerificationError("Invalid verification draft") from None
            if draft.verification_id != request.verification_id:
                raise LeviVerificationError("Verification draft ID mismatch")
            verdict = (
                VerificationVerdict.PASSED
                if all(check.passed for check in draft.checks)
                else VerificationVerdict.FAILED
            )
            return VerificationResult(
                verification_id=request.verification_id,
                task_id=request.task_id,
                step_id=request.step_id,
                subject_agent_id=request.subject_agent_id,
                verdict=verdict,
                checks=draft.checks,
            )
        finally:
            del self._active[request.verification_id]

    async def cancel(self, verification_id: str) -> CancellationResult:
        self._bind_loop()
        identity = _identifier.validate_python(verification_id)
        operation = self._active.get(identity)
        status = CancellationStatus.NOT_FOUND
        if operation is not None:
            operation.cancellation_requested = True
            operation.task.cancel()
            status = CancellationStatus.ACCEPTED
        return CancellationResult(verification_id=identity, status=status)
