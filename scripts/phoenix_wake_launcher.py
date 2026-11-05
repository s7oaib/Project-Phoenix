"""Always-on "Phoenix, Rise" listener (either word order): starts Ollama and the PHOENIX app by voice.

Local only. Audio is processed in memory and never written to disk or sent anywhere.
Only short voiced segments are transcribed (offline Whisper tiny.en on the CPU), so the
idle cost is a microphone stream and a little RMS arithmetic.

  python scripts/phoenix_wake_launcher.py            listen forever
  python scripts/phoenix_wake_launcher.py --selftest clip.wav   test the matcher, no mic
  python scripts/phoenix_wake_launcher.py --dry-run  listen but do not launch anything
"""

from __future__ import annotations

import argparse
import ctypes
import difflib
import queue
import re
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "apps/desktop/src-tauri/target/release/phoenix-desktop.exe"
OLLAMA = Path.home() / "AppData/Local/Programs/Ollama/ollama app.exe"
MODEL_DIR = Path(r"D:\Phoenix\models\voice\whisper\tiny.en")
LOG = Path.home() / ".phoenix" / "wake_launcher.log"
RATE = 16000
BLOCK = 1600  # 0.1 s
SPEECH_RMS = 0.012
END_SILENCE_BLOCKS = 6  # 0.6 s of quiet ends an utterance
MIN_BLOCKS, MAX_BLOCKS = 5, 40  # 0.5 s to 4 s
COOLDOWN_SECONDS = 25


def is_wake(text: str) -> bool:
    """True for "Phoenix, Rise" or "Rise, Phoenix" and the usual mishearings (rice phoenix, fenix rise)."""
    words = re.sub(r"[^a-z ]", " ", text.lower()).split()
    rise = any(difflib.SequenceMatcher(None, w, "rise").ratio() >= 0.7 for w in words)
    phoenix = any(
        difflib.SequenceMatcher(None, w, "phoenix").ratio() >= 0.6 for w in words
    )
    return rise and phoenix


def _running(image: str) -> bool:
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image}"], capture_output=True, text=True
    ).stdout
    return image.lower() in out.lower()


def launch(dry_run: bool) -> str:
    started = []
    if not _running("ollama.exe") and not _running("ollama app.exe") and OLLAMA.exists():
        started.append("ollama")
        if not dry_run:
            subprocess.Popen([str(OLLAMA)], creationflags=0x00000008)  # DETACHED_PROCESS
    if not _running("phoenix-desktop.exe"):
        started.append("phoenix")
        if not dry_run:
            subprocess.Popen([str(EXE)], cwd=str(EXE.parent), creationflags=0x00000008)
    return ", ".join(started) or "already running"


def show_fullscreen(title: str = "PHOENIX", wait: float = 40.0) -> bool:
    """Maximize the PHOENIX window and bring it to the front (waits for it to appear)."""
    user32 = ctypes.windll.user32  # Windows only, like the app
    deadline = time.time() + wait
    while time.time() < deadline:
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE: out of the taskbar if minimized
            user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            user32.keybd_event(0x12, 0, 0, 0)  # a tap of Alt lets a background
            user32.keybd_event(0x12, 0, 2, 0)  # process take the foreground
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            return True
        time.sleep(0.5)
    return False


def log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


def transcribe(model, samples: np.ndarray) -> str:
    segments, _ = model.transcribe(
        samples, language="en", beam_size=1, vad_filter=False, temperature=0.0
    )
    return " ".join(s.text for s in segments).strip()


def load_model():
    from faster_whisper import WhisperModel

    return WhisperModel(str(MODEL_DIR), device="cpu", compute_type="int8", local_files_only=True)


def selftest(path: str) -> int:
    with wave.open(path, "rb") as clip:
        raw = np.frombuffer(clip.readframes(clip.getnframes()), dtype=np.int16)
        rate, channels = clip.getframerate(), clip.getnchannels()
    audio = raw.reshape(-1, channels).mean(axis=1).astype(np.float32) / 32768.0
    if rate != RATE:  # linear resample is enough for a spoken phrase
        audio = np.interp(
            np.linspace(0, len(audio), int(len(audio) * RATE / rate), endpoint=False),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)
    text = transcribe(load_model(), audio)
    print(f"heard={text!r} wake={is_wake(text)}")
    return 0


def listen(dry_run: bool) -> int:
    import sounddevice as sd

    model = load_model()
    blocks: queue.Queue[np.ndarray] = queue.Queue()
    voiced: list[np.ndarray] = []
    quiet = 0
    last_launch = 0.0
    log("listening" + (" (dry run)" if dry_run else ""))
    with sd.InputStream(
        samplerate=RATE,
        channels=1,
        blocksize=BLOCK,
        dtype="float32",
        callback=lambda data, *_: blocks.put(data[:, 0].copy()),
    ):
        while True:
            block = blocks.get()
            loud = float(np.sqrt(np.mean(block**2))) >= SPEECH_RMS
            if loud:
                voiced.append(block)
                quiet = 0
            elif voiced:
                voiced.append(block)
                quiet += 1
            ended = quiet >= END_SILENCE_BLOCKS or len(voiced) >= MAX_BLOCKS
            if voiced and ended:
                clip, voiced, quiet = voiced, [], 0
                if len(clip) >= MIN_BLOCKS and time.time() - last_launch > COOLDOWN_SECONDS:
                    text = transcribe(model, np.concatenate(clip))
                    if is_wake(text):
                        last_launch = time.time()
                        log(f"wake heard -> {launch(dry_run)}")
                        if not dry_run:  # take over the screen, open or closed before
                            threading.Thread(target=show_fullscreen, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--selftest", metavar="WAV")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest(args.selftest)
    try:
        return listen(args.dry_run)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
