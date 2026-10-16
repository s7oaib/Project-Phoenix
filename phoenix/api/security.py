"""Local API security: loopback Host, Tauri Origin and a per-launch token (PHX-101).

ADR-085. Every HTTP request and WebSocket handshake must:

- carry exactly one ``Host`` equal to the bound loopback ``host:port``
  (which defeats DNS rebinding);
- carry no ``Origin``, or exactly one allowlisted Tauri origin;
- prove the per-launch token, compared in constant time. HTTP uses
  ``Authorization: Bearer <token>``. A WebSocket offers the subprotocols
  ``phoenix.v1`` and ``phoenix.auth.<token>``, and only ``phoenix.v1`` is echoed.

Checks run in pure ASGI middleware before routing, so they cover every route,
including /health, /ready and unknown paths. No CORS headers are ever sent.
Errors use a fixed envelope that never echoes input, headers or the token. The
token is held as bytes and never logged or shown in a repr.
"""

import base64
import binascii
import hmac
import re
from collections.abc import Mapping
from typing import Final
from uuid import uuid4

from fastapi import FastAPI, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketClose

LOOPBACK_HOSTS: Final = ("127.0.0.1", "::1")
# Tauri 2 app origins (tauri 2.12.1 manager/mod.rs): Windows/Android serve
# http(s)://tauri.localhost; macOS/Linux serve tauri://localhost.
TAURI_ORIGINS: Final = (
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
)
WS_SUBPROTOCOL: Final = "phoenix.v1"
WS_TOKEN_PREFIX: Final = "phoenix.auth."
WS_POLICY_VIOLATION: Final = 1008
MIN_TOKEN_BYTES: Final = 32
STATE_KEY: Final = "local_api_security"
_TOKEN = re.compile(r"[A-Za-z0-9_-]{43,512}")

_MESSAGES: Final[Mapping[str, str]] = {
    "unauthorized": "Missing or invalid credentials.",
    "forbidden": "Forbidden.",
    "forbidden_host": "Host not allowed.",
    "forbidden_origin": "Origin not allowed.",
    "not_found": "Not found.",
    "method_not_allowed": "Method not allowed.",
    "invalid_request": "Request validation failed.",
    "http_error": "Request failed.",
    "internal_error": "Internal error.",
}
_HTTP_CODES: Final[Mapping[int, str]] = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    422: "invalid_request",
}


class TokenError(ValueError):
    """A malformed token; the message never contains the token."""


def parse_token(raw: str) -> str:
    """URL-safe base64 without padding, 43-512 chars, at least 32 bytes decoded."""
    token = raw.removesuffix("\n").removesuffix("\r")
    if not _TOKEN.fullmatch(token):
        raise TokenError("API token must be 43-512 URL-safe base64 characters")
    try:
        decoded = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (binascii.Error, ValueError):
        raise TokenError("API token is not valid URL-safe base64") from None
    if len(decoded) < MIN_TOKEN_BYTES:
        raise TokenError("API token must encode at least 32 random bytes")
    return token


class LocalApiSecurity:
    """The bound loopback address and the per-launch token; repr is redacted."""

    __slots__ = ("_token", "host", "port", "allowed_origins")

    def __init__(self, token: str, *, host: str, port: int) -> None:
        if host not in LOOPBACK_HOSTS:
            raise ValueError("Local API security requires a loopback host")
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Local API security requires the bound port")
        self._token = parse_token(token).encode("ascii")
        self.host = host
        self.port = port
        self.allowed_origins = TAURI_ORIGINS

    def __repr__(self) -> str:
        return (
            f"LocalApiSecurity(host={self.host!r}, port={self.port}, token=<redacted>)"
        )

    @property
    def host_header(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"

    def token_matches(self, candidate: str) -> bool:
        """Constant-time comparison; never short-circuits on content."""
        return hmac.compare_digest(candidate.encode("utf-8", "replace"), self._token)


def _bearer_ok(security: LocalApiSecurity, headers: Headers) -> bool:
    values = headers.getlist("authorization")
    if len(values) != 1:
        return False
    parts = values[0].split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return security.token_matches(parts[1])


def _websocket_token_ok(security: LocalApiSecurity, scope: Scope) -> bool:
    offered = [str(item) for item in scope.get("subprotocols", ())]
    tokens = [
        item.removeprefix(WS_TOKEN_PREFIX)
        for item in offered
        if item.startswith(WS_TOKEN_PREFIX)
    ]
    matched = len(tokens) == 1 and security.token_matches(tokens[0])
    return matched and WS_SUBPROTOCOL in offered


def check_scope(security: LocalApiSecurity, scope: Scope) -> tuple[int, str] | None:
    """Return (HTTP status, error code) for a refused request, else None."""
    headers = Headers(scope=scope)
    hosts = headers.getlist("host")
    if len(hosts) != 1 or hosts[0] != security.host_header:
        return 403, "forbidden_host"
    origins = headers.getlist("origin")
    if len(origins) > 1 or (origins and origins[0] not in security.allowed_origins):
        return 403, "forbidden_origin"
    if scope["type"] == "websocket":
        authenticated = _websocket_token_ok(security, scope)
    else:
        authenticated = _bearer_ok(security, headers)
    return None if authenticated else (401, "unauthorized")


def error_response(
    status: int,
    code: str,
    request_id: str,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    message = _MESSAGES.get(code, _MESSAGES["http_error"])
    extra = {"X-Request-ID": request_id, **(headers or {})}
    if status == 401:
        extra["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        {"error": {"code": code, "message": message, "request_id": request_id}},
        status_code=status,
        headers=extra,
    )


class LocalApiSecurityMiddleware:
    """Pure ASGI: refuses before routing; lifespan passes through."""

    def __init__(self, app: ASGIApp, security: LocalApiSecurity) -> None:
        self.app = app
        self.security = security

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        refusal = check_scope(self.security, scope)
        if refusal is not None:
            if scope["type"] == "websocket":
                await WebSocketClose(code=WS_POLICY_VIOLATION)(scope, receive, send)
            else:
                status, code = refusal
                await error_response(status, code, request_id)(scope, receive, send)
            return
        if scope["type"] == "http":

            async def send_with_id(message: Message) -> None:
                if message["type"] == "http.response.start":
                    MutableHeaders(scope=message)["X-Request-ID"] = request_id
                await send(message)

            await self.app(scope, receive, send_with_id)
            return
        await self.app(scope, receive, send)


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else uuid4().hex


async def _http_error(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, StarletteHTTPException):
        return await _internal_error(request, exc)
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    allow = (exc.headers or {}).get("Allow")
    headers = {"Allow": allow} if allow is not None else None
    return error_response(exc.status_code, code, _request_id(request), headers)


async def _validation_error(request: Request, exc: Exception) -> Response:
    return error_response(422, "invalid_request", _request_id(request))


async def _internal_error(request: Request, exc: Exception) -> Response:
    return error_response(500, "internal_error", _request_id(request))


def install_local_api_security(app: FastAPI, security: LocalApiSecurity) -> None:
    """Middleware, error envelope and the WebSocket helper's policy, together."""
    if type(security) is not LocalApiSecurity:
        raise TypeError("Expected LocalApiSecurity")
    setattr(app.state, STATE_KEY, security)
    app.add_middleware(LocalApiSecurityMiddleware, security=security)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _internal_error)


async def accept_websocket(websocket: WebSocket) -> bool:
    """Re-check and accept with ``phoenix.v1``; otherwise close 1008 (fail closed)."""
    security = getattr(websocket.app.state, STATE_KEY, None)
    if (
        type(security) is not LocalApiSecurity
        or check_scope(security, websocket.scope) is not None
    ):
        await websocket.close(code=WS_POLICY_VIOLATION)
        return False
    await websocket.accept(subprotocol=WS_SUBPROTOCOL)
    return True
