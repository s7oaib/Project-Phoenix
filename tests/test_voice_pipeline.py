"""PHX-114 voice pipeline (ADR-105 to ADR-109).

Fakes only by default: scripted microphone frames, wake/STT/TTS providers and
an audio output stand in for faster-whisper, Piper and sounddevice, which are
replaced in ``sys.modules`` where their adapters are exercised. No microphone,
speaker, model file, network, Ollama or GPU is used. The backend tests run the
real composition root (MissionService, PhoenixRuntime triage, PermissionEngine,
SQLite) with a scripted model, so a spoken request is shown to travel the same
path as ``POST /chat``.

Live checks (real models, still no microphone) run only with
``PHOENIX_VOICE_LIVE=1`` and installed models; ``scripts/voice_smoke.py`` is
the owner-run end-to-end check with a real microphone and speaker.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import copy
import math
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time
import tomllib
import types
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterator
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, metadata, version
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from phoenix.agents.mission_events import (
    VOICE_CODES,
    VOICE_STATES,
    EventPayloadError,
    check_event,
    check_payload,
)
from phoenix.api.missions import MissionService
from phoenix.api.security import LocalApiSecurity
from phoenix.api.server import (
    ProviderSet,
    ServerConfig,
    build_server_app,
    get_services,
)
from phoenix.api.voice import (
    VoiceService,
    _spoken_outcome,
    local_components,
)
from phoenix.core.config import AppSettings
from phoenix.core.events import EventEnvelope, EventType
from phoenix.models.provider import (
    CancellationResult,
    CancellationStatus,
    GenerationChunk,
    GenerationRequest,
    GenerationResponse,
    ModelCapabilities,
    ModelProvider,
    ModelProviderError,
    ProviderHealth,
    ProviderHealthStatus,
    TokenUsage,
)
from phoenix.persistence.missions import MissionRecord, MissionStatus
from phoenix.voice.audio import AudioEncoding, PcmAudioFrame
from phoenix.voice.conversation import (
    AudioOutput,
    AudioOutputError,
    AudioOutputErrorCode,
    AudioPlaybackRequest,
    AudioPlaybackResult,
    AudioPlaybackStatus,
    VoiceResponder,
    VoiceResponseDraft,
    VoiceResponseRequest,
)
from phoenix.voice.engines import models as voice_models
from phoenix.voice.engines import piper as piper_engine
from phoenix.voice.engines import sound
from phoenix.voice.engines import whisper as whisper_engine
from phoenix.voice.engines.pipeline import (
    VoiceComponents,
    VoiceFailureCode,
    VoicePipeline,
    VoicePipelineConfig,
    VoicePipelineState,
    VoiceStateChange,
    segment_from_pcm,
    speakable_text,
    strip_wake_phrase,
)
from phoenix.voice.engines.sound import MicrophoneError, MicrophoneSource, pcm_frame
from phoenix.voice.engines.whisper import (
    FasterWhisperSttProvider,
    FasterWhisperWakeWordProvider,
    VoiceModelMissingError,
    WhisperEngine,
    wake_phrase_score,
)
from phoenix.voice.startup import (
    STARTUP_ACKNOWLEDGEMENT,
    STARTUP_HEALTHY_RESPONSE,
    StartupCheckResult,
    StartupCheckStatus,
    StartupHealthProbe,
    StartupSubsystem,
)
from phoenix.voice.stt import (
    SttCapabilities,
    SttProvider,
    SttProviderError,
    SttProviderErrorCode,
    SttRequest,
    SttTranscript,
)
from phoenix.voice.tts import (
    TtsAudio,
    TtsAudioEncoding,
    TtsCapabilities,
    TtsProvider,
    TtsProviderError,
    TtsProviderErrorCode,
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
    WakeWordProviderError,
    WakeWordProviderErrorCode,
    WakeWordRequest,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = "phoenix_primary"
SENTINEL = "VOICE-SENTINEL-7731"


# ---- audio helpers


def tone(frames: int, amplitude: int = 8000) -> bytes:
    """A 300 Hz tone (about -15 dBFS): speech to the energy VAD."""
    samples = [
        int(amplitude * math.sin(2 * math.pi * 300 * i / 16000))
        for i in range(frames * 480)
    ]
    return struct.pack(f"<{len(samples)}h", *samples)


def silence(frames: int) -> bytes:
    return b"\x00\x00" * 480 * frames


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


Step = bytes | Callable[[], bool]


class FakeMic(MicrophoneSource):
    """Scripted 30 ms frames; a predicate step holds the script (silence flows)
    until it is true. Every frame advances the fake clock by 30 ms."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.script: deque[Step] = deque()
        self.stream_id = "mic-stream"
        self.sequence = 0
        self.started = 0
        self.closed = 0
        self.drained = 0
        self.fail_after: int | None = None

    def feed(self, pcm: bytes) -> FakeMic:
        for start in range(0, len(pcm), 960):
            self.script.append(pcm[start : start + 960])
        return self

    def gate(self, predicate: Callable[[], bool]) -> FakeMic:
        self.script.append(predicate)
        return self

    async def start(self) -> None:
        self.started += 1

    async def read(self) -> PcmAudioFrame:
        await asyncio.sleep(0.0005)
        if self.fail_after is not None and self.sequence >= self.fail_after:
            raise MicrophoneError("gone")
        chunk = silence(1)
        while self.script:
            head = self.script[0]
            if isinstance(head, bytes):
                chunk = head
                self.script.popleft()
                break
            if not head():
                break
            self.script.popleft()
        self.clock.now += 0.03
        frame = pcm_frame(self.stream_id, self.sequence, chunk)
        self.sequence += 1
        return frame

    def drain(self) -> int:
        self.drained += 1
        return 0

    async def aclose(self) -> None:
        self.closed += 1


class FakeWake(WakeWordProvider):
    def __init__(self) -> None:
        super().__init__("fake_wake")
        self.decisions: deque[bool] = deque()
        self.default = True
        self.calls = 0

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return ()

    async def capabilities(self) -> WakeWordCapabilities:
        return WakeWordCapabilities(
            provider_id=self.provider_id,
            supported_phrases=(DEFAULT_WAKE_PHRASE,),
            sample_rate_hz=16000,
            channels=1,
            sample_width_bytes=2,
            encoding=AudioEncoding.PCM_S16LE,
            supports_cancellation=False,
        )

    async def detect(self, request: WakeWordRequest) -> WakeWordDetection:
        self.calls += 1
        detected = self.decisions.popleft() if self.decisions else self.default
        return WakeWordDetection(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            phrase_id=request.phrase.phrase_id,
            stream_id=request.frames[0].stream_id,
            start_sequence=request.frames[0].sequence,
            end_sequence=request.frames[-1].sequence,
            detected=detected,
            score=1.0 if detected else 0.0,
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.UNSUPPORTED,
        )

    async def aclose(self) -> None:
        pass


class FakeStt(SttProvider):
    def __init__(self) -> None:
        super().__init__("fake_stt")
        self.texts: deque[str | Exception] = deque()
        self.calls = 0
        self.seconds: list[float] = []

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return ()

    async def capabilities(self) -> SttCapabilities:
        return SttCapabilities(
            provider_id=self.provider_id,
            sample_rate_hz=16000,
            channels=1,
            sample_width_bytes=2,
            encoding=AudioEncoding.PCM_S16LE,
            supports_language_hint=True,
            supports_language_detection=False,
            supports_cancellation=False,
        )

    async def transcribe(self, request: SttRequest) -> SttTranscript:
        self.calls += 1
        self.seconds.append(request.audio_duration_seconds)
        item = self.texts.popleft() if self.texts else "Hello."
        if isinstance(item, Exception):
            raise item
        return SttTranscript(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            stream_id=request.frames[0].stream_id,
            start_sequence=request.frames[0].sequence,
            end_sequence=request.frames[-1].sequence,
            text=item,
            language="en",
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.UNSUPPORTED,
        )

    async def aclose(self) -> None:
        pass


def tts_audio(operation_id: str, provider_id: str, pcm: bytes) -> TtsAudio:
    return TtsAudio(
        operation_id=operation_id,
        provider_id=provider_id,
        voice_profile_id=PROFILE,
        sample_rate_hz=16000,
        channels=1,
        sample_width_bytes=2,
        encoding=TtsAudioEncoding.PCM_S16LE,
        pcm_base64=base64.b64encode(pcm).decode("ascii"),
        pcm_sha256=sha256(pcm).hexdigest(),
    )


class FakeTts(TtsProvider):
    def __init__(self) -> None:
        super().__init__("fake_tts")
        self.texts: list[str] = []
        self.fail = False

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return ()

    async def capabilities(self) -> TtsCapabilities:
        return TtsCapabilities(
            provider_id=self.provider_id,
            voices=(
                TtsVoiceProfile(
                    profile_id=PROFILE,
                    display_name="PHOENIX",
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
            supports_cancellation=False,
        )

    async def synthesize(self, request: TtsRequest) -> TtsAudio:
        self.texts.append(request.text)
        if self.fail:
            raise TtsProviderError(TtsProviderErrorCode.UNAVAILABLE)
        return tts_audio(request.operation_id, self.provider_id, tone(3))

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.UNSUPPORTED,
        )

    async def aclose(self) -> None:
        pass


class FakeOutput(AudioOutput):
    def __init__(self, seconds: float = 0.02) -> None:
        super().__init__("fake_output")
        self.seconds = seconds
        self.played: list[str] = []  # provider IDs: fake_tts or earcon
        self.statuses: list[AudioPlaybackStatus] = []
        self.fail = False
        self._active: str | None = None
        self._stop = asyncio.Event()
        self.closed = 0

    @property
    def active_playback_ids(self) -> tuple[str, ...]:
        return () if self._active is None else (self._active,)

    async def play(self, request: AudioPlaybackRequest) -> AudioPlaybackResult:
        self.played.append(request.audio.provider_id)
        if self.fail and request.audio.provider_id != "earcon":
            raise AudioOutputError(AudioOutputErrorCode.UNAVAILABLE)
        self._active = request.playback_id
        self._stop = asyncio.Event()
        try:
            seconds = 0.02 if request.audio.provider_id == "earcon" else self.seconds
            await asyncio.wait_for(self._stop.wait(), seconds)
            status = AudioPlaybackStatus.INTERRUPTED
        except TimeoutError:
            status = AudioPlaybackStatus.COMPLETED
        finally:
            self._active = None
        self.statuses.append(status)
        return AudioPlaybackResult(
            playback_id=request.playback_id, output_id=self.output_id, status=status
        )

    async def interrupt(self, playback_id: str) -> CancellationResult:
        found = self._active == playback_id
        if found:
            self._stop.set()
        return CancellationResult(
            operation_id=playback_id,
            provider_id=self.output_id,
            status=CancellationStatus.ACCEPTED
            if found
            else CancellationStatus.NOT_FOUND,
        )

    async def aclose(self) -> None:
        self.closed += 1


class FakeResponder(VoiceResponder):
    def __init__(self) -> None:
        super().__init__("fake_responder")
        self.transcripts: list[str] = []

    async def respond(self, request: VoiceResponseRequest) -> VoiceResponseDraft:
        self.transcripts.append(request.transcript_text)
        return VoiceResponseDraft(
            session_id=request.session_id,
            turn_id=request.turn_id,
            response_text=f"Answer: {request.transcript_text}",
        )


class HealthyProbe(StartupHealthProbe):
    async def check(self, startup_id: str) -> StartupCheckResult:
        return StartupCheckResult(
            startup_id=startup_id,
            subsystem=self.subsystem,
            status=StartupCheckStatus.HEALTHY,
        )


def probes() -> tuple[StartupHealthProbe, ...]:
    return tuple(HealthyProbe(subsystem) for subsystem in StartupSubsystem)


class Rig:
    def __init__(
        self,
        *,
        ritual: bool = False,
        output_seconds: float = 0.02,
        follow_up: float = 3.0,
    ) -> None:
        self.clock = FakeClock()
        self.mic = FakeMic(self.clock)
        self.wake = FakeWake()
        self.stt = FakeStt()
        self.tts = FakeTts()
        self.output = FakeOutput(output_seconds)
        self.responder = FakeResponder()
        self.changes: list[VoiceStateChange] = []
        self.components = VoiceComponents(
            wake=self.wake,
            stt=self.stt,
            tts=self.tts,
            microphone=self.mic,
            output=self.output,
            voice_profile_id=PROFILE,
        )
        self.pipeline = VoicePipeline(
            self.components,
            self.responder,
            on_state=self.record,
            startup_probes=probes() if ritual else None,
            config=VoicePipelineConfig(follow_up_timeout_seconds=follow_up),
            clock=self.clock,
        )

    async def record(self, change: VoiceStateChange) -> None:
        self.changes.append(change)

    @property
    def states(self) -> list[str]:
        return [change.state.value for change in self.changes]

    def state_is(self, state: VoicePipelineState) -> Callable[[], bool]:
        return lambda: self.pipeline.state is state

    async def run_until(
        self, predicate: Callable[[], bool], timeout: float = 20
    ) -> None:
        task = asyncio.create_task(self.pipeline.run())
        try:
            async with asyncio.timeout(timeout):
                while not predicate():
                    if task.done():
                        task.result()
                        raise AssertionError("pipeline ended")
                    await asyncio.sleep(0.002)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


# ---- wake phrase matching, transcripts and spoken text


@pytest.mark.parametrize(
    "heard",
    [
        "Rise, Phoenix.",
        "rise the Phoenix.",
        "rise of phoenix.",
        "Rias, Phoenix, what time is it?",
        "Brian's a Phoenix.",
        "Okay, rise, Phoenix",
        "Arise Phenix",
    ],
)
def test_wake_phrase_variants_match(heard: str) -> None:
    assert wake_phrase_score(heard) >= whisper_engine.WAKE_DETECTION_THRESHOLD


@pytest.mark.parametrize(
    "heard",
    [
        "",
        "Hello there, how are you?",
        "Phoenix is a city in Arizona.",
        "I love phoenix",
        "rise and shine",
        "The weather today is fine and the phoenix rose",
    ],
)
def test_other_speech_does_not_wake(heard: str) -> None:
    assert wake_phrase_score(heard) < whisper_engine.WAKE_DETECTION_THRESHOLD


def test_strip_wake_phrase_and_speakable_text() -> None:
    assert strip_wake_phrase("Rise, Phoenix, what time is it?") == "what time is it?"
    assert strip_wake_phrase("rise of phoenix. Open notes") == "Open notes"
    assert strip_wake_phrase("Rise, Phoenix.") == ""
    assert strip_wake_phrase("Brian's a Phoenix, open notes") == "open notes"
    assert strip_wake_phrase("What is Phoenix?") == "What is Phoenix?"
    assert speakable_text("**Paris** is the `capital`.") == "Paris is the capital ."
    code = speakable_text("Use this:\n```python\nprint(1)\n```\nDone.")
    assert "print" not in code and "code is shown on screen" in code
    assert "http" not in speakable_text("See https://example.com/x for more.")
    long = speakable_text("This is one sentence. " * 80)
    assert len(long) < 700 and long.endswith("The full answer is on screen.")
    assert speakable_text("```\nx\n```") == "(code is shown on screen)"
    assert speakable_text("   ") == "I have an answer for you on screen."


def test_segments_rechunk_to_one_second_frames_within_stt_bounds() -> None:
    segment = segment_from_pcm(tone(100))  # 3 s
    assert [len(f.pcm_bytes_for_provider()) for f in segment.frames] == [
        32000,
        32000,
        32000,
    ]
    assert segment.start_sequence == 0 and segment.end_sequence == 2
    huge = segment_from_pcm(silence(1200))  # 36 s is cut to the 30 s window
    assert sum(len(f.pcm_bytes_for_provider()) for f in huge.frames) == 960000


def record(status: MissionStatus) -> MissionRecord:
    return MissionRecord.model_construct(status=status)


def test_spoken_outcomes_never_claim_success_or_approve() -> None:
    assert _spoken_outcome(record(MissionStatus.COMPLETED), "Paris.") == "Paris."
    assert "permission" in _spoken_outcome(
        record(MissionStatus.WAITING_PERMISSION), None
    )
    assert "couldn't" in _spoken_outcome(record(MissionStatus.FAILED), None)
    assert "couldn't" in _spoken_outcome(record(MissionStatus.COMPLETED), None)
    assert "cancelled" in _spoken_outcome(record(MissionStatus.CANCELLED), None)
    assert "longer" in _spoken_outcome(None, None)
    # The responder reads missions; it never decides permissions (INV-003).
    tree = ast.parse((ROOT / "phoenix/api/voice.py").read_text("utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not called & {"approve", "deny", "revoke", "use_runtime", "run_again"}


# ---- faster-whisper adapters


class FakeSegment:
    def __init__(self, text: str, no_speech: float = 0.1, logprob: float = -0.2):
        self.text = text
        self.no_speech_prob = no_speech
        self.avg_logprob = logprob


class FakeWhisperModel:
    def __init__(self, segments: list[FakeSegment]) -> None:
        self.segments = segments
        self.calls: list[dict[str, Any]] = []

    def transcribe(self, audio: Any, **options: Any) -> tuple[Any, Any]:
        self.calls.append({"samples": len(audio), **options})
        return iter(self.segments), None


def model_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "whisper"
    directory.mkdir()
    for name in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"):
        (directory / name).write_bytes(b"x")
    return directory


def test_whisper_engine_filters_hallucinations_and_never_loads_missing(
    tmp_path: Path,
) -> None:
    built: list[tuple[Path, int]] = []
    model = FakeWhisperModel(
        [
            FakeSegment(" What time"),
            FakeSegment(" is it?"),
            FakeSegment(" Thank you.", no_speech=0.9),
            FakeSegment(" you", logprob=-1.5),
        ]
    )

    def factory(directory: Path, threads: int) -> Any:
        built.append((directory, threads))
        return model

    missing = WhisperEngine(tmp_path / "absent", model_factory=factory)
    with pytest.raises(VoiceModelMissingError):
        missing.load()
    assert built == []
    engine = WhisperEngine(model_dir(tmp_path), cpu_threads=6, model_factory=factory)
    assert engine.transcribe(tone(10)) == "What time is it?"
    assert built == [(tmp_path / "whisper", 6)]
    call = model.calls[0]
    assert call["samples"] == 4800 and call["language"] == "en"
    assert call["vad_filter"] is False and call["beam_size"] == 1
    assert call["condition_on_previous_text"] is False


def test_faster_whisper_loads_on_cpu_int8_local_files_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    class WhisperModel:
        def __init__(self, path: str, **options: Any) -> None:
            seen.update(path=path, **options)

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = WhisperModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    WhisperEngine(model_dir(tmp_path), cpu_threads=4).load()
    assert seen == {
        "path": str(tmp_path / "whisper"),
        "device": "cpu",
        "compute_type": "int8",
        "cpu_threads": 4,
        "num_workers": 1,
        "local_files_only": True,
    }


def wake_window(pcm: bytes, stream: str = "s") -> WakeWordRequest:
    return WakeWordRequest(
        phrase=DEFAULT_WAKE_PHRASE, frames=(pcm_frame(stream, 7, pcm),)
    )


def engine_saying(tmp_path: Path, *texts: str) -> WhisperEngine:
    replies = deque(texts)

    class Model:
        def transcribe(self, audio: Any, **options: Any) -> tuple[Any, Any]:
            return iter([FakeSegment(replies.popleft())]), None

    return WhisperEngine(model_dir(tmp_path), model_factory=lambda d, t: Model())


def test_wake_provider_reports_correlated_evidence(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider = FasterWhisperWakeWordProvider(
            engine_saying(tmp_path, "rise of Phoenix.", f"Hello {SENTINEL}")
        )
        capabilities = await provider.capabilities()
        capabilities.validate_provider(provider.provider_id)
        assert capabilities.supported_phrases == (DEFAULT_WAKE_PHRASE,)
        for expected in (True, False):
            request = wake_window(tone(20))
            detection = await provider.detect(request)
            detection.validate_request(request, provider.provider_id)
            assert detection.detected is expected
            assert SENTINEL not in repr(detection)
        other = WakePhrase(phrase_id="hey", text="Hey there")
        with pytest.raises(WakeWordProviderError) as caught:
            await provider.detect(
                WakeWordRequest(phrase=other, frames=(pcm_frame("s", 0, tone(2)),))
            )
        assert caught.value.code.value == "unsupported_phrase"
        await provider.aclose()
        with pytest.raises(WakeWordProviderError) as caught:
            await provider.detect(wake_window(tone(2)))
        assert caught.value.code.value == "closed"

    run(scenario())


def test_engine_failures_become_closed_codes_without_content(tmp_path: Path) -> None:
    class Broken:
        def transcribe(self, audio: Any, **options: Any) -> tuple[Any, Any]:
            raise RuntimeError(SENTINEL)

    async def scenario() -> None:
        engine = WhisperEngine(model_dir(tmp_path), model_factory=lambda d, t: Broken())
        wake = FasterWhisperWakeWordProvider(engine)
        with pytest.raises(WakeWordProviderError) as caught:
            await wake.detect(wake_window(tone(2)))
        assert caught.value.code.value == "unavailable"
        assert SENTINEL not in str(caught.value) and caught.value.__cause__ is None
        stt = FasterWhisperSttProvider(engine)
        with pytest.raises(SttProviderError) as stt_caught:
            await stt.transcribe(SttRequest(frames=(pcm_frame("s", 0, tone(2)),)))
        assert stt_caught.value.code.value == "unavailable"
        assert SENTINEL not in str(stt_caught.value)
        missing = FasterWhisperSttProvider(WhisperEngine(tmp_path / "none"))
        with pytest.raises(SttProviderError) as stt_caught:
            await missing.transcribe(SttRequest(frames=(pcm_frame("s", 0, tone(2)),)))
        assert stt_caught.value.code.value == "unavailable"

    run(scenario())


def test_stt_provider_transcript_is_correlated_english(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider = FasterWhisperSttProvider(engine_saying(tmp_path, "Open my notes."))
        capabilities = await provider.capabilities()
        capabilities.validate_provider(provider.provider_id)
        request = SttRequest(
            frames=(pcm_frame("s", 3, tone(10)), pcm_frame("s", 4, tone(10)))
        )
        transcript = await provider.transcribe(request)
        transcript.validate_request(request, provider.provider_id)
        assert (transcript.text, transcript.language) == ("Open my notes.", "en")
        with pytest.raises(SttProviderError) as caught:
            await provider.transcribe(
                SttRequest(frames=request.frames, language_hint="fr")
            )
        assert caught.value.code.value == "unsupported_language"

    run(scenario())


# ---- Piper adapter


class FakeChunk:
    def __init__(self, pcm: bytes) -> None:
        self.audio_int16_bytes = pcm


class FakePiperVoice:
    def __init__(self, rate: int = 22050) -> None:
        self.config = types.SimpleNamespace(sample_rate=rate)
        self.texts: list[str] = []

    def synthesize(self, text: str) -> Iterator[FakeChunk]:
        self.texts.append(text)
        yield FakeChunk(b"\x01\x00" * 2205)
        yield FakeChunk(b"\x02\x00" * 2205)


def piper_files(tmp_path: Path) -> Path:
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"x")
    (tmp_path / "voice.onnx.json").write_text("{}")
    return model


def test_piper_provider_synthesizes_with_an_original_primary_voice(
    tmp_path: Path,
) -> None:
    voice = FakePiperVoice()

    async def scenario() -> None:
        provider = piper_engine.PiperTtsProvider(
            piper_files(tmp_path), voice_loader=lambda path: voice
        )
        capabilities = await provider.capabilities()
        capabilities.validate_provider(provider.provider_id)
        (primary,) = capabilities.voices
        assert primary.profile_id == PROFILE and primary.is_primary
        assert primary.provenance is VoiceProfileProvenance.ORIGINAL
        request = TtsRequest(text="All systems go.", voice_profile_id=PROFILE)
        audio = await provider.synthesize(request)
        audio.validate_request(request, capabilities)
        assert audio.sample_rate_hz == 22050
        assert audio.duration_seconds == pytest.approx(0.2)
        assert voice.texts == ["Ready.", "All systems go."]  # warm-up on load

    run(scenario())


def test_piper_never_loads_missing_files_and_uses_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        provider = piper_engine.PiperTtsProvider(tmp_path / "absent.onnx")
        with pytest.raises(TtsProviderError) as caught:
            await provider.capabilities()
        assert caught.value.code.value == "unavailable"

    run(scenario())
    seen: dict[str, Any] = {}

    class PiperVoice:
        @staticmethod
        def load(path: str, **options: Any) -> FakePiperVoice:
            seen.update(path=path, **options)
            return FakePiperVoice()

    module = types.ModuleType("piper")
    module.PiperVoice = PiperVoice  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "piper", module)
    model = piper_files(tmp_path)
    piper_engine.PiperTtsProvider(model).load()
    assert seen == {"path": str(model), "use_cuda": False}


# ---- sounddevice adapters


class FakeSoundDevice(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("sounddevice")
        self.inputs: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.written: list[bytes] = []
        self.reject_rates: set[int] = set()
        self.block_event: threading.Event | None = None
        device = self

        class RawInputStream:
            def __init__(self, **options: Any) -> None:
                if options["samplerate"] in device.reject_rates:
                    raise RuntimeError("Invalid sample rate")
                device.inputs.append(options)
                self.callback = options["callback"]

            def start(self) -> None:
                pass

            def close(self) -> None:
                pass

        class RawOutputStream:
            def __init__(self, **options: Any) -> None:
                if options["samplerate"] in device.reject_rates:
                    raise RuntimeError("Invalid sample rate")
                device.outputs.append(options)
                self.aborted = False

            def __enter__(self) -> RawOutputStream:
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def write(self, data: bytes) -> None:
                device.written.append(data)
                if device.block_event is not None:
                    device.block_event.wait(0.01)

            def abort(self) -> None:
                self.aborted = True

        self.RawInputStream = RawInputStream
        self.RawOutputStream = RawOutputStream

    def query_devices(self, device: object, kind: str) -> dict[str, float]:
        return {"default_samplerate": 48000.0}


def test_microphone_frames_are_consecutive_30ms_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeSoundDevice()
    monkeypatch.setitem(sys.modules, "sounddevice", device)

    async def scenario() -> None:
        mic = sound.SoundDeviceMicrophone()
        await mic.start()
        options = device.inputs[0]
        assert (options["samplerate"], options["channels"], options["dtype"]) == (
            16000,
            1,
            "int16",
        )
        options["callback"](tone(3)[:1500], 750, None, None)
        options["callback"](tone(3)[1500:], 690, None, None)
        await asyncio.sleep(0.01)
        frames = [await mic.read() for _ in range(3)]
        assert [f.sequence for f in frames] == [0, 1, 2]
        assert {len(f.pcm_bytes_for_provider()) for f in frames} == {960}
        assert b"".join(f.pcm_bytes_for_provider() for f in frames) == tone(3)
        await mic.aclose()
        with pytest.raises(MicrophoneError):
            await mic.read()

    run(scenario())


def test_microphone_falls_back_to_the_device_rate_and_resamples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeSoundDevice()
    device.reject_rates = {16000}
    monkeypatch.setitem(sys.modules, "sounddevice", device)

    async def scenario() -> None:
        mic = sound.SoundDeviceMicrophone()
        await mic.start()
        assert device.inputs[0]["samplerate"] == 48000
        device.inputs[0]["callback"](b"\x10\x00" * 1440 * 2, 2880, None, None)
        await asyncio.sleep(0.01)
        frames = [await mic.read() for _ in range(2)]
        assert {len(f.pcm_bytes_for_provider()) for f in frames} == {960}
        await mic.aclose()

    run(scenario())


def test_microphone_that_cannot_open_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeSoundDevice()
    device.reject_rates = {16000, 48000}
    monkeypatch.setitem(sys.modules, "sounddevice", device)
    with pytest.raises(MicrophoneError):
        run(sound.SoundDeviceMicrophone().start())


def playback(seconds: float, rate: int = 16000) -> AudioPlaybackRequest:
    pcm = b"\x01\x00" * int(rate * seconds)
    audio = TtsAudio(
        operation_id="op",
        provider_id="fake_tts",
        voice_profile_id=PROFILE,
        sample_rate_hz=rate,
        channels=1,
        sample_width_bytes=2,
        encoding=TtsAudioEncoding.PCM_S16LE,
        pcm_base64=base64.b64encode(pcm).decode("ascii"),
        pcm_sha256=sha256(pcm).hexdigest(),
    )
    return AudioPlaybackRequest(
        playback_id="play-1", session_id="s", turn_id="t", audio=audio
    )


def test_output_plays_completes_and_is_interruptible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeSoundDevice()
    monkeypatch.setitem(sys.modules, "sounddevice", device)

    async def scenario() -> None:
        output = sound.SoundDeviceAudioOutput()
        done = await output.play(playback(0.2))
        done.validate_request(playback(0.2), output.output_id)
        assert done.status is AudioPlaybackStatus.COMPLETED
        assert b"".join(device.written) == b"\x01\x00" * 3200
        device.written.clear()
        device.block_event = threading.Event()
        task = asyncio.create_task(output.play(playback(5.0)))
        while not output.active_playback_ids:
            await asyncio.sleep(0.001)
        ack = await output.interrupt("play-1")
        assert ack.status is CancellationStatus.ACCEPTED
        assert (await task).status is AudioPlaybackStatus.INTERRUPTED
        assert len(b"".join(device.written)) < 160000
        missing = await output.interrupt("play-1")
        assert missing.status is CancellationStatus.NOT_FOUND

    run(scenario())


def test_output_resamples_when_the_device_rejects_the_voice_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FakeSoundDevice()
    device.reject_rates = {22050}
    monkeypatch.setitem(sys.modules, "sounddevice", device)

    async def scenario() -> None:
        output = sound.SoundDeviceAudioOutput()
        result = await output.play(playback(0.1, rate=22050))
        assert result.status is AudioPlaybackStatus.COMPLETED
        assert device.outputs[0]["samplerate"] == 48000
        assert len(b"".join(device.written)) == 2 * 4800

    run(scenario())


# ---- the pipeline


def first_wake(rig: Rig, frames: int = 40) -> None:
    rig.mic.feed(tone(frames)).feed(silence(20))


def command(rig: Rig, frames: int = 40) -> None:
    rig.mic.feed(tone(frames)).feed(silence(30))


def test_wake_listen_answer_speak_then_sleep_after_the_follow_up_window() -> None:
    rig = Rig()
    rig.stt.texts.extend(["What is two plus two?", "And three plus three?"])
    first_wake(rig)
    rig.mic.gate(rig.state_is(VoicePipelineState.LISTENING))
    command(rig)
    rig.mic.gate(lambda: rig.pipeline.turn_count == 1 and rig.output.statuses != [])
    rig.mic.gate(rig.state_is(VoicePipelineState.LISTENING))
    command(rig)  # a follow-up needs no wake phrase

    async def scenario() -> None:
        await rig.run_until(
            lambda: rig.pipeline.turn_count == 2
            and rig.pipeline.state is VoicePipelineState.SLEEPING
        )

    run(scenario())
    assert rig.wake.calls == 1
    assert rig.responder.transcripts == [
        "What is two plus two?",
        "And three plus three?",
    ]
    assert rig.tts.texts == [
        "Answer: What is two plus two?",
        "Answer: And three plus three?",
    ]
    assert rig.output.played == ["earcon", "fake_tts", "fake_tts"]
    assert rig.output.statuses[1:] == [AudioPlaybackStatus.COMPLETED] * 2
    states = rig.states
    assert states[:3] == ["sleeping", "listening", "thinking"]
    assert "speaking" in states and states[-1] == "sleeping"
    assert {c.session_id for c in rig.changes} == {rig.pipeline.session_id}
    assert rig.mic.started == 1


def test_first_wake_runs_the_rise_phoenix_ritual_then_later_wakes_chime() -> None:
    rig = Rig(ritual=True, follow_up=1.0)
    first_wake(rig)
    rig.mic.gate(lambda: rig.pipeline.wake_count == 1)
    rig.mic.gate(rig.state_is(VoicePipelineState.SLEEPING))
    first_wake(rig)

    async def scenario() -> None:
        await rig.run_until(lambda: rig.pipeline.wake_count == 2)

    run(scenario())
    assert rig.tts.texts == [STARTUP_ACKNOWLEDGEMENT, STARTUP_HEALTHY_RESPONSE]
    assert rig.output.played == ["fake_tts", "fake_tts", "earcon"]
    assert rig.responder.transcripts == []


def test_wake_phrase_and_request_in_one_breath_becomes_the_first_turn() -> None:
    rig = Rig()
    rig.stt.texts.append("Rise, Phoenix, what time is it?")
    first_wake(rig, frames=80)  # 2.4 s utterance

    async def scenario() -> None:
        await rig.run_until(
            lambda: rig.pipeline.turn_count == 1 and bool(rig.tts.texts)
        )

    run(scenario())
    assert rig.responder.transcripts == ["Rise, Phoenix, what time is it?"]
    assert rig.stt.seconds[0] >= 2.2


def test_not_the_wake_phrase_and_noise_blips_start_nothing() -> None:
    rig = Rig()
    rig.wake.default = False
    rig.mic.feed(tone(4)).feed(silence(20))  # 120 ms blip: below minimum speech
    first_wake(rig)
    rig.mic.gate(lambda: rig.wake.calls == 1)
    rig.mic.feed(silence(50))

    async def scenario() -> None:
        await rig.run_until(lambda: rig.wake.calls == 1 and not rig.mic.script)

    run(scenario())
    assert rig.states == ["sleeping"] and rig.stt.calls == 0
    assert rig.output.played == []


def test_user_speech_interrupts_playback_and_becomes_the_next_turn() -> None:
    rig = Rig(output_seconds=10.0)
    rig.stt.texts.extend(["Tell me a long story.", "Stop, what time is it?"])
    first_wake(rig)
    rig.mic.gate(rig.state_is(VoicePipelineState.LISTENING))
    command(rig)
    rig.mic.gate(rig.state_is(VoicePipelineState.SPEAKING))
    command(rig)  # barge-in: louder than the -28 dBFS barge-in gate

    async def scenario() -> None:
        await rig.run_until(lambda: len(rig.responder.transcripts) == 2)

    start = time.monotonic()
    run(scenario())
    assert time.monotonic() - start < 9  # the 10 s playback was cut short
    assert rig.output.statuses[1] is AudioPlaybackStatus.INTERRUPTED
    assert rig.responder.transcripts[1] == "Stop, what time is it?"
    interrupted = [c for c in rig.changes if c.interrupted]
    assert len(interrupted) == 1 and interrupted[0].state.value == "listening"


def test_quiet_echo_does_not_interrupt_playback() -> None:
    rig = Rig(output_seconds=0.3)
    rig.stt.texts.append("Hello.")
    first_wake(rig)
    rig.mic.gate(rig.state_is(VoicePipelineState.LISTENING))
    command(rig)
    rig.mic.gate(rig.state_is(VoicePipelineState.SPEAKING))
    rig.mic.feed(tone(40, amplitude=600))  # about -37 dBFS: below the gate

    async def scenario() -> None:
        await rig.run_until(lambda: len(rig.output.statuses) == 2)

    run(scenario())
    assert rig.output.statuses[1] is AudioPlaybackStatus.COMPLETED


def test_transcription_failures_retry_quietly_then_fall_back_to_text() -> None:
    rig = Rig()
    failure = SttProviderError(SttProviderErrorCode.UNAVAILABLE)
    rig.stt.texts.extend([failure, failure, failure])
    first_wake(rig)

    def listening_after(calls: int) -> Callable[[], bool]:
        return lambda: (
            rig.stt.calls == calls
            and rig.pipeline.state is VoicePipelineState.LISTENING
        )

    for calls in range(3):
        rig.mic.gate(listening_after(calls))
        command(rig, frames=20)

    async def scenario() -> None:
        await rig.run_until(lambda: rig.pipeline.state is VoicePipelineState.TEXT_ONLY)

    run(scenario())
    assert rig.stt.calls == 3 and rig.responder.transcripts == []
    assert rig.states.count("listening") == 3  # the wake plus two quiet retries
    assert rig.states[-1] == "text_only"


def test_speech_output_failure_degrades_voice_but_answers_continue() -> None:
    rig = Rig(follow_up=1.0)
    rig.tts.fail = True
    rig.stt.texts.extend(["First?", "Second?"])
    first_wake(rig)
    rig.mic.gate(rig.state_is(VoicePipelineState.LISTENING))
    command(rig)
    rig.mic.gate(rig.state_is(VoicePipelineState.DEGRADED_VOICE))
    command(rig)

    async def scenario() -> None:
        await rig.run_until(
            lambda: len(rig.responder.transcripts) == 2
            and rig.pipeline.state is VoicePipelineState.SLEEPING
        )

    run(scenario())
    assert rig.responder.transcripts == ["First?", "Second?"]
    assert rig.tts.texts == ["Answer: First?"]  # degraded: no second synthesis
    assert "degraded_voice" in rig.states


def test_wake_engine_errors_are_skipped_until_they_persist() -> None:
    class FlakyWake(FakeWake):
        async def detect(self, request: WakeWordRequest) -> WakeWordDetection:
            self.calls += 1
            if self.calls <= 2 or self.calls >= 4:
                raise WakeWordProviderError(WakeWordProviderErrorCode.TIMEOUT)
            self.calls -= 1  # counted above
            return await super().detect(request)

    rig = Rig()
    rig.components.wake = rig.wake = FlakyWake()
    rig.pipeline = VoicePipeline(
        rig.components, rig.responder, on_state=rig.record, clock=rig.clock
    )
    for _ in range(9):
        first_wake(rig)
        rig.mic.gate(rig.state_is(VoicePipelineState.SLEEPING))

    async def scenario() -> None:
        with pytest.raises(WakeWordProviderError):
            await rig.pipeline.run()

    run(scenario())
    assert rig.pipeline.wake_count == 1  # the third check woke it
    assert rig.wake.calls == 8  # then five failures in a row end the run


def test_microphone_failure_ends_the_run_with_its_error() -> None:
    rig = Rig()
    rig.mic.fail_after = 5

    async def scenario() -> None:
        with pytest.raises(MicrophoneError):
            await rig.pipeline.run()

    run(scenario())


# ---- events


def test_voice_event_allowlist_mirrors_the_pipeline() -> None:
    assert {s.value for s in VoicePipelineState} == VOICE_STATES
    assert {c.value for c in VoiceFailureCode} == VOICE_CODES
    session = "aaaaaaaa-0000-4000-8000-000000000001"
    for event_type in (
        EventType.VOICE_LISTENING,
        EventType.VOICE_SPEAKING,
        EventType.VOICE_INTERRUPTED,
    ):
        check_payload(
            event_type,
            {"state": "listening", "session_id": session, "turn_id": session},
        )
    for payload in (
        {"state": "listening", "transcript": SENTINEL},
        {"state": SENTINEL},
        {"session_id": "session"},
        {"code": "voice failure"},
        {"state": None},
    ):
        with pytest.raises(EventPayloadError):
            check_payload(EventType.VOICE_LISTENING, payload)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.events.append(event)


def test_voice_service_publishes_allowlisted_state_and_isolates_failures() -> None:
    rig = Rig()
    first_wake(rig)
    sink = RecordingSink()

    async def factory() -> VoiceComponents:
        return rig.components

    async def scenario() -> None:
        service = VoiceService(cast(MissionService, object()), sink, factory=factory)
        assert service.status().state is VoicePipelineState.OFF
        await service.start()
        async with asyncio.timeout(10):
            while service.status().state is not VoicePipelineState.LISTENING:
                await asyncio.sleep(0.002)
        assert (await service.start()).state is VoicePipelineState.LISTENING  # idem
        stopped = await service.stop()
        assert stopped.state is VoicePipelineState.OFF and rig.mic.closed == 1
        assert service.dropped_events == 0

        async def broken() -> VoiceComponents:
            raise VoiceModelMissingError("missing")

        failing = VoiceService(cast(MissionService, object()), sink, factory=broken)
        await failing.start()
        async with asyncio.timeout(10):
            while failing.status().state is not VoicePipelineState.UNAVAILABLE:
                await asyncio.sleep(0.002)
        status = failing.status()
        assert status.code is VoiceFailureCode.MODELS_MISSING
        assert dict(status.components)["conversation"] == "unavailable"
        await failing.aclose()

    run(scenario())
    states = [e.payload["state"] for e in sink.events]
    assert states[:4] == ["starting", "sleeping", "listening", "off"]
    assert states[-3:] == ["starting", "unavailable", "off"]
    assert sink.events[-2].payload["code"] == "models_missing"
    for event in sink.events:
        check_event(event)
        assert event.agent_id == "voice"
        assert event.event_type in (EventType.VOICE_LISTENING, EventType.VOICE_SPEAKING)


def test_missing_models_are_reported_without_loading_any_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("faster_whisper", "piper", "sounddevice"):
        monkeypatch.setitem(sys.modules, name, None)  # importing would fail

    async def scenario() -> None:
        with pytest.raises(VoiceModelMissingError):
            await local_components(tmp_path)()

    run(scenario())


# ---- the backend: same path as POST /chat


TOKEN = secrets.token_urlsafe(32)
PORT = 48767
SECURITY = LocalApiSecurity(TOKEN, host="127.0.0.1", port=PORT)
PASS = '{"checks":[{"criterion":"Correct","passed":true,"evidence_summary":"Fine."}]}'


class ScriptedProvider(ModelProvider):
    def __init__(self) -> None:
        super().__init__("ollama")
        self.requests: list[GenerationRequest] = []

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id, status=ProviderHealthStatus.HEALTHY
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        return ModelCapabilities(
            provider_id="ollama",
            model_id=model_id,
            supports_streaming=True,
            supports_structured_output=False,
            supports_tool_use=True,
            supports_vision=False,
            supports_cancellation=True,
            context_limit_tokens=16384,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.requests.append(request)
        role = request.messages[0].text.splitlines()[0].removeprefix("PHOENIX ROLE: ")
        text = {
            "triage": '{"path": "direct"}',
            "direct": "Four. **Two plus two** is four.",
        }.get(role, PASS)
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text=text,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=5, output_tokens=3),
        )

    async def _no_stream(self) -> AsyncIterator[GenerationChunk]:
        raise ModelProviderError("streaming is not scripted")
        yield  # pragma: no cover

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        return self._no_stream()

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.ACCEPTED,
        )


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx network send attempted")

    def refuse_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("outbound socket connection attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_send)
    monkeypatch.setattr(socket, "create_connection", refuse_connect)


class Backend:
    def __init__(self, tmp_path: Path, rig: Rig, **config: Any) -> None:
        self.settings = AppSettings(data_dir=tmp_path / "data", _env_file=None)  # type: ignore[call-arg]
        self.rig = rig
        self.provider = ScriptedProvider()
        self.factory_calls = 0
        self.fail: Exception | None = None
        self.config = ServerConfig(
            probe_timeout_seconds=0.5, startup_timeout_seconds=5, **config
        )

    async def factory(self) -> VoiceComponents:
        self.factory_calls += 1
        if self.fail is not None:
            raise self.fail
        return self.rig.components

    def client(self, *, token: str | None = TOKEN) -> TestClient:
        provider = self.provider
        app = build_server_app(
            self.settings,
            security=SECURITY,
            config=self.config,
            provider_factory=lambda config: ProviderSet(providers=(provider,)),
            voice_factory=self.factory,
        )
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        return TestClient(app, base_url=f"http://127.0.0.1:{PORT}", headers=headers)


def until(predicate: Callable[[], bool], timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.01)


def voice_state(client: TestClient) -> str:
    response = client.get("/voice/status")
    assert response.status_code == 200
    return str(response.json()["state"])


def test_voice_routes_require_the_token_and_start_nothing_by_themselves(
    tmp_path: Path,
) -> None:
    backend = Backend(tmp_path, Rig())
    with backend.client(token=None) as client:
        for method, path in (
            ("GET", "/voice/status"),
            ("POST", "/voice/start"),
            ("POST", "/voice/stop"),
        ):
            response = client.request(method, path)
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "unauthorized"
        valid = {"Authorization": f"Bearer {TOKEN}"}
        body = client.get("/voice/status", headers=valid).json()
        assert body == {
            "state": "off",
            "code": None,
            "wake_phrase": "Rise, Phoenix",
            "session_id": None,
            "conversation_id": None,
            "mission_id": None,
            "components": body["components"],
        }
        assert [c["id"] for c in body["components"]] == [
            "wake_word",
            "vad",
            "stt",
            "tts",
            "conversation",
            "audio_output",
        ]
    assert backend.factory_calls == 0 and backend.rig.mic.started == 0


def test_spoken_request_runs_as_a_chat_mission_and_the_answer_is_spoken(
    tmp_path: Path,
) -> None:
    rig = Rig()
    rig.stt.texts.append(f"Rise, Phoenix, what is two plus two? {SENTINEL}")
    first_wake(rig, frames=80)
    backend = Backend(tmp_path, rig)
    with backend.client() as client:
        response = client.post("/voice/start")
        assert response.status_code == 202
        assert response.json()["state"] in ("starting", "sleeping")
        # The first wake plays the startup ritual, then answers the request
        # spoken in the same breath.
        until(lambda: len(rig.tts.texts) == 3)
        until(lambda: voice_state(client) == "listening")
        status = client.get("/voice/status").json()
        missions = client.get("/missions").json()["items"]
        assert len(missions) == 1
        (mission,) = missions
        assert status["mission_id"] == mission["mission_id"]
        assert status["conversation_id"] == mission["conversation_id"]
        assert mission["triage_outcome"] == "decided"  # PHOENIX triaged it
        detail = client.get(f"/missions/{mission['mission_id']}").json()
        assert detail["request_text"] == f"what is two plus two? {SENTINEL}"
        assert detail["status"] == "completed"
        assert rig.tts.texts[0] == STARTUP_ACKNOWLEDGEMENT
        assert rig.tts.texts[2] == speakable_text(detail["answer_text"])
        assert rig.tts.texts[2] == "Four. Two plus two is four."
        # A typed follow-up in the same conversation still works.
        typed = client.post(
            "/chat",
            json={"text": "Thanks", "conversation_id": status["conversation_id"]},
        )
        assert typed.status_code == 202
        services = get_services(cast(FastAPI, client.app))
        assert services is not None and services.voice.dropped_events == 0
        stopped = client.post("/voice/stop")
        assert stopped.status_code == 200 and stopped.json()["state"] == "off"
        assert SENTINEL not in stopped.text and SENTINEL not in str(status)
    assert rig.mic.closed == 1


def test_voice_failures_never_break_text_chat(tmp_path: Path) -> None:
    rig = Rig()
    backend = Backend(tmp_path, rig)
    backend.fail = VoiceModelMissingError("missing")
    with backend.client() as client:
        client.post("/voice/start")
        until(lambda: voice_state(client) == "unavailable")
        body = client.get("/voice/status").json()
        assert body["code"] == "models_missing"
        chat = client.post("/chat", json={"text": "Still there?"})
        assert chat.status_code == 202
        mission_id = chat.json()["mission_id"]
        until(
            lambda: client.get(f"/missions/{mission_id}").json()["status"]
            == "completed"
        )
        backend.fail = None
        rig.mic.fail_after = 10
        client.post("/voice/start")
        until(lambda: client.get("/voice/status").json()["code"] is not None)
        assert client.get("/voice/status").json() | {"components": []} == {
            "state": "unavailable",
            "code": "microphone_unavailable",
            "wake_phrase": "Rise, Phoenix",
            "session_id": client.get("/voice/status").json()["session_id"],
            "conversation_id": None,
            "mission_id": None,
            "components": [],
        }
        assert client.get("/ready").status_code == 200


def test_autostart_listens_from_launch(tmp_path: Path) -> None:
    rig = Rig()
    backend = Backend(tmp_path, rig, voice_autostart=True)
    with backend.client() as client:
        until(lambda: voice_state(client) == "sleeping")
        assert backend.factory_calls == 1 and rig.mic.started == 1
    assert rig.mic.closed == 1


def test_autostart_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_VOICE_AUTOSTART", "1")
    assert ServerConfig().voice_autostart is True
    monkeypatch.setenv("PHOENIX_VOICE_AUTOSTART", "yes")
    assert ServerConfig().voice_autostart is False


# ---- models, setup and dependencies


def test_model_manifest_is_pinned_and_checked_locally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    every = [*voice_models.WHISPER_MODELS.values(), voice_models.PIPER_VOICE]
    for model in every:
        for file in model.files:
            assert file.url.startswith("https://huggingface.co/")
            revision = file.url.split("/resolve/")[1].split("/")[0]
            assert len(revision) == 40 and int(revision, 16) >= 0
            assert len(file.sha256) == 64 and int(file.sha256, 16) >= 0
            assert file.size_bytes > 0
    assert voice_models.DEFAULT_WAKE_MODEL == "tiny.en"
    assert voice_models.DEFAULT_STT_MODEL == "base.en"
    monkeypatch.setenv("PHOENIX_VOICE_MODELS_DIR", str(tmp_path))
    assert voice_models.default_models_dir() == tmp_path
    monkeypatch.setenv("PHOENIX_VOICE_STT_MODEL", "small.en")
    assert voice_models.selected_stt_model() == "small.en"
    monkeypatch.setenv("PHOENIX_VOICE_STT_MODEL", "large-v3")
    assert voice_models.selected_stt_model() == "base.en"
    assert len(voice_models.missing_files(tmp_path, voice_models.PIPER_VOICE)) == 2


def test_setup_check_never_downloads(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/voice_setup.py",
            "--check",
            "--models-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **os.environ,
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
        },
    )
    assert result.returncode == 1
    assert "missing  whisper/tiny.en/model.bin" in result.stdout
    assert "fetching" not in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_engines_stay_offline_on_the_cpu() -> None:
    for path in ("whisper.py", "piper.py", "sound.py", "pipeline.py", "models.py"):
        source = (ROOT / "phoenix/voice/engines" / path).read_text("utf-8")
        for name in ("snapshot_download", "download_model", "hf_hub", "urlopen"):
            assert name not in source
        assert "cuda" not in source.lower().replace("use_cuda=false", "")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for name in names:
                    # Engines load lazily through importlib, never at import.
                    assert name.split(".")[0] not in {
                        "faster_whisper",
                        "piper",
                        "sounddevice",
                        "numpy",
                        "huggingface_hub",
                        "logging",
                    }
    whisper_source = (ROOT / "phoenix/voice/engines/whisper.py").read_text("utf-8")
    assert (
        'device="cpu"' in whisper_source and "local_files_only=True" in whisper_source
    )
    assert "use_cuda=False" in (ROOT / "phoenix/voice/engines/piper.py").read_text(
        "utf-8"
    )


# PHX-114 dependency delta, against its base (origin/main at 5722431).
BASE = "57224311c1449f7a71ff0417ed11a04a2df7c0a1"
REQUIREMENTS = [
    "faster-whisper>=1.2.1,<2",
    "numpy>=2,<3",
    "piper-tts>=1.8,<2",
    "sounddevice>=0.5.6,<0.6",
]
LOCKED = {
    "av": "18.1.0",
    "cffi": "2.1.1",
    "ctranslate2": "4.8.3",
    "faster-whisper": "1.2.1",
    "filelock": "4.2.4",
    "flatbuffers": "25.12.19",
    "fsspec": "2026.9.0",
    "hf-xet": "1.7.0",
    "huggingface-hub": "0.36.2",
    "numpy": "2.4.6",
    "onnxruntime": "1.31.0",
    "pathvalidate": "3.3.1",
    "piper-tts": "1.8.0",
    "protobuf": "7.36.2",
    "pycparser": "3.11",
    "pyyaml": "6.0.3",
    "requests": "2.34.2",
    "sounddevice": "0.5.6",
    "tokenizers": "0.23.3",
    "tqdm": "4.70.1",
    "urllib3": "2.8.0",
}


def at_base(path: str) -> str:
    return subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT).decode()


def test_exact_voice_dependency_delta() -> None:
    before = tomllib.loads(at_base("pyproject.toml"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    for requirement in REQUIREMENTS:
        assert project["project"]["dependencies"].count(requirement) == 1
        project["project"]["dependencies"].remove(requirement)
    assert project["tool"].pop("uv") == {
        "constraint-dependencies": ["huggingface-hub<1"]
    }
    assert project == before
    old = tomllib.loads(at_base("uv.lock"))
    new = tomllib.loads((ROOT / "uv.lock").read_text())
    old_records = {p["name"]: p for p in old.pop("package")}
    new_records = {p["name"]: p for p in new.pop("package")}
    assert new.pop("manifest") == {
        "constraints": [{"name": "huggingface-hub", "specifier": "<1"}]
    }
    assert old == new
    assert {
        n: new_records[n]["version"] for n in set(new_records) - set(old_records)
    } == LOCKED
    assert not set(old_records) - set(new_records)
    for name, locked in LOCKED.items():
        assert new_records[name]["source"] == {"registry": "https://pypi.org/simple"}
        artifacts = [*new_records[name].get("wheels", [])]
        if "sdist" in new_records[name]:
            artifacts.append(new_records[name]["sdist"])
        assert artifacts and all(a["hash"].startswith("sha256:") for a in artifacts)
        try:
            assert version(name) == locked
        except PackageNotFoundError:
            # hub 0.36 marks hf-xet for "amd64", not Windows' "AMD64".
            assert name == "hf-xet"
    names = {r.split(">")[0] for r in REQUIREMENTS}
    for name, record in old_records.items():
        after = copy.deepcopy(new_records[name])
        if name == "phoenix":
            for entries in (after["dependencies"], after["metadata"]["requires-dist"]):
                entries[:] = [x for x in entries if x["name"] not in names]
        assert after == record, name
    assert metadata("faster-whisper")["License"] == "MIT"
    assert metadata("piper-tts")["License"] == "GPL-3.0-or-later"  # ADR-106
    assert metadata("sounddevice")["License-Expression"] == "MIT"


# ---- live (real models; no microphone): PHOENIX_VOICE_LIVE=1


def _models_ready() -> bool:
    root = voice_models.default_models_dir()
    return not any(
        voice_models.missing_files(root, m)
        for m in (
            voice_models.WHISPER_MODELS["tiny.en"],
            voice_models.WHISPER_MODELS[voice_models.selected_stt_model()],
            voice_models.PIPER_VOICE,
        )
    )


live = pytest.mark.skipif(
    os.environ.get("PHOENIX_VOICE_LIVE") != "1" or not _models_ready(),
    reason="live voice checks need PHOENIX_VOICE_LIVE=1 and installed models",
)


@live
def test_live_piper_speech_wakes_and_transcribes() -> None:
    root = voice_models.default_models_dir()

    async def scenario() -> tuple[bool, str]:
        tts = piper_engine.PiperTtsProvider(
            voice_models.PIPER_VOICE.path(root)
            / voice_models.PIPER_VOICE.files[0].relative_path
        )

        async def speak(text: str) -> bytes:
            audio = await tts.synthesize(
                TtsRequest(text=text, voice_profile_id=PROFILE)
            )
            return sound.resample_pcm(
                audio.pcm_bytes_for_playback(), audio.sample_rate_hz, 16000
            )

        wake = FasterWhisperWakeWordProvider(
            WhisperEngine(voice_models.WHISPER_MODELS["tiny.en"].path(root))
        )
        heard = await wake.detect(
            WakeWordRequest(
                phrase=DEFAULT_WAKE_PHRASE,
                frames=segment_from_pcm((await speak("Rise, Phoenix."))[:96000]).frames,
            )
        )
        stt = FasterWhisperSttProvider(
            WhisperEngine(
                voice_models.WHISPER_MODELS[voice_models.selected_stt_model()].path(
                    root
                ),
                beam_size=5,
            )
        )
        segment = segment_from_pcm(await speak("What is the capital of France?"))
        transcript = await stt.transcribe(SttRequest(frames=segment.frames))
        return heard.detected, transcript.text

    detected, text = run(scenario())
    assert detected
    assert "capital of france" in text.lower()
