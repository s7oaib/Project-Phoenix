"""Pinned voice model files and where they live (PHX-114, ADR-109).

The manifest pins each file to a Hugging Face commit and a SHA-256 digest.
``scripts/voice_setup.py`` downloads and verifies them; the backend only reads
them and never downloads (no network at runtime). Model directory, in order:
``PHOENIX_VOICE_MODELS_DIR``; ``D:\\Phoenix\\models\\voice`` when it exists
(BLAZE-6 keeps large files on D:); else ``~/.phoenix/models/voice``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = (
    "ModelFile",
    "VoiceModel",
    "WHISPER_MODELS",
    "PIPER_VOICE",
    "DEFAULT_WAKE_MODEL",
    "DEFAULT_STT_MODEL",
    "MODELS_DIR_ENV",
    "STT_MODEL_ENV",
    "default_models_dir",
    "selected_stt_model",
    "missing_files",
)

MODELS_DIR_ENV: Final = "PHOENIX_VOICE_MODELS_DIR"
STT_MODEL_ENV: Final = "PHOENIX_VOICE_STT_MODEL"
_BLAZE6_DIR: Final = Path("D:/Phoenix/models/voice")


@dataclass(frozen=True)
class ModelFile:
    relative_path: str  # under the model's own directory
    url: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class VoiceModel:
    model_id: str
    directory: str  # relative to the voice models directory
    files: tuple[ModelFile, ...]

    def path(self, root: Path) -> Path:
        return root / self.directory


def _whisper(
    name: str, revision: str, sizes: dict[str, int], hashes: dict[str, str]
) -> VoiceModel:
    base = f"https://huggingface.co/Systran/faster-whisper-{name}/resolve/{revision}/"
    return VoiceModel(
        model_id=name,
        directory=f"whisper/{name}",
        files=tuple(
            ModelFile(file, base + file, sizes[file], hashes[file])
            for file in ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
        ),
    )


_TOKENIZER = 2128466, "929c5252409436dce1b38a75d1abbcb5e132d170d8e324e4e04ed915fa2d22df"
_VOCABULARY = 422309, "ff77588746d3a2595d32ab5b69ffd7b95ce2441ac57533cb66fc3eb575a115cf"


def _sizes(config: int, model: int) -> dict[str, int]:
    return {
        "config.json": config,
        "model.bin": model,
        "tokenizer.json": _TOKENIZER[0],
        "vocabulary.txt": _VOCABULARY[0],
    }


def _hashes(config: str, model: str) -> dict[str, str]:
    return {
        "config.json": config,
        "model.bin": model,
        "tokenizer.json": _TOKENIZER[1],
        "vocabulary.txt": _VOCABULARY[1],
    }


# MIT-licensed CTranslate2 conversions of OpenAI Whisper (English-only).
WHISPER_MODELS: Final[dict[str, VoiceModel]] = {
    "tiny.en": _whisper(
        "tiny.en",
        "0d3d19a32d3338f10357c0889762bd8d64bbdeba",
        _sizes(2317, 75537502),
        _hashes(
            "14b1b421a90349bc551b881461426b561a874049cb9e4c4864f2ca384f6a7cc5",
            "1a5afae06a4db91c975c9a9d78be5cc110ee4ea022ad57d55492e4550e936b2a",
        ),
    ),
    "base.en": _whisper(
        "base.en",
        "3d3d5dee26484f91867d81cb899cfcf72b96be6c",
        _sizes(2227, 145216508),
        _hashes(
            "f3bc3821e9fc76a27bae538e11ae5b677dcdd352b4600429ce7951d398569aeb",
            "2a166925539a16005f14ff328359f9b9adb9dc4fb631bb3b227526862e93e2ef",
        ),
    ),
    "small.en": _whisper(
        "small.en",
        "d1d751a5f8271d482d14ca55d9e2deeebbae577f",
        _sizes(2657, 483545366),
        _hashes(
            "666a9605530ac1f61fa8177f3702b4dacec9966749e42610839fcc32661d5fae",
            "62b2a45b05ee59acb4a5341b33ee35e041395d378d418a18acfe4c9e768ee37a",
        ),
    ),
}

_PIPER_BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/"
    "5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/en/en_US/norman/medium/"
)
# Piper "norman" (medium): US English male, trained from scratch on public
# domain LibriVox recordings (ADR-109).
PIPER_VOICE: Final = VoiceModel(
    model_id="en_US-norman-medium",
    directory="piper",
    files=(
        ModelFile(
            "en_US-norman-medium.onnx",
            _PIPER_BASE + "en_US-norman-medium.onnx",
            63531379,
            "b9739443232a80a59c7d18810dd856899bf16a7964725f5ab81ea49b1351cb71",
        ),
        ModelFile(
            "en_US-norman-medium.onnx.json",
            _PIPER_BASE + "en_US-norman-medium.onnx.json",
            4968,
            "6c2db7f558a4a8deb9fe822583c1c5105f6c4e834dd0f9de8ad17a888ee9fe1d",
        ),
    ),
)

DEFAULT_WAKE_MODEL: Final = "tiny.en"
DEFAULT_STT_MODEL: Final = "base.en"


def default_models_dir() -> Path:
    configured = os.environ.get(MODELS_DIR_ENV, "").strip()
    if configured:
        return Path(configured)
    if os.name == "nt" and _BLAZE6_DIR.is_dir():
        return _BLAZE6_DIR
    return Path.home() / ".phoenix" / "models" / "voice"


def selected_stt_model() -> str:
    """The STT model: ``PHOENIX_VOICE_STT_MODEL`` when it names a pinned model."""
    configured = os.environ.get(STT_MODEL_ENV, "").strip()
    return configured if configured in WHISPER_MODELS else DEFAULT_STT_MODEL


def missing_files(root: Path, model: VoiceModel) -> tuple[str, ...]:
    """Relative paths that are absent or have the wrong size (cheap check only;
    the setup script verifies digests)."""
    missing = []
    for file in model.files:
        path = model.path(root) / file.relative_path
        try:
            if not path.is_file() or path.stat().st_size != file.size_bytes:
                missing.append(f"{model.directory}/{file.relative_path}")
        except OSError:
            missing.append(f"{model.directory}/{file.relative_path}")
    return tuple(missing)
