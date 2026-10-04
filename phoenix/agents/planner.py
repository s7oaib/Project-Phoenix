"""Subordinate decomposition proposals only; no routing or execution authority."""

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from phoenix.agents.registry import AgentRegistry, build_default_agent_registry

_Identifier = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Objective = Annotated[str, StringConstraints(strict=True, min_length=1)]
_identifier = TypeAdapter(_Identifier)


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    @field_validator("objective", check_fields=False)
    @classmethod
    def meaningful_objective(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Objective must contain non-whitespace content")
        return value


class PlanningRequest(_Value):
    request_id: _Identifier = Field(default_factory=lambda: str(uuid4()))
    objective: _Objective


class PlanStep(_Value):
    """An unassigned descriptive objective; neither a task nor a handoff."""

    step_id: _Identifier
    objective: _Objective


class _Steps(_Value):
    steps: Annotated[tuple[PlanStep, ...], Field(min_length=1)]

    @field_validator("steps", mode="before")
    @classmethod
    def exact_step_contracts(cls, value: object) -> object:
        if not isinstance(value, (tuple, list)):
            raise ValueError("Steps require an ordered tuple or list")
        for step in value:
            if isinstance(step, PlanStep) and type(step) is not PlanStep:
                raise ValueError("Expected exact PlanStep contracts")
        return value

    @model_validator(mode="after")
    def unique_step_ids(self) -> Self:
        if len({step.step_id for step in self.steps}) != len(self.steps):
            raise ValueError("Duplicate plan step ID")
        return self


class PlanDraft(_Steps):
    """Ordered subordinate content, without arbitrary metadata or authority."""

    request_id: _Identifier


class PlanningResponder(Protocol):
    async def propose(self, request: PlanningRequest) -> PlanDraft:
        """Return correlated decomposition content or propagate failure/cancellation."""
        ...


class AizenPlan(_Steps):
    """A validated proposal only; never evidence of user-task success."""

    request_id: _Identifier
    planner_id: Literal["strategist"] = "strategist"


class CancellationStatus(StrEnum):
    ACCEPTED = "accepted"
    NOT_FOUND = "not_found"


class CancellationResult(_Value):
    """Local acknowledgement, not proof external computation stopped."""

    request_id: _Identifier
    status: CancellationStatus


class AizenPlanningError(Exception):
    """A planning contract or request lifetime requirement was violated."""


class DuplicatePlanningRequestError(AizenPlanningError):
    """The same planning request ID is already active on this planner."""


@dataclass
class _ActiveRequest:
    task: asyncio.Task[object]
    cancellation_requested: bool = False


class AizenPlanner:
    """One process/event loop, with one active operation per caller task.

    Distinct caller tasks may plan concurrently; no hidden workers are created.
    Cancellation is cooperative and callers own deadlines. Aliases, role text
    and plan objectives confer no execution or permission authority.
    """

    def __init__(self, registry: AgentRegistry | None = None) -> None:
        store = build_default_agent_registry() if registry is None else registry
        AgentRegistry.model_validate(store.model_dump()).require("strategist")
        self._active: dict[str, _ActiveRequest] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def planner_id(self) -> Literal["strategist"]:
        return "strategist"

    @property
    def active_request_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise AizenPlanningError("Planner belongs to another event loop")
        self._loop = loop

    async def plan(
        self, request: PlanningRequest, *, responder: PlanningResponder
    ) -> AizenPlan:
        self._bind_loop()
        if type(request) is not PlanningRequest:
            raise AizenPlanningError("Expected a PlanningRequest")
        request = PlanningRequest.model_validate(request)
        if request.request_id in self._active:
            raise DuplicatePlanningRequestError("Planning request ID is already active")
        task = asyncio.current_task()
        if task is None:
            raise AizenPlanningError("Planning requires a caller task")
        if any(active.task is task for active in self._active.values()):
            raise AizenPlanningError(
                "Caller task already owns an active planning request"
            )
        operation = _ActiveRequest(task)
        self._active[request.request_id] = operation
        try:
            draft = await responder.propose(request)
            if operation.cancellation_requested or task.cancelling():
                raise asyncio.CancelledError
            if type(draft) is not PlanDraft:
                raise AizenPlanningError("Expected a subordinate PlanDraft")
            try:
                draft = PlanDraft.model_validate(draft)
            except ValidationError:
                raise AizenPlanningError("Invalid plan draft") from None
            if draft.request_id != request.request_id:
                raise AizenPlanningError("Plan draft request ID mismatch")
            return AizenPlan(request_id=request.request_id, steps=draft.steps)
        finally:
            del self._active[request.request_id]

    async def cancel(self, request_id: str) -> CancellationResult:
        self._bind_loop()
        identity = _identifier.validate_python(request_id)
        operation = self._active.get(identity)
        status = CancellationStatus.NOT_FOUND
        if operation is not None:
            operation.cancellation_requested = True
            operation.task.cancel()
            status = CancellationStatus.ACCEPTED
        return CancellationResult(request_id=identity, status=status)
