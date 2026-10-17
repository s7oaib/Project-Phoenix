"""PHX-103 runtime and mission-service events (ADR-087).

Real components: PhoenixRuntime with a FilesystemTool workspace on tmp_path,
ToolRegistry, PermissionEngine, LEVI, an alembic-migrated SQLite database and
the MissionService. Only the model is scripted, answering by the system
prompt's role marker. No network, Ollama or GPU is used. Sentinels are planted
in the user request, the file name, the file content and every model output,
and must never appear in any event.
"""

import ast
import asyncio
import json
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from phoenix.agents import mission_events, tool_steps
from phoenix.agents.mission_events import (
    EventPayloadError,
    check_event,
    check_payload,
    mission_event,
)
from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest
from phoenix.agents.runtime import (
    DOCUMENT_TOOL_REASON,
    MissionFailureCode,
    MissionKind,
    MissionResult,
    PhoenixRuntime,
)
from phoenix.api.missions import MissionService
from phoenix.core.event_bus import EventBus, EventBusFullError
from phoenix.core.events import EventEnvelope, EventSeverity, EventType
from phoenix.models.lifecycle import ModelLifecycleManager
from phoenix.models.provider import (
    CancellationResult,
    CancellationStatus,
    GenerationChunk,
    GenerationRequest,
    GenerationResponse,
    ModelCapabilities,
    ModelProvider,
    ModelProviderError,
    ProviderHealth,
    ProviderHealthStatus,
    TokenUsage,
)
from phoenix.models.registry import ModelCapabilityRegistry
from phoenix.persistence.missions import (
    INTERNAL_ERROR,
    INTERRUPTED,
    PERMISSION_DENIED,
    MissionRecord,
    MissionStatus,
    MissionStore,
)
from phoenix.security.audit import AuditTrail
from phoenix.security.permissions import (
    GrantDuration,
    PermissionGrant,
    PermissionPolicy,
)
from phoenix.tools.filesystem import (
    FilesystemCall,
    FilesystemTool,
    filesystem_tool_definitions,
)
from phoenix.tools.registry import ToolRegistry, ToolResultStatus, ToolRiskLevel

ROOT = Path(__file__).resolve().parents[1]
MODELS = ("qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b", "qwen2.5-coder:7b")
PASS = '{"checks":[{"criterion":"Correct","passed":true,"evidence_summary":"Fine."}]}'
FAIL = '{"checks":[{"criterion":"Correct","passed":false,"evidence_summary":"No."}]}'
SESSION = "session-1"
# Planted content: none of these may ever appear in an event.
USER = "USER-SENTINEL-37"
FILE = "FILE-SENTINEL-93"
MODEL = "MODEL-SENTINEL-55"
FILENAME = "PATH-SENTINEL-61.txt"
QUESTION = f"What is the codename? {USER}"
NOTES = f"Project codename: PELICAN-7. {FILE}\n"
SENTINELS = (USER, FILE, MODEL, "PATH-SENTINEL-61", "PELICAN", "codename", "\\")
Reply = str | Exception | Callable[[GenerationRequest], str]


def role_of(request: GenerationRequest) -> str:
    return request.messages[0].text.splitlines()[0].removeprefix("PHOENIX ROLE: ")


def tool_request(operation: str, relative_path: str) -> str:
    return json.dumps(
        {"tool_request": {"operation": operation, "relative_path": relative_path}}
    )


class ScriptedProvider(ModelProvider):
    """Answers by role; every model output carries the model sentinel."""

    def __init__(self) -> None:
        super().__init__("ollama")
        self.replies: dict[str, Reply] = {
            "direct": f"PELICAN-7 {MODEL}",
            "planner": json.dumps(
                {
                    "steps": [
                        {"objective": f"Read {FILENAME} in the workspace. {MODEL}"},
                        {"objective": f"State the project codename. {MODEL}"},
                    ]
                }
            ),
            "assignment": json.dumps(
                {
                    "assignments": [
                        {"step_id": "s1", "domain": "documents"},
                        {"step_id": "s2", "domain": "intelligence"},
                    ]
                }
            ),
            "document_specialist": tool_request("read_text", FILENAME),
            "document_answer": f"The notes give the codename PELICAN-7. {MODEL}",
            "specialist": f"The codename is PELICAN-7. {MODEL}",
            "final_answer": f"PELICAN-7 {MODEL}",
            "verifier": PASS,
            "document_verifier": PASS,
            "final_verifier": PASS,
            "step_verifier": PASS,
        }
        self.gate: asyncio.Event | None = None

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id, status=ProviderHealthStatus.HEALTHY
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        return capabilities(model_id)

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        if self.gate is not None:
            await self.gate.wait()
        await asyncio.sleep(0)
        reply = self.replies[role_of(request)]
        if isinstance(reply, Exception):
            raise reply
        output = reply(request) if callable(reply) else reply
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text=output,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=5, output_tokens=3),
        )

    async def _no_stream(self) -> AsyncIterator[GenerationChunk]:
        raise ModelProviderError("streaming is not scripted")
        yield  # pragma: no cover

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        return self._no_stream()

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.ACCEPTED,
        )


def capabilities(model_id: str) -> ModelCapabilities:
    return ModelCapabilities(
        provider_id="ollama",
        model_id=model_id,
        supports_streaming=True,
        supports_structured_output=False,
        supports_tool_use=True,
        supports_vision=False,
        supports_cancellation=True,
        context_limit_tokens=16384,
    )


class RecordingSink:
    """An EventSink that keeps every envelope; strict about the allowlist."""

    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        check_event(event)
        self.events.append(event)


class RaisingSink:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def publish(self, event: EventEnvelope) -> None:
        self.calls += 1
        raise self.error


@dataclass
class Stack:
    root: Path
    engine: Engine
    sessions: sessionmaker[Session]
    provider: ScriptedProvider = field(default_factory=ScriptedProvider)
    tools: ToolRegistry = field(
        default_factory=lambda: ToolRegistry(filesystem_tool_definitions())
    )

    def filesystem(self) -> FilesystemTool:
        return FilesystemTool(
            self.root,
            self.tools,
            PermissionPolicy(),
            AuditTrail(self.sessions, self.tools),
        )

    def runtime(self, events: Any = None) -> PhoenixRuntime:
        registry = ModelCapabilityRegistry()
        for model in MODELS:
            registry.register(capabilities(model))
        return PhoenixRuntime(
            providers=(self.provider,),
            capability_registry=registry,
            lifecycle=ModelLifecycleManager(),
            filesystem_tool=self.filesystem(),
            events=events,
        )

    def service(self, events: Any) -> MissionService:
        return MissionService(
            runtime=self.runtime(events),
            store=MissionStore(self.sessions),
            tool_registry=self.tools,
            events=events,
        )

    def grant(self, relative_path: str = FILENAME) -> PermissionGrant:
        intent = self.filesystem().describe(
            FilesystemCall.model_validate(
                {"operation": "read_text", "relative_path": relative_path}
            )
        )
        return PermissionGrant(
            grant_id="Grant.Notes",
            actor_id="document_analyst",
            tool_id=intent.tool_id,
            action=intent.action,
            resource=intent.resource,
            reason=DOCUMENT_TOOL_REASON,
            duration=GrantDuration.SESSION,
            risk_level=intent.risk_level,
            issued_at=datetime.now(UTC) - timedelta(minutes=1),
            session_id=SESSION,
            user_decision="approved",
        )


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx network send attempted")

    def refuse_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("outbound socket connection attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_send)
    monkeypatch.setattr(socket, "create_connection", refuse_connect)


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / FILENAME).write_bytes(NOTES.encode("utf-8"))
    db = tmp_path / "phoenix.db"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db}".replace("%", "%%"))
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db}")
    try:
        yield Stack(root=root, engine=engine, sessions=sessionmaker(engine))
    finally:
        engine.dispose()


def run(scenario: Awaitable[Any], timeout: float = 30) -> Any:
    async def bounded() -> Any:
        return await asyncio.wait_for(scenario, timeout)

    return asyncio.run(bounded())


def mission(
    stack: Stack,
    path: OrchestrationPath = OrchestrationPath.COMPLEX,
    *,
    events: Any = None,
    grants: tuple[PermissionGrant, ...] = (),
    request_id: str = "mission-1",
) -> tuple[MissionResult, PhoenixRuntime]:
    async def exercise() -> tuple[MissionResult, PhoenixRuntime]:
        runtime = stack.runtime(events)
        result = await runtime.run(
            PhoenixRequest(request_id=request_id, text=QUESTION),
            path,
            grants=grants,
            session_id=SESSION,
        )
        return result, runtime

    outcome: tuple[MissionResult, PhoenixRuntime] = run(exercise())
    return outcome


def sig(event: EventEnvelope) -> str:
    """A readable signature: type plus the identifying payload field."""
    payload = event.payload
    name = event.event_type.value
    if "to_state" in payload:
        return f"{name} {payload['from_state']}>{payload['to_state']}"
    if "mission_status" in payload:
        return f"{name} {payload['mission_status']}"
    if "role" in payload:
        return f"{name} {payload['role']}"
    if "step_id" in payload:
        return f"{name} {payload['step_id']}"
    return name


def assert_content_free(events: list[EventEnvelope], *extra: str) -> None:
    assert events
    for event in events:
        check_event(event)  # the allowlist holds for every published event
        wire = event.model_dump_json()
        for sentinel in (*SENTINELS, *extra):
            assert sentinel not in wire, (sentinel, sig(event))


def payloads(events: list[EventEnvelope], event_type: EventType) -> list[Any]:
    return [dict(e.payload) for e in events if e.event_type == event_type]


COMPLEX_DOCUMENT_SEQUENCE = [
    "task.created",
    "task.state_changed created>planning",
    "agent.started planner",
    "agent.completed planner",
    "agent.started assignment",
    "agent.completed assignment",
    "task.state_changed planning>ready",
    "task.state_changed ready>running",
    "step.started s1",
    "agent.started document",
    "tool.requested s1",
    "tool.started s1",
    "tool.completed s1",
    "agent.completed document",
    "verification.started s1",
    "verification.passed s1",
    "step.completed s1",
    "step.started s2",
    "agent.started specialist",
    "agent.completed specialist",
    "verification.started s2",
    "verification.passed s2",
    "step.completed s2",
    "step.started final",
    "agent.started final_answer",
    "agent.completed final_answer",
    "task.state_changed running>verifying",
    "verification.started final",
    "verification.passed final",
    "step.completed final",
    "task.state_changed verifying>completed",
]


# ---- runtime events


def test_complex_document_mission_publishes_ordered_correlated_events(
    stack: Stack,
) -> None:
    sink = RecordingSink()
    result, runtime = mission(stack, events=sink, grants=(stack.grant(),))
    assert result.kind == MissionKind.COMPLETED
    events = sink.events
    assert [sig(e) for e in events] == COMPLEX_DOCUMENT_SEQUENCE
    # Correlation: the mission (request) ID and the runtime's task ID.
    assert {e.correlation_id for e in events} == {"mission-1"}
    assert {e.task_id for e in events} == {result.trace.task_id}
    assert {e.severity for e in events} == {EventSeverity.INFO}
    assert runtime.dropped_events == 0
    (created,) = payloads(events, EventType.TASK_CREATED)
    assert created == {"path": "complex"}
    agents = {(e.agent_id, e.payload.get("role")) for e in events if e.agent_id}
    assert ("strategist", "planner") in agents and ("phoenix", "assignment") in agents
    assert ("document_analyst", "document") in agents
    assert {e.agent_id for e in events if "verification" in e.event_type} == {
        "verifier"
    }
    # Tool and verification IDs correlate with the trace.
    (call,) = result.trace.tool_calls
    (requested,) = payloads(events, EventType.TOOL_REQUESTED)
    assert requested == {
        "step_id": "s1",
        "tool_id": "filesystem.read_text",
        "call_id": call.call_id,
        "operation": "read_text",
        "permission_request_id": call.request_id,
    }
    (completed,) = payloads(events, EventType.TOOL_COMPLETED)
    assert completed["tool_status"] == "success" and completed["call_id"] == (
        call.call_id
    )
    passed = payloads(events, EventType.VERIFICATION_PASSED)
    assert [p["verification_id"] for p in passed] == [
        v.verification_id for v in result.trace.verifications
    ]
    assert {p["verdict"] for p in passed} == {"passed"}
    for event in events:
        if event.event_type.value.endswith((".completed", ".passed")):
            assert type(event.payload["duration_ms"]) is int
    assert_content_free(events, result.text or "unreachable")


def test_direct_mission_publishes_phoenix_agent_events(stack: Stack) -> None:
    sink = RecordingSink()
    result, _ = mission(stack, OrchestrationPath.DIRECT, events=sink)
    assert result.kind == MissionKind.COMPLETED
    assert [sig(e) for e in sink.events] == [
        "agent.started direct",
        "agent.completed direct",
    ]
    assert {(e.agent_id, e.correlation_id, e.task_id) for e in sink.events} == {
        ("phoenix", "mission-1", None)
    }
    assert sink.events[1].payload["outcome"] == "completed"
    assert_content_free(sink.events)


def test_waiting_permission_publishes_permission_requested(stack: Stack) -> None:
    sink = RecordingSink()
    result, _ = mission(stack, events=sink)  # no grant
    assert result.kind == MissionKind.WAITING_PERMISSION
    pending = result.pending_permission
    assert pending is not None
    tail = [sig(e) for e in sink.events][-6:]
    assert tail == [
        "agent.started document",
        "tool.requested s1",
        "tool.started s1",
        "permission.requested s1",
        "agent.completed document",
        "task.state_changed running>waiting_permission",
    ]
    (permission,) = payloads(sink.events, EventType.PERMISSION_REQUESTED)
    assert permission["permission_request_id"] == pending.request_id
    assert permission["risk_level"] == "P1"
    assert permission["tool_id"] == "filesystem.read_text"
    assert payloads(sink.events, EventType.AGENT_COMPLETED)[-1]["outcome"] == (
        "waiting_permission"
    )
    assert not payloads(sink.events, EventType.TOOL_COMPLETED)
    # The pending request names the absolute path; no event does.
    assert_content_free(sink.events, pending.resource, str(stack.root))


def test_failed_verification_publishes_verification_failed(stack: Stack) -> None:
    stack.provider.replies["document_verifier"] = FAIL
    sink = RecordingSink()
    result, _ = mission(stack, events=sink, grants=(stack.grant(),))
    assert result.failure_code == MissionFailureCode.VERIFICATION_FAILED
    (failed,) = [
        e for e in sink.events if e.event_type == EventType.VERIFICATION_FAILED
    ]
    (verification,) = result.trace.verifications
    assert dict(failed.payload) == {
        "step_id": "s1",
        "verification_id": verification.verification_id,
        "verdict": "failed",
        "code": "verification_failed",
        "duration_ms": failed.payload["duration_ms"],
    }
    assert failed.severity == EventSeverity.WARNING
    last = sink.events[-1]
    assert sig(last) == "task.state_changed running>failed"
    assert last.payload["failure_code"] == "verification_failed"
    assert last.severity == EventSeverity.WARNING
    assert not payloads(sink.events, EventType.STEP_COMPLETED)
    assert_content_free(sink.events)


@pytest.mark.parametrize(
    ("proposal", "code", "events_before"),
    [
        # Rejected before a call exists: only tool.failed.
        (tool_request("write_text", FILENAME), "tool_rejected", []),
        (tool_request("read_text", "../outside.txt"), "tool_rejected", []),
        # A real call that the tool reports as failed.
        (
            tool_request("read_text", "missing.txt"),
            "tool_failed",
            ["tool.requested s1", "tool.started s1"],
        ),
    ],
)
def test_tool_failures_publish_tool_failed(
    stack: Stack, proposal: str, code: str, events_before: list[str]
) -> None:
    stack.provider.replies["document_specialist"] = proposal
    sink = RecordingSink()
    grant = stack.grant("missing.txt") if code == "tool_failed" else stack.grant()
    result, _ = mission(stack, events=sink, grants=(grant,))
    assert result.failure_code == MissionFailureCode(code)
    signatures = [sig(e) for e in sink.events]
    start = signatures.index("agent.started document") + 1
    assert signatures[start:] == [
        *events_before,
        "tool.failed s1",
        "agent.failed document",
        "task.state_changed running>failed",
    ]
    (failed,) = payloads(sink.events, EventType.TOOL_FAILED)
    assert failed["code"] == code
    if code == "tool_failed":
        assert failed["tool_status"] == ToolResultStatus.FAILURE.value
        assert failed["decision"] == "allowed"
    assert payloads(sink.events, EventType.AGENT_FAILED)[-1]["code"] == code
    assert sink.events[-1].payload["failure_code"] == code
    assert_content_free(sink.events, "outside", "missing")


def test_responder_output_failure_publishes_agent_failed(stack: Stack) -> None:
    stack.provider.replies["planner"] = f"not json {MODEL}"
    sink = RecordingSink()
    result, _ = mission(stack, events=sink)
    assert result.failure_code == MissionFailureCode.RESPONDER_OUTPUT
    assert [sig(e) for e in sink.events] == [
        "task.created",
        "task.state_changed created>planning",
        "agent.started planner",
        "agent.failed planner",
        "task.state_changed planning>failed",
    ]
    assert sink.events[3].payload["code"] == "responder_output"
    assert dict(sink.events[-1].payload) == {
        "scope": "task",
        "from_state": "planning",
        "to_state": "failed",
        "failure_code": "responder_output",
        "responder_error": "not_json",
    }
    assert_content_free(sink.events)


def test_cancellation_publishes_cancelled(stack: Stack) -> None:
    async def scenario() -> tuple[MissionResult, list[EventEnvelope]]:
        sink = RecordingSink()
        runtime = stack.runtime(sink)
        stack.provider.gate = asyncio.Event()
        task = asyncio.create_task(
            runtime.run(
                PhoenixRequest(request_id="mission-1", text=QUESTION),
                OrchestrationPath.COMPLEX,
            )
        )
        while not sink.events or sig(sink.events[-1]) != "agent.started planner":
            await asyncio.sleep(0.005)
        await runtime.cancel("mission-1")
        return await task, sink.events

    result, events = run(scenario())
    assert result.kind == MissionKind.CANCELLED
    assert [sig(e) for e in events][-2:] == [
        "agent.failed planner",
        "task.state_changed planning>cancelled",
    ]
    assert events[-2].payload["code"] == "cancelled"


# ---- publishing never blocks or fails a mission


@pytest.mark.parametrize(
    "error", [EventBusFullError("full"), RuntimeError("sink defect")]
)
def test_runtime_completes_when_the_sink_raises(stack: Stack, error: Exception) -> None:
    sink = RaisingSink(error)
    result, runtime = mission(stack, events=sink, grants=(stack.grant(),))
    baseline, quiet = mission(stack, grants=(stack.grant(),), request_id="mission-2")
    assert result.kind == baseline.kind == MissionKind.COMPLETED
    assert result.text == baseline.text
    assert result.trace.states == baseline.trace.states
    assert sink.calls == len(COMPLEX_DOCUMENT_SEQUENCE)
    assert runtime.dropped_events == sink.calls  # counted, not reported
    assert quiet.dropped_events == 0
    assert "dropped" not in result.model_dump_json()


def test_runtime_completes_when_the_bus_is_full(stack: Stack) -> None:
    async def scenario() -> tuple[MissionResult, PhoenixRuntime, int]:
        bus = EventBus(capacity=1)
        stalled = await bus.subscribe()  # never read: full after one event
        runtime = stack.runtime(bus)
        result = await runtime.run(
            PhoenixRequest(request_id="mission-1", text=QUESTION),
            OrchestrationPath.COMPLEX,
            grants=(stack.grant(),),
            session_id=SESSION,
        )
        first = await stalled.get()
        assert sig(first) == "task.created"
        await stalled.close()
        return result, runtime, bus.subscriber_count

    result, runtime, subscribers = run(scenario())
    assert result.kind == MissionKind.COMPLETED
    assert runtime.dropped_events == len(COMPLEX_DOCUMENT_SEQUENCE) - 1
    assert subscribers == 0


def test_off_allowlist_events_are_refused_before_any_sink() -> None:
    # Off-allowlist events are refused before they reach any sink.
    with pytest.raises(EventPayloadError):
        mission_event(
            EventType.AGENT_COMPLETED,
            {"role": "direct", "text": "PELICAN-7"},
            correlation_id="m",
        )


def test_without_a_sink_nothing_is_published_and_results_match(stack: Stack) -> None:
    result, runtime = mission(stack, grants=(stack.grant(),))
    assert result.kind == MissionKind.COMPLETED
    assert runtime.dropped_events == 0


# ---- the allowlist


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (EventType.AGENT_COMPLETED, {"role": "direct", "answer": "PELICAN-7"}),
        (EventType.STEP_STARTED, {"step_id": "The codename is PELICAN-7."}),
        (EventType.TOOL_REQUESTED, {"call_id": "C:\\Users\\me\\notes.txt"}),
        (EventType.TOOL_REQUESTED, {"call_id": "/etc/passwd"}),
        (EventType.TOOL_REQUESTED, {"operation": "write_text"}),
        (EventType.TASK_STATE_CHANGED, {"failure_code": "PELICAN"}),
        (EventType.TASK_STATE_CHANGED, {"to_state": "done"}),
        (EventType.STEP_COMPLETED, {"duration_ms": -1}),
        (EventType.STEP_COMPLETED, {"duration_ms": 1.5}),
        (EventType.STEP_COMPLETED, {"duration_ms": True}),
        (EventType.STEP_STARTED, {"final": "yes"}),
        (EventType.STEP_STARTED, {"step_id": "x" * 129}),
        (EventType.STEP_STARTED, {"step_id": ["s1"]}),
        (EventType.MEMORY_CREATED, {}),
        (EventType.VOICE_SPEAKING, {}),
    ],
)
def test_allowlist_refuses_content_and_unknown_fields(
    event_type: EventType, payload: dict[str, Any]
) -> None:
    with pytest.raises(EventPayloadError) as caught:
        check_payload(event_type, payload)
    assert "PELICAN" not in str(caught.value) and "passwd" not in str(caught.value)


def test_envelope_ids_must_be_identifiers() -> None:
    for fields in (
        {"correlation_id": "a request with spaces"},
        {"correlation_id": "m", "task_id": "../task"},
        {"correlation_id": "m", "agent_id": "LEVI says hi"},
    ):
        with pytest.raises(EventPayloadError):
            mission_event(EventType.TASK_CREATED, {"path": "complex"}, **fields)


def test_vocabulary_stays_32_and_mapping_uses_existing_types() -> None:
    assert len(EventType) == 32
    assert set(mission_events.PAYLOAD_FIELDS) <= set(EventType)
    assert {t.value for t in mission_events.PAYLOAD_FIELDS} == {
        "task.created",
        "task.state_changed",
        "step.started",
        "step.completed",
        "agent.started",
        "agent.completed",
        "agent.failed",
        "tool.requested",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "permission.requested",
        "verification.started",
        "verification.passed",
        "verification.failed",
    }


def test_mirrored_closed_values_match_their_sources() -> None:
    assert mission_events.MISSION_STATUSES == {s.value for s in MissionStatus}
    assert {c.value for c in MissionFailureCode} | {
        INTERRUPTED,
        PERMISSION_DENIED,
        INTERNAL_ERROR,
        "cancelled",
    } == mission_events.FAILURE_CODES
    assert mission_events.TOOL_STATUSES == {s.value for s in ToolResultStatus}
    assert mission_events.RISK_LEVELS == {r.value for r in ToolRiskLevel}
    assert mission_events.DOCUMENT_OPERATIONS == {
        o.value for o in tool_steps.DOCUMENT_OPERATIONS
    }


def test_event_module_stays_provider_and_transport_neutral() -> None:
    tree = ast.parse((ROOT / "phoenix/agents/mission_events.py").read_text("utf-8"))
    imported = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any(
        module.startswith(
            (
                "phoenix.models",
                "phoenix.tools",
                "phoenix.persistence",
                "phoenix.api",
                "httpx",
                "logging",
                "fastapi",
                "starlette",
            )
        )
        for module in imported
    )


# ---- mission service events


async def until(predicate: Callable[[], bool], timeout: float = 10) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


async def settled(service: MissionService, mission_id: str) -> MissionRecord:
    async with asyncio.timeout(10):
        while True:
            record = await service.get_mission(mission_id)
            if record.status not in (MissionStatus.QUEUED, MissionStatus.RUNNING):
                return record
            await asyncio.sleep(0.005)


def mission_statuses(events: list[EventEnvelope], mission_id: str) -> list[str]:
    return [
        str(e.payload["mission_status"])
        for e in events
        if e.correlation_id == mission_id and e.payload.get("scope") == "mission"
    ]


def test_service_publishes_queued_running_and_outcome(stack: Stack) -> None:
    async def scenario() -> tuple[str, list[EventEnvelope], MissionService]:
        sink = RecordingSink()
        service = stack.service(sink)
        await service.start()
        conversation = await service.create_conversation()
        mission_id = await service.submit(conversation, QUESTION, "direct")
        await settled(service, mission_id)
        await service.stop()
        return mission_id, sink.events, service

    mission_id, events, service = run(scenario())
    assert [sig(e) for e in events] == [
        "task.state_changed queued",
        "task.state_changed running",
        "agent.started direct",
        "agent.completed direct",
        "task.state_changed completed",
    ]
    assert {e.correlation_id for e in events} == {mission_id}
    assert dict(events[0].payload) == {"scope": "mission", "mission_status": "queued"}
    assert service.dropped_events == 0
    assert_content_free(events)


def test_service_publishes_superseded_and_linked_rerun(stack: Stack) -> None:
    async def scenario() -> tuple[str, str, list[EventEnvelope]]:
        sink = RecordingSink()
        service = stack.service(sink)
        await service.start()
        conversation = await service.create_conversation()
        original = await service.submit(conversation, QUESTION, "complex")
        record = await settled(service, original)
        assert record.permission_request is not None
        request_id = record.permission_request.request.request_id
        rerun = await service.approve(request_id, GrantDuration.SESSION)
        assert (await settled(service, rerun)).status == MissionStatus.COMPLETED
        await service.stop()
        return original, rerun, sink.events

    original, rerun, events = run(scenario())
    assert mission_statuses(events, original) == [
        "queued",
        "running",
        "waiting_permission",
        "superseded",
    ]
    assert mission_statuses(events, rerun) == ["queued", "running", "completed"]
    queued = next(
        e for e in events if e.correlation_id == rerun and "mission_status" in e.payload
    )
    assert queued.payload["rerun_of"] == original
    # The re-run's runtime events are correlated to the re-run.
    assert any(
        e.correlation_id == rerun and e.event_type == EventType.TOOL_COMPLETED
        for e in events
    )
    assert_content_free(events)


def test_service_publishes_denied_cancelled_and_interrupted(stack: Stack) -> None:
    async def scenario() -> dict[str, str]:
        sink = RecordingSink()
        service = stack.service(sink)
        await service.start()
        conversation = await service.create_conversation()
        waiting = await service.submit(conversation, QUESTION, "complex")
        record = await settled(service, waiting)
        assert record.permission_request is not None
        await service.deny(record.permission_request.request.request_id)
        stack.provider.gate = asyncio.Event()
        running = await service.submit(conversation, QUESTION, "direct")
        queued = await service.submit(conversation, QUESTION, "direct")
        cancelled = await service.submit(conversation, QUESTION, "direct")
        await until(lambda: service.running_mission_id == running)
        await service.cancel(cancelled)
        await service.stop()
        sink_events.extend(sink.events)
        return {
            "waiting": waiting,
            "running": running,
            "queued": queued,
            "cancelled": cancelled,
        }

    sink_events: list[EventEnvelope] = []
    ids = run(scenario())
    assert mission_statuses(sink_events, ids["waiting"])[-1:] == ["failed"]
    denied = [
        e
        for e in sink_events
        if e.correlation_id == ids["waiting"] and e.payload.get("failure_code")
    ]
    assert denied[-1].payload["failure_code"] == "permission_denied"
    assert mission_statuses(sink_events, ids["cancelled"]) == ["queued", "cancelled"]
    assert mission_statuses(sink_events, ids["queued"]) == ["queued", "failed"]
    interrupted = [
        e.payload.get("failure_code")
        for e in sink_events
        if e.correlation_id in (ids["queued"], ids["running"])
        and e.payload.get("mission_status") == "failed"
    ]
    assert interrupted == ["interrupted", "interrupted"]
    assert_content_free(sink_events)


def test_restart_recovery_publishes_interrupted(stack: Stack) -> None:
    store = MissionStore(stack.sessions)
    now = datetime.now(UTC)
    store.create_conversation("c", now)
    store.submit(
        conversation_id="c",
        mission_id="left-queued",
        message_id="u",
        text=QUESTION,
        path="direct",
        now=now,
    )

    async def scenario() -> list[EventEnvelope]:
        sink = RecordingSink()
        service = stack.service(sink)
        await service.start()
        await service.stop()
        return sink.events

    events = run(scenario())
    assert [(e.correlation_id, dict(e.payload)) for e in events] == [
        (
            "left-queued",
            {
                "scope": "mission",
                "mission_status": "failed",
                "failure_code": "interrupted",
            },
        )
    ]


def test_service_keeps_running_when_the_sink_raises(stack: Stack) -> None:
    async def scenario() -> tuple[MissionRecord, MissionService, RaisingSink]:
        sink = RaisingSink(RuntimeError("sink defect"))
        service = stack.service(sink)
        await service.start()
        conversation = await service.create_conversation()
        mission_id = await service.submit(conversation, QUESTION, "direct")
        record = await settled(service, mission_id)
        await service.stop()
        return record, service, sink

    record, service, sink = run(scenario())
    assert record.status == MissionStatus.COMPLETED
    assert service.dropped_events == 3  # queued, running, completed
    assert sink.calls == 5  # plus the runtime's two agent events
