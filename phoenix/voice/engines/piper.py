"""Piper speech synthesis on the CPU (PHX-114, ADR-106).

One local Piper voice (ONNX Runtime CPU provider, ``use_cuda=False``) from the
voice models directory; nothing is downloaded. Loading also runs one short
warm-up synthesis, because Piper's first call pays a multi-second one-time
initialization. The primary profile is PHOENIX's stock voice, declared
``original`` in the PHX-063 sense: a generic synthetic voice that imitates no
character or voice actor (ADR-109). Text and PCM are never logged or stored.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import threading
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

from phoenix.models.provider import CancellationResult, CancellationStatus
from phoenix.voice.engines.whisper import VoiceModelMissingError
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

__all__ = ("PRIMARY_PROFILE_ID", "PiperTtsProvider")

PRIMARY_PROFILE_ID: Final = "phoenix_primary"
_MAX_SECONDS: Final = 115.0  # TtsAudio admits at most 120 s
_MAX_BYTES: Final = 8_388_608
TTS_TIMEOUT_SECONDS: Final = 60.0

VoiceLoader = Callable[[Path], Any]


def _load_piper(model: Path) -> Any:
    module = importlib.import_module("piper")
    return module.PiperVoice.load(str(model), use_cuda=False)


class PiperTtsProvider(TtsProvider):
    """One loaded voice; one synthesis at a time in a worker thread."""

    def __init__(
        self,
        model_path: Path,
        provider_id: str = "piper_tts",
        *,
        voice_loader: VoiceLoader | None = None,
        timeout_seconds: float = TTS_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(provider_id)
        self._model_path = model_path
        self._loader = voice_loader or _load_piper
        self._timeout = timeout_seconds
        self._voice: Any = None
        self._rate = 0
        self._lock = threading.Lock()
        self._active: str | None = None
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def active_operation_ids(self) -> tuple[str, ...]:
        return () if self._active is None else (self._active,)

    @property
    def sample_rate_hz(self) -> int:
        return self._rate

    def load(self) -> None:
        """Blocking: load the voice and warm it up (never downloads)."""
        with self._lock:
            if self._voice is not None:
                return
            config = self._model_path.with_name(self._model_path.name + ".json")
            if not self._model_path.is_file() or not config.is_file():
                raise VoiceModelMissingError("voice model files are missing")
            voice = self._loader(self._model_path)
            rate = int(voice.config.sample_rate)
            if not 8000 <= rate <= 48000:
                raise VoiceModelMissingError("unsupported voice sample rate")
            for _ in voice.synthesize("Ready."):
                pass
            self._voice, self._rate = voice, rate

    def _synthesize(self, text: str) -> bytes:
        self.load()
        limit = min(_MAX_BYTES, int(self._rate * 2 * _MAX_SECONDS))
        parts: list[bytes] = []
        size = 0
        with self._lock:
            for chunk in self._voice.synthesize(text):
                data = bytes(chunk.audio_int16_bytes)
                size += len(data)
                if size > limit:
                    raise TtsProviderError(TtsProviderErrorCode.INVALID_INPUT)
                parts.append(data)
        return b"".join(parts)

    def _enter(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise TtsProviderError(TtsProviderErrorCode.WRONG_EVENT_LOOP)
        if self._closed:
            raise TtsProviderError(TtsProviderErrorCode.CLOSED)

    async def capabilities(self) -> TtsCapabilities:
        self._enter()
        if self._voice is None:
            try:
                await asyncio.to_thread(self.load)
            except Exception:
                raise TtsProviderError(TtsProviderErrorCode.UNAVAILABLE) from None
        return TtsCapabilities(
            provider_id=self.provider_id,
            voices=(
                TtsVoiceProfile(
                    profile_id=PRIMARY_PROFILE_ID,
                    display_name="PHOENIX",
                    provenance=VoiceProfileProvenance.ORIGINAL,
                    languages=("en",),
                    is_primary=True,
                ),
            ),
            sample_rate_hz=self._rate,
            channels=1,
            sample_width_bytes=2,
            encoding=TtsAudioEncoding.PCM_S16LE,
            supports_language_hint=True,
            supports_cancellation=False,
        )

    async def synthesize(self, request: TtsRequest) -> TtsAudio:
        capabilities = await self.capabilities()
        if type(request) is not TtsRequest:
            raise TtsProviderError(TtsProviderErrorCode.INVALID_INPUT)
        accepted = TtsRequest.model_validate(request)
        accepted.validate_capabilities(capabilities)
        if self._active is not None:
            raise TtsProviderError(TtsProviderErrorCode.BUSY)
        self._active = accepted.operation_id
        try:
            async with asyncio.timeout(self._timeout):
                pcm = await asyncio.to_thread(self._synthesize, accepted.text)
        except TimeoutError:
            raise TtsProviderError(TtsProviderErrorCode.TIMEOUT) from None
        except TtsProviderError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise TtsProviderError(TtsProviderErrorCode.UNAVAILABLE) from None
        finally:
            self._active = None
        if not pcm:
            raise TtsProviderError(TtsProviderErrorCode.INVALID_RESPONSE)
        return TtsAudio(
            operation_id=accepted.operation_id,
            provider_id=self.provider_id,
            voice_profile_id=accepted.voice_profile_id,
            sample_rate_hz=self._rate,
            channels=1,
            sample_width_bytes=2,
            encoding=TtsAudioEncoding.PCM_S16LE,
            pcm_base64=base64.b64encode(pcm).decode("ascii"),
            pcm_sha256=sha256(pcm).hexdigest(),
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
