"""PHX-100 backend composition root, readiness and entrypoint (ADR-084).

Fakes only: a scripted ModelProvider and SQLite under tmp_path. An autouse guard
fails any httpx send or outbound socket connection, so no test touches Ollama
or the network. Since PHX-101 every client presents the token and bound Host.
"""

import asyncio
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect

from alembic import command
from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest
from phoenix.agents.runtime import MissionKind, PhoenixRuntimeError
from phoenix.api import launcher
from phoenix.api.launcher import (
    LaunchOptions,
    PhoenixServer,
    create_server,
    main,
    parse_options,
)
from phoenix.api.security import LocalApiSecurity
from phoenix.api.server import (
    BackendServices,
    MigrationLockTimeout,
    ProviderSet,
    ServerConfig,
    build_server_app,
    get_services,
    migrate_to_head,
    migration_config,
    migration_lock,
    ollama_providers,
)
from phoenix.core.config import AppSettings
from phoenix.models.ollama import OllamaProvider
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
from phoenix.persistence.database import create_engine_for_settings
from phoenix.tools.filesystem import filesystem_tool_definitions

HEAD = "0008_mission_triage"
ROOT = Path(__file__).resolve().parents[1]
TOKEN = secrets.token_urlsafe(32)
PORT = 48765  # Host-header identity for in-process clients; nothing binds it
SECURITY = LocalApiSecurity(TOKEN, host="127.0.0.1", port=PORT)
SECRET_DETAIL = "internal-detail-http://10.9.8.7:4321/private"
INSTALLED = ("qwen3.5:4b", "qwen3.5:2b")
FAST = ServerConfig(probe_timeout_seconds=0.2, startup_timeout_seconds=2)


class FakeProvider(ModelProvider):
    """Scripted provider; generation blocks until cancelled."""

    def __init__(
        self,
        *,
        provider_id: str = "ollama",
        status: ProviderHealthStatus = ProviderHealthStatus.HEALTHY,
        models: tuple[str, ...] = INSTALLED,
        health_error: Exception | None = None,
        hang: bool = False,
        report_id: str | None = None,
    ) -> None:
        super().__init__(provider_id)
        self.report_id = provider_id if report_id is None else report_id
        self.status = status
        self.models = models
        self.health_error = health_error
        self.hang = hang
        self.health_calls = 0
        self.closed = 0
        self.generating = False

    async def health(self) -> ProviderHealth:
        self.health_calls += 1
        if self.hang:
            await asyncio.Event().wait()
        if self.health_error is not None:
            raise self.health_error
        return ProviderHealth(
            provider_id=self.report_id, status=self.status, detail=SECRET_DETAIL
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        if model_id not in self.models:
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
        self.generating = True
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

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

    async def aclose(self) -> None:
        self.closed += 1


class Factory:
    """Counts constructions; returns the same fakes it was given."""

    def __init__(self, default: FakeProvider, structured: FakeProvider) -> None:
        self.default = default
        self.structured = structured
        self.calls = 0

    def __call__(self, config: ServerConfig) -> ProviderSet:
        self.calls += 1
        return ProviderSet(providers=(self.default,), structured=(self.structured,))


def factory(**kwargs: object) -> Factory:
    return Factory(FakeProvider(**kwargs), FakeProvider())  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx network send attempted")

    def refuse_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("outbound socket connection attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_send)
    monkeypatch.setattr(socket, "create_connection", refuse_connect)


@pytest.fixture
def settings(tmp_path: Path) -> AppSettings:
    return AppSettings(data_dir=tmp_path / "data", _env_file=None)  # type: ignore[call-arg]


@contextmanager
def services_engine(settings: AppSettings) -> Iterator[Engine]:
    engine = create_engine_for_settings(settings)
    try:
        yield engine
    finally:
        engine.dispose()


def client_for(app: FastAPI) -> TestClient:
    """An authenticated client presenting the bound loopback Host (PHX-101)."""
    return TestClient(
        app,
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def services_of(client: TestClient) -> BackendServices:
    services = get_services(client.app)  # type: ignore[arg-type]
    assert services is not None
    return services


def test_construction_is_lazy(settings: AppSettings) -> None:
    fakes = factory()
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=fakes
    )
    assert fakes.calls == 0
    assert not settings.data_dir.exists()
    assert get_services(app) is None
    paths = {getattr(route, "path", None) for route in app.routes}
    # PHX-103 (ADR-087) adds WS /events.
    assert {"/health", "/ready", "/events"} <= paths
    # PHX-104 (ADR-089) includes the REST routers (tests/test_rest_api.py owns
    # the exact table); docs and the deferred PHX-110 routes stay absent.
    included = {
        getattr(route, "path", None)
        for router in app.routes
        for route in getattr(getattr(router, "original_router", None), "routes", ())
    }
    assert {"/chat", "/missions", "/agents", "/models", "/settings"} <= included
    assert not (paths | included) & {
        "/tasks",
        "/files/upload",
        "/memory/search",
        "/docs",
        "/openapi.json",
    }


def test_lifespan_builds_services_once_and_closes_them(settings: AppSettings) -> None:
    fakes = factory()
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=fakes
    )
    with client_for(app) as client:
        services = services_of(client)
        for _ in range(3):
            assert client.get("/ready").status_code == 200
        assert services_of(client) is services
        assert fakes.calls == 1
        assert services.providers == (fakes.default,)
        assert services.structured_providers == (fakes.structured,)
        assert services.runtime.orchestrator_id == "phoenix"
        assert services.lifecycle.capacity == 1  # INV-023
        assert services.migration_heads == (HEAD,)
        assert services.event_bus.subscriber_count == 0
        assert services.tool_registry.list_tools() == filesystem_tool_definitions()
        assert services.permission_policy.rules == ()
        assert fakes.default.closed == fakes.structured.closed == 0
    assert get_services(app) is None
    assert fakes.default.closed == fakes.structured.closed == 1
    assert fakes.calls == 1


def test_startup_migrates_database_to_head(settings: AppSettings) -> None:
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=factory()
    )
    with client_for(app) as client:
        engine = services_of(client).engine
        tables = set(inspect(engine).get_table_names())
        assert {"alembic_version", "memories", "documents", "audit_events"} <= tables
        with engine.connect() as connection:
            revision: str = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one()
        assert revision == HEAD
    assert (settings.data_dir / "phoenix.db").is_file()


def test_ready_when_provider_and_database_are_healthy(settings: AppSettings) -> None:
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=factory()
    )
    with client_for(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "phoenix-api",
        "version": version("phoenix"),
        "providers": [{"provider_id": "ollama", "status": "healthy"}],
        "models": [
            {"provider_id": "ollama", "model_id": "qwen3.5:2b"},
            {"provider_id": "ollama", "model_id": "qwen3.5:4b"},
        ],
        "database": {"at_head": True},
    }


def test_missing_tier_models_are_skipped(settings: AppSettings) -> None:
    fakes = factory(models=("qwen3.5:2b",))
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=fakes
    )
    with client_for(app) as client:
        body = client.get("/ready").json()
    assert body["status"] == "ready"
    assert body["models"] == [{"provider_id": "ollama", "model_id": "qwen3.5:2b"}]


@pytest.mark.parametrize(
    ("kwargs", "reported"),
    [
        ({"status": ProviderHealthStatus.UNAVAILABLE}, "unavailable"),
        ({"status": ProviderHealthStatus.DEGRADED}, "degraded"),
        ({"health_error": ModelProviderError("down")}, "unavailable"),
        ({"health_error": RuntimeError("defect")}, "unavailable"),
        # A health report naming another provider ID is not trusted.
        ({"report_id": "impostor"}, "unavailable"),
    ],
)
def test_unhealthy_provider_is_degraded_not_a_crash(
    settings: AppSettings, kwargs: dict[str, object], reported: str
) -> None:
    fakes = factory(**kwargs)
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=fakes
    )
    with client_for(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "degraded"
    assert body["providers"][0]["status"] == reported
    assert body["database"] == {"at_head": True}
    if reported == "unavailable":
        assert body["models"] == []


def test_hung_provider_probe_is_bounded(settings: AppSettings) -> None:
    fakes = factory(hang=True)
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=fakes
    )
    with client_for(app) as client:
        started = time.monotonic()
        body = client.get("/ready").json()
        elapsed = time.monotonic() - started
    assert elapsed < 2
    assert body["status"] == "degraded"
    assert body["providers"] == [{"provider_id": "ollama", "status": "unavailable"}]
    assert body["models"] == []


def test_no_registered_models_is_degraded(settings: AppSettings) -> None:
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=factory(models=())
    )
    with client_for(app) as client:
        body = client.get("/ready").json()
    assert body["status"] == "degraded"
    assert body["providers"] == [{"provider_id": "ollama", "status": "healthy"}]


def test_database_behind_head_is_unavailable(settings: AppSettings) -> None:
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=factory()
    )
    with client_for(app) as client:
        command.downgrade(migration_config(settings, FAST), "0001")
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
    assert response.json()["database"] == {"at_head": False}


def test_readiness_echoes_no_detail_or_url(settings: AppSettings) -> None:
    config = ServerConfig(
        ollama_base_url="http://127.0.0.1:59999/private-path",
        probe_timeout_seconds=0.2,
    )
    app = build_server_app(
        settings, security=SECURITY, config=config, provider_factory=factory()
    )
    with client_for(app) as client:
        text = client.get("/ready").text
    leaks = (SECRET_DETAIL, "59999", "private", "http://", str(settings.data_dir))
    for secret in leaks:
        assert secret not in text


def test_failed_startup_closes_opened_providers(settings: AppSettings) -> None:
    fakes = Factory(FakeProvider(), FakeProvider(provider_id="orphan"))
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=fakes
    )
    with pytest.raises(PhoenixRuntimeError):
        with client_for(app):
            pass  # pragma: no cover
    assert fakes.default.closed == fakes.structured.closed == 1
    assert get_services(app) is None


def test_migration_failure_stops_before_providers(settings: AppSettings) -> None:
    settings.data_dir.parent.mkdir(parents=True, exist_ok=True)
    settings.data_dir.write_text("not a directory", encoding="utf-8")
    fakes = factory()
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=fakes
    )
    with pytest.raises(OSError):
        with client_for(app):
            pass  # pragma: no cover
    assert fakes.calls == 0


def test_shutdown_cancels_active_missions(settings: AppSettings) -> None:
    fakes = factory()
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=fakes
    )

    async def scenario() -> MissionKind:
        async with app.router.lifespan_context(app):
            services = get_services(app)
            assert services is not None
            mission = asyncio.create_task(
                services.runtime.run(
                    PhoenixRequest(text="Say hello."), OrchestrationPath.DIRECT
                )
            )
            async with asyncio.timeout(5):
                while not fakes.structured.generating:
                    await asyncio.sleep(0.01)
            assert len(services.runtime.active_request_ids) == 1
        result = await asyncio.wait_for(mission, 5)
        return result.kind

    assert asyncio.run(scenario()) == MissionKind.CANCELLED


def test_default_factory_builds_ollama_pair_without_io() -> None:
    config = ServerConfig(ollama_base_url="http://127.0.0.1:11434")
    pair = ollama_providers(config)
    try:
        assert len(pair.providers) == len(pair.structured) == 1
        default, structured = pair.providers[0], pair.structured[0]
        assert isinstance(default, OllamaProvider)
        assert isinstance(structured, OllamaProvider)
        assert default.provider_id == structured.provider_id == "ollama"
        assert getattr(default, "_think") is None
        assert getattr(structured, "_think") is False
        assert str(default.base_url) == "http://127.0.0.1:11434/"
    finally:

        async def close() -> None:
            for provider in (*pair.providers, *pair.structured):
                assert isinstance(provider, OllamaProvider)
                await provider.aclose()

        asyncio.run(close())


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "::", "localhost", "192.168.1.20", "127.0.0.2", ""]
)
def test_non_loopback_host_is_refused_before_binding(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_bind(*args: object) -> socket.socket:
        raise AssertionError("bound despite refusal")

    monkeypatch.setattr(launcher, "bind_loopback", refuse_bind)
    out, err = io.StringIO(), io.StringIO()
    assert main(["--host", host], stdout=out, stderr=err) == 2
    assert out.getvalue() == ""
    assert "loopback only" in err.getvalue()


@pytest.mark.parametrize("port", ["-1", "65536", "http", "8000.5"])
def test_invalid_port_is_a_usage_error(port: str) -> None:
    with pytest.raises(SystemExit) as caught:
        parse_options(["--port", port])
    assert caught.value.code == 2


def test_defaults_are_loopback_and_os_assigned_port() -> None:
    options = parse_options([])
    assert options == LaunchOptions(host="127.0.0.1", port=0, data_dir=None)
    assert parse_options(["--host", "::1", "--port", "0"]).host == "::1"


MIGRATE_SCRIPT = """
import pathlib, sys, time
from phoenix.api.server import ServerConfig, migrate_to_head
from phoenix.core.config import AppSettings
from phoenix.persistence.database import create_engine_for_settings
data, go = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
print("waiting", flush=True)
deadline = time.monotonic() + 30
while not go.exists() and time.monotonic() < deadline:
    time.sleep(0.005)
settings = AppSettings(data_dir=data, _env_file=None)
print(",".join(migrate_to_head(settings, ServerConfig())), flush=True)
"""


def test_concurrent_process_migrations_are_serialized(
    settings: AppSettings, tmp_path: Path
) -> None:
    # SQLite DDL is not transactional: unlocked racers fail ("already exists").
    go = tmp_path / "go"
    children = [
        subprocess.Popen(
            [sys.executable, "-c", MIGRATE_SCRIPT, str(settings.data_dir), str(go)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    try:
        for child in children:
            assert child.stdout is not None
            assert child.stdout.readline().strip() == "waiting"
        go.touch()
        outcomes = [child.communicate(timeout=120) for child in children]
    finally:
        for child in children:
            if child.poll() is None:  # pragma: no cover - only on failure
                child.kill()
    for child, (out, err) in zip(children, outcomes, strict=True):
        assert child.returncode == 0, err
        assert out.strip() == HEAD
    with services_engine(settings) as engine:
        assert {"memories", "audit_events"} <= set(inspect(engine).get_table_names())


def test_migration_lock_wait_is_bounded_and_explicit(settings: AppSettings) -> None:
    settings.data_dir.mkdir(parents=True)
    config = ServerConfig(migration_lock_timeout_seconds=0.2)
    with migration_lock(settings.data_dir, 1):
        started = time.monotonic()
        with pytest.raises(MigrationLockTimeout, match="migration lock"):
            migrate_to_head(settings, config)
        assert time.monotonic() - started < 5
    assert not (settings.data_dir / "phoenix.db").exists()
    assert migrate_to_head(settings, config) == (HEAD,)  # released


def test_interactive_docs_and_schema_are_removed(settings: AppSettings) -> None:
    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=factory()
    )
    assert app.openapi_url is None and app.docs_url is None and app.redoc_url is None
    with client_for(app) as client:
        for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
            assert client.get(path).status_code == 404
        assert client.get("/ready").status_code == 200


def test_ready_keeps_one_database_probe_in_flight(
    settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import phoenix.api.server as server_module

    release = threading.Event()
    calls: list[int] = []
    real = server_module.database_at_head

    def hung(*args: object) -> bool:
        calls.append(1)
        release.wait(10)
        return real(*args)  # type: ignore[arg-type]

    app = build_server_app(
        settings, security=SECURITY, config=FAST, provider_factory=factory()
    )
    try:
        with client_for(app) as client:
            monkeypatch.setattr(server_module, "database_at_head", hung)
            for _ in range(4):
                response = client.get("/ready")
                assert response.status_code == 503
                assert response.json()["database"] == {"at_head": False}
            assert len(calls) == 1  # later calls share the hung probe
            release.set()
            deadline = time.monotonic() + 5
            while client.get("/ready").status_code != 200:
                assert time.monotonic() < deadline
            assert len(calls) <= 2  # the hung probe's result may be shared
            settled = len(calls)
            assert client.get("/ready").status_code == 200
            assert len(calls) == settled + 1  # a finished probe is replaced
    finally:
        release.set()


def test_shutdown_drains_even_if_mission_cancel_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drained: list[bool] = []

    async def failing_cancel(services: object) -> int:
        raise RuntimeError("cancel failed")

    async def drain(self: uvicorn.Server, sockets: object = None) -> None:
        drained.append(True)

    monkeypatch.setattr(launcher, "get_services", lambda app: object())
    monkeypatch.setattr(launcher, "cancel_active_missions", failing_cancel)
    monkeypatch.setattr(uvicorn.Server, "shutdown", drain)
    app = FastAPI()
    server = PhoenixServer(
        uvicorn.Config(app), app=app, host="127.0.0.1", stdout=io.StringIO()
    )
    with pytest.raises(RuntimeError, match="cancel failed"):
        asyncio.run(server.shutdown())
    assert drained == [True]


@pytest.fixture
def serve_once(settings: AppSettings) -> Iterator[tuple[io.StringIO, Factory]]:
    out = io.StringIO()
    fakes = factory()

    async def scenario() -> None:
        server, sock = create_server(
            LaunchOptions(host="127.0.0.1", port=0, data_dir=settings.data_dir),
            settings=settings,
            token=TOKEN,
            stdout=out,
            config=FAST,
            provider_factory=fakes,
        )
        try:
            task = asyncio.create_task(server.serve(sockets=[sock]))
            async with asyncio.timeout(10):
                while not server.started:
                    await asyncio.sleep(0.01)
            assert server.announced_port == sock.getsockname()[1]
            server.should_exit = True
            await asyncio.wait_for(task, 15)
        finally:
            sock.close()

    asyncio.run(scenario())
    yield out, fakes


def test_port_zero_yields_real_port_in_single_ready_line(
    serve_once: tuple[io.StringIO, Factory],
) -> None:
    out, fakes = serve_once
    lines = out.getvalue().splitlines()
    assert len(lines) == 1
    ready = json.loads(lines[0])
    assert set(ready) == {"event", "host", "port", "pid", "version"}
    assert ready["event"] == "phoenix.backend.ready"
    assert ready["host"] == "127.0.0.1"
    assert isinstance(ready["port"], int) and 0 < ready["port"] <= 65535
    assert ready["pid"] == os.getpid()
    assert ready["version"] == version("phoenix")
    assert fakes.calls == 1
    assert fakes.default.closed == fakes.structured.closed == 1
