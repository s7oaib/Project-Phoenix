"""Frame-level VAD evidence owned conceptually by voice (UTA display only).

PHOENIX remains supreme. Energy is an amplitude gate, not semantic speech truth
or calibrated probability: noise/music can trigger it and quiet speech can fail.
Caller owns capture, exact chunking/timing and deadlines. PHX-064 owns
debounce/hangover, segments, conversation and barge-in. No PCM is
retained, logged or persisted; classification performs bounded local CPU work.
"""

from __future__ import annotations

import asyncio
import math
import struct
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, ClassVar, Self, TypeVar, cast
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    ValidationError,
    field_validator,
    model_validator,
)

from phoenix.models.provider import CancellationResult, CancellationStatus
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
    "VadActivity",
    "VadFrameDuration",
    "VadProviderErrorCode",
    "VadProviderError",
    "VadCapabilities",
    "VadRequest",
    "VadDecision",
    "VadProvider",
    "DEFAULT_ENERGY_VAD_THRESHOLD_DBFS",
    "EnergyVadProvider",
)


class VadActivity(StrEnum):
    SILENCE = "silence"
    SPEECH = "speech"


class VadFrameDuration(StrEnum):
    MS_10 = "ms_10"
    MS_20 = "ms_20"
    MS_30 = "ms_30"

    @property
    def pcm_byte_count(self) -> int:
        """Exact PHX-060 mono S16LE evidence; no arbitrary duration/conversion."""
        for member, count in (
            (VadFrameDuration.MS_10, 320),
            (VadFrameDuration.MS_20, 640),
            (VadFrameDuration.MS_30, 960),
        ):
            if self is member:
                return count
        raise ValueError("invalid_input")


class VadProviderErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    INVALID_RESPONSE = "invalid_response"
    UNSUPPORTED_AUDIO = "unsupported_audio"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    BUSY = "busy"
    CLOSED = "closed"
    WRONG_EVENT_LOOP = "wrong_event_loop"
    CLEANUP_FAILURE = "cleanup_failure"


class VadProviderError(Exception):
    """Safe category only; no audio, thresholds or backend diagnostics."""

    def __init__(self, code: VadProviderErrorCode) -> None:
        if not any(code is member for member in VadProviderErrorCode):
            raise ValueError("invalid_input")
        self.code = code
        super().__init__(code.value)


_E = TypeVar("_E", bound=StrEnum)


def _enum(value: object, cls: type[_E]) -> _E:
    for member in cls:
        if value is member or (type(value) is str and value == member.value):
            return member
    raise ValueError("invalid_input")


def _id(value: object) -> str:
    try:
        return _identifier(value)
    except (ValueError, TypeError):
        raise VadProviderError(VadProviderErrorCode.INVALID_INPUT) from None


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )
    _failure_code: ClassVar[VadProviderErrorCode] = VadProviderErrorCode.INVALID_INPUT

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
            VadProviderError,
        ):
            raise VadProviderError(cls._failure_code) from None

    @field_validator(
        "operation_id", "provider_id", "stream_id", mode="before", check_fields=False
    )
    @classmethod
    def identifier(cls, value: object) -> str:
        return _identifier(value)

    @field_validator("duration", mode="before", check_fields=False)
    @classmethod
    def duration_value(cls, value: object) -> VadFrameDuration:
        return _enum(value, VadFrameDuration)


class VadCapabilities(_Contract):
    """Technical support only; no availability, quality or selection authority."""

    _failure_code = VadProviderErrorCode.INVALID_RESPONSE
    provider_id: str
    supported_durations: tuple[VadFrameDuration, ...]
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    encoding: AudioEncoding
    supports_cancellation: bool = Field(strict=True)

    @field_validator("supported_durations", mode="before")
    @classmethod
    def durations(cls, value: object) -> tuple[VadFrameDuration, ...]:
        if type(value) not in (list, tuple):
            raise ValueError("invalid_response")
        values = cast(Sequence[object], value)
        if not 1 <= len(values) <= 3:
            raise ValueError("invalid_response")
        accepted = tuple(_enum(item, VadFrameDuration) for item in values)
        if accepted != tuple(
            member for member in VadFrameDuration if member in accepted
        ):
            raise ValueError("invalid_response")
        return accepted

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
        evidence = VadCapabilities.model_validate(self)
        if evidence.provider_id != _id(provider_id):
            raise VadProviderError(VadProviderErrorCode.INVALID_RESPONSE)


class VadRequest(_Contract):
    operation_id: str = Field(default_factory=lambda: str(uuid4()))
    frame: PcmAudioFrame
    duration: VadFrameDuration

    @field_validator("frame", mode="before")
    @classmethod
    def frame_snapshot(cls, value: object) -> PcmAudioFrame:
        return PcmAudioFrame.model_validate(value)

    @model_validator(mode="after")
    def exact_audio(self) -> Self:
        if len(self.frame.pcm_bytes_for_provider()) != self.duration.pcm_byte_count:
            raise VadProviderError(VadProviderErrorCode.UNSUPPORTED_AUDIO)
        return self


class VadDecision(_Contract):
    """Provider evidence only; neutral score never derives/overrides activity."""

    _failure_code = VadProviderErrorCode.INVALID_RESPONSE
    operation_id: str
    provider_id: str
    stream_id: str
    sequence: Annotated[int, Field(strict=True, ge=0)]
    captured_at: AwareDatetime
    activity: VadActivity
    score: float
    duration: VadFrameDuration

    @field_validator("sequence", mode="before")
    @classmethod
    def ordinary_integer(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("invalid_response")
        return value

    @field_validator("captured_at", mode="before")
    @classmethod
    def timestamp(cls, value: object) -> object:
        if type(value) not in (datetime, str):
            raise ValueError("invalid_response")
        return value

    @field_validator("captured_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("activity", mode="before")
    @classmethod
    def activity_value(cls, value: object) -> VadActivity:
        return _enum(value, VadActivity)

    @field_validator("score", mode="before")
    @classmethod
    def normalized_score(cls, value: object) -> float:
        if type(value) is not float or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("invalid_response")
        return value

    def validate_request(self, request: VadRequest, provider_id: str) -> None:
        evidence = VadDecision.model_validate(self)
        if type(request) is not VadRequest:
            raise VadProviderError(VadProviderErrorCode.INVALID_INPUT)
        accepted = VadRequest.model_validate(request)
        if (
            evidence.operation_id,
            evidence.provider_id,
            evidence.stream_id,
            evidence.sequence,
            evidence.captured_at,
            evidence.duration,
        ) != (
            accepted.operation_id,
            _id(provider_id),
            accepted.frame.stream_id,
            accepted.frame.sequence,
            accepted.frame.captured_at,
            accepted.duration,
        ):
            raise VadProviderError(VadProviderErrorCode.INVALID_RESPONSE)


class VadProvider(ABC):
    """Pure boundary; adapters own bounded work, cleanup and scoped cancellation.

    Consumers exact-revalidate capabilities, decisions and cancellation identity.
    ACCEPTED acknowledges only a request, not proven native inference cessation.
    No selection, retry, fallback or global scheduling policy belongs here.
    """

    def __init__(self, provider_id: str) -> None:
        self._provider_id = _id(provider_id)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    @abstractmethod
    def active_operation_ids(self) -> tuple[str, ...]:
        """Sorted immutable observational IDs, never task handles or authority."""

    @abstractmethod
    async def capabilities(self) -> VadCapabilities:
        """Return exact provider-correlated technical evidence."""

    @abstractmethod
    async def classify(self, request: VadRequest) -> VadDecision:
        """Return exact correlated frame evidence or a safe explicit error."""

    @abstractmethod
    async def cancel(self, operation_id: str) -> CancellationResult:
        """Operation-scoped acknowledgement only, never global shutdown."""

    @abstractmethod
    async def aclose(self) -> None:
        """Explicit cleanup; the base allocates no runtime resource."""


DEFAULT_ENERGY_VAD_THRESHOLD_DBFS = -42.0


class EnergyVadProvider(VadProvider):
    """Deterministic stdlib amplitude baseline, not human-speech discrimination.

    At most 480 samples per classify, without awaits, tasks, queues or workers.
    Owns only configuration/loop/closed state and retains no PCM/request/result.
    First async method binds a loop; cross-loop calls fail even after closure.
    Same-loop close is idempotent; other methods then fail closed. Cancellation
    is unsupported because classify finishes without cooperative suspension.
    """

    _closed: bool

    def __init__(
        self,
        provider_id: str = "energy_vad_v1",
        *,
        threshold_dbfs: float = DEFAULT_ENERGY_VAD_THRESHOLD_DBFS,
    ) -> None:
        if hasattr(self, "_closed"):
            raise VadProviderError(
                VadProviderErrorCode.CLOSED
                if self._closed
                else VadProviderErrorCode.INVALID_INPUT
            )
        super().__init__(provider_id)
        if (
            type(threshold_dbfs) is not float
            or not math.isfinite(threshold_dbfs)
            or not -90.0 <= threshold_dbfs <= -10.0
        ):
            raise VadProviderError(VadProviderErrorCode.INVALID_INPUT)
        self._threshold_dbfs = threshold_dbfs
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    @property
    def threshold_dbfs(self) -> float:
        return self._threshold_dbfs

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return ()

    @property
    def closed(self) -> bool:
        return self._closed

    def _check_loop(self, *, allow_closed: bool = False) -> None:
        current = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = current
        elif self._loop is not current:
            raise VadProviderError(VadProviderErrorCode.WRONG_EVENT_LOOP)
        if self._closed and not allow_closed:
            raise VadProviderError(VadProviderErrorCode.CLOSED)

    async def capabilities(self) -> VadCapabilities:
        self._check_loop()
        return VadCapabilities(
            provider_id=self.provider_id,
            supported_durations=tuple(VadFrameDuration),
            sample_rate_hz=16000,
            channels=1,
            sample_width_bytes=2,
            encoding=AudioEncoding.PCM_S16LE,
            supports_cancellation=False,
        )

    async def classify(self, request: VadRequest) -> VadDecision:
        self._check_loop()
        if type(request) is not VadRequest:
            raise VadProviderError(VadProviderErrorCode.INVALID_INPUT)
        accepted = VadRequest.model_validate(request)
        pcm = accepted.frame.pcm_bytes_for_provider()
        square_sum = sum(sample * sample for (sample,) in struct.iter_unpack("<h", pcm))
        rms = math.sqrt(square_sum / (len(pcm) // 2))
        dbfs = -96.0 if rms == 0 else 20 * math.log10(rms / 32768.0)
        dbfs = max(-96.0, min(0.0, dbfs))
        return VadDecision(
            operation_id=accepted.operation_id,
            provider_id=self.provider_id,
            stream_id=accepted.frame.stream_id,
            sequence=accepted.frame.sequence,
            captured_at=accepted.frame.captured_at,
            duration=accepted.duration,
            activity=VadActivity.SPEECH
            if dbfs >= self.threshold_dbfs
            else VadActivity.SILENCE,
            score=max(0.0, min(1.0, (dbfs + 96.0) / 96.0)),
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        self._check_loop()
        return CancellationResult(
            operation_id=_id(operation_id),
            provider_id=self.provider_id,
            status=CancellationStatus.UNSUPPORTED,
        )

    async def aclose(self) -> None:
        self._check_loop(allow_closed=True)
        self._closed = True
