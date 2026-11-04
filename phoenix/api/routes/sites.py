"""Website designer routes: list, build, edit, undo/redo and preview.

Behind the same middleware as every route. Builds and edits are jobs: the POST
answers 202 at once and clients poll ``GET /sites/{id}`` (status, phase).
"""

from pathlib import Path as FsPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Path, Request
from fastapi.responses import HTMLResponse
from pydantic import StringConstraints

from phoenix.api.routes.common import ApiError, Contract
from phoenix.api.server import STATE_KEY
from phoenix.api.workspace import WorkspaceStatus
from phoenix.sites.generator import provider_model_fn
from phoenix.sites.service import SiteBusy, SiteNotFound, SitesService, set_default
from phoenix.sites.spec import SiteError

router = APIRouter()

SlugPath = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$", max_length=40)]


class SiteSummary(Contract):
    id: str
    name: str
    type: str
    head: int
    max: int
    updated_at: str


class SiteView(Contract):
    id: str
    name: str | None = None
    type: str | None = None
    status: Literal["building", "ready", "failed"]
    phase: Literal["planning", "spec", "render", "ready"]
    head: int
    max: int
    spec: dict[str, Any] | None = None
    error: str | None = None
    message: str | None = None


class BuildRequest(Contract):
    prompt: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=2000, pattern=r"\S"),
    ]


class EditRequest(Contract):
    instruction: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=1000, pattern=r"\S"),
    ]
    section_id: Annotated[str, StringConstraints(pattern=r"^s[0-9]{1,4}$")] | None = (
        None
    )


def install(app: FastAPI) -> None:
    """Create the SitesService for this app (also the default for voice routing)."""

    def backend() -> Any:
        found = getattr(app.state, STATE_KEY, None)
        if found is None:
            raise ApiError(503, "service_unavailable")
        return found

    def root() -> FsPath:
        state = backend().workspace.state
        if state.status == WorkspaceStatus.ACTIVE and state.root:
            return FsPath(state.root)
        return backend().settings.data_dir / "workspace"

    def image_roots() -> list[FsPath]:
        home = FsPath.home()
        return [
            root(),
            *(
                home / name
                for name in ("Desktop", "Documents", "Downloads", "Pictures")
            ),
        ]

    def model() -> Any:
        b = backend()
        return provider_model_fn(b.structured_providers or b.providers)

    app.state.sites = service = SitesService(root, model, image_roots)
    set_default(service)


def sites(request: Request) -> SitesService:
    found = getattr(request.app.state, "sites", None)
    if not isinstance(found, SitesService):
        raise ApiError(503, "service_unavailable")
    return found


def _fail(exc: SiteError) -> ApiError:
    if isinstance(exc, SiteNotFound):
        return ApiError(404, "not_found")
    if isinstance(exc, SiteBusy):
        return ApiError(409, "invalid_state")
    return ApiError(422, "invalid_request")


def _view(data: dict[str, Any]) -> SiteView:
    return SiteView.model_validate(data)


@router.get("/sites", response_model=list[SiteSummary])
async def list_sites(request: Request) -> list[dict[str, Any]]:
    return sites(request).list()


@router.post(
    "/sites", status_code=202, response_model=SiteView, response_model_exclude_none=True
)
async def build_site(body: BuildRequest, request: Request) -> SiteView:
    try:
        return _view(sites(request).start_build(body.prompt))
    except SiteError as exc:
        raise _fail(exc) from None


@router.get(
    "/sites/{site_id}", response_model=SiteView, response_model_exclude_none=True
)
async def get_site(site_id: SlugPath, request: Request) -> SiteView:
    try:
        return _view(sites(request).get(site_id))
    except SiteError as exc:
        raise _fail(exc) from None


@router.post(
    "/sites/{site_id}/edit",
    status_code=202,
    response_model=SiteView,
    response_model_exclude_none=True,
)
async def edit_site(site_id: SlugPath, body: EditRequest, request: Request) -> SiteView:
    try:
        return _view(
            sites(request).start_edit(site_id, body.instruction, body.section_id)
        )
    except SiteError as exc:
        raise _fail(exc) from None


@router.post(
    "/sites/{site_id}/undo", response_model=SiteView, response_model_exclude_none=True
)
async def undo_site(site_id: SlugPath, request: Request) -> SiteView:
    try:
        return _view(sites(request).move(site_id, -1))
    except SiteError as exc:
        raise _fail(exc) from None


@router.post(
    "/sites/{site_id}/redo", response_model=SiteView, response_model_exclude_none=True
)
async def redo_site(site_id: SlugPath, request: Request) -> SiteView:
    try:
        return _view(sites(request).move(site_id, 1))
    except SiteError as exc:
        raise _fail(exc) from None


@router.get("/sites/{site_id}/preview", response_class=HTMLResponse)
async def preview_site(site_id: SlugPath, request: Request) -> HTMLResponse:
    try:
        return HTMLResponse(sites(request).preview(site_id))
    except SiteError as exc:
        raise _fail(exc) from None
