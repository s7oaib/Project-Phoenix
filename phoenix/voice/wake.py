"""Provider-neutral wake evidence owned conceptually by voice (UTA display only).

Detections start nothing and grant no authority. Scores are detector evidence,
not a probability guarantee or identity/health/task confidence. Future adapters
own bounded operations, cooperative cancellation, cleanup and caller deadlines.
Device capture, VAD/STT/TTS, conversation/barge-in and startup remain separate.
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
    "WakePhrase",
    "DEFAULT_WAKE_PHRASE",
    "WakeWordCapabilities",
    "WakeWordRequest",
    "WakeWordDetection",
    "WakeWordProviderErrorCode",
    "WakeWordProviderError",
    "WakeWordProvider",
)


class WakeWordProviderErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    INVALID_RESPONSE = "invalid_response"
    UNSUPPORTED_PHRASE = "unsupported_phrase"
    UNSUPPORTED_AUDIO = "unsupported_audio"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    BUSY = "busy"
    CLOSED = "closed"
    WRONG_EVENT_LOOP = "wrong_event_loop"
    CLEANUP_FAILURE = "cleanup_failure"


class WakeWordProviderError(Exception):
    """Closed safe code only; backend diagnostics and audio never belong here."""

    def __init__(self, code: WakeWordProviderErrorCode) -> None:
        if not any(code is member for member in WakeWordProviderErrorCode):
            raise ValueError("invalid_input")
        self.code = code
        super().__init__(code.value)


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )
    _failure_code: ClassVar[WakeWordProviderErrorCode] = (
        WakeWordProviderErrorCode.INVALID_INPUT
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
            WakeWordProviderError,
            _AudioValidationError,
        ):
            raise WakeWordProviderError(cls._failure_code) from None

    @field_validator(
        "operation_id",
        "provider_id",
        "phrase_id",
        "stream_id",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def identifier(cls, value: object) -> str:
        return _identifier(value)


class WakePhrase(_Contract):
    phrase_id: str
    text: str

    @field_validator("phrase_id", "text", mode="before")
    @classmethod
    def phrase_text(cls, value: object) -> str:
        return _identifier(value, 128)


DEFAULT_WAKE_PHRASE = WakePhrase(phrase_id="rise_phoenix", text="Rise, Phoenix")


class WakeWordCapabilities(_Contract):
    """Technical support only; consumers must revalidate provider correlation."""

    _failure_code = WakeWordProviderErrorCode.INVALID_RESPONSE
    provider_id: str
    supported_phrases: tuple[WakePhrase, ...]
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    encoding: AudioEncoding
    supports_cancellation: bool = Field(strict=True)

    @field_validator("supported_phrases", mode="before")
    @classmethod
    def phrases(cls, value: object) -> tuple[WakePhrase, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("invalid_response")
        values = cast(Sequence[object], value)
        if not 1 <= len(values) <= 32:
            raise ValueError("invalid_response")
        return tuple(WakePhrase.model_validate(item) for item in values)

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

    @model_validator(mode="after")
    def unique_phrases(self) -> Self:
        if len({phrase.phrase_id for phrase in self.supported_phrases}) != len(
            self.supported_phrases
        ) or len({phrase.text for phrase in self.supported_phrases}) != len(
            self.supported_phrases
        ):
            raise WakeWordProviderError(WakeWordProviderErrorCode.INVALID_RESPONSE)
        return self

    def validate_provider(self, provider_id: str) -> None:
        """Check exact stored capabilities and identity, without provider selection."""
        capabilities = WakeWordCapabilities.model_validate(self)
        if capabilities.provider_id != _provider_id(provider_id):
            raise WakeWordProviderError(WakeWordProviderErrorCode.INVALID_RESPONSE)


class WakeWordRequest(_Contract):
    """Caller window only, with no backend options, audio repair or authority."""

    operation_id: str = Field(default_factory=lambda: str(uuid4()))
    phrase: WakePhrase
    frames: tuple[PcmAudioFrame, ...]

    @field_validator("phrase", mode="before")
    @classmethod
    def phrase_snapshot(cls, value: object) -> WakePhrase:
        return WakePhrase.model_validate(value)

    @field_validator("frames", mode="before")
    @classmethod
    def frame_snapshots(cls, value: object) -> tuple[PcmAudioFrame, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("invalid_input")
        values = cast(Sequence[object], value)
        if not 1 <= len(values) <= 32:
            raise ValueError("invalid_input")
        return tuple(PcmAudioFrame.model_validate(item) for item in values)

    @model_validator(mode="after")
    def window_integrity(self) -> Self:
        if sum(len(frame.pcm_bytes_for_provider()) for frame in self.frames) > 96000:
            raise WakeWordProviderError(WakeWordProviderErrorCode.INVALID_INPUT)
        first = self.frames[0]
        for previous, current in zip(self.frames, self.frames[1:]):
            if (
                current.stream_id != first.stream_id
                or current.sequence != previous.sequence + 1
                or current.captured_at < previous.captured_at
            ):
                raise WakeWordProviderError(WakeWordProviderErrorCode.INVALID_INPUT)
        return self


class WakeWordDetection(_Contract):
    """Untrusted detector evidence; detected is never derived from score."""

    _failure_code = WakeWordProviderErrorCode.INVALID_RESPONSE
    operation_id: str
    provider_id: str
    phrase_id: str
    stream_id: str
    start_sequence: Annotated[int, Field(strict=True, ge=0)]
    end_sequence: Annotated[int, Field(strict=True, ge=0)]
    detected: bool = Field(strict=True)
    score: float

    @field_validator("phrase_id", mode="before")
    @classmethod
    def phrase_identity(cls, value: object) -> str:
        return _identifier(value, 128)

    @field_validator("start_sequence", "end_sequence", mode="before")
    @classmethod
    def ordinary_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("invalid_response")
        return value

    @field_validator("score", mode="before")
    @classmethod
    def detector_score(cls, value: object) -> float:
        if type(value) is not float or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("invalid_response")
        return value

    @model_validator(mode="after")
    def range_integrity(self) -> Self:
        if self.end_sequence < self.start_sequence:
            raise WakeWordProviderError(WakeWordProviderErrorCode.INVALID_RESPONSE)
        return self

    def validate_request(self, request: WakeWordRequest, provider_id: str) -> None:
        """Revalidate both sides and exact range correlation; no action is started."""
        detection = WakeWordDetection.model_validate(self)
        if type(request) is not WakeWordRequest:
            raise WakeWordProviderError(WakeWordProviderErrorCode.INVALID_INPUT)
        accepted = WakeWordRequest.model_validate(request)
        provider = _provider_id(provider_id)
        if (
            detection.operation_id,
            detection.provider_id,
            detection.phrase_id,
            detection.stream_id,
            detection.start_sequence,
            detection.end_sequence,
        ) != (
            accepted.operation_id,
            provider,
            accepted.phrase.phrase_id,
            accepted.frames[0].stream_id,
            accepted.frames[0].sequence,
            accepted.frames[-1].sequence,
        ):
            raise WakeWordProviderError(WakeWordProviderErrorCode.INVALID_RESPONSE)


def _provider_id(value: object) -> str:
    try:
        return _identifier(value)
    except (ValueError, TypeError):
        raise WakeWordProviderError(WakeWordProviderErrorCode.INVALID_INPUT) from None


class WakeWordProvider(ABC):
    """Pure async boundary; no concrete wake engine, capture or allocated resource.

    Adapters own sorted immutable active IDs and bounded operation cleanup. Every
    capability/detection/cancellation result needs exact consumer revalidation and
    correlation. ACCEPTED acknowledges cancellation, not proven inference stop.
    The ABC imposes no global scheduling, retry, fallback or selection policy.
    """

    def __init__(self, provider_id: str) -> None:
        self._provider_id = _provider_id(provider_id)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    @abstractmethod
    def active_operation_ids(self) -> tuple[str, ...]:
        """Sorted immutable observational IDs only, with no task handles."""

    @abstractmethod
    async def capabilities(self) -> WakeWordCapabilities:
        """Return provider-correlated support evidence; no availability claim."""

    @abstractmethod
    async def detect(self, request: WakeWordRequest) -> WakeWordDetection:
        """Consume only supplied audio; return correlated evidence or a safe error."""

    @abstractmethod
    async def cancel(self, operation_id: str) -> CancellationResult:
        """Acknowledge this operation only; never globally shut down inference."""

    @abstractmethod
    async def aclose(self) -> None:
        """Explicit adapter-owned cleanup boundary; the base allocates nothing."""
