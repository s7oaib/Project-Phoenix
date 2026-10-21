"""PHX-108 first-run setup and capability flags (ADR-110, ADR-112).

``GET /onboarding`` is the setup check the desktop's first-run screen and
Onboarding view show (``phoenix.api.onboarding``). It is also the user's
"re-check": before answering, any tier model that is still missing is
registered if its provider now answers (``ModelRegistrar``, bounded), so a
runtime started after PHOENIX is picked up without a restart.

``GET /capabilities`` returns the fixed capability flags
(``phoenix.api.capabilities``). Both are read-only and return statuses, IDs,
the data directory path and fixed hints only.
"""

import asyncio

from fastapi import APIRouter, Request

from phoenix.api.capabilities import CAPABILITY_FLAGS, CapabilityId
from phoenix.api.onboarding import (
    REQUIRED_MODELS,
    display_name,
    free_bytes,
    install_command,
    runtime_state,
)
from phoenix.api.routes.common import options
from phoenix.api.routes.common import services as backend
from phoenix.api.routes.schemas import (
    CapabilitiesOut,
    CapabilityOut,
    ModelCheck,
    OnboardingOut,
    RuntimeCheck,
    StorageCheck,
    WorkspaceOut,
)
from phoenix.api.server import probe_provider, tier_models
from phoenix.api.workspace import WorkspaceStatus
from phoenix.models.provider import ProviderHealthStatus

router = APIRouter()


@router.get("/onboarding", response_model=OnboardingOut)
async def get_onboarding(request: Request) -> OnboardingOut:
    services = backend(request)
    timeout = options(request).probe_timeout_seconds
    await services.model_registrar.refresh()  # bounded per provider call
    healths = await asyncio.gather(
        *(probe_provider(provider, timeout) for provider in services.providers)
    )
    runtimes = tuple(
        RuntimeCheck(
            provider_id=provider.provider_id,
            display_name=display_name(provider.provider_id),
            state=runtime_state(provider.provider_id, health),
        )
        for provider, health in zip(services.providers, healths, strict=True)
    )
    models = []
    for provider_id, model_id in tier_models(services.tier_table):
        registered = services.capability_registry.get(provider_id, model_id) is not None
        models.append(
            ModelCheck(
                provider_id=provider_id,
                model_id=model_id,
                required=(provider_id, model_id) in REQUIRED_MODELS,
                registered=registered,
                install_command=None
                if registered
                else install_command(provider_id, model_id),
            )
        )
    data_dir = services.settings.data_dir
    storage = StorageCheck(
        data_dir=str(data_dir),
        free_bytes=await asyncio.to_thread(free_bytes, data_dir),
        database_ready=await services.database_probe.at_head(timeout),
    )
    workspace = services.workspace.state
    complete = (
        bool(healths)
        and all(health == ProviderHealthStatus.HEALTHY for health in healths)
        and all(model.registered for model in models if model.required)
        and storage.database_ready
        and workspace.status == WorkspaceStatus.ACTIVE
    )
    return OnboardingOut(
        complete=complete,
        runtimes=runtimes,
        models=tuple(models),
        storage=storage,
        workspace=WorkspaceOut(
            root=workspace.root,
            status=workspace.status,
            updated_at=workspace.updated_at,
        ),
    )


@router.get("/capabilities", response_model=CapabilitiesOut)
async def get_capabilities(request: Request) -> CapabilitiesOut:
    services = backend(request)  # 503 while the backend is starting or stopping
    return CapabilitiesOut(
        capabilities=tuple(
            CapabilityOut(
                capability_id=capability_id,
                # PHX-120 (ADR-161): web research also needs the launch opt-in.
                enabled=flag.enabled
                and (
                    capability_id != CapabilityId.WEB_RESEARCH
                    or services.settings.web_research_enabled
                ),
                ticket=flag.ticket,
                agent_id=flag.agent_id,
            )
            for capability_id, flag in CAPABILITY_FLAGS.items()
        )
    )
