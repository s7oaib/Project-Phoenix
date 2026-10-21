"""PHX-104 permission decisions and grants (ADR-089).

Approve and deny are user decisions made through this authenticated API; they
are the only way a grant is created (INV-003, INV-005). The path names the
stored request and the body only the duration ("session" or "always"): the
tool, action, resource and actor of the grant always come from the stored
request, and a body naming any of them is refused (422). A request whose
resource is outside the active workspace root cannot be approved (409
``workspace_changed``); deny it instead. PHX-111: a web research request names
the search provider, not a workspace path, so the workspace check does not
apply to it; MissionService.approve still admits only the research step scope.
PHX-120 (ADR-162): the same holds for PHX-113 media requests (Spotify and
YouTube resources are not paths); PHX-113 document requests are workspace
paths and keep the check. PHX-112: a computer-control request
names no workspace path, so that check does not apply to it; its pending view
carries a display-only summary (ADR-151) that is never used to authorize.
"""

from fastapi import APIRouter, Request

from phoenix.agents.capability_steps import CAPABILITY_TOOLS, MEDIA_ACTOR_ID
from phoenix.agents.control_steps import CONTROL_ACTOR_ID
from phoenix.agents.research_steps import RESEARCH_TOOL_IDS
from phoenix.api.routes.common import (
    DEFAULT_PAGE,
    ApiError,
    Limit,
    Offset,
    Page,
    Uuid,
    page,
)
from phoenix.api.routes.common import services as backend
from phoenix.api.routes.missions import permission_out
from phoenix.api.routes.schemas import (
    ApproveRequest,
    ApproveResult,
    DenyRequest,
    DenyResult,
    GrantOut,
    PermissionRequestOut,
    RevokeResult,
)
from phoenix.persistence.missions import GrantRecord
from phoenix.security.permissions import GrantDuration

router = APIRouter()

_DURATIONS = {"session": GrantDuration.SESSION, "always": GrantDuration.PERSISTENT}
MEDIA_TOOL_IDS = tuple(
    tool_id
    for tool_id, (actor, _, _) in CAPABILITY_TOOLS.items()
    if actor == MEDIA_ACTOR_ID
)


def _grant(record: GrantRecord) -> GrantOut:
    grant = record.grant
    return GrantOut(
        grant_id=grant.grant_id,
        tool_id=grant.tool_id,
        action=grant.action,
        resource=grant.resource,
        risk_level=grant.risk_level.value,
        duration="session" if grant.duration == GrantDuration.SESSION else "always",
        status=record.status,
        issued_at=grant.issued_at,
        revoked_at=record.revoked_at,
    )


@router.get("/permissions", response_model=Page[PermissionRequestOut])
async def list_pending(
    request: Request, limit: Limit = DEFAULT_PAGE, offset: Offset = 0
) -> Page[PermissionRequestOut]:
    services = backend(request)
    records = await services.missions.list_pending_permissions()
    items = [
        permission_out(record, services.tool_registry, services.control_steps)
        for record in records
    ]
    return page(items, limit, offset)


@router.get("/permissions/{request_id}", response_model=PermissionRequestOut)
async def get_request(request_id: Uuid, request: Request) -> PermissionRequestOut:
    services = backend(request)
    record = await services.missions.get_permission_request(request_id)
    return permission_out(record, services.tool_registry, services.control_steps)


@router.post(
    "/permissions/{request_id}/approve", status_code=202, response_model=ApproveResult
)
async def approve(
    request_id: Uuid, body: ApproveRequest, request: Request
) -> ApproveResult:
    services = backend(request)
    missions = services.missions
    record = await missions.get_permission_request(request_id)
    # A request made under another (or no longer valid) workspace would store a
    # grant outside the active root; it can only be denied.
    # PHX-111/PHX-120: research and media requests name no workspace path;
    # PHX-112: neither do computer-control requests.
    unbound = (
        record.request.tool_id in (*RESEARCH_TOOL_IDS, *MEDIA_TOOL_IDS)
        or record.request.actor_id == CONTROL_ACTOR_ID
    )
    if not unbound and not services.workspace.covers(record.request.resource):
        raise ApiError(409, "workspace_changed")
    rerun = await missions.approve(request_id, _DURATIONS[body.duration])
    return ApproveResult(
        request_id=request_id,
        duration=body.duration,
        superseded_mission_id=record.mission_id,
        mission_id=rerun,
    )


@router.post("/permissions/{request_id}/deny", response_model=DenyResult)
async def deny(
    request_id: Uuid, request: Request, body: DenyRequest | None = None
) -> DenyResult:
    missions = backend(request).missions
    record = await missions.get_permission_request(request_id)
    await missions.deny(request_id)
    return DenyResult(request_id=request_id, mission_id=record.mission_id)


@router.get("/grants", response_model=Page[GrantOut])
async def list_grants(
    request: Request,
    include_inactive: bool = False,
    limit: Limit = DEFAULT_PAGE,
    offset: Offset = 0,
) -> Page[GrantOut]:
    records = await backend(request).missions.list_grants(
        include_inactive=include_inactive
    )
    return page([_grant(record) for record in records], limit, offset)


@router.post("/grants/{grant_id}/revoke", response_model=RevokeResult)
async def revoke(grant_id: Uuid, request: Request) -> RevokeResult:
    if not await backend(request).missions.revoke(grant_id):
        raise ApiError(409, "invalid_state")  # already revoked
    return RevokeResult(grant_id=grant_id)
