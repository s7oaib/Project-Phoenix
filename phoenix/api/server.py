"""PHOENIX backend composition root (PHX-100, ADR-084).

build_server_app() wraps the transport from create_app() with a lifespan that
migrates SQLite to head, builds every backend service exactly once, keeps them in
one typed container on ``app.state.phoenix`` and closes them on exit. The routes
added are ``GET /ready``, ``WS /events`` and the PHX-104 REST routes, and every
route requires local API security (PHX-101); the interactive docs and OpenAPI
schema are removed. Migrations run
under an exclusive, bounded data-directory lock. Readiness probes are bounded,
with at most one database check in flight; a failing provider reports as
unavailable and the backend as degraded, never as a crash (INV-021, INV-022).
PHX-102 (ADR-086) adds the
persisted MissionService: it starts with the lifespan, and the shutdown path stops
it, so queued and running missions are recorded as interrupted. PHX-103 (ADR-087)
adds ``WS /events``: the runtime and the mission service publish content-free
events to the EventStream, which fans them out to authenticated WebSocket
clients with a bounded buffer each (a slow client is closed with 1013) and
closes every connection when the backend stops. PHX-104 (ADR-089) adds the REST
API v1 routes (``phoenix.api.routes``) and the document workspace: the stored
root is re-validated at startup, and setting it builds a FilesystemTool and a
runtime that missions starting afterwards use (``WorkspaceService``).
"""

import asyncio
import sys
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO, Literal

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from phoenix.agents.registry import AgentRegistry, build_default_agent_registry
from phoenix.agents.runtime import ModelTierTable, PhoenixRuntime, default_tier_table
from phoenix.api.app import create_app
from phoenix.api.body_limit import BodyLimitMiddleware
from phoenix.api.event_stream import (
    DEFAULT_CLIENT_BUFFER,
    DEFAULT_MAX_CONNECTIONS,
    EVENT_BUS_CAPACITY,
    EVENTS_PATH,
    WS_TRY_AGAIN_LATER,
    EventStream,
)
from phoenix.api.missions import MissionService
from phoenix.api.security import (
    LocalApiSecurity,
    accept_websocket,
    error_response,
    install_local_api_security,
)
from phoenix.api.workspace import WorkspaceService
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
from phoenix.persistence.missions import MissionStore
from phoenix.persistence.settings import SettingsStore
from phoenix.security.audit import AuditTrail
from phoenix.security.permissions import PermissionPolicy
from phoenix.tools.filesystem import FilesystemTool, filesystem_tool_definitions
from phoenix.tools.registry import ToolRegistry

if sys.platform == "win32":
    import msvcrt

    def _try_lock(handle: IO[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock(handle: IO[bytes]) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(handle: IO[bytes]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
STATE_KEY = "phoenix"
MIGRATION_LOCK_NAME = "phoenix.migrate.lock"
_LOCK_POLL_SECONDS = 0.05


class ServerConfig(BaseModel):
    """Backend options AppSettings cannot hold (it is frozen); PHX-108 exposes them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ollama_base_url: str = "http://127.0.0.1:11434"
    probe_timeout_seconds: float = Field(default=2.0, gt=0, le=30, allow_inf_nan=False)
    startup_timeout_seconds: float = Field(
        default=10.0, gt=0, le=120, allow_inf_nan=False
    )
    migration_lock_timeout_seconds: float = Field(
        default=30.0, gt=0, le=600, allow_inf_nan=False
    )
    # PHX-103: events buffered per WebSocket client before it counts as slow.
    event_client_buffer: int = Field(
        default=DEFAULT_CLIENT_BUFFER, strict=True, ge=1, le=4096
    )
    # PHX-103: concurrent WS /events connections; more are refused with 503.
    event_max_connections: int = Field(
        default=DEFAULT_MAX_CONNECTIONS, strict=True, ge=1, le=256
    )
    alembic_ini: Path = ALEMBIC_INI


class MigrationLockTimeout(TimeoutError):
    """Another process held the migration lock for the whole bounded wait."""


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


def _consume(task: "asyncio.Task[bool]") -> None:
    if not task.cancelled():
        task.exception()  # retrieved: a late failure is not an unhandled error


class DatabaseProbe:
    """At most one database check in flight.

    A timed-out ``to_thread`` worker keeps running, so a hung check is shared
    by later readiness calls rather than started again; the executor cannot
    fill up while the database hangs.
    """

    def __init__(self, engine: Engine, heads: tuple[str, ...]) -> None:
        self._engine = engine
        self._heads = heads
        self._task: asyncio.Task[bool] | None = None

    async def at_head(self, timeout: float) -> bool:
        task = self._task
        if task is None or task.done():
            task = asyncio.create_task(
                asyncio.to_thread(database_at_head, self._engine, self._heads)
            )
            task.add_done_callback(_consume)
            self._task = task
        try:
            async with asyncio.timeout(timeout):
                return await asyncio.shield(task)
        except Exception:
            return False  # any database failure or timeout is reported, not raised


@dataclass(frozen=True)
class BackendServices:
    """Every service the backend owns; built once per lifespan."""

    settings: AppSettings
    engine: Engine
    session_factory: sessionmaker[Session]
    migration_heads: tuple[str, ...]
    database_probe: DatabaseProbe
    agent_registry: AgentRegistry
    tier_table: ModelTierTable
    capability_registry: ModelCapabilityRegistry
    lifecycle: ModelLifecycleManager
    providers: tuple[ModelProvider, ...]
    structured_providers: tuple[ModelProvider, ...]
    event_bus: EventBus
    event_stream: EventStream
    tool_registry: ToolRegistry
    permission_policy: PermissionPolicy
    audit_trail: AuditTrail
    runtime: PhoenixRuntime  # the runtime built at startup (no workspace)
    missions: MissionService
    settings_store: SettingsStore
    workspace: WorkspaceService


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
    """Statuses and IDs plus the PHOENIX version only.

    Provider URLs, provider versions, health details, paths and secrets are
    never echoed.
    """

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


@contextmanager
def migration_lock(data_dir: Path, timeout: float) -> Iterator[None]:
    """OS-level exclusive lock in the data directory, waited for at most timeout.

    SQLite DDL is not transactional, so concurrent upgrades could interleave and
    leave a half-applied schema. Waiting is bounded and fails explicitly.
    """
    deadline = time.monotonic() + timeout
    with open(data_dir / MIGRATION_LOCK_NAME, "a+b") as handle:
        while True:
            try:
                _try_lock(handle)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise MigrationLockTimeout(
                        "Another PHOENIX process holds the migration lock;"
                        f" waited {timeout:g} s"
                    ) from None
                time.sleep(_LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(handle)


def migrate_to_head(settings: AppSettings, config: ServerConfig) -> tuple[str, ...]:
    """Create the data directory, upgrade to head under the lock, return heads."""
    prepare_data_directory(settings)
    alembic = migration_config(settings, config)
    with migration_lock(settings.data_dir, config.migration_lock_timeout_seconds):
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
    """Request cooperative cancellation of every active mission (INV-019).

    The mission service stops first (PHX-102): queued missions and the running
    one are recorded failed/interrupted. Any other runtime mission is cancelled.
    """
    await services.missions.stop()
    cancelled = 0
    # PHX-104: after a workspace change missions run on a newer runtime.
    for runtime in dict.fromkeys((services.runtime, services.missions.runtime)):
        request_ids = runtime.active_request_ids
        for request_id in request_ids:
            await runtime.cancel(request_id)
        cancelled += len(request_ids)
    return cancelled


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
        bus = EventBus(capacity=EVENT_BUS_CAPACITY)
        stream = EventStream(
            bus,
            client_buffer=config.event_client_buffer,
            max_connections=config.event_max_connections,
        )
        policy = PermissionPolicy()
        audit = AuditTrail(session_factory, tools)

        def build_runtime(workspace_root: str | None = None) -> PhoenixRuntime:
            """PHX-104: one runtime per workspace root; None has no documents."""
            return PhoenixRuntime(
                providers=provider_set.providers,
                structured_providers=provider_set.structured,
                capability_registry=capabilities,
                lifecycle=lifecycle,
                agent_registry=agents,
                tier_table=table,
                filesystem_tool=None
                if workspace_root is None
                else FilesystemTool(workspace_root, tools, policy, audit),
                events=stream,
            )

        runtime = build_runtime()
        missions = MissionService(
            runtime=runtime,
            store=MissionStore(session_factory),
            tool_registry=tools,
            events=stream,
        )
        settings_store = SettingsStore(session_factory)
        services = BackendServices(
            settings=settings,
            engine=engine,
            session_factory=session_factory,
            migration_heads=heads,
            database_probe=DatabaseProbe(engine, heads),
            agent_registry=agents,
            tier_table=table,
            capability_registry=capabilities,
            lifecycle=lifecycle,
            providers=provider_set.providers,
            structured_providers=provider_set.structured,
            event_bus=bus,
            event_stream=stream,
            tool_registry=tools,
            permission_policy=policy,
            audit_trail=audit,
            runtime=runtime,
            missions=missions,
            settings_store=settings_store,
            workspace=WorkspaceService(
                store=settings_store,
                missions=missions,
                build_runtime=build_runtime,
                data_dir=settings.data_dir,
            ),
        )
        try:
            await services.workspace.load()  # before the first mission runs
            await services.missions.start()
            yield services
        finally:
            try:
                await cancel_active_missions(services)
            finally:
                await stream.close()  # 1001 to every remaining client


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
    at_head = await services.database_probe.at_head(timeout)
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


def _remove_interactive_docs(app: FastAPI) -> None:
    """No /docs, /redoc or /openapi.json on the backend (smaller surface)."""
    hidden = {
        app.openapi_url,
        app.docs_url,
        app.redoc_url,
        app.swagger_ui_oauth2_redirect_url,
    } - {None}
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if getattr(route, "path", None) not in hidden
    ]
    app.openapi_url = app.docs_url = app.redoc_url = None
    app.swagger_ui_oauth2_redirect_url = None


async def _refuse_connection(websocket: WebSocket) -> None:
    """HTTP 503 denial (distinct from auth's 403); 1013 if unsupported."""
    request_id = websocket.scope.get("state", {}).get("request_id", "")
    response = error_response(
        503, "too_many_connections", str(request_id), {"Retry-After": "1"}
    )
    try:
        await websocket.send_denial_response(response)
    except RuntimeError:
        await websocket.close(code=WS_TRY_AGAIN_LATER)


def build_server_app(
    settings: AppSettings,
    *,
    security: LocalApiSecurity,
    config: ServerConfig | None = None,
    provider_factory: ProviderFactory | None = None,
) -> FastAPI:
    """create_app() plus the owning lifespan, GET /ready, WS /events and the
    PHX-104 REST routes.

    Local API security is mandatory (PHX-101): every route, including /health
    and /ready, requires the bound Host, an allowed Origin and the token.
    """
    # The routes import this module (BackendServices, probes), so they are
    # imported here, after it has fully loaded.
    from phoenix.api.routes import install_api_routes

    options = ServerConfig() if config is None else config
    factory = ollama_providers if provider_factory is None else provider_factory
    app = create_app()
    _remove_interactive_docs(app)
    # PHX-104: added first, so it runs inside the security middleware.
    app.add_middleware(BodyLimitMiddleware)
    install_local_api_security(app, security)
    install_api_routes(app, probe_timeout_seconds=options.probe_timeout_seconds)

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

    @app.websocket(EVENTS_PATH)
    async def events(websocket: WebSocket) -> None:
        """PHX-103: live, content-free events; REST stays the source of truth.

        The middleware has already authenticated the handshake. Beyond the
        connection cap the upgrade is refused at the HTTP level (503 with the
        error envelope and Retry-After), before any WebSocket session exists.
        """
        services = get_services(websocket.app)
        stream = None if services is None else services.event_stream
        if stream is not None and not stream.reserve():
            await _refuse_connection(websocket)
            return
        serving = False
        try:
            if not await accept_websocket(websocket):
                return  # closed 1008 before accept (PHX-101)
            if stream is None:
                await websocket.close(code=WS_TRY_AGAIN_LATER)
                return
            serving = True
            await stream.serve(websocket)  # releases the slot when it ends
        finally:
            if stream is not None and not serving:
                stream.release()

    return app
