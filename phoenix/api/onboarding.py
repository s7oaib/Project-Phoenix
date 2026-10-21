"""PHX-108 first-run setup checks (ADR-110, ADR-113).

``GET /onboarding`` reports, in the order the user must fix them:

1. the local model runtime: ``running`` (the provider's health probe answers
   healthy), ``degraded`` (it answers but not correctly), ``not_running``
   (installed but not answering) or ``not_detected`` (not answering and not
   found in the usual install locations);
2. the tier models: each with whether PHOENIX has registered it and whether it
   is required (``REQUIRED_MODELS``: the fast and smart tiers);
3. storage: PHOENIX's data directory, free space and whether the database is
   at the migration head;
4. the document workspace folder.

Setup is ``complete`` when the runtime is running, every required model is
registered, the database is at head and a workspace folder is active. Nothing
is persisted: the desktop shows the setup screen whenever setup is
incomplete, and the user may continue without it (text chat works without a
workspace, INV-015).

Detection only looks for the runtime's executable; it never starts, installs
or downloads anything. The install hint is fixed text built from the model ID
(already validated by the tier table), never from provider output.
"""

import os
import shutil
from pathlib import Path
from typing import Literal

from phoenix.models.provider import ProviderHealthStatus

# The fast (T1) and smart (T2) tiers; PHOENIX's own answers, triage, planning,
# assignment and verification run on them (``default_tier_table``).
REQUIRED_MODELS: tuple[tuple[str, str], ...] = (
    ("ollama", "qwen3.5:4b"),
    ("ollama", "qwen3.5:9b"),
)

RuntimeState = Literal["running", "degraded", "not_running", "not_detected"]

_DISPLAY_NAMES = {"ollama": "Ollama", "colibri": "Colibri"}


def display_name(provider_id: str) -> str:
    return _DISPLAY_NAMES.get(provider_id, provider_id)


def install_command(provider_id: str, model_id: str) -> str | None:
    """The command the user runs to install a missing model, if known."""
    if provider_id == "ollama":
        return f"ollama pull {model_id}"
    return None


def _ollama_installed() -> bool:
    if shutil.which("ollama") is not None:
        return True
    local = os.environ.get("LOCALAPPDATA")
    candidates = [Path(local) / "Programs" / "Ollama" / "ollama.exe"] if local else []
    return any(candidate.is_file() for candidate in candidates)


def runtime_installed(provider_id: str) -> bool:
    """Whether the provider's runtime executable is found (Ollama only)."""
    try:
        return provider_id == "ollama" and _ollama_installed()
    except OSError:
        return False


def runtime_state(provider_id: str, health: ProviderHealthStatus) -> RuntimeState:
    if health == ProviderHealthStatus.HEALTHY:
        return "running"
    if health == ProviderHealthStatus.DEGRADED:
        return "degraded"
    return "not_running" if runtime_installed(provider_id) else "not_detected"


def free_bytes(directory: Path) -> int | None:
    try:
        return shutil.disk_usage(directory).free
    except OSError:
        return None
