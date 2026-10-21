"""PHX-104 agents, models and settings (ADR-089).

The document workspace root and (PHX-108, ADR-111) the default mission path
are writable (``PATCH /settings``); model tiers, the API address and
retention are read-only in v1. ``GET /models`` reports what the backend
already knows: the tier-table placement, whether each model is registered
(PHX-108 registers late ones first), the provider's health from the same
bounded probe as ``/ready``, and whether the model holds the generation lease
now.
"""

import asyncio
from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Request

from phoenix.agents.runtime import ModelCandidateRef
from phoenix.api.missions import DEFAULT_RETENTION
from phoenix.api.routes.common import options
from phoenix.api.routes.common import services as backend
from phoenix.api.routes.schemas import (
    AgentOut,
    ApiAddress,
    MissionPathChoice,
    ModelOut,
    ModelRef,
    SettingsOut,
    SettingsPatch,
    WorkspaceOut,
)
from phoenix.api.security import STATE_KEY as SECURITY_KEY
from phoenix.api.security import LocalApiSecurity
from phoenix.api.server import BackendServices, probe_provider, tier_models
from phoenix.api.workspace import WorkspaceService
from phoenix.models.provider import ProviderHealthStatus

router = APIRouter()


@router.get("/agents", response_model=tuple[AgentOut, ...])
async def list_agents(request: Request) -> tuple[AgentOut, ...]:
    agents = backend(request).agent_registry.agents
    return tuple(
        AgentOut(
            agent_id=agent.internal_id, alias=agent.alias, role=agent.professional_role
        )
        for agent in agents
    )


@router.get("/models", response_model=tuple[ModelOut, ...])
async def list_models(request: Request) -> tuple[ModelOut, ...]:
    services = backend(request)
    timeout = options(request).probe_timeout_seconds
    await services.model_registrar.refresh()  # PHX-108: late provider start
    statuses = dict(
        zip(
            (provider.provider_id for provider in services.providers),
            await asyncio.gather(
                *(probe_provider(p, timeout) for p in services.providers)
            ),
            strict=True,
        )
    )
    table = services.tier_table
    placements: dict[tuple[str, str], list[str]] = {}
    for role, refs in (*table.roles.items(), ("default", table.default)):
        for ref in refs:
            placements.setdefault((ref.provider_id, ref.model_id), []).append(role)
    leased = {
        (lease.provider_id, lease.model_id)
        for lease in services.lifecycle.active_leases()
    }
    models: list[ModelOut] = []
    for provider_id, model_id in tier_models(table):
        record = services.capability_registry.get(provider_id, model_id)
        models.append(
            ModelOut(
                provider_id=provider_id,
                model_id=model_id,
                roles=tuple(placements[(provider_id, model_id)]),
                registered=record is not None,
                provider_status=statuses.get(
                    provider_id, ProviderHealthStatus.UNAVAILABLE
                ),
                generating=(provider_id, model_id) in leased,
                context_limit_tokens=None
                if record is None
                else record.capabilities.context_limit_tokens,
            )
        )
    return tuple(models)


async def _settings(services: BackendServices, request: Request) -> SettingsOut:
    security: LocalApiSecurity = getattr(request.app.state, SECURITY_KEY)
    stored = await asyncio.to_thread(services.settings_store.get_default_mission_path)
    workspace = services.workspace.state
    table = services.tier_table

    def refs(values: tuple[ModelCandidateRef, ...]) -> tuple[ModelRef, ...]:
        return tuple(
            ModelRef(provider_id=ref.provider_id, model_id=ref.model_id)
            for ref in values
        )

    return SettingsOut(
        workspace=WorkspaceOut(
            root=workspace.root,
            status=workspace.status,
            updated_at=workspace.updated_at,
        ),
        default_mission_path=cast(
            MissionPathChoice, "auto" if stored is None else stored.value
        ),
        model_tiers={role: refs(values) for role, values in table.roles.items()},
        default_tier=refs(table.default),
        retention_days=DEFAULT_RETENTION.days,
        api=ApiAddress(host=security.host, port=security.port),
    )


@router.get("/settings", response_model=SettingsOut)
async def get_settings(request: Request) -> SettingsOut:
    return await _settings(backend(request), request)


@router.patch("/settings", response_model=SettingsOut)
async def patch_settings(body: SettingsPatch, request: Request) -> SettingsOut:
    """The workspace is validated and applied first; if it is refused, the
    mission path is not saved either."""
    services = backend(request)
    workspace: WorkspaceService = services.workspace
    if body.workspace_root is not None:
        await workspace.set_root(body.workspace_root)
    if body.default_mission_path is not None:
        await asyncio.to_thread(
            services.settings_store.set_default_mission_path,
            body.default_mission_path,
            datetime.now(UTC),
        )
    return await _settings(services, request)
