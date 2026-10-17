"""PHX-103 mission events: content-free, allowlisted envelopes (ADR-087).

PHOENIX's runtime and the backend mission service publish live progress with
the frozen 32-type vocabulary only. A payload may hold IDs, closed state and
code values, booleans and millisecond timings, nothing else: every key must be
on its event type's allowlist and every value must match the key's kind. No
message text, prompt, model output, tool output, file content, path or secret
can pass, because none of them is a UUID, a step ID or a closed value. The UI
fetches answer text over REST, which stays the source of truth.

Publishing never fails or blocks a mission (INV-021, INV-022): a sink must not
wait (``EventBus.publish`` raises instead of blocking), and anything a sink or
the allowlist raises is swallowed and counted by ``EventPublisher``. Only
cancellation propagates. Publication grants no authority.
"""

import re
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from types import MappingProxyType, TracebackType
from typing import Final, Protocol

from pydantic import JsonValue

from phoenix.agents.orchestrator import OrchestrationPath
from phoenix.agents.registry import build_default_agent_registry
from phoenix.agents.responders import ResponderErrorCode
from phoenix.agents.router import RoutingDomain
from phoenix.agents.task_graph import TaskState
from phoenix.agents.verifier import VerificationVerdict
from phoenix.core.events import EventEnvelope, EventSeverity, EventType
from phoenix.security.permissions import PermissionStatus


class EventSink(Protocol):
    """Where events go; ``EventBus`` and the backend's event stream satisfy it.

    ``publish`` must not wait for subscribers: refuse (raise) instead.
    """

    async def publish(self, event: EventEnvelope) -> None: ...


class EventPayloadError(ValueError):
    """An event field is not on the allowlist; the message holds no value."""


# The real ID shapes only: runtime and mission IDs are canonical UUID4 text,
# plan steps are "s1".."s99" plus "final"; agent and tool IDs are closed sets.
_UUID: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
_STEP_ID: Final = re.compile(r"s[1-9][0-9]?|final")
UUID: Final = "uuid"
STEP: Final = "step"
MILLISECONDS: Final = "ms"
FLAG: Final = "flag"
_Kind = str | frozenset[str]


def _values(enum: type[StrEnum]) -> frozenset[str]:
    return frozenset(member.value for member in enum)


# Mirrors phoenix.persistence.missions.MissionStatus (tests assert equality);
# phoenix/agents may not import persistence.
MISSION_STATUSES: Final = frozenset(
    {
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
        "waiting_permission",
        "superseded",
    }
)
# MissionFailureCode values, the mission store's own codes, and "cancelled" for
# a phase stopped by cancellation or the mission deadline (tests assert this).
FAILURE_CODES: Final = frozenset(
    {
        "provider_unavailable",
        "provider_error",
        "responder_output",
        "prompt_boundary",
        "no_eligible_model",
        "verification_failed",
        "timeout",
        "tool_rejected",
        "tool_failed",
        "permission_denied",
        "interrupted",
        "internal_error",
        "cancelled",
    }
)
# Mirrors phoenix.tools.registry (ToolResultStatus, ToolRiskLevel) and the
# PHX-046 read-only operations; tests assert equality.
TOOL_STATUSES: Final = frozenset(
    {
        "success",
        "partial_success",
        "denied",
        "timeout",
        "cancelled",
        "failure",
        "unavailable_dependency",
    }
)
RISK_LEVELS: Final = frozenset({"P0", "P1", "P2", "P3", "P4", "P5"})
DOCUMENT_OPERATIONS: Final = frozenset({"read_text", "list_directory", "stat"})
TOOL_IDS: Final = frozenset(f"filesystem.{op}" for op in DOCUMENT_OPERATIONS)
AGENT_IDS: Final = frozenset(
    agent.internal_id for agent in build_default_agent_registry().agents
)
AGENT_ROLES: Final = frozenset(
    {"direct", "planner", "assignment", "specialist", "document", "final_answer"}
)
AGENT_OUTCOMES: Final = frozenset({"completed", "waiting_permission"})
SCOPES: Final = frozenset({"task", "mission"})

_STEP: dict[str, _Kind] = {"step_id": STEP}
_TOOL: dict[str, _Kind] = {**_STEP, "tool_id": TOOL_IDS, "call_id": UUID}
_ROLE: dict[str, _Kind] = {"role": AGENT_ROLES, "step_id": STEP}

# Every event type PHX-103 publishes, with its complete payload allowlist.
PAYLOAD_FIELDS: Final[Mapping[EventType, Mapping[str, _Kind]]] = MappingProxyType(
    {
        EventType.TASK_CREATED: {"path": _values(OrchestrationPath)},
        EventType.TASK_STATE_CHANGED: {
            "scope": SCOPES,
            "from_state": _values(TaskState),
            "to_state": _values(TaskState),
            "mission_status": MISSION_STATUSES,
            "failure_code": FAILURE_CODES,
            "responder_error": _values(ResponderErrorCode),
            "rerun_of": UUID,
        },
        EventType.STEP_STARTED: {
            **_STEP,
            "domain": _values(RoutingDomain),
            "final": FLAG,
        },
        EventType.STEP_COMPLETED: {**_STEP, "duration_ms": MILLISECONDS},
        EventType.AGENT_STARTED: _ROLE,
        EventType.AGENT_COMPLETED: {
            **_ROLE,
            "outcome": AGENT_OUTCOMES,
            "duration_ms": MILLISECONDS,
        },
        EventType.AGENT_FAILED: {
            **_ROLE,
            "code": FAILURE_CODES,
            "duration_ms": MILLISECONDS,
        },
        EventType.TOOL_REQUESTED: {
            **_TOOL,
            "operation": DOCUMENT_OPERATIONS,
            "permission_request_id": UUID,
        },
        EventType.TOOL_STARTED: _TOOL,
        EventType.TOOL_COMPLETED: {
            **_TOOL,
            "tool_status": TOOL_STATUSES,
            "duration_ms": MILLISECONDS,
        },
        EventType.TOOL_FAILED: {
            **_TOOL,
            "code": FAILURE_CODES,
            "decision": _values(PermissionStatus),
            "tool_status": TOOL_STATUSES,
            "duration_ms": MILLISECONDS,
        },
        EventType.PERMISSION_REQUESTED: {
            **_TOOL,
            "permission_request_id": UUID,
            "risk_level": RISK_LEVELS,
        },
        EventType.VERIFICATION_STARTED: _STEP,
        EventType.VERIFICATION_PASSED: {
            **_STEP,
            "verification_id": UUID,
            "verdict": _values(VerificationVerdict),
            "duration_ms": MILLISECONDS,
        },
        EventType.VERIFICATION_FAILED: {
            **_STEP,
            "verification_id": UUID,
            "verdict": _values(VerificationVerdict),
            "code": FAILURE_CODES,
            "duration_ms": MILLISECONDS,
        },
    }
)
_WARNING_TYPES: Final = frozenset(
    {EventType.AGENT_FAILED, EventType.TOOL_FAILED, EventType.VERIFICATION_FAILED}
)


def is_uuid(value: object) -> bool:
    return type(value) is str and _UUID.fullmatch(value) is not None


def _value_ok(kind: _Kind, value: object) -> bool:
    if kind == UUID:
        return is_uuid(value)
    if kind == STEP:
        return type(value) is str and _STEP_ID.fullmatch(value) is not None
    if kind == MILLISECONDS:
        return type(value) is int and 0 <= value <= 7 * 24 * 3600 * 1000
    if kind == FLAG:
        return type(value) is bool
    return type(value) is str and value in kind


def check_payload(event_type: EventType, payload: Mapping[str, object]) -> None:
    """Raise EventPayloadError unless every field is allowlisted for the type."""
    fields = PAYLOAD_FIELDS.get(event_type)
    if fields is None:
        raise EventPayloadError("Event type is not published by PHOENIX missions")
    for key, value in payload.items():
        kind = fields.get(key)
        if kind is None or not _value_ok(kind, value):
            raise EventPayloadError("Event payload field is not allowlisted")


def check_event(event: EventEnvelope) -> None:
    """The whole envelope: allowlisted payload, UUID correlation and task IDs,
    and a known agent ID."""
    check_payload(event.event_type, event.payload)
    if not is_uuid(event.correlation_id):
        raise EventPayloadError("Event correlation ID is not a UUID")
    if event.task_id is not None and not is_uuid(event.task_id):
        raise EventPayloadError("Event task ID is not a UUID")
    if event.agent_id is not None and event.agent_id not in AGENT_IDS:
        raise EventPayloadError("Event agent ID is not a known agent")


def mission_event(
    event_type: EventType,
    payload: Mapping[str, JsonValue],
    *,
    correlation_id: str,
    task_id: str | None = None,
    agent_id: str | None = None,
) -> EventEnvelope:
    """Build a validated envelope; failures are warnings, the rest info."""
    failed = event_type in _WARNING_TYPES or "failed" in (
        payload.get("to_state"),
        payload.get("mission_status"),
    )
    event = EventEnvelope(
        event_type=event_type,
        task_id=task_id,
        agent_id=agent_id,
        payload=payload,
        severity=EventSeverity.WARNING if failed else EventSeverity.INFO,
        correlation_id=correlation_id,
    )
    check_event(event)
    return event


class EventPublisher:
    """Publishes to an optional sink; never raises except for cancellation.

    A refused publication (a full bus, a raising sink or an off-allowlist
    payload) is counted in ``dropped``, never reported in a mission result.
    """

    def __init__(self, sink: EventSink | None) -> None:
        self._sink = sink
        self._dropped = 0

    @property
    def enabled(self) -> bool:
        return self._sink is not None

    @property
    def dropped(self) -> int:
        return self._dropped

    async def publish(
        self,
        event_type: EventType,
        payload: Mapping[str, JsonValue],
        *,
        correlation_id: str,
        task_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        sink = self._sink
        if sink is None:
            return
        try:
            event = mission_event(
                event_type,
                payload,
                correlation_id=correlation_id,
                task_id=task_id,
                agent_id=agent_id,
            )
            await sink.publish(event)
        except Exception:
            self._dropped += 1


class MissionEvents:
    """One mission's view: the correlation ID is the mission (request) ID."""

    def __init__(self, publisher: EventPublisher, correlation_id: str) -> None:
        self._publisher = publisher
        self.correlation_id = correlation_id
        self.task_id: str | None = None

    @property
    def enabled(self) -> bool:
        return self._publisher.enabled

    async def emit(
        self,
        event_type: EventType,
        *,
        agent_id: str | None = None,
        **payload: JsonValue,
    ) -> None:
        await self._publisher.publish(
            event_type,
            payload,
            correlation_id=self.correlation_id,
            task_id=self.task_id,
            agent_id=agent_id,
        )

    def phase(
        self,
        started: EventType,
        done: EventType,
        *,
        agent_id: str | None,
        failed: Callable[[BaseException], tuple[EventType, dict[str, JsonValue]]],
        **payload: JsonValue,
    ) -> "Phase":
        return Phase(self, started, done, agent_id, failed, payload)


def elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


class Phase:
    """``started`` on entry; on exit ``done`` plus ``result``, or ``failed(error)``.

    The body may change ``done`` (e.g. to a failed verdict) and add ``result``
    fields; both apply only when it returns normally. Every payload carries the
    phase's fields and ``duration_ms``. An exception always propagates.
    """

    def __init__(
        self,
        events: MissionEvents,
        started: EventType,
        done: EventType,
        agent_id: str | None,
        failed: Callable[[BaseException], tuple[EventType, dict[str, JsonValue]]],
        payload: dict[str, JsonValue],
    ) -> None:
        self._events = events
        self._started = started
        self._agent_id = agent_id
        self._failed = failed
        self._payload = payload
        self._clock = 0.0
        self.done = done
        self.result: dict[str, JsonValue] = {}

    async def __aenter__(self) -> "Phase":
        self._clock = time.monotonic()
        await self._events.emit(self._started, agent_id=self._agent_id, **self._payload)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc is None:
            event_type, outcome = self.done, self.result
        else:
            event_type, outcome = self._failed(exc)
        fields = {**self._payload, **outcome, "duration_ms": elapsed_ms(self._clock)}
        await self._events.emit(event_type, agent_id=self._agent_id, **fields)
