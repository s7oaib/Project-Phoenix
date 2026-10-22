"""PHX-101 local API security: token, Host, Origin, WebSocket and envelope (ADR-085).

Fakes only, with SQLite under a temporary directory. The one real-server test
binds an OS-assigned loopback port and talks only to itself; any httpx send,
which is the Ollama adapter's path, fails the test.
"""

import ast
import hmac
import http.client
import io
import json
import logging
import secrets
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from pydantic import BaseModel
from starlette.websockets import WebSocketDisconnect

from phoenix.api import launcher, security
from phoenix.api.security import (
    TAURI_ORIGINS,
    WS_SUBPROTOCOL,
    WS_TOKEN_PREFIX,
    LocalApiSecurity,
    TokenError,
    accept_websocket,
    parse_token,
)
from phoenix.api.server import ProviderSet, ServerConfig, build_server_app
from phoenix.core.config import AppSettings
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
)

ROOT = Path(__file__).resolve().parents[1]
TOKEN = secrets.token_urlsafe(32)
WRONG = secrets.token_urlsafe(32)
PORT = 48766
HOST = f"127.0.0.1:{PORT}"
BASE = f"http://{HOST}"
WS_URL = f"ws://{HOST}/test-ws"
FAST = ServerConfig(probe_timeout_seconds=0.2, startup_timeout_seconds=2)
MARKER = "leak-marker-7f3a9c"
LAUNCHER_STOP_SECONDS = 120  # a slow CI migration must finish, not leak
REQUEST_ID = set("0123456789abcdef")


class Body(BaseModel):
    count: int


class HealthyProvider(ModelProvider):
    """Healthy, with the T0 model installed; never generates."""

    def __init__(self) -> None:
        super().__init__("ollama")

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id, status=ProviderHealthStatus.HEALTHY
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        if model_id != "qwen3.5:2b":
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
        raise ModelProviderError("generation is not scripted")

    async def _no_stream(self) -> AsyncIterator[GenerationChunk]:
        raise ModelProviderError("streaming is not scripted")
        yield  # pragma: no cover

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        return self._no_stream()

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.UNSUPPORTED,
        )


def fake_providers(config: ServerConfig) -> ProviderSet:
    return ProviderSet(providers=(HealthyProvider(),), structured=(HealthyProvider(),))


def secured_app(data_dir: Path) -> FastAPI:
    settings = AppSettings(data_dir=data_dir, _env_file=None)  # type: ignore[call-arg]
    app = build_server_app(
        settings,
        security=LocalApiSecurity(TOKEN, host="127.0.0.1", port=PORT),
        config=FAST,
        provider_factory=fake_providers,
    )

    # Test-only routes; production has no WebSocket or echo route (PHX-103/104).
    @app.post("/test-echo")
    def echo(body: Body) -> dict[str, int]:
        return {"count": body.count}

    @app.get("/test-crash")
    def crash() -> None:
        raise RuntimeError(MARKER)

    @app.websocket("/test-ws")
    async def socket(websocket: WebSocket) -> None:
        if await accept_websocket(websocket):
            await websocket.send_text("authenticated")
            await websocket.close()

    return app


@pytest.fixture(autouse=True)
def no_model_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx network send attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_send)


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    app = secured_app(tmp_path_factory.mktemp("security") / "data")
    with TestClient(app, base_url=BASE, raise_server_exceptions=False) as client:
        yield client


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_envelope(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "request_id"}
    assert error["code"] == code
    assert len(error["request_id"]) == 32 and set(error["request_id"]) <= REQUEST_ID
    assert response.headers["x-request-id"] == error["request_id"]
    assert TOKEN not in response.text and TOKEN not in str(response.headers)


# ---- token format and the security object


def test_parse_token_accepts_url_safe_base64_of_32_plus_bytes() -> None:
    for token in (secrets.token_urlsafe(32), secrets.token_urlsafe(64), "A" * 43):
        assert parse_token(token) == token
        assert parse_token(token + "\n") == token
        assert parse_token(token + "\r\n") == token


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "\n",
        secrets.token_urlsafe(16),  # 22 chars, 16 bytes
        "A" * 42,
        "A" * 45,  # length 1 mod 4 is not base64
        "A" * 513,
        secrets.token_urlsafe(32) + "=",
        "a+b/" * 11,
        "A" * 20 + " " + "A" * 22,
        " " + secrets.token_urlsafe(32),
        "é" * 43,
        secrets.token_urlsafe(32) + "\n\n",
    ],
)
def test_parse_token_rejects_malformed(raw: str) -> None:
    with pytest.raises(TokenError) as caught:
        parse_token(raw)
    if raw.strip():
        assert raw.strip() not in str(caught.value)


@pytest.mark.parametrize(
    ("host", "port"), [("0.0.0.0", PORT), ("localhost", PORT), ("127.0.0.1", 0)]
)
def test_security_requires_bound_loopback(host: str, port: int) -> None:
    with pytest.raises(ValueError):
        LocalApiSecurity(TOKEN, host=host, port=port)
    with pytest.raises(ValueError):
        LocalApiSecurity(TOKEN, host="127.0.0.1", port=True)


def test_security_repr_and_host_header_hide_token() -> None:
    policy = LocalApiSecurity(TOKEN, host="127.0.0.1", port=PORT)
    assert TOKEN not in repr(policy) and "redacted" in repr(policy)
    assert policy.host_header == HOST
    assert LocalApiSecurity(TOKEN, host="::1", port=PORT).host_header == f"[::1]:{PORT}"
    assert policy.allowed_origins == TAURI_ORIGINS


# ---- HTTP token


@pytest.mark.parametrize("path", ["/health", "/ready", "/does-not-exist"])
def test_every_route_requires_the_token(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert_envelope(response, 401, "unauthorized")
    assert response.headers["www-authenticate"] == "Bearer"
    assert client.get(path, headers=auth()).status_code in (200, 404)


@pytest.mark.parametrize(
    "value",
    [
        f"Bearer {WRONG}",
        f"Bearer {TOKEN[:-1]}",
        f"Bearer {TOKEN}x",
        f"Basic {TOKEN}",
        TOKEN,
        "Bearer",
        "Bearer ",
        f"Bearer  {TOKEN}",
        f"Bearer {TOKEN} extra",
        f" Bearer {TOKEN}",
    ],
)
def test_wrong_or_malformed_token_is_unauthorized(
    client: TestClient, value: str
) -> None:
    response = client.get("/health", headers={"Authorization": value})
    assert_envelope(response, 401, "unauthorized")


def test_non_ascii_and_repeated_authorization_are_unauthorized(
    client: TestClient,
) -> None:
    non_ascii = [(b"authorization", ("Bearer " + "é" * 43).encode("latin-1"))]
    assert_envelope(client.get("/health", headers=non_ascii), 401, "unauthorized")
    repeated = [("Authorization", f"Bearer {TOKEN}")] * 2
    assert_envelope(client.get("/health", headers=repeated), 401, "unauthorized")


def test_correct_token_reaches_routes_and_scheme_is_case_insensitive(
    client: TestClient,
) -> None:
    for scheme in ("Bearer", "bearer", "BEARER"):
        response = client.get("/health", headers={"Authorization": f"{scheme} {TOKEN}"})
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert len(response.headers["x-request-id"]) == 32
    ready = client.get("/ready", headers=auth())
    assert ready.status_code == 200 and ready.json()["status"] == "ready"


def test_token_compare_is_constant_time(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[bytes, bytes]] = []
    original = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    assert client.get("/health", headers=auth()).status_code == 200
    assert client.get("/health", headers=auth(WRONG)).status_code == 401
    assert len(calls) == 2
    assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in calls)
    # The token is never compared any other way.
    tree = ast.parse((ROOT / "phoenix/api/security.py").read_text(encoding="utf-8"))
    compares = [node for node in ast.walk(tree) if isinstance(node, ast.Compare)]
    assert not any("_token" in ast.unparse(node) for node in compares)


# ---- Host and Origin


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:1",
        "127.0.0.1",
        f"localhost:{PORT}",
        f"evil.example:{PORT}",
        f"127.0.0.1.nip.io:{PORT}",
        f"[::1]:{PORT}",
        f"0.0.0.0:{PORT}",
        "",
    ],
)
def test_wrong_host_is_forbidden_even_with_token(client: TestClient, host: str) -> None:
    response = client.get("/health", headers={**auth(), "Host": host})
    assert_envelope(response, 403, "forbidden_host")


def test_repeated_host_is_forbidden(client: TestClient) -> None:
    headers = [("Host", HOST), ("Host", HOST), ("Authorization", f"Bearer {TOKEN}")]
    assert_envelope(client.get("/health", headers=headers), 403, "forbidden_host")


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "null",
        "http://127.0.0.1:5173",
        BASE,
        "http://tauri.localhost.evil.example",
        "HTTP://TAURI.LOCALHOST",
        "http://tauri.localhost/",
        "",
    ],
)
def test_foreign_origin_is_forbidden_even_with_token(
    client: TestClient, origin: str
) -> None:
    response = client.post(
        "/test-echo", json={"count": 1}, headers={**auth(), "Origin": origin}
    )
    assert_envelope(response, 403, "forbidden_origin")


def test_repeated_origin_is_forbidden(client: TestClient) -> None:
    headers = [("Authorization", f"Bearer {TOKEN}")] + [
        ("Origin", TAURI_ORIGINS[0])
    ] * 2
    assert_envelope(client.get("/health", headers=headers), 403, "forbidden_origin")


@pytest.mark.parametrize("origin", [*TAURI_ORIGINS, None])
def test_tauri_origins_and_no_origin_are_accepted(
    client: TestClient, origin: str | None
) -> None:
    headers = auth() if origin is None else {**auth(), "Origin": origin}
    response = client.post("/test-echo", json={"count": 2}, headers=headers)
    assert response.status_code == 200 and response.json() == {"count": 2}


def test_no_cors_headers_ever(client: TestClient) -> None:
    responses = [
        client.get("/health", headers={**auth(), "Origin": TAURI_ORIGINS[0]}),
        client.options(
            "/health",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        ),
        client.options(
            "/health",
            headers={
                **auth(),
                "Origin": TAURI_ORIGINS[0],
                "Access-Control-Request-Method": "GET",
            },
        ),
    ]
    assert responses[0].status_code == 200
    assert_envelope(responses[1], 403, "forbidden_origin")
    assert_envelope(responses[2], 405, "method_not_allowed")
    for response in responses:
        assert not any(
            name.lower().startswith("access-control-") for name in response.headers
        )


# ---- error envelope


def test_envelope_for_404_405_422_and_500_never_echoes_input(
    client: TestClient,
) -> None:
    assert_envelope(client.get(f"/{MARKER}", headers=auth()), 404, "not_found")
    method = client.delete("/health", headers=auth())
    assert_envelope(method, 405, "method_not_allowed")
    assert method.headers["allow"] == "GET"
    for payload in ({"count": MARKER}, {"unexpected": MARKER}, MARKER):
        invalid = client.post("/test-echo", json=payload, headers=auth())
        assert_envelope(invalid, 422, "invalid_request")
        assert MARKER not in invalid.text
    crashed = client.get("/test-crash", headers=auth())
    assert_envelope(crashed, 500, "internal_error")
    assert MARKER not in crashed.text


def test_client_request_id_is_ignored(client: TestClient) -> None:
    response = client.get("/nope", headers={**auth(), "X-Request-ID": MARKER})
    assert_envelope(response, 404, "not_found")
    assert MARKER not in response.text


def test_token_never_appears_in_responses_or_logs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    texts: list[str] = []
    for headers in (
        auth(),
        auth(WRONG),
        {"Authorization": f"Basic {TOKEN}"},
        {**auth(), "Host": "evil.example"},
        {**auth(), "Origin": "https://evil.example"},
    ):
        for path in ("/health", "/ready", "/missing"):
            response = client.get(path, headers=headers)
            texts.append(response.text + str(response.headers))
    assert caplog.records  # capture is active
    for text in (*texts, caplog.text):
        assert TOKEN not in text


# ---- WebSocket helper (test-only endpoint)


def ws_protocols(token: str = TOKEN) -> list[str]:
    return [WS_SUBPROTOCOL, WS_TOKEN_PREFIX + token]


def test_websocket_accepts_token_subprotocol(client: TestClient) -> None:
    for origin in (*TAURI_ORIGINS, None):
        headers = {} if origin is None else {"Origin": origin}
        with client.websocket_connect(
            WS_URL, subprotocols=ws_protocols(), headers=headers
        ) as websocket:
            assert websocket.accepted_subprotocol == WS_SUBPROTOCOL
            assert websocket.receive_text() == "authenticated"


@pytest.mark.parametrize(
    ("protocols", "headers"),
    [
        (None, {}),
        ([WS_SUBPROTOCOL], {}),
        (ws_protocols(WRONG), {}),
        ([WS_TOKEN_PREFIX + TOKEN], {}),
        ([*ws_protocols(), WS_TOKEN_PREFIX + TOKEN], {}),
        ([*ws_protocols(), WS_TOKEN_PREFIX + WRONG], {}),
        (ws_protocols(), {"Origin": "https://evil.example"}),
        (ws_protocols(), {"Origin": "null"}),
        (ws_protocols(), {"Host": f"evil.example:{PORT}"}),
    ],
)
def test_websocket_rejections_close_before_accept(
    client: TestClient, protocols: list[str] | None, headers: dict[str, str]
) -> None:
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(WS_URL, subprotocols=protocols, headers=headers):
            pass  # pragma: no cover
    assert caught.value.code == 1008


def test_websocket_helper_fails_closed_without_installed_security() -> None:
    bare = FastAPI()

    @bare.websocket("/test-ws")
    async def socket(websocket: WebSocket) -> None:
        await accept_websocket(websocket)

    with TestClient(bare, base_url=BASE) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(WS_URL, subprotocols=ws_protocols()):
                pass  # pragma: no cover
    assert caught.value.code == 1008


def test_websocket_helper_rechecks_when_middleware_is_bypassed() -> None:
    bare = FastAPI()
    setattr(
        bare.state,
        security.STATE_KEY,
        LocalApiSecurity(TOKEN, host="127.0.0.1", port=PORT),
    )

    @bare.websocket("/test-ws")
    async def socket(websocket: WebSocket) -> None:
        if await accept_websocket(websocket):
            await websocket.close()

    with TestClient(bare, base_url=BASE) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(WS_URL, subprotocols=ws_protocols(WRONG)):
                pass  # pragma: no cover
        assert caught.value.code == 1008
        with client.websocket_connect(WS_URL, subprotocols=ws_protocols()) as ws:
            assert ws.accepted_subprotocol == WS_SUBPROTOCOL


# ---- launcher: token handoff and refusal


class Unreadable(io.StringIO):
    def readline(self, size: int | None = -1, /) -> str:  # type: ignore[override]
        raise AssertionError("stdin read before the host was validated")


class Blocking(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.release = threading.Event()

    def readline(self, size: int | None = -1, /) -> str:  # type: ignore[override]
        self.release.wait(10)
        return ""


def refuse_bind(*args: object) -> Any:
    raise AssertionError("bound without a valid token")


@pytest.mark.parametrize(
    ("stdin", "message"),
    [
        (io.StringIO(""), "No API token"),
        (io.StringIO("\n"), "No API token"),
        (io.StringIO(f"{MARKER}\n"), "URL-safe base64"),
        (io.StringIO(secrets.token_urlsafe(16) + "\n"), "URL-safe base64"),
        (io.StringIO("A" * 2000 + "\n"), "URL-safe base64"),
    ],
)
def test_startup_without_valid_token_is_refused(
    stdin: io.StringIO, message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "bind_loopback", refuse_bind)
    out, err = io.StringIO(), io.StringIO()
    assert launcher.main([], stdin=stdin, stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert message in err.getvalue() and "not starting" in err.getvalue()
    assert MARKER not in err.getvalue()


def test_late_token_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher, "bind_loopback", refuse_bind)
    stdin, out, err = Blocking(), io.StringIO(), io.StringIO()
    started = time.monotonic()
    try:
        code = launcher.main([], stdin=stdin, stdout=out, stderr=err, token_timeout=0.2)
    finally:
        stdin.release.set()
    assert code == 2 and time.monotonic() - started < 5
    assert "No API token on stdin within 0.2 s" in err.getvalue()


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "::"])
def test_non_loopback_refused_before_token_is_read(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher, "bind_loopback", refuse_bind)
    out, err = io.StringIO(), io.StringIO()
    code = launcher.main(["--host", host], stdin=Unreadable(), stdout=out, stderr=err)
    assert code == 2 and out.getvalue() == ""
    assert "loopback only" in err.getvalue()


def test_there_is_no_insecure_flag() -> None:
    with pytest.raises(SystemExit) as caught:
        launcher.parse_options(["--dev-insecure"])
    assert caught.value.code == 2
    assert "insecure" not in launcher.build_parser().format_help().lower()


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            root.removeHandler(handler)
    root.setLevel(level)


def stop_launcher(
    servers: list[launcher.PhoenixServer], thread: threading.Thread
) -> None:
    """Stop and join the in-process launcher before the test ends.

    A thread left running (e.g. mid-migration) shares alembic's process-global
    context with later tests' migrations and breaks them, so never let it leak.
    """
    deadline = time.monotonic() + LAUNCHER_STOP_SECONDS
    while thread.is_alive() and time.monotonic() < deadline:
        for server in servers:  # a server created late is stopped too
            server.should_exit = True
        thread.join(0.05)
    assert not thread.is_alive(), (
        f"launcher thread still alive after {LAUNCHER_STOP_SECONDS} s; it would leak"
    )


def request(port: int, path: str, headers: dict[str, str]) -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


def test_real_launcher_with_stdin_token_serves_securely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    restore_logging: None,
) -> None:
    caplog.set_level(logging.DEBUG)
    servers: list[launcher.PhoenixServer] = []
    real_create: Callable[..., Any] = launcher.create_server

    def create(*args: Any, **kwargs: Any) -> Any:
        created = real_create(
            *args, **kwargs, config=FAST, provider_factory=fake_providers
        )
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
        name="phoenix-launcher-under-test",
        daemon=True,
    )
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not (servers and servers[0].announced_port):
            assert time.monotonic() < deadline, "launcher never announced its port"
            time.sleep(0.02)
        server = servers[0]
        port = server.announced_port
        assert port is not None
        host = f"127.0.0.1:{port}"
        bearer = {"Host": host, "Authorization": f"Bearer {TOKEN}"}
        assert request(port, "/health", bearer)[0] == 200
        assert request(port, "/ready", bearer)[0] == 200
        assert request(port, "/health", {"Host": host})[0] == 401
        status, body = request(port, "/health", {**bearer, "Host": f"evil:{port}"})
        assert status == 403 and json.loads(body)["error"]["code"] == "forbidden_host"
    finally:
        stop_launcher(servers, thread)
    assert result == [0]
    lines = out.getvalue().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["port"] == port
    for text in (out.getvalue(), err.getvalue(), caplog.text):
        assert TOKEN not in text
