"""Memory-only technical snapshots; no discovery, routing, or authority."""

from datetime import UTC, datetime
from typing import Self

from pydantic import AwareDatetime, Field, TypeAdapter, field_validator, model_validator

from phoenix.models.provider import (
    Identifier,
    ModelCapabilities,
    ModelProvider,
    ModelProviderError,
    PositiveInt,
    ProviderModel,
)


class ModelCapabilityRecord(ProviderModel):
    """Immutable neutral evidence, independent of availability or residency."""

    provider_id: Identifier
    model_id: Identifier
    capabilities: ModelCapabilities
    refreshed_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("capabilities", mode="before")
    @classmethod
    def validate_snapshot(cls, value: object) -> object:
        # Revalidate even model_copy/model_construct inputs; retain no caller state.
        if isinstance(value, ModelCapabilities):
            return ModelCapabilities.model_validate(value.model_dump(warnings=False))
        return value

    @field_validator("refreshed_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def identity_agrees(self) -> Self:
        if (self.provider_id, self.model_id) != (
            self.capabilities.provider_id,
            self.capabilities.model_id,
        ):
            raise ValueError("Capability record identity contradicts its snapshot")
        return self


class CapabilityRequirements(ProviderModel):
    """True requires support; false imposes no constraint. No ranking policy."""

    requires_streaming: bool = Field(default=False, strict=True)
    requires_structured_output: bool = Field(default=False, strict=True)
    requires_tool_use: bool = Field(default=False, strict=True)
    requires_vision: bool = Field(default=False, strict=True)
    requires_cancellation: bool = Field(default=False, strict=True)
    min_context_tokens: PositiveInt | None = None


def _key(provider_id: str, model_id: str) -> tuple[str, str]:
    identifier = TypeAdapter(Identifier)
    return (
        identifier.validate_python(provider_id),
        identifier.validate_python(model_id),
    )


class ModelCapabilityRegistry:
    """Explicit snapshots for one process/event loop, initially empty.

    Synchronous mutations contain no awaits. Refresh awaits only the supplied
    provider, validates, then commits atomically: last successful completion wins.
    Failure or cancellation retains prior state. No locks or workers are needed;
    concurrent threads/processes are outside this registry's contract.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], ModelCapabilityRecord] = {}

    def register(self, capabilities: ModelCapabilities) -> ModelCapabilityRecord:
        snapshot = ModelCapabilities.model_validate(
            capabilities.model_dump(warnings=False)
        )
        record = ModelCapabilityRecord(
            provider_id=snapshot.provider_id,
            model_id=snapshot.model_id,
            capabilities=snapshot,
        )
        self._records[(record.provider_id, record.model_id)] = record
        return record

    def get(self, provider_id: str, model_id: str) -> ModelCapabilityRecord | None:
        return self._records.get(_key(provider_id, model_id))

    def remove(self, provider_id: str, model_id: str) -> bool:
        return self._records.pop(_key(provider_id, model_id), None) is not None

    def clear(self) -> None:
        self._records.clear()

    def list_records(self) -> tuple[ModelCapabilityRecord, ...]:
        return tuple(self._records[key] for key in sorted(self._records))

    def matching(
        self, requirements: CapabilityRequirements
    ) -> tuple[ModelCapabilityRecord, ...]:
        requirements = CapabilityRequirements.model_validate(
            requirements.model_dump(warnings=False)
        )
        return tuple(
            record
            for record in self.list_records()
            if all(
                not required or supported
                for required, supported in (
                    (
                        requirements.requires_streaming,
                        record.capabilities.supports_streaming,
                    ),
                    (
                        requirements.requires_structured_output,
                        record.capabilities.supports_structured_output,
                    ),
                    (
                        requirements.requires_tool_use,
                        record.capabilities.supports_tool_use,
                    ),
                    (requirements.requires_vision, record.capabilities.supports_vision),
                    (
                        requirements.requires_cancellation,
                        record.capabilities.supports_cancellation,
                    ),
                )
            )
            and (
                requirements.min_context_tokens is None
                or record.capabilities.context_limit_tokens is not None
                and record.capabilities.context_limit_tokens
                >= requirements.min_context_tokens
            )
        )

    async def refresh(
        self, provider: ModelProvider, model_id: str
    ) -> ModelCapabilityRecord:
        provider_id, model_id = _key(provider.provider_id, model_id)
        result = await provider.capabilities(model_id)
        snapshot = ModelCapabilities.model_validate(result.model_dump(warnings=False))
        if (snapshot.provider_id, snapshot.model_id) != (provider_id, model_id):
            raise ModelProviderError(
                "Capability refresh returned contradictory identity"
            )
        return self.register(snapshot)
