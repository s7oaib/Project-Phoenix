"""PHX-108 settings, onboarding, capability flags and late model registration.

ADR-110 (tier models register when their provider becomes available), ADR-111
(saved default mission path), ADR-112 (capability flags) and ADR-113 (setup
checks). Real composition root, PHX-101 middleware, MissionService, runtime
and alembic-migrated SQLite; only the provider is a fake, whose health can be
switched to simulate Ollama starting after PHOENIX. No network, Ollama or GPU
is used; an autouse guard fails any httpx send or outbound socket connection.
"""

import asyncio
import os
import secrets
import shutil
import socket
import sqlite3
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import MappingProxyType
from typing import Any

import httpx
import pytest
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient

from alembic import command
from phoenix.agents.runtime import default_tier_table
from phoenix.api import capabilities as capability_module
from phoenix.api import onboarding as onboarding_module
from phoenix.api.capabilities import CAPABILITY_FLAGS, CapabilityFlag, CapabilityId
from phoenix.api.security import LocalApiSecurity
from phoenix.api.server import (
    BackendServices,
    ModelRegistrar,
    ProviderSet,
    ServerConfig,
    build_server_app,
    get_services,
    tier_models,
)
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
    TokenUsage,
)
from phoenix.models.registry import ModelCapabilityRegistry
from phoenix.persistence.database import database_url

TOKEN = secrets.token_urlsafe(32)
PORT = 48767  # Host-header identity for in-process clients; nothing binds it
SECURITY = LocalApiSecurity(TOKEN, host="127.0.0.1", port=PORT)
ROOT = Path(__file__).resolve().parents[1]
ALL_MODELS = ("qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b", "qwen2.5-coder:7b")
# The background loop is effectively off unless a test turns it on.
QUIET = ServerConfig(
    probe_timeout_seconds=0.5,
    startup_timeout_seconds=5,
    model_refresh_seconds=600,
    model_refresh_idle_seconds=3600,
)


class SwitchableProvider(ModelProvider):
    """An Ollama stand-in whose health and installed models can change."""

    def __init__(
        self,
        *,
        status: ProviderHealthStatus = ProviderHealthStatus.UNAVAILABLE,
        models: tuple[str, ...] = ALL_MODELS,
    ) -> None:
        super().__init__("ollama")
        self.status = status
        self.models = models
        self.capability_calls: list[str] = []
        self.health_calls = 0

    async def health(self) -> ProviderHealth:
        self.health_calls += 1
        if self.status == ProviderHealthStatus.UNAVAILABLE:
            raise ModelProviderError("connection refused")
        return ProviderHealth(provider_id=self.provider_id, status=self.status)

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        self.capability_calls.append(model_id)
        if self.status != ProviderHealthStatus.HEALTHY or model_id not in self.models:
            raise ModelProviderError("model not installed")
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
        if self.status != ProviderHealthStatus.HEALTHY:
            raise ModelProviderError("connection refused")
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text="Hello from PHOENIX.",
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


class Backend:
    def __init__(self, base: Path, provider: SwitchableProvider) -> None:
        self.base = base
        self.settings = AppSettings(data_dir=base / "data", _env_file=None)  # type: ignore[call-arg]
        self.workspace = base / "workspace"
        self.workspace.mkdir()
        self.provider = provider

    def app(self, config: ServerConfig = QUIET) -> FastAPI:
        provider = self.provider
        return build_server_app(
            self.settings,
            security=SECURITY,
            config=config,
            provider_factory=lambda _: ProviderSet(providers=(provider,)),
        )

    def client(
        self, config: ServerConfig = QUIET, *, token: str | None = TOKEN
    ) -> TestClient:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        return TestClient(
            self.app(config), base_url=f"http://127.0.0.1:{PORT}", headers=headers
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
def base() -> Iterator[Path]:
    """A directory outside AppData (not an acceptable workspace), canonical."""
    root = Path(os.path.realpath(tempfile.mkdtemp(prefix="phx108-", dir=Path.home())))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def backend(base: Path) -> Backend:
    return Backend(base, SwitchableProvider())


def services_of(client: TestClient) -> BackendServices:
    services = get_services(client.app)  # type: ignore[arg-type]
    assert services is not None
    return services


def wait_settled(client: TestClient, mission_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while True:
        body: dict[str, Any] = client.get(f"/missions/{mission_id}").json()
        if body["status"] in {"completed", "failed", "cancelled"}:
            return body
        assert time.monotonic() < deadline, body["status"]
        time.sleep(0.01)


def registered(client: TestClient) -> list[str]:
    records = services_of(client).capability_registry.list_records()
    return sorted(record.model_id for record in records)


# ---- ADR-110: a provider that starts after PHOENIX


def test_ollama_started_after_phoenix_is_picked_up_by_ready(backend: Backend) -> None:
    with backend.client() as client:
        body = client.get("/ready").json()
        assert body["status"] == "degraded"
        assert body["models"] == []
        backend.provider.status = ProviderHealthStatus.HEALTHY  # Ollama starts
        body = client.get("/ready").json()
        assert body["status"] == "ready"
        assert sorted(m["model_id"] for m in body["models"]) == sorted(ALL_MODELS)


def test_late_start_mission_succeeds_without_restart(backend: Backend) -> None:
    """The owner's rehearsal: before PHX-108 every mission failed
    no_eligible_model until PHOENIX was restarted."""
    with backend.client() as client:
        first = client.post("/chat", json={"text": "Hi", "path": "direct"}).json()
        failed = wait_settled(client, first["mission_id"])
        assert failed["status"] == "failed"  # explicit while Ollama is down
        assert registered(client) == []
        # Ollama starts. No re-check, no /ready and no background tick (QUIET):
        # the next mission registers the tier models before it runs.
        backend.provider.status = ProviderHealthStatus.HEALTHY
        second = client.post("/chat", json={"text": "Hi", "path": "direct"}).json()
        done = wait_settled(client, second["mission_id"])
        assert done["status"] == "completed", done
        assert done["answer_text"] == "Hello from PHOENIX."
        assert registered(client) == sorted(ALL_MODELS)


def test_onboarding_recheck_registers_late_models(backend: Backend) -> None:
    with backend.client() as client:
        assert onboarding(client)["runtimes"][0]["state"] in {
            "not_running",
            "not_detected",
        }
        backend.provider.status = ProviderHealthStatus.HEALTHY
        body = onboarding(client)  # the user's re-check
        assert body["runtimes"][0]["state"] == "running"
        assert all(model["registered"] for model in body["models"])
        assert registered(client) == sorted(ALL_MODELS)


def test_missions_do_not_probe_for_optional_models(base: Path) -> None:
    """Only a missing required model triggers a refresh before a mission."""
    provider = SwitchableProvider(
        status=ProviderHealthStatus.HEALTHY, models=("qwen3.5:4b", "qwen3.5:9b")
    )
    backend = Backend(base, provider)
    with backend.client() as client:
        calls = len(provider.capability_calls)
        for _ in range(2):
            accepted = client.post("/chat", json={"text": "Hi", "path": "direct"})
            done = wait_settled(client, accepted.json()["mission_id"])
            assert done["status"] == "completed"
        assert len(provider.capability_calls) == calls


def test_background_loop_registers_without_any_request(backend: Backend) -> None:
    fast = QUIET.model_copy(
        update={"model_refresh_seconds": 0.05, "model_refresh_idle_seconds": 0.05}
    )
    with backend.client(fast) as client:
        assert registered(client) == []
        backend.provider.status = ProviderHealthStatus.HEALTHY
        deadline = time.monotonic() + 10
        while registered(client) != sorted(ALL_MODELS):
            assert time.monotonic() < deadline
            time.sleep(0.02)


def test_loop_retries_slowly_once_the_provider_answers(base: Path) -> None:
    """Healthy but a tier model not installed: idle interval, not a busy loop;
    registered models are never asked again."""
    provider = SwitchableProvider(
        status=ProviderHealthStatus.HEALTHY, models=("qwen3.5:4b", "qwen3.5:9b")
    )
    backend = Backend(base, provider)
    slow_idle = QUIET.model_copy(
        update={"model_refresh_seconds": 0.02, "model_refresh_idle_seconds": 3600}
    )
    with backend.client(slow_idle) as client:
        assert registered(client) == ["qwen3.5:4b", "qwen3.5:9b"]
        calls = list(provider.capability_calls)
        time.sleep(0.3)  # 15 fast intervals; the idle interval applies instead
        assert provider.capability_calls == calls
        assert set(calls) == set(ALL_MODELS)  # each asked once at startup
        provider.models = ALL_MODELS  # the user pulls the missing models
        assert len(client.get("/ready").json()["models"]) == 4
        assert provider.capability_calls[len(calls) :] == [
            "qwen3.5:2b",
            "qwen2.5-coder:7b",
        ]


def test_refresh_is_single_flight_and_skips_registered_models() -> None:
    async def run() -> None:
        provider = SwitchableProvider(status=ProviderHealthStatus.HEALTHY)
        registry = ModelCapabilityRegistry()
        registrar = ModelRegistrar((provider,), default_tier_table(), registry, 0.5)
        await asyncio.gather(*(registrar.refresh() for _ in range(5)))
        assert registrar.passes == 1
        assert sorted(provider.capability_calls) == sorted(ALL_MODELS)
        assert registrar.missing() == ()
        await registrar.refresh()  # nothing missing: no provider call at all
        assert provider.health_calls == 1
        assert registrar.passes == 1

    asyncio.run(run())


def test_registrar_ignores_other_providers_models() -> None:
    provider = SwitchableProvider()
    registrar = ModelRegistrar(
        (provider,), default_tier_table(), ModelCapabilityRegistry(), 0.5
    )
    assert registrar.missing() == tier_models(default_tier_table())


# ---- ADR-113: onboarding checks


def onboarding(client: TestClient) -> dict[str, Any]:
    response = client.get("/onboarding")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_onboarding_reports_each_step_in_order(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding_module, "runtime_installed", lambda _: False)
    with backend.client() as client:
        body = onboarding(client)
        assert set(body) == {"complete", "runtimes", "models", "storage", "workspace"}
        assert body["complete"] is False
        assert body["runtimes"] == [
            {"provider_id": "ollama", "display_name": "Ollama", "state": "not_detected"}
        ]
        models = {m["model_id"]: m for m in body["models"]}
        assert list(models) == [m for _, m in tier_models(default_tier_table())]
        assert models["qwen3.5:4b"] == {
            "provider_id": "ollama",
            "model_id": "qwen3.5:4b",
            "required": True,
            "registered": False,
            "install_command": "ollama pull qwen3.5:4b",
        }
        assert models["qwen3.5:9b"]["required"] is True
        assert models["qwen3.5:2b"]["required"] is False
        assert models["qwen2.5-coder:7b"]["required"] is False
        storage = body["storage"]
        assert storage["data_dir"] == str(backend.settings.data_dir)
        assert isinstance(storage["free_bytes"], int) and storage["free_bytes"] > 0
        assert storage["database_ready"] is True
        assert body["workspace"] == {
            "root": None,
            "status": "unset",
            "updated_at": None,
        }

        monkeypatch.setattr(onboarding_module, "runtime_installed", lambda _: True)
        assert onboarding(client)["runtimes"][0]["state"] == "not_running"

        backend.provider.status = ProviderHealthStatus.HEALTHY
        body = onboarding(client)
        assert body["runtimes"][0]["state"] == "running"
        assert all(m["registered"] for m in body["models"])
        assert all(m["install_command"] is None for m in body["models"])
        assert body["complete"] is False  # no workspace folder yet

        response = client.patch(
            "/settings", json={"workspace_root": str(backend.workspace)}
        )
        assert response.status_code == 200, response.text
        body = onboarding(client)
        assert body["workspace"]["status"] == "active"
        assert body["complete"] is True


def test_onboarding_is_incomplete_without_a_required_model(base: Path) -> None:
    provider = SwitchableProvider(
        status=ProviderHealthStatus.HEALTHY, models=("qwen3.5:4b", "qwen3.5:2b")
    )
    backend = Backend(base, provider)
    with backend.client() as client:
        assert client.patch(
            "/settings", json={"workspace_root": str(backend.workspace)}
        ).is_success
        body = onboarding(client)
        missing = [m["model_id"] for m in body["models"] if not m["registered"]]
        assert missing == ["qwen3.5:9b", "qwen2.5-coder:7b"]
        assert body["complete"] is False
        provider.models = ("qwen3.5:4b", "qwen3.5:2b", "qwen3.5:9b")
        assert onboarding(client)["complete"] is True  # coder is optional


def test_degraded_runtime_is_reported(backend: Backend) -> None:
    backend.provider.status = ProviderHealthStatus.DEGRADED
    with backend.client() as client:
        assert onboarding(client)["runtimes"][0]["state"] == "degraded"


def test_required_models_are_tier_models() -> None:
    assert set(onboarding_module.REQUIRED_MODELS) <= set(
        tier_models(default_tier_table())
    )
    assert onboarding_module.install_command("colibri", "x") is None


# ---- ADR-112: capability flags


# PHX-120 (ADR-161): memory, web research, media and document generation are
# enabled; web research is reported enabled only with the launch opt-in
# (PHOENIX_WEB_RESEARCH_ENABLED, off in these tests).
EXPECTED_FLAGS = [
    ("workspace_documents", True, "PHX-046", "document_analyst"),
    ("memory", True, "PHX-110", "memory"),
    ("web_research", False, "PHX-111", "web_research"),
    ("computer_control", False, "PHX-112", "computer_control"),
    ("media", True, "PHX-113", "media"),
    ("document_generation", True, "PHX-113", "document_analyst"),
    ("voice", False, "PHX-114", "voice"),
]


def test_capability_flags_are_fixed_and_honest(backend: Backend) -> None:
    with backend.client() as client:
        body = client.get("/capabilities").json()
        assert body == {
            "capabilities": [
                {
                    "capability_id": capability,
                    "enabled": enabled,
                    "ticket": ticket,
                    "agent_id": agent,
                }
                for capability, enabled, ticket, agent in EXPECTED_FLAGS
            ]
        }
        known = {
            agent.internal_id for agent in services_of(client).agent_registry.agents
        }
        assert {flag.agent_id for flag in CAPABILITY_FLAGS.values()} <= known
        for method in ("POST", "PATCH", "PUT", "DELETE"):
            assert client.request(method, "/capabilities", json={}).status_code in {
                404,
                405,
            }


def test_a_later_ticket_only_flips_a_flag(
    backend: Backend, monkeypatch: pytest.MonkeyPatch
) -> None:
    flipped = dict(CAPABILITY_FLAGS)
    flipped[CapabilityId.COMPUTER_CONTROL] = CapabilityFlag(
        enabled=True, ticket="PHX-112", agent_id="computer_control"
    )
    monkeypatch.setattr(
        capability_module, "CAPABILITY_FLAGS", MappingProxyType(flipped)
    )
    from phoenix.api.routes import setup

    monkeypatch.setattr(setup, "CAPABILITY_FLAGS", MappingProxyType(flipped))
    with backend.client() as client:
        flags = {
            item["capability_id"]: item["enabled"]
            for item in client.get("/capabilities").json()["capabilities"]
        }
    assert flags["computer_control"] is True
    assert flags["voice"] is False


def test_web_research_flag_follows_the_launch_opt_in(backend: Backend) -> None:
    # PHX-120 (ADR-161): the flag is on, but the Research view is told it is
    # enabled only while this launch holds the web research tool.
    assert CAPABILITY_FLAGS[CapabilityId.WEB_RESEARCH].enabled is True
    for opted_in in (False, True):
        backend.settings = AppSettings(  # type: ignore[call-arg]
            data_dir=backend.base / "data",
            web_research_enabled=opted_in,
            _env_file=None,
        )
        with backend.client() as client:
            flags = {
                item["capability_id"]: item["enabled"]
                for item in client.get("/capabilities").json()["capabilities"]
            }
            assert (services_of(client).runtime._research is not None) is opted_in
        assert flags["web_research"] is opted_in


def test_new_routes_require_the_token(backend: Backend) -> None:
    with backend.client(token=None) as client:
        for path in ("/onboarding", "/capabilities"):
            for headers in (
                {},
                {"Authorization": f"Bearer {secrets.token_urlsafe(32)}"},
            ):
                response = client.get(path, headers=headers)
                assert response.status_code == 401
                assert response.json()["error"]["code"] == "unauthorized"


def test_new_routes_without_running_services_are_unavailable(
    backend: Backend,
) -> None:
    client = backend.client()  # no context manager: the lifespan never starts
    for path in ("/onboarding", "/capabilities"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "service_unavailable"


def test_onboarding_echoes_no_provider_detail(backend: Backend) -> None:
    with backend.client() as client:
        text = client.get("/onboarding").text
    assert "connection refused" not in text
    assert TOKEN not in text


# ---- ADR-111: saved default mission path


def test_default_mission_path_is_saved_and_survives_restart(backend: Backend) -> None:
    with backend.client() as client:
        assert client.get("/settings").json()["default_mission_path"] == "auto"
        response = client.patch("/settings", json={"default_mission_path": "direct"})
        assert response.status_code == 200, response.text
        assert response.json()["default_mission_path"] == "direct"
        assert response.json()["workspace"]["status"] == "unset"
    with backend.client() as client:
        assert client.get("/settings").json()["default_mission_path"] == "direct"
        both = client.patch(
            "/settings",
            json={
                "workspace_root": str(backend.workspace),
                "default_mission_path": "complex",
            },
        ).json()
        assert both["default_mission_path"] == "complex"
        assert both["workspace"]["status"] == "active"


def test_default_mission_path_rejects_bad_bodies(backend: Backend) -> None:
    with backend.client() as client:
        before = client.get("/settings").json()
        for body in (
            {},
            {"default_mission_path": "fast"},
            {"default_mission_path": None},
            {"default_mission_path": "AUTO"},
            {"default_mission_path": "auto", "retention_days": 1},
        ):
            response = client.patch("/settings", json=body)
            assert response.status_code == 422, body
            assert response.json()["error"]["code"] == "invalid_request"
        # A refused workspace saves nothing, not even a valid mission path.
        response = client.patch(
            "/settings",
            json={
                "workspace_root": str(backend.base / "missing"),
                "default_mission_path": "direct",
            },
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "workspace_not_found"
        assert client.get("/settings").json() == before


def alembic_config(settings: AppSettings) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", str(database_url(settings)).replace("%", "%%")
    )
    return config


def test_migration_0009_constrains_keys_and_values_and_downgrades(
    tmp_path: Path,
) -> None:
    settings = AppSettings(data_dir=tmp_path / "data", _env_file=None)  # type: ignore[call-arg]
    settings.data_dir.mkdir()
    config = alembic_config(settings)
    command.upgrade(config, "head")
    database = settings.data_dir / "phoenix.db"
    with sqlite3.connect(database) as connection:
        insert = "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)"
        connection.execute(insert, ("default_mission_path", "direct", "2026-10-21"))
        connection.execute(insert, ("workspace_root", "D:\\docs", "2026-10-21"))
        for key, value in (
            ("default_mission_path", "fast"),
            ("other", "x"),
            ("default_mission_path", ""),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT OR REPLACE INTO app_settings (key, value, updated_at)"
                    " VALUES (?, ?, ?)",
                    (key, value, "2026-10-21"),
                )
    command.downgrade(config, "0008_mission_triage")
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT key, value FROM app_settings").fetchall()
        assert rows == [("workspace_root", "D:\\docs")]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
                ("default_mission_path", "auto", "2026-10-21"),
            )
    command.upgrade(config, "head")
