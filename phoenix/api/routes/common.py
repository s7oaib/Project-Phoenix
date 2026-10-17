"""Shared plumbing for the PHX-104 REST routes: services, errors, pagination.

Errors use the PHX-101 envelope (``error_response``) with a closed code and a
fixed message; nothing from the request is echoed. MissionService and workspace
refusals map to HTTP status codes in one place:

- ``NotFoundError`` -> 404 ``not_found``
- ``InvalidMissionStateError`` -> 409 ``invalid_state``
- ``InvalidRequestError`` -> 422 ``invalid_request``
- ``WorkspaceError`` -> 422 with its own ``workspace_*`` code
- ``ServiceNotRunningError`` or no services -> 503 ``service_unavailable``
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, Generic, TypeVar
from uuid import uuid4

from fastapi import FastAPI, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from phoenix.api.missions import (
    InvalidMissionStateError,
    InvalidRequestError,
    NotFoundError,
    ServiceNotRunningError,
)
from phoenix.api.security import error_response
from phoenix.api.server import BackendServices, get_services
from phoenix.api.workspace import WorkspaceError

# Canonical lowercase UUIDs: every mission, conversation, request and grant ID.
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
MAX_PAGE = 200
DEFAULT_PAGE = 50
MAX_OFFSET = 1_000_000

Uuid = Annotated[str, Path(pattern=UUID_PATTERN, max_length=36)]
UuidQuery = Annotated[str | None, Query(pattern=UUID_PATTERN, max_length=36)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE)]
Offset = Annotated[int, Query(ge=0, le=MAX_OFFSET)]

OPTIONS_KEY = "phoenix_api_options"

_T = TypeVar("_T")


@dataclass(frozen=True)
class ApiOptions:
    probe_timeout_seconds: float


class ApiError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


class Contract(BaseModel):
    """Request and response schemas: exact fields only."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Page(Contract, Generic[_T]):
    items: tuple[_T, ...]
    limit: int
    offset: int
    total: int


def page(items: Sequence[_T], limit: int, offset: int) -> Page[_T]:
    return Page(
        items=tuple(items[offset : offset + limit]),
        limit=limit,
        offset=offset,
        total=len(items),
    )


def services(request: Request) -> BackendServices:
    found = get_services(request.app)
    if found is None:
        raise ApiError(503, "service_unavailable")
    return found


def options(request: Request) -> ApiOptions:
    found = getattr(request.app.state, OPTIONS_KEY, None)
    if not isinstance(found, ApiOptions):
        raise ApiError(503, "service_unavailable")
    return found


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else uuid4().hex


Handler = Callable[[Request, Exception], Awaitable[Response]]


def _fixed(status: int, code: str) -> Handler:
    async def handle(request: Request, exc: Exception) -> Response:
        return error_response(status, code, _request_id(request))

    return handle


async def _api_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, ApiError)
    return error_response(exc.status, exc.code, _request_id(request))


async def _workspace_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, WorkspaceError)
    return error_response(422, exc.code.value, _request_id(request))


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(WorkspaceError, _workspace_error)
    app.add_exception_handler(NotFoundError, _fixed(404, "not_found"))
    app.add_exception_handler(InvalidMissionStateError, _fixed(409, "invalid_state"))
    app.add_exception_handler(InvalidRequestError, _fixed(422, "invalid_request"))
    app.add_exception_handler(
        ServiceNotRunningError, _fixed(503, "service_unavailable")
    )
