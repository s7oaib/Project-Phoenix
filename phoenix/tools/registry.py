"""Immutable tool metadata only; no execution, permission or audit authority."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Annotated, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_validator,
)

_Identifier = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_identifier = TypeAdapter(_Identifier)


class ToolRiskLevel(StrEnum):
    """Descriptive classification; PHX-041 owns authorization."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"
    P5 = "P5"


class ToolResultStatus(StrEnum):
    """Future result vocabulary; registry inspection creates no result."""

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    DENIED = "denied"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    FAILURE = "failure"
    UNAVAILABLE_DEPENDENCY = "unavailable_dependency"


class ToolRegistryError(Exception):
    """Explicit registry construction or resolution failure."""


class DuplicateToolError(ToolRegistryError):
    """Two definitions declare the same normalized exact ID."""


class UnknownToolError(ToolRegistryError, LookupError):
    """A valid required ID is absent; no alias or fallback lookup."""


def _freeze(value: object, active: set[int]) -> object:
    """Validate ordinary JSON data and detach maps/arrays without interpretation."""
    kind = type(value)
    if value is None or kind in (bool, str, int):
        return value
    if kind is float:
        if not isfinite(cast(float, value)):
            raise ValueError("Schema numbers must be finite")
        return value
    if kind not in (dict, MappingProxyType, list, tuple):
        raise ValueError("Schema requires ordinary JSON data")
    identity = id(value)
    if identity in active:
        raise ValueError("Cyclic schema evidence")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            if any(type(key) is not str for key in value):
                raise ValueError("Schema object keys must be strings")
            return MappingProxyType(
                {key: _freeze(item, active) for key, item in value.items()}
            )
        return tuple(_freeze(item, active) for item in cast(Sequence[object], value))
    finally:
        active.remove(identity)


def _json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json(item) for item in value]
    return value


class ToolDefinition(BaseModel):
    """Detached declaration evidence, never a handler, grant or executable schema."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    tool_id: _Identifier
    description: Annotated[str, StringConstraints(strict=True, min_length=1)]
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    risk_level: ToolRiskLevel
    permission_requirement: _Identifier
    timeout_seconds: Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
    cancellation_supported: bool = Field(strict=True)
    audit_behavior: _Identifier
    supported_platforms: tuple[_Identifier, ...]
    supported_environments: tuple[_Identifier, ...]

    @model_validator(mode="wrap")
    @classmethod
    def stored_snapshot(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        if isinstance(value, BaseModel):
            if type(value) is not cls:
                raise ValueError("Expected exact ToolDefinition")
            value = {**vars(value), **(value.__pydantic_extra__ or {})}
        return handler(value)

    @field_validator("description")
    @classmethod
    def meaningful_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Description must contain meaningful text")
        return value

    @field_validator("input_schema", "output_schema", mode="plain")
    @classmethod
    def schema_evidence(cls, value: object) -> Mapping[str, object]:
        if type(value) not in (dict, MappingProxyType):
            raise ValueError("Schema top level must be a JSON object")
        try:
            return cast(Mapping[str, object], _freeze(value, set()))
        except RecursionError:
            raise ValueError("Schema nesting exceeds safe validation depth") from None

    @field_serializer("input_schema", "output_schema")
    def serialize_schema(self, value: Mapping[str, object]) -> object:
        return _json(value)

    @field_validator("risk_level", mode="before")
    @classmethod
    def exact_risk(cls, value: object) -> object:
        if type(value) not in (str, ToolRiskLevel):
            raise ValueError("Expected ToolRiskLevel or its exact textual value")
        return value

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def ordinary_timeout(cls, value: object) -> object:
        if type(value) not in (int, float):
            raise ValueError("Timeout requires an ordinary positive number")
        try:
            return float(cast(int | float, value))
        except OverflowError:
            raise ValueError("Timeout must be finite") from None

    @field_validator("supported_platforms", "supported_environments", mode="before")
    @classmethod
    def ordered_support(cls, value: object) -> object:
        if type(value) not in (tuple, list):
            raise ValueError("Support declarations require an ordered sequence")
        return tuple(cast(Sequence[object], value))

    @field_validator("supported_platforms", "supported_environments")
    @classmethod
    def unique_support(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("Support declarations must be nonempty and unique")
        return value


@dataclass(frozen=True, slots=True, init=False)
class ToolRegistry:
    """Ordered metadata snapshot; a different tool set requires a new registry."""

    _tools: tuple[ToolDefinition, ...]

    def __init__(self, tools: Sequence[ToolDefinition] = ()) -> None:
        if hasattr(self, "_tools"):
            raise ToolRegistryError("Registry is already initialized")
        if type(tools) not in (tuple, list):
            raise ValueError("Registry requires an ordered definition sequence")
        definitions: list[ToolDefinition] = []
        identities: set[str] = set()
        for tool in tuple(tools):
            if type(tool) is not ToolDefinition:
                raise ValueError("Registry requires exact ToolDefinition instances")
            validated = ToolDefinition.model_validate(tool)
            if validated.tool_id in identities:
                raise DuplicateToolError("Duplicate normalized tool ID")
            identities.add(validated.tool_id)
            definitions.append(validated)
        # Publish only after all evidence validates; no partially built registry.
        object.__setattr__(self, "_tools", tuple(definitions))

    def get(self, tool_id: str) -> ToolDefinition | None:
        identity = _identifier.validate_python(tool_id)
        return next((tool for tool in self._tools if tool.tool_id == identity), None)

    def require(self, tool_id: str) -> ToolDefinition:
        tool = self.get(tool_id)
        if tool is None:
            raise UnknownToolError("Unknown tool ID")
        return tool

    def contains(self, tool_id: str) -> bool:
        return self.get(tool_id) is not None

    def list_tools(self) -> tuple[ToolDefinition, ...]:
        return self._tools

    @property
    def tool_ids(self) -> tuple[str, ...]:
        return tuple(tool.tool_id for tool in self._tools)
