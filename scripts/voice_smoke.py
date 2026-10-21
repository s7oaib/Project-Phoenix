"""PHX-114 owner-run voice smoke checks (real models, CPU only; never in CI).

    uv run python scripts/voice_smoke.py measure        # latencies, no devices
    uv run python scripts/voice_smoke.py speak "Hello"  # speaker check
    uv run python scripts/voice_smoke.py mic            # 4 s mic level + text
    uv run python scripts/voice_smoke.py live           # full loop, echo replies

``measure`` synthesizes test speech with Piper (so it needs no microphone),
then times wake detection on "Rise, Phoenix.", transcription of a ~3 s
utterance and Piper's time to first audio. ``live`` runs the real pipeline with
the microphone and speaker; it answers by repeating what it heard, so it checks
voice without the backend or Ollama (the app path is ``PHOENIX_VOICE_AUTOSTART=1``
or ``POST /voice/start``). Models come from the voice models directory; run
``scripts/voice_setup.py`` first. Nothing here downloads or uses the GPU.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
import wave
from pathlib import Path
from typing import Any

from phoenix.voice.conversation import (
    VoiceResponder,
    VoiceResponseDraft,
    VoiceResponseRequest,
)
from phoenix.voice.engines.models import (
    DEFAULT_WAKE_MODEL,
    PIPER_VOICE,
    WHISPER_MODELS,
    default_models_dir,
    selected_stt_model,
)
from phoenix.voice.engines.pipeline import (
    VoiceComponents,
    VoicePipeline,
    VoiceStateChange,
    pcm_dbfs,
    segment_from_pcm,
    strip_wake_phrase,
)
from phoenix.voice.engines.piper import PRIMARY_PROFILE_ID, PiperTtsProvider
from phoenix.voice.engines.sound import (
    SoundDeviceAudioOutput,
    SoundDeviceMicrophone,
    resample_pcm,
)
from phoenix.voice.engines.whisper import (
    FasterWhisperSttProvider,
    FasterWhisperWakeWordProvider,
    WhisperEngine,
)
from phoenix.voice.stt import SttRequest
from phoenix.voice.tts import TtsRequest
from phoenix.voice.wake import DEFAULT_WAKE_PHRASE, WakeWordRequest

UTTERANCE = "What is the capital of France, and how far is it from London?"
REPLY = "All agents online. All core systems operational. What's the mission?"


def _paths(stt_model: str) -> tuple[Path, Path, Path]:
    root = default_models_dir()
    return (
        WHISPER_MODELS[DEFAULT_WAKE_MODEL].path(root),
        WHISPER_MODELS[stt_model].path(root),
        PIPER_VOICE.path(root) / PIPER_VOICE.files[0].relative_path,
    )


def _timed(label: str, action: Any) -> Any:
    started = time.perf_counter()
    result = action()
    print(f"{label}: {time.perf_counter() - started:.2f} s")
    return result


async def _speech(tts: PiperTtsProvider, text: str) -> bytes:
    request = TtsRequest(text=text, voice_profile_id=PRIMARY_PROFILE_ID)
    audio = await tts.synthesize(request)
    return resample_pcm(audio.pcm_bytes_for_playback(), audio.sample_rate_hz, 16000)


def _summary(label: str, values: list[float]) -> None:
    print(
        f"{label}: median {statistics.median(values) * 1000:.0f} ms, "
        f"min {min(values) * 1000:.0f} ms, max {max(values) * 1000:.0f} ms "
        f"(n={len(values)})"
    )


async def measure(stt_model: str, runs: int, wav_out: Path | None) -> None:
    wake_dir, stt_dir, voice_path = _paths(stt_model)
    print(
        f"models: {default_models_dir()} (wake {DEFAULT_WAKE_MODEL}, stt {stt_model})"
    )
    tts = PiperTtsProvider(voice_path)
    wake_engine = WhisperEngine(wake_dir, cpu_threads=4, beam_size=1)
    stt_engine = WhisperEngine(stt_dir, cpu_threads=8, beam_size=5)
    _timed("load wake model", wake_engine.load)
    _timed("load stt model", stt_engine.load)
    _timed("load + warm Piper voice", tts.load)
    wake = FasterWhisperWakeWordProvider(wake_engine)
    stt = FasterWhisperSttProvider(stt_engine)

    phrase = await _speech(tts, "Rise, Phoenix.")
    utterance = await _speech(tts, UTTERANCE)
    print(
        f"test utterance: {len(utterance) / 32000:.2f} s (Piper, resampled to 16 kHz)"
    )
    if wav_out is not None:
        with wave.open(str(wav_out), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(utterance)
        print(f"wrote {wav_out}")

    wake_times, stt_times, first_audio, full_reply = [], [], [], []
    detected = 0
    text = ""
    for _ in range(runs):
        request = WakeWordRequest(
            phrase=DEFAULT_WAKE_PHRASE, frames=segment_from_pcm(phrase[:96000]).frames
        )
        started = time.perf_counter()
        detection = await wake.detect(request)
        wake_times.append(time.perf_counter() - started)
        detected += detection.detected
        segment = segment_from_pcm(utterance)
        started = time.perf_counter()
        transcript = await stt.transcribe(SttRequest(frames=segment.frames))
        stt_times.append(time.perf_counter() - started)
        text = transcript.text
        voice: Any = tts._voice  # noqa: SLF001 - timing Piper's first chunk
        started = time.perf_counter()
        for _chunk in voice.synthesize(REPLY):
            first_audio.append(time.perf_counter() - started)
            break
        started = time.perf_counter()
        await tts.synthesize(
            TtsRequest(text=REPLY, voice_profile_id=PRIMARY_PROFILE_ID)
        )
        full_reply.append(time.perf_counter() - started)
    print(f"wake detected {detected}/{runs}; transcript: {text!r}")
    _summary("wake detection (transcribe + match, after end of speech)", wake_times)
    print("  + endpointer hangover before detection starts: 450 ms")
    _summary(
        f"STT {len(utterance) / 32000:.1f} s utterance ({stt_model}, beam 5)", stt_times
    )
    _summary("TTS time to first audio (first sentence)", first_audio)
    _summary("TTS full reply (what playback waits for today)", full_reply)


async def speak(text: str) -> None:
    _, _, voice_path = _paths(selected_stt_model())
    tts = PiperTtsProvider(voice_path)
    await asyncio.to_thread(tts.load)
    request = TtsRequest(text=text, voice_profile_id=PRIMARY_PROFILE_ID)
    audio = await tts.synthesize(request)
    from phoenix.voice.conversation import AudioPlaybackRequest

    output = SoundDeviceAudioOutput()
    result = await output.play(
        AudioPlaybackRequest(
            playback_id="smoke", session_id="smoke", turn_id="smoke", audio=audio
        )
    )
    print(f"played {audio.duration_seconds:.1f} s: {result.status.value}")


async def mic(seconds: float, stt_model: str) -> None:
    _, stt_dir, _ = _paths(stt_model)
    engine = WhisperEngine(stt_dir, cpu_threads=8, beam_size=5)
    await asyncio.to_thread(engine.load)
    microphone = SoundDeviceMicrophone()
    await microphone.start()
    print(f"speak now ({seconds:.0f} s)...")
    frames = [await microphone.read() for _ in range(int(seconds / 0.03))]
    await microphone.aclose()
    pcm = b"".join(frame.pcm_bytes_for_provider() for frame in frames)
    print(f"level: {pcm_dbfs(pcm):.1f} dBFS (endpointer speech threshold: -45 dBFS)")
    print(f"heard: {engine.transcribe(pcm)!r}")


class EchoResponder(VoiceResponder):
    def __init__(self) -> None:
        super().__init__("smoke_echo")

    async def respond(self, request: VoiceResponseRequest) -> VoiceResponseDraft:
        heard = strip_wake_phrase(request.transcript_text).strip() or "nothing"
        print(f"heard: {request.transcript_text!r}")
        return VoiceResponseDraft(
            session_id=request.session_id,
            turn_id=request.turn_id,
            response_text=f"You said: {heard}",
        )


async def live(stt_model: str) -> None:
    wake_dir, stt_dir, voice_path = _paths(stt_model)
    wake_engine = WhisperEngine(wake_dir, cpu_threads=4, beam_size=1)
    stt_engine = WhisperEngine(stt_dir, cpu_threads=8, beam_size=5)
    tts = PiperTtsProvider(voice_path)
    for load in (wake_engine.load, stt_engine.load, tts.load):
        await asyncio.to_thread(load)
    components = VoiceComponents(
        wake=FasterWhisperWakeWordProvider(wake_engine),
        stt=FasterWhisperSttProvider(stt_engine),
        tts=tts,
        microphone=SoundDeviceMicrophone(),
        output=SoundDeviceAudioOutput(),
        voice_profile_id=PRIMARY_PROFILE_ID,
    )
    started = time.perf_counter()

    async def report(change: VoiceStateChange) -> None:
        extra = " (barge-in)" if change.interrupted else ""
        print(f"[{time.perf_counter() - started:7.2f} s] {change.state.value}{extra}")

    pipeline = VoicePipeline(components, EchoResponder(), on_state=report)
    print('say "Rise, Phoenix", then a request; Ctrl+C to stop')
    try:
        await pipeline.run()
    finally:
        await components.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stt", choices=sorted(WHISPER_MODELS), default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    measured = commands.add_parser("measure")
    measured.add_argument("--runs", type=int, default=5)
    measured.add_argument("--wav-out", type=Path, default=None)
    spoken = commands.add_parser("speak")
    spoken.add_argument("text")
    recorded = commands.add_parser("mic")
    recorded.add_argument("--seconds", type=float, default=4.0)
    commands.add_parser("live")
    args = parser.parse_args(argv)
    stt_model = args.stt or selected_stt_model()
    try:
        if args.command == "measure":
            asyncio.run(measure(stt_model, max(1, args.runs), args.wav_out))
        elif args.command == "speak":
            asyncio.run(speak(args.text))
        elif args.command == "mic":
            asyncio.run(mic(args.seconds, stt_model))
        else:
            asyncio.run(live(stt_model))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
