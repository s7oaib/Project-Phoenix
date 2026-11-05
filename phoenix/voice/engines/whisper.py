"""faster-whisper wake spotting and transcription on the CPU (PHX-114, ADR-105).

Both adapters run a CTranslate2 Whisper model with ``device="cpu"`` and
``compute_type="int8"`` from a local model directory (``local_files_only``), so
the GPU stays with Ollama and nothing is downloaded. Inference is blocking
native work: it runs in a worker thread, one operation per adapter at a time,
under a caller-visible deadline. A deadline or cancellation abandons the
result; it cannot stop native inference already running (as the contracts say).

Wake spotting transcribes a short utterance with the small model and matches
"Rise, Phoenix" variants in the text (``wake_phrase_score``). Transcripts,
audio and backend diagnostics are never logged, stored or put in errors.
"""

from __future__ import annotations

import asyncio
import importlib
import re
import threading
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Final

from phoenix.models.provider import CancellationResult, CancellationStatus
from phoenix.voice.audio import AudioEncoding
from phoenix.voice.stt import (
    SttCapabilities,
    SttProvider,
    SttProviderError,
    SttProviderErrorCode,
    SttRequest,
    SttTranscript,
)
from phoenix.voice.wake import (
    DEFAULT_WAKE_PHRASE,
    WakeWordCapabilities,
    WakeWordDetection,
    WakeWordProvider,
    WakeWordProviderError,
    WakeWordProviderErrorCode,
    WakeWordRequest,
)

__all__ = (
    "WAKE_DETECTION_THRESHOLD",
    "VoiceModelMissingError",
    "WhisperEngine",
    "stop_phrase_score",
    "wake_phrase_score",
    "FasterWhisperWakeWordProvider",
    "FasterWhisperSttProvider",
)

_REQUIRED_FILES: Final = (
    "config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.txt",
)
# Segments Whisper itself marks as probable non-speech, or decodes with very
# low confidence, are dropped: they are where it hallucinates ("Thank you.").
_NO_SPEECH_MAX: Final = 0.6
_AVG_LOGPROB_MIN: Final = -1.0
WAKE_DETECTION_THRESHOLD: Final = 0.7
WAKE_TIMEOUT_SECONDS: Final = 15.0
STT_TIMEOUT_SECONDS: Final = 60.0

ModelFactory = Callable[[Path, int], Any]


class VoiceModelMissingError(RuntimeError):
    """A required model file is absent; the message names no user data."""


def _load_faster_whisper(directory: Path, cpu_threads: int) -> Any:
    module = importlib.import_module("faster_whisper")
    return module.WhisperModel(
        str(directory),
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads,
        num_workers=1,
        local_files_only=True,
    )


class WhisperEngine:
    """One local CPU model; ``transcribe`` is blocking and thread-serialized."""

    def __init__(
        self,
        directory: Path,
        *,
        cpu_threads: int = 4,
        beam_size: int = 1,
        model_factory: ModelFactory | None = None,
    ) -> None:
        if not 1 <= cpu_threads <= 32 or not 1 <= beam_size <= 8:
            raise ValueError("invalid engine options")
        self._directory = directory
        self._threads = cpu_threads
        self._beam = beam_size
        self._factory = model_factory or _load_faster_whisper
        self._model: Any = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Blocking: load the model once (never downloads)."""
        with self._lock:
            if self._model is not None:
                return
            if not all((self._directory / name).is_file() for name in _REQUIRED_FILES):
                raise VoiceModelMissingError("voice model files are missing")
            self._model = self._factory(self._directory, self._threads)

    def transcribe(self, pcm: bytes) -> str:
        """Blocking: mono 16 kHz S16LE in, English text out ('' for no speech)."""
        self.load()
        numpy = importlib.import_module("numpy")
        audio = numpy.frombuffer(pcm, dtype="<i2").astype(numpy.float32) / 32768.0
        with self._lock:
            segments, _info = self._model.transcribe(
                audio,
                language="en",
                beam_size=self._beam,
                temperature=0.0,
                condition_on_previous_text=False,
                without_timestamps=True,
                vad_filter=False,
            )
            texts = [
                str(segment.text).strip()
                for segment in segments
                if float(segment.no_speech_prob) < _NO_SPEECH_MAX
                and float(segment.avg_logprob) > _AVG_LOGPROB_MIN
            ]
        return " ".join(text for text in texts if text)


_WORD = re.compile(r"[a-z']+")
_FILLERS: Final = frozenset({"the", "of", "a", "uh", "um", "oh", "and"})
_RISE_FORMS: Final = frozenset(
    {"rise", "rises", "rise's", "arise", "rias", "rice", "raise", "rys", "ryes"}
)
_PHOENIX_FORMS: Final = frozenset(
    {"phoenix", "phoenix's", "phenix", "feenix", "fenix", "phoenixes"}
)


def _similar(word: str, forms: frozenset[str], target: str) -> float:
    if word in forms:
        return 1.0
    return SequenceMatcher(None, word, target).ratio()


def wake_phrase_score(text: str) -> float:
    """How well a transcript matches "Rise, Phoenix" (0 to 1).

    A Phoenix-like word within the first four words, preceded (ignoring fillers
    such as "the" or "of", which Whisper inserts) by a rise-like word. Whisper
    hears the phrase as e.g. "rise of Phoenix", "Rias, Phoenix" or "Brian's a
    Phoenix"; "Phoenix is a city" starts with Phoenix and does not match.
    """
    words = _WORD.findall(text.lower())[:6]
    best = 0.0
    for index, word in enumerate(words[:4]):
        phoenix = _similar(word, _PHOENIX_FORMS, "phoenix")
        if phoenix < 0.75:
            continue
        before = [w for w in words[:index] if w not in _FILLERS]
        if not before:
            continue
        rise = _similar(before[-1], _RISE_FORMS, "rise")
        best = max(best, (rise + phoenix) / 2)
    # "Phoenix, rise" (the owner's wake phrase, 2026-11-05): a Phoenix-like word in the
    # first three words, then a rise-like word. "Phoenix is a city" does not match.
    for index, word in enumerate(words[:3]):
        phoenix = _similar(word, _PHOENIX_FORMS, "phoenix")
        after = [w for w in words[index + 1 :] if w not in _FILLERS][:1]
        if phoenix < 0.75 or not after:
            continue
        rise = _similar(after[0], _RISE_FORMS, "rise")
        if rise >= 0.75:
            best = max(best, (rise + phoenix) / 2)
    # The shortest wake word: just "Phoenix" (or "hey Phoenix") as the first words.
    for word in words[:2]:
        phoenix = _similar(word, _PHOENIX_FORMS, "phoenix")
        if phoenix >= 0.75:
            best = max(best, phoenix)
    return best


_STOP_FORMS: Final = frozenset({"stop", "stops", "stopped", "halt", "cancel", "enough"})
_STOP_EXTRAS: Final = frozenset({"it", "that", "now", "please", "hey", "ok", "okay"})


def stop_phrase_score(text: str) -> float:
    """How well a transcript is a stop command ("stop", "Stop, Phoenix"), 0 to 1.

    The whole utterance (at most four words) must be a stop-like word plus only
    filler words, "it", "that", "now", "please" or Phoenix. "Stop the music" is a
    real request, so it does not match.
    """
    words = _WORD.findall(text.lower())
    if not words or len(words) > 4:
        return 0.0
    stop = max(_similar(w, _STOP_FORMS, "stop") for w in words)
    if stop < 0.75:
        return 0.0
    for word in words:
        if _similar(word, _STOP_FORMS, "stop") >= 0.75:
            continue
        if _similar(word, _PHOENIX_FORMS, "phoenix") >= 0.75:
            continue
        if word not in _STOP_EXTRAS and word not in _FILLERS:
            return 0.0
    return stop


def _loop_guard() -> Callable[[], None]:
    state: dict[str, asyncio.AbstractEventLoop] = {}

    def check() -> None:
        loop = asyncio.get_running_loop()
        bound = state.setdefault("loop", loop)
        if bound is not loop:
            raise RuntimeError("wrong event loop")

    return check


class FasterWhisperWakeWordProvider(WakeWordProvider):
    """Spots the default wake phrase only; detection is untrusted evidence."""

    def __init__(
        self,
        engine: WhisperEngine,
        provider_id: str = "faster_whisper_wake",
        *,
        threshold: float = WAKE_DETECTION_THRESHOLD,
        timeout_seconds: float = WAKE_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(provider_id)
        self._engine = engine
        self._threshold = threshold
        self._timeout = timeout_seconds
        self._active: str | None = None
        self._closed = False
        self._check_loop = _loop_guard()
        # Was the last transcript "Stop, Phoenix"? A flag only; text is never kept.
        self.stop_heard = False

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return () if self._active is None else (self._active,)

    def _enter(self) -> None:
        try:
            self._check_loop()
        except RuntimeError:
            raise WakeWordProviderError(
                WakeWordProviderErrorCode.WRONG_EVENT_LOOP
            ) from None
        if self._closed:
            raise WakeWordProviderError(WakeWordProviderErrorCode.CLOSED)

    async def capabilities(self) -> WakeWordCapabilities:
        self._enter()
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
        self._enter()
        if type(request) is not WakeWordRequest:
            raise WakeWordProviderError(WakeWordProviderErrorCode.INVALID_INPUT)
        accepted = WakeWordRequest.model_validate(request)
        if accepted.phrase != DEFAULT_WAKE_PHRASE:
            raise WakeWordProviderError(WakeWordProviderErrorCode.UNSUPPORTED_PHRASE)
        if self._active is not None:
            raise WakeWordProviderError(WakeWordProviderErrorCode.BUSY)
        pcm = b"".join(frame.pcm_bytes_for_provider() for frame in accepted.frames)
        self._active = accepted.operation_id
        try:
            async with asyncio.timeout(self._timeout):
                text = await asyncio.to_thread(self._engine.transcribe, pcm)
        except TimeoutError:
            raise WakeWordProviderError(WakeWordProviderErrorCode.TIMEOUT) from None
        except VoiceModelMissingError:
            raise WakeWordProviderError(WakeWordProviderErrorCode.UNAVAILABLE) from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise WakeWordProviderError(WakeWordProviderErrorCode.UNAVAILABLE) from None
        finally:
            self._active = None
        score = float(wake_phrase_score(text))
        self.stop_heard = stop_phrase_score(text) >= self._threshold
        return WakeWordDetection(
            operation_id=accepted.operation_id,
            provider_id=self.provider_id,
            phrase_id=accepted.phrase.phrase_id,
            stream_id=accepted.frames[0].stream_id,
            start_sequence=accepted.frames[0].sequence,
            end_sequence=accepted.frames[-1].sequence,
            detected=score >= self._threshold,
            score=max(0.0, min(1.0, score)),
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        self._enter()
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.UNSUPPORTED,
        )

    async def aclose(self) -> None:
        self._closed = True


class FasterWhisperSttProvider(SttProvider):
    """English transcription of one bounded utterance; text is untrusted."""

    def __init__(
        self,
        engine: WhisperEngine,
        provider_id: str = "faster_whisper_stt",
        *,
        timeout_seconds: float = STT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(provider_id)
        self._engine = engine
        self._timeout = timeout_seconds
        self._active: str | None = None
        self._closed = False
        self._check_loop = _loop_guard()

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return () if self._active is None else (self._active,)

    def _enter(self) -> None:
        try:
            self._check_loop()
        except RuntimeError:
            raise SttProviderError(SttProviderErrorCode.WRONG_EVENT_LOOP) from None
        if self._closed:
            raise SttProviderError(SttProviderErrorCode.CLOSED)

    async def capabilities(self) -> SttCapabilities:
        self._enter()
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
        self._enter()
        if type(request) is not SttRequest:
            raise SttProviderError(SttProviderErrorCode.INVALID_INPUT)
        accepted = SttRequest.model_validate(request)
        if accepted.language_hint not in (None, "en"):
            raise SttProviderError(SttProviderErrorCode.UNSUPPORTED_LANGUAGE)
        if self._active is not None:
            raise SttProviderError(SttProviderErrorCode.BUSY)
        pcm = accepted.pcm_bytes_for_provider()
        self._active = accepted.operation_id
        try:
            async with asyncio.timeout(self._timeout):
                text = await asyncio.to_thread(self._engine.transcribe, pcm)
        except TimeoutError:
            raise SttProviderError(SttProviderErrorCode.TIMEOUT) from None
        except asyncio.CancelledError:
            raise
        except Exception:
            raise SttProviderError(SttProviderErrorCode.UNAVAILABLE) from None
        finally:
            self._active = None
        return SttTranscript(
            operation_id=accepted.operation_id,
            provider_id=self.provider_id,
            stream_id=accepted.frames[0].stream_id,
            start_sequence=accepted.frames[0].sequence,
            end_sequence=accepted.frames[-1].sequence,
            text=text[:20000],
            language="en",
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        self._enter()
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.UNSUPPORTED,
        )

    async def aclose(self) -> None:
        self._closed = True
