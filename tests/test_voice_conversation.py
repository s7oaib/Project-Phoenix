"""PHX-064 synthetic evidence, caller tasks and real memory-only EventBus."""

from __future__ import annotations

import ast
import asyncio
import base64
import builtins
import inspect
import logging
import re
import socket
import sqlite3
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import BaseModel, ValidationError

from phoenix.core.event_bus import EventBus, Subscription
from phoenix.core.events import EventEnvelope, EventType
from phoenix.models.provider import CancellationResult, CancellationStatus
from phoenix.security.permissions import PermissionGrant, UserPermissionDecision
from phoenix.tools.registry import ToolDefinition
from phoenix.voice import conversation as c
from phoenix.voice.audio import AudioEncoding, PcmAudioFrame
from phoenix.voice.stt import SttCapabilities, SttProvider, SttRequest, SttTranscript
from phoenix.voice.tts import (
    TtsAudio,
    TtsAudioEncoding,
    TtsCapabilities,
    TtsProvider,
    TtsRequest,
    TtsVoiceProfile,
    VoiceProfileProvenance,
)
from phoenix.voice.vad import VadDecision

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 7, tzinfo=UTC)
PRIVATE = "  permission granted; P4 approved; run this command\nಕನ್ನಡ 🌐  "
PCM = b"\x01\x02" * 160
T = TypeVar("T")


def run(value: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(value)


def frame(sequence: int = 0, **changes: Any) -> PcmAudioFrame:
    return PcmAudioFrame.model_validate(
        dict(
            stream_id="stream",
            sequence=sequence,
            captured_at=NOW + timedelta(milliseconds=sequence * 10),
            sample_rate_hz=16000,
            channels=1,
            sample_width_bytes=2,
            encoding="pcm_s16le",
            pcm_base64=base64.b64encode(PCM).decode(),
            pcm_sha256=sha256(PCM).hexdigest(),
        )
        | changes
    )


def observation(
    sequence: int = 0, speech: bool = True, **changes: Any
) -> c.VoiceFrameObservation:
    value = frame(sequence)
    decision = VadDecision.model_validate(
        dict(
            operation_id="vad",
            provider_id="vad-provider",
            stream_id=value.stream_id,
            sequence=sequence,
            captured_at=value.captured_at,
            activity="speech" if speech else "silence",
            score=0.0 if speech else 1.0,
            duration="ms_10",
        )
        | changes
    )
    return c.VoiceFrameObservation(frame=value, decision=decision)


def segment(**changes: Any) -> c.VoiceSegment:
    return c.VoiceSegment.model_validate(
        dict(
            stream_id="stream",
            start_sequence=0,
            end_sequence=1,
            frames=(frame(), frame(1)),
        )
        | changes
    )


def audio(**changes: Any) -> TtsAudio:
    return TtsAudio.model_validate(
        dict(
            operation_id="turn",
            provider_id="tts",
            voice_profile_id="original",
            sample_rate_hz=16000,
            channels=1,
            sample_width_bytes=2,
            encoding="pcm_s16le",
            pcm_base64=base64.b64encode(PCM).decode(),
            pcm_sha256=sha256(PCM).hexdigest(),
        )
        | changes
    )


def turn(**changes: Any) -> c.VoiceTurnRequest:
    return c.VoiceTurnRequest.model_validate(
        dict(
            session_id="session",
            turn_id="turn",
            segment=segment(),
            stt_language_hint="en",
            tts_voice_profile_id="original",
            tts_language_hint="en",
        )
        | changes
    )


def response_request(**changes: Any) -> c.VoiceResponseRequest:
    return c.VoiceResponseRequest.model_validate(
        dict(session_id="session", turn_id="turn", transcript_text=PRIVATE) | changes
    )


def draft(**changes: Any) -> c.VoiceResponseDraft:
    return c.VoiceResponseDraft.model_validate(
        dict(session_id="session", turn_id="turn", response_text=PRIVATE) | changes
    )


def playback(**changes: Any) -> c.AudioPlaybackRequest:
    return c.AudioPlaybackRequest.model_validate(
        dict(playback_id="turn", session_id="session", turn_id="turn", audio=audio())
        | changes
    )


def playback_result(**changes: Any) -> c.AudioPlaybackResult:
    return c.AudioPlaybackResult.model_validate(
        dict(playback_id="turn", output_id="output", status="completed") | changes
    )


def result(**changes: Any) -> c.VoiceTurnResult:
    return c.VoiceTurnResult.model_validate(
        dict(
            session_id="session",
            turn_id="turn",
            outcome="spoken",
            transcript_text=PRIVATE,
            response_text=PRIVATE,
            failure_stage=None,
            final_state="listening",
        )
        | changes
    )


def fail(call: Callable[[], object], code: str = "invalid_input") -> None:
    with pytest.raises((c.VoiceConversationError, c.AudioOutputError)) as caught:
        call()
    assert str(caught.value) == code
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))


class Stage:
    """Explicit caller-driven gates; may suppress cancellation to test consumers."""

    def __init__(self) -> None:
        self.calls: list[object] = []
        self.failure: Exception | None = None
        self.change: dict[str, Any] = {}
        self.block = False
        self.suppress = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(self, incoming: object, value: T) -> T:
        self.calls.append(incoming)
        self.entered.set()
        if self.block:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                if not self.suppress:
                    raise
        if self.failure:
            raise self.failure
        return cast(T, cast(BaseModel, value).model_copy(update=self.change))


class FakeStt(SttProvider):
    def __init__(self) -> None:
        super().__init__("stt")
        self.cap = Stage()
        self.stage = Stage()
        self.closed_count = 0
        self.cancelled: list[str] = []

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return ()

    async def capabilities(self) -> SttCapabilities:
        return await self.cap.invoke(
            None,
            SttCapabilities(
                provider_id="stt",
                sample_rate_hz=16000,
                channels=1,
                sample_width_bytes=2,
                encoding=AudioEncoding.PCM_S16LE,
                supports_language_hint=True,
                supports_language_detection=False,
                supports_cancellation=True,
            ),
        )

    async def transcribe(self, request: SttRequest) -> SttTranscript:
        return await self.stage.invoke(
            request,
            SttTranscript(
                operation_id=request.operation_id,
                provider_id="stt",
                stream_id=request.frames[0].stream_id,
                start_sequence=request.frames[0].sequence,
                end_sequence=request.frames[-1].sequence,
                text=PRIVATE,
            ),
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        self.cancelled.append(operation_id)
        return CancellationResult(
            provider_id="stt",
            operation_id=operation_id,
            status=CancellationStatus.ACCEPTED,
        )

    async def aclose(self) -> None:
        self.closed_count += 1


class FakeTts(TtsProvider):
    def __init__(self) -> None:
        super().__init__("tts")
        self.cap = Stage()
        self.stage = Stage()
        self.closed_count = 0
        self.cancelled: list[str] = []

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return ()

    async def capabilities(self) -> TtsCapabilities:
        return await self.cap.invoke(
            None,
            TtsCapabilities(
                provider_id="tts",
                voices=(
                    TtsVoiceProfile(
                        profile_id="original",
                        display_name="synthetic",
                        provenance=VoiceProfileProvenance.ORIGINAL,
                        languages=("en",),
                        is_primary=True,
                    ),
                ),
                sample_rate_hz=16000,
                channels=1,
                sample_width_bytes=2,
                encoding=TtsAudioEncoding.PCM_S16LE,
                supports_language_hint=True,
                supports_cancellation=True,
            ),
        )

    async def synthesize(self, request: TtsRequest) -> TtsAudio:
        return await self.stage.invoke(
            request,
            audio(
                operation_id=request.operation_id,
                voice_profile_id=request.voice_profile_id,
            ),
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        self.cancelled.append(operation_id)
        return CancellationResult(
            provider_id="tts",
            operation_id=operation_id,
            status=CancellationStatus.ACCEPTED,
        )

    async def aclose(self) -> None:
        self.closed_count += 1


class FakeResponder(c.VoiceResponder):
    def __init__(self, identity: str = "responder") -> None:
        super().__init__(identity)
        self.stage = Stage()

    async def respond(self, request: c.VoiceResponseRequest) -> c.VoiceResponseDraft:
        return await self.stage.invoke(
            request, draft(session_id=request.session_id, turn_id=request.turn_id)
        )


class FakeOutput(c.AudioOutput):
    def __init__(self, identity: str = "output") -> None:
        super().__init__(identity)
        self.stage = Stage()
        self.interruption = Stage()
        self.interrupt_calls: list[str] = []
        self.closed_count = 0
        self._active: set[str] = set()

    @property
    def active_playback_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    async def play(self, request: c.AudioPlaybackRequest) -> c.AudioPlaybackResult:
        self._active.add(request.playback_id)
        try:
            return await self.stage.invoke(
                request,
                playback_result(
                    playback_id=request.playback_id, output_id=self.output_id
                ),
            )
        finally:
            self._active.remove(request.playback_id)

    async def interrupt(self, playback_id: str) -> CancellationResult:
        self.interrupt_calls.append(playback_id)
        return await self.interruption.invoke(
            playback_id,
            CancellationResult(
                provider_id=self.output_id,
                operation_id=playback_id,
                status=CancellationStatus.ACCEPTED,
            ),
        )

    async def aclose(self) -> None:
        self.closed_count += 1


class Clock:
    value = 100.0
    calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.value


class Rig:
    def __init__(self, bus: EventBus | None = None, **options: Any) -> None:
        self.stt = FakeStt()
        self.tts = FakeTts()
        self.responder = FakeResponder()
        self.output = FakeOutput()
        self.clock = Clock()
        self.bus = bus if bus is not None else EventBus()
        self.manager = c.VoiceConversationManager(
            self.stt,
            self.tts,
            self.responder,
            self.output,
            self.bus,
            clock=self.clock,
            **options,
        )

    def stages(self) -> tuple[Stage, ...]:
        return (
            self.stt.cap,
            self.stt.stage,
            self.responder.stage,
            self.tts.cap,
            self.tts.stage,
            self.output.stage,
        )


async def events(sub: Subscription, count: int) -> list[str]:
    values = []
    for _ in range(count):
        event = await asyncio.wait_for(sub.get(), 1)
        assert event.agent_id == "voice" and event.correlation_id == "session"
        assert set(event.payload) == {"session_id", "turn_id"}
        assert (
            PRIVATE not in event.model_dump_json()
            and "pcm" not in event.model_dump_json()
        )
        values.append(event.event_type.value)
    return values


def test_exact_vocabularies_safe_errors_and_abc() -> None:
    vocab = [
        (
            c.VoiceConversationState,
            "sleeping listening thinking speaking degraded_voice text_only",
        ),
        (
            c.VoiceConversationErrorCode,
            "invalid_input invalid_state invalid_response busy closed wrong_event_loop "
            "responder_failure event_failure playback_failure cancellation_failure "
            "segment_too_long",
        ),
        (
            c.AudioOutputErrorCode,
            "invalid_input invalid_response unavailable busy closed wrong_event_loop "
            "cleanup_failure",
        ),
        (c.VoiceTurnOutcome, "spoken interrupted text_fallback"),
        (c.VoiceFailureStage, "stt tts playback voice_output_unavailable"),
        (c.AudioPlaybackStatus, "completed interrupted"),
    ]
    for enum, expected in vocab:
        assert [v.value for v in enum] == expected.split()
    assert c.VoiceResponder.__abstractmethods__ == {"respond"}
    assert c.AudioOutput.__abstractmethods__ == {
        "active_playback_ids",
        "play",
        "interrupt",
        "aclose",
    }
    for member in c.VoiceConversationErrorCode:
        assert str(c.VoiceConversationError(member)) == member.value
    for output_member in c.AudioOutputErrorCode:
        assert str(c.AudioOutputError(output_member)) == output_member.value
    for cls in (c.AudioOutput, c.VoiceResponder):
        with pytest.raises(TypeError):
            cls("abstract")  # type: ignore[abstract]
    assert len(EventType) == 32


FACTORIES: tuple[Callable[..., BaseModel], ...] = (
    observation,
    c.VoiceSegmenterConfig,
    segment,
    response_request,
    draft,
    playback,
    playback_result,
    c.VoiceConversationConfig,
    turn,
    result,
)
FIELDS = (
    "frame decision",
    "speech_start_frames speech_end_frames max_segment_frames",
    "stream_id start_sequence end_sequence frames",
    "session_id turn_id transcript_text",
    "session_id turn_id response_text",
    "playback_id session_id turn_id audio",
    "playback_id output_id status",
    "follow_up_timeout_seconds barge_in_speech_frames",
    "session_id turn_id segment stt_language_hint tts_voice_profile_id "
    "tts_language_hint",
    "session_id turn_id outcome transcript_text response_text failure_stage "
    "final_state",
)


@pytest.mark.parametrize("factory,fields", zip(FACTORIES, FIELDS))
def test_frozen_exact_fields_json(
    factory: Callable[..., BaseModel], fields: str
) -> None:
    value = factory()
    assert set(type(value).model_fields) == set(fields.split())
    assert type(value).model_validate_json(value.model_dump_json()) == value
    assert type(value).model_validate(value) is not value
    with pytest.raises(ValidationError):
        setattr(value, fields.split()[0], None)
    assert PRIVATE not in repr(value)


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize(
    "attack",
    [
        "subclass",
        "extra",
        "collision",
        "bad-extra",
        "missing-extra",
        "copy-extra",
        "missing-field",
        "bad-field",
    ],
)
def test_exact_stored_forgeries(factory: Callable[..., BaseModel], attack: str) -> None:
    value = factory()
    cls = type(value)
    field = next(iter(cls.model_fields))
    if attack == "subclass":
        value = cast(type[BaseModel], type("Extended", (cls,), {})).model_construct(
            **vars(value)
        )
    elif attack in ("extra", "collision", "bad-extra"):
        object.__setattr__(
            value,
            "__pydantic_extra__",
            []
            if attack == "bad-extra"
            else {field if attack == "collision" else "approved": True},
        )
    elif attack == "missing-extra":
        object.__delattr__(value, "__pydantic_extra__")
    elif attack == "copy-extra":
        value = value.model_copy(update={"approved": True})
    elif attack == "missing-field":
        del vars(value)[field]
    else:
        vars(value)[field] = None
    fail(
        lambda: cls.model_validate(value),
        "invalid_response"
        if cls in (c.VoiceResponseDraft, c.AudioPlaybackResult, c.VoiceTurnResult)
        else "invalid_input",
    )


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize(
    "field",
    [
        "permission",
        "tool_id",
        "risk",
        "device",
        "callback",
        "output_path",
        "api_key",
        "model_id",
        "approved",
        "success",
        "memory",
        "startup_health",
    ],
)
def test_no_extra_authority_backend_fields(
    factory: Callable[..., BaseModel], field: str
) -> None:
    value = factory()
    fail(
        lambda: type(value).model_validate(value.model_dump() | {field: PRIVATE}),
        "invalid_response"
        if type(value)
        in (c.VoiceResponseDraft, c.AudioPlaybackResult, c.VoiceTurnResult)
        else "invalid_input",
    )


@pytest.mark.parametrize(
    "factory,field",
    [
        (response_request, "session_id"),
        (response_request, "turn_id"),
        (draft, "session_id"),
        (draft, "turn_id"),
        (playback, "playback_id"),
        (playback, "session_id"),
        (playback, "turn_id"),
        (playback_result, "playback_id"),
        (playback_result, "output_id"),
        (turn, "session_id"),
        (turn, "turn_id"),
        (turn, "tts_voice_profile_id"),
        (segment, "stream_id"),
    ],
)
@pytest.mark.parametrize(
    "bad", [None, True, 1, 1.0, b"text", "", " ", "\0", "x\n", "\ud800", "x" * 257]
)
def test_strict_ids(factory: Callable[..., BaseModel], field: str, bad: object) -> None:
    cls = type(factory())
    fail(
        lambda: factory(**{field: bad}),
        "invalid_response"
        if cls in (c.VoiceResponseDraft, c.AudioPlaybackResult)
        else "invalid_input",
    )


@pytest.mark.parametrize(
    "field,maximum",
    [
        ("speech_start_frames", 8),
        ("speech_end_frames", 50),
        ("max_segment_frames", 256),
    ],
)
@pytest.mark.parametrize("bad", [0, -1, True, False, 1.0, "2", None])
def test_segment_config_strict(field: str, maximum: int, bad: object) -> None:
    fail(lambda: c.VoiceSegmenterConfig.model_validate({field: bad}))
    fail(lambda: c.VoiceSegmenterConfig.model_validate({field: maximum + 1}))
    assert (
        getattr(c.VoiceSegmenterConfig.model_validate({field: maximum}), field)
        == maximum
    )


@pytest.mark.parametrize(
    "bad", [1, True, False, "30", None, 0.9, 600.1, float("nan"), float("inf")]
)
def test_timeout_strict(bad: object) -> None:
    fail(
        lambda: c.VoiceConversationConfig.model_validate(
            {"follow_up_timeout_seconds": bad}
        )
    )


@pytest.mark.parametrize("bad", [0, 9, True, 1.0, "2", None])
def test_barge_config_strict(bad: object) -> None:
    fail(
        lambda: c.VoiceConversationConfig.model_validate(
            {"barge_in_speech_frames": bad}
        )
    )


@pytest.mark.parametrize(
    "factory,field,bound",
    [(response_request, "transcript_text", 20000), (draft, "response_text", 4096)],
)
@pytest.mark.parametrize(
    "bad", [None, True, 1, b"text", "", " \n\t ", "a\0b", "\ud800"]
)
def test_text_strict_privacy(
    factory: Callable[..., BaseModel], field: str, bound: int, bad: object
) -> None:
    code = "invalid_response" if factory is draft else "invalid_input"
    fail(lambda: factory(**{field: bad}), code)
    fail(lambda: factory(**{field: "x" * (bound + 1)}), code)
    assert getattr(factory(**{field: "x" * bound}), field) == "x" * bound
    assert getattr(factory(**{field: PRIVATE}), field) == PRIVATE


@pytest.mark.parametrize("field", ["stt_language_hint", "tts_language_hint"])
@pytest.mark.parametrize("bad", [True, 1, b"en", "", " ", "en\n", "\ud800", "x" * 33])
def test_language_tokens(field: str, bad: object) -> None:
    fail(lambda: turn(**{field: bad}))
    assert getattr(turn(**{field: " en-US "}), field) == "en-US"
    assert getattr(turn(**{field: None}), field) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"stream_id": "other"},
        {"sequence": 9},
        {"captured_at": NOW + timedelta(seconds=1)},
        {"duration": "ms_20"},
        {"activity": True},
        {"score": 1},
        {"approved": True},
    ],
)
def test_observation_exact_correlation(changes: dict[str, object]) -> None:
    value = observation()
    fail(
        lambda: c.VoiceFrameObservation(
            frame=value.frame, decision=value.decision.model_copy(update=changes)
        )
    )
    assert observation(score=0.0).decision.activity.value == "speech"
    assert observation(speech=False, score=1.0).decision.activity.value == "silence"


@pytest.mark.parametrize(
    "changes",
    [
        {"frames": ()},
        {"frames": tuple(frame(i) for i in range(257))},
        {"stream_id": "other"},
        {"start_sequence": 1},
        {"end_sequence": 4},
        {"start_sequence": True},
        {"frames": (frame(), frame(2))},
        {"frames": (frame(), frame(1, stream_id="other"))},
        {"frames": (frame(), frame(1, captured_at=NOW - timedelta(seconds=1)))},
    ],
)
def test_segment_invalid(changes: dict[str, object]) -> None:
    fail(lambda: segment(**changes))


def test_segment_pcm_bound() -> None:
    data = b"a" * 32000
    frames = tuple(
        frame(
            i,
            pcm_base64=base64.b64encode(data).decode(),
            pcm_sha256=sha256(data).hexdigest(),
        )
        for i in range(30)
    )
    assert len(segment(frames=frames, end_sequence=29).frames) == 30
    fail(
        lambda: segment(
            frames=frames
            + (
                frame(
                    30,
                    pcm_base64=base64.b64encode(data).decode(),
                    pcm_sha256=sha256(data).hexdigest(),
                ),
            ),
            end_sequence=30,
        )
    )


def test_segmenter_debounce_hangover_and_reset() -> None:
    value = c.VoiceSegmenter()
    assert (
        value.config == c.VoiceSegmenterConfig()
        and not value.active
        and value.buffered_frame_count == 0
    )
    for i in range(3):
        assert (
            value.feed(observation(i, False)) is None
            and value.buffered_frame_count == 0
        )
    value.feed(observation(3))
    assert not value.active and value.buffered_frame_count == 1
    value.feed(observation(4, False))
    assert value.buffered_frame_count == 0
    value.feed(observation(5))
    value.feed(observation(6))
    assert value.active
    value.feed(observation(7, False))
    value.feed(observation(8))
    for i in range(9, 18):
        assert value.feed(observation(i, False)) is None
    returned = value.feed(observation(18, False))
    assert returned is not None and tuple(f.sequence for f in returned.frames) == (
        5,
        6,
        7,
        8,
    )
    assert returned.start_sequence == 5 and returned.end_sequence == 8
    assert value.buffered_frame_count == 0 and not value.active
    value.feed(observation(50))
    value.reset()
    assert value.buffered_frame_count == 0


@pytest.mark.parametrize("start", range(1, 9))
def test_segmenter_thresholds_and_overflow(start: int) -> None:
    value = c.VoiceSegmenter(
        c.VoiceSegmenterConfig(speech_start_frames=start, speech_end_frames=1)
    )
    for i in range(256):
        value.feed(observation(i))
        assert value.active == (i + 1 >= start)
        assert value.buffered_frame_count == i + 1
    fail(lambda: value.feed(observation(256, False)), "segment_too_long")
    assert value.buffered_frame_count == 0
    for i in range(255):
        value.feed(observation(i))
    returned = value.feed(observation(255, False))
    assert returned is not None and len(returned.frames) == 255
    for i in range(256):
        value.feed(observation(i))
    fail(lambda: value.feed(observation(256)), "segment_too_long")
    assert value.buffered_frame_count == 0 and not value.active


@pytest.mark.parametrize("bad", ["stream", "gap", "time", "forged"])
def test_segmenter_invalid_resets(bad: str) -> None:
    value = c.VoiceSegmenter()
    value.feed(observation())
    next_value = observation(1)
    changes: dict[str, object] = (
        {"stream_id": "wrong"}
        if bad == "stream"
        else {"sequence": 7}
        if bad == "gap"
        else {"captured_at": NOW - timedelta(seconds=1)}
    )
    next_value = (
        next_value.model_copy(update={"approved": True})
        if bad == "forged"
        else next_value.model_copy(
            update={"frame": next_value.frame.model_copy(update=changes)}
        )
    )
    fail(lambda: value.feed(next_value))
    assert not value.active and value.buffered_frame_count == 0


@pytest.mark.parametrize("state", list(c.VoiceConversationState))
def test_interrupted_result_clarification(state: c.VoiceConversationState) -> None:
    if state.value in ("listening", "sleeping"):
        assert result(outcome="interrupted", final_state=state).final_state is state
    else:
        fail(
            lambda: result(outcome="interrupted", final_state=state), "invalid_response"
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": "spoken", "final_state": "sleeping"},
        {"transcript_text": None},
        {"response_text": None},
        {"failure_stage": "tts"},
        {"final_state": "speaking"},
        {"outcome": "text_fallback", "failure_stage": None},
        {"response_text": "x" * 4097},
    ],
)
def test_result_invalid_consistency(changes: dict[str, object]) -> None:
    fail(lambda: result(**changes), "invalid_response")


@pytest.mark.parametrize("stage", list(c.VoiceFailureStage))
def test_fallback_result_consistency(stage: c.VoiceFailureStage) -> None:
    changes: dict[str, object] = dict(
        outcome="text_fallback", failure_stage=stage, final_state="degraded_voice"
    )
    if stage is c.VoiceFailureStage.STT:
        changes.update(
            transcript_text=None, response_text=None, final_state="text_only"
        )
    assert result(**changes).failure_stage is stage


def test_normal_pipeline_followup_observation_events_and_privacy() -> None:
    async def exercise() -> None:
        rig = Rig()
        m = rig.manager
        assert m.owner_id == "voice" and m.state.value == "sleeping"
        assert all(
            value is None
            for value in (
                m.session_id,
                m.active_turn_id,
                m.active_playback_id,
                m.follow_up_deadline,
            )
        )
        assert not m.closed and rig.clock.calls == 0
        sub = await rig.bus.subscribe()
        await m.activate(" session ")
        assert m.follow_up_deadline == 130.0
        with pytest.raises(c.VoiceConversationError, match="^busy$"):
            await m.activate("other")
        rig.clock.value = 120.0
        assert not await m.expire_if_idle()
        assert await m.observe_listening(observation()) is None
        with pytest.raises(c.VoiceConversationError, match="^busy$"):
            await m.process_turn(turn())
        assert await m.observe_listening(observation(1)) is None
        for i in range(2, 11):
            assert await m.observe_listening(observation(i, False)) is None
        collected = await m.observe_listening(observation(11, False))
        assert collected == segment()
        outcome = await m.process_turn(turn(segment=collected))
        assert (
            outcome.outcome.value == "spoken"
            and outcome.transcript_text == PRIVATE
            and outcome.response_text == PRIVATE
        )
        assert PRIVATE not in repr(outcome)
        assert [len(s.calls) for s in rig.stages()] == [1] * 6
        incoming = rig.stt.stage.calls[0]
        assert (
            isinstance(incoming, SttRequest)
            and incoming.operation_id == "turn"
            and incoming.frames == segment().frames
            and incoming.language_hint == "en"
        )
        assert rig.responder.stage.calls == [response_request()]
        outgoing = rig.tts.stage.calls[0]
        assert (
            isinstance(outgoing, TtsRequest)
            and outgoing.text == PRIVATE
            and outgoing.voice_profile_id == "original"
            and outgoing.language_hint == "en"
        )
        assert rig.output.stage.calls == [playback()]
        assert (
            m.follow_up_deadline == 150.0
            and m.active_turn_id is m.active_playback_id is None
        )
        assert await events(sub, 3) == [
            "voice.listening",
            "voice.speaking",
            "voice.listening",
        ]
        await m.process_turn(turn(turn_id="followup"))
        assert [len(s.calls) for s in rig.stages()] == [2] * 6
        assert await events(sub, 2) == ["voice.speaking", "voice.listening"]
        rig.clock.value = 150.0
        assert await m.expire_if_idle()
        assert (
            m.state.value == "sleeping"
            and m.session_id is None
            and m.follow_up_deadline is None
        )
        await m.aclose()
        await m.aclose()
        assert not sub.closed and all(
            p.closed_count == 0 for p in (rig.stt, rig.tts, rig.output)
        )
        await sub.close()

    run(exercise())


@pytest.mark.parametrize("index", range(6))
@pytest.mark.parametrize("kind", ["raise", "wrong-id", "forged"])
def test_stage_failures_and_degraded_recovery(index: int, kind: str) -> None:
    async def exercise() -> None:
        rig = Rig()
        sub = await rig.bus.subscribe()
        await rig.manager.activate("session")
        stage = rig.stages()[index]
        if kind == "raise":
            stage.failure = RuntimeError(PRIVATE)
        else:
            field = (
                "provider_id",
                "provider_id",
                "turn_id",
                "provider_id",
                "provider_id",
                "output_id",
            )[index]
            stage.change = (
                {field: "wrong"} if kind == "wrong-id" else {"approved": True}
            )
        if index == 2:
            with pytest.raises(c.VoiceConversationError, match="^responder_failure$"):
                await rig.manager.process_turn(turn())
            assert (
                rig.manager.state.value == "listening"
                and rig.manager.follow_up_deadline == 130.0
            )
        else:
            returned = await rig.manager.process_turn(turn())
            expected = "stt" if index < 2 else "tts" if index < 5 else "playback"
            assert (
                returned.failure_stage is not None
                and returned.failure_stage.value == expected
            )
            if index < 2:
                assert (
                    rig.manager.state.value == "text_only"
                    and returned.transcript_text is returned.response_text is None
                )
                assert rig.manager.follow_up_deadline is None
            else:
                assert (
                    rig.manager.state.value == "degraded_voice"
                    and returned.transcript_text == PRIVATE
                    and returned.response_text == PRIVATE
                )
                counts = [len(s.calls) for s in rig.stages()]
                assert await rig.manager.observe_listening(observation()) is None
                rig.manager._segmenter.reset()
                second = await rig.manager.process_turn(turn(turn_id="second"))
                assert (
                    second.failure_stage is c.VoiceFailureStage.VOICE_OUTPUT_UNAVAILABLE
                )
                assert [len(s.calls) for s in rig.stages()][3:] == counts[3:]
        stage.failure = None
        stage.change = {}
        if rig.manager.state.value != "listening":
            await rig.manager.activate("session")
        assert (await rig.manager.process_turn(turn())).outcome.value == "spoken"
        assert rig.manager.active_turn_id is rig.manager.active_playback_id is None
        await sub.close()

    run(exercise())


@pytest.mark.parametrize("sleep", [False, True])
@pytest.mark.parametrize("completed", [False, True])
def test_barge_and_sleep_ownership_suppressed_interruption(
    sleep: bool, completed: bool
) -> None:
    async def exercise() -> None:
        rig = Rig()
        sub = await rig.bus.subscribe()
        m = rig.manager
        await m.activate("session")
        rig.output.stage.block = True
        rig.output.stage.change = {
            "status": c.AudioPlaybackStatus.COMPLETED
            if completed
            else c.AudioPlaybackStatus.INTERRUPTED
        }
        task = asyncio.create_task(m.process_turn(turn()))
        await asyncio.wait_for(rig.output.stage.entered.wait(), 1)
        assert (
            m.state.value == "speaking"
            and m.active_turn_id == m.active_playback_id == "turn"
        )
        assert rig.output.active_playback_ids == ("turn",)
        rig.clock.value = 500.0
        assert not await m.expire_if_idle()
        if sleep:
            await m.sleep()
            await m.sleep()
        else:
            assert not await m.observe_barge_in(observation())
            assert not await m.observe_barge_in(observation(1, False))
            assert not await m.observe_barge_in(observation(2))
            assert await m.observe_barge_in(observation(3))
            assert not await m.observe_barge_in(observation(4))
        assert rig.output.interrupt_calls == ["turn"]
        rig.output.stage.release.set()
        returned = await asyncio.wait_for(task, 1)
        assert (
            returned.outcome.value == "interrupted" and returned.failure_stage is None
        )
        assert returned.final_state.value == ("sleeping" if sleep else "listening")
        assert (
            m.state is returned.final_state
            and m.active_turn_id is m.active_playback_id is None
        )
        if sleep:
            assert (
                m.session_id is None
                and m.follow_up_deadline is None
                and m._segmenter.buffered_frame_count == 0
            )
            assert await events(sub, 3) == [
                "voice.listening",
                "voice.speaking",
                "voice.interrupted",
            ]
        else:
            assert m.session_id == "session" and m.follow_up_deadline == 530.0
            assert await events(sub, 4) == [
                "voice.listening",
                "voice.speaking",
                "voice.interrupted",
                "voice.listening",
            ]
        assert rig.stt.cancelled == rig.tts.cancelled == []
        await sub.close()

    run(exercise())


@pytest.mark.parametrize("sleep", [False, True])
@pytest.mark.parametrize(
    "changes",
    [
        {"status": CancellationStatus.NOT_FOUND},
        {"status": CancellationStatus.UNSUPPORTED},
        {"provider_id": "wrong"},
        {"operation_id": "wrong"},
        {"approved": True},
        {"status": "accepted"},
        {"status": None},
    ],
)
def test_interruption_requires_exact_ack_no_fake_event(
    sleep: bool, changes: dict[str, object]
) -> None:
    async def exercise() -> None:
        rig = Rig()
        sub = await rig.bus.subscribe()
        await rig.manager.activate("session")
        rig.output.stage.block = True
        rig.output.interruption.change = changes
        task = asyncio.create_task(rig.manager.process_turn(turn()))
        await asyncio.wait_for(rig.output.stage.entered.wait(), 1)
        with pytest.raises(c.VoiceConversationError, match="^cancellation_failure$"):
            if sleep:
                await rig.manager.sleep()
            else:
                await rig.manager.observe_barge_in(observation())
                await rig.manager.observe_barge_in(observation(1))
        assert not rig.manager._sleep_requested and not rig.manager._interrupted
        rig.output.stage.release.set()
        assert (await task).outcome.value == "spoken"
        assert await events(sub, 3) == [
            "voice.listening",
            "voice.speaking",
            "voice.listening",
        ]
        await sub.close()

    run(exercise())


@pytest.mark.parametrize("index", range(6))
@pytest.mark.parametrize("suppress", [False, True])
def test_caller_cancel_or_suppression_never_advances(
    index: int, suppress: bool
) -> None:
    async def exercise() -> None:
        rig = Rig()
        await rig.manager.activate("session")
        stage = rig.stages()[index]
        stage.block = True
        stage.suppress = suppress
        task = asyncio.create_task(rig.manager.process_turn(turn()))
        await asyncio.wait_for(stage.entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert all(not s.calls for s in rig.stages()[index + 1 :])
        assert rig.manager.active_turn_id is rig.manager.active_playback_id is None
        assert rig.manager.state.value == "sleeping"
        assert (
            rig.output.interrupt_calls == rig.stt.cancelled == rig.tts.cancelled == []
        )

    run(exercise())


@pytest.mark.parametrize("index", [0, 2, 5])
def test_close_active_stops_suppressed_turn_and_keeps_collaborators(index: int) -> None:
    async def exercise() -> None:
        rig = Rig()
        await rig.manager.activate("session")
        stage = rig.stages()[index]
        stage.block = True
        task = asyncio.create_task(rig.manager.process_turn(turn()))
        await asyncio.wait_for(stage.entered.wait(), 1)
        await rig.manager.aclose()
        await rig.manager.aclose()
        stage.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rig.output.interrupt_calls == (["turn"] if index == 5 else [])
        assert rig.manager.closed and rig.manager.state.value == "sleeping"
        assert all(
            v is None
            for v in (
                rig.manager.session_id,
                rig.manager.follow_up_deadline,
                rig.manager.active_turn_id,
                rig.manager.active_playback_id,
            )
        )
        assert all(p.closed_count == 0 for p in (rig.stt, rig.tts, rig.output))
        for method, args in [
            (rig.manager.activate, ("session",)),
            (rig.manager.process_turn, (turn(),)),
            (rig.manager.observe_listening, (observation(),)),
            (rig.manager.observe_barge_in, (observation(),)),
            (rig.manager.sleep, ()),
            (rig.manager.expire_if_idle, ()),
        ]:
            with pytest.raises(c.VoiceConversationError, match="^closed$"):
                await cast(Any, method)(*args)

    run(exercise())


@pytest.mark.parametrize(
    "phase", ["activate", "speaking", "completion", "interruption"]
)
def test_eventbus_full_safe_state_and_no_delivery_claim(phase: str) -> None:
    async def exercise() -> None:
        rig = Rig(EventBus(capacity=1))
        sub = await rig.bus.subscribe()
        if phase == "activate":
            await rig.bus.publish(
                EventEnvelope(
                    event_type=EventType.VOICE_LISTENING,
                    agent_id="voice",
                    correlation_id="session",
                    payload={"session_id": "session", "turn_id": None},
                )
            )
            with pytest.raises(c.VoiceConversationError, match="^event_failure$"):
                await rig.manager.activate("session")
            assert (
                rig.manager.state.value == "sleeping" and rig.manager.session_id is None
            )
        else:
            await rig.manager.activate("session")
            if phase != "speaking":
                await sub.get()
            if phase == "interruption":
                rig.output.stage.block = True
                task = asyncio.create_task(rig.manager.process_turn(turn()))
                await asyncio.wait_for(rig.output.stage.entered.wait(), 1)
                with pytest.raises(c.VoiceConversationError, match="^event_failure$"):
                    await rig.manager.sleep()
                rig.output.stage.release.set()
                with pytest.raises(c.VoiceConversationError, match="^event_failure$"):
                    await task
            else:
                with pytest.raises(c.VoiceConversationError, match="^event_failure$"):
                    await rig.manager.process_turn(turn())
            assert rig.manager.state.value == "sleeping"
            assert rig.manager.active_turn_id is rig.manager.active_playback_id is None
            if phase == "speaking":
                assert rig.output.stage.calls == []
        await sub.close()

    run(exercise())


def test_busy_invalid_state_timeout_sleep_and_loop_contract() -> None:
    async def exercise() -> Rig:
        rig = Rig()
        with pytest.raises(c.VoiceConversationError, match="^invalid_state$"):
            await rig.manager.process_turn(turn())
        await rig.manager.activate("session")
        with pytest.raises(c.VoiceConversationError, match="^busy$"):
            await rig.manager.activate("new")
        with pytest.raises(c.VoiceConversationError, match="^invalid_input$"):
            await rig.manager.process_turn(turn(session_id="wrong"))
        await rig.manager.observe_listening(observation())
        with pytest.raises(c.VoiceConversationError, match="^busy$"):
            await rig.manager.process_turn(turn())
        await rig.manager.sleep()
        assert rig.manager._segmenter.buffered_frame_count == 0
        await rig.manager.activate("session")
        rig.responder.stage.block = True
        task = asyncio.create_task(rig.manager.process_turn(turn()))
        await asyncio.wait_for(rig.responder.stage.entered.wait(), 1)
        assert rig.manager.state.value == "thinking"
        assert not await rig.manager.expire_if_idle()
        for method, args in [
            (rig.manager.process_turn, (turn(),)),
            (rig.manager.sleep, ()),
            (rig.manager.activate, ("new",)),
        ]:
            with pytest.raises(c.VoiceConversationError, match="^busy$"):
                await cast(Any, method)(*args)
        rig.responder.stage.release.set()
        await task
        rig.clock.value = 130.0
        with pytest.raises(c.VoiceConversationError, match="^invalid_state$"):
            await rig.manager.observe_listening(observation())
        assert rig.manager.session_id is None
        return rig

    rig = run(exercise())
    for value in (
        rig.manager.activate("session"),
        rig.manager.aclose(),
        rig.manager.expire_if_idle(),
    ):
        fail(lambda: run(value), "wrong_event_loop")


@pytest.mark.parametrize(
    "authority", [PermissionGrant, UserPermissionDecision, ToolDefinition]
)
@pytest.mark.parametrize(
    "factory", [response_request, draft, turn, result, playback, observation]
)
def test_voice_evidence_not_permission_or_tool(
    authority: type[BaseModel], factory: Callable[..., BaseModel]
) -> None:
    value = factory()
    for evidence in (value, value.model_dump()):
        with pytest.raises(ValidationError):
            authority.model_validate(evidence)


def test_public_identity_pure_helpers_and_correlations() -> None:
    for value, field in [
        (FakeResponder(), "responder_id"),
        (FakeOutput(), "output_id"),
        (c.VoiceSegmenter(), "config"),
    ]:
        with pytest.raises(AttributeError):
            setattr(value, field, "wrong")
    for bad in (True, None, "", "x\n", "\ud800", "x" * 257):
        fail(lambda: FakeResponder(cast(Any, bad)))
        fail(lambda: FakeOutput(cast(Any, bad)))
    draft().validate_request(response_request())
    fail(
        lambda: draft(turn_id="wrong").validate_request(response_request()),
        "invalid_response",
    )
    playback_result().validate_request(playback(), "output")
    fail(
        lambda: playback_result(output_id="wrong").validate_request(
            playback(), "output"
        ),
        "invalid_response",
    )
    fail(lambda: playback(audio=audio().model_copy(update={"pcm_sha256": "a" * 64})))
    assert all(
        inspect.iscoroutinefunction(getattr(c.AudioOutput, name))
        for name in ("play", "interrupt", "aclose")
    )


def test_static_isolation_no_concrete_engine_or_unauthorized_import() -> None:
    source = Path(c.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = {
        "__future__",
        "asyncio",
        "math",
        "abc",
        "collections.abc",
        "enum",
        "typing",
        "pydantic",
        "phoenix.core.event_bus",
        "phoenix.core.events",
        "phoenix.models.provider",
        "phoenix.voice.audio",
        "phoenix.voice.stt",
        "phoenix.voice.tts",
        "phoenix.voice.vad",
    }
    forbidden = {
        "create_task",
        "ensure_future",
        "to_thread",
        "run_in_executor",
        "open",
        "print",
        "getenv",
        "Popen",
        "socket",
        "connect",
        "execute",
        "subscribe",
        "transcribe_startup",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        elif isinstance(node, ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            assert name not in forbidden
        elif isinstance(node, ast.ClassDef):
            assert not node.name.startswith(
                ("Fake", "Mock", "Coqui", "Xtts", "Windows")
            )
    assert ScriptDirectory.from_config(
        Config(str(ROOT / "alembic.ini"))
    ).get_heads() == ["0008_mission_triage"]


def test_guarded_uncached_construction_no_events_or_clock_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = compile(Path(c.__file__).read_text(encoding="utf-8"), "conversation", "exec")
    rig = Rig()

    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("Unexpected side effect")

    with monkeypatch.context() as scope:
        for target, name in [
            (builtins, "open"),
            (Path, "open"),
            (sqlite3, "connect"),
            (socket, "socket"),
            (subprocess, "Popen"),
            (threading.Thread, "start"),
            (asyncio, "create_task"),
            (EventBus, "publish"),
            (logging.Logger, "_log"),
        ]:
            scope.setattr(target, name, forbidden)
        namespace: dict[str, Any] = {"__name__": "isolated_conversation"}
        exec(code, namespace)
        module_manager = namespace["VoiceConversationManager"]
        # Isolated ABC identities require isolated subclasses, without running stages.
        responder = type(
            "TestResponder",
            (namespace["VoiceResponder"],),
            {"respond": FakeResponder.respond},
        )("responder")
        output = type(
            "TestOutput",
            (namespace["AudioOutput"],),
            {
                "active_playback_ids": property(lambda self: ()),
                "play": FakeOutput.play,
                "interrupt": FakeOutput.interrupt,
                "aclose": FakeOutput.aclose,
            },
        )("output")
        manager = module_manager(
            rig.stt, rig.tts, responder, output, rig.bus, clock=forbidden
        )
        assert manager.state.value == "sleeping" and manager._loop is None
        namespace["VoiceSegmenter"]()
        assert all(not stage.calls for stage in rig.stages())


def test_repository_credential_signature_scan() -> None:
    paths = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    ).splitlines()
    paths.extend(
        ["phoenix/voice/conversation/__init__.py", "tests/test_voice_conversation.py"]
    )
    patterns = [
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE" + r" KEY-----",
        r"github_" + r"pat_[A-Za-z0-9_]{20,}",
        r"gh" + r"p_[A-Za-z0-9]{30,}",
        r"xox" + r"[baprs]-[A-Za-z0-9-]{20,}",
        r"AK" + r"IA[0-9A-Z]{16}",
    ]
    for path in set(paths):
        content = (ROOT / path).read_bytes().decode("utf-8", errors="replace")
        assert not any(re.search(pattern, content) for pattern in patterns), path


@pytest.mark.parametrize("outcome", list(c.VoiceTurnOutcome))
@pytest.mark.parametrize("state", list(c.VoiceConversationState))
@pytest.mark.parametrize("stage", [None, *list(c.VoiceFailureStage)])
@pytest.mark.parametrize(
    "texts", [(None, None), (PRIVATE, None), (None, PRIVATE), (PRIVATE, PRIVATE)]
)
def test_exhaustive_turn_result_consistency(
    outcome: c.VoiceTurnOutcome,
    state: c.VoiceConversationState,
    stage: c.VoiceFailureStage | None,
    texts: tuple[str | None, str | None],
) -> None:
    present = all(text is not None for text in texts)
    valid = (
        outcome is c.VoiceTurnOutcome.SPOKEN
        and stage is None
        and present
        and state is c.VoiceConversationState.LISTENING
        or outcome is c.VoiceTurnOutcome.INTERRUPTED
        and stage is None
        and present
        and state
        in (c.VoiceConversationState.LISTENING, c.VoiceConversationState.SLEEPING)
        or outcome is c.VoiceTurnOutcome.TEXT_FALLBACK
        and stage is c.VoiceFailureStage.STT
        and texts == (None, None)
        and state is c.VoiceConversationState.TEXT_ONLY
        or outcome is c.VoiceTurnOutcome.TEXT_FALLBACK
        and stage
        in (
            c.VoiceFailureStage.TTS,
            c.VoiceFailureStage.PLAYBACK,
            c.VoiceFailureStage.VOICE_OUTPUT_UNAVAILABLE,
        )
        and present
        and state is c.VoiceConversationState.DEGRADED_VOICE
    )

    def call() -> c.VoiceTurnResult:
        return result(
            outcome=outcome,
            final_state=state,
            failure_stage=stage,
            transcript_text=texts[0],
            response_text=texts[1],
        )

    if valid:
        assert call().outcome is outcome
    else:
        fail(call, "invalid_response")


@pytest.mark.parametrize(
    "factory,field,enum",
    [
        (result, "outcome", c.VoiceTurnOutcome),
        (result, "final_state", c.VoiceConversationState),
        (result, "failure_stage", c.VoiceFailureStage),
        (playback_result, "status", c.AudioPlaybackStatus),
    ],
)
def test_foreign_copied_enums(
    factory: Callable[..., BaseModel], field: str, enum: type[StrEnum]
) -> None:
    member = next(iter(enum))
    foreign = next(iter(StrEnum("Foreign", {"VALUE": member.value})))
    copied = str.__new__(enum, member.value)
    copied._name_ = member.name
    copied._value_ = member.value
    for value in (copied, foreign):
        fail(lambda: factory(**{field: value}), "invalid_response")
    for error, code in [
        (c.VoiceConversationError, c.VoiceConversationErrorCode.INVALID_INPUT),
        (c.AudioOutputError, c.AudioOutputErrorCode.INVALID_INPUT),
    ]:
        with pytest.raises(ValueError, match="^invalid_input$"):
            cast(Any, error)(code.value)


@pytest.mark.parametrize("sleep", [False, True])
def test_delayed_ack_serializes_completion_and_duplicate_interrupt(sleep: bool) -> None:
    async def exercise() -> None:
        rig = Rig(config=c.VoiceConversationConfig(barge_in_speech_frames=1))
        sub = await rig.bus.subscribe()
        await rig.manager.activate("session")
        rig.output.stage.block = True
        rig.output.interruption.block = True
        task = asyncio.create_task(rig.manager.process_turn(turn()))
        await asyncio.wait_for(rig.output.stage.entered.wait(), 1)
        first = asyncio.create_task(
            rig.manager.sleep()
            if sleep
            else rig.manager.observe_barge_in(observation())
        )
        await asyncio.wait_for(rig.output.interruption.entered.wait(), 1)
        duplicate = asyncio.create_task(
            rig.manager.sleep()
            if sleep
            else rig.manager.observe_barge_in(observation(1))
        )
        rig.output.stage.release.set()
        await asyncio.sleep(0)
        assert not task.done() and rig.output.interrupt_calls == ["turn"]
        rig.output.interruption.release.set()
        await first
        await duplicate
        returned = await task
        assert returned.outcome.value == "interrupted"
        assert returned.final_state.value == ("sleeping" if sleep else "listening")
        assert await events(sub, 3 if sleep else 4) == (
            ["voice.listening", "voice.speaking", "voice.interrupted"]
            + ([] if sleep else ["voice.listening"])
        )
        await sub.close()

    run(exercise())


@pytest.mark.parametrize("state", ["listening", "degraded_voice", "text_only"])
def test_sleep_each_idle_state_resets_without_parsing(state: str) -> None:
    async def exercise() -> None:
        rig = Rig()
        sub = await rig.bus.subscribe()
        await rig.manager.activate("session")
        if state == "degraded_voice":
            rig.tts.stage.failure = RuntimeError(PRIVATE)
            assert (await rig.manager.process_turn(turn())).final_state.value == state
        elif state == "text_only":
            rig.stt.stage.failure = RuntimeError(PRIVATE)
            assert (await rig.manager.process_turn(turn())).final_state.value == state
        else:
            assert (await rig.manager.process_turn(turn())).outcome.value == "spoken"
            # The responder's approval-looking text was not parsed into commands.
            assert rig.manager.state.value == "listening"
            await rig.manager.observe_listening(observation())
        await rig.manager.sleep()
        assert rig.manager.state.value == "sleeping"
        assert rig.manager.session_id is None and rig.manager.follow_up_deadline is None
        assert rig.manager._segmenter.buffered_frame_count == 0
        await sub.close()

    run(exercise())


@pytest.mark.parametrize("sleep", [False, True])
def test_interrupt_exception_is_safe_and_spontaneous_interrupted_fails(
    sleep: bool,
) -> None:
    async def exercise() -> None:
        rig = Rig(config=c.VoiceConversationConfig(barge_in_speech_frames=1))
        await rig.manager.activate("session")
        rig.output.stage.block = True
        rig.output.interruption.failure = c.AudioOutputError(
            c.AudioOutputErrorCode.UNAVAILABLE
        )
        task = asyncio.create_task(rig.manager.process_turn(turn()))
        await asyncio.wait_for(rig.output.stage.entered.wait(), 1)
        with pytest.raises(c.VoiceConversationError, match="^cancellation_failure$"):
            if sleep:
                await rig.manager.sleep()
            else:
                await rig.manager.observe_barge_in(observation())
        rig.output.stage.change = {"status": c.AudioPlaybackStatus.INTERRUPTED}
        rig.output.stage.release.set()
        returned = await task
        assert (
            returned.outcome.value == "text_fallback"
            and returned.failure_stage is c.VoiceFailureStage.PLAYBACK
        )
        assert returned.transcript_text == PRIVATE and returned.response_text == PRIVATE

    run(exercise())


def test_fresh_interpreter_import() -> None:
    script = """
import importlib.abc
import sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('phoenix.agents', 'phoenix.tools', 'phoenix.security',
                                'phoenix.memory', 'phoenix.rag', 'phoenix.persistence',
                                'phoenix.api', 'phoenix.web', 'phoenix.voice.wake')):
            raise AssertionError('Unexpected integration import')
sys.meta_path.insert(0, Guard())
from phoenix.voice.conversation import VoiceSegmenter, VoiceConversationConfig
assert VoiceSegmenter().buffered_frame_count == 0
assert VoiceConversationConfig().follow_up_timeout_seconds == 30.0
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
    "stage,changes",
    [
        (1, {"operation_id": "wrong"}),
        (1, {"stream_id": "wrong"}),
        (1, {"start_sequence": 7}),
        (1, {"end_sequence": 7}),
        (1, {"text": ""}),
        (2, {"session_id": "wrong"}),
        (2, {"response_text": ""}),
        (4, {"operation_id": "wrong"}),
        (4, {"voice_profile_id": "wrong"}),
        (4, {"sample_rate_hz": 24000}),
        (4, {"channels": 2}),
        (4, {"sample_width_bytes": 1}),
        (4, {"encoding": "wav"}),
        (4, {"pcm_sha256": "a" * 64}),
        (5, {"playback_id": "wrong"}),
        (5, {"status": "interrupted"}),
        (5, {"status": "unknown"}),
    ],
)
def test_all_stage_correlations(stage: int, changes: dict[str, object]) -> None:
    async def exercise() -> None:
        rig = Rig()
        await rig.manager.activate("session")
        rig.stages()[stage].change = changes
        if stage == 2:
            with pytest.raises(c.VoiceConversationError, match="^responder_failure$"):
                await rig.manager.process_turn(turn())
        else:
            returned = await rig.manager.process_turn(turn())
            assert returned.failure_stage is not None
            assert returned.failure_stage.value == (
                "stt" if stage == 1 else "tts" if stage == 4 else "playback"
            )
        assert all(not s.calls for s in rig.stages()[stage + 1 :])

    run(exercise())


@pytest.mark.parametrize("threshold", range(1, 9))
def test_configured_barge_threshold(threshold: int) -> None:
    async def exercise() -> None:
        rig = Rig(config=c.VoiceConversationConfig(barge_in_speech_frames=threshold))
        await rig.manager.activate("session")
        rig.output.stage.block = True
        task = asyncio.create_task(rig.manager.process_turn(turn()))
        await asyncio.wait_for(rig.output.stage.entered.wait(), 1)
        for index in range(threshold):
            assert await rig.manager.observe_barge_in(
                observation(index, score=0.0)
            ) == (index == threshold - 1)
        rig.output.stage.release.set()
        assert (await task).outcome.value == "interrupted"
        assert rig.output.interrupt_calls == ["turn"]

    run(exercise())


def test_no_hidden_tasks_and_close_failed_interrupt_cleanup() -> None:
    async def exercise() -> None:
        before = asyncio.all_tasks()
        rig = Rig()
        assert asyncio.all_tasks() == before
        await rig.manager.activate("session")
        assert asyncio.all_tasks() == before
        await rig.manager.process_turn(turn())
        assert asyncio.all_tasks() == before
        rig.output.stage.block = True
        rig.output.interruption.change = {"provider_id": "wrong"}
        task = asyncio.create_task(rig.manager.process_turn(turn()))
        await asyncio.wait_for(rig.output.stage.entered.wait(), 1)
        with pytest.raises(c.VoiceConversationError, match="^cancellation_failure$"):
            await rig.manager.aclose()
        assert rig.manager.closed and rig.manager.state.value == "sleeping"
        rig.output.stage.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rig.output.closed_count == 0

    run(exercise())


def test_integer_subclasses_rejected() -> None:
    class Number(int):
        pass

    for call in (
        lambda: c.VoiceSegmenterConfig(speech_start_frames=Number(2)),
        lambda: c.VoiceConversationConfig(barge_in_speech_frames=Number(2)),
        lambda: segment(start_sequence=Number(0)),
    ):
        fail(call)


@pytest.mark.parametrize("activity", [True, False])
def test_total_segment_buffer_bound_includes_pending_silence(activity: bool) -> None:
    value = c.VoiceSegmenter(c.VoiceSegmenterConfig(max_segment_frames=3))
    value.feed(observation())
    value.feed(observation(1))
    value.feed(observation(2, False))
    assert value.buffered_frame_count == 3
    fail(lambda: value.feed(observation(3, activity)), "segment_too_long")
    assert value.buffered_frame_count == 0 and not value.active


def test_cancelled_interrupt_event_cannot_finalize_observable_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        rig = Rig()
        await rig.manager.activate("session")
        rig.output.stage.block = True
        task = asyncio.create_task(rig.manager.process_turn(turn()))
        await asyncio.wait_for(rig.output.stage.entered.wait(), 1)
        entered = asyncio.Event()
        release = asyncio.Event()
        original = rig.bus.publish

        async def delayed(event: EventEnvelope) -> None:
            if event.event_type is EventType.VOICE_INTERRUPTED:
                entered.set()
                await release.wait()
            await original(event)

        monkeypatch.setattr(rig.bus, "publish", delayed)
        sleep_task = asyncio.create_task(rig.manager.sleep())
        await asyncio.wait_for(entered.wait(), 1)
        sleep_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sleep_task
        rig.output.stage.release.set()
        with pytest.raises(c.VoiceConversationError, match="^event_failure$"):
            await task
        assert rig.manager.active_turn_id is rig.manager.active_playback_id is None

    run(exercise())
