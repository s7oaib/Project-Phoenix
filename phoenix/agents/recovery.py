"""Bounded recovery proposals only; no strategy execution or success authority.

Failure, rationale and parameter descriptions are untrusted data. Only explicit
eligible recover() awaits one neutral responder. Callers own carried-forward
budgets, deadlines and later orchestration; exhaustion does not activate KAIDO.
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
from phoenix.agents.verifier import VerificationResult, VerificationVerdict

_Identifier = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
_Nonnegative = Annotated[int, Field(strict=True, ge=0)]
_Positive = Annotated[int, Field(strict=True, gt=0)]
_identifier = TypeAdapter(_Identifier)


class MahoragaRecoveryError(ValueError):
    """Recovery evidence, proposal or operation lifetime is invalid."""


class DuplicateRecoveryError(MahoragaRecoveryError):
    """The recovery ID is already active on this instance."""


def _snapshot(value: object, contract: type[BaseModel]) -> dict[str, object]:
    if not isinstance(value, BaseModel) or type(value) is not contract:
        raise MahoragaRecoveryError("Expected an exact recovery input contract")
    # model_dump can omit fields injected by model_copy(update=...).
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

    @field_validator("trigger", "action", "status", mode="before", check_fields=False)
    @classmethod
    def textual_vocabulary(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("Vocabulary requires a string or its enum member")
        return value

    @field_validator("failure_summary", "rationale", check_fields=False)
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text must contain non-whitespace content")
        return value


class RecoveryTrigger(StrEnum):
    EXECUTION_FAILURE = "execution_failure"
    VERIFICATION_FAILURE = "verification_failure"
    TIMEOUT = "timeout"
    RESOURCE_UNAVAILABLE = "resource_unavailable"


class _Failure(_Value):
    task_id: _Identifier
    step_id: _Identifier
    subject_agent_id: _Identifier
    trigger: RecoveryTrigger
    failure_summary: _Text
    max_attempts: _Positive
    max_replan_steps: _Positive


class RecoveryRequest(_Failure):
    recovery_id: _Identifier = Field(default_factory=lambda: str(uuid4()))
    attempts_used: _Nonnegative


class RecoveryAction(StrEnum):
    RETRY_SAME = "retry_same"
    ADJUST_PARAMETERS = "adjust_parameters"
    SWITCH_TOOL = "switch_tool"
    SWITCH_MODEL_PROVIDER = "switch_model_provider"
    SWITCH_AGENT = "switch_agent"
    REPLAN_SUBGRAPH = "replan_subgraph"


class RecoveryDraft(_Value):
    """One exclusive strategy description. None means an absent optional field."""

    recovery_id: _Identifier
    action: RecoveryAction
    rationale: _Text
    replacement_agent_id: _Identifier | None = None
    replacement_provider_id: _Identifier | None = None
    replacement_model_id: _Identifier | None = None
    replacement_tool_id: _Identifier | None = None
    parameter_adjustments: Annotated[tuple[_Text, ...], Field(min_length=1)] | None = (
        None
    )
    replan_step_ids: Annotated[tuple[_Identifier, ...], Field(min_length=1)] | None = (
        None
    )

    @field_validator("parameter_adjustments", "replan_step_ids", mode="before")
    @classmethod
    def ordered_descriptions(cls, value: object) -> object:
        if value is not None and not isinstance(value, (tuple, list)):
            raise ValueError("Expected an ordered tuple or list")
        return value

    @field_validator("parameter_adjustments", "replan_step_ids")
    @classmethod
    def meaningful_unique_entries(
        cls, value: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        if value is not None:
            normalized = tuple(item.strip() for item in value)
            if any(not item for item in normalized):
                raise ValueError("Sequence text must contain non-whitespace content")
            if len(set(normalized)) != len(normalized):
                raise ValueError("Duplicate normalized proposal entry")
        return value

    @model_validator(mode="after")
    def exclusive_action_fields(self) -> Self:
        required = {
            RecoveryAction.RETRY_SAME: frozenset(),
            RecoveryAction.ADJUST_PARAMETERS: frozenset({"parameter_adjustments"}),
            RecoveryAction.SWITCH_TOOL: frozenset({"replacement_tool_id"}),
            RecoveryAction.SWITCH_MODEL_PROVIDER: frozenset(
                {"replacement_provider_id", "replacement_model_id"}
            ),
            RecoveryAction.SWITCH_AGENT: frozenset({"replacement_agent_id"}),
            RecoveryAction.REPLAN_SUBGRAPH: frozenset({"replan_step_ids"}),
        }
        present = {
            name
            for name in (
                "replacement_agent_id",
                "replacement_provider_id",
                "replacement_model_id",
                "replacement_tool_id",
                "parameter_adjustments",
                "replan_step_ids",
            )
            if getattr(self, name) is not None
        }
        if present != required[self.action]:
            raise ValueError("Proposal fields contradict recovery action")
        return self


class RecoveryResponder(Protocol):
    async def propose(self, request: RecoveryRequest) -> RecoveryDraft:
        """Return one correlated proposal or propagate failure/cancellation."""
        ...


class RecoveryStatus(StrEnum):
    PROPOSED = "proposed"
    EXHAUSTED = "exhausted"


class RecoveryResult(_Failure):
    """Failure-preserving strategy/budget evidence, never execution success."""

    recovery_id: _Identifier
    recovery_agent_id: Literal["adaptive_recovery"] = "adaptive_recovery"
    status: RecoveryStatus
    attempts_used_before: _Nonnegative
    attempt_number: _Positive | None = None
    proposal: RecoveryDraft | None = None

    @field_validator("proposal", mode="before")
    @classmethod
    def snapshot_proposal(cls, value: object) -> object:
        return (
            _snapshot(value, RecoveryDraft) if isinstance(value, BaseModel) else value
        )

    @model_validator(mode="after")
    def consistent_result(self) -> Self:
        if self.status == RecoveryStatus.EXHAUSTED:
            if (
                self.attempts_used_before < self.max_attempts
                or self.attempt_number is not None
                or self.proposal is not None
            ):
                raise ValueError("Exhausted evidence contradicts budget/proposal")
        else:
            if (
                self.proposal is None
                or self.proposal.recovery_id != self.recovery_id
                or self.attempt_number != self.attempts_used_before + 1
                or self.attempt_number > self.max_attempts
            ):
                raise ValueError("Proposed evidence contradicts budget/proposal")
            if self.proposal.replacement_agent_id == self.subject_agent_id:
                raise ValueError("Replacement agent must differ from failed subject")
            if self.proposal.replan_step_ids is not None and (
                self.step_id not in self.proposal.replan_step_ids
                or len(self.proposal.replan_step_ids) > self.max_replan_steps
            ):
                raise ValueError("Replan must include failed step within size budget")
        return self


class CancellationStatus(StrEnum):
    ACCEPTED = "accepted"
    NOT_FOUND = "not_found"


class CancellationResult(_Value):
    """Local request acknowledgement, not proof external computation stopped."""

    recovery_id: _Identifier
    status: CancellationStatus


@dataclass
class _ActiveRecovery:
    task: asyncio.Task[object]
    cancellation_requested: bool = False


class MahoragaRecovery:
    """One process/event loop; caller tasks own eligible decisions, without workers.

    Caller-provided attempt evidence must be carried forward by later integration.
    Registered replacements are structural metadata, never routing or permission.
    Cancellation is cooperative and deadlines remain caller-owned.
    """

    def __init__(self, agent_registry: AgentRegistry | None = None) -> None:
        registry = (
            build_default_agent_registry() if agent_registry is None else agent_registry
        )
        data = _snapshot(registry, AgentRegistry)
        data["agents"] = _entries(data.get("agents", ()), AgentDefinition)
        self._agents = AgentRegistry.model_validate(data)
        try:
            self._agents.require("adaptive_recovery")
        except UnknownAgentError:
            raise MahoragaRecoveryError("Recovery internal ID is absent") from None
        self._active: dict[str, _ActiveRecovery] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def recovery_agent_id(self) -> Literal["adaptive_recovery"]:
        return "adaptive_recovery"

    @property
    def active_recovery_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise MahoragaRecoveryError("Recovery belongs to another event loop")
        self._loop = loop

    async def recover(
        self,
        *,
        graph: TaskGraph,
        handoff: HandoffEnvelope,
        trigger: RecoveryTrigger,
        failure_summary: str,
        attempts_used: int,
        max_attempts: int,
        max_replan_steps: int,
        responder: RecoveryResponder,
        verification_result: VerificationResult | None = None,
        recovery_id: str | None = None,
    ) -> RecoveryResult:
        """Propose once or report exhaustion; omitted/None ID generates UUID4."""
        self._bind_loop()
        data = _snapshot(graph, TaskGraph)
        data["steps"] = _entries(data.get("steps"), TaskStepDefinition)
        graph = TaskGraph.model_validate(data, context={"agent_registry": self._agents})
        handoff = HandoffEnvelope.model_validate(_snapshot(handoff, HandoffEnvelope))
        selected = graph.step(handoff.step_id)
        if selected is None:
            raise MahoragaRecoveryError("Unknown recovery step ID")
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
            raise MahoragaRecoveryError("Graph/handoff correlation mismatch")
        try:
            self._agents.require(handoff.source_agent_id)
            self._agents.require(handoff.target_agent_id)
        except UnknownAgentError:
            raise MahoragaRecoveryError("Handoff agent internal ID is absent") from None
        request = RecoveryRequest.model_validate(
            {
                "task_id": graph.task_id,
                "step_id": selected.step_id,
                "subject_agent_id": handoff.target_agent_id,
                "trigger": trigger,
                "failure_summary": failure_summary,
                "attempts_used": attempts_used,
                "max_attempts": max_attempts,
                "max_replan_steps": max_replan_steps,
                **({} if recovery_id is None else {"recovery_id": recovery_id}),
            }
        )
        if request.trigger == RecoveryTrigger.VERIFICATION_FAILURE:
            evidence = VerificationResult.model_validate(
                _snapshot(verification_result, VerificationResult)
            )
            if (
                evidence.task_id,
                evidence.step_id,
                evidence.subject_agent_id,
                evidence.verdict,
            ) != (
                request.task_id,
                request.step_id,
                request.subject_agent_id,
                VerificationVerdict.FAILED,
            ):
                raise MahoragaRecoveryError("Expected correlated FAILED verification")
        elif verification_result is not None:
            raise MahoragaRecoveryError(
                "Verification evidence requires verification_failure trigger"
            )
        if request.recovery_id in self._active:
            raise DuplicateRecoveryError("Recovery ID is already active")
        result_data = {
            "recovery_id": request.recovery_id,
            "task_id": request.task_id,
            "step_id": request.step_id,
            "subject_agent_id": request.subject_agent_id,
            "trigger": request.trigger,
            "failure_summary": request.failure_summary,
            "attempts_used_before": request.attempts_used,
            "max_attempts": request.max_attempts,
            "max_replan_steps": request.max_replan_steps,
        }
        if request.attempts_used >= request.max_attempts:
            return RecoveryResult.model_validate(
                {**result_data, "status": RecoveryStatus.EXHAUSTED}
            )
        task = asyncio.current_task()
        if task is None:
            raise MahoragaRecoveryError("Recovery requires a caller task")
        if any(active.task is task for active in self._active.values()):
            raise MahoragaRecoveryError("Caller task already owns an active recovery")
        operation = _ActiveRecovery(task)
        self._active[request.recovery_id] = operation
        try:
            draft = await responder.propose(request)
            if operation.cancellation_requested or task.cancelling():
                raise asyncio.CancelledError
            try:
                draft = RecoveryDraft.model_validate(_snapshot(draft, RecoveryDraft))
            except ValidationError:
                raise MahoragaRecoveryError("Invalid recovery draft") from None
            if draft.recovery_id != request.recovery_id:
                raise MahoragaRecoveryError("Recovery draft ID mismatch")
            if draft.replacement_agent_id is not None:
                try:
                    self._agents.require(draft.replacement_agent_id)
                except UnknownAgentError:
                    raise MahoragaRecoveryError(
                        "Unknown replacement internal ID"
                    ) from None
                if draft.replacement_agent_id == request.subject_agent_id:
                    raise MahoragaRecoveryError("Replacement must differ from subject")
            if draft.replan_step_ids is not None and (
                request.step_id not in draft.replan_step_ids
                or len(draft.replan_step_ids) > request.max_replan_steps
                or any(
                    graph.step(identity) is None for identity in draft.replan_step_ids
                )
            ):
                raise MahoragaRecoveryError("Invalid bounded replan subgraph")
            return RecoveryResult.model_validate(
                {
                    **result_data,
                    "status": RecoveryStatus.PROPOSED,
                    "attempt_number": request.attempts_used + 1,
                    "proposal": draft,
                }
            )
        finally:
            del self._active[request.recovery_id]

    async def cancel(self, recovery_id: str) -> CancellationResult:
        self._bind_loop()
        identity = _identifier.validate_python(recovery_id)
        operation = self._active.get(identity)
        status = CancellationStatus.NOT_FOUND
        if operation is not None:
            operation.cancellation_requested = True
            operation.task.cancel()
            status = CancellationStatus.ACCEPTED
        return CancellationResult(recovery_id=identity, status=status)
