"""PHX-114 voice model setup: the explicit, owner-run download step (ADR-109).

PHOENIX never downloads voice models by itself. This script fetches the pinned
files listed in ``phoenix/voice/engines/models.py`` (Hugging Face commits, exact
sizes and SHA-256 digests) into the voice models directory, verifies every
file, and replaces nothing that is already correct.

    uv run python scripts/voice_setup.py            # wake + STT + Piper voice
    uv run python scripts/voice_setup.py --check    # verify only, no network
    uv run python scripts/voice_setup.py --stt small.en --models-dir D:\\x

Exit status: 0 when every requested file is present and verified, 1 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

from phoenix.voice.engines.models import (
    DEFAULT_WAKE_MODEL,
    PIPER_VOICE,
    WHISPER_MODELS,
    ModelFile,
    VoiceModel,
    default_models_dir,
    selected_stt_model,
)

_CHUNK = 1 << 20
_TIMEOUT_SECONDS = 60


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _verified(path: Path, file: ModelFile) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == file.size_bytes
        and _digest(path) == file.sha256
    )


def _download(file: ModelFile, target: Path) -> None:
    if not file.url.startswith("https://huggingface.co/"):
        raise ValueError("model URLs must be pinned Hugging Face URLs")
    partial = target.with_name(target.name + ".partial")
    request = urllib.request.Request(
        file.url, headers={"User-Agent": "phoenix-voice-setup"}
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310
        with partial.open("wb") as handle:
            written = 0
            while chunk := response.read(_CHUNK):
                written += len(chunk)
                if written > file.size_bytes:
                    # PHX-123 (ADR-188): never write past the pinned size.
                    handle.close()
                    partial.unlink()
                    raise ValueError(f"{file.relative_path}: larger than pinned size")
                handle.write(chunk)
    if partial.stat().st_size != file.size_bytes or _digest(partial) != file.sha256:
        partial.unlink()
        raise ValueError(f"{file.relative_path}: size or SHA-256 mismatch")
    os.replace(partial, target)


def ensure(model: VoiceModel, root: Path, *, check_only: bool) -> bool:
    ok = True
    directory = model.path(root)
    for file in model.files:
        target = directory / file.relative_path
        if _verified(target, file):
            print(f"ok       {model.directory}/{file.relative_path}")
            continue
        if check_only:
            print(f"missing  {model.directory}/{file.relative_path}")
            ok = False
            continue
        directory.mkdir(parents=True, exist_ok=True)
        size = f"{file.size_bytes:,} bytes"
        print(f"fetching {model.directory}/{file.relative_path} ({size})")
        try:
            _download(file, target)
        except (OSError, ValueError) as error:
            if _verified(target, file):  # another setup run finished it first
                print(f"ok       {model.directory}/{file.relative_path}")
                continue
            print(f"FAILED   {model.directory}/{file.relative_path}: {error}")
            ok = False
            continue
        print(f"ok       {model.directory}/{file.relative_path}")
    return ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models-dir", type=Path, default=None)
    parser.add_argument("--stt", choices=sorted(WHISPER_MODELS), default=None)
    parser.add_argument(
        "--all-whisper",
        action="store_true",
        help="also fetch every pinned Whisper size",
    )
    parser.add_argument("--check", action="store_true", help="verify only")
    args = parser.parse_args(argv)
    root: Path = args.models_dir or default_models_dir()
    stt = args.stt or selected_stt_model()
    names = (
        sorted(WHISPER_MODELS)
        if args.all_whisper
        else sorted({DEFAULT_WAKE_MODEL, stt})
    )
    print(f"voice models directory: {root}")
    results = [
        ensure(WHISPER_MODELS[name], root, check_only=args.check) for name in names
    ]
    results.append(ensure(PIPER_VOICE, root, check_only=args.check))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
