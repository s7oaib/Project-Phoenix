"""The live voice loop: wake, listen, transcribe, ask PHOENIX, speak (PHX-114).

``VoicePipeline.run`` drives the PHX-064 ``VoiceConversationManager`` and, for
the first wake of a run, the PHX-065 ``RisePhoenixStartupCoordinator``, from a
microphone source:

1. SLEEPING: an energy endpointer cuts utterances; each one's first 3 s go to
   the wake provider. "Rise, Phoenix" starts a conversation. The first wake of
   a run plays the startup ritual (acknowledgement, real health checks,
   ready/degraded line); later wakes play a short earcon. A wake utterance long
   enough to hold a request too ("Rise, Phoenix, what time is it?") becomes the
   first turn directly.
2. LISTENING: the next utterance (end of speech = a run of silent frames)
   becomes one turn: STT, then the injected responder (the backend submits it
   as a normal chat mission), then TTS and playback. While PHOENIX speaks, the
   microphone keeps feeding a stricter barge-in detector; sustained speech
   interrupts playback and the speech becomes the next turn.
3. Follow-ups need no wake phrase until ``follow_up_timeout_seconds`` pass in
   silence; then the pipeline sleeps again.

Failures stay inside voice (INV-015, INV-016): a failed transcription is
retried silently a few times before the turn falls back to text; a failed
synthesis or playback leaves the conversation in ``degraded_voice`` (answers
stay readable in the chat); a microphone failure ends the run with a closed
code. The pipeline reports states through ``on_state`` (no text, no audio).
"""

from __future__ import annotations

import asyncio
import base64
import math
import re
import struct
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from typing import Final
from uuid import uuid4

from phoenix.agents.failsafe import KaidoFailSafe
from phoenix.core.event_bus import EventBus
from phoenix.voice.audio import PcmAudioFrame
from phoenix.voice.conversation import (
    AudioOutput,
    AudioPlaybackRequest,
    VoiceConversationConfig,
    VoiceConversationError,
    VoiceConversationManager,
    VoiceConversationState,
    VoiceFailureStage,
    VoiceFrameObservation,
    VoiceResponder,
    VoiceSegment,
    VoiceTurnOutcome,
    VoiceTurnRequest,
    VoiceTurnResult,
)
from phoenix.voice.engines.sound import MicrophoneSource
from phoenix.voice.startup import (
    RisePhoenixStartupCoordinator,
    RisePhoenixStartupRequest,
    StartupError,
    StartupErrorCode,
    StartupHealthProbe,
)
from phoenix.voice.stt import SttProvider
from phoenix.voice.tts import TtsAudio, TtsAudioEncoding, TtsProvider
from phoenix.voice.vad import (
    EnergyVadProvider,
    VadActivity,
    VadFrameDuration,
    VadRequest,
)
from phoenix.voice.wake import (
    DEFAULT_WAKE_PHRASE,
    WakeWordDetection,
    WakeWordProvider,
    WakeWordRequest,
)

__all__ = (
    "VoicePipelineState",
    "VoiceFailureCode",
    "VoiceStateChange",
    "VoiceComponents",
    "VoicePipelineConfig",
    "VoicePipeline",
    "speakable_text",
    "strip_wake_phrase",
    "segment_from_pcm",
    "pcm_dbfs",
)

_BYTES_PER_SECOND: Final = 32000
_WAKE_WINDOW_BYTES: Final = 96000  # PHX-060 window bound (3 s)
_STT_MAX_BYTES: Final = 960000  # PHX-062 window bound (30 s)
_CHUNK_BYTES: Final = 32000  # 1 s PcmAudioFrame, the PHX-060 frame bound
_MAX_WAKE_FAILURES: Final = 5


class VoicePipelineState(StrEnum):
    OFF = "off"
    STARTING = "starting"
    SLEEPING = "sleeping"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    DEGRADED_VOICE = "degraded_voice"
    TEXT_ONLY = "text_only"
    UNAVAILABLE = "unavailable"


class VoiceFailureCode(StrEnum):
    MODELS_MISSING = "models_missing"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    MICROPHONE_UNAVAILABLE = "microphone_unavailable"
    VOICE_FAILURE = "voice_failure"


@dataclass(frozen=True)
class VoiceStateChange:
    """IDs and closed values only; never transcript, answer or audio."""

    state: VoicePipelineState
    session_id: str | None = None
    turn_id: str | None = None
    mission_id: str | None = None
    code: VoiceFailureCode | None = None
    interrupted: bool = False


@dataclass
class VoiceComponents:
    wake: WakeWordProvider
    stt: SttProvider
    tts: TtsProvider
    microphone: MicrophoneSource
    output: AudioOutput
    voice_profile_id: str

    async def aclose(self) -> None:
        for close in (
            self.microphone.aclose,
            self.output.aclose,
            self.wake.aclose,
            self.stt.aclose,
            self.tts.aclose,
        ):
            try:
                await close()
            except Exception:
                pass


@dataclass(frozen=True)
class VoicePipelineConfig:
    vad_threshold_dbfs: float = -45.0
    # While PHOENIX speaks, its own voice reaches the microphone; barge-in needs
    # louder, longer speech than the endpointer (no echo cancellation, ADR-108).
    barge_in_threshold_dbfs: float = -28.0
    barge_in_frames: int = 8
    speech_start_frames: int = 3
    wake_end_silence_frames: int = 15  # 450 ms
    command_end_silence_frames: int = 27  # 810 ms
    min_speech_frames: int = 8  # 240 ms
    pre_roll_frames: int = 10  # 300 ms
    max_wake_utterance_frames: int = 200  # 6 s
    max_command_frames: int = 930  # 27.9 s, inside the 30 s STT window
    # A wake utterance at least this long also holds a request.
    wake_with_request_seconds: float = 2.2
    follow_up_timeout_seconds: float = 30.0
    max_consecutive_stt_failures: int = 3


def strip_wake_phrase(text: str) -> str:
    """Drop a leading "Rise, Phoenix" (as heard) from a transcript."""
    return re.sub(
        r"^\W*(?:(?:okay|ok|hey)\W+)?[\w']*\W+(?:(?:the|of|a)\W+)?"
        r"(?:phoenix|phenix|feenix)\b\W*",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )


_MAX_SPOKEN_CHARS: Final = 600


def speakable_text(answer: str) -> str:
    """A short spoken rendering of an answer; the full text stays in the chat."""
    text = re.sub(r"```.*?(```|$)", " (code is shown on screen) ", answer, flags=re.S)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[`*_#>|~]+", " ", text)
    text = re.sub(r"^\s*[-+]\s+", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "I have an answer for you on screen."
    if len(text) <= _MAX_SPOKEN_CHARS:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    spoken = ""
    for sentence in sentences:
        if len(spoken) + len(sentence) + 1 > _MAX_SPOKEN_CHARS:
            break
        spoken = f"{spoken} {sentence}".strip()
    if not spoken:
        spoken = text[:_MAX_SPOKEN_CHARS].rsplit(" ", 1)[0]
    return f"{spoken} The full answer is on screen."


def _frames(pcm: bytes, stream_id: str) -> tuple[PcmAudioFrame, ...]:
    from phoenix.voice.engines.sound import pcm_frame

    return tuple(
        pcm_frame(stream_id, index, pcm[start : start + _CHUNK_BYTES])
        for index, start in enumerate(range(0, len(pcm), _CHUNK_BYTES))
    )


def segment_from_pcm(pcm: bytes) -> VoiceSegment:
    """A fresh stream of 1 s frames (up to 30 s), as the STT window expects."""
    pcm = pcm[: _STT_MAX_BYTES - _STT_MAX_BYTES % 2]
    stream_id = str(uuid4())
    frames = _frames(pcm, stream_id)
    return VoiceSegment(
        stream_id=stream_id,
        start_sequence=0,
        end_sequence=len(frames) - 1,
        frames=frames,
    )


def _wake_request(pcm: bytes) -> WakeWordRequest:
    return WakeWordRequest(
        phrase=DEFAULT_WAKE_PHRASE,
        frames=_frames(pcm[:_WAKE_WINDOW_BYTES], str(uuid4())),
    )


def _earcon() -> TtsAudio:
    """A 160 ms rising two-tone chime at 16 kHz (no synthesis latency)."""
    rate = 16000
    samples = []
    for index in range(int(rate * 0.16)):
        t = index / rate
        frequency = 660.0 if t < 0.08 else 880.0
        envelope = min(1.0, index / 160, (int(rate * 0.16) - index) / 400)
        samples.append(int(9000 * envelope * math.sin(2 * math.pi * frequency * t)))
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    return TtsAudio(
        operation_id=str(uuid4()),
        provider_id="earcon",
        voice_profile_id="earcon",
        sample_rate_hz=rate,
        channels=1,
        sample_width_bytes=2,
        encoding=TtsAudioEncoding.PCM_S16LE,
        pcm_base64=base64.b64encode(pcm).decode("ascii"),
        pcm_sha256=sha256(pcm).hexdigest(),
    )


StateSink = Callable[[VoiceStateChange], Awaitable[None]]


@dataclass
class _Utterance:
    pcm: bytes
    frames: int


@dataclass
class _TurnContext:
    recent: deque[PcmAudioFrame] = field(default_factory=lambda: deque(maxlen=12))
    interrupted: bool = False


class VoicePipeline:
    """One run of the live loop; ``run`` returns only by cancellation or error."""

    def __init__(
        self,
        components: VoiceComponents,
        responder: VoiceResponder,
        *,
        on_state: StateSink,
        startup_probes: Sequence[StartupHealthProbe] | None = None,
        config: VoicePipelineConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
        session_id: str | None = None,
    ) -> None:
        self._c = components
        self._responder = responder
        self._on_state = on_state
        self._probes = tuple(startup_probes) if startup_probes is not None else None
        self._config = config or VoicePipelineConfig()
        self._clock = clock
        self.session_id = session_id or str(uuid4())
        self._bus = EventBus(capacity=64)  # manager/coordinator events stay private
        self._manager = VoiceConversationManager(
            components.stt,
            components.tts,
            responder,
            components.output,
            self._bus,
            clock=self._now,
            config=VoiceConversationConfig(
                # The pipeline owns the follow-up window; the manager's own window
                # is longer so a request started just before it lapses survives.
                follow_up_timeout_seconds=float(
                    min(600.0, self._config.follow_up_timeout_seconds + 60.0)
                ),
                barge_in_speech_frames=self._config.barge_in_frames,
            ),
        )
        self._coordinator = (
            RisePhoenixStartupCoordinator(
                components.wake,
                self._probes,
                components.tts,
                components.output,
                self._manager,
                self._bus,
                KaidoFailSafe(),
            )
            if self._probes is not None
            else None
        )
        self._vad = EnergyVadProvider(
            "energy_vad_v1", threshold_dbfs=self._config.vad_threshold_dbfs
        )
        self._barge_vad = EnergyVadProvider(
            "energy_vad_barge_in", threshold_dbfs=self._config.barge_in_threshold_dbfs
        )
        self._ritual_done = self._coordinator is None
        self._deadline: float | None = None
        self._stt_failures = 0
        self._wake_failures = 0
        self._state = VoicePipelineState.STARTING
        self._seed: list[PcmAudioFrame] = []
        self.wake_count = 0
        self.turn_count = 0

    @property
    def state(self) -> VoicePipelineState:
        return self._state

    @property
    def manager(self) -> VoiceConversationManager:
        return self._manager

    def _now(self) -> float:
        return float(self._clock())

    async def _publish(
        self,
        state: VoicePipelineState,
        *,
        turn_id: str | None = None,
        mission_id: str | None = None,
        interrupted: bool = False,
    ) -> None:
        self._state = state
        try:
            await self._on_state(
                VoiceStateChange(
                    state=state,
                    session_id=self.session_id,
                    turn_id=turn_id,
                    mission_id=mission_id,
                    interrupted=interrupted,
                )
            )
        except Exception:
            pass  # reporting never stops voice (INV-022 counts it upstream)

    async def note_mission(self, turn_id: str | None, mission_id: str) -> None:
        """The responder submitted the turn as this mission (THINKING)."""
        await self._publish(
            VoicePipelineState.THINKING, turn_id=turn_id, mission_id=mission_id
        )

    # ---- capture

    async def _classify(self, vad: EnergyVadProvider, frame: PcmAudioFrame) -> bool:
        decision = await vad.classify(
            VadRequest(frame=frame, duration=VadFrameDuration.MS_30)
        )
        return decision.activity is VadActivity.SPEECH

    async def _capture(self, *, wake: bool) -> _Utterance | None:
        """The next utterance; None when the follow-up window lapses."""
        cfg = self._config
        end_frames = (
            cfg.wake_end_silence_frames if wake else cfg.command_end_silence_frames
        )
        limit = cfg.max_wake_utterance_frames if wake else cfg.max_command_frames
        pre: deque[PcmAudioFrame] = deque(maxlen=cfg.pre_roll_frames)
        buffer: list[PcmAudioFrame] = []
        in_speech, run, silence, voiced = False, 0, 0, 0
        if self._seed:  # barge-in: the user is already talking
            buffer, in_speech, voiced = list(self._seed), True, len(self._seed)
            self._seed = []
        while True:
            if not wake and not in_speech and self._deadline is not None:
                if self._now() >= self._deadline:
                    return None
            frame = await self._c.microphone.read()
            speaking = await self._classify(self._vad, frame)
            if not in_speech:
                pre.append(frame)
                run = run + 1 if speaking else 0
                if run >= cfg.speech_start_frames:
                    in_speech, buffer, silence, voiced = True, list(pre), 0, run
                continue
            buffer.append(frame)
            if speaking:
                silence, voiced = 0, voiced + 1
            else:
                silence += 1
            if silence >= end_frames or len(buffer) >= limit:
                keep = len(buffer) - max(0, silence - 5)  # ~150 ms of tail
                pcm = b"".join(f.pcm_bytes_for_provider() for f in buffer[:keep])
                if voiced >= cfg.min_speech_frames:
                    return _Utterance(pcm=pcm, frames=keep)
                pre.clear()
                buffer, in_speech, run, silence, voiced = [], False, 0, 0, 0

    # ---- wake

    async def _play_earcon(self) -> None:
        try:
            await self._c.output.play(
                AudioPlaybackRequest(
                    playback_id=str(uuid4()),
                    session_id=self.session_id,
                    turn_id=str(uuid4()),
                    audio=_earcon(),
                )
            )
        except Exception:
            pass  # the chime is a courtesy; listening continues without it

    async def _wake(self, utterance: _Utterance) -> str | None:
        """None, or how the conversation started: "ritual" or "earcon"."""
        request = _wake_request(utterance.pcm)
        if not self._ritual_done and self._coordinator is not None:
            try:
                result = await self._coordinator.handle_wake(
                    RisePhoenixStartupRequest(
                        startup_id=str(uuid4()),
                        session_id=self.session_id,
                        wake_request=request,
                        tts_voice_profile_id=self._c.voice_profile_id,
                    )
                )
            except StartupError as error:
                if error.code is StartupErrorCode.WAKE_FAILURE:
                    return None
                # The phrase was heard but the ritual failed: start plainly.
                self._ritual_done = True
                if self._manager.state is not VoiceConversationState.SLEEPING:
                    listening = self._manager.state is VoiceConversationState.LISTENING
                    return "earcon" if listening else None
            else:
                if result is None:
                    return None
                self._ritual_done = True
                self._c.microphone.drain()
                return "ritual"
        else:
            try:
                detection = await self._c.wake.detect(request)
                detection = WakeWordDetection.model_validate(detection)
                detection.validate_request(request, self._c.wake.provider_id)
            except Exception:
                # One failed check is ignored; a wake engine that keeps failing
                # ends the run, which the service reports as unavailable.
                self._wake_failures += 1
                if self._wake_failures >= _MAX_WAKE_FAILURES:
                    raise
                return None
            self._wake_failures = 0
            if not detection.detected:
                return None
        await self._manager.activate(self.session_id)
        await self._play_earcon()
        self._c.microphone.drain()
        return "earcon"

    # ---- turns

    async def _turn(self, utterance: _Utterance) -> None:
        turn_id = str(uuid4())
        self.turn_count += 1
        request = VoiceTurnRequest(
            session_id=self.session_id,
            turn_id=turn_id,
            segment=segment_from_pcm(utterance.pcm),
            tts_voice_profile_id=self._c.voice_profile_id,
        )
        await self._publish(VoicePipelineState.THINKING, turn_id=turn_id)
        context = _TurnContext()
        task = asyncio.create_task(self._manager.process_turn(request))
        try:
            await self._watch(task, turn_id, context)
            result: VoiceTurnResult | None = await task
        except VoiceConversationError:
            result = None
        finally:
            if not task.done():
                task.cancel()
        await self._settle(result, turn_id, context)

    async def _watch(
        self, task: asyncio.Task[VoiceTurnResult], turn_id: str, context: _TurnContext
    ) -> None:
        """Feed barge-in frames while the turn runs; THINKING frames are dropped."""
        while not task.done():
            reader = asyncio.ensure_future(self._c.microphone.read())
            done, _ = await asyncio.wait(
                {task, reader}, return_when=asyncio.FIRST_COMPLETED
            )
            if reader not in done:
                reader.cancel()
                break
            frame = reader.result()
            if self._manager.state is not VoiceConversationState.SPEAKING:
                continue
            if self._state is not VoicePipelineState.SPEAKING:
                await self._publish(VoicePipelineState.SPEAKING, turn_id=turn_id)
            context.recent.append(frame)
            decision = await self._barge_vad.classify(
                VadRequest(frame=frame, duration=VadFrameDuration.MS_30)
            )
            try:
                if await self._manager.observe_barge_in(
                    VoiceFrameObservation(frame=frame, decision=decision)
                ):
                    context.interrupted = True
            except VoiceConversationError:
                pass  # playback just finished, or interruption was refused

    async def _settle(
        self, result: VoiceTurnResult | None, turn_id: str, context: _TurnContext
    ) -> None:
        manager_state = self._manager.state
        if result is not None and result.outcome is VoiceTurnOutcome.TEXT_FALLBACK:
            if result.failure_stage is VoiceFailureStage.STT:
                self._stt_failures += 1
                if self._stt_failures < self._config.max_consecutive_stt_failures:
                    # Most often noise or an unclear phrase: keep listening.
                    await self._manager.activate(self.session_id)
                    self._listen_again()
                    await self._publish(VoicePipelineState.LISTENING, turn_id=turn_id)
                    return
                self._stt_failures = 0
                self._deadline = None
                await self._publish(VoicePipelineState.TEXT_ONLY, turn_id=turn_id)
                return
            self._stt_failures = 0
            self._listen_again()
            await self._publish(VoicePipelineState.DEGRADED_VOICE, turn_id=turn_id)
            return
        self._stt_failures = 0 if result is not None else self._stt_failures
        if manager_state in (
            VoiceConversationState.LISTENING,
            VoiceConversationState.DEGRADED_VOICE,
        ):
            interrupted = result is not None and (
                result.outcome is VoiceTurnOutcome.INTERRUPTED
            )
            if interrupted:
                self._seed = list(context.recent)[-self._config.barge_in_frames :]
            else:
                self._c.microphone.drain()
            self._listen_again()
            await self._publish(
                VoicePipelineState.LISTENING
                if manager_state is VoiceConversationState.LISTENING
                else VoicePipelineState.DEGRADED_VOICE,
                turn_id=turn_id,
                interrupted=interrupted,
            )
            return
        self._deadline = None
        await self._publish(VoicePipelineState.SLEEPING, turn_id=turn_id)

    def _listen_again(self) -> None:
        self._deadline = self._now() + self._config.follow_up_timeout_seconds

    def _in_conversation(self) -> bool:
        return self._manager.state in (
            VoiceConversationState.LISTENING,
            VoiceConversationState.DEGRADED_VOICE,
        )

    # ---- the loop

    async def run(self) -> None:
        await self._c.microphone.start()
        await self._publish(VoicePipelineState.SLEEPING)
        try:
            while True:
                if not self._in_conversation():
                    utterance = await self._capture(wake=True)
                    assert utterance is not None
                    started = await self._wake(utterance)
                    if started is None:
                        continue
                    self.wake_count += 1
                    self._listen_again()
                    await self._publish(VoicePipelineState.LISTENING)
                    seconds = len(utterance.pcm) / _BYTES_PER_SECOND
                    if seconds >= self._config.wake_with_request_seconds:
                        await self._turn(utterance)
                    continue
                command = await self._capture(wake=False)
                if command is None:
                    await self._manager.sleep()
                    self._deadline = None
                    await self._publish(VoicePipelineState.SLEEPING)
                    continue
                await self._turn(command)
        finally:
            for close in (self._manager.aclose, self._close_coordinator):
                try:
                    await close()
                except (Exception, asyncio.CancelledError):
                    pass

    async def _close_coordinator(self) -> None:
        if self._coordinator is not None:
            await self._coordinator.aclose()


def pcm_dbfs(pcm: bytes) -> float:
    """RMS level of S16LE audio in dBFS (diagnostics for the smoke script)."""
    if len(pcm) < 2:
        return -96.0
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm[: len(pcm) // 2 * 2])
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return -96.0 if rms == 0 else max(-96.0, 20 * math.log10(rms / 32768.0))
