"""PHX-104 REST API v1 routes (ADR-089); installed by ``build_server_app``.

Every route sits behind the PHX-101 middleware (Host, Origin, token). The
routes are thin: MissionService, WorkspaceService and (PHX-110, ADR-132 and
ADR-133) KnowledgeService own the behaviour. PHX-110 adds ``/files/upload``,
``/files``, ``/memory`` and ``/memory/search`` (see ``knowledge``). PHX-108
(ADR-110, ADR-112) adds ``GET /onboarding`` and ``GET /capabilities``
(``routes.setup``).
"""

from fastapi import FastAPI

from phoenix.api.routes import knowledge, missions, permissions, setup, system
from phoenix.api.routes.common import OPTIONS_KEY, ApiOptions, install_error_handlers


def install_api_routes(app: FastAPI, *, probe_timeout_seconds: float) -> None:
    setattr(app.state, OPTIONS_KEY, ApiOptions(probe_timeout_seconds))
    install_error_handlers(app)
    for module in (missions, permissions, system, setup, knowledge):
        app.include_router(module.router)
