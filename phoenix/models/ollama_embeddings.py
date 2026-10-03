"""Bounded explicit Ollama embeddings; no discovery, storage or generation."""

import asyncio
import math
from typing import Self
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from phoenix.models.embeddings import (
    EmbeddingProvider,
    EmbeddingProviderError,
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingUnavailableError,
)
from phoenix.models.provider import NonnegativeInt


class _Metadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str | None = Field(default=None, strict=True)
    prompt_eval_count: NonnegativeInt | None = None
    total_duration: NonnegativeInt | None = None


def _base_url(value: str) -> httpx.URL:
    # Deliberately self-contained: PHX-011 private helpers remain private.
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
            "Invalid embedding base URL (HTTP(S), no credentials/query)"
        ) from None


def _response(data: object, request: EmbeddingRequest) -> EmbeddingResponse:
    try:
        if not isinstance(data, dict) or "error" in data:
            raise ValueError
        metadata = _Metadata.model_validate(data)
        if "model" in data and (
            not metadata.model
            or metadata.model.removesuffix(":latest")
            != request.model_id.removesuffix(":latest")
        ):
            raise ValueError
        vectors = data.get("embeddings")
        if (
            not isinstance(vectors, list)
            or len(vectors) != len(request.inputs)
            or any(not isinstance(vector, list) or not vector for vector in vectors)
        ):
            raise ValueError
        dimensions = len(vectors[0])
        if "dimensions" in data and (
            type(data["dimensions"]) is not int or data["dimensions"] != dimensions
        ):
            raise ValueError
        return EmbeddingResponse.model_validate(
            {
                "operation_id": request.operation_id,
                "provider_id": "ollama",
                "model_id": request.model_id,
                "embeddings": vectors,
                "dimensions": dimensions,
                "input_tokens": metadata.prompt_eval_count,
                "duration_ms": metadata.total_duration / 1_000_000
                if metadata.total_duration is not None
                else None,
            }
        )
    except (ValueError, OverflowError):
        raise EmbeddingProviderError("Ollama invalid embedding response") from None


class OllamaEmbeddingProvider(EmbeddingProvider):
    """One event loop; callers await/cancel embed before closing the provider.

    Owned clients disable ambient proxies and verify TLS. Borrowed clients are
    caller-configured/caller-owned; redirects and timeouts are enforced per call.
    No worker tasks or requests start at construction.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        *,
        timeout: httpx.Timeout | None = None,
        operation_timeout: float = 300,
        cleanup_timeout: float = 5,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__("ollama")
        self.base_url = _base_url(base_url)
        self._timeout = timeout or httpx.Timeout(connect=3, read=120, write=10, pool=5)
        for value in (
            *self._timeout.as_dict().values(),
            operation_timeout,
            cleanup_timeout,
        ):
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("Embedding timeouts must be finite and positive")
        if client is not None and transport is not None:
            raise ValueError("Inject either client or transport, not both")
        self._operation_timeout = operation_timeout
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
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def _ready(self) -> None:
        if self._closed:
            raise EmbeddingProviderError("Embedding provider closed")
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise EmbeddingProviderError(
                "Embedding provider belongs to another event loop"
            )
        self._loop = loop

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self._ready()
        try:
            request = EmbeddingRequest.model_validate(
                request.model_dump(warnings=False)
            )
        except ValidationError:
            raise EmbeddingProviderError("Invalid embedding request") from None
        try:
            async with asyncio.timeout(self._operation_timeout):
                response = await self._client.post(
                    self.base_url.join("api/embed"),
                    json={
                        "model": request.model_id,
                        "input": list(request.inputs),
                        # Ollama defaults to silent truncation; preserve caller text.
                        "truncate": False,
                    },
                    timeout=self._timeout,
                    follow_redirects=False,
                )
                response.raise_for_status()
                return _response(response.json(), request)
        except (httpx.NetworkError, httpx.TimeoutException, TimeoutError):
            raise EmbeddingUnavailableError(
                "Ollama embeddings unavailable or timed out"
            ) from None
        except (httpx.HTTPError, ValueError, UnicodeError, RuntimeError):
            raise EmbeddingProviderError(
                "Ollama invalid embedding HTTP/protocol"
            ) from None

    async def aclose(self) -> None:
        if self._closed and (not self._owns_client or self._client.is_closed):
            return
        self._closed = True
        if self._owns_client:
            try:
                async with asyncio.timeout(self._cleanup_timeout):
                    await self._client.aclose()
            except (TimeoutError, httpx.HTTPError, RuntimeError):
                raise EmbeddingProviderError(
                    "Embedding client cleanup failed"
                ) from None

    async def __aenter__(self) -> Self:
        self._ready()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
