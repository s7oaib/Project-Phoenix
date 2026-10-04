"""Immutable DAG definitions and memory-only canonical task state evidence.

Queries never schedule steps. State markers never execute permission, verification,
recovery or fail-safe subsystems, nor prove user-facing success. Future PHOENIX
runtime integration owns those outcomes and any event/persistence production.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from heapq import heapify, heappop, heappush
from types import MappingProxyType
from typing import Annotated, Self
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

from phoenix.agents.registry import (
    AgentRegistry,
    UnknownAgentError,
    build_default_agent_registry,
)

_Identifier = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
_identifier = TypeAdapter(_Identifier)
_identifiers = TypeAdapter(tuple[_Identifier, ...])


class TaskState(StrEnum):
    """Exactly the canonical task vocabulary; no per-step runtime state."""

    CREATED = "created"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    WAITING_PERMISSION = "waiting_permission"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERING = "recovering"
    DEGRADED = "degraded"
    CANCELLED = "cancelled"


TASK_TRANSITIONS: Mapping[TaskState, frozenset[TaskState]] = MappingProxyType(
    {
        TaskState.CREATED: frozenset({TaskState.PLANNING, TaskState.CANCELLED}),
        TaskState.PLANNING: frozenset(
            {TaskState.READY, TaskState.FAILED, TaskState.CANCELLED}
        ),
        TaskState.READY: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
        TaskState.RUNNING: frozenset(
            {
                TaskState.WAITING_PERMISSION,
                TaskState.VERIFYING,
                TaskState.FAILED,
                TaskState.CANCELLED,
            }
        ),
        TaskState.WAITING_PERMISSION: frozenset(
            {TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED}
        ),
        TaskState.VERIFYING: frozenset(
            {
                TaskState.COMPLETED,
                TaskState.FAILED,
                TaskState.RECOVERING,
                TaskState.CANCELLED,
            }
        ),
        TaskState.COMPLETED: frozenset(),
        TaskState.FAILED: frozenset(
            {TaskState.RECOVERING, TaskState.DEGRADED, TaskState.CANCELLED}
        ),
        TaskState.RECOVERING: frozenset(
            {
                TaskState.PLANNING,
                TaskState.READY,
                TaskState.RUNNING,
                TaskState.FAILED,
                TaskState.DEGRADED,
                TaskState.CANCELLED,
            }
        ),
        TaskState.DEGRADED: frozenset(),
        TaskState.CANCELLED: frozenset(),
    }
)
_state = TypeAdapter(TaskState)


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


def _ordered(value: object) -> object:
    if not isinstance(value, (tuple, list)):
        raise ValueError("Expected an ordered tuple or list")
    return value


class TaskStepDefinition(_Value):
    """Static metadata only; owners are validated when assembled into a graph."""

    step_id: _Identifier
    owner_agent_id: _Identifier
    objective: _Text
    depends_on: tuple[_Identifier, ...] = ()
    input_refs: tuple[_Identifier, ...] = ()
    expected_output: _Text
    timeout_seconds: float = Field(strict=True, gt=0, allow_inf_nan=False)
    completion_rule: _Text

    @field_validator("objective", "expected_output", "completion_rule")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text must contain non-whitespace content")
        return value

    @field_validator("depends_on", "input_refs", mode="before")
    @classmethod
    def ordered_references(cls, value: object) -> object:
        return _ordered(value)

    @model_validator(mode="after")
    def valid_dependencies(self) -> Self:
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("Duplicate step dependency")
        if self.step_id in self.depends_on:
            raise ValueError("Self-dependency is forbidden")
        return self


def _topological_ids(steps: tuple[TaskStepDefinition, ...]) -> tuple[str, ...]:
    """Kahn traversal with a heap of source positions, including newly ready nodes."""
    positions = {step.step_id: index for index, step in enumerate(steps)}
    incoming = {step.step_id: len(step.depends_on) for step in steps}
    successors: dict[str, list[str]] = {step.step_id: [] for step in steps}
    for step in steps:
        for dependency in step.depends_on:
            successors[dependency].append(step.step_id)
    ready = [index for index, step in enumerate(steps) if incoming[step.step_id] == 0]
    heapify(ready)
    ordered: list[str] = []
    while ready:
        identity = steps[heappop(ready)].step_id
        ordered.append(identity)
        for successor in successors[identity]:
            incoming[successor] -= 1
            if incoming[successor] == 0:
                heappush(ready, positions[successor])
    if len(ordered) != len(steps):
        raise ValueError("Task graph contains a dependency cycle")
    return tuple(ordered)


class UnknownStepError(LookupError):
    """Completed-step evidence references an ID absent from this graph."""


class TaskGraph(_Value):
    """Frozen source-ordered DAG, without attached registry/runtime objects.

    Construction uses the default roster. Custom registries may be supplied via
    model_validate(..., context={"agent_registry": registry}); JSON revalidation
    with custom owner IDs needs that same explicit context. Ownership is checked
    by value during validation, never by alias or role, and conveys no permission.
    """

    task_id: _Identifier = Field(default_factory=lambda: str(uuid4()))
    steps: tuple[TaskStepDefinition, ...] = Field(min_length=1)

    @field_validator("steps", mode="before")
    @classmethod
    def snapshot_steps(cls, value: object) -> object:
        _ordered(value)
        assert isinstance(value, (tuple, list))
        return tuple(
            item.model_dump(warnings=False) if isinstance(item, BaseModel) else item
            for item in value
        )

    @model_validator(mode="after")
    def validate_graph(self, info: ValidationInfo) -> Self:
        ids = {step.step_id for step in self.steps}
        if len(ids) != len(self.steps):
            raise ValueError("Duplicate task step ID")
        for step in self.steps:
            if not set(step.depends_on) <= ids:
                raise ValueError("Unknown step dependency")
        _topological_ids(self.steps)
        context = info.context
        if context is not None and not isinstance(context, dict):
            raise ValueError("Graph validation context must be a dictionary")
        registry = None if context is None else context.get("agent_registry")
        if registry is None:
            registry = build_default_agent_registry()
        if not isinstance(registry, AgentRegistry):
            raise ValueError("Expected an AgentRegistry for owner validation")
        registry = AgentRegistry.model_validate(registry.model_dump(warnings=False))
        for step in self.steps:
            try:
                registry.require(step.owner_agent_id)
            except UnknownAgentError:
                raise ValueError("Unknown step owner agent ID") from None
        return self

    def step(self, step_id: str) -> TaskStepDefinition | None:
        """Exact normalized lookup; a valid unknown ID returns None."""
        identity = _identifier.validate_python(step_id)
        return next((step for step in self.steps if step.step_id == identity), None)

    @property
    def root_step_ids(self) -> tuple[str, ...]:
        return tuple(step.step_id for step in self.steps if not step.depends_on)

    @property
    def topological_step_ids(self) -> tuple[str, ...]:
        return _topological_ids(self.steps)

    def ready_step_ids(
        self, completed_step_ids: tuple[str, ...] | list[str]
    ) -> tuple[str, ...]:
        """Structural readiness only; no proof completed IDs ran or passed checks."""
        completed = _identifiers.validate_python(_ordered(completed_step_ids))
        if len(set(completed)) != len(completed):
            raise ValueError("Duplicate completed step ID")
        known = {step.step_id for step in self.steps}
        if not set(completed) <= known:
            raise UnknownStepError("Unknown completed step ID")
        done = set(completed)
        return tuple(
            step.step_id
            for step in self.steps
            if step.step_id not in done and set(step.depends_on) <= done
        )


class TaskTransition(_Value):
    """Immutable legal transition evidence; append order, not UTC time, is authority."""

    task_id: _Identifier
    from_state: TaskState
    to_state: TaskState
    occurred_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("occurred_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def legal_pair(self) -> Self:
        if self.to_state not in TASK_TRANSITIONS[self.from_state]:
            raise ValueError("Illegal task transition record")
        return self


class TaskStateTransitionError(ValueError):
    """A requested transition is outside the explicit canonical table."""


class TaskStateMachine:
    """Synchronous one-process/thread state evidence; no execution or timers.

    The caller reports state changes. A legal transition to COMPLETED does not
    independently establish successful execution or required verification.
    """

    __slots__ = ("_task_id", "_snapshot")

    def __init__(self, task_id: str) -> None:
        self._task_id = _identifier.validate_python(task_id)
        self._snapshot: tuple[TaskState, tuple[TaskTransition, ...]] = (
            TaskState.CREATED,
            (),
        )

    @property
    def task_id(self) -> str:
        return self._task_id

    @property
    def state(self) -> TaskState:
        return self._snapshot[0]

    @property
    def history(self) -> tuple[TaskTransition, ...]:
        return self._snapshot[1]

    @property
    def is_terminal(self) -> bool:
        return not TASK_TRANSITIONS[self.state]

    def can_transition(self, target: TaskState) -> bool:
        return _state.validate_python(target) in TASK_TRANSITIONS[self.state]

    def transition(self, target: TaskState) -> TaskTransition:
        destination = _state.validate_python(target)
        if destination not in TASK_TRANSITIONS[self.state]:
            raise TaskStateTransitionError("Illegal task state transition")
        record = TaskTransition(
            task_id=self.task_id, from_state=self.state, to_state=destination
        )
        # Prepare every potentially failing operation before the single commit.
        snapshot = destination, (*self.history, record)
        self._snapshot = snapshot
        return record
