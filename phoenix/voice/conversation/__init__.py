"""Caller-driven voice (UTA) conversation; PHOENIX retains all authority.

Only injected collaborators perform explicit async work. No capture, speaker
backend, startup, transcript commands, history, routing, timer or worker exists.
Cancellation acknowledgement never proves native playback stopped.
"""

from __future__ import annotations

import asyncio
import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import ClassVar, Self, TypeVar, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    field_validator,
    model_validator,
)

from phoenix.core.event_bus import EventBus
from phoenix.core.events import EventEnvelope, EventType
from phoenix.models.provider import CancellationResult, CancellationStatus
from phoenix.voice.audio import PcmAudioFrame, _identifier, _stored_values
from phoenix.voice.stt import SttCapabilities, SttProvider, SttRequest, SttTranscript
from phoenix.voice.tts import TtsAudio, TtsCapabilities, TtsProvider, TtsRequest
from phoenix.voice.vad import VadActivity, VadDecision, VadRequest

__all__ = (
    "VoiceConversationState",
    "VoiceConversationErrorCode",
    "VoiceConversationError",
    "VoiceFrameObservation",
    "VoiceSegmenterConfig",
    "VoiceSegment",
    "VoiceSegmenter",
    "VoiceResponseRequest",
    "VoiceResponseDraft",
    "VoiceResponder",
    "AudioPlaybackStatus",
    "AudioOutputErrorCode",
    "AudioOutputError",
    "AudioPlaybackRequest",
    "AudioPlaybackResult",
    "AudioOutput",
    "VoiceConversationConfig",
    "VoiceTurnRequest",
    "VoiceTurnOutcome",
    "VoiceFailureStage",
    "VoiceTurnResult",
    "VoiceConversationManager",
)


class VoiceConversationState(StrEnum):
    SLEEPING = "sleeping"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    DEGRADED_VOICE = "degraded_voice"
    TEXT_ONLY = "text_only"


class VoiceConversationErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    INVALID_STATE = "invalid_state"
    INVALID_RESPONSE = "invalid_response"
    BUSY = "busy"
    CLOSED = "closed"
    WRONG_EVENT_LOOP = "wrong_event_loop"
    RESPONDER_FAILURE = "responder_failure"
    EVENT_FAILURE = "event_failure"
    PLAYBACK_FAILURE = "playback_failure"
    CANCELLATION_FAILURE = "cancellation_failure"
    SEGMENT_TOO_LONG = "segment_too_long"


class VoiceConversationError(Exception):
    def __init__(self, code: VoiceConversationErrorCode) -> None:
        if not any(code is member for member in VoiceConversationErrorCode):
            raise ValueError("invalid_input")
        self.code = code
        super().__init__(code.value)


class AudioPlaybackStatus(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class AudioOutputErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    INVALID_RESPONSE = "invalid_response"
    UNAVAILABLE = "unavailable"
    BUSY = "busy"
    CLOSED = "closed"
    WRONG_EVENT_LOOP = "wrong_event_loop"
    CLEANUP_FAILURE = "cleanup_failure"


class AudioOutputError(Exception):
    def __init__(self, code: AudioOutputErrorCode) -> None:
        if not any(code is member for member in AudioOutputErrorCode):
            raise ValueError("invalid_input")
        self.code = code
        super().__init__(code.value)


class VoiceTurnOutcome(StrEnum):
    SPOKEN = "spoken"
    INTERRUPTED = "interrupted"
    TEXT_FALLBACK = "text_fallback"


class VoiceFailureStage(StrEnum):
    STT = "stt"
    TTS = "tts"
    PLAYBACK = "playback"
    VOICE_OUTPUT_UNAVAILABLE = "voice_output_unavailable"


E = TypeVar("E", bound=StrEnum)


def _enum(value: object, cls: type[E]) -> E:
    for member in cls:
        if value is member or (type(value) is str and value == member.value):
            return member
    raise ValueError("invalid_input")


def _error(code: VoiceConversationErrorCode) -> VoiceConversationError:
    return VoiceConversationError(code)


def _id(value: object) -> str:
    try:
        return _identifier(value)
    except Exception:
        raise _error(VoiceConversationErrorCode.INVALID_INPUT) from None


def _text(value: object, bound: int) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or "\0" in value
        or len(value) > bound
    ):
        raise ValueError("invalid_input")
    value.encode("utf-8")
    return value


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )
    _response: ClassVar[bool] = False
    _output: ClassVar[bool] = False

    @model_validator(mode="wrap")
    @classmethod
    def exact(cls, value: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        try:
            return handler(_stored_values(value, cls))._integrity()
        except Exception:
            if cls._output:
                raise AudioOutputError(
                    AudioOutputErrorCode.INVALID_RESPONSE
                    if cls._response
                    else AudioOutputErrorCode.INVALID_INPUT
                ) from None
            raise _error(
                VoiceConversationErrorCode.INVALID_RESPONSE
                if cls._response
                else VoiceConversationErrorCode.INVALID_INPUT
            ) from None

    @field_validator(
        "session_id",
        "turn_id",
        "stream_id",
        "playback_id",
        "output_id",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def identifiers(cls, value: object) -> str:
        return _identifier(value)

    def _integrity(self) -> Self:
        return self


class VoiceFrameObservation(_Contract):
    frame: PcmAudioFrame = Field(repr=False)
    decision: VadDecision

    @field_validator("frame", mode="before")
    @classmethod
    def frame_snapshot(cls, value: object) -> PcmAudioFrame:
        return PcmAudioFrame.model_validate(value)

    @field_validator("decision", mode="before")
    @classmethod
    def decision_snapshot(cls, value: object) -> VadDecision:
        return VadDecision.model_validate(value)

    def _integrity(self) -> Self:
        request = VadRequest(
            operation_id=self.decision.operation_id,
            frame=self.frame,
            duration=self.decision.duration,
        )
        self.decision.validate_request(request, self.decision.provider_id)
        return self


class VoiceSegmenterConfig(_Contract):
    speech_start_frames: int = Field(default=2, strict=True, ge=1, le=8)
    speech_end_frames: int = Field(default=10, strict=True, ge=1, le=50)
    max_segment_frames: int = Field(default=256, strict=True, ge=1, le=256)

    @field_validator(
        "speech_start_frames", "speech_end_frames", "max_segment_frames", mode="before"
    )
    @classmethod
    def ordinary_counts(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("invalid_input")
        return value


class VoiceSegment(_Contract):
    stream_id: str
    start_sequence: int = Field(strict=True, ge=0)
    end_sequence: int = Field(strict=True, ge=0)
    frames: tuple[PcmAudioFrame, ...] = Field(repr=False)

    @field_validator("start_sequence", "end_sequence", mode="before")
    @classmethod
    def ordinary_sequence(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("invalid_input")
        return value

    @field_validator("frames", mode="before")
    @classmethod
    def snapshots(cls, value: object) -> tuple[PcmAudioFrame, ...]:
        if (
            type(value) not in (tuple, list)
            or not 1 <= len(cast(Sequence[object], value)) <= 256
        ):
            raise ValueError("invalid_input")
        return tuple(
            PcmAudioFrame.model_validate(item) for item in cast(Sequence[object], value)
        )

    def _integrity(self) -> Self:
        # Reuse the unchanged STT window validation and byte bound.
        SttRequest(frames=self.frames)
        if (self.stream_id, self.start_sequence, self.end_sequence) != (
            self.frames[0].stream_id,
            self.frames[0].sequence,
            self.frames[-1].sequence,
        ):
            raise ValueError("invalid_input")
        return self


class VoiceSegmenter:
    def __init__(self, config: VoiceSegmenterConfig | None = None) -> None:
        self._config = VoiceSegmenterConfig.model_validate(
            config if config is not None else VoiceSegmenterConfig()
        )
        self.reset()

    @property
    def config(self) -> VoiceSegmenterConfig:
        return VoiceSegmenterConfig.model_validate(self._config)

    @property
    def active(self) -> bool:
        return self._active

    @property
    def buffered_frame_count(self) -> int:
        return len(self._frames) + len(self._silence)

    def reset(self) -> None:
        self._active = False
        self._frames: list[PcmAudioFrame] = []
        self._silence: list[PcmAudioFrame] = []

    def feed(self, observation: VoiceFrameObservation) -> VoiceSegment | None:
        try:
            value = VoiceFrameObservation.model_validate(observation)
            frame = value.frame
            last = (
                self._silence[-1]
                if self._silence
                else self._frames[-1]
                if self._frames
                else None
            )
            if last is not None and (
                frame.stream_id != last.stream_id
                or frame.sequence != last.sequence + 1
                or frame.captured_at < last.captured_at
            ):
                raise _error(VoiceConversationErrorCode.INVALID_INPUT)
            if (self._active or value.decision.activity is VadActivity.SPEECH) and (
                self.buffered_frame_count + 1 > self._config.max_segment_frames
            ):
                raise _error(VoiceConversationErrorCode.SEGMENT_TOO_LONG)
            if value.decision.activity is VadActivity.SILENCE:
                if not self._active:
                    self.reset()
                    return None
                # Terminal silence is never accepted into the utterance itself.
                self._silence.append(frame)
                if len(self._silence) == self._config.speech_end_frames:
                    segment = VoiceSegment(
                        stream_id=self._frames[0].stream_id,
                        start_sequence=self._frames[0].sequence,
                        end_sequence=self._frames[-1].sequence,
                        frames=tuple(self._frames),
                    )
                    self.reset()
                    return segment
                return None
            self._frames.extend(self._silence)
            self._silence.clear()
            self._frames.append(frame)
            if len(self._frames) >= self._config.speech_start_frames:
                self._active = True
            return None
        except Exception:
            self.reset()
            raise


class VoiceResponseRequest(_Contract):
    session_id: str
    turn_id: str
    transcript_text: str = Field(repr=False)

    @field_validator("transcript_text", mode="before")
    @classmethod
    def text(cls, value: object) -> str:
        return _text(value, 20000)


class VoiceResponseDraft(_Contract):
    _response = True
    session_id: str
    turn_id: str
    response_text: str = Field(repr=False)

    @field_validator("response_text", mode="before")
    @classmethod
    def text(cls, value: object) -> str:
        return _text(value, 4096)

    def validate_request(self, request: VoiceResponseRequest) -> None:
        draft = VoiceResponseDraft.model_validate(self)
        accepted = VoiceResponseRequest.model_validate(request)
        if (draft.session_id, draft.turn_id) != (accepted.session_id, accepted.turn_id):
            raise _error(VoiceConversationErrorCode.INVALID_RESPONSE)


class VoiceResponder(ABC):
    def __init__(self, responder_id: str) -> None:
        self._responder_id = _id(responder_id)

    @property
    def responder_id(self) -> str:
        return self._responder_id

    @abstractmethod
    async def respond(self, request: VoiceResponseRequest) -> VoiceResponseDraft:
        """Return subordinate correlated text, never permissions or PCM."""


class AudioPlaybackRequest(_Contract):
    _output = True
    playback_id: str
    session_id: str
    turn_id: str
    audio: TtsAudio = Field(repr=False)

    @field_validator("audio", mode="before")
    @classmethod
    def snapshot(cls, value: object) -> TtsAudio:
        return TtsAudio.model_validate(value)


class AudioPlaybackResult(_Contract):
    _output = True
    _response = True
    playback_id: str
    output_id: str
    status: AudioPlaybackStatus

    @field_validator("status", mode="before")
    @classmethod
    def status_value(cls, value: object) -> AudioPlaybackStatus:
        return _enum(value, AudioPlaybackStatus)

    def validate_request(self, request: AudioPlaybackRequest, output_id: str) -> None:
        value = AudioPlaybackResult.model_validate(self)
        accepted = AudioPlaybackRequest.model_validate(request)
        if (value.playback_id, value.output_id) != (
            accepted.playback_id,
            _identifier(output_id),
        ):
            raise AudioOutputError(AudioOutputErrorCode.INVALID_RESPONSE)


class AudioOutput(ABC):
    def __init__(self, output_id: str) -> None:
        try:
            self._output_id = _identifier(output_id)
        except Exception:
            raise AudioOutputError(AudioOutputErrorCode.INVALID_INPUT) from None

    @property
    def output_id(self) -> str:
        return self._output_id

    @property
    @abstractmethod
    def active_playback_ids(self) -> tuple[str, ...]:
        """Sorted immutable observational IDs; no task handles."""

    @abstractmethod
    async def play(self, request: AudioPlaybackRequest) -> AudioPlaybackResult:
        """Caller-owned playback implementation; no concrete backend here."""

    @abstractmethod
    async def interrupt(self, playback_id: str) -> CancellationResult:
        """Scoped acknowledgement, not proof of native cessation."""

    @abstractmethod
    async def aclose(self) -> None:
        """Explicit caller-owned cleanup."""


class VoiceConversationConfig(_Contract):
    follow_up_timeout_seconds: float = 30.0
    barge_in_speech_frames: int = Field(default=2, strict=True, ge=1, le=8)

    @field_validator("barge_in_speech_frames", mode="before")
    @classmethod
    def ordinary_count(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("invalid_input")
        return value

    @field_validator("follow_up_timeout_seconds", mode="before")
    @classmethod
    def timeout(cls, value: object) -> float:
        if (
            type(value) is not float
            or not math.isfinite(value)
            or not 1.0 <= value <= 600.0
        ):
            raise ValueError("invalid_input")
        return value


class VoiceTurnRequest(_Contract):
    session_id: str
    turn_id: str
    segment: VoiceSegment = Field(repr=False)
    stt_language_hint: str | None = None
    tts_voice_profile_id: str
    tts_language_hint: str | None = None

    @field_validator("segment", mode="before")
    @classmethod
    def snapshot(cls, value: object) -> VoiceSegment:
        return VoiceSegment.model_validate(value)

    @field_validator("stt_language_hint", "tts_language_hint", mode="before")
    @classmethod
    def language(cls, value: object) -> str | None:
        return None if value is None else _identifier(value, 32)

    @field_validator("tts_voice_profile_id", mode="before")
    @classmethod
    def profile(cls, value: object) -> str:
        return _identifier(value, 128)


class VoiceTurnResult(_Contract):
    _response = True
    session_id: str
    turn_id: str
    outcome: VoiceTurnOutcome
    transcript_text: str | None = Field(default=None, repr=False)
    response_text: str | None = Field(default=None, repr=False)
    failure_stage: VoiceFailureStage | None = None
    final_state: VoiceConversationState

    @field_validator("transcript_text", "response_text", mode="before")
    @classmethod
    def optional_text(cls, value: object) -> str | None:
        return None if value is None else _text(value, 20000)

    @field_validator("response_text")
    @classmethod
    def response_bound(cls, value: str | None) -> str | None:
        return None if value is None else _text(value, 4096)

    @field_validator("outcome", mode="before")
    @classmethod
    def outcome_value(cls, value: object) -> VoiceTurnOutcome:
        return _enum(value, VoiceTurnOutcome)

    @field_validator("failure_stage", mode="before")
    @classmethod
    def failure_value(cls, value: object) -> VoiceFailureStage | None:
        return None if value is None else _enum(value, VoiceFailureStage)

    @field_validator("final_state", mode="before")
    @classmethod
    def state_value(cls, value: object) -> VoiceConversationState:
        return _enum(value, VoiceConversationState)

    def _integrity(self) -> Self:
        texts = self.transcript_text is not None and self.response_text is not None
        if self.outcome is VoiceTurnOutcome.SPOKEN:
            valid = (
                texts
                and self.failure_stage is None
                and self.final_state is VoiceConversationState.LISTENING
            )
        elif self.outcome is VoiceTurnOutcome.INTERRUPTED:
            valid = (
                texts
                and self.failure_stage is None
                and self.final_state
                in (VoiceConversationState.LISTENING, VoiceConversationState.SLEEPING)
            )
        elif self.failure_stage is VoiceFailureStage.STT:
            valid = (
                self.transcript_text is None
                and self.response_text is None
                and self.final_state is VoiceConversationState.TEXT_ONLY
            )
        else:
            valid = (
                texts
                and self.failure_stage
                in (
                    VoiceFailureStage.TTS,
                    VoiceFailureStage.PLAYBACK,
                    VoiceFailureStage.VOICE_OUTPUT_UNAVAILABLE,
                )
                and self.final_state is VoiceConversationState.DEGRADED_VOICE
            )
        if not valid:
            raise ValueError("invalid_response")
        return self


class VoiceConversationManager:
    """One caller task per turn; injected collaborators remain caller-owned.

    A local lock serializes interruption acknowledgement/events with playback
    completion. It creates no worker. Event failure aborts the turn explicitly;
    accepted interruption may already exist even when its event fails to publish.
    """

    def __init__(
        self,
        stt_provider: SttProvider,
        tts_provider: TtsProvider,
        responder: VoiceResponder,
        audio_output: AudioOutput,
        event_bus: EventBus,
        *,
        clock: Callable[[], float],
        segmenter_config: VoiceSegmenterConfig | None = None,
        config: VoiceConversationConfig | None = None,
    ) -> None:
        if not (
            isinstance(stt_provider, SttProvider)
            and isinstance(tts_provider, TtsProvider)
            and isinstance(responder, VoiceResponder)
            and isinstance(audio_output, AudioOutput)
            and isinstance(event_bus, EventBus)
            and callable(clock)
        ):
            raise _error(VoiceConversationErrorCode.INVALID_INPUT)
        self._stt = stt_provider
        self._tts = tts_provider
        self._responder = responder
        self._output = audio_output
        self._bus = event_bus
        self._clock = clock
        self._config = VoiceConversationConfig.model_validate(
            config if config is not None else VoiceConversationConfig()
        )
        self._segmenter = VoiceSegmenter(segmenter_config)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._task: asyncio.Task[object] | None = None
        self._turn: str | None = None
        self._playback: str | None = None
        self._state = VoiceConversationState.SLEEPING
        self._session: str | None = None
        self._deadline: float | None = None
        self._reset_interrupt()

    @property
    def owner_id(self) -> str:
        return "voice"

    @property
    def state(self) -> VoiceConversationState:
        return self._state

    @property
    def session_id(self) -> str | None:
        return self._session

    @property
    def active_turn_id(self) -> str | None:
        return self._turn

    @property
    def active_playback_id(self) -> str | None:
        return self._playback

    @property
    def follow_up_deadline(self) -> float | None:
        return self._deadline

    @property
    def closed(self) -> bool:
        return self._closed

    def _bind(self, *, closing: bool = False) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise _error(VoiceConversationErrorCode.WRONG_EVENT_LOOP)
        if self._closed and not closing:
            raise _error(VoiceConversationErrorCode.CLOSED)

    def _check_task(self) -> None:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        if self._closed:
            raise asyncio.CancelledError
        if self._event_failed:
            raise _error(VoiceConversationErrorCode.EVENT_FAILURE)

    def _now(self) -> float:
        try:
            value = self._clock()
            if type(value) is not float or not math.isfinite(value):
                raise ValueError
            return value
        except Exception:
            raise _error(VoiceConversationErrorCode.INVALID_INPUT) from None

    def _refresh(self) -> None:
        self._deadline = self._now() + self._config.follow_up_timeout_seconds

    def _reset_interrupt(self) -> None:
        self._barge_count = 0
        self._interrupt_attempted = False
        self._interrupted = False
        self._sleep_requested = False
        self._event_failed = False

    def _sleep_local(self) -> None:
        self._state = VoiceConversationState.SLEEPING
        self._session = None
        self._deadline = None
        self._segmenter.reset()
        self._barge_count = 0

    async def _publish(
        self, kind: EventType, session: str, turn: str | None = None
    ) -> None:
        try:
            event = EventEnvelope(
                event_type=kind,
                agent_id="voice",
                correlation_id=session,
                payload={"session_id": session, "turn_id": turn},
            )
            await self._bus.publish(event)
            self._check_task()
        except asyncio.CancelledError:
            self._event_failed = True
            raise
        except Exception:
            self._event_failed = True
            raise _error(VoiceConversationErrorCode.EVENT_FAILURE) from None

    async def activate(self, session_id: str) -> None:
        self._bind()
        session = _id(session_id)
        async with self._lock:
            self._bind()
            if self._turn is not None or self._state not in (
                VoiceConversationState.SLEEPING,
                VoiceConversationState.TEXT_ONLY,
                VoiceConversationState.DEGRADED_VOICE,
            ):
                raise _error(VoiceConversationErrorCode.BUSY)
            deadline = self._now() + self._config.follow_up_timeout_seconds
            self._reset_interrupt()
            await self._publish(EventType.VOICE_LISTENING, session)
            self._segmenter.reset()
            self._session = session
            self._deadline = deadline
            self._state = VoiceConversationState.LISTENING

    async def expire_if_idle(self) -> bool:
        self._bind()
        if (
            self._state is VoiceConversationState.LISTENING
            and self._turn is None
            and self._deadline is not None
            and self._now() >= self._deadline
        ):
            self._sleep_local()
            return True
        return False

    async def observe_listening(
        self, observation: VoiceFrameObservation
    ) -> VoiceSegment | None:
        self._bind()
        await self.expire_if_idle()
        self._check_task()
        if self._turn is not None or self._state not in (
            VoiceConversationState.LISTENING,
            VoiceConversationState.DEGRADED_VOICE,
        ):
            raise _error(VoiceConversationErrorCode.INVALID_STATE)
        return self._segmenter.feed(observation)

    async def process_turn(self, request: VoiceTurnRequest) -> VoiceTurnResult:
        self._bind()
        await self.expire_if_idle()
        self._check_task()
        if self._turn is not None:
            raise _error(VoiceConversationErrorCode.BUSY)
        if self._state not in (
            VoiceConversationState.LISTENING,
            VoiceConversationState.DEGRADED_VOICE,
        ):
            raise _error(VoiceConversationErrorCode.INVALID_STATE)
        accepted = VoiceTurnRequest.model_validate(request)
        if accepted.session_id != self._session:
            raise _error(VoiceConversationErrorCode.INVALID_INPUT)
        if self._segmenter.buffered_frame_count:
            raise _error(VoiceConversationErrorCode.BUSY)
        degraded = self._state is VoiceConversationState.DEGRADED_VOICE
        self._turn = accepted.turn_id
        self._task = asyncio.current_task()
        self._reset_interrupt()
        self._state = VoiceConversationState.THINKING
        try:
            return await self._run_turn(accepted, degraded)
        except asyncio.CancelledError:
            self._sleep_local()
            raise
        except VoiceConversationError:
            if self._state in (
                VoiceConversationState.THINKING,
                VoiceConversationState.SPEAKING,
            ):
                self._sleep_local()
            raise
        finally:
            self._turn = None
            self._playback = None
            self._task = None
            self._segmenter.reset()
            self._barge_count = 0

    def _fallback(
        self,
        request: VoiceTurnRequest,
        stage: VoiceFailureStage,
        transcript: str | None = None,
        response: str | None = None,
    ) -> VoiceTurnResult:
        self._state = (
            VoiceConversationState.TEXT_ONLY
            if stage is VoiceFailureStage.STT
            else VoiceConversationState.DEGRADED_VOICE
        )
        if stage is VoiceFailureStage.STT:
            self._deadline = None
        else:
            self._refresh()
        return VoiceTurnResult(
            session_id=request.session_id,
            turn_id=request.turn_id,
            outcome=VoiceTurnOutcome.TEXT_FALLBACK,
            transcript_text=transcript,
            response_text=response,
            failure_stage=stage,
            final_state=self._state,
        )

    async def _run_turn(
        self, request: VoiceTurnRequest, degraded: bool
    ) -> VoiceTurnResult:
        try:
            incoming = SttRequest(
                operation_id=request.turn_id,
                frames=request.segment.frames,
                language_hint=request.stt_language_hint,
            )
            raw_cap = await self._stt.capabilities()
            self._check_task()
            cap = SttCapabilities.model_validate(raw_cap)
            cap.validate_provider(self._stt.provider_id)
            if incoming.language_hint is not None and not cap.supports_language_hint:
                raise ValueError("invalid_input")
            raw_transcript = await self._stt.transcribe(incoming)
            self._check_task()
            transcript = SttTranscript.model_validate(raw_transcript)
            transcript.validate_request(incoming, self._stt.provider_id)
            response_request = VoiceResponseRequest(
                session_id=request.session_id,
                turn_id=request.turn_id,
                transcript_text=transcript.text,
            )
        except Exception:
            self._check_task()
            return self._fallback(request, VoiceFailureStage.STT)
        try:
            raw_draft = await self._responder.respond(response_request)
            self._check_task()
            draft = VoiceResponseDraft.model_validate(raw_draft)
            draft.validate_request(response_request)
        except Exception:
            self._check_task()
            self._state = VoiceConversationState.LISTENING
            self._refresh()
            raise _error(VoiceConversationErrorCode.RESPONDER_FAILURE) from None
        if degraded:
            return self._fallback(
                request,
                VoiceFailureStage.VOICE_OUTPUT_UNAVAILABLE,
                transcript.text,
                draft.response_text,
            )
        try:
            raw_tts_cap = await self._tts.capabilities()
            self._check_task()
            tts_cap = TtsCapabilities.model_validate(raw_tts_cap)
            tts_cap.validate_provider(self._tts.provider_id)
            outgoing = TtsRequest(
                operation_id=request.turn_id,
                text=draft.response_text,
                voice_profile_id=request.tts_voice_profile_id,
                language_hint=request.tts_language_hint,
            )
            outgoing.validate_capabilities(tts_cap)
            raw_audio = await self._tts.synthesize(outgoing)
            self._check_task()
            audio = TtsAudio.model_validate(raw_audio)
            audio.validate_request(outgoing, tts_cap)
        except Exception:
            self._check_task()
            return self._fallback(
                request, VoiceFailureStage.TTS, transcript.text, draft.response_text
            )
        playback = AudioPlaybackRequest(
            playback_id=request.turn_id,
            session_id=request.session_id,
            turn_id=request.turn_id,
            audio=audio,
        )
        async with self._lock:
            self._check_task()
            await self._publish(
                EventType.VOICE_SPEAKING, request.session_id, request.turn_id
            )
            self._playback = request.turn_id
            self._state = VoiceConversationState.SPEAKING
        playback_failed = False
        try:
            raw_result = await self._output.play(playback)
            self._check_task()
            result = AudioPlaybackResult.model_validate(raw_result)
            result.validate_request(playback, self._output.output_id)
        except Exception:
            self._check_task()
            playback_failed = True
        async with self._lock:
            self._check_task()
            if self._sleep_requested:
                self._sleep_local()
                outcome = VoiceTurnOutcome.INTERRUPTED
            elif playback_failed or (
                result.status is AudioPlaybackStatus.INTERRUPTED
                and not self._interrupted
            ):
                return self._fallback(
                    request,
                    VoiceFailureStage.PLAYBACK,
                    transcript.text,
                    draft.response_text,
                )
            else:
                outcome = (
                    VoiceTurnOutcome.INTERRUPTED
                    if self._interrupted
                    else VoiceTurnOutcome.SPOKEN
                )
                deadline = self._now() + self._config.follow_up_timeout_seconds
                await self._publish(
                    EventType.VOICE_LISTENING, request.session_id, request.turn_id
                )
                self._state = VoiceConversationState.LISTENING
                self._deadline = deadline
            return VoiceTurnResult(
                session_id=request.session_id,
                turn_id=request.turn_id,
                outcome=outcome,
                transcript_text=transcript.text,
                response_text=draft.response_text,
                final_state=self._state,
            )

    async def _interrupt(self, *, sleep: bool) -> bool:
        # Caller holds _lock, so completion cannot outrun an acknowledgement/event.
        if self._interrupted:
            if sleep:
                self._sleep_requested = True
            return False
        if self._interrupt_attempted:
            raise _error(VoiceConversationErrorCode.CANCELLATION_FAILURE)
        playback = self._playback
        session = self._session
        if playback is None or session is None:
            raise _error(VoiceConversationErrorCode.INVALID_STATE)
        self._interrupt_attempted = True
        try:
            raw = await self._output.interrupt(playback)
            self._check_task()
            if type(raw) is not CancellationResult:
                raise ValueError
            values = _stored_values(raw, CancellationResult)
            if not any(raw.status is member for member in CancellationStatus):
                raise ValueError
            result = CancellationResult.model_validate(values)
            if (result.provider_id, result.operation_id) != (
                self._output.output_id,
                playback,
            ) or result.status is not CancellationStatus.ACCEPTED:
                raise ValueError
        except Exception:
            self._check_task()
            raise _error(VoiceConversationErrorCode.CANCELLATION_FAILURE) from None
        self._interrupted = True
        self._sleep_requested = sleep
        await self._publish(EventType.VOICE_INTERRUPTED, session, self._turn)
        return True

    async def observe_barge_in(self, observation: VoiceFrameObservation) -> bool:
        self._bind()
        value = VoiceFrameObservation.model_validate(observation)
        async with self._lock:
            self._bind()
            if (
                self._state is not VoiceConversationState.SPEAKING
                or self._turn is None
                or self._playback is None
            ):
                raise _error(VoiceConversationErrorCode.INVALID_STATE)
            if self._interrupted:
                return False
            if value.decision.activity is VadActivity.SILENCE:
                self._barge_count = 0
                return False
            self._barge_count += 1
            if self._barge_count < self._config.barge_in_speech_frames:
                return False
            return await self._interrupt(sleep=False)

    async def sleep(self) -> None:
        self._bind()
        async with self._lock:
            self._bind()
            if self._state is VoiceConversationState.SPEAKING:
                await self._interrupt(sleep=True)
            elif (
                self._turn is not None or self._state is VoiceConversationState.THINKING
            ):
                raise _error(VoiceConversationErrorCode.BUSY)
            elif self._state in (
                VoiceConversationState.LISTENING,
                VoiceConversationState.DEGRADED_VOICE,
                VoiceConversationState.TEXT_ONLY,
            ):
                self._sleep_local()
                self._reset_interrupt()
            else:
                raise _error(VoiceConversationErrorCode.INVALID_STATE)

    async def aclose(self) -> None:
        self._bind(closing=True)
        async with self._lock:
            if self._closed:
                return
            try:
                if self._playback is not None:
                    await self._interrupt(sleep=True)
            finally:
                self._closed = True
                self._sleep_local()
                self._turn = None
                self._playback = None
                self._task = None
