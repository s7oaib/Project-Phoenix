"""Neutral text-to-vector data contracts, independent of generation or storage."""

from abc import ABC, abstractmethod
from typing import Annotated, Self
from uuid import uuid4

from pydantic import (
    BeforeValidator,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

from phoenix.models.provider import (
    Identifier,
    NonnegativeFloat,
    NonnegativeInt,
    PositiveInt,
    ProviderModel,
)

_Text = Annotated[str, Field(strict=True)]


def _numeric(value: object) -> object:
    if type(value) not in (int, float):
        raise ValueError("Embedding components must be numeric values")
    return value


_Number = Annotated[
    float, BeforeValidator(_numeric), Field(strict=True, allow_inf_nan=False)
]
_Vector = Annotated[tuple[_Number, ...], Field(min_length=1)]


class EmbeddingRequest(ProviderModel):
    operation_id: Identifier = Field(default_factory=lambda: str(uuid4()))
    model_id: Identifier
    inputs: tuple[_Text, ...] = Field(min_length=1)

    @field_validator("inputs")
    @classmethod
    def nonblank_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not text.strip() for text in value):
            raise ValueError("Embedding inputs must contain nonblank text")
        return value


class EmbeddingResponse(ProviderModel):
    """Dimensions describe actual vectors; adapters verify request batch count."""

    operation_id: Identifier
    provider_id: Identifier
    model_id: Identifier
    embeddings: tuple[_Vector, ...] = Field(min_length=1)
    dimensions: PositiveInt
    input_tokens: NonnegativeInt | None = None
    duration_ms: NonnegativeFloat | None = None

    @model_validator(mode="after")
    def uniform_dimensions(self) -> Self:
        if any(len(vector) != self.dimensions for vector in self.embeddings):
            raise ValueError("Embedding dimensions contradict vector shape")
        return self


class EmbeddingProviderError(Exception):
    """Explicit safe embedding failure; no fabricated vectors."""


class EmbeddingUnavailableError(EmbeddingProviderError):
    """The embedding provider is unreachable or timed out."""


class EmbeddingProvider(ABC):
    """Adapters preserve input order, count and operation/model correlation."""

    def __init__(self, provider_id: str) -> None:
        self._provider_id = TypeAdapter(Identifier).validate_python(provider_id)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @abstractmethod
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """Return validated vectors corresponding 1:1 to the ordered inputs."""
