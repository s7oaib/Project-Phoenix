"""Explicit bounded Ollama REST adapter; no discovery or lifecycle policy."""

import asyncio
import json
import math
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from phoenix.models.provider import (
    CancellationResult,
    CancellationStatus,
    GenerationChunk,
    GenerationMetrics,
    GenerationRequest,
    GenerationResponse,
    Identifier,
    ModelCapabilities,
    ModelProvider,
    ModelProviderError,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderUnavailableError,
    TokenUsage,
)

_Count = Annotated[int, Field(strict=True, ge=0)]


class _Message(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str = Field(strict=True)


class _Record(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str | None = Field(default=None, strict=True)
    message: _Message
    done: bool = Field(strict=True)
    done_reason: str | None = Field(default=None, strict=True)
    prompt_eval_count: _Count | None = None
    eval_count: _Count | None = None
    total_duration: _Count | None = None
    eval_duration: _Count | None = None
    load_duration: _Count | None = None
    prompt_eval_duration: _Count | None = None


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
        raise ValueError(
            "Invalid Ollama base URL (HTTP(S), no credentials/query)"
        ) from None


def _context_limit(info: object) -> int | None:
    if not isinstance(info, dict):
        return None
    values: set[int] = set()
    for key, value in info.items():
        if isinstance(key, str) and key.endswith(".context_length"):
            if type(value) is not int or value <= 0:
                return None
            values.add(value)
    return next(iter(values)) if len(values) == 1 else None


def _payload(
    request: GenerationRequest, *, stream: bool, think: bool | None
) -> dict[str, object]:
    body: dict[str, object] = {
        "model": request.model_id,
        "messages": [
            {"role": message.role.value, "content": message.text}
            for message in request.messages
        ],
        "stream": stream,
    }
    # Reasoning text Ollama returns in message.thinking is never mapped out.
    if think is not None:
        body["think"] = think
    options: dict[str, object] = {}
    if request.temperature is not None:
        options["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        options["num_predict"] = request.max_output_tokens
    if request.stop_sequences:
        options["stop"] = list(request.stop_sequences)
    if options:
        body["options"] = options
    return body


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or "error" in value:
        raise ModelProviderError("Ollama invalid protocol response")
    return value


def _record(value: object, request: GenerationRequest) -> _Record:
    data = _object(value)
    try:
        record = _Record.model_validate(data)
    except ValidationError:
        raise ModelProviderError("Ollama invalid chat record") from None
    if "model" in data:
        returned = record.model
        if not returned or returned.removesuffix(
            ":latest"
        ) != request.model_id.removesuffix(":latest"):
            raise ModelProviderError("Ollama contradictory model identity")
    return record


def _response(record: _Record, request: GenerationRequest) -> GenerationResponse:
    try:
        usage = None
        if record.prompt_eval_count is not None or record.eval_count is not None:
            usage = TokenUsage(
                input_tokens=record.prompt_eval_count, output_tokens=record.eval_count
            )
        duration = (
            record.total_duration / 1_000_000
            if record.total_duration is not None
            else None
        )
        rate = (
            record.eval_count / (record.eval_duration / 1_000_000_000)
            if record.eval_count is not None and record.eval_duration
            else None
        )
        metrics = (
            GenerationMetrics(duration_ms=duration, tokens_per_second=rate)
            if duration is not None or rate is not None
            else None
        )
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id="ollama",
            model_id=request.model_id,
            text=record.message.content,
            finish_reason=record.done_reason.strip() or None
            if record.done_reason is not None
            else None,
            usage=usage,
            metrics=metrics,
        )
    except (OverflowError, ValidationError):
        raise ModelProviderError("Ollama invalid usage or timing") from None


class OllamaProvider(ModelProvider):
    """Single-event-loop adapter. Caller owns stream exhaustion or explicit close.

    Injected clients remain caller-owned; injected transports belong to the owned
    client. No requests or workers start at construction. Stream consumers that
    stop early must call aclose() on the iterator or close this provider.
    think=None sends no thinking control (model default); True/False is sent as-is.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        *,
        timeout: httpx.Timeout | None = None,
        operation_timeout: float = 300,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        think: bool | None = None,
    ) -> None:
        super().__init__("ollama")
        if think is not None and type(think) is not bool:
            raise ValueError("Ollama think must be True, False or None")
        self._think = think
        self.base_url = _base_url(base_url)
        self._timeout = timeout or httpx.Timeout(connect=3, read=120, write=10, pool=5)
        for value in (*self._timeout.as_dict().values(), operation_timeout):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("Ollama timeouts must be finite and positive")
        if client is not None and transport is not None:
            raise ValueError("Inject either client or transport, not both")
        self._operation_timeout = operation_timeout
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
        if self._closed:
            raise ModelProviderError("Ollama provider closed")
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise ModelProviderError("Ollama provider belongs to another event loop")
        self._loop = loop

    def _register(self, operation_id: str) -> _Operation:
        self._ready()
        if operation_id in self._active:
            raise ModelProviderError("Ollama duplicate active operation ID")
        operation = _Operation(asyncio.current_task())
        self._active[operation_id] = operation
        return operation

    def _finish(self, operation_id: str, operation: _Operation) -> None:
        if self._active.get(operation_id) is operation:
            del self._active[operation_id]

    @staticmethod
    def _status(response: httpx.Response) -> None:
        if not response.is_success:
            raise ModelProviderError(f"Ollama HTTP status {response.status_code}")

    async def _json(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> dict[str, object]:
        self._ready()
        try:
            async with asyncio.timeout(self._operation_timeout):
                response = await self._client.request(
                    method,
                    self.base_url.join(path),
                    json=body,
                    timeout=self._timeout,
                    follow_redirects=False,
                )
                self._status(response)
                return _object(response.json())
        except (httpx.TimeoutException, httpx.NetworkError, TimeoutError):
            raise ProviderUnavailableError(
                "Ollama transport unavailable or timed out"
            ) from None
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise ModelProviderError("Ollama invalid HTTP/JSON response") from None

    async def health(self) -> ProviderHealth:
        try:
            data = await self._json("GET", "api/version")
            version = data.get("version")
            if not isinstance(version, str) or not re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][a-zA-Z0-9.-]+)?", version
            ):
                raise ModelProviderError("Ollama invalid version response")
            return ProviderHealth(
                provider_id=self.provider_id,
                status=ProviderHealthStatus.HEALTHY,
                detail=f"Ollama version {version}",
            )
        except ProviderUnavailableError:
            return ProviderHealth(
                provider_id=self.provider_id,
                status=ProviderHealthStatus.UNAVAILABLE,
                detail="Ollama unavailable",
            )
        except ModelProviderError:
            return ProviderHealth(
                provider_id=self.provider_id,
                status=ProviderHealthStatus.DEGRADED,
                detail="Ollama health response invalid",
            )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        model_id = TypeAdapter(Identifier).validate_python(model_id)
        data = await self._json("POST", "api/show", {"model": model_id})
        values = data.get("capabilities")
        capabilities = (
            set(values)
            if isinstance(values, list)
            and all(isinstance(item, str) for item in values)
            else set()
        )
        return ModelCapabilities(
            provider_id=self.provider_id,
            model_id=model_id,
            supports_streaming="completion" in capabilities,
            supports_structured_output=False,
            supports_tool_use="tools" in capabilities,
            supports_vision="vision" in capabilities,
            supports_cancellation=True,
            context_limit_tokens=_context_limit(data.get("model_info")),
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        operation = self._register(request.operation_id)
        try:
            record = _record(
                await self._json(
                    "POST",
                    "api/chat",
                    _payload(request, stream=False, think=self._think),
                ),
                request,
            )
            if not record.done:
                raise ModelProviderError("Ollama generation lacks completion")
            return _response(record, request)
        finally:
            self._finish(request.operation_id, operation)

    async def stream(
        self, request: GenerationRequest
    ) -> AsyncGenerator[GenerationChunk, None]:
        operation = self._register(request.operation_id)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._operation_timeout
        response: httpx.Response | None = None
        try:
            async with asyncio.timeout_at(deadline):
                wire_request = self._client.build_request(
                    "POST",
                    self.base_url.join("api/chat"),
                    json=_payload(request, stream=True, think=self._think),
                    timeout=self._timeout,
                )
                response = await self._client.send(
                    wire_request, stream=True, follow_redirects=False
                )
            operation.response = response
            self._status(response)
            lines = response.aiter_lines().__aiter__()
            while True:
                if operation.cancelled:
                    raise asyncio.CancelledError
                if loop.time() >= deadline:
                    raise TimeoutError
                operation.task = asyncio.current_task()
                try:
                    async with asyncio.timeout_at(deadline):
                        line = await anext(lines)
                except StopAsyncIteration:
                    raise ModelProviderError(
                        "Ollama stream ended without completion"
                    ) from None
                if not line.strip():
                    continue
                record = _record(json.loads(line), request)
                neutral = _response(record, request)
                if record.done:
                    await response.aclose()
                    self._finish(request.operation_id, operation)
                # A suspended iterator must not cancel unrelated consumer work.
                operation.task = None
                yield GenerationChunk(**neutral.model_dump(), is_final=record.done)
                if record.done:
                    return
        except (httpx.TimeoutException, httpx.NetworkError, TimeoutError):
            raise ProviderUnavailableError(
                "Ollama stream unavailable or timed out"
            ) from None
        except (httpx.HTTPError, ValueError, UnicodeError):
            raise ModelProviderError("Ollama invalid HTTP/stream record") from None
        finally:
            self._finish(request.operation_id, operation)
            if response is not None:
                await response.aclose()

    async def cancel(self, operation_id: str) -> CancellationResult:
        operation = self._active.get(operation_id)
        status = CancellationStatus.NOT_FOUND
        if operation is not None:
            operation.cancelled = True
            if operation.task is not None and not operation.task.done():
                if not operation.task.cancelling():
                    operation.task.cancel()
                status = CancellationStatus.ACCEPTED
            elif operation.task is None and operation.response is not None:
                await operation.response.aclose()
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
        self._closed = True
        tasks: set[asyncio.Task[object]] = set()
        try:
            async with asyncio.timeout(5):
                for operation_id, operation in tuple(self._active.items()):
                    operation.cancelled = True
                    if operation.task is not None and not operation.task.done():
                        if operation.task is asyncio.current_task():
                            raise ModelProviderError(
                                "Close Ollama outside its active request task"
                            )
                        if not operation.task.cancelling():
                            operation.task.cancel()
                        tasks.add(operation.task)
                    elif operation.response is not None:
                        await operation.response.aclose()
                        self._finish(operation_id, operation)
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            raise ModelProviderError("Ollama request cleanup timed out") from None
        finally:
            self._active.clear()
            if self._owns_client:
                async with asyncio.timeout(5):
                    await self._client.aclose()

    async def __aenter__(self) -> "OllamaProvider":
        self._ready()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
