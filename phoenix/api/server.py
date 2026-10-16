"""PHOENIX backend composition root (PHX-100, ADR-084).

build_server_app() wraps the transport from create_app() with a lifespan that
migrates SQLite to head, builds every backend service exactly once, keeps them in
one typed container on ``app.state.phoenix`` and closes them on exit. The only
route added is ``GET /ready``. Readiness probes are bounded; a failing provider
reports as unavailable and the backend as degraded, never as a crash (INV-021,
INV-022). No filesystem workspace is configured here (PHX-108).
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from phoenix.agents.registry import AgentRegistry, build_default_agent_registry
from phoenix.agents.runtime import ModelTierTable, PhoenixRuntime, default_tier_table
from phoenix.api.app import create_app
from phoenix.core.config import AppSettings
from phoenix.core.event_bus import EventBus
from phoenix.models.lifecycle import ModelLifecycleManager
from phoenix.models.ollama import OllamaProvider
from phoenix.models.provider import (
    ModelProvider,
    ModelProviderError,
    ProviderHealthStatus,
)
from phoenix.models.registry import ModelCapabilityRegistry
from phoenix.persistence.database import (
    create_engine_for_settings,
    create_session_factory,
    database_url,
    prepare_data_directory,
)
from phoenix.security.audit import AuditTrail
from phoenix.security.permissions import PermissionPolicy
from phoenix.tools.filesystem import filesystem_tool_definitions
from phoenix.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
STATE_KEY = "phoenix"


class ServerConfig(BaseModel):
    """Backend options AppSettings cannot hold (it is frozen); PHX-108 exposes them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ollama_base_url: str = "http://127.0.0.1:11434"
    probe_timeout_seconds: float = Field(default=2.0, gt=0, le=30, allow_inf_nan=False)
    startup_timeout_seconds: float = Field(
        default=10.0, gt=0, le=120, allow_inf_nan=False
    )
    alembic_ini: Path = ALEMBIC_INI


@dataclass(frozen=True)
class ProviderSet:
    """Default providers plus optional structured (thinking-off) twins (ADR-080)."""

    providers: tuple[ModelProvider, ...]
    structured: tuple[ModelProvider, ...] = ()


ProviderFactory = Callable[[ServerConfig], ProviderSet]


def ollama_providers(config: ServerConfig) -> ProviderSet:
    """Local Ollama, default and think=False; construction performs no I/O."""
    return ProviderSet(
        providers=(OllamaProvider(config.ollama_base_url),),
        structured=(OllamaProvider(config.ollama_base_url, think=False),),
    )


@dataclass(frozen=True)
class BackendServices:
    """Every service the backend owns; built once per lifespan."""

    settings: AppSettings
    engine: Engine
    session_factory: sessionmaker[Session]
    migration_heads: tuple[str, ...]
    agent_registry: AgentRegistry
    tier_table: ModelTierTable
    capability_registry: ModelCapabilityRegistry
    lifecycle: ModelLifecycleManager
    providers: tuple[ModelProvider, ...]
    structured_providers: tuple[ModelProvider, ...]
    event_bus: EventBus
    tool_registry: ToolRegistry
    permission_policy: PermissionPolicy
    audit_trail: AuditTrail
    runtime: PhoenixRuntime


class ReadinessStatus(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ProviderReadiness(_Contract):
    provider_id: str
    status: ProviderHealthStatus


class ModelReadiness(_Contract):
    provider_id: str
    model_id: str


class DatabaseReadiness(_Contract):
    at_head: bool


class ReadinessResponse(_Contract):
    """IDs and statuses only: no URLs, versions, details or secrets."""

    status: ReadinessStatus
    service: Literal["phoenix-api"]
    version: str = Field(min_length=1)
    providers: tuple[ProviderReadiness, ...]
    models: tuple[ModelReadiness, ...]
    database: DatabaseReadiness


def tier_models(table: ModelTierTable) -> tuple[tuple[str, str], ...]:
    """Distinct (provider, model) pairs in tier-table order."""
    refs = [ref for refs in table.roles.values() for ref in refs]
    refs += list(table.default)
    return tuple(dict.fromkeys((ref.provider_id, ref.model_id) for ref in refs))


def migration_config(settings: AppSettings, config: ServerConfig) -> Config:
    alembic = Config(str(config.alembic_ini))
    # ConfigParser interpolation treats % specially; tests do the same escape.
    alembic.set_main_option(
        "sqlalchemy.url", str(database_url(settings)).replace("%", "%%")
    )
    return alembic


def migrate_to_head(settings: AppSettings, config: ServerConfig) -> tuple[str, ...]:
    """Create the data directory, upgrade to head and return the head revisions."""
    prepare_data_directory(settings)
    alembic = migration_config(settings, config)
    command.upgrade(alembic, "head")
    return tuple(sorted(ScriptDirectory.from_config(alembic).get_heads()))


def database_at_head(engine: Engine, heads: tuple[str, ...]) -> bool:
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_heads()
    return bool(heads) and tuple(sorted(current)) == heads


async def probe_provider(
    provider: ModelProvider, timeout: float
) -> ProviderHealthStatus:
    """Bounded health probe; any error, mismatch or timeout is UNAVAILABLE.

    Readiness must report a broken provider, not crash on it (INV-021/022).
    """
    try:
        async with asyncio.timeout(timeout):
            report = await provider.health()
    except Exception:
        return ProviderHealthStatus.UNAVAILABLE
    if report.provider_id != provider.provider_id:
        return ProviderHealthStatus.UNAVAILABLE
    return report.status


async def register_tier_models(
    providers: tuple[ModelProvider, ...],
    table: ModelTierTable,
    registry: ModelCapabilityRegistry,
    timeout: float,
) -> None:
    """Register installed tier models of healthy providers; skip the rest."""
    wanted = tier_models(table)
    for provider in providers:
        if await probe_provider(provider, timeout) != ProviderHealthStatus.HEALTHY:
            continue
        for provider_id, model_id in wanted:
            if provider_id != provider.provider_id:
                continue
            try:
                async with asyncio.timeout(timeout):
                    capabilities = await provider.capabilities(model_id)
            except (ModelProviderError, TimeoutError):
                continue  # not installed or not answering; tiers fall back
            if (capabilities.provider_id, capabilities.model_id) == (
                provider_id,
                model_id,
            ):
                registry.register(capabilities)


async def _close_provider(provider: ModelProvider) -> None:
    close = getattr(provider, "aclose", None)
    if callable(close):
        await close()


async def cancel_active_missions(services: BackendServices) -> int:
    """Request cooperative cancellation of every active mission (INV-019)."""
    request_ids = services.runtime.active_request_ids
    for request_id in request_ids:
        await services.runtime.cancel(request_id)
    return len(request_ids)


@asynccontextmanager
async def open_services(
    settings: AppSettings, config: ServerConfig, factory: ProviderFactory
) -> AsyncIterator[BackendServices]:
    """Build the services once; on exit (or failed startup) close what was opened."""
    async with AsyncExitStack() as stack:
        heads = await asyncio.to_thread(migrate_to_head, settings, config)
        engine = create_engine_for_settings(settings)
        stack.callback(engine.dispose)
        provider_set = factory(config)
        for provider in (*provider_set.providers, *provider_set.structured):
            stack.push_async_callback(_close_provider, provider)
        agents = build_default_agent_registry()
        table = default_tier_table()
        capabilities = ModelCapabilityRegistry()
        try:
            async with asyncio.timeout(config.startup_timeout_seconds):
                await register_tier_models(
                    provider_set.providers,
                    table,
                    capabilities,
                    config.probe_timeout_seconds,
                )
        except TimeoutError:
            pass  # keep what registered; readiness reports the gap (INV-022)
        lifecycle = ModelLifecycleManager()
        session_factory = create_session_factory(engine)
        tools = ToolRegistry(filesystem_tool_definitions())
        services = BackendServices(
            settings=settings,
            engine=engine,
            session_factory=session_factory,
            migration_heads=heads,
            agent_registry=agents,
            tier_table=table,
            capability_registry=capabilities,
            lifecycle=lifecycle,
            providers=provider_set.providers,
            structured_providers=provider_set.structured,
            event_bus=EventBus(),
            tool_registry=tools,
            permission_policy=PermissionPolicy(),
            audit_trail=AuditTrail(session_factory, tools),
            runtime=PhoenixRuntime(
                providers=provider_set.providers,
                structured_providers=provider_set.structured,
                capability_registry=capabilities,
                lifecycle=lifecycle,
                agent_registry=agents,
                tier_table=table,
            ),
        )
        try:
            yield services
        finally:
            await cancel_active_missions(services)


def get_services(app: FastAPI) -> BackendServices | None:
    services = getattr(app.state, STATE_KEY, None)
    return services if isinstance(services, BackendServices) else None


async def readiness(
    services: BackendServices | None, version: str, timeout: float
) -> ReadinessResponse:
    if services is None:
        return ReadinessResponse(
            status=ReadinessStatus.UNAVAILABLE,
            service="phoenix-api",
            version=version,
            providers=(),
            models=(),
            database=DatabaseReadiness(at_head=False),
        )
    statuses = await asyncio.gather(
        *(probe_provider(provider, timeout) for provider in services.providers)
    )
    try:
        async with asyncio.timeout(timeout):
            at_head = await asyncio.to_thread(
                database_at_head, services.engine, services.migration_heads
            )
    except Exception:
        at_head = False  # any database failure is reported, never raised
    models = tuple(
        ModelReadiness(provider_id=record.provider_id, model_id=record.model_id)
        for record in services.capability_registry.list_records()
    )
    if not at_head:
        status = ReadinessStatus.UNAVAILABLE
    elif not models or any(s != ProviderHealthStatus.HEALTHY for s in statuses):
        status = ReadinessStatus.DEGRADED
    else:
        status = ReadinessStatus.READY
    return ReadinessResponse(
        status=status,
        service="phoenix-api",
        version=version,
        providers=tuple(
            ProviderReadiness(provider_id=provider.provider_id, status=health)
            for provider, health in zip(services.providers, statuses, strict=True)
        ),
        models=models,
        database=DatabaseReadiness(at_head=at_head),
    )


def build_server_app(
    settings: AppSettings,
    *,
    config: ServerConfig | None = None,
    provider_factory: ProviderFactory | None = None,
) -> FastAPI:
    """create_app() plus the owning lifespan and GET /ready; no other routes."""
    options = ServerConfig() if config is None else config
    factory = ollama_providers if provider_factory is None else provider_factory
    app = create_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_services(settings, options, factory) as services:
            setattr(app.state, STATE_KEY, services)
            try:
                yield
            finally:
                setattr(app.state, STATE_KEY, None)

    app.router.lifespan_context = lifespan

    @app.get("/ready", response_model=ReadinessResponse)
    async def ready(request: Request) -> JSONResponse:
        report = await readiness(
            get_services(request.app), app.version, options.probe_timeout_seconds
        )
        code = 503 if report.status == ReadinessStatus.UNAVAILABLE else 200
        return JSONResponse(report.model_dump(mode="json"), status_code=code)

    return app
