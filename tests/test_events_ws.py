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
from starlette.testclient import WebSocketDenialResponse
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
    EVENT_BUS_CAPACITY,
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
MISSION = "5f0c1d2e-3a4b-4c5d-8e6f-7a8b9c0d1e2f"
MISSION = "5f0c1d2e-3a4b-4c5d-8e6f-7a8b9c0d1e2f"


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
        payload={"step_id": f"s{step % 99 + 1}"},
        correlation_id=MISSION,
    )


async def until(predicate: Callable[[], bool], timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def has_frames(client: FakeWebSocket, count: int) -> Callable[[], bool]:
    return lambda: len(client.frames) == count


async def settle_loop(rounds: int = 10) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


@pytest.fixture(autouse=True)
def no_model_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx network send attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_send)


# ---- the bridge


def start(stream: EventStream, client: FakeWebSocket) -> "asyncio.Task[None]":
    assert stream.reserve()
    return asyncio.create_task(stream.serve(client))  # type: ignore[arg-type]


def test_slow_client_gets_1013_while_others_keep_receiving() -> None:
    async def scenario() -> None:
        bus = EventBus()
        stream = EventStream(bus, client_buffer=2)
        fast, other, slow = FakeWebSocket(), FakeWebSocket(), FakeWebSocket(slow=True)
        serving = [start(stream, socket) for socket in (fast, other, slow)]
        await until(lambda: stream.connection_count == 3)
        assert bus.subscriber_count == 1  # one shared bridge subscription
        for step in range(10):
            await stream.publish(event(step))  # never waits for the slow client
            await settle_loop()
        await until(lambda: slow.closed_with is not None)
        assert slow.closed_with == WS_TRY_AGAIN_LATER
        assert stream.slow_disconnects == 1 and stream.connection_count == 2
        for step in range(10, 15):
            await stream.publish(event(step))
            await settle_loop()
        for client in (fast, other):
            await until(has_frames(client, 15))
            assert [f["payload"]["step_id"] for f in client.frames] == [
                f"s{step % 99 + 1}" for step in range(15)
            ]
            assert client.closed_with is None
        assert stream.dropped_events == 0 and stream.overflow_disconnects == 0
        fast.leave.set()
        other.leave.set()  # both remaining clients disconnect themselves
        await asyncio.gather(*serving)
        await until(lambda: bus.subscriber_count == 0)
        assert stream.connection_count == 0
        assert fast.closed_with is None  # no close is sent to a departed client

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_burst_beyond_256_without_yield_reaches_every_fast_client() -> None:
    async def scenario() -> None:
        bus = EventBus(capacity=EVENT_BUS_CAPACITY)
        stream = EventStream(bus)  # default per-client buffer: 256
        clients = [FakeWebSocket() for _ in range(3)]
        slow = FakeWebSocket(slow=True)
        serving = [start(stream, client) for client in (*clients, slow)]
        await until(lambda: stream.connection_count == 4)
        burst = 300
        for step in range(burst):  # no yield between publications
            await stream.publish(event(step))
        for client in clients:
            await until(has_frames(client, burst))
            assert client.closed_with is None
        await until(lambda: slow.closed_with is not None)
        assert slow.closed_with == WS_TRY_AGAIN_LATER  # only the slow one
        assert stream.dropped_events == 0 and stream.slow_disconnects == 1
        for client in clients:
            client.leave.set()
        await asyncio.gather(*serving)
        await until(lambda: bus.subscriber_count == 0)

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_bridge_subscription_overflow_closes_every_client_with_1013() -> None:
    async def scenario() -> None:
        bus = EventBus(capacity=4)
        stream = EventStream(bus)
        clients = [FakeWebSocket() for _ in range(3)]
        serving = [start(stream, client) for client in clients]
        await until(lambda: stream.connection_count == 3)
        for step in range(10):  # more than the bridge subscription holds
            await stream.publish(event(step))
        await asyncio.gather(*serving)
        # Nobody is left silently behind: every client must refetch over REST.
        assert [c.closed_with for c in clients] == [WS_TRY_AGAIN_LATER] * 3
        assert stream.dropped_events == 6 and stream.overflow_disconnects >= 1
        await until(lambda: bus.subscriber_count == 0)
        assert stream.connection_count == 0

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
        serving = start(stream, client)
        await until(lambda: bus.subscriber_count == 1)
        await bus.publish(
            EventEnvelope(
                event_type=EventType.AGENT_COMPLETED,
                payload={"role": "direct", "answer": f"PELICAN-7 {MODEL}"},
                correlation_id=MISSION,
            )
        )
        await bus.publish(
            EventEnvelope(event_type=EventType.MEMORY_CREATED, correlation_id=MISSION)
        )
        await bus.publish(event(1))
        await until(lambda: len(client.frames) == 1)
        assert client.frames[0]["payload"] == {"step_id": "s2"}
        assert stream.rejected_events == 2
        client.leave.set()
        await serving

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_close_sends_1001_to_every_client_and_refuses_new_ones() -> None:
    async def scenario() -> None:
        bus = EventBus()
        stream = EventStream(bus)
        clients = [FakeWebSocket(), FakeWebSocket(slow=True)]
        serving = [start(stream, client) for client in clients]
        await until(lambda: stream.connection_count == 2)
        await stream.close()
        await asyncio.gather(*serving)
        assert [client.closed_with for client in clients] == [WS_GOING_AWAY] * 2
        await until(lambda: bus.subscriber_count == 0)
        assert stream.connection_count == 0
        assert not stream.reserve()  # no slot once closed

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_serve_blocked_on_subscribe_during_close_is_refused() -> None:
    async def scenario() -> None:
        bus = EventBus()
        stream = EventStream(bus)
        assert stream.reserve()
        async with bus._lock:  # contend subscribe() while close() runs
            late = FakeWebSocket()
            serving = asyncio.create_task(stream.serve(late))  # type: ignore[arg-type]
            await settle_loop()
            closing = asyncio.create_task(stream.close())
            await settle_loop()
        await asyncio.gather(serving, closing)
        assert late.closed_with == WS_GOING_AWAY
        await until(lambda: bus.subscriber_count == 0)
        assert stream.connection_count == 0

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_connection_cap_reserves_and_releases_slots() -> None:
    async def scenario() -> None:
        stream = EventStream(EventBus(), max_connections=2)
        first, second = FakeWebSocket(), FakeWebSocket()
        serving = [start(stream, first), start(stream, second)]
        await until(lambda: stream.connection_count == 2)
        assert not stream.reserve() and stream.refused_connections == 1
        first.leave.set()
        await serving[0]
        assert stream.reserve()  # the departed client's slot is free again
        stream.release()
        second.leave.set()
        await serving[1]

    asyncio.run(asyncio.wait_for(scenario(), 10))


def test_bridge_rejects_invalid_configuration() -> None:
    for options in (
        {"client_buffer": 0},
        {"max_connections": 0},
        {"close_timeout_seconds": 0.0},
    ):
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
    assert client.portal is not None
    client.portal.call(until, lambda: services.event_bus.subscriber_count == 0)


def test_connections_beyond_the_cap_are_refused_with_503(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "data", _env_file=None)  # type: ignore[call-arg]
    app = build_server_app(
        settings,
        security=LocalApiSecurity(TOKEN, host="127.0.0.1", port=PORT),
        config=FAST.model_copy(update={"event_max_connections": 2}),
        provider_factory=LoopCapture(),
    )
    with TestClient(app, base_url=f"http://{HOST}") as client:
        stream = services_of(client).event_stream
        with (
            client.websocket_connect(WS_URL, subprotocols=ws_protocols()),
            client.websocket_connect(WS_URL, subprotocols=ws_protocols()),
        ):
            assert client.portal is not None
            client.portal.call(until, lambda: stream.connection_count == 2)
            with pytest.raises(WebSocketDenialResponse) as caught:
                with client.websocket_connect(WS_URL, subprotocols=ws_protocols()):
                    pass  # pragma: no cover
            denied = caught.value
            assert denied.status_code == 503
            assert denied.headers["retry-after"] == "1"
            assert denied.json()["error"]["code"] == "too_many_connections"
            assert TOKEN not in denied.text
            assert stream.refused_connections == 1
        client.portal.call(until, lambda: stream.connection_count == 0)
        # Slots are released: a new connection is accepted again.
        with client.websocket_connect(WS_URL, subprotocols=ws_protocols()) as ws:
            assert ws.accepted_subprotocol == WS_SUBPROTOCOL


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

            call(until(lambda: services.event_stream.connection_count == 2))
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
            call(until(lambda: services.event_stream.connection_count == 1))
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
# The exact PyPI artifacts locked for 17.2: the sdist and 27 wheels.
WEBSOCKETS_HASHES = frozenset(
    {
        "063508ce9e0db745f30ab52fc652f4e59efc79c2b74934b3837d5cdb974da620",
        "15a7101b660a9f15fac34108c92cefc9848f6753a50acef8869e3cd94148fdb7",
        "214da56dba368f61b3d745c77630b2d03c61c02da7b42fe80ef6efba079d3077",
        "2de1ccf298f5c9e0f27113836d742edb95f015eee3148f004ac386f7ba9a05b1",
        "36c2fb94c990cc2545143b12690e2de6c16300f9dbe5b4f33fa300cf57dc8792",
        "376a693697ddb695ea282ead76060f4847f90e564b12b4389f2c7589e6fadb9e",
        "3892d76754b5f36fb40619f3ef09c68e5c3091f1ab8840964518ae5a41f30952",
        "4733fc2d99fe888261417b7e29995403a72d9ffa78629902882325ea141177f2",
        "48997ed4431d8006988788ef4b62e1fd3f053c7463b4fa793aa6c4f9e96a3bb7",
        "4e312e07557a5ad348f4e83d3419773527f6e790c7f97928b1911d767b6ea1c7",
        "5436ffea003adb50e283ca0684a3fcaa1396104f841736c3322ee6582bd09e98",
        "55c5b9eab079540bfb639b40b07b7b467e5c5a7ecf97a65cc8665781381c9856",
        "55f9a808a0e072473337c240c939849818276e288e2374b832255b5b791b0851",
        "569ed5db651e420b13279f9333443bb5b84a436cc66b599cbc535697ae4434a0",
        "660aa158127035e741d4b1835dbe79ae18a1fbb21ecd236655f31d60110e68d5",
        "6aa59f0ef92e796b2db6f5f26550c4713c0e4036899fadf02f55e2ed4db0b7ae",
        "761cde41439f0be761aa460e1451a31e2e14baf4a46db6fe4913e5a06a90df66",
        "80cbc645af23ac5c12096545c161626960114a1bc10f864760558d3b3e82ba18",
        "902ce8cafca2dc14cef9558a6fc3b45dbf7f121d1404bf2ad18a1c894555e48c",
        "946ac2164d646e733004946ae39536b5af473853183d81da5962e29d36e3ad35",
        "9c393a202df08e96ed619310f0cd78be700e532a57d9a6ceee5f80b4e35bef14",
        "9df9d048def11365d170b375b6ffc8b23a7f188c3560acd4418ba088ca2e2705",
        "ada04d0262ab06527054a2a497f384d102698ff39b3865dc566a7d24b6f4058c",
        "af4c565b923bb5975401b8e4cedc2e17b2fdbf33b905737ee12384e6a6fd9507",
        "c2ec7e51157a3fa0e9cfdb1a8969bab38d1c22ad1ace7c6cea006383b43a1ad4",
        "c81d6cdbacccda7e0eef3b076a457fd14c3835cdbc5993d2881580c2fb1f5f26",
        "e53d950e16d4bb672a5ff41fe3131e65a4e5d688d694e1c7074c8c9990bb3ceb",
        "ecd63d0c7ed0d3d719c91b5a3861f0f0b3cec9bf223033ddf69d17aaac74bb6d",
    }
)


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
    assert added["source"] == {"registry": "https://pypi.org/simple"}
    artifacts = [added["sdist"], *added["wheels"]]
    assert len(artifacts) == len(WEBSOCKETS_HASHES) == 28
    assert {a["hash"].removeprefix("sha256:") for a in artifacts} == (WEBSOCKETS_HASHES)
    assert all(a["hash"].startswith("sha256:") for a in artifacts)
    assert all(
        a["url"].startswith("https://files.pythonhosted.org/packages/")
        and "/websockets-17.2" in a["url"]
        for a in artifacts
    )
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
