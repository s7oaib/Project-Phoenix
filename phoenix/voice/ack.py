"""Instant acknowledgement: Piper clips rendered once, cached on disk, replayed.

Never raises into the pipeline: a missing or failing TTS just means no clip.
"""

import asyncio
import hashlib
import os
import random
from pathlib import Path
from typing import Final
from uuid import uuid4

from phoenix.voice.conversation import AudioOutput, AudioPlaybackRequest
from phoenix.voice.persona import ACK_LINES, READY_LINE
from phoenix.voice.tts import TtsAudio, TtsProvider, TtsRequest

__all__ = ("CACHE_DIR_ENV", "AckClips", "default_cache_dir")

CACHE_DIR_ENV: Final = "PHOENIX_VOICE_CACHE_DIR"
PREPARE_SECONDS: Final = 20.0


def default_cache_dir() -> Path:
    configured = os.environ.get(CACHE_DIR_ENV, "").strip()
    return Path(configured) if configured else Path.home() / ".phoenix" / "voice_cache"


class AckClips:
    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir
        self._clips: list[TtsAudio] = []
        self._ready: list[TtsAudio] = []  # "Yes boss, tell me." after a spoken stop

    @property
    def count(self) -> int:
        return len(self._clips)

    async def prepare(self, tts: TtsProvider, voice_profile_id: str) -> None:
        """Load cached clips, render the missing ones. Call while TTS is idle."""
        try:
            async with asyncio.timeout(PREPARE_SECONDS):
                for text in ACK_LINES:
                    await self._one(tts, voice_profile_id, text)
                await self._one(tts, voice_profile_id, READY_LINE, self._ready)
        except Exception:
            pass  # includes timeout; whatever loaded stays usable

    async def _one(
        self,
        tts: TtsProvider,
        profile: str,
        text: str,
        target: list[TtsAudio] | None = None,
    ) -> None:
        bucket = self._clips if target is None else target
        key = hashlib.sha256(f"{profile}\0{text}".encode()).hexdigest()[:16]
        path = self._dir / f"ack-{key}.json"
        try:
            bucket.append(TtsAudio.model_validate_json(path.read_text("utf-8")))
            return
        except Exception:
            pass  # absent or corrupt: render again
        try:
            audio = await tts.synthesize(
                TtsRequest(
                    operation_id=str(uuid4()), text=text, voice_profile_id=profile
                )
            )
        except Exception:
            return
        bucket.append(audio)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(audio.model_dump_json(), "utf-8")
        except OSError:
            pass  # cache is best effort

    async def play(self, output: AudioOutput, session_id: str, turn_id: str) -> bool:
        """Play one random clip; False when none or playback failed."""
        if not self._clips:
            return False
        try:
            await output.play(
                AudioPlaybackRequest(
                    playback_id=str(uuid4()),
                    session_id=session_id,
                    turn_id=turn_id,
                    audio=random.choice(self._clips),
                )
            )
        except Exception:
            return False  # CancelledError is BaseException and propagates
        return True

    async def play_ready(
        self, output: AudioOutput, session_id: str, turn_id: str
    ) -> bool:
        """Say "Yes boss, tell me." once PHOENIX has stopped and is listening."""
        if not self._ready:
            return False
        try:
            await output.play(
                AudioPlaybackRequest(
                    playback_id=str(uuid4()),
                    session_id=session_id,
                    turn_id=turn_id,
                    audio=self._ready[0],
                )
            )
        except Exception:
            return False
        return True
