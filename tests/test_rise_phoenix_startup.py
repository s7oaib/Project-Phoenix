"""PHX-065 same-run synthetic probes/voice, real EventBus and conversation."""

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

from phoenix.agents.failsafe import (
    FailSafeCapability,
    FailSafeDecision,
    FailSafeRequest,
    FailSafeTrigger,
    KaidoFailSafe,
)
from phoenix.core.event_bus import EventBus
from phoenix.core.events import EventEnvelope, EventType
from phoenix.models.provider import CancellationResult, CancellationStatus
from phoenix.security.permissions import PermissionGrant, UserPermissionDecision
from phoenix.tools.registry import ToolDefinition
from phoenix.voice import startup as s
from phoenix.voice.audio import AudioEncoding, PcmAudioFrame
from phoenix.voice.conversation import (
    AudioOutput,
    AudioPlaybackRequest,
    AudioPlaybackResult,
    AudioPlaybackStatus,
    VoiceConversationManager,
    VoiceConversationState,
    VoiceResponder,
    VoiceResponseDraft,
    VoiceResponseRequest,
)
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
from phoenix.voice.wake import (
    DEFAULT_WAKE_PHRASE,
    WakePhrase,
    WakeWordCapabilities,
    WakeWordDetection,
    WakeWordProvider,
    WakeWordRequest,
)

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = "PRIVATE-BACKEND-DIAGNOSTIC-PCM-TRANSCRIPT-CREDENTIAL"
T = TypeVar("T")
NOW = datetime(2026, 10, 7, tzinfo=UTC)
PCM = b"\x01\x02" * 160


def run(value: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(value)


def wake_request(**changes: Any) -> WakeWordRequest:
    return WakeWordRequest.model_validate(
        dict(
            operation_id="wake-operation", phrase=DEFAULT_WAKE_PHRASE, frames=(frame(),)
        )
        | changes
    )


def request(**changes: Any) -> s.RisePhoenixStartupRequest:
    return s.RisePhoenixStartupRequest.model_validate(
        dict(
            startup_id="startup",
            session_id="session",
            wake_request=wake_request(),
            tts_voice_profile_id="original",
            tts_language_hint="en",
        )
        | changes
    )


def check(**changes: Any) -> s.StartupCheckResult:
    return s.StartupCheckResult.model_validate(
        dict(startup_id="startup", subsystem="persistence", status="healthy") | changes
    )


def decision(**changes: Any) -> FailSafeDecision:
    value = KaidoFailSafe().activate(
        FailSafeRequest(
            failsafe_id="startup",
            trigger=FailSafeTrigger.CRITICAL_SUBSYSTEM_DEGRADATION,
            reason="Startup health or voice output is degraded.",
            requested_capabilities=(
                FailSafeCapability.BASIC_CONVERSATION,
                FailSafeCapability.DIAGNOSTICS,
                FailSafeCapability.RECOVERY_GUIDANCE,
            ),
            recovery_result=None,
        )
    )
    return value.model_copy(update=changes)


def result(**changes: Any) -> s.RisePhoenixStartupResult:
    return s.RisePhoenixStartupResult.model_validate(
        dict(
            startup_id="startup",
            session_id="session",
            outcome="ready",
            checks=tuple(
                check(subsystem=subsystem) for subsystem in s.StartupSubsystem
            ),
            response_text=s.STARTUP_HEALTHY_RESPONSE,
            voice_failure_stage=None,
            failsafe_decision=None,
            final_voice_state="listening",
        )
        | changes
    )


def fail(call: Callable[[], object], code: str = "invalid_response") -> None:
    with pytest.raises(s.StartupError) as caught:
        call()
    assert caught.value.args == (code,) and str(caught.value) == code
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))


class Gate:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.changes: dict[int, dict[str, Any]] = {}
        self.failures: dict[int, Exception] = {}
        self.block_at: int | None = None
        self.suppress = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def invoke(self, incoming: object, value: T) -> T:
        self.calls.append(incoming)
        count = len(self.calls)
        if count == self.block_at:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                if not self.suppress:
                    raise
        if count in self.failures:
            raise self.failures[count]
        if count in self.changes:
            return cast(
                T, cast(BaseModel, value).model_copy(update=self.changes[count])
            )
        return value


Stage = Gate


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


class FakeResponder(VoiceResponder):
    def __init__(self, identity: str = "responder") -> None:
        super().__init__(identity)
        self.stage = Stage()

    async def respond(self, request: VoiceResponseRequest) -> VoiceResponseDraft:
        return await self.stage.invoke(
            request,
            VoiceResponseDraft(
                session_id=request.session_id,
                turn_id=request.turn_id,
                response_text=PRIVATE,
            ),
        )


class FakeOutput(AudioOutput):
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

    async def play(self, request: AudioPlaybackRequest) -> AudioPlaybackResult:
        self._active.add(request.playback_id)
        try:
            return await self.stage.invoke(
                request,
                AudioPlaybackResult(
                    status=AudioPlaybackStatus.COMPLETED,
                    playback_id=request.playback_id,
                    output_id=self.output_id,
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


class Wake(WakeWordProvider):
    def __init__(self) -> None:
        super().__init__("wake")
        self.cap = Gate()
        self.detect_gate = Gate()
        self.close_count = 0
        self.cancelled: list[str] = []

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return ()

    async def capabilities(self) -> WakeWordCapabilities:
        return await self.cap.invoke(
            None,
            WakeWordCapabilities(
                provider_id="wake",
                supported_phrases=(DEFAULT_WAKE_PHRASE,),
                sample_rate_hz=16000,
                channels=1,
                sample_width_bytes=2,
                encoding=AudioEncoding.PCM_S16LE,
                supports_cancellation=True,
            ),
        )

    async def detect(self, incoming: WakeWordRequest) -> WakeWordDetection:
        return await self.detect_gate.invoke(
            incoming,
            WakeWordDetection(
                operation_id=incoming.operation_id,
                provider_id="wake",
                phrase_id=incoming.phrase.phrase_id,
                stream_id=incoming.frames[0].stream_id,
                start_sequence=incoming.frames[0].sequence,
                end_sequence=incoming.frames[-1].sequence,
                detected=True,
                score=0.0,
            ),
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        self.cancelled.append(operation_id)
        return CancellationResult(
            provider_id="wake",
            operation_id=operation_id,
            status=CancellationStatus.UNSUPPORTED,
        )

    async def aclose(self) -> None:
        self.close_count += 1


class Probe(s.StartupHealthProbe):
    def __init__(self, subsystem: s.StartupSubsystem, order: list[str]) -> None:
        super().__init__(subsystem)
        self.gate = Gate()
        self.order = order

    async def check(self, startup_id: str) -> s.StartupCheckResult:
        self.order.append(self.subsystem.value)
        return await self.gate.invoke(
            startup_id, check(startup_id=startup_id, subsystem=self.subsystem)
        )


class Bus(EventBus):
    def __init__(self, capacity: int = 256) -> None:
        super().__init__(capacity)
        self.gate = Gate()
        self.delivered: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        await self.gate.invoke(event, event)
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            return  # Deliberately suppress without fabricating delivery.
        await super().publish(event)
        self.delivered.append(event)


class Conversation(VoiceConversationManager):
    def __init__(self, tts: FakeTts, output: FakeOutput, bus: EventBus) -> None:
        self.activate_gate = Gate()
        self.sleep_calls = 0
        self.sleep_failure: Exception | None = None
        super().__init__(FakeStt(), tts, FakeResponder(), output, bus, clock=Clock())

    async def activate(self, session_id: str) -> None:
        await self.activate_gate.invoke(session_id, None)
        await super().activate(session_id)

    async def sleep(self) -> None:
        self.sleep_calls += 1
        if self.sleep_failure:
            raise self.sleep_failure
        await super().sleep()


class Policy(KaidoFailSafe):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[FailSafeRequest] = []
        self.change: dict[str, Any] = {}
        self.failure: Exception | None = None

    def activate(self, incoming: FailSafeRequest) -> FailSafeDecision:
        self.calls.append(incoming)
        if self.failure:
            raise self.failure
        return super().activate(incoming).model_copy(update=self.change)


class Rig:
    def __init__(self, **changes: Any) -> None:
        self.order: list[str] = []
        self.wake = Wake()
        self.probes = tuple(
            Probe(subsystem, self.order) for subsystem in s.StartupSubsystem
        )
        self.tts = FakeTts()
        self.tts.cap = cast(Any, Gate())
        self.tts.stage = cast(Any, Gate())
        self.output = FakeOutput()
        self.output.stage = cast(Any, Gate())
        self.bus = Bus()
        self.conversation = Conversation(self.tts, self.output, self.bus)
        self.policy = Policy()
        self.coordinator = s.RisePhoenixStartupCoordinator(
            **cast(
                dict[str, Any],
                (
                    dict(
                        wake_provider=self.wake,
                        probes=self.probes,
                        tts_provider=self.tts,
                        audio_output=self.output,
                        conversation=self.conversation,
                        event_bus=self.bus,
                        failsafe=self.policy,
                    )
                    | changes
                ),
            )
        )

    def gates(self) -> dict[str, Gate]:
        values = {
            "wake_cap": self.wake.cap,
            "wake_detect": self.wake.detect_gate,
            "tts_cap": self.tts.cap,
            "synthesis": self.tts.stage,
            "playback": self.output.stage,
            "activate": self.conversation.activate_gate,
            "event": self.bus.gate,
        }
        values.update(
            {
                "probe_" + str(index): probe.gate
                for index, probe in enumerate(self.probes)
            }
        )
        return values


def assert_events(rig: Rig, outcome: str) -> None:
    values = rig.bus.delivered
    assert [event.event_type.value for event in values] == [
        "phoenix.started",
        "voice.listening",
        "phoenix." + outcome,
    ]
    for event in values:
        assert (
            PRIVATE not in event.model_dump_json()
            and "pcm" not in event.model_dump_json()
        )
        if event.agent_id == "phoenix":
            assert event.correlation_id == "startup"
            expected = (
                {"startup_id", "session_id"}
                if event.event_type is EventType.PHOENIX_STARTED
                else {
                    "startup_id",
                    "session_id",
                    "outcome",
                    "unhealthy_subsystems",
                    "voice_failure_stage",
                }
            )
            assert set(event.payload) == expected
            assert (
                "response_text" not in event.payload and "reason" not in event.payload
            )
        else:
            assert event.agent_id == "voice" and event.correlation_id == "session"


def test_exact_text_vocabularies_errors_and_probe_abc() -> None:
    assert s.STARTUP_ACKNOWLEDGEMENT == (
        "Wake command acknowledged. Initializing Phoenix network. "
        "Checking all agents and core systems."
    )
    assert (
        s.STARTUP_HEALTHY_RESPONSE
        == "All agents online. All core systems operational. What's the mission?"
    )
    assert s.STARTUP_DEGRADED_RESPONSE == (
        "Phoenix initialized in degraded mode. Some core systems are unavailable. "
        "Continuing with available capabilities."
    )
    for enum, expected in [
        (s.StartupSubsystem, "persistence hardware model_provider tools voice agents"),
        (s.StartupCheckStatus, "healthy degraded unavailable"),
        (s.StartupOutcome, "ready degraded"),
        (s.StartupVoiceFailureStage, "tts playback"),
        (
            s.StartupErrorCode,
            "invalid_input invalid_response busy closed wrong_event_loop wake_failure "
            "health_failure tts_failure playback_failure conversation_failure "
            "event_failure failsafe_failure",
        ),
    ]:
        assert [value.value for value in enum] == expected.split()
    for code in s.StartupErrorCode:
        assert str(s.StartupError(code)) == code.value
    assert s.StartupHealthProbe.__abstractmethods__ == {"check"}
    with pytest.raises(TypeError):
        s.StartupHealthProbe(s.StartupSubsystem.AGENTS)  # type: ignore[abstract]
    assert inspect.iscoroutinefunction(s.StartupHealthProbe.check)
    assert len(EventType) == 32


FACTORIES = (check, request, result)
FIELDS = (
    "startup_id subsystem status",
    "startup_id session_id wake_request tts_voice_profile_id tts_language_hint",
    "startup_id session_id outcome checks response_text voice_failure_stage "
    "failsafe_decision final_voice_state",
)


@pytest.mark.parametrize("factory,fields", zip(FACTORIES, FIELDS))
def test_exact_frozen_detached_json(
    factory: Callable[..., BaseModel], fields: str
) -> None:
    value = factory()
    assert set(type(value).model_fields) == set(fields.split())
    assert type(value).model_validate_json(value.model_dump_json()) == value
    assert type(value).model_validate(value) is not value
    with pytest.raises(ValidationError):
        setattr(value, fields.split()[0], None)
    assert "pcm_base64" not in repr(value) and PRIVATE not in repr(value)


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
def test_stored_forgery_rejected(
    factory: Callable[..., BaseModel], attack: str
) -> None:
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
        "invalid_input" if factory is request else "invalid_response",
    )


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize(
    "extra",
    [
        "healthy",
        "detail",
        "permission",
        "tool",
        "risk",
        "transcript",
        "model_id",
        "device",
        "api_key",
        "callback",
        "output_path",
        "startup_text",
        "health_override",
        "provider",
        "verified",
        "success",
        "memory",
    ],
)
def test_no_extra_backend_health_authority_fields(
    factory: Callable[..., BaseModel], extra: str
) -> None:
    value = factory()
    fail(
        lambda: type(value).model_validate(value.model_dump() | {extra: PRIVATE}),
        "invalid_input" if factory is request else "invalid_response",
    )


@pytest.mark.parametrize(
    "factory,field,bound",
    [
        (check, "startup_id", 256),
        (request, "startup_id", 256),
        (request, "session_id", 256),
        (request, "tts_voice_profile_id", 128),
        (result, "startup_id", 256),
        (result, "session_id", 256),
    ],
)
@pytest.mark.parametrize(
    "bad", [None, True, 1, 1.0, b"text", "", " ", "x\n", "x\0", "x\x85", "\ud800"]
)
def test_strict_identifiers(
    factory: Callable[..., BaseModel], field: str, bound: int, bad: object
) -> None:
    code = "invalid_input" if factory is request else "invalid_response"
    fail(lambda: factory(**{field: bad}), code)
    fail(lambda: factory(**{field: "x" * (bound + 1)}), code)


@pytest.mark.parametrize(
    "factory,field,enum",
    [
        (check, "subsystem", s.StartupSubsystem),
        (check, "status", s.StartupCheckStatus),
        (result, "outcome", s.StartupOutcome),
        (result, "voice_failure_stage", s.StartupVoiceFailureStage),
        (result, "final_voice_state", VoiceConversationState),
    ],
)
def test_copied_foreign_enums(
    factory: Callable[..., BaseModel], field: str, enum: type[StrEnum]
) -> None:
    member = next(iter(enum))
    copied = str.__new__(enum, member.value)
    copied._name_ = member.name
    copied._value_ = member.value
    foreign = next(iter(StrEnum("Foreign", {"VALUE": member.value})))
    for value in (copied, foreign, True, 1):
        fail(lambda: factory(**{field: value}))
    with pytest.raises(ValueError, match="^invalid_input$"):
        s.StartupError(cast(Any, "wake_failure"))


@pytest.mark.parametrize(
    "phrase_id,text",
    [
        ("other", "Rise, Phoenix"),
        ("rise_phoenix", "rise, phoenix"),
        ("rise_phoenix", "Rise Phoenix"),
        ("rise_phoenix", "Rise, Phoenix!"),
        ("alias", "UTA"),
        ("rise_phoenix", "Phoenix"),
    ],
)
def test_only_exact_wake_phrase(phrase_id: str, text: str) -> None:
    fail(
        lambda: request(
            wake_request=wake_request(phrase=WakePhrase(phrase_id=phrase_id, text=text))
        ),
        "invalid_input",
    )


@pytest.mark.parametrize("bad", [True, 1, b"en", "", " ", "en\n", "\ud800", "x" * 33])
def test_language_strict(bad: object) -> None:
    fail(lambda: request(tts_language_hint=bad), "invalid_input")
    assert request(tts_language_hint=" en-US ").tts_language_hint == "en-US"
    assert request(tts_language_hint=None).tts_language_hint is None


@pytest.mark.parametrize(
    "kind", ["missing", "extra", "duplicate", "reorder", "wrong-object", "generator"]
)
def test_exact_probe_configuration(kind: str) -> None:
    rig = Rig()
    probes: Any = rig.probes
    if kind == "missing":
        probes = probes[:-1]
    elif kind == "extra":
        probes = probes + (probes[0],)
    elif kind == "duplicate":
        probes = probes[:-1] + (probes[0],)
    elif kind == "reorder":
        probes = tuple(reversed(probes))
    elif kind == "wrong-object":
        probes = (object(),) + probes[1:]
    else:
        probes = iter(probes)
    fail(
        lambda: s.RisePhoenixStartupCoordinator(
            rig.wake, probes, rig.tts, rig.output, rig.conversation, rig.bus, rig.policy
        ),
        "invalid_input",
    )
    assert not rig.order and not rig.wake.cap.calls


def test_healthy_complete_pipeline_exact_calls_events_and_no_cached_health() -> None:
    async def exercise() -> Rig:
        rig = Rig()
        assert (
            rig.coordinator.owner_id == "phoenix"
            and rig.coordinator.active_startup_id is None
        )
        assert not rig.coordinator.closed and rig.coordinator._loop is None
        assert all(not gate.calls for gate in rig.gates().values())
        before = asyncio.all_tasks()
        returned = await rig.coordinator.handle_wake(request())
        assert returned == result()
        assert len(rig.wake.cap.calls) == len(rig.wake.detect_gate.calls) == 1
        assert rig.wake.detect_gate.calls == [wake_request()]
        assert rig.order == [member.value for member in s.StartupSubsystem]
        assert [probe.gate.calls for probe in rig.probes] == [["startup"]] * 6
        assert (
            rig.conversation.activate_gate.calls == ["session"]
            and rig.policy.calls == []
        )
        assert (
            len(rig.tts.cap.calls)
            == len(rig.tts.stage.calls)
            == len(rig.output.stage.calls)
            == 2
        )
        assert [cast(Any, value).text for value in rig.tts.stage.calls] == [
            s.STARTUP_ACKNOWLEDGEMENT,
            s.STARTUP_HEALTHY_RESPONSE,
        ]
        assert all(
            cast(Any, value).voice_profile_id == "original"
            and cast(Any, value).language_hint == "en"
            for value in rig.tts.stage.calls
        )
        assert rig.output.stage.calls[0] != rig.output.stage.calls[1]
        for outgoing, played in zip(rig.tts.stage.calls, rig.output.stage.calls):
            assert (
                cast(Any, played).audio.operation_id == cast(Any, outgoing).operation_id
            )
            assert (
                cast(Any, played).session_id == "session"
                and cast(Any, played).playback_id == cast(Any, outgoing).operation_id
            )
        assert (
            rig.coordinator.active_startup_id is None and asyncio.all_tasks() == before
        )
        assert_events(rig, "ready")
        await rig.conversation.sleep()
        rig.probes[0].gate.changes[2] = {"status": s.StartupCheckStatus.UNAVAILABLE}
        second = await rig.coordinator.handle_wake(request())
        assert second is not None and second.outcome.value == "degraded"
        assert all(len(probe.gate.calls) == 2 for probe in rig.probes)
        assert len(rig.policy.calls) == 1
        await rig.coordinator.aclose()
        await rig.coordinator.aclose()
        assert (
            not rig.conversation.closed
            and rig.wake.close_count
            == rig.tts.closed_count
            == rig.output.closed_count
            == 0
        )
        with pytest.raises(s.StartupError, match="^closed$"):
            await rig.coordinator.handle_wake(request())
        return rig

    rig = run(exercise())
    fail(lambda: run(rig.coordinator.handle_wake(request())), "wrong_event_loop")
    fail(lambda: run(rig.coordinator.aclose()), "wrong_event_loop")


@pytest.mark.parametrize("detected", [False, True])
@pytest.mark.parametrize("score", [0.0, 0.2, 1.0])
def test_detected_only_score_ignored(detected: bool, score: float) -> None:
    async def exercise() -> None:
        rig = Rig()
        rig.wake.detect_gate.changes[1] = {"detected": detected, "score": score}
        returned = await rig.coordinator.handle_wake(request())
        if detected:
            assert returned is not None and returned.outcome.value == "ready"
        else:
            assert returned is None
            assert not rig.order and rig.bus.delivered == [] and rig.policy.calls == []
            assert all(
                not gate.calls
                for name, gate in rig.gates().items()
                if name not in ("wake_cap", "wake_detect")
            )
            assert rig.conversation.state is VoiceConversationState.SLEEPING

    run(exercise())


@pytest.mark.parametrize(
    "gate_name,changes",
    [
        ("wake_cap", {"provider_id": "wrong"}),
        (
            "wake_cap",
            {"supported_phrases": (WakePhrase(phrase_id="other", text="other"),)},
        ),
        ("wake_cap", {"approved": True}),
        ("wake_detect", {"operation_id": "wrong"}),
        ("wake_detect", {"provider_id": "wrong"}),
        ("wake_detect", {"phrase_id": "wrong"}),
        ("wake_detect", {"stream_id": "wrong"}),
        ("wake_detect", {"start_sequence": 2}),
        ("wake_detect", {"end_sequence": 2}),
        ("wake_detect", {"detected": "true"}),
        ("wake_detect", {"score": True}),
        ("wake_detect", {"approved": True}),
    ],
)
def test_wake_invalid_never_starts(gate_name: str, changes: dict[str, object]) -> None:
    async def exercise() -> None:
        rig = Rig()
        rig.gates()[gate_name].changes[1] = changes
        with pytest.raises(s.StartupError, match="^wake_failure$"):
            await rig.coordinator.handle_wake(request())
        assert rig.bus.delivered == [] and not rig.order and not rig.tts.cap.calls
        assert rig.coordinator.active_startup_id is None

    run(exercise())


@pytest.mark.parametrize("subsystem", range(6))
@pytest.mark.parametrize(
    "status", [s.StartupCheckStatus.DEGRADED, s.StartupCheckStatus.UNAVAILABLE]
)
def test_every_unhealthy_subsystem_forces_degraded_without_shortcircuit(
    subsystem: int, status: s.StartupCheckStatus
) -> None:
    async def exercise() -> None:
        rig = Rig()
        rig.probes[subsystem].gate.changes[1] = {"status": status}
        returned = await rig.coordinator.handle_wake(request())
        assert returned is not None and returned.outcome.value == "degraded"
        assert returned.checks[subsystem].status is status
        assert rig.order == [member.value for member in s.StartupSubsystem]
        assert len(rig.policy.calls) == 1
        policy = rig.policy.calls[0]
        assert (
            policy.failsafe_id == "startup"
            and policy.trigger is FailSafeTrigger.CRITICAL_SUBSYSTEM_DEGRADATION
        )
        assert policy.requested_capabilities == (
            FailSafeCapability.BASIC_CONVERSATION,
            FailSafeCapability.DIAGNOSTICS,
            FailSafeCapability.RECOVERY_GUIDANCE,
        )
        assert policy.recovery_result is None and PRIVATE not in policy.reason
        assert (
            returned.failsafe_decision is not None
            and returned.failsafe_decision.failsafe_agent_id == "failsafe"
        )
        assert_events(rig, "degraded")

    run(exercise())


@pytest.mark.parametrize("subsystem", range(6))
def test_probe_exception_becomes_unavailable_safely(subsystem: int) -> None:
    async def exercise() -> None:
        rig = Rig()
        rig.probes[subsystem].gate.failures[1] = RuntimeError(PRIVATE)
        returned = await rig.coordinator.handle_wake(request())
        assert (
            returned is not None
            and returned.checks[subsystem].status is s.StartupCheckStatus.UNAVAILABLE
        )
        assert PRIVATE not in returned.model_dump_json() and PRIVATE not in repr(
            returned
        )
        assert all(len(probe.gate.calls) == 1 for probe in rig.probes)

    run(exercise())


@pytest.mark.parametrize("subsystem", range(6))
@pytest.mark.parametrize(
    "changes",
    [
        {"startup_id": "wrong"},
        {"subsystem": "wrong"},
        {"status": "unknown"},
        {"detail": PRIVATE},
    ],
)
def test_probe_malformed_is_error_not_unavailable(
    subsystem: int, changes: dict[str, object]
) -> None:
    async def exercise() -> None:
        rig = Rig()
        rig.probes[subsystem].gate.changes[1] = changes
        with pytest.raises(s.StartupError, match="^health_failure$"):
            await rig.coordinator.handle_wake(request())
        assert all(not probe.gate.calls for probe in rig.probes[subsystem + 1 :])
        assert not rig.policy.calls and not rig.conversation.activate_gate.calls
        assert len(rig.bus.delivered) == 1 and rig.coordinator.active_startup_id is None

    run(exercise())


@pytest.mark.parametrize("phase", [1, 2])
@pytest.mark.parametrize("gate_name", ["tts_cap", "synthesis", "playback"])
@pytest.mark.parametrize("kind", ["raise", "wrong-id", "forged", "wrong-format"])
def test_voice_failure_degrades_no_retry(phase: int, gate_name: str, kind: str) -> None:
    async def exercise() -> None:
        rig = Rig()
        gate = rig.gates()[gate_name]
        if kind == "raise":
            gate.failures[phase] = RuntimeError(PRIVATE)
        else:
            field = "output_id" if gate_name == "playback" else "provider_id"
            gate.changes[phase] = (
                {field: "wrong"}
                if kind == "wrong-id"
                else {"approved": True}
                if kind == "forged"
                else (
                    {"status": "interrupted"}
                    if gate_name == "playback"
                    else {"channels": 2}
                )
            )
        returned = await rig.coordinator.handle_wake(request())
        assert returned is not None and returned.outcome.value == "degraded"
        assert (
            returned.voice_failure_stage is not None
            and returned.voice_failure_stage.value
            == ("playback" if gate_name == "playback" else "tts")
        )
        assert returned.response_text == s.STARTUP_DEGRADED_RESPONSE
        assert len(gate.calls) == phase and all(
            len(probe.gate.calls) == 1 for probe in rig.probes
        )
        assert (
            len(rig.policy.calls) == 1
            and len(rig.conversation.activate_gate.calls) == 1
        )
        if phase == 1:
            assert all(
                cast(Any, value).text == s.STARTUP_ACKNOWLEDGEMENT
                for value in rig.tts.stage.calls
            )
        else:
            assert all(
                cast(Any, value).text != s.STARTUP_DEGRADED_RESPONSE
                for value in rig.tts.stage.calls
            )
        assert_events(rig, "degraded")

    run(exercise())


@pytest.mark.parametrize(
    "change",
    [
        {"failsafe_id": "wrong"},
        {"failsafe_agent_id": "phoenix"},
        {"mode": "ready"},
        {"trigger": FailSafeTrigger.RESOURCE_PRESSURE},
        {"enabled_capabilities": (FailSafeCapability.SAFE_LIMITED_TOOL_ROUTING,)},
        {"reason": PRIVATE},
        {"recovery_id": "wrong"},
        {"approved": True},
    ],
)
def test_failsafe_exact_policy_correlation(change: dict[str, object]) -> None:
    async def exercise() -> None:
        rig = Rig()
        rig.probes[0].gate.changes[1] = {"status": s.StartupCheckStatus.DEGRADED}
        rig.policy.change = change
        with pytest.raises(s.StartupError, match="^failsafe_failure$"):
            await rig.coordinator.handle_wake(request())
        assert not rig.conversation.activate_gate.calls and len(rig.bus.delivered) == 1

    run(exercise())


@pytest.mark.parametrize("failure_phase", [1, 2, 3])
def test_event_failures_no_false_delivery_and_rollback(failure_phase: int) -> None:
    async def exercise() -> None:
        rig = Rig()
        rig.bus.gate.failures[failure_phase] = RuntimeError(PRIVATE)
        code = "conversation_failure" if failure_phase == 2 else "event_failure"
        with pytest.raises(s.StartupError, match="^" + code + "$"):
            await rig.coordinator.handle_wake(request())
        assert len(rig.bus.delivered) == failure_phase - 1
        assert rig.conversation.state is VoiceConversationState.SLEEPING
        assert rig.coordinator.active_startup_id is None
        if failure_phase == 1:
            assert (
                rig.order == []
                and rig.tts.cap.calls == []
                and rig.conversation.activate_gate.calls == []
            )
        if failure_phase == 3:
            assert rig.conversation.sleep_calls == 1

    run(exercise())


def test_final_event_rollback_failure_preserves_event_error_and_observable_state() -> (
    None
):
    async def exercise() -> None:
        rig = Rig()
        rig.bus.gate.failures[3] = RuntimeError(PRIVATE)
        rig.conversation.sleep_failure = RuntimeError(PRIVATE)
        with pytest.raises(s.StartupError, match="^event_failure$"):
            await rig.coordinator.handle_wake(request())
        assert rig.conversation.state is VoiceConversationState.LISTENING
        assert rig.coordinator.active_startup_id is None and len(rig.bus.delivered) == 2

    run(exercise())


CANCEL_POINTS = [
    ("wake_cap", 1),
    ("wake_detect", 1),
    ("tts_cap", 1),
    ("synthesis", 1),
    ("playback", 1),
    *(("probe_" + str(index), 1) for index in range(6)),
    ("activate", 1),
    ("tts_cap", 2),
    ("synthesis", 2),
    ("playback", 2),
    ("event", 1),
    ("event", 2),
    ("event", 3),
]


@pytest.mark.parametrize("gate_name,call_index", CANCEL_POINTS)
@pytest.mark.parametrize("suppress", [False, True])
def test_cancel_every_boundary_and_suppression_no_success(
    gate_name: str, call_index: int, suppress: bool
) -> None:
    async def exercise() -> None:
        rig = Rig()
        gate = rig.gates()[gate_name]
        gate.block_at = call_index
        gate.suppress = suppress
        task = asyncio.create_task(rig.coordinator.handle_wake(request()))
        await asyncio.wait_for(gate.entered.wait(), 1)
        counts = {name: len(value.calls) for name, value in rig.gates().items()}
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert rig.coordinator.active_startup_id is None
        if gate_name == "activate" and suppress:
            counts["event"] += 1  # Internal activation call; no later startup stage.
        assert counts == {name: len(value.calls) for name, value in rig.gates().items()}
        assert not any(
            event.event_type in (EventType.PHOENIX_READY, EventType.PHOENIX_DEGRADED)
            for event in rig.bus.delivered
        )
        assert rig.wake.cancelled == [] and rig.tts.cancelled == []
        assert not rig.conversation.closed

    run(exercise())


@pytest.mark.parametrize("gate_name,call_index", CANCEL_POINTS)
def test_close_late_continuation_cannot_finalize(
    gate_name: str, call_index: int
) -> None:
    async def exercise() -> None:
        rig = Rig()
        gate = rig.gates()[gate_name]
        gate.block_at = call_index
        task = asyncio.create_task(rig.coordinator.handle_wake(request()))
        await asyncio.wait_for(gate.entered.wait(), 1)
        assert rig.coordinator.active_startup_id == "startup"
        await rig.coordinator.aclose()
        await rig.coordinator.aclose()
        gate.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rig.coordinator.closed and rig.coordinator.active_startup_id is None
        assert (
            rig.wake.close_count == rig.tts.closed_count == rig.output.closed_count == 0
        )
        assert not rig.conversation.closed
        with pytest.raises(s.StartupError, match="^closed$"):
            await rig.coordinator.handle_wake(request())

    run(exercise())


def test_busy_sleeping_requirement_and_activation_failure() -> None:
    async def exercise() -> None:
        rig = Rig()
        rig.wake.cap.block_at = 1
        task = asyncio.create_task(rig.coordinator.handle_wake(request()))
        await asyncio.wait_for(rig.wake.cap.entered.wait(), 1)
        with pytest.raises(s.StartupError, match="^busy$"):
            await rig.coordinator.handle_wake(request())
        rig.wake.cap.release.set()
        await task
        with pytest.raises(s.StartupError, match="^invalid_input$"):
            await rig.coordinator.handle_wake(request())
        await rig.conversation.sleep()
        rig.conversation.activate_gate.failures[2] = RuntimeError(PRIVATE)
        with pytest.raises(s.StartupError, match="^conversation_failure$"):
            await rig.coordinator.handle_wake(request())
        assert rig.conversation.state is VoiceConversationState.SLEEPING

    run(exercise())


@pytest.mark.parametrize("outcome", list(s.StartupOutcome))
@pytest.mark.parametrize("healthy", [False, True])
@pytest.mark.parametrize("voice_failure", [None, *list(s.StartupVoiceFailureStage)])
@pytest.mark.parametrize("has_decision", [False, True])
@pytest.mark.parametrize("state", list(VoiceConversationState))
def test_exhaustive_result_consistency(
    outcome: s.StartupOutcome,
    healthy: bool,
    voice_failure: s.StartupVoiceFailureStage | None,
    has_decision: bool,
    state: VoiceConversationState,
) -> None:
    checks = tuple(
        check(subsystem=member, status="healthy" if healthy or index else "unavailable")
        for index, member in enumerate(s.StartupSubsystem)
    )

    def call() -> s.RisePhoenixStartupResult:
        return result(
            outcome=outcome,
            checks=checks,
            voice_failure_stage=voice_failure,
            failsafe_decision=decision() if has_decision else None,
            final_voice_state=state,
            response_text=s.STARTUP_HEALTHY_RESPONSE
            if outcome is s.StartupOutcome.READY
            else s.STARTUP_DEGRADED_RESPONSE,
        )

    valid = state is VoiceConversationState.LISTENING and (
        (
            outcome is s.StartupOutcome.READY
            and healthy
            and voice_failure is None
            and not has_decision
        )
        or (
            outcome is s.StartupOutcome.DEGRADED
            and (not healthy or voice_failure is not None)
            and has_decision
        )
    )
    if valid:
        assert call().outcome is outcome
    else:
        fail(call)


@pytest.mark.parametrize(
    "changes",
    [
        {"checks": ()},
        {
            "checks": tuple(
                check(subsystem=member) for member in reversed(s.StartupSubsystem)
            )
        },
        {"checks": tuple(check() for _ in range(6))},
        {
            "checks": tuple(
                check(startup_id="wrong", subsystem=member)
                for member in s.StartupSubsystem
            )
        },
        {"response_text": s.STARTUP_ACKNOWLEDGEMENT},
        {"response_text": "READY"},
        {"response_text": True},
    ],
)
def test_result_canonical_order_ids_exact_text(changes: dict[str, object]) -> None:
    fail(lambda: result(**changes))


@pytest.mark.parametrize("factory", FACTORIES)
@pytest.mark.parametrize(
    "authority", [PermissionGrant, UserPermissionDecision, ToolDefinition]
)
def test_startup_data_not_permission_tool_or_task_success(
    factory: Callable[..., BaseModel], authority: type[BaseModel]
) -> None:
    value = factory()
    for candidate in (value, value.model_dump()):
        with pytest.raises(ValidationError):
            authority.model_validate(candidate)


def test_static_security_schema_and_bounded_scope() -> None:
    source = Path(s.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    allowed = {
        "__future__",
        "asyncio",
        "abc",
        "collections.abc",
        "enum",
        "hashlib",
        "typing",
        "pydantic",
        "phoenix.agents.failsafe",
        "phoenix.core.event_bus",
        "phoenix.core.events",
        "phoenix.voice.audio",
        "phoenix.voice.conversation",
        "phoenix.voice.tts",
        "phoenix.voice.wake",
    }
    banned = {
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
        "generate",
        "subscribe",
        "cancel",
        "aclose",
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
            assert name not in banned
    assert ScriptDirectory.from_config(
        Config(str(ROOT / "alembic.ini"))
    ).get_heads() == ["0006_missions"]


def test_uncached_guarded_import_constructor_no_io_or_collaborator_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    code = compile(Path(s.__file__).read_text(encoding="utf-8"), "startup", "exec")
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
        namespace: dict[str, Any] = {"__name__": "isolated_startup"}
        exec(code, namespace)
        probes = tuple(
            type("Probe", (namespace["StartupHealthProbe"],), {"check": Probe.check})(
                member
            )
            for member in namespace["StartupSubsystem"]
        )
        coordinator = namespace["RisePhoenixStartupCoordinator"](
            rig.wake, probes, rig.tts, rig.output, rig.conversation, rig.bus, rig.policy
        )
        assert coordinator.owner_id == "phoenix" and coordinator._loop is None
        assert all(not gate.calls for gate in rig.gates().values())


def test_fresh_import_has_no_unauthorized_runtime() -> None:
    script = """
import importlib.abc
import sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.startswith(('phoenix.tools','phoenix.security','phoenix.memory',
                                'phoenix.rag','phoenix.persistence','phoenix.api','phoenix.web')):
            raise AssertionError('Unexpected integration import')
sys.meta_path.insert(0,Guard())
from phoenix.voice.startup import StartupSubsystem, StartupCheckResult
assert len(StartupSubsystem)==6
value=StartupCheckResult(startup_id='id',subsystem='voice',status='unavailable')
assert value.status.value=='unavailable'
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr


def test_repository_credential_signature_scan() -> None:
    paths = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True
    ).splitlines()
    paths.extend(
        ["phoenix/voice/startup/__init__.py", "tests/test_rise_phoenix_startup.py"]
    )
    signatures = [
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE" + r" KEY-----",
        r"github_" + r"pat_[A-Za-z0-9_]{20,}",
        r"gh" + r"p_[A-Za-z0-9]{30,}",
        r"xox" + r"[baprs]-[A-Za-z0-9-]{20,}",
        r"AK" + r"IA[0-9A-Z]{16}",
    ]
    for path in set(paths):
        content = (ROOT / path).read_bytes().decode("utf-8", errors="replace")
        assert not any(re.search(pattern, content) for pattern in signatures), path


@pytest.mark.parametrize("gate_name", ["wake_cap", "wake_detect"])
def test_raw_wake_error_is_private_and_has_no_startup(gate_name: str) -> None:
    async def exercise() -> None:
        rig = Rig()
        rig.gates()[gate_name].failures[1] = RuntimeError(PRIVATE)
        with pytest.raises(s.StartupError) as caught:
            await rig.coordinator.handle_wake(request())
        assert str(caught.value) == "wake_failure" and PRIVATE not in "".join(
            traceback.format_exception(caught.value)
        )
        assert not rig.bus.delivered and not rig.order

    run(exercise())


def test_policy_exception_and_multiple_unhealthy_degraded_final_failure() -> None:
    async def exercise() -> None:
        rig = Rig()
        for index in (0, 2, 5):
            rig.probes[index].gate.changes[1] = {
                "status": s.StartupCheckStatus.UNAVAILABLE
            }
        rig.output.stage.failures[2] = RuntimeError(PRIVATE)
        returned = await rig.coordinator.handle_wake(request())
        assert returned is not None and returned.outcome is s.StartupOutcome.DEGRADED
        assert returned.voice_failure_stage is s.StartupVoiceFailureStage.PLAYBACK
        assert len(rig.policy.calls) == 1 and len(rig.output.stage.calls) == 2
        assert cast(Any, rig.tts.stage.calls[1]).text == s.STARTUP_DEGRADED_RESPONSE
        assert cast(object, rig.bus.delivered[-1].payload["unhealthy_subsystems"]) == (
            "persistence",
            "model_provider",
            "agents",
        )
        await rig.conversation.sleep()
        rig.probes[0].gate.changes[2] = {"status": s.StartupCheckStatus.UNAVAILABLE}
        rig.policy.failure = RuntimeError(PRIVATE)
        with pytest.raises(s.StartupError, match="^failsafe_failure$"):
            await rig.coordinator.handle_wake(request())
        assert len(rig.conversation.activate_gate.calls) == 1

    run(exercise())


@pytest.mark.parametrize(
    "field,changes",
    [
        ("wake_request", {"approved": True}),
        ("wake_request", {"frames": ()}),
        ("checks", {"status": True}),
        ("checks", {"approved": True}),
        ("failsafe_decision", {"approved": True}),
        ("failsafe_decision", {"failsafe_id": "wrong"}),
    ],
)
def test_nested_stored_evidence_forgery(field: str, changes: dict[str, object]) -> None:
    if field == "wake_request":
        fail(
            lambda: request(wake_request=wake_request().model_copy(update=changes)),
            "invalid_input",
        )
    elif field == "checks":
        checks = list(result().checks)
        checks[0] = checks[0].model_copy(update=changes)
        fail(lambda: result(checks=checks))
    else:
        fail(
            lambda: result(
                outcome="degraded",
                voice_failure_stage="tts",
                response_text=s.STARTUP_DEGRADED_RESPONSE,
                failsafe_decision=decision(**changes),
            )
        )


@pytest.mark.parametrize("field", ["trigger", "enabled_capabilities"])
def test_failsafe_foreign_copied_enum_rejected(field: str) -> None:
    enum = FailSafeTrigger if field == "trigger" else FailSafeCapability
    member = (
        FailSafeTrigger.CRITICAL_SUBSYSTEM_DEGRADATION
        if field == "trigger"
        else FailSafeCapability.BASIC_CONVERSATION
    )
    copied = str.__new__(enum, member.value)
    copied._name_ = member.name
    copied._value_ = member.value
    for forged in (copied, next(iter(StrEnum("Foreign", {"VALUE": member.value})))):
        change = {
            field: forged
            if field == "trigger"
            else (
                forged,
                FailSafeCapability.DIAGNOSTICS,
                FailSafeCapability.RECOVERY_GUIDANCE,
            )
        }
        fail(
            lambda: result(
                outcome="degraded",
                voice_failure_stage="tts",
                response_text=s.STARTUP_DEGRADED_RESPONSE,
                failsafe_decision=decision(**change),
            )
        )


def test_real_eventbus_capacity_rejection_rolls_back() -> None:
    async def exercise() -> None:
        rig = Rig()
        # One subscriber has room for started + listening, then rejects final.
        bus = Bus(capacity=2)
        sub = await bus.subscribe()
        conversation = Conversation(rig.tts, rig.output, bus)
        coordinator = s.RisePhoenixStartupCoordinator(
            rig.wake, rig.probes, rig.tts, rig.output, conversation, bus, rig.policy
        )
        with pytest.raises(s.StartupError, match="^event_failure$"):
            await coordinator.handle_wake(request())
        assert (
            len(bus.delivered) == 2
            and conversation.state is VoiceConversationState.SLEEPING
        )
        assert (await sub.get()).event_type is EventType.PHOENIX_STARTED
        assert (await sub.get()).event_type is EventType.VOICE_LISTENING
        assert coordinator.active_startup_id is None
        await sub.close()

    run(exercise())


def test_probe_identity_mutation_detected_before_check_and_readonly_property() -> None:
    async def exercise() -> None:
        rig = Rig()
        with pytest.raises(AttributeError):
            setattr(rig.probes[0], "subsystem", s.StartupSubsystem.AGENTS)
        rig.probes[0]._subsystem = s.StartupSubsystem.AGENTS
        with pytest.raises(s.StartupError, match="^health_failure$"):
            await rig.coordinator.handle_wake(request())
        assert not rig.order

    run(exercise())


def test_maximum_ids_and_detached_probe_container() -> None:
    async def exercise() -> None:
        rig = Rig()
        probes = list(rig.probes)
        coordinator = s.RisePhoenixStartupCoordinator(
            rig.wake, probes, rig.tts, rig.output, rig.conversation, rig.bus, rig.policy
        )
        probes.clear()
        returned = await coordinator.handle_wake(
            request(startup_id="s" * 256, session_id="v" * 256)
        )
        assert returned is not None and returned.outcome is s.StartupOutcome.READY
        assert len(returned.startup_id) == len(returned.session_id) == 256
        for played in rig.output.stage.calls:
            assert len(cast(Any, played).playback_id) <= 256
        assert all(len(probe.gate.calls) == 1 for probe in rig.probes)

    run(exercise())


@pytest.mark.parametrize("bad", [None, True, 1, "", "unknown"])
def test_probe_identity_and_constructor_invalid_collaborator(bad: object) -> None:
    fail(lambda: Probe(cast(Any, bad), []), "invalid_input")
    fail(lambda: Rig(wake_provider=bad), "invalid_input")


@pytest.mark.parametrize("fail_on_read", [1, 2, 3])
def test_conversation_observability_failure_cannot_leak_diagnostics(
    monkeypatch: pytest.MonkeyPatch, fail_on_read: int
) -> None:
    async def exercise() -> None:
        rig = Rig()
        reads = 0

        def state(value: Conversation) -> VoiceConversationState:
            nonlocal reads
            reads += 1
            if reads >= fail_on_read:
                raise RuntimeError(PRIVATE)
            return value._state

        monkeypatch.setattr(Conversation, "state", property(state))
        with pytest.raises(s.StartupError) as caught:
            await rig.coordinator.handle_wake(request())
        assert str(caught.value) == "conversation_failure"
        assert PRIVATE not in "".join(traceback.format_exception(caught.value))
        assert rig.coordinator.active_startup_id is None

    run(exercise())


def test_probe_observability_failure_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        rig = Rig()

        def bad(value: Probe) -> s.StartupSubsystem:
            raise RuntimeError(PRIVATE)

        monkeypatch.setattr(Probe, "subsystem", property(bad))
        with pytest.raises(s.StartupError) as caught:
            await rig.coordinator.handle_wake(request())
        assert str(caught.value) == "health_failure"
        assert PRIVATE not in "".join(traceback.format_exception(caught.value))
        assert rig.order == []

    run(exercise())
