"""PHX-103 ``WS /events``: authentication, fan-out, slow clients, shutdown (ADR-087).

Three layers:

- the EventStream bridge with fake sockets (deterministic slow clients);
- the composition root through Starlette's TestClient;
- one real uvicorn server (via the launcher, websockets backend) on an
  OS-assigned loopback port, talked to with the websockets client.

Models are scripted fakes; any httpx send, the Ollama adapter's path, fails.
"""

import asyncio
import copy
import io
import json
import logging
import secrets
import subprocess
import threading
import time
import tomllib
from collections.abc import AsyncIterator, Callable, Iterator
from importlib.metadata import metadata, version
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from uvicorn.protocols.websockets.auto import AutoWebSocketsProtocol
from uvicorn.protocols.websockets.websockets_sansio_impl import (
    WebSocketsSansIOProtocol,
)
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import ClientConnection, connect
from websockets.typing import Origin, Subprotocol

from phoenix.api import launcher
from phoenix.api.event_stream import (
    WS_GOING_AWAY,
    WS_TRY_AGAIN_LATER,
    EventStream,
)
from phoenix.api.security import WS_SUBPROTOCOL, WS_TOKEN_PREFIX, LocalApiSecurity
from phoenix.api.server import (
    BackendServices,
    ProviderSet,
    ServerConfig,
    build_server_app,
    get_services,
)
from phoenix.core.config import AppSettings
from phoenix.core.event_bus import EventBus
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

ROOT = Path(__file__).resolve().parents[1]
TOKEN = secrets.token_urlsafe(32)
WRONG = secrets.token_urlsafe(32)
PORT = 48767
HOST = f"127.0.0.1:{PORT}"
WS_URL = f"ws://{HOST}/events"
FAST = ServerConfig(probe_timeout_seconds=0.2, startup_timeout_seconds=2)
MODELS = ("qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b", "qwen2.5-coder:7b")
USER = "USER-SENTINEL-37"
MODEL = "MODEL-SENTINEL-55"
QUESTION = f"What is the codename? {USER}"


# ---- fakes


class ScriptedProvider(ModelProvider):
    """Healthy, every tier model installed; DIRECT answers carry a sentinel."""

    def __init__(self) -> None:
        super().__init__("ollama")

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id, status=ProviderHealthStatus.HEALTHY
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        if model_id not in MODELS:
            raise ModelProviderError("model not installed")
        return ModelCapabilities(
            provider_id=self.provider_id,
            model_id=model_id,
            supports_streaming=True,
            supports_structured_output=False,
            supports_tool_use=False,
            supports_vision=False,
            supports_cancellation=True,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text=f"PELICAN-7 {MODEL}",
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


class LoopCapture:
    """A provider factory that also records the server's event loop."""

    def __init__(self) -> None:
        self.loop: asyncio.AbstractEventLoop | None = None

    def __call__(self, config: ServerConfig) -> ProviderSet:
        self.loop = asyncio.get_running_loop()
        return ProviderSet(
            providers=(ScriptedProvider(),), structured=(ScriptedProvider(),)
        )


class FakeWebSocket:
    """Records frames and the close code; ``slow`` stalls every send."""

    def __init__(self, *, slow: bool = False) -> None:
        self.frames: list[dict[str, Any]] = []
        self.closed_with: int | None = None
        self.release = asyncio.Event()
        self.leave = asyncio.Event()
        self.slow = slow

    async def send_text(self, data: str) -> None:
        if self.slow:
            await self.release.wait()
        self.frames.append(json.loads(data))

    async def receive(self) -> dict[str, Any]:
        await self.leave.wait()
        return {"type": "websocket.disconnect", "code": 1000}

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code


def event(step: int) -> EventEnvelope:
    return EventEnvelope(
        event_type=EventType.STEP_STARTED,
        payload={"step_id": f"s{step}"},
        correlation_id="mission-1",
    )


async def until(predicate: Callable[[], bool], timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


async def settle_loop(rounds: int = 10) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def no_model_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx network send attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_send)


# ---- the bridge


def test_slow_client_gets_1013_while_others_keep_receiving() -> None:
    async def scenario() -> None:
        bus = EventBus()
        stream = EventStream(bus, client_buffer=2)
        fast, slow = FakeWebSocket(), FakeWebSocket(slow=True)
        serving = [
            asyncio.create_task(stream.serve(socket))  # type: ignore[arg-type]
            for socket in (fast, slow)
        ]
        await until(lambda: bus.subscriber_count == 2)
        for step in range(10):
            await stream.publish(event(step))  # never waits for the slow client
            await settle_loop()
        await until(lambda: slow.closed_with is not None)
        assert slow.closed_with == WS_TRY_AGAIN_LATER
        assert stream.slow_disconnects == 1
        assert bus.subscriber_count == 1  # the slow client's subscription is gone
        assert stream.connection_count == 1
        for step in range(10, 15):
            await stream.publish(event(step))
            await settle_loop()
        await until(lambda: len(fast.frames) == 15)
        assert [f["payload"]["step_id"] for f in fast.frames] == [
            f"s{step}" for step in range(15)
        ]
        assert stream.dropped_events == 0
        assert fast.closed_with is None
        fast.leave.set()  # the fast client disconnects itself
        await asyncio.gather(*serving)
        assert bus.subscriber_count == 0 and stream.connection_count == 0
        assert fast.closed_with is None  # no close is sent to a departed client

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_publish_never_blocks_and_counts_a_full_bus() -> None:
    async def scenario() -> None:
        bus = EventBus(capacity=1)
        stalled = await bus.subscribe()  # never read
        stream = EventStream(bus)
        async with asyncio.timeout(1):
            for step in range(5):
                await stream.publish(event(step))
        assert stream.dropped_events == 4
        await stalled.close()

    asyncio.run(scenario())


def test_off_allowlist_events_are_never_sent() -> None:
    async def scenario() -> None:
        bus = EventBus()
        stream = EventStream(bus)
        client = FakeWebSocket()
        serving = asyncio.create_task(stream.serve(client))  # type: ignore[arg-type]
        await until(lambda: bus.subscriber_count == 1)
        await bus.publish(
            EventEnvelope(
                event_type=EventType.AGENT_COMPLETED,
                payload={"role": "direct", "answer": f"PELICAN-7 {MODEL}"},
                correlation_id="mission-1",
            )
        )
        await bus.publish(
            EventEnvelope(
                event_type=EventType.MEMORY_CREATED, correlation_id="mission-1"
            )
        )
        await bus.publish(event(1))
        await until(lambda: len(client.frames) == 1)
        assert client.frames[0]["payload"] == {"step_id": "s1"}
        assert stream.rejected_events == 2
        client.leave.set()
        await serving

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_close_sends_1001_to_every_client_and_refuses_new_ones() -> None:
    async def scenario() -> None:
        bus = EventBus()
        stream = EventStream(bus)
        clients = [FakeWebSocket(), FakeWebSocket(slow=True)]
        serving = [
            asyncio.create_task(stream.serve(client))  # type: ignore[arg-type]
            for client in clients
        ]
        await until(lambda: stream.connection_count == 2)
        await stream.close()
        await asyncio.gather(*serving)
        assert [client.closed_with for client in clients] == [WS_GOING_AWAY] * 2
        assert bus.subscriber_count == 0 and stream.connection_count == 0
        late = FakeWebSocket()
        await stream.serve(late)  # type: ignore[arg-type]
        assert late.closed_with == WS_GOING_AWAY and bus.subscriber_count == 0

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_bridge_rejects_invalid_configuration() -> None:
    for options in ({"client_buffer": 0}, {"close_timeout_seconds": 0.0}):
        with pytest.raises(ValueError):
            EventStream(EventBus(), **options)


# ---- the composition root through TestClient


def ws_protocols(token: str = TOKEN) -> list[str]:
    return [WS_SUBPROTOCOL, WS_TOKEN_PREFIX + token]


def server_app(data_dir: Path, factory: LoopCapture | None = None) -> Any:
    settings = AppSettings(data_dir=data_dir, _env_file=None)  # type: ignore[call-arg]
    return build_server_app(
        settings,
        security=LocalApiSecurity(TOKEN, host="127.0.0.1", port=PORT),
        config=FAST,
        provider_factory=factory or LoopCapture(),
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = server_app(tmp_path / "data")
    with TestClient(app, base_url=f"http://{HOST}") as client:
        yield client


def services_of(client: TestClient) -> BackendServices:
    services = get_services(client.app)  # type: ignore[arg-type]
    assert services is not None
    return services


@pytest.mark.parametrize(
    ("protocols", "headers"),
    [
        (None, {}),
        ([WS_SUBPROTOCOL], {}),
        (ws_protocols(WRONG), {}),
        ([WS_TOKEN_PREFIX + TOKEN], {}),
        (ws_protocols(), {"Origin": "https://evil.example"}),
        (ws_protocols(), {"Origin": "null"}),
        (ws_protocols(), {"Host": f"evil.example:{PORT}"}),
    ],
)
def test_events_requires_the_token_and_closes_1008_before_accept(
    client: TestClient, protocols: list[str] | None, headers: dict[str, str]
) -> None:
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(WS_URL, subprotocols=protocols, headers=headers):
            pass  # pragma: no cover
    assert caught.value.code == 1008
    assert services_of(client).event_bus.subscriber_count == 0


def test_valid_token_receives_mission_events_without_content(
    client: TestClient,
) -> None:
    services = services_of(client)
    with client.websocket_connect(
        WS_URL,
        subprotocols=ws_protocols(),
        headers={"Origin": "http://tauri.localhost"},
    ) as websocket:
        assert websocket.accepted_subprotocol == WS_SUBPROTOCOL  # token not echoed
        assert TOKEN not in repr(websocket.extra_headers)
        assert client.portal is not None
        conversation = client.portal.call(services.missions.create_conversation)
        mission_id = client.portal.call(
            services.missions.submit, conversation, QUESTION, "direct"
        )
        frames = [websocket.receive_text() for _ in range(5)]
    events = [EventEnvelope.model_validate_json(frame) for frame in frames]
    assert [
        (e.event_type.value, e.payload.get("mission_status") or e.payload.get("role"))
        for e in events
    ] == [
        ("task.state_changed", "queued"),
        ("task.state_changed", "running"),
        ("agent.started", "direct"),
        ("agent.completed", "direct"),
        ("task.state_changed", "completed"),
    ]
    assert {e.correlation_id for e in events} == {mission_id}
    for frame in frames:
        for secret in (TOKEN, USER, MODEL, "PELICAN"):
            assert secret not in frame
    assert set(json.loads(frames[0])) == {
        "event_id",
        "event_type",
        "timestamp",
        "task_id",
        "agent_id",
        "payload",
        "severity",
        "correlation_id",
    }


def test_disconnect_releases_the_bus_subscription(client: TestClient) -> None:
    services = services_of(client)
    for _ in range(3):
        with client.websocket_connect(WS_URL, subprotocols=ws_protocols()):
            assert client.portal is not None
            client.portal.call(
                until, lambda: services.event_stream.connection_count == 1
            )
            assert services.event_bus.subscriber_count == 1
        client.portal.call(
            until,
            lambda: services.event_bus.subscriber_count == 0
            and services.event_stream.connection_count == 0,
        )


def test_stream_close_closes_open_sockets_with_1001(client: TestClient) -> None:
    services = services_of(client)
    with client.websocket_connect(WS_URL, subprotocols=ws_protocols()) as ws:
        assert client.portal is not None
        client.portal.call(until, lambda: services.event_stream.connection_count == 1)
        client.portal.call(services.event_stream.close)
        message = ws.receive()
        assert (message["type"], message["code"]) == ("websocket.close", WS_GOING_AWAY)
    assert services.event_bus.subscriber_count == 0


def test_events_without_running_services_is_try_again_later(tmp_path: Path) -> None:
    app = server_app(tmp_path / "data")
    bare = TestClient(app, base_url=f"http://{HOST}")  # no lifespan: no services
    with bare.websocket_connect(WS_URL, subprotocols=ws_protocols()) as ws:
        assert ws.accepted_subprotocol == WS_SUBPROTOCOL
        message = ws.receive()
        assert (message["type"], message["code"]) == (
            "websocket.close",
            WS_TRY_AGAIN_LATER,
        )


# ---- a real uvicorn server with the websockets backend


def real_connect(port: int, **options: Any) -> ClientConnection:
    settings: dict[str, Any] = {
        "subprotocols": [Subprotocol(item) for item in ws_protocols()],
        "open_timeout": 5,
        "close_timeout": 2,
    }
    connection: ClientConnection = connect(
        f"ws://127.0.0.1:{port}/events", **(settings | options)
    )
    return connection


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            root.removeHandler(handler)
    root.setLevel(level)


def test_real_server_streams_events_and_closes_sockets_on_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_logging: None
) -> None:
    factory = LoopCapture()
    servers: list[launcher.PhoenixServer] = []
    real_create: Callable[..., Any] = launcher.create_server

    def create(*args: Any, **kwargs: Any) -> Any:
        created = real_create(*args, **kwargs, config=FAST, provider_factory=factory)
        servers.append(created[0])
        return created

    monkeypatch.setattr(launcher, "create_server", create)
    out, err = io.StringIO(), io.StringIO()
    result: list[int] = []
    thread = threading.Thread(
        target=lambda: result.append(
            launcher.main(
                ["--port", "0", "--data-dir", str(tmp_path / "data")],
                stdin=io.StringIO(TOKEN + "\n"),
                stdout=out,
                stderr=err,
            )
        ),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 15
    while not (servers and servers[0].announced_port) and time.monotonic() < deadline:
        time.sleep(0.02)
    server = servers[0]
    port = server.announced_port
    assert port is not None and factory.loop is not None
    loop = factory.loop
    app = server.config.app
    assert isinstance(app, FastAPI)
    services = get_services(app)
    assert services is not None
    try:
        # No token, or a foreign Origin: refused before the upgrade.
        for options in (
            {"subprotocols": None},
            {"origin": Origin("https://evil.example")},
        ):
            with pytest.raises(InvalidStatus) as caught:
                with real_connect(port, **options):
                    pass  # pragma: no cover
            assert caught.value.response.status_code == 403
        assert services.event_bus.subscriber_count == 0
        with real_connect(port) as first, real_connect(port) as second:
            for ws in (first, second):
                assert ws.subprotocol == WS_SUBPROTOCOL
                assert ws.response is not None
                assert TOKEN not in str(ws.response.headers)

            def call(coroutine: Any) -> Any:
                return asyncio.run_coroutine_threadsafe(coroutine, loop).result(10)

            call(until(lambda: services.event_bus.subscriber_count == 2))
            conversation = call(services.missions.create_conversation())
            mission_id = call(
                services.missions.submit(conversation, QUESTION, "direct")
            )
            for ws in (first, second):
                frames = [str(ws.recv(timeout=10)) for _ in range(5)]
                statuses = [json.loads(f)["payload"] for f in frames]
                assert statuses[-1] == {
                    "scope": "mission",
                    "mission_status": "completed",
                }
                assert {json.loads(f)["correlation_id"] for f in frames} == {mission_id}
                for frame in frames:
                    for secret in (TOKEN, USER, MODEL, "PELICAN"):
                        assert secret not in frame
            second.close()
            call(until(lambda: services.event_bus.subscriber_count == 1))
            server.should_exit = True
            with pytest.raises(ConnectionClosed) as closed:
                first.recv(timeout=10)
            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code in (1001, 1012)  # going away / restart
    finally:
        server.should_exit = True
        thread.join(15)
    assert result == [0]
    for text in (out.getvalue(), err.getvalue()):
        assert TOKEN not in text


# ---- the one authorized dependency (owner decision 1; ADR-088)

# pyproject.toml and uv.lock were byte-identical from 4a70867 (PHX-073) to the
# PHX-103 base; this gate owns current manifest/lock exactness.
DEPENDENCY_BASELINE = "4a70867203a70d3a85e2c141ef0dbb92bdff42e2"
WEBSOCKETS = "websockets>=17.2,<18"


def historic(path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"{DEPENDENCY_BASELINE}:{path}"], cwd=ROOT
    ).decode()


def test_exact_websockets_dependency_delta() -> None:
    before = tomllib.loads(historic("pyproject.toml"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["dependencies"].count(WEBSOCKETS) == 1
    project["project"]["dependencies"].remove(WEBSOCKETS)
    assert project == before
    old = tomllib.loads(historic("uv.lock"))
    new = tomllib.loads((ROOT / "uv.lock").read_text())
    old_records = {p["name"]: p for p in old.pop("package")}
    new_records = {p["name"]: p for p in new.pop("package")}
    assert old == new
    assert set(new_records) - set(old_records) == {"websockets"}
    assert not set(old_records) - set(new_records)
    added = new_records["websockets"]
    assert added["version"] == "17.2" and "dependencies" not in added
    for name, record in old_records.items():
        after = copy.deepcopy(new_records[name])
        if name == "phoenix":
            requires = after["metadata"]["requires-dist"]
            assert {"name": "websockets", "specifier": ">=17.2,<18"} in requires
            for entries in (after["dependencies"], requires):
                entries[:] = [x for x in entries if x["name"] != "websockets"]
        assert after == record, name
    assert version("websockets") == "17.2"
    assert metadata("websockets")["License-Expression"] == "BSD-3-Clause"
    # uvicorn's automatic WebSocket backend is the websockets sans-I/O one.
    assert AutoWebSocketsProtocol is WebSocketsSansIOProtocol
