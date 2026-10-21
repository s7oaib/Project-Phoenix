"""PHX-104 REST API v1 routes (ADR-089); installed by ``build_server_app``.

Every route sits behind the PHX-101 middleware (Host, Origin, token). The
routes are thin: MissionService and WorkspaceService own the behaviour.
``/files/upload`` and ``/memory/search`` are deferred to PHX-110 and do not
exist. PHX-108 (ADR-110, ADR-112) adds ``GET /onboarding`` and
``GET /capabilities`` (``routes.setup``). PHX-115 (ADR-146) adds
``GET /metrics``.
"""

from fastapi import FastAPI

from phoenix.api.routes import (
    missions,
    observability,
    permissions,
    setup,
    system,
    voice,
)
from phoenix.api.routes.common import OPTIONS_KEY, ApiOptions, install_error_handlers


def install_api_routes(app: FastAPI, *, probe_timeout_seconds: float) -> None:
    setattr(app.state, OPTIONS_KEY, ApiOptions(probe_timeout_seconds))
    install_error_handlers(app)
    # PHX-114 (ADR-108) adds the voice routes.
    for module in (missions, permissions, system, setup, voice, observability):
        app.include_router(module.router)
