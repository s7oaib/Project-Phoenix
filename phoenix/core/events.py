"""Canonical, immutable event information; publication never grants authority."""

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import cast
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    field_validator,
)


class EventType(StrEnum):
    """Exact frozen event names; defining a type does not emit an event."""

    PHOENIX_STARTED = "phoenix.started"
    PHOENIX_READY = "phoenix.ready"
    PHOENIX_DEGRADED = "phoenix.degraded"
    AGENT_STARTED = "agent.started"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"
    TASK_CREATED = "task.created"
    TASK_STATE_CHANGED = "task.state_changed"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    TOOL_REQUESTED = "tool.requested"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_APPROVED = "permission.approved"
    PERMISSION_DENIED = "permission.denied"
    MODEL_LOADING = "model.loading"
    MODEL_LOADED = "model.loaded"
    MODEL_UNLOADED = "model.unloaded"
    PROVIDER_UNAVAILABLE = "provider.unavailable"
    VERIFICATION_STARTED = "verification.started"
    VERIFICATION_PASSED = "verification.passed"
    VERIFICATION_FAILED = "verification.failed"
    RECOVERY_STARTED = "recovery.started"
    RECOVERY_COMPLETED = "recovery.completed"
    RECOVERY_EXHAUSTED = "recovery.exhausted"
    MEMORY_CREATED = "memory.created"
    DOCUMENT_INDEXED = "document.indexed"
    VOICE_LISTENING = "voice.listening"
    VOICE_SPEAKING = "voice.speaking"
    VOICE_INTERRUPTED = "voice.interrupted"


class EventSeverity(StrEnum):
    """Observability metadata only, without permission or execution policy."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


def _freeze(value: JsonValue) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _json_value(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return cast(JsonValue, value)  # Scalars were validated before freezing.


class EventEnvelope(BaseModel):
    """Validated envelope; payload mappings/lists become read-only maps/tuples."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, validate_default=True
    )

    event_id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    task_id: str | None = None
    agent_id: str | None = None
    payload: Mapping[str, JsonValue] = Field(default_factory=dict)
    severity: EventSeverity = EventSeverity.INFO
    correlation_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)

    @field_validator("timestamp")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("payload")
    @classmethod
    def freeze_payload(cls, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        # Validation uses JsonValue; freezing preserves its JSON meaning while
        # preventing caller/subscriber mutations. Serialization restores arrays.
        return cast(Mapping[str, JsonValue], _freeze(dict(value)))

    @field_serializer("payload")
    def serialize_payload(self, value: Mapping[str, JsonValue]) -> JsonValue:
        return _json_value(value)
