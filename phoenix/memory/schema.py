"""Immutable memory evidence only; content and metadata never grant authority.

Creation/update timestamps and scores are explicit caller evidence. This schema
does not admit memories, detect semantic secrets, reconcile contradictions,
dereference sources or embeddings, retrieve content or execute instructions.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Annotated, Self, cast
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ModelWrapValidatorHandler,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

_Identifier = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
_Score = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class MemoryType(StrEnum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    USER = "user"
    PROJECT = "project"
    AGENT = "agent"


def _json_value(value: object, active: set[int] | None = None) -> JsonValue:
    """Detach finite JSON data, rejecting cycles and arbitrary runtime values."""
    if value is None or type(value) in (str, bool, int):
        return cast(JsonValue, value)
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("Metadata numbers must be finite")
        return value
    if not isinstance(value, (Mapping, list, tuple)):
        raise ValueError("Metadata must contain JSON data only")
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError("Metadata cannot contain cycles")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            if any(type(key) is not str for key in value):
                raise ValueError("Metadata object keys must be strings")
            return {key: _json_value(item, active) for key, item in value.items()}
        return [_json_value(item, active) for item in value]
    finally:
        active.remove(identity)


def _freeze(value: JsonValue) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class MemoryRecord(BaseModel):
    """Fourteen frozen logical fields; required timestamps have no clock defaults."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
    )

    id: _Identifier = Field(default_factory=lambda: str(uuid4()))
    type: MemoryType
    content: _Text
    source_type: _Identifier
    source_ref: _Identifier | None = None
    scope: _Identifier
    confidence: _Score
    importance: _Score
    created_at: AwareDatetime
    updated_at: AwareDatetime
    last_accessed_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    embedding_ref: _Identifier | None = None
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="wrap")
    @classmethod
    def snapshot_contract(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        if isinstance(value, BaseModel):
            if type(value) is not cls:
                raise ValueError("Expected an exact memory record")
            value = {**vars(value), **(value.__pydantic_extra__ or {})}
        return handler(value)

    @field_validator("type", mode="before")
    @classmethod
    def textual_type(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("Memory type requires a string or its enum member")
        return value

    @field_validator("content")
    @classmethod
    def meaningful_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Content must contain non-whitespace text")
        return value

    @field_validator("confidence", "importance", mode="before")
    @classmethod
    def ordinary_numeric_score(cls, value: object) -> object:
        if type(value) not in (int, float):
            raise ValueError("Scores require ordinary numeric evidence")
        return value

    @field_validator(
        "created_at", "updated_at", "last_accessed_at", "expires_at", mode="before"
    )
    @classmethod
    def datetime_input(cls, value: object) -> object:
        if value is not None and not isinstance(value, (datetime, str)):
            raise ValueError(
                "Timestamp requires an aware datetime or ISO representation"
            )
        return value

    @field_validator("created_at", "updated_at", "last_accessed_at", "expires_at")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @field_validator("metadata", mode="before")
    @classmethod
    def json_object(cls, value: object) -> JsonValue:
        if not isinstance(value, Mapping):
            raise ValueError("Metadata must be a JSON object")
        return _json_value(value)

    @field_validator("metadata")
    @classmethod
    def freeze_metadata(cls, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        return cast(Mapping[str, JsonValue], _freeze(dict(value)))

    @field_serializer("metadata")
    def serialize_metadata(self, value: Mapping[str, JsonValue]) -> JsonValue:
        return _json_value(value)

    @model_validator(mode="after")
    def timestamp_relationships(self) -> Self:
        if any(
            timestamp is not None and timestamp < self.created_at
            for timestamp in (self.updated_at, self.last_accessed_at, self.expires_at)
        ):
            raise ValueError("Memory timestamps cannot precede creation")
        return self
