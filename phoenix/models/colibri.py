"""Optional bounded Colibri HTTP adapter; never installs or manages its engine."""

import asyncio
import json
import math
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal, Self
from urllib.parse import quote, urlsplit

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    ValidationError,
)

from phoenix.models.provider import (
    CancellationResult,
    CancellationStatus,
    GenerationChunk,
    GenerationRequest,
    GenerationResponse,
    Identifier,
    ModelCapabilities,
    ModelProvider,
    ModelProviderError,
    NonnegativeInt,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderUnavailableError,
    TokenUsage,
)


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Model(_Wire):
    object: Literal["model"]
    id: str = Field(strict=True, min_length=1, pattern=r"\S")


class _Models(_Wire):
    object: Literal["list"]
    data: tuple[_Model, ...]


class _Usage(_Wire):
    prompt_tokens: NonnegativeInt | None = None
    completion_tokens: NonnegativeInt | None = None
    total_tokens: NonnegativeInt | None = None


class _Envelope(_Wire):
    model: str | None = Field(default=None, strict=True)
    usage: _Usage | None = None


class _Message(_Wire):
    role: Literal["assistant"]
    content: str = Field(strict=True)


class _Choice(_Wire):
    index: int = Field(strict=True, ge=0, le=0)
    message: _Message
    finish_reason: Identifier | None = None


class _Completion(_Envelope):
    object: Literal["chat.completion"]
    choices: tuple[_Choice, ...] = Field(min_length=1, max_length=1)


class _Delta(_Wire):
    role: Literal["assistant"] | None = None
    content: str | None = Field(default=None, strict=True)


class _StreamChoice(_Wire):
    index: int = Field(strict=True, ge=0, le=0)
    delta: _Delta
    finish_reason: Identifier | None = None


class _Chunk(_Envelope):
    object: Literal["chat.completion.chunk"]
    choices: tuple[_StreamChoice, ...] = Field(max_length=1)


@dataclass
class _Operation:
    task: asyncio.Task[object] | None
    response: httpx.Response | None = None
    cancelled: bool = False


def _base_url(value: str) -> httpx.URL:
    try:
        if not isinstance(value, str) or any(char.isspace() for char in value):
            raise ValueError
        parts = urlsplit(value)
        if (
            parts.scheme not in ("http", "https")
            or not parts.hostname
            or "@" in parts.netloc
            or "?" in value
            or "#" in value
            or "\\" in value
            or any(ord(char) < 32 for char in value)
        ):
            raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
        return httpx.URL(value).copy_with(path=parts.path.rstrip("/") + "/")
    except (ValueError, httpx.InvalidURL):
        raise ValueError("Invalid Colibri API base URL") from None


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or "error" in value:
        raise ModelProviderError("Colibri invalid protocol response")
    return value


def _identity(data: dict[str, object], request: GenerationRequest) -> None:
    if "model" in data and data["model"] != request.model_id:
        raise ModelProviderError("Colibri contradictory model identity")


def _usage(
    value: _Usage | None, previous: TokenUsage | None = None
) -> TokenUsage | None:
    if value is None:
        return previous
    reported = {
        "input_tokens": value.prompt_tokens,
        "output_tokens": value.completion_tokens,
        "total_tokens": value.total_tokens,
    }
    values = previous.model_dump() if previous is not None else {}
    values.update({key: count for key, count in reported.items() if count is not None})
    return TokenUsage.model_validate(values) if values else previous


def _payload(request: GenerationRequest, *, stream: bool) -> dict[str, object]:
    body: dict[str, object] = {
        "model": request.model_id,
        "messages": [
            {"role": item.role.value, "content": item.text} for item in request.messages
        ],
        "stream": stream,
    }
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        body["max_tokens"] = request.max_output_tokens
    if request.stop_sequences:
        body["stop"] = list(request.stop_sequences)
    return body


def _request(request: GenerationRequest) -> GenerationRequest:
    try:
        return GenerationRequest.model_validate(request.model_dump(warnings=False))
    except ValidationError:
        raise ModelProviderError("Colibri invalid generation request") from None


async def _sse_lines(response: httpx.Response) -> AsyncGenerator[str, None]:
    """Decode complete SSE lines strictly, preserving split UTF-8 and CRLF."""
    remainder = b""
    async for part in response.aiter_bytes():
        lines = (remainder + part).splitlines(keepends=True)
        remainder = b""
        for index, line in enumerate(lines):
            # Defer a trailing CR until the next piece can supply its paired LF.
            if index == len(lines) - 1 and not line.endswith(b"\n"):
                remainder = line
            else:
                yield line.removesuffix(b"\n").removesuffix(b"\r").decode("utf-8")
    if remainder:
        yield remainder.removesuffix(b"\r").decode("utf-8")


class ColibriProvider(ModelProvider):
    """One event loop, independent caller operations, no hidden workers.

    Borrowed clients stay caller-owned/configured. Stream consumers must exhaust
    or explicitly close the iterator; aclose() cancels active provider operations.
    Accepted cancellation acknowledges local I/O cancellation, not server shutdown.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000/v1",
        *,
        api_key: str | None = None,
        timeout: httpx.Timeout | None = None,
        operation_timeout: float = 1800,
        metadata_timeout: float = 10,
        cleanup_timeout: float = 5,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__("colibri")
        self.base_url = _base_url(base_url)
        if api_key is not None and (
            not isinstance(api_key, str)
            or not api_key
            or any(not 33 <= ord(char) <= 126 for char in api_key)
        ):
            raise ValueError("Colibri API key must be a nonempty HTTP bearer token")
        self._api_key = SecretStr(api_key) if api_key is not None else None
        self._timeout = timeout or httpx.Timeout(connect=3, read=600, write=10, pool=5)
        for value in (
            *self._timeout.as_dict().values(),
            operation_timeout,
            metadata_timeout,
            cleanup_timeout,
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("Colibri timeouts must be finite and positive")
        if client is not None and transport is not None:
            raise ValueError("Inject either client or transport, not both")
        self._operation_timeout = operation_timeout
        self._metadata_timeout = metadata_timeout
        self._cleanup_timeout = cleanup_timeout
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.AsyncClient(
                timeout=self._timeout,
                transport=transport,
                trust_env=False,
                follow_redirects=False,
            )
        )
        self._active: dict[str, _Operation] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return tuple(self._active)

    def _ready(self) -> None:
        if self._closed or self._client.is_closed:
            raise ModelProviderError("Colibri provider closed")
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise ModelProviderError("Colibri provider belongs to another event loop")
        self._loop = loop

    def _headers(self) -> dict[str, str]:
        return (
            {"Authorization": "Bearer " + self._api_key.get_secret_value()}
            if self._api_key is not None
            else {}
        )

    def _register(self, operation_id: str) -> _Operation:
        self._ready()
        if operation_id in self._active:
            raise ModelProviderError("Colibri duplicate active operation ID")
        operation = _Operation(asyncio.current_task())
        self._active[operation_id] = operation
        return operation

    def _finish(self, operation_id: str, operation: _Operation) -> None:
        if self._active.get(operation_id) is operation:
            del self._active[operation_id]

    async def _json(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> dict[str, object]:
        self._ready()
        try:
            async with asyncio.timeout(
                self._metadata_timeout if body is None else self._operation_timeout
            ):
                response = await self._client.request(
                    method,
                    self.base_url.join(path),
                    json=body,
                    headers=self._headers(),
                    timeout=self._timeout,
                    follow_redirects=False,
                )
                response.raise_for_status()
                return _object(response.json())
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.ProxyError,
            TimeoutError,
        ):
            raise ProviderUnavailableError(
                "Colibri transport unavailable or timed out"
            ) from None
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise ModelProviderError("Colibri invalid HTTP/JSON response") from None

    async def health(self) -> ProviderHealth:
        try:
            _Models.model_validate(await self._json("GET", "models"))
            status = ProviderHealthStatus.HEALTHY
            detail = "Colibri model API reachable"
        except ProviderUnavailableError:
            status = ProviderHealthStatus.UNAVAILABLE
            detail = "Colibri unavailable"
        except (ModelProviderError, ValidationError):
            status = ProviderHealthStatus.DEGRADED
            detail = "Colibri health response invalid"
        return ProviderHealth(
            provider_id=self.provider_id, status=status, detail=detail
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        model_id = TypeAdapter(Identifier).validate_python(model_id)
        # Quote the whole component, including slashes/query delimiters and dots.
        component = quote(model_id, safe="").replace(".", "%2E")
        data = await self._json("GET", "models/" + component)
        try:
            model = _Model.model_validate(data)
        except ValidationError:
            raise ModelProviderError("Colibri invalid model response") from None
        if model.id != model_id:
            raise ModelProviderError("Colibri contradictory model identity")
        return ModelCapabilities(
            provider_id=self.provider_id,
            model_id=model_id,
            supports_streaming=True,
            supports_cancellation=True,
            supports_structured_output=False,
            supports_tool_use=False,
            supports_vision=False,
            context_limit_tokens=None,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        request = _request(request)
        operation = self._register(request.operation_id)
        try:
            data = await self._json(
                "POST", "chat/completions", _payload(request, stream=False)
            )
            _identity(data, request)
            record = _Completion.model_validate(data)
            choice = record.choices[0]
            return GenerationResponse(
                operation_id=request.operation_id,
                provider_id=self.provider_id,
                model_id=request.model_id,
                text=choice.message.content,
                finish_reason=choice.finish_reason,
                usage=_usage(record.usage),
            )
        except ValidationError:
            raise ModelProviderError("Colibri invalid completion response") from None
        finally:
            self._finish(request.operation_id, operation)

    async def _close_response(self, response: httpx.Response) -> None:
        try:
            async with asyncio.timeout(self._cleanup_timeout):
                await response.aclose()
        except (TimeoutError, httpx.HTTPError):
            raise ModelProviderError("Colibri response cleanup failed") from None

    async def stream(
        self, request: GenerationRequest
    ) -> AsyncGenerator[GenerationChunk, None]:
        request = _request(request)
        operation = self._register(request.operation_id)
        deadline = asyncio.get_running_loop().time() + self._operation_timeout
        response: httpx.Response | None = None
        finish: str | None = None
        usage: TokenUsage | None = None
        seen_choice = False
        frame: list[str] = []
        try:
            async with asyncio.timeout_at(deadline):
                wire = self._client.build_request(
                    "POST",
                    self.base_url.join("chat/completions"),
                    json=_payload(request, stream=True),
                    headers=self._headers(),
                    timeout=self._timeout,
                )
                response = await self._client.send(
                    wire, stream=True, follow_redirects=False
                )
            operation.response = response
            response.raise_for_status()
            lines = _sse_lines(response).__aiter__()
            while True:
                if operation.cancelled:
                    raise asyncio.CancelledError
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError
                operation.task = asyncio.current_task()
                try:
                    async with asyncio.timeout_at(deadline):
                        line = await anext(lines)
                except StopAsyncIteration:
                    raise ModelProviderError(
                        "Colibri stream ended without terminal evidence"
                    ) from None
                if line.startswith(":"):
                    continue
                if line:
                    if not line.startswith("data:"):
                        raise ModelProviderError("Colibri invalid SSE line")
                    frame.append(line[5:].removeprefix(" "))
                    continue
                if not frame:
                    continue
                value = "\n".join(frame)
                frame.clear()
                if value == "[DONE]":
                    if not seen_choice:
                        raise ModelProviderError(
                            "Colibri stream lacks completion choice"
                        )
                    await self._close_response(response)
                    self._finish(request.operation_id, operation)
                    operation.task = None
                    yield GenerationChunk(
                        operation_id=request.operation_id,
                        provider_id=self.provider_id,
                        model_id=request.model_id,
                        text="",
                        finish_reason=finish,
                        usage=usage,
                        is_final=True,
                    )
                    return
                data = _object(json.loads(value))
                _identity(data, request)
                # Recognized provider telemetry contains no completion choice.
                if "choices" not in data and isinstance(data.get("colibri"), dict):
                    if set(data) - {"colibri", "model"}:
                        raise ModelProviderError("Colibri invalid extension frame")
                    continue
                record = _Chunk.model_validate(data)
                usage = _usage(record.usage, usage)
                if not record.choices:
                    if record.usage is None:
                        raise ModelProviderError(
                            "Colibri empty stream frame lacks usage"
                        )
                    continue
                choice = record.choices[0]
                seen_choice = True
                text = choice.delta.content or ""
                if finish is not None and (text or choice.finish_reason is not None):
                    raise ModelProviderError("Colibri content follows finished choice")
                if choice.finish_reason is not None:
                    finish = choice.finish_reason
                operation.task = None
                yield GenerationChunk(
                    operation_id=request.operation_id,
                    provider_id=self.provider_id,
                    model_id=request.model_id,
                    text=text,
                )
        except (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.ProxyError,
            TimeoutError,
        ):
            raise ProviderUnavailableError(
                "Colibri stream unavailable or timed out"
            ) from None
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise ModelProviderError("Colibri invalid HTTP/SSE response") from None
        finally:
            self._finish(request.operation_id, operation)
            if response is not None:
                await self._close_response(response)

    async def cancel(self, operation_id: str) -> CancellationResult:
        operation_id = TypeAdapter(Identifier).validate_python(operation_id)
        self._ready()
        operation = self._active.get(operation_id)
        status = CancellationStatus.NOT_FOUND
        if operation is not None:
            operation.cancelled = True
            if operation.task is not None and not operation.task.done():
                if not operation.task.cancelling():
                    operation.task.cancel()
            elif operation.response is not None:
                await self._close_response(operation.response)
                self._finish(operation_id, operation)
            status = CancellationStatus.ACCEPTED
        return CancellationResult(
            operation_id=operation_id, provider_id=self.provider_id, status=status
        )

    async def aclose(self) -> None:
        if (
            self._closed
            and not self._active
            and (not self._owns_client or self._client.is_closed)
        ):
            return
        if any(
            operation.task is asyncio.current_task()
            for operation in self._active.values()
        ):
            raise ModelProviderError("Close Colibri outside its active request task")
        self._closed = True
        try:
            async with asyncio.timeout(self._cleanup_timeout):
                tasks: set[asyncio.Task[object]] = set()
                for operation_id, operation in tuple(self._active.items()):
                    operation.cancelled = True
                    if operation.task is not None and not operation.task.done():
                        if not operation.task.cancelling():
                            operation.task.cancel()
                        tasks.add(operation.task)
                    elif operation.response is not None:
                        await self._close_response(operation.response)
                        self._finish(operation_id, operation)
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            raise ModelProviderError("Colibri operation cleanup timed out") from None
        finally:
            self._active.clear()
            if self._owns_client:
                try:
                    async with asyncio.timeout(self._cleanup_timeout):
                        await self._client.aclose()
                except (TimeoutError, httpx.HTTPError):
                    raise ModelProviderError("Colibri client cleanup failed") from None

    async def __aenter__(self) -> Self:
        self._ready()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
