"""Provider-neutral synthesis evidence owned by voice (UTA display metadata).

PHOENIX remains supreme. Declared original/licensed/consented provenance is
evidence, not independent rights/consent or speaker-identity proof. Text/profiles/
PCM confer no permission or task success. Explicit serialization/byte access is
sensitive caller data and must not be logged or persisted. No bundled profile,
engine, model, cloning sample, playback, cache or integration lives here. Coqui
XTTS remains an optional candidate requiring separate runtime/model authorization.
PHX-064 owns playback/conversation/barge-in; PHX-065 owns startup health.
"""

from __future__ import annotations

import base64
import binascii
from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import StrEnum
from hashlib import sha256
from typing import ClassVar, Self, cast
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    ValidationError,
    field_validator,
    model_validator,
)

from phoenix.models.provider import CancellationResult
from phoenix.voice.audio import _format_integer, _identifier, _stored_values

__all__ = (
    "TtsAudioEncoding",
    "VoiceProfileProvenance",
    "TtsProviderErrorCode",
    "TtsProviderError",
    "TtsVoiceProfile",
    "TtsCapabilities",
    "TtsRequest",
    "TtsAudio",
    "TtsProvider",
)


class TtsAudioEncoding(StrEnum):
    PCM_S16LE = "pcm_s16le"


class VoiceProfileProvenance(StrEnum):
    ORIGINAL = "original"
    LICENSED = "licensed"
    CONSENTED = "consented"


class TtsProviderErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    INVALID_RESPONSE = "invalid_response"
    UNSUPPORTED_VOICE = "unsupported_voice"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    BUSY = "busy"
    CLOSED = "closed"
    WRONG_EVENT_LOOP = "wrong_event_loop"
    CLEANUP_FAILURE = "cleanup_failure"


class TtsProviderError(Exception):
    """Closed safe code only; never text, PCM or backend diagnostics."""

    def __init__(self, code: TtsProviderErrorCode) -> None:
        if not any(code is member for member in TtsProviderErrorCode):
            raise ValueError("invalid_input")
        self.code = code
        super().__init__(code.value)


def _provider_id(value: object) -> str:
    try:
        return _identifier(value)
    except (ValueError, TypeError):
        raise TtsProviderError(TtsProviderErrorCode.INVALID_INPUT) from None


def _pcm(value: object) -> bytes:
    # Bound encoded allocation before strict decoding. This is raw PCM, not WAV.
    if type(value) is not str or not value or len(value) > 11184812:
        raise ValueError("invalid_response")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("invalid_response") from None
    if (
        not decoded
        or len(decoded) > 8388608
        or len(decoded) % 2
        or base64.b64encode(decoded).decode("ascii") != value
    ):
        raise ValueError("invalid_response")
    return decoded


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )
    _failure_code: ClassVar[TtsProviderErrorCode] = (
        TtsProviderErrorCode.INVALID_RESPONSE
    )

    @model_validator(mode="wrap")
    @classmethod
    def exact_snapshot(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        try:
            return handler(_stored_values(value, cls))
        except (
            ValidationError,
            ValueError,
            TypeError,
            AttributeError,
            OverflowError,
            RecursionError,
            TtsProviderError,
        ):
            raise TtsProviderError(cls._failure_code) from None

    @field_validator("operation_id", "provider_id", mode="before", check_fields=False)
    @classmethod
    def identifier(cls, value: object) -> str:
        return _identifier(value)

    @field_validator(
        "profile_id", "voice_profile_id", mode="before", check_fields=False
    )
    @classmethod
    def profile_identifier(cls, value: object) -> str:
        return _identifier(value, 128)

    @field_validator("language_hint", mode="before", check_fields=False)
    @classmethod
    def language_token(cls, value: object) -> str | None:
        return None if value is None else _identifier(value, 32)

    @field_validator("sample_rate_hz", mode="before", check_fields=False)
    @classmethod
    def sample_rate(cls, value: object) -> int:
        if type(value) is not int or not 8000 <= value <= 48000:
            raise ValueError("invalid_response")
        return value

    @field_validator("channels", mode="before", check_fields=False)
    @classmethod
    def channel_count(cls, value: object) -> int:
        return _format_integer(value, 1)

    @field_validator("sample_width_bytes", mode="before", check_fields=False)
    @classmethod
    def sample_width(cls, value: object) -> int:
        return _format_integer(value, 2)

    @field_validator("encoding", mode="before", check_fields=False)
    @classmethod
    def encoding_value(cls, value: object) -> TtsAudioEncoding:
        if value is TtsAudioEncoding.PCM_S16LE or (
            type(value) is str and value == "pcm_s16le"
        ):
            return TtsAudioEncoding.PCM_S16LE
        raise ValueError("invalid_response")


class TtsVoiceProfile(_Contract):
    """Declared provenance and language evidence, never proof of rights or identity."""

    profile_id: str
    display_name: str
    provenance: VoiceProfileProvenance
    languages: tuple[str, ...]
    is_primary: bool = Field(strict=True)

    @field_validator("display_name", mode="before")
    @classmethod
    def display(cls, value: object) -> str:
        return _identifier(value, 256)

    @field_validator("provenance", mode="before")
    @classmethod
    def provenance_value(cls, value: object) -> VoiceProfileProvenance:
        for member in VoiceProfileProvenance:
            if value is member or (type(value) is str and value == member.value):
                return member
        raise ValueError("invalid_response")

    @field_validator("languages", mode="before")
    @classmethod
    def language_snapshots(cls, value: object) -> tuple[str, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("invalid_response")
        values = cast(Sequence[object], value)
        if not 1 <= len(values) <= 32:
            raise ValueError("invalid_response")
        languages = tuple(_identifier(item, 32) for item in values)
        if len(set(languages)) != len(languages):
            raise ValueError("invalid_response")
        return languages


class TtsCapabilities(_Contract):
    """Technical/declarative evidence; primary must be original, no playback."""

    provider_id: str
    voices: tuple[TtsVoiceProfile, ...]
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    encoding: TtsAudioEncoding
    supports_language_hint: bool = Field(strict=True)
    supports_cancellation: bool = Field(strict=True)

    @field_validator("voices", mode="before")
    @classmethod
    def voice_snapshots(cls, value: object) -> tuple[TtsVoiceProfile, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("invalid_response")
        values = cast(Sequence[object], value)
        if not 1 <= len(values) <= 64:
            raise ValueError("invalid_response")
        return tuple(TtsVoiceProfile.model_validate(item) for item in values)

    @model_validator(mode="after")
    def primary_integrity(self) -> Self:
        primary = tuple(voice for voice in self.voices if voice.is_primary)
        if (
            len({voice.profile_id for voice in self.voices}) != len(self.voices)
            or len(primary) != 1
            or primary[0].provenance is not VoiceProfileProvenance.ORIGINAL
        ):
            raise TtsProviderError(TtsProviderErrorCode.INVALID_RESPONSE)
        return self

    def validate_provider(self, provider_id: str) -> None:
        evidence = TtsCapabilities.model_validate(self)
        if evidence.provider_id != _provider_id(provider_id):
            raise TtsProviderError(TtsProviderErrorCode.INVALID_RESPONSE)


class TtsRequest(_Contract):
    """Exact supplied text and explicit voice/language reference, without options."""

    _failure_code = TtsProviderErrorCode.INVALID_INPUT
    operation_id: str = Field(default_factory=lambda: str(uuid4()))
    text: str = Field(repr=False)
    voice_profile_id: str
    language_hint: str | None = None

    @field_validator("text", mode="before")
    @classmethod
    def exact_text(cls, value: object) -> str:
        if (
            type(value) is not str
            or "\0" in value
            or not value.strip()
            or len(value) > 4096
        ):
            raise ValueError("invalid_input")
        value.encode("utf-8")
        return value

    def validate_capabilities(self, capabilities: TtsCapabilities) -> None:
        request = TtsRequest.model_validate(self)
        evidence = TtsCapabilities.model_validate(capabilities)
        selected = next(
            (
                voice
                for voice in evidence.voices
                if voice.profile_id == request.voice_profile_id
            ),
            None,
        )
        if selected is None:
            raise TtsProviderError(TtsProviderErrorCode.UNSUPPORTED_VOICE)
        if request.language_hint is not None and (
            not evidence.supports_language_hint
            or request.language_hint not in selected.languages
        ):
            raise TtsProviderError(TtsProviderErrorCode.UNSUPPORTED_LANGUAGE)


class TtsAudio(_Contract):
    """Bounded canonical raw PCM; hash/duration prove no semantic synthesis truth."""

    operation_id: str
    provider_id: str
    voice_profile_id: str
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    encoding: TtsAudioEncoding
    pcm_base64: str = Field(repr=False)
    pcm_sha256: str

    @field_validator("pcm_base64", mode="before")
    @classmethod
    def canonical_pcm(cls, value: object) -> str:
        _pcm(value)
        assert type(value) is str
        return value

    @field_validator("pcm_sha256", mode="before")
    @classmethod
    def digest_text(cls, value: object) -> str:
        if (
            type(value) is not str
            or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)
        ):
            raise ValueError("invalid_response")
        return value

    @model_validator(mode="after")
    def pcm_integrity(self) -> Self:
        decoded = _pcm(self.pcm_base64)
        if (
            self.pcm_sha256 != sha256(decoded).hexdigest()
            or len(decoded)
            / (self.sample_rate_hz * self.channels * self.sample_width_bytes)
            > 120.0
        ):
            raise TtsProviderError(TtsProviderErrorCode.INVALID_RESPONSE)
        return self

    def pcm_bytes_for_playback(self) -> bytes:
        """Return exact raw PCM after revalidation, without playback/I/O/cache."""
        accepted = TtsAudio.model_validate(self)
        return _pcm(accepted.pcm_base64)

    @property
    def duration_seconds(self) -> float:
        accepted = TtsAudio.model_validate(self)
        return len(_pcm(accepted.pcm_base64)) / (
            accepted.sample_rate_hz * accepted.channels * accepted.sample_width_bytes
        )

    def validate_request(
        self, request: TtsRequest, capabilities: TtsCapabilities
    ) -> None:
        accepted = TtsRequest.model_validate(request)
        evidence = TtsCapabilities.model_validate(capabilities)
        accepted.validate_capabilities(evidence)
        audio = TtsAudio.model_validate(self)
        if (
            audio.operation_id,
            audio.provider_id,
            audio.voice_profile_id,
            audio.sample_rate_hz,
            audio.channels,
            audio.sample_width_bytes,
            audio.encoding,
        ) != (
            accepted.operation_id,
            evidence.provider_id,
            accepted.voice_profile_id,
            evidence.sample_rate_hz,
            evidence.channels,
            evidence.sample_width_bytes,
            evidence.encoding,
        ):
            raise TtsProviderError(TtsProviderErrorCode.INVALID_RESPONSE)


class TtsProvider(ABC):
    """Pure boundary; adapters own bounded deadlines, state and cooperative cleanup.

    Consumers exact-revalidate capabilities/audio/cancellation and correlation.
    ACCEPTED acknowledges cancellation, not proven native inference cessation.
    No routing/retry/fallback/playback or conversation policy belongs here.
    """

    def __init__(self, provider_id: str) -> None:
        self._provider_id = _provider_id(provider_id)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    @abstractmethod
    def active_operation_ids(self) -> tuple[str, ...]:
        """Sorted immutable observational IDs, without handles or authority."""

    @abstractmethod
    async def capabilities(self) -> TtsCapabilities:
        """Return exact provider-correlated technical/declarative evidence."""

    @abstractmethod
    async def synthesize(self, request: TtsRequest) -> TtsAudio:
        """Consume validated text/voice/language; return bounded correlated PCM."""

    @abstractmethod
    async def cancel(self, operation_id: str) -> CancellationResult:
        """Operation-scoped acknowledgement only; never global shutdown."""

    @abstractmethod
    async def aclose(self) -> None:
        """Explicit adapter cleanup; base allocates no runtime resources."""
