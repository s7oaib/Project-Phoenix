"""PHX-105 request triage (ADR-090), offline only.

Every collaborator is real except the model: a scripted ModelProvider answers
by the system prompt's role marker. The runtime tests use PhoenixRuntime with
the real orchestrator, AIZEN, router and LEVI; the service tests add the
alembic-migrated SQLite schema and MissionService/MissionStore. No network,
Ollama or GPU is used; an autouse guard fails any outbound connection.
"""

import asyncio
import json
import socket
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from alembic import command
from phoenix.agents import mission_events, prompts
from phoenix.agents.mission_events import (
    EventPayloadError,
    check_payload,
)
from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest
from phoenix.agents.runtime import (
    MissionFailureCode,
    MissionKind,
    MissionResult,
    MissionTrace,
    ModelCandidateRef,
    ModelTierTable,
    PhoenixRuntime,
    PhoenixRuntimeError,
    RuntimeLimits,
    default_tier_table,
)
from phoenix.agents.triage import (
    TRIAGE_ROLE,
    TriageFallbackReason,
    TriageOutcome,
)
from phoenix.api.missions import MissionService
from phoenix.core.events import EventEnvelope, EventType
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
from phoenix.persistence import missions as mission_store
from phoenix.persistence.missions import MissionRecord, MissionStatus, MissionStore
from phoenix.tools.filesystem import filesystem_tool_definitions
from phoenix.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
MODELS = ("qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b", "qwen2.5-coder:7b")
PASS = '{"checks":[{"criterion":"Correct","passed":true,"evidence_summary":"Fine."}]}'
SENTINEL = "TRIAGE-REQUEST-SENTINEL-4417"
REQUEST_ID = "6f1d2c3b-4a5e-4f60-9a7b-8c9d0e1f2a3b"
Reply = str | Exception | Callable[[GenerationRequest], str]


def role_of(request: GenerationRequest) -> str:
    return request.messages[0].text.splitlines()[0].removeprefix("PHOENIX ROLE: ")


class ScriptedProvider(ModelProvider):
    """Answers by role; roles in ``hang`` wait until cancelled."""

    def __init__(self) -> None:
        super().__init__("ollama")
        self.replies: dict[str, Reply] = {
            "triage": '{"path": "direct"}',
            "direct": "Hello.",
            "planner": json.dumps({"steps": [{"objective": "Recall a fact."}]}),
            "assignment": json.dumps(
                {"assignments": [{"step_id": "s1", "domain": "intelligence"}]}
            ),
            "specialist": "Step answer.",
            "final_answer": "100",
            "verifier": PASS,
            "step_verifier": PASS,
            "final_verifier": PASS,
        }
        self.finish: dict[str, str] = {}
        self.hang: set[str] = set()
        self.health_status = ProviderHealthStatus.HEALTHY
        self.health_calls = 0
        self.requests: list[GenerationRequest] = []
        self.entered = asyncio.Event()

    async def health(self) -> ProviderHealth:
        self.health_calls += 1
        return ProviderHealth(provider_id=self.provider_id, status=self.health_status)

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        return capabilities(model_id)

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        role = role_of(request)
        self.requests.append(request)
        self.entered.set()
        if role in self.hang:
            await asyncio.Event().wait()
        await asyncio.sleep(0)
        reply = self.replies[role]
        if isinstance(reply, Exception):
            raise reply
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text=reply(request) if callable(reply) else reply,
            finish_reason=self.finish.get(role, "stop"),
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

    def roles(self) -> list[str]:
        return [role_of(request) for request in self.requests]

    def calls(self) -> list[tuple[str, str]]:
        return [(role_of(item), item.model_id) for item in self.requests]


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


def registry(models: tuple[str, ...] = MODELS) -> ModelCapabilityRegistry:
    store = ModelCapabilityRegistry()
    for model in models:
        store.register(capabilities(model))
    return store


class Sink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.events.append(event)


def build(provider: ScriptedProvider, **options: Any) -> PhoenixRuntime:
    options.setdefault("capability_registry", registry())
    return PhoenixRuntime(
        providers=(provider,), lifecycle=ModelLifecycleManager(), **options
    )


def run(coroutine: Coroutine[Any, Any, Any], timeout: float = 10) -> Any:
    return asyncio.run(asyncio.wait_for(coroutine, timeout=timeout))


def mission(
    provider: ScriptedProvider,
    path: OrchestrationPath | None = None,
    *,
    text: str = "What is water's boiling point?",
    sink: Sink | None = None,
    **options: Any,
) -> MissionResult:
    async def exercise() -> MissionResult:
        runtime = build(provider, events=sink, **options)
        result: MissionResult = await runtime.run(
            PhoenixRequest(request_id=REQUEST_ID, text=text), path
        )
        assert runtime.dropped_events == 0
        return result

    result: MissionResult = run(exercise())
    return result


def triage_events(sink: Sink) -> list[tuple[EventType, dict[str, Any]]]:
    return [
        (event.event_type, dict(event.payload))
        for event in sink.events
        if event.payload.get("role") == TRIAGE_ROLE
    ]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx network send attempted")

    def refuse_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("outbound socket connection attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_send)
    monkeypatch.setattr(socket, "create_connection", refuse_connect)


# ---- triage decides


def test_triage_decides_direct() -> None:
    provider, sink = ScriptedProvider(), Sink()
    result = mission(provider, sink=sink)
    assert (result.kind, result.text) == (MissionKind.COMPLETED, "Hello.")
    trace = result.trace
    assert (trace.path, trace.triage_outcome, trace.triage_reason) == (
        OrchestrationPath.DIRECT,
        TriageOutcome.DECIDED,
        None,
    )
    assert provider.calls() == [("triage", "qwen3.5:4b"), ("direct", "qwen3.5:4b")]
    assert [item.role for item in trace.generations] == ["triage", "direct"]
    assert trace.task_id is None and trace.states == ()
    # The triage generation sees TRIAGE_SYSTEM and the request text only.
    messages = provider.requests[0].messages
    assert [message.text for message in messages] == [
        prompts.TRIAGE_SYSTEM,
        "What is water's boiling point?",
    ]
    events = triage_events(sink)
    assert [kind for kind, _ in events] == [
        EventType.AGENT_STARTED,
        EventType.AGENT_COMPLETED,
    ]
    done = events[1][1]
    assert done.pop("duration_ms") >= 0
    assert done == {
        "role": "triage",
        "outcome": "completed",
        "triage_outcome": "decided",
        "path": "direct",
    }
    assert all(event.agent_id == "phoenix" for event in sink.events[:2])
    # Health is probed once per mission: triage's probe serves the path too.
    assert provider.health_calls == 1


def test_triage_decides_complex() -> None:
    provider, sink = ScriptedProvider(), Sink()
    provider.replies["triage"] = '```json\n{"path": "complex"}\n```'
    result = mission(provider, sink=sink)
    assert (result.kind, result.text) == (MissionKind.COMPLETED, "100")
    assert (result.trace.path, result.trace.triage_outcome) == (
        OrchestrationPath.COMPLEX,
        TriageOutcome.DECIDED,
    )
    assert provider.roles()[:2] == ["triage", "planner"]
    assert "verifier" in provider.roles() and "final_verifier" in provider.roles()
    assert triage_events(sink)[-1][1]["path"] == "complex"
    created = [e for e in sink.events if e.event_type == EventType.TASK_CREATED]
    assert [dict(e.payload) for e in created] == [{"path": "complex"}]
    assert provider.health_calls == 1


# ---- every fallback reason takes COMPLEX, recorded and published


def unregistered_triage_table() -> ModelTierTable:
    table = default_tier_table()
    missing = (ModelCandidateRef(provider_id="ollama", model_id="absent:1b"),)
    return ModelTierTable(
        roles={**table.roles, TRIAGE_ROLE: missing}, default=table.default
    )


FALLBACKS: list[tuple[TriageFallbackReason, str, Callable[[ScriptedProvider], Any]]] = [
    (
        TriageFallbackReason.NOT_JSON,
        "responder_output",
        lambda p: p.replies.__setitem__("triage", "direct"),
    ),
    (
        TriageFallbackReason.SCHEMA,
        "responder_output",
        lambda p: p.replies.__setitem__("triage", '{"path": "deep"}'),
    ),
    (
        TriageFallbackReason.SCHEMA,
        "responder_output",
        lambda p: p.replies.__setitem__("triage", '{"path": "direct", "grant": "all"}'),
    ),
    (
        TriageFallbackReason.TRUNCATED,
        "responder_output",
        lambda p: p.finish.__setitem__("triage", "length"),
    ),
    (
        TriageFallbackReason.INVALID_TEXT,
        "responder_output",
        lambda p: p.replies.__setitem__("triage", '{"path": "direct"}\ud800'),
    ),
    (
        TriageFallbackReason.OVERSIZED,
        "responder_output",
        lambda p: p.replies.__setitem__("triage", " " * 30000 + '{"path":"direct"}'),
    ),
    (
        TriageFallbackReason.TIMEOUT,
        "timeout",
        lambda p: p.hang.add("triage"),
    ),
    (
        TriageFallbackReason.PROVIDER_ERROR,
        "provider_error",
        lambda p: p.replies.__setitem__("triage", ModelProviderError("down")),
    ),
    (
        TriageFallbackReason.INTERNAL_ERROR,
        "internal_error",
        lambda p: p.replies.__setitem__("triage", RuntimeError("defect")),
    ),
]


@pytest.mark.parametrize(("reason", "code", "arrange"), FALLBACKS)
def test_triage_failure_falls_back_to_complex(
    reason: TriageFallbackReason, code: str, arrange: Callable[[ScriptedProvider], Any]
) -> None:
    provider, sink = ScriptedProvider(), Sink()
    arrange(provider)
    limits = RuntimeLimits(triage_timeout_seconds=0.2)
    result = mission(provider, sink=sink, limits=limits)
    # COMPLEX: AIZEN planning plus LEVI verification, then completion.
    assert (result.kind, result.text) == (MissionKind.COMPLETED, "100"), result
    assert (result.trace.path, result.trace.triage_outcome) == (
        OrchestrationPath.COMPLEX,
        TriageOutcome.FALLBACK,
    )
    assert result.trace.triage_reason == reason
    roles = provider.roles()
    assert roles[0] == "triage" and roles[1] == "planner"
    assert "final_verifier" in roles
    events = triage_events(sink)
    assert [kind for kind, _ in events] == [
        EventType.AGENT_STARTED,
        EventType.AGENT_FAILED,
    ]
    failed = events[1][1]
    failed.pop("duration_ms")
    assert failed == {
        "role": "triage",
        "code": code,
        "triage_outcome": "fallback",
        "triage_reason": reason.value,
        "path": "complex",
    }
    assert sink.events[1].severity.value == "warning"


def test_no_eligible_triage_model_falls_back() -> None:
    provider, sink = ScriptedProvider(), Sink()
    result = mission(provider, sink=sink, tier_table=unregistered_triage_table())
    assert result.kind == MissionKind.COMPLETED
    assert result.trace.triage_reason == TriageFallbackReason.NO_ELIGIBLE_MODEL
    assert "triage" not in provider.roles()  # no model was asked
    assert triage_events(sink)[1][1]["code"] == "no_eligible_model"


def test_unavailable_provider_falls_back_and_complex_fails_explicitly() -> None:
    provider, sink = ScriptedProvider(), Sink()
    provider.health_status = ProviderHealthStatus.UNAVAILABLE
    result = mission(provider, sink=sink)
    assert (result.kind, result.failure_code) == (
        MissionKind.FAILED,
        MissionFailureCode.PROVIDER_UNAVAILABLE,
    )
    assert (result.trace.triage_outcome, result.trace.triage_reason) == (
        TriageOutcome.FALLBACK,
        TriageFallbackReason.PROVIDER_UNAVAILABLE,
    )
    assert result.trace.path == OrchestrationPath.COMPLEX
    assert provider.requests == [] and provider.health_calls == 1


def test_cancellation_during_triage_is_not_a_fallback() -> None:
    provider, sink = ScriptedProvider(), Sink()
    provider.hang.add("triage")

    async def exercise() -> MissionResult:
        runtime = build(provider, events=sink)
        task = asyncio.create_task(
            runtime.run(PhoenixRequest(request_id=REQUEST_ID, text="Hi."))
        )
        await provider.entered.wait()
        await runtime.cancel(REQUEST_ID)
        return await task

    result: MissionResult = run(exercise())
    assert result.kind == MissionKind.CANCELLED
    assert (result.trace.path, result.trace.triage_outcome) == (None, None)
    assert provider.roles() == ["triage"]  # no planner: cancelled, not COMPLEX
    failed = triage_events(sink)[-1][1]
    assert failed["code"] == "cancelled" and "triage_outcome" not in failed


def test_mission_deadline_during_triage_fails_without_a_decision() -> None:
    provider = ScriptedProvider()
    provider.hang.add("triage")
    limits = RuntimeLimits(mission_timeout_seconds=0.1, triage_timeout_seconds=5)
    result = mission(provider, limits=limits)
    assert (result.kind, result.failure_code) == (
        MissionKind.FAILED,
        MissionFailureCode.TIMEOUT,
    )
    assert (result.trace.path, result.trace.triage_outcome) == (None, None)


# ---- the client's path bypasses triage


@pytest.mark.parametrize("path", list(OrchestrationPath))
def test_client_path_bypasses_triage(path: OrchestrationPath) -> None:
    provider, sink = ScriptedProvider(), Sink()
    result = mission(provider, path, sink=sink)
    assert result.kind == MissionKind.COMPLETED
    assert (result.trace.path, result.trace.triage_outcome) == (
        path,
        TriageOutcome.CLIENT,
    )
    assert "triage" not in provider.roles() and triage_events(sink) == []


def test_reused_decision_is_recorded_without_triaging_again() -> None:
    for decided in (TriageOutcome.DECIDED, TriageOutcome.FALLBACK):
        provider = ScriptedProvider()

        async def exercise() -> MissionResult:
            runtime = build(provider)
            result: MissionResult = await runtime.run(
                PhoenixRequest(text="Hi."),
                OrchestrationPath.COMPLEX,
                decided_by=decided,
            )
            return result

        result = run(exercise())
        assert result.trace.triage_outcome == decided
        assert result.trace.triage_reason is None
        assert "triage" not in provider.roles()


def test_inconsistent_decisions_are_refused() -> None:
    async def exercise() -> None:
        runtime = build(ScriptedProvider())
        for path, decided in (
            (None, TriageOutcome.DECIDED),
            (None, TriageOutcome.FALLBACK),
            (OrchestrationPath.DIRECT, TriageOutcome.FALLBACK),
            (OrchestrationPath.DIRECT, "nobody"),
        ):
            with pytest.raises(PhoenixRuntimeError):
                await runtime.run(PhoenixRequest(text="Hi."), path, decided_by=decided)  # type: ignore[arg-type]
        assert runtime.active_request_ids == ()

    run(exercise())


# ---- content never reaches events or the trace


@pytest.mark.parametrize(
    "reply", ['{"path": "direct"}', f"not json {SENTINEL}", f'{{"path": "{SENTINEL}"}}']
)
def test_request_and_triage_output_never_reach_events_or_trace(reply: str) -> None:
    provider, sink = ScriptedProvider(), Sink()
    provider.replies["triage"] = reply
    provider.replies["direct"] = "Fine."
    result = mission(provider, text=f"Classify this {SENTINEL} please.", sink=sink)
    assert result.kind == MissionKind.COMPLETED
    dumped = result.trace.model_dump_json()
    assert SENTINEL not in dumped and "Classify this" not in dumped
    for event in sink.events:
        serialized = event.model_dump_json()
        assert SENTINEL not in serialized and "Classify" not in serialized


# ---- tier, structured provider and allowlist


def test_triage_tier_is_4b_then_2b_on_structured_providers() -> None:
    table = default_tier_table()
    assert [ref.model_id for ref in table.roles[TRIAGE_ROLE]] == [
        "qwen3.5:4b",
        "qwen3.5:2b",
    ]
    default, structured = ScriptedProvider(), ScriptedProvider()
    result = mission(default, structured_providers=(structured,))
    assert result.kind == MissionKind.COMPLETED
    assert structured.roles() == ["triage", "direct"] and default.roles() == []
    assert structured.requests[0].max_output_tokens == RuntimeLimits().max_output_tokens
    # Without the 4B model, triage selects T0.
    fallback = ScriptedProvider()
    mission(fallback, capability_registry=registry(("qwen3.5:2b",)))
    assert fallback.calls()[0] == ("triage", "qwen3.5:2b")


def test_event_allowlist_takes_only_closed_triage_values() -> None:
    good = {
        "role": "triage",
        "code": "responder_output",
        "triage_outcome": "fallback",
        "triage_reason": "not_json",
        "path": "complex",
        "duration_ms": 3,
    }
    check_payload(EventType.AGENT_FAILED, good)
    check_payload(
        EventType.AGENT_COMPLETED,
        {"role": "triage", "triage_outcome": "decided", "path": "direct"},
    )
    for key, value in (
        ("triage_reason", SENTINEL),
        ("triage_outcome", "maybe"),
        ("path", "deep"),
        ("triage_note", "x"),
    ):
        with pytest.raises(EventPayloadError):
            check_payload(EventType.AGENT_FAILED, good | {key: value})
    with pytest.raises(EventPayloadError):
        check_payload(EventType.AGENT_STARTED, {"role": "triage", "path": "direct"})
    assert TRIAGE_ROLE in mission_events.AGENT_ROLES


def test_mirrored_triage_values_match() -> None:
    assert set(mission_store.TRIAGE_OUTCOMES) == {o.value for o in TriageOutcome}
    reasons = {r.value for r in TriageFallbackReason}
    assert reasons - mission_events.FAILURE_CODES == {
        "truncated",
        "oversized",
        "not_json",
        "schema",
        "invalid_text",
    }


def test_trace_validates_triage_consistency() -> None:
    base: dict[str, Any] = {"prompt_set_version": "v", "path": "complex"}
    MissionTrace.model_validate(base)  # a pre-PHX-105 trace
    MissionTrace.model_validate(
        base | {"triage_outcome": "fallback", "triage_reason": "timeout"}
    )
    for bad in (
        base | {"triage_reason": "timeout"},
        base | {"triage_outcome": "decided", "triage_reason": "timeout"},
        base | {"path": "direct", "triage_outcome": "fallback"},
        base | {"path": None, "triage_outcome": "client"},
        base | {"triage_outcome": "fallback", "triage_reason": SENTINEL},
    ):
        with pytest.raises(ValidationError):
            MissionTrace.model_validate(bad)
    assert MissionTrace.model_validate({**base, "path": None}).path is None


# ---- mission service: stored decisions and run again


def alembic_config(path: Path) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}".replace("%", "%%"))
    return config


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db = tmp_path / "phoenix.db"
    command.upgrade(alembic_config(db), "head")
    created = create_engine(f"sqlite:///{db}")
    try:
        yield created
    finally:
        created.dispose()


def service(engine: Engine, provider: ScriptedProvider) -> MissionService:
    runtime = PhoenixRuntime(
        providers=(provider,),
        capability_registry=registry(),
        lifecycle=ModelLifecycleManager(),
    )
    return MissionService(
        runtime=runtime,
        store=MissionStore(sessionmaker(engine)),
        tool_registry=ToolRegistry(filesystem_tool_definitions()),
    )


async def settled(missions: MissionService, mission_id: str) -> MissionRecord:
    async with asyncio.timeout(10):
        while True:
            record = await missions.get_mission(mission_id)
            if record.status not in (MissionStatus.QUEUED, MissionStatus.RUNNING):
                return record
            await asyncio.sleep(0.005)


def test_service_records_triage_and_client_decisions(engine: Engine) -> None:
    provider = ScriptedProvider()
    provider.replies["triage"] = '{"path": "complex"}'

    async def scenario() -> tuple[MissionRecord, ...]:
        missions = service(engine, provider)
        await missions.start()
        conversation = await missions.create_conversation()
        provider.hang.add("triage")
        triaged = await missions.submit(conversation, "Plan it.")
        await provider.entered.wait()
        pending = await missions.get_mission(triaged)
        provider.hang.clear()
        await missions.cancel(triaged)
        cancelled = await settled(missions, triaged)
        decided = await settled(missions, await missions.submit(conversation, "Again."))
        client = await settled(
            missions, await missions.submit(conversation, "Hi.", "direct")
        )
        await missions.stop()
        return pending, cancelled, decided, client

    pending, cancelled, decided, client = run(scenario(), timeout=30)
    # Queued and running without a decision; cancelled before triage decided.
    assert (pending.path, pending.triage_outcome) == (None, None)
    assert (cancelled.status, cancelled.path, cancelled.triage_outcome) == (
        MissionStatus.CANCELLED,
        None,
        None,
    )
    assert (decided.status, decided.path, decided.triage_outcome) == (
        MissionStatus.COMPLETED,
        "complex",
        "decided",
    )
    assert (client.path, client.triage_outcome) == ("direct", "client")


def test_run_again_reuses_the_decision_or_triages_an_undecided_mission(
    engine: Engine,
) -> None:
    provider = ScriptedProvider()
    provider.replies["triage"] = '{"path": "complex"}'

    async def first_launch() -> tuple[str, str]:
        missions = service(engine, provider)
        await missions.start()
        conversation = await missions.create_conversation()
        provider.hang.add("planner")  # stopped after triage decided
        decided = await missions.submit(conversation, "Plan it.")
        await asyncio.wait_for(_until(lambda: "planner" in provider.roles()), 10)
        undecided = await missions.submit(conversation, "Queued.")
        await missions.stop()
        return decided, undecided

    decided_id, undecided_id = run(first_launch(), timeout=30)
    provider.hang.clear()
    provider.requests.clear()

    async def second_launch() -> tuple[MissionRecord, MissionRecord, MissionRecord]:
        missions = service(engine, provider)
        await missions.start()
        original = await missions.get_mission(decided_id)
        reused = await settled(missions, await missions.run_again(decided_id))
        assert "triage" not in provider.roles()
        retriaged = await settled(missions, await missions.run_again(undecided_id))
        await missions.stop()
        return original, reused, retriaged

    original, reused, retriaged = run(second_launch(), timeout=30)
    assert (original.status, original.failure_code) == (
        MissionStatus.FAILED,
        "interrupted",
    )
    assert (original.path, original.triage_outcome) == ("complex", "decided")
    assert (reused.status, reused.path, reused.triage_outcome) == (
        MissionStatus.COMPLETED,
        "complex",
        "decided",
    )
    assert (retriaged.path, retriaged.triage_outcome) == ("complex", "decided")
    assert provider.roles().count("triage") == 1  # only the undecided one


async def _until(predicate: Callable[[], bool]) -> None:
    while not predicate():
        await asyncio.sleep(0.005)


def test_schema_keeps_path_and_triage_outcome_consistent(engine: Engine) -> None:
    store = MissionStore(sessionmaker(engine))
    store.create_conversation("c", _now())
    store.submit(
        conversation_id="c",
        mission_id="m",
        message_id="u",
        text="Hi.",
        path=None,
        now=_now(),
    )
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT path, triage_outcome FROM missions")
        ).one()
    assert tuple(row) == (None, None)
    for path, outcome in (
        (None, "client"),
        ("direct", None),
        ("direct", "fallback"),
        ("complex", "other"),
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text("UPDATE missions SET path = :path, triage_outcome = :outcome"),
                {"path": path, "outcome": outcome},
            )


def _now() -> datetime:
    return datetime.now(UTC)
