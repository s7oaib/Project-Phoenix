"""Provider-neutral contracts only: no probes, runtime, or execution authority."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    field_validator,
)

Identifier = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonnegativeInt = Annotated[int, Field(strict=True, ge=0)]
NonnegativeFloat = Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]


class ProviderModel(BaseModel):
    """Frozen data without arbitrary metadata, coercion, or runtime ownership."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ModelMessage(ProviderModel):
    """Text content only; the tool role does not execute or authorize anything."""

    role: MessageRole
    text: str = Field(strict=True)


class GenerationRequest(ProviderModel):
    operation_id: Identifier = Field(default_factory=lambda: str(uuid4()))
    model_id: Identifier
    messages: tuple[ModelMessage, ...]
    temperature: NonnegativeFloat | None = None
    max_output_tokens: PositiveInt | None = None
    stop_sequences: tuple[Identifier, ...] = ()


class ModelCapabilities(ProviderModel):
    """Technical support only; never permissions or model-selection policy."""

    provider_id: Identifier
    model_id: Identifier
    supports_streaming: bool = Field(strict=True)
    supports_structured_output: bool = Field(strict=True)
    supports_tool_use: bool = Field(strict=True)
    supports_vision: bool = Field(strict=True)
    supports_cancellation: bool = Field(strict=True)
    context_limit_tokens: PositiveInt | None = None


class ProviderHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class ProviderHealth(ProviderModel):
    """Provider-local health, never overall PHOENIX readiness."""

    provider_id: Identifier
    status: ProviderHealthStatus
    checked_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    detail: str | None = Field(default=None, strict=True)

    @field_validator("checked_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class TokenUsage(ProviderModel):
    """Reported counts only; unknown totals are not inferred."""

    input_tokens: NonnegativeInt | None = None
    output_tokens: NonnegativeInt | None = None
    total_tokens: NonnegativeInt | None = None


class GenerationMetrics(ProviderModel):
    ttft_ms: NonnegativeFloat | None = None
    duration_ms: NonnegativeFloat | None = None
    tokens_per_second: NonnegativeFloat | None = None


class GenerationResponse(ProviderModel):
    operation_id: Identifier
    provider_id: Identifier
    model_id: Identifier
    text: str = Field(strict=True)
    finish_reason: Identifier | None = None
    usage: TokenUsage | None = None
    metrics: GenerationMetrics | None = None


class GenerationChunk(GenerationResponse):
    """Incremental text; final chunks may contain no text and carry metrics."""

    is_final: bool = Field(default=False, strict=True)


class CancellationStatus(StrEnum):
    ACCEPTED = "accepted"
    UNSUPPORTED = "unsupported"
    NOT_FOUND = "not_found"


class CancellationResult(ProviderModel):
    """Accepted is an acknowledgement, not proof that execution has stopped."""

    operation_id: Identifier
    provider_id: Identifier
    status: CancellationStatus


class ModelProviderError(Exception):
    """Provider failures propagate; implementations must not invent success."""


class ProviderUnavailableError(ModelProviderError):
    """The provider cannot serve the requested operation."""


class ModelProvider(ABC):
    """Backend adapters implement this boundary without exposing SDK objects.

    Adapters call super().__init__ with their stable ID. Methods return matching
    provider/model/operation identities; no correlation or health is fabricated.
    Stream is an async iterator directly, not a coroutine requiring an await.
    Implementations own bounded I/O and operation-scoped cancellation; this ABC
    starts no work. Capabilities and generated text confer no execution authority.
    """

    def __init__(self, provider_id: str) -> None:
        self._provider_id = TypeAdapter(Identifier).validate_python(provider_id)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @abstractmethod
    async def health(self) -> ProviderHealth:
        """Report provider-local health; errors remain explicit."""

    @abstractmethod
    async def capabilities(self, model_id: str) -> ModelCapabilities:
        """Describe support for this provider/model without choosing a model."""

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Generate content correlated to the request or raise a provider error."""

    @abstractmethod
    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        """Yield ordered incremental chunks, including completion evidence."""

    @abstractmethod
    async def cancel(self, operation_id: str) -> CancellationResult:
        """Request cancellation for one operation, never global shutdown."""
