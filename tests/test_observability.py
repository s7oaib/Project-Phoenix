"""PHX-115 observability (ADR-145 to ADR-148).

Structured JSON logs and their redaction, the rotating log file, the metrics
collector between PHOENIX's publishers and the event stream, the bounded VRAM
probe (a faked Ollama ``/api/ps``; no Ollama, GPU or network is used) and the
authenticated ``GET /metrics`` route on the real composition root with a
scripted model.
"""

import asyncio
import io
import json
import logging
import re
import secrets
import socket
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from phoenix.agents.mission_events import EventPublisher, mission_event
from phoenix.agents.responders import GenerationRecord
from phoenix.agents.router import ProviderRouteHealth
from phoenix.agents.runtime import MissionTrace
from phoenix.api.observability import (
    LOGGER_NAME,
    MetricsCollector,
    MetricsOut,
    VramReport,
    parse_ollama_ps,
    probe_ollama_vram,
    sample_memory,
)
from phoenix.api.security import LocalApiSecurity
from phoenix.api.server import ProviderSet, ServerConfig, build_server_app
from phoenix.api.structured_logging import (
    LOG_DIRECTORY,
    LOG_FILE,
    QUIET_LOGGERS,
    REDACTED,
    JsonLogFormatter,
    configure_logging,
    forget_secret,
    log_entry,
    redact,
    register_secret,
)
from phoenix.core.config import AppSettings
from phoenix.core.events import EventEnvelope, EventType
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

MISSION = "11111111-1111-4111-8111-111111111111"
SECOND = "22222222-2222-4222-8222-222222222222"
TASK = "33333333-3333-4333-8333-333333333333"
CALL = "44444444-4444-4444-8444-444444444444"
REQUEST = "55555555-5555-4555-8555-555555555555"
VERIFY = "66666666-6666-4666-8666-666666666666"
RECOVERY = "77777777-7777-4777-8777-777777777777"
DENIED = "88888888-8888-4888-8888-888888888888"
SECRET_TEXT = "PROMPT-SENTINEL-42 the user's private question"


# ---- redaction and the JSON log format


@pytest.mark.parametrize(
    "raw",
    [
        "Authorization: Bearer abcDEF0123456789abcdef",
        "api_key=abcDEF0123456789",
        'password: "hunter2-very-secret"',
        "token=abc.def-ghi",
        "client_secret='s3cr3t-value'",
        "using sk-" + "A" * 24,
        "ghp_" + "b" * 30,
        "github_" + "pat_" + "c" * 30,
        "xox" + "b-1234567890-abcdef",
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "-----BEGIN " + "PRIVATE KEY-----\nMIIEv\n-----END PRIVATE KEY-----",
        # PHX-123 (ADR-182): JSON-quoted keys and authorization schemes.
        '{"api_key": "abcDEF0123456789"}',
        "{'password': 'hunter2-very-secret'}",
        "Authorization: Basic abcDEF0123456789==",
        '"authorization": "Basic abcDEF0123456789"',
    ],
)
def test_redact_removes_credential_shapes(raw: str) -> None:
    cleaned = redact(f"before {raw} after")
    assert REDACTED in cleaned
    assert cleaned.startswith("before") and cleaned.rstrip().endswith("after")
    for fragment in ("abcDEF0123456789", "hunter2", "s3cr3t", "AAAAAAAA", "MIIEv"):
        assert fragment not in cleaned


def test_registered_secret_control_characters_and_length_bound() -> None:
    token = secrets.token_urlsafe(32)
    register_secret(token)
    try:
        assert redact(f"leaked {token} here") == f"leaked {REDACTED} here"
    finally:
        forget_secret(token)
    assert token in redact(token)  # only registered values are known
    register_secret("short")  # under 8 characters: ignored, never matched
    assert redact("short") == "short"
    assert redact("a\x00b\x1bc\nd") == "a b c\nd"
    long = redact("x" * 5000)
    assert len(long) == 1000 and long.endswith("…")


def test_log_entry_keeps_only_safe_fields_and_never_exception_text() -> None:
    logger = logging.getLogger("phoenix.test.observability")
    try:
        raise ValueError(SECRET_TEXT)
    except ValueError:
        exc_info = sys.exc_info()
    record = logger.makeRecord(
        logger.name,
        logging.ERROR,
        __file__,
        1,
        "mission failed with api_key=%s",
        ("abcdef123456",),
        exc_info,
        extra={
            "correlation_id": MISSION,
            "task_id": "not a safe value: has spaces",
            "step_id": "s1",
            "prompt": SECRET_TEXT,
            "answer_text": SECRET_TEXT,
        },
    )
    entry = log_entry(record)
    assert set(entry) == {
        "ts",
        "level",
        "subsystem",
        "message",
        "correlation_id",
        "step_id",
        "error_type",
        "error_frames",
    }
    assert entry["message"] == f"mission failed with api_key={REDACTED}"
    assert entry["error_type"] == "ValueError"
    frames = entry["error_frames"]
    assert isinstance(frames, list) and frames
    assert all(re.fullmatch(r"[\w.]+:\d+:[\w<>]+", frame) for frame in frames)
    line = JsonLogFormatter().format(record)
    assert json.loads(line) == entry
    assert "PROMPT-SENTINEL" not in line and "abcdef123456" not in line
    assert str(entry["ts"]).endswith("+00:00")


def test_configure_logging_writes_rotating_json_and_restores_the_root(
    tmp_path: Path,
) -> None:
    root = logging.getLogger()
    before = (list(root.handlers), root.level)
    stream = io.StringIO()
    handle = configure_logging(tmp_path, stream, max_bytes=2000, backups=2)
    token = secrets.token_urlsafe(32)
    register_secret(token)
    try:
        logger = logging.getLogger("phoenix.test.rotation")
        for index in range(60):
            logger.info(
                "record %d with Bearer %s",
                index,
                token,
                extra={"mission_id": MISSION, "step_id": "final"},
            )
        try:
            raise RuntimeError(SECRET_TEXT)
        except RuntimeError:
            logger.exception("step failed")
    finally:
        handle.close()
        forget_secret(token)
    assert (list(root.handlers), root.level) == before
    handle.close()  # idempotent
    directory = tmp_path / LOG_DIRECTORY
    assert handle.log_path == directory / LOG_FILE
    files = sorted(path.name for path in directory.iterdir())
    assert files == [LOG_FILE, f"{LOG_FILE}.1", f"{LOG_FILE}.2"]  # rotated, bounded
    texts = [path.read_text("utf-8") for path in directory.iterdir()]
    texts.append(stream.getvalue())
    for text in texts:
        assert token not in text and "PROMPT-SENTINEL" not in text
        for line in text.splitlines():
            entry = json.loads(line)
            assert entry["level"] in {"info", "error"}
    last = json.loads((directory / LOG_FILE).read_text("utf-8").splitlines()[-1])
    assert last["error_type"] == "RuntimeError" and last["message"] == "step failed"
    streamed = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(streamed) == 61
    assert streamed[0]["mission_id"] == MISSION and streamed[0]["step_id"] == "final"
    assert streamed[0]["message"] == f"record 0 with {REDACTED}"


def test_http_client_request_urls_never_reach_the_log(tmp_path: Path) -> None:
    # PHX-123 (SR-01, ADR-180): httpx logs "HTTP Request: GET <full URL>" at
    # INFO; query strings (search terms, reset codes, emails) must stay out.
    before = {name: logging.getLogger(name).level for name in QUIET_LOGGERS}
    stream = io.StringIO()
    handle = configure_logging(tmp_path, stream)
    url = "https://example.org/reset?code=abc123XYZ&email=a@b.c"
    try:

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            assert client.get(url).status_code == 200
        logging.getLogger("httpx").info("HTTP Request: GET %s", url)
        logging.getLogger("httpcore.connection").info("connect %s", url)
        logging.getLogger("phoenix.test.quiet").info("phoenix still logs")
        logging.getLogger("httpx").warning("client warning")
    finally:
        handle.close()
    assert {name: logging.getLogger(name).level for name in QUIET_LOGGERS} == before
    log_file = tmp_path / LOG_DIRECTORY / LOG_FILE
    for text in (stream.getvalue(), log_file.read_text("utf-8")):
        assert "abc123XYZ" not in text and "a@b.c" not in text
        assert "example.org" not in text
        assert "phoenix still logs" in text and "client warning" in text


# ---- the collector


class Sink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.events.append(event)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def ev(
    event_type: EventType,
    mission: str = MISSION,
    agent: str | None = None,
    **payload: Any,
) -> EventEnvelope:
    return mission_event(
        event_type, payload, correlation_id=mission, task_id=TASK, agent_id=agent
    )


def status(mission: str, value: str, **extra: Any) -> EventEnvelope:
    return mission_event(
        EventType.TASK_STATE_CHANGED,
        {"scope": "mission", "mission_status": value, **extra},
        correlation_id=mission,
    )


def generation(model: str, tokens: int, duration: float | None) -> GenerationRecord:
    return GenerationRecord(
        role="direct",
        agent_id="phoenix",
        provider_id="ollama",
        model_id=model,
        operation_id=secrets.token_hex(8),
        finish_reason="stop",
        input_tokens=10,
        output_tokens=tokens,
        duration_ms=duration,
        output_chars=5,
        output_sha256="0" * 64,
    )


def trace(*generations: GenerationRecord) -> MissionTrace:
    return MissionTrace(
        prompt_set_version="v1",
        path=None,
        provider_health=(
            ProviderRouteHealth(
                provider_id="ollama", status=ProviderHealthStatus.HEALTHY
            ),
        ),
        generations=generations,
    )


def collector(
    sink: Sink, clock: Clock, **kwargs: Any
) -> tuple[MetricsCollector, list[str]]:
    loaded: list[str] = []
    traces = {
        MISSION: trace(generation("qwen3.5:4b", 20, 2000.0)),
        SECOND: trace(
            generation("qwen3.5:4b", 30, 1000.0), generation("qwen3.5:9b", 0, None)
        ),
    }

    async def load(mission_id: str) -> MissionTrace | None:
        loaded.append(mission_id)
        return traces.get(mission_id)

    observer = MetricsCollector(
        sink,
        clock=clock,
        memory_sampler=lambda: (1024 * 1024 * 50, 24 * 2**30, 12 * 2**30),
        **kwargs,
    )
    observer.bind_traces(load)
    return observer, loaded


def test_collector_aggregates_forwards_and_logs_content_free_events() -> None:
    async def run() -> None:
        sink, clock = Sink(), Clock()
        observer, loaded = collector(sink, clock)
        observer.bind_stream_counters(lambda: (2, 1))
        script: list[tuple[float, EventEnvelope]] = [
            (0.0, status(MISSION, "queued")),
            (0.5, status(MISSION, "running")),
            (0.0, ev(EventType.AGENT_STARTED, agent="strategist", role="planner")),
            (
                0.0,
                ev(
                    EventType.AGENT_COMPLETED,
                    agent="strategist",
                    role="planner",
                    outcome="completed",
                    duration_ms=900,
                ),
            ),
            (
                0.0,
                ev(
                    EventType.TOOL_COMPLETED,
                    step_id="s1",
                    tool_id="filesystem.read_text",
                    call_id=CALL,
                    tool_status="success",
                    duration_ms=12,
                ),
            ),
            (
                0.0,
                ev(
                    EventType.TOOL_FAILED,
                    step_id="s1",
                    tool_id="filesystem.read_text",
                    call_id=CALL,
                    code="tool_rejected",
                    decision="denied",
                    tool_status="denied",
                    duration_ms=3,
                ),
            ),
            (
                0.0,
                ev(
                    EventType.PERMISSION_REQUESTED,
                    step_id="s1",
                    tool_id="filesystem.read_text",
                    call_id=CALL,
                    permission_request_id=REQUEST,
                    risk_level="P2",
                ),
            ),
            (
                0.0,
                ev(
                    EventType.VERIFICATION_PASSED,
                    agent="verifier",
                    step_id="final",
                    verification_id=VERIFY,
                    verdict="passed",
                    duration_ms=40,
                ),
            ),
            (
                0.0,
                ev(
                    EventType.VERIFICATION_FAILED,
                    agent="verifier",
                    step_id="s1",
                    verification_id=VERIFY,
                    verdict="failed",
                    code="verification_failed",
                    duration_ms=60,
                ),
            ),
            (
                0.0,
                ev(
                    EventType.RECOVERY_STARTED,
                    recovery_id=RECOVERY,
                    role="planner",
                    recovery_class="responder_output",
                    responder_error="schema",
                    action="retry_same",
                ),
            ),
            (
                0.0,
                ev(
                    EventType.RECOVERY_COMPLETED,
                    recovery_id=RECOVERY,
                    role="planner",
                    recovery_class="responder_output",
                    action="retry_same",
                    outcome="recovered",
                    duration_ms=700,
                ),
            ),
            (0.0, ev(EventType.STEP_COMPLETED, step_id="final", duration_ms=1500)),
            (2.0, status(MISSION, "completed")),
            (0.0, status(SECOND, "queued")),
            (1.0, status(SECOND, "running")),
            (1.0, status(SECOND, "waiting_permission")),
            (5.0, status(SECOND, "superseded")),
            (0.0, status(DENIED, "queued")),
            (
                0.0,
                status(
                    DENIED,
                    "failed",
                    failure_code="permission_denied",
                ),
            ),
        ]
        for advance, event in script:
            clock.now += advance
            await observer.publish(event)
        assert sink.events == [event for _, event in script]  # unchanged, in order

        clock.now += 1.0
        snapshot = await observer.snapshot()
        assert snapshot.missions == {
            "completed": 1,
            "failed": 1,
            "queued": 3,
            "running": 2,
            "superseded": 1,
            "waiting_permission": 1,
        }
        stages = {item.stage: item for item in snapshot.latency}
        assert set(stages) == {
            "agent.planner",
            "mission.first_answer",
            "mission.queue_wait",
            "mission.run",
            "recovery",
            "step",
            "tool",
            "verification",
        }
        assert stages["mission.first_answer"].p50_ms == 2500  # queued -> completed
        assert stages["mission.queue_wait"].count == 2
        assert stages["mission.run"].max_ms == 2000
        assert stages["tool"].count == 2 and stages["tool"].p95_ms == 12
        assert snapshot.ttft_basis == "mission_first_answer"
        assert snapshot.tools.calls == 2 and snapshot.tools.success_rate == 0.5
        assert snapshot.tools.by_status == {"denied": 1, "success": 1}
        permissions = snapshot.permissions
        assert (permissions.requested, permissions.approved) == (1, 1)
        assert (permissions.denied, permissions.policy_denied) == (1, 1)
        assert permissions.by_risk == {"P2": 1}
        assert (snapshot.verification.passed, snapshot.verification.failed) == (1, 1)
        assert snapshot.verification.by_verdict == {"failed": 1, "passed": 1}
        recoveries = snapshot.recoveries
        assert (recoveries.started, recoveries.completed) == (1, 1)
        assert recoveries.by_action == {"retry_same": 1}
        assert recoveries.by_outcome == {"recovered": 1}
        # Traces of the missions that ended: each ingested once.
        assert loaded == [MISSION, SECOND, DENIED]
        assert snapshot.generation.missions_ingested == 2  # DENIED had none
        assert snapshot.generation.generations == 3
        assert snapshot.generation.input_tokens == 30
        assert snapshot.generation.output_tokens == 50
        assert (
            snapshot.generation.output_tokens_per_second_mean == 20.0
        )  # (10 + 30) / 2
        assert snapshot.generation.duration.count == 2
        assert [m.model_id for m in snapshot.generation.models] == [
            "qwen3.5:4b",
            "qwen3.5:9b",
        ]
        assert snapshot.generation.provider_health[0].status == "healthy"
        assert snapshot.resources.process_rss_bytes == 50 * 1024 * 1024
        assert snapshot.resources.vram.status == "unavailable"
        assert (snapshot.events.observed, snapshot.events.rejected) == (len(script), 0)
        assert (snapshot.events.stream_dropped, snapshot.events.stream_rejected) == (
            2,
            1,
        )
        # Logs: one per event, IDs only, no free text.
        assert len(snapshot.logs) == len(script)
        first = snapshot.logs[0]
        assert first.subsystem == LOGGER_NAME and first.level == "info"
        assert first.correlation_id == MISSION and first.mission_id == MISSION
        assert first.message == "task.state_changed scope=mission mission_status=queued"
        tool = next(log for log in snapshot.logs if log.event_type == "tool.failed")
        assert tool.level == "warning" and tool.step_id == "s1" and tool.task_id == TASK
        again = await observer.snapshot()
        assert loaded == [MISSION, SECOND, DENIED]  # nothing new to ingest
        assert again.generation.generations == 3

    asyncio.run(run())


def test_collector_never_blocks_or_breaks_publication() -> None:
    async def run() -> None:
        sink, clock = Sink(), Clock()
        observer, loaded = collector(sink, clock)
        forged = EventEnvelope(
            event_type=EventType.TOOL_COMPLETED,
            payload={"tool_output": SECRET_TEXT},
            correlation_id=MISSION,
        )
        await observer.publish(forged)
        assert sink.events == [forged]  # forwarded; the stream's own check rejects
        assert (observer.observed_events, observer.rejected_events) == (0, 1)
        # A publisher's allowlist still refuses content before the collector.
        publisher = EventPublisher(observer)
        await publisher.publish(
            EventType.TOOL_COMPLETED,
            {"tool_output": SECRET_TEXT},
            correlation_id=MISSION,
        )
        assert publisher.dropped == 1 and len(sink.events) == 1
        # Tracking is bounded; logs keep the last 64.
        for index in range(300):
            mission = f"{index:08d}-0000-4000-8000-000000000000"
            await observer.publish(status(mission, "queued"))
        snapshot = await observer.snapshot()
        assert snapshot.missions == {"queued": 300}
        assert not loaded
        assert all("PROMPT-SENTINEL" not in log.message for log in snapshot.logs)
        assert len(snapshot.logs) == 64

    asyncio.run(run())


def test_unreadable_traces_are_skipped() -> None:
    async def run() -> None:
        sink, clock = Sink(), Clock()
        observer = MetricsCollector(sink, clock=clock)

        async def broken(mission_id: str) -> MissionTrace | None:
            raise LookupError(mission_id)

        observer.bind_traces(broken)
        await observer.publish(status(MISSION, "completed"))
        snapshot = await observer.snapshot()
        assert snapshot.generation.missions_ingested == 0
        assert snapshot.missions == {"completed": 1}
        assert snapshot.uptime_seconds == 0

    asyncio.run(run())


def test_cpu_percent_is_bounded_and_needs_an_interval() -> None:
    async def run() -> None:
        clock = Clock()
        cpu = [0.0]
        observer = MetricsCollector(Sink(), clock=clock, cpu_clock=lambda: cpu[0])
        assert (await observer.snapshot()).resources.process_cpu_percent is None
        clock.now += 10
        cpu[0] += 1_000_000.0
        assert (await observer.snapshot()).resources.process_cpu_percent == 100.0

    asyncio.run(run())


def test_memory_is_sampled_with_the_standard_library() -> None:
    rss, total, available = sample_memory()
    for value in (rss, total, available):
        assert value is None or (type(value) is int and value > 0)
    if sys.platform == "win32":
        assert rss is not None and total is not None and available is not None
        assert rss < total and available <= total


# ---- VRAM from a faked Ollama /api/ps


PS = {
    "models": [
        {
            "name": "qwen3.5:4b",
            "model": "qwen3.5:4b",
            "size": 5_000_000_000,
            "size_vram": 4_000_000_000,
            "digest": "abc",
            "expires_at": "2026-10-21T10:00:00Z",
        }
    ]
}


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"models": "x"},
        {"models": [1]},
        {"models": [{"name": "bad name with spaces", "size": 1, "size_vram": 1}]},
        {"models": [{"name": "m", "size": -1, "size_vram": 1}]},
        {"models": [{"name": "m", "size": 1, "size_vram": 1.5}]},
        {"models": [{"name": "m", "size": True, "size_vram": 1}]},
    ],
)
def test_malformed_ps_reports_unavailable(body: object) -> None:
    assert parse_ollama_ps(body) == VramReport(status="unavailable")


def test_vram_probe_reads_placement_and_fails_closed() -> None:
    seen: list[httpx.Request] = []

    def respond(status_code: int, body: object) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(status_code, json=body)

        return httpx.MockTransport(handler)

    async def run() -> None:
        report = await probe_ollama_vram(
            "http://127.0.0.1:11434", 1.0, transport=respond(200, PS)
        )
        assert report.status == "ok" and report.total_vram_bytes == 4_000_000_000
        assert report.models[0].model_id == "qwen3.5:4b"
        assert report.models[0].size_bytes == 5_000_000_000
        assert seen[0].method == "GET" and seen[0].url.path == "/api/ps"
        empty = await probe_ollama_vram(
            "http://127.0.0.1:11434", 1.0, transport=respond(200, {"models": []})
        )
        assert empty == VramReport(status="ok", total_vram_bytes=0)
        for transport in (
            respond(500, PS),
            respond(200, {"models": [{"name": "x" * 5000}]}),
            respond(200, {"padding": "x" * 300_000, "models": []}),
        ):
            report = await probe_ollama_vram(
                "http://127.0.0.1:11434", 1.0, transport=transport
            )
            assert report.status == "unavailable"

        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        down = await probe_ollama_vram(
            "http://127.0.0.1:11434", 1.0, transport=httpx.MockTransport(refuse)
        )
        assert down.status == "unavailable"

        async def stall(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(5)
            return httpx.Response(200, json=PS)

        started = time.monotonic()
        slow = await probe_ollama_vram(
            "http://127.0.0.1:11434", 0.1, transport=httpx.MockTransport(stall)
        )
        assert slow.status == "unavailable" and time.monotonic() - started < 2

    asyncio.run(run())


# ---- GET /metrics on the composition root


TOKEN = secrets.token_urlsafe(32)
PORT = 48767
SECURITY = LocalApiSecurity(TOKEN, host="127.0.0.1", port=PORT)
FAST = ServerConfig(probe_timeout_seconds=0.5, startup_timeout_seconds=5)
QUESTION = "Say hello. USER-SENTINEL-115"
ANSWER = "Hello there ANSWER-SENTINEL-115"
PASS = '{"checks":[{"criterion":"Correct","passed":true,"evidence_summary":"Fine."}]}'


class ScriptedProvider(ModelProvider):
    def __init__(self) -> None:
        super().__init__("ollama")

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id, status=ProviderHealthStatus.HEALTHY
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
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

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        role = request.messages[0].text.splitlines()[0].removeprefix("PHOENIX ROLE: ")
        text = ANSWER if role == "direct" else PASS
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text=text,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=7, output_tokens=4),
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


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if "vram_probe" in request.node.name:
        return  # MockTransport only; it never opens a socket

    async def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx network send attempted")

    def refuse_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("outbound socket connection attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_send)
    monkeypatch.setattr(socket, "create_connection", refuse_connect)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = AppSettings(data_dir=tmp_path / "data", _env_file=None)  # type: ignore[call-arg]
    provider = ScriptedProvider()
    app = build_server_app(
        settings,
        security=SECURITY,
        config=FAST,
        provider_factory=lambda config: ProviderSet(providers=(provider,)),
    )
    with TestClient(
        app,
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as test_client:
        yield test_client


def test_metrics_route_requires_the_token_and_reports_a_real_mission(
    client: TestClient,
) -> None:
    for headers in (
        {"Authorization": ""},
        {"Authorization": f"Bearer {secrets.token_urlsafe(32)}"},
    ):
        refused = client.get("/metrics", headers=headers)
        assert refused.status_code == 401
        assert refused.json()["error"]["code"] == "unauthorized"
    assert client.get("/metrics", headers={"Host": "evil.example"}).status_code == 403
    assert client.post("/metrics").status_code == 405

    empty = MetricsOut.model_validate(client.get("/metrics").json())
    assert empty.missions == {} and empty.logs == ()
    assert empty.resources.vram.status == "unavailable"  # no Ollama provider here

    accepted = client.post("/chat", json={"text": QUESTION, "path": "direct"})
    assert accepted.status_code == 202
    mission = accepted.json()["mission_id"]
    deadline = time.monotonic() + 20
    while client.get(f"/missions/{mission}").json()["status"] != "completed":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text
    for sentinel in ("USER-SENTINEL", "ANSWER-SENTINEL", TOKEN):
        assert sentinel not in body
    metrics = MetricsOut.model_validate(response.json())
    assert metrics.missions["completed"] == 1
    assert {"mission.first_answer", "mission.run"} <= {
        item.stage for item in metrics.latency
    }
    assert metrics.generation.generations >= 1
    assert metrics.generation.output_tokens == 4 * metrics.generation.generations
    assert metrics.generation.models[0].provider_id == "ollama"
    assert metrics.events.observed > 0 and metrics.events.rejected == 0
    assert any(
        log.correlation_id == mission and log.event_type == "task.state_changed"
        for log in metrics.logs
    )
    assert all(log.correlation_id in (None, mission) for log in metrics.logs)


def test_launcher_logs_json_to_the_data_directory_and_redacts_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from phoenix.api import launcher

    class FakeSocket:
        def close(self) -> None:
            pass

    class FakeServer:
        started = True

        def run(self, sockets: list[object]) -> None:
            logging.getLogger("phoenix.test.launcher").warning("token was %s", TOKEN)
            logging.getLogger("phoenix.api").warning(
                "serving", extra={"correlation_id": MISSION}
            )

    def create(*args: object, **kwargs: object) -> tuple[FakeServer, FakeSocket]:
        return FakeServer(), FakeSocket()

    monkeypatch.setattr(launcher, "create_server", create)
    root = logging.getLogger()
    before = (list(root.handlers), root.level)
    err = io.StringIO()
    code = launcher.main(
        ["--data-dir", str(tmp_path / "data")],
        stdin=io.StringIO(TOKEN + "\n"),
        stdout=io.StringIO(),
        stderr=err,
    )
    assert code == 0
    assert (list(root.handlers), root.level) == before
    assert redact(TOKEN) == TOKEN  # forgotten after the run
    written = (tmp_path / "data" / LOG_DIRECTORY / LOG_FILE).read_text("utf-8")
    for text in (written, err.getvalue()):
        entries = [json.loads(line) for line in text.splitlines()]
        assert [entry["message"] for entry in entries] == [
            f"token was {REDACTED}",
            "serving",
        ]
        assert entries[1]["correlation_id"] == MISSION
        assert TOKEN not in text
