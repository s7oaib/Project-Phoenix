"""Microphone capture and speaker playback through sounddevice (PHX-114, ADR-107).

The microphone delivers 30 ms mono 16 kHz S16LE ``PcmAudioFrame`` snapshots
into a bounded queue (oldest dropped when full, counted). It opens the default
input device only when the pipeline starts, which happens only on an explicit
request (``POST /voice/start`` or ``PHOENIX_VOICE_AUTOSTART=1``). Playback writes
Piper PCM to the default output device in short blocks so an interruption
(barge-in) stops it within one block. When a device rejects the native rate,
audio is resampled linearly to the device's default rate. Audio is never
written to disk or logged.
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import threading
from abc import ABC, abstractmethod
from collections import deque
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Final
from uuid import uuid4

from phoenix.models.provider import CancellationResult, CancellationStatus
from phoenix.voice.audio import AudioEncoding, PcmAudioFrame
from phoenix.voice.conversation import (
    AudioOutput,
    AudioOutputError,
    AudioOutputErrorCode,
    AudioPlaybackRequest,
    AudioPlaybackResult,
    AudioPlaybackStatus,
)

__all__ = (
    "FRAME_BYTES",
    "FRAME_SAMPLES",
    "SAMPLE_RATE",
    "MicrophoneError",
    "MicrophoneSource",
    "SoundDeviceMicrophone",
    "SoundDeviceAudioOutput",
    "pcm_frame",
    "resample_pcm",
)

SAMPLE_RATE: Final = 16000
FRAME_SAMPLES: Final = 480  # 30 ms, a PHX-061 VAD frame
FRAME_BYTES: Final = FRAME_SAMPLES * 2
_QUEUE_FRAMES: Final = 200  # 6 s
_PLAY_BLOCK_SECONDS: Final = 0.05


class MicrophoneError(RuntimeError):
    """The microphone could not be opened or stopped delivering audio."""


def pcm_frame(stream_id: str, sequence: int, pcm: bytes) -> PcmAudioFrame:
    return PcmAudioFrame(
        stream_id=stream_id,
        sequence=sequence,
        captured_at=datetime.now(UTC),
        sample_rate_hz=SAMPLE_RATE,
        channels=1,
        sample_width_bytes=2,
        encoding=AudioEncoding.PCM_S16LE,
        pcm_base64=base64.b64encode(pcm).decode("ascii"),
        pcm_sha256=sha256(pcm).hexdigest(),
    )


def resample_pcm(pcm: bytes, source_rate: int, target_rate: int) -> bytes:
    """Linear-interpolation resampling of mono S16LE (adequate for speech)."""
    if source_rate == target_rate or not pcm:
        return pcm
    numpy = importlib.import_module("numpy")
    samples = numpy.frombuffer(pcm, dtype="<i2").astype(numpy.float32)
    count = max(1, round(len(samples) * target_rate / source_rate))
    positions = numpy.linspace(0, len(samples) - 1, count)
    resampled = numpy.interp(positions, numpy.arange(len(samples)), samples)
    clipped = numpy.clip(numpy.rint(resampled), -32768, 32767).astype("<i2")
    return bytes(clipped.tobytes())


class MicrophoneSource(ABC):
    """An async source of consecutive 30 ms frames from one stream."""

    @abstractmethod
    async def start(self) -> None:
        """Open the device; raise MicrophoneError if it cannot be opened."""

    @abstractmethod
    async def read(self) -> PcmAudioFrame:
        """The next frame, in order; raise MicrophoneError on device failure."""

    @abstractmethod
    def drain(self) -> int:
        """Discard queued frames (our own speech echo); returns how many."""

    @abstractmethod
    async def aclose(self) -> None:
        """Close the device; idempotent."""


class SoundDeviceMicrophone(MicrophoneSource):
    def __init__(self, device: int | str | None = None) -> None:
        self._device = device
        self._stream: Any = None
        self._queue: deque[PcmAudioFrame] = deque(maxlen=_QUEUE_FRAMES)
        self._ready = asyncio.Event()
        self._pending = bytearray()
        self._rate = SAMPLE_RATE
        self._stream_id = str(uuid4())
        self._sequence = 0
        self._dropped = 0
        self._failed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def dropped_frames(self) -> int:
        return self._dropped

    def _callback(self, indata: Any, frames: int, time: Any, status: Any) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._push, bytes(indata))

    def _push(self, data: bytes) -> None:
        if self._rate != SAMPLE_RATE:
            data = resample_pcm(data, self._rate, SAMPLE_RATE)
        self._pending.extend(data)
        while len(self._pending) >= FRAME_BYTES:
            chunk = bytes(self._pending[:FRAME_BYTES])
            del self._pending[:FRAME_BYTES]
            if len(self._queue) == self._queue.maxlen:
                self._dropped += 1
            self._queue.append(pcm_frame(self._stream_id, self._sequence, chunk))
            self._sequence = (self._sequence + 1) % 2147483647
        if self._queue:
            self._ready.set()

    async def start(self) -> None:
        if self._stream is not None:
            return
        self._loop = asyncio.get_running_loop()
        try:
            sounddevice = importlib.import_module("sounddevice")
        except Exception:
            raise MicrophoneError("sounddevice is unavailable") from None

        def open_stream(rate: int) -> Any:
            stream = sounddevice.RawInputStream(
                samplerate=rate,
                channels=1,
                dtype="int16",
                blocksize=round(rate * 0.03),
                device=self._device,
                callback=self._callback,
            )
            stream.start()
            return stream

        try:
            self._stream = await asyncio.to_thread(open_stream, SAMPLE_RATE)
        except Exception:
            try:
                info = sounddevice.query_devices(self._device, "input")
                self._rate = int(info["default_samplerate"])
                self._stream = await asyncio.to_thread(open_stream, self._rate)
            except Exception:
                raise MicrophoneError("microphone unavailable") from None

    async def read(self) -> PcmAudioFrame:
        while not self._queue:
            if self._failed or self._stream is None:
                raise MicrophoneError("microphone closed")
            self._ready.clear()
            try:
                async with asyncio.timeout(2.0):
                    await self._ready.wait()
            except TimeoutError:
                # A live input stream delivers a block every 30 ms.
                self._failed = True
                raise MicrophoneError("microphone stopped delivering audio") from None
        return self._queue.popleft()

    def drain(self) -> int:
        count = len(self._queue)
        self._queue.clear()
        return count

    async def aclose(self) -> None:
        stream, self._stream = self._stream, None
        self._queue.clear()
        if stream is not None:
            try:
                await asyncio.to_thread(stream.close)
            except Exception:
                pass


class SoundDeviceAudioOutput(AudioOutput):
    """Default output device; one playback at a time, interruptible per block."""

    def __init__(
        self, output_id: str = "sounddevice_output", device: int | str | None = None
    ) -> None:
        super().__init__(output_id)
        self._device = device
        self._active: str | None = None
        self._stop = threading.Event()
        self._closed = False

    @property
    def active_playback_ids(self) -> tuple[str, ...]:
        return () if self._active is None else (self._active,)

    def _play_blocking(self, pcm: bytes, rate: int) -> AudioPlaybackStatus:
        sounddevice = importlib.import_module("sounddevice")
        try:
            stream = sounddevice.RawOutputStream(
                samplerate=rate, channels=1, dtype="int16", device=self._device
            )
        except Exception:
            info = sounddevice.query_devices(self._device, "output")
            target = int(info["default_samplerate"])
            pcm = resample_pcm(pcm, rate, target)
            rate = target
            stream = sounddevice.RawOutputStream(
                samplerate=rate, channels=1, dtype="int16", device=self._device
            )
        block = max(2, round(rate * _PLAY_BLOCK_SECONDS) * 2)
        with stream:
            for start in range(0, len(pcm), block):
                if self._stop.is_set():
                    stream.abort()
                    return AudioPlaybackStatus.INTERRUPTED
                stream.write(pcm[start : start + block])
        return AudioPlaybackStatus.COMPLETED

    async def play(self, request: AudioPlaybackRequest) -> AudioPlaybackResult:
        if self._closed:
            raise AudioOutputError(AudioOutputErrorCode.CLOSED)
        if type(request) is not AudioPlaybackRequest:
            raise AudioOutputError(AudioOutputErrorCode.INVALID_INPUT)
        accepted = AudioPlaybackRequest.model_validate(request)
        if self._active is not None:
            raise AudioOutputError(AudioOutputErrorCode.BUSY)
        pcm = accepted.audio.pcm_bytes_for_playback()
        self._active = accepted.playback_id
        self._stop.clear()
        try:
            status = await asyncio.to_thread(
                self._play_blocking, pcm, accepted.audio.sample_rate_hz
            )
        except asyncio.CancelledError:
            self._stop.set()
            raise
        except Exception:
            raise AudioOutputError(AudioOutputErrorCode.UNAVAILABLE) from None
        finally:
            self._active = None
        return AudioPlaybackResult(
            playback_id=accepted.playback_id, output_id=self.output_id, status=status
        )

    async def interrupt(self, playback_id: str) -> CancellationResult:
        found = self._active is not None and self._active == playback_id
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
        self._closed = True
        self._stop.set()
