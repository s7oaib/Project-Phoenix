"""Immutable handoff descriptions only; no dispatch or execution authority.

Graph fields remain structural evidence. Constraints, output schema descriptions,
requested scopes and verification flags are untrusted data, never permissions,
verified success, executable instructions or timers.
"""

from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    field_validator,
    model_validator,
)

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


class HandoffError(ValueError):
    """A handoff identity, graph selection or exact contract is invalid."""


def _snapshot(value: BaseModel, contract: type[BaseModel]) -> dict[str, object]:
    # Dumping alone can omit unknown fields injected by model_copy(update=...).
    # Validate stored fields by value, including extras, without trusting subclasses.
    if type(value) is not contract:
        raise HandoffError("Expected an exact handoff input contract")
    return {**vars(value), **(value.__pydantic_extra__ or {})}


class HandoffEnvelope(BaseModel):
    """Detached evidence; standalone validation does not prove graph correlation.

    Use HandoffBuilder to establish graph correlation and registered identities.
    Text retains formatting; IDs/scopes/refs trim outer whitespace only.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    task_id: _Identifier
    step_id: _Identifier
    source_agent_id: _Identifier
    target_agent_id: _Identifier
    objective: _Text
    input_refs: tuple[_Identifier, ...] = ()
    constraints: tuple[_Text, ...] = ()
    expected_output_schema: _Text
    timeout_seconds: float = Field(strict=True, gt=0, allow_inf_nan=False)
    permission_scope: tuple[_Identifier, ...] = ()
    verification_required: bool = Field(strict=True)

    @model_validator(mode="wrap")
    @classmethod
    def snapshot_contract(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        if isinstance(value, BaseModel):
            value = _snapshot(value, HandoffEnvelope)
        return handler(value)

    @field_validator("input_refs", "constraints", "permission_scope", mode="before")
    @classmethod
    def ordered_strings(cls, value: object) -> object:
        if not isinstance(value, (tuple, list)):
            raise ValueError("Expected an ordered tuple or list")
        return value

    @field_validator("objective", "expected_output_schema")
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Text must contain non-whitespace content")
        return value

    @field_validator("constraints")
    @classmethod
    def meaningful_unique_constraints(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(item.strip() for item in value)
        if any(not item for item in normalized):
            raise ValueError("Constraints must contain non-whitespace content")
        if len(set(normalized)) != len(normalized):
            raise ValueError("Duplicate normalized constraint")
        return value

    @field_validator("permission_scope")
    @classmethod
    def unique_scopes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Duplicate normalized permission scope")
        return value

    @model_validator(mode="after")
    def distinct_agents(self) -> Self:
        if self.source_agent_id == self.target_agent_id:
            raise ValueError("Self-handoff is forbidden")
        return self


class HandoffBuilder:
    """Synchronous read-only construction from exact revalidated graph evidence.

    Registry identities are snapshotted at construction. Every graph owner must
    exist in that same registry during revalidation. Any registered control ID is
    structurally valid; this builder neither chooses agents nor invokes them.
    """

    def __init__(self, agent_registry: AgentRegistry | None = None) -> None:
        registry = (
            build_default_agent_registry() if agent_registry is None else agent_registry
        )
        if not isinstance(registry, AgentRegistry):
            raise HandoffError("Expected an AgentRegistry")
        data = _snapshot(registry, AgentRegistry)
        entries = data.get("agents")
        if isinstance(entries, (tuple, list)):
            data["agents"] = tuple(
                _snapshot(item, AgentDefinition)
                if isinstance(item, BaseModel)
                else item
                for item in entries
            )
        self._agents = AgentRegistry.model_validate(data)

    def build(
        self,
        *,
        graph: TaskGraph,
        step_id: str,
        source_agent_id: str,
        expected_output_schema: str,
        verification_required: bool,
        constraints: tuple[str, ...] | list[str] = (),
        permission_scope: tuple[str, ...] | list[str] = (),
    ) -> HandoffEnvelope:
        """Derive graph-owned fields; return data without changing task state."""
        if not isinstance(graph, TaskGraph):
            raise HandoffError("Expected a TaskGraph")
        data = _snapshot(graph, TaskGraph)
        entries = data.get("steps")
        if isinstance(entries, (tuple, list)):
            data["steps"] = tuple(
                _snapshot(item, TaskStepDefinition)
                if isinstance(item, BaseModel)
                else item
                for item in entries
            )
        graph = TaskGraph.model_validate(data, context={"agent_registry": self._agents})
        selected = graph.step(step_id)
        if selected is None:
            raise HandoffError("Unknown handoff step ID")
        try:
            source = self._agents.require(source_agent_id)
            target = self._agents.require(selected.owner_agent_id)
        except UnknownAgentError:
            raise HandoffError("Unknown handoff agent internal ID") from None
        return HandoffEnvelope.model_validate(
            {
                "task_id": graph.task_id,
                "step_id": selected.step_id,
                "source_agent_id": source.internal_id,
                "target_agent_id": target.internal_id,
                "objective": selected.objective,
                "input_refs": selected.input_refs,
                "timeout_seconds": selected.timeout_seconds,
                "constraints": constraints,
                "expected_output_schema": expected_output_schema,
                "permission_scope": permission_scope,
                "verification_required": verification_required,
            }
        )
