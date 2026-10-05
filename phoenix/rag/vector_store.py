"""Neutral vector/provenance evidence only; no source or inference authority."""

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from math import isfinite
from typing import Annotated, Self, TypeVar

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)

_Identifier = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Hash = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    ),
]
_identifier = TypeAdapter(_Identifier)
_Model = TypeVar("_Model", bound=BaseModel)


class VectorStoreError(ValueError):
    """Explicit safe failure without vectors, SQL, paths or source data."""


class VectorInputError(VectorStoreError):
    """Invalid exact input or configuration."""


class VectorConflictError(VectorStoreError):
    """Durable ID or source/model key already exists; no overwrite."""


class VectorDimensionError(VectorStoreError):
    """Input dimension disagrees with the explicit stored embedding space."""


class VectorCorruptionError(VectorStoreError):
    """Invalid stored evidence; never silently skipped or repaired."""


class VectorPersistenceError(VectorStoreError):
    """Database operation failed; no automatic retry or repair."""


class VectorSearchLimitError(VectorStoreError):
    """Exact filtered candidate set exceeds the configured bound."""


def _revalidate(value: object, contract: type[_Model]) -> _Model:
    if type(value) is not contract or not isinstance(value, BaseModel):
        raise ValueError("Expected an exact vector contract")
    return contract.model_validate({**vars(value), **(value.__pydantic_extra__ or {})})


def _vector(value: object) -> tuple[float, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise ValueError("Vector requires a nonempty ordered sequence")
    result: list[float] = []
    for component in value:
        if type(component) not in (int, float):
            raise ValueError("Vector components must be ordinary numbers")
        try:
            number = float(component)
        except (ValueError, OverflowError):
            raise ValueError("Vector components must be finite") from None
        if not isfinite(number):
            raise ValueError("Vector components must be finite")
        result.append(number)
    # Avoid squared-norm overflow/underflow: a finite vector is zero iff all are zero.
    if not any(number != 0 for number in result):
        raise ValueError("Zero-norm vector is invalid")
    return tuple(result)


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
    )

    @model_validator(mode="wrap")
    @classmethod
    def exact_snapshot(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        if isinstance(value, BaseModel):
            if type(value) is not cls:
                raise ValueError("Expected an exact vector contract")
            value = {**vars(value), **(value.__pydantic_extra__ or {})}
        return handler(value)

    @field_validator("vector", mode="before", check_fields=False)
    @classmethod
    def finite_nonzero_vector(cls, value: object) -> tuple[float, ...]:
        return _vector(value)


class VectorRecord(_Contract):
    id: _Identifier
    source_kind: _Identifier
    source_id: _Identifier
    source_hash: _Hash
    provider_id: _Identifier
    model_id: _Identifier
    dimensions: Annotated[int, Field(strict=True, ge=1)]
    vector: tuple[float, ...] = Field(repr=False)
    created_at: AwareDatetime

    @field_validator("created_at", mode="before")
    @classmethod
    def timestamp_input(cls, value: object) -> object:
        if not isinstance(value, (datetime, str)):
            raise ValueError(
                "Timestamp requires an aware datetime or ISO representation"
            )
        return value

    @field_validator("created_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def dimension_agreement(self) -> Self:
        if len(self.vector) != self.dimensions:
            raise ValueError("Dimensions contradict vector shape")
        return self


class VectorBatch(_Contract):
    records: Annotated[tuple[VectorRecord, ...], Field(min_length=1)]

    @field_validator("records", mode="before")
    @classmethod
    def exact_records(cls, value: object) -> tuple[VectorRecord, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("Batch requires an ordered sequence")
        return tuple(_revalidate(record, VectorRecord) for record in value)

    @model_validator(mode="after")
    def consistent_evidence(self) -> Self:
        ids: set[str] = set()
        keys: set[tuple[str, str, str, str]] = set()
        spaces: dict[tuple[str, str], int] = {}
        for record in self.records:
            key = (
                record.source_kind,
                record.source_id,
                record.provider_id,
                record.model_id,
            )
            space = (record.provider_id, record.model_id)
            if record.id in ids or key in keys:
                raise ValueError("Duplicate batch identity")
            if space in spaces and spaces[space] != record.dimensions:
                raise ValueError("Inconsistent batch embedding-space dimensions")
            ids.add(record.id)
            keys.add(key)
            spaces[space] = record.dimensions
        return self


class VectorSearchQuery(_Contract):
    provider_id: _Identifier
    model_id: _Identifier
    vector: tuple[float, ...] = Field(repr=False)
    limit: Annotated[int, Field(strict=True, ge=1, le=100)]
    source_kind: _Identifier | None = None
    source_ids: Annotated[tuple[_Identifier, ...], Field(max_length=2000)] | None = None

    @field_validator("source_ids", mode="before")
    @classmethod
    def ordered_ids(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, (tuple, list)):
            raise ValueError("Source IDs require an ordered sequence")
        return tuple(value)

    @field_validator("source_ids")
    @classmethod
    def unique_ids(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("Duplicate source IDs")
        return value


class VectorMatch(_Contract):
    record_id: _Identifier
    source_kind: _Identifier
    source_id: _Identifier
    source_hash: _Hash
    provider_id: _Identifier
    model_id: _Identifier
    score: float = Field(strict=True, allow_inf_nan=False, ge=-1, le=1)

    @field_validator("score", mode="before")
    @classmethod
    def ordinary_score(cls, value: object) -> object:
        if type(value) not in (int, float):
            raise ValueError("Score must be an ordinary number")
        return value


class VectorStore(ABC):
    @abstractmethod
    def add(self, record: VectorRecord) -> VectorRecord:
        """Insert explicit evidence without overwrite or provider work."""

    @abstractmethod
    def add_many(self, records: tuple[VectorRecord, ...]) -> tuple[VectorRecord, ...]:
        """Commit one nonempty batch atomically, preserving caller order."""

    @abstractmethod
    def get(self, record_id: str) -> VectorRecord | None:
        """Return detached exact evidence, None when absent, or explicit failure."""

    @abstractmethod
    def delete(self, record_id: str) -> bool:
        """Delete only the exact embedding ID; never a source or event."""

    @abstractmethod
    def search(self, query: VectorSearchQuery) -> tuple[VectorMatch, ...]:
        """Bounded similarity evidence; no source resolution or retrieval policy."""
