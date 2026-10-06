"""Provider-neutral transcription evidence owned by voice (UTA display only).

PHOENIX remains supreme. Caller supplies already-collected PCM; PHX-064 owns
utterance selection/conversation. Text, language and confidence are evidence,
not authentication, intent or permission. Explicit PCM/JSON access is sensitive
caller data and must not be logged or persisted. No concrete engine, capture,
segmentation, translation, streaming partial text or word timestamps exist here.
Future adapters own deadlines, bounded operation state and cooperative cleanup.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import StrEnum
from typing import Annotated, ClassVar, Self, cast
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
from phoenix.voice.audio import (
    AudioEncoding,
    PcmAudioFrame,
    _AudioValidationError,
    _encoding,
    _format_integer,
    _identifier,
    _stored_values,
)

__all__ = (
    "SttProviderErrorCode",
    "SttProviderError",
    "SttCapabilities",
    "SttRequest",
    "SttTranscript",
    "SttProvider",
)


class SttProviderErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    INVALID_RESPONSE = "invalid_response"
    UNSUPPORTED_AUDIO = "unsupported_audio"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    BUSY = "busy"
    CLOSED = "closed"
    WRONG_EVENT_LOOP = "wrong_event_loop"
    CLEANUP_FAILURE = "cleanup_failure"


class SttProviderError(Exception):
    """Closed safe code only; never audio, transcript or backend diagnostics."""

    def __init__(self, code: SttProviderErrorCode) -> None:
        if not any(code is member for member in SttProviderErrorCode):
            raise ValueError("invalid_input")
        self.code = code
        super().__init__(code.value)


def _language(value: object) -> str | None:
    return None if value is None else _identifier(value, 32)


def _id(value: object) -> str:
    try:
        return _identifier(value)
    except (ValueError, TypeError):
        raise SttProviderError(SttProviderErrorCode.INVALID_INPUT) from None


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )
    _failure_code: ClassVar[SttProviderErrorCode] = SttProviderErrorCode.INVALID_INPUT

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
            _AudioValidationError,
            SttProviderError,
        ):
            raise SttProviderError(cls._failure_code) from None

    @field_validator(
        "operation_id", "provider_id", "stream_id", mode="before", check_fields=False
    )
    @classmethod
    def identifier(cls, value: object) -> str:
        return _identifier(value)

    @field_validator("language_hint", "language", mode="before", check_fields=False)
    @classmethod
    def language_token(cls, value: object) -> str | None:
        return _language(value)


class SttCapabilities(_Contract):
    """Technical support only, without provider/language selection or authority."""

    _failure_code = SttProviderErrorCode.INVALID_RESPONSE
    provider_id: str
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    encoding: AudioEncoding
    supports_language_hint: bool = Field(strict=True)
    supports_language_detection: bool = Field(strict=True)
    supports_cancellation: bool = Field(strict=True)

    @field_validator("sample_rate_hz", mode="before")
    @classmethod
    def sample_rate(cls, value: object) -> int:
        return _format_integer(value, 16000)

    @field_validator("channels", mode="before")
    @classmethod
    def channel_count(cls, value: object) -> int:
        return _format_integer(value, 1)

    @field_validator("sample_width_bytes", mode="before")
    @classmethod
    def sample_width(cls, value: object) -> int:
        return _format_integer(value, 2)

    @field_validator("encoding", mode="before")
    @classmethod
    def encoding_value(cls, value: object) -> AudioEncoding:
        return _encoding(value)

    def validate_provider(self, provider_id: str) -> None:
        evidence = SttCapabilities.model_validate(self)
        if evidence.provider_id != _id(provider_id):
            raise SttProviderError(SttProviderErrorCode.INVALID_RESPONSE)


class SttRequest(_Contract):
    """One bounded caller window; language hint neither selects nor guarantees."""

    operation_id: str = Field(default_factory=lambda: str(uuid4()))
    frames: tuple[PcmAudioFrame, ...]
    language_hint: str | None = None

    @field_validator("frames", mode="before")
    @classmethod
    def frame_snapshots(cls, value: object) -> tuple[PcmAudioFrame, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("invalid_input")
        values = cast(Sequence[object], value)
        if not 1 <= len(values) <= 256:
            raise ValueError("invalid_input")
        return tuple(PcmAudioFrame.model_validate(item) for item in values)

    @model_validator(mode="after")
    def window_integrity(self) -> Self:
        if sum(len(frame.pcm_bytes_for_provider()) for frame in self.frames) > 960000:
            raise SttProviderError(SttProviderErrorCode.INVALID_INPUT)
        stream = self.frames[0].stream_id
        for previous, current in zip(self.frames, self.frames[1:]):
            if (
                current.stream_id != stream
                or current.sequence != previous.sequence + 1
                or current.captured_at < previous.captured_at
            ):
                raise SttProviderError(SttProviderErrorCode.INVALID_INPUT)
        return self

    def pcm_bytes_for_provider(self) -> bytes:
        """Exact raw S16LE in source order; revalidate, decode, join without caching."""
        accepted = SttRequest.model_validate(self)
        return b"".join(frame.pcm_bytes_for_provider() for frame in accepted.frames)

    @property
    def audio_duration_seconds(self) -> float:
        """Decoded bytes / 32000.0, without clock, rounding, inference or retention."""
        accepted = SttRequest.model_validate(self)
        return (
            sum(len(frame.pcm_bytes_for_provider()) for frame in accepted.frames)
            / 32000.0
        )


class SttTranscript(_Contract):
    """Exact provider text, including empty evidence, never authenticated approval."""

    _failure_code = SttProviderErrorCode.INVALID_RESPONSE
    operation_id: str
    provider_id: str
    stream_id: str
    start_sequence: Annotated[int, Field(strict=True, ge=0)]
    end_sequence: Annotated[int, Field(strict=True, ge=0)]
    text: str = Field(repr=False)
    language: str | None = None
    language_confidence: float | None = None

    @field_validator("start_sequence", "end_sequence", mode="before")
    @classmethod
    def ordinary_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("invalid_response")
        return value

    @field_validator("text", mode="before")
    @classmethod
    def exact_text(cls, value: object) -> str:
        if type(value) is not str or "\0" in value or len(value) > 20000:
            raise ValueError("invalid_response")
        value.encode("utf-8")
        return value

    @field_validator("language_confidence", mode="before")
    @classmethod
    def confidence(cls, value: object) -> float | None:
        if value is None:
            return None
        if type(value) is not float or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("invalid_response")
        return value

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if self.end_sequence < self.start_sequence or (
            self.language_confidence is not None and self.language is None
        ):
            raise SttProviderError(SttProviderErrorCode.INVALID_RESPONSE)
        return self

    def validate_request(self, request: SttRequest, provider_id: str) -> None:
        """Revalidate all stored evidence; hint never fabricates/constrains language."""
        evidence = SttTranscript.model_validate(self)
        if type(request) is not SttRequest:
            raise SttProviderError(SttProviderErrorCode.INVALID_INPUT)
        accepted = SttRequest.model_validate(request)
        if (
            evidence.operation_id,
            evidence.provider_id,
            evidence.stream_id,
            evidence.start_sequence,
            evidence.end_sequence,
        ) != (
            accepted.operation_id,
            _id(provider_id),
            accepted.frames[0].stream_id,
            accepted.frames[0].sequence,
            accepted.frames[-1].sequence,
        ):
            raise SttProviderError(SttProviderErrorCode.INVALID_RESPONSE)


class SttProvider(ABC):
    """Pure boundary, with no engine, allocated resource or provider selection.

    Adapters own bounded active state and scoped cancellation/cleanup. Consumers
    exact-revalidate and correlate capabilities/transcripts/cancellation results.
    ACCEPTED acknowledges cancellation, not proven native inference cessation.
    No capture, wake/VAD, retry, fallback, TTS or conversation action belongs here.
    """

    def __init__(self, provider_id: str) -> None:
        self._provider_id = _id(provider_id)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    @abstractmethod
    def active_operation_ids(self) -> tuple[str, ...]:
        """Sorted immutable observational IDs, without task handles or authority."""

    @abstractmethod
    async def capabilities(self) -> SttCapabilities:
        """Return exact provider-correlated technical evidence."""

    @abstractmethod
    async def transcribe(self, request: SttRequest) -> SttTranscript:
        """Consume only supplied PCM; return correlated evidence or a safe error."""

    @abstractmethod
    async def cancel(self, operation_id: str) -> CancellationResult:
        """Operation-scoped acknowledgement only; never global shutdown."""

    @abstractmethod
    async def aclose(self) -> None:
        """Explicit adapter cleanup; the base allocates no runtime resource."""
