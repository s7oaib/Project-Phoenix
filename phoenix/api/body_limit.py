"""PHX-104 request-body cap for every HTTP route (ADR-089).

Pure ASGI, inside the PHX-101 security middleware: a body larger than
``MAX_BODY_BYTES`` is refused with 413 and the error envelope before any route
parses it, whether it declares a ``Content-Length`` or arrives in chunks. The
largest valid v1 body (a 16,000-character chat message) is far below the cap.
"""

from typing import Final
from uuid import uuid4

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from phoenix.api.security import error_response

MAX_BODY_BYTES: Final = 64 * 1024


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        request_id = scope.get("state", {}).get("request_id")
        if not isinstance(request_id, str):
            request_id = uuid4().hex
        response = error_response(413, "payload_too_large", request_id)
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = Headers(scope=scope).getlist("content-length")
        try:
            too_large = any(int(value) > self.max_bytes for value in declared)
        except ValueError:
            too_large = True  # a malformed length is refused, not guessed
        if too_large:
            await self._refuse(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        pending: Message | None = None
        while True:
            message = await receive()
            if message["type"] != "http.request":
                pending = message  # e.g. a disconnect: pass it on as is
                break
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                await self._refuse(scope, receive, send)
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            if pending is not None:
                return pending
            return {"type": "http.request", "body": b"".join(chunks)}

        await self.app(scope, replay, send)
