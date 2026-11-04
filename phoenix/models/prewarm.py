"""Backend-start pre-warm: Ollama model, Piper voice, Whisper. Fail soft, timed.

Runs in daemon threads so startup never waits. The voice stage loads then drops
the engines: it pays import, disk-cache and runtime-init cost once so the real
load on voice start is fast. Logs stage names and milliseconds only.
"""

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Final

import httpx

__all__ = ("PREWARM_MODEL", "prewarm_ollama", "prewarm_voice", "start_prewarm")

log = logging.getLogger("phoenix.models.prewarm")
PREWARM_MODEL: Final = "qwen3.5:4b"
KEEP_ALIVE: Final = "30m"
OLLAMA_URL: Final = "http://127.0.0.1:11434"
_DISABLE_ENV: Final = "PHOENIX_PREWARM"


def _timed(stage: str, work: Callable[[], object]) -> bool:
    start = time.monotonic()
    try:
        work()
        ok = True
    except Exception:
        ok = False
    log.info(
        "prewarm stage=%s ok=%d ms=%d", stage, ok, (time.monotonic() - start) * 1000
    )
    return ok


def prewarm_ollama(base_url: str = OLLAMA_URL, model: str = PREWARM_MODEL) -> bool:
    """Empty-prompt generate loads the model and pins it for 30 minutes."""

    def load() -> None:
        body = {"model": model, "keep_alive": KEEP_ALIVE, "stream": False}
        httpx.post(
            f"{base_url}/api/generate", json=body, timeout=180, trust_env=False
        ).raise_for_status()

    return _timed("ollama", load)


def prewarm_voice(models_dir: Path | None = None) -> None:
    from phoenix.voice.engines.models import (
        PIPER_VOICE,
        WHISPER_MODELS,
        default_models_dir,
        missing_files,
        selected_stt_model,
    )
    from phoenix.voice.engines.piper import PiperTtsProvider
    from phoenix.voice.engines.whisper import WhisperEngine

    root = models_dir or default_models_dir()
    stt = WHISPER_MODELS[selected_stt_model()]
    if not missing_files(root, PIPER_VOICE):
        model = PIPER_VOICE.path(root) / PIPER_VOICE.files[0].relative_path
        _timed("piper", PiperTtsProvider(model).load)
    if not missing_files(root, stt):
        _timed(
            "whisper",
            WhisperEngine(stt.path(root), cpu_threads=8, beam_size=5).load,
        )


def start_prewarm() -> list[threading.Thread]:
    """Start both stages; off with PHOENIX_PREWARM=0 and under pytest."""
    if os.environ.get(_DISABLE_ENV) == "0" or "PYTEST_CURRENT_TEST" in os.environ:
        return []
    threads = [
        threading.Thread(target=prewarm_ollama, name="prewarm-ollama", daemon=True),
        threading.Thread(target=prewarm_voice, name="prewarm-voice", daemon=True),
    ]
    for thread in threads:
        thread.start()
    return threads
