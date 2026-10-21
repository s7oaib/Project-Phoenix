"""PHX-112 (ADR-150): where PHOENIX keeps its own Playwright Chromium.

BrowserTool launches Playwright's bundled Chromium (never the owner's Chrome or
Edge) with a fresh in-memory browser context: an isolated profile with no
cookies, history, extensions or saved passwords from the owner's browsers, and
nothing written back to them. PHX-112 also keeps the browser binary in a
PHOENIX-owned folder rather than Playwright's per-user default, and installs
it only through the explicit, owner-run setup step
(``scripts/install_control_browser.py``); nothing here downloads anything.

The folder is ``D:\\Phoenix\\browsers`` by default (MASTER_SPEC: large data on
D:), overridable with ``PHOENIX_BROWSERS_PATH``. ``use_phoenix_browsers`` points
this process's Playwright at it; call it before the first session_open.
"""

import os
import sys
from collections.abc import Mapping
from pathlib import Path

DEFAULT_BROWSERS_PATH = Path("D:/Phoenix/browsers")
OVERRIDE_ENV = "PHOENIX_BROWSERS_PATH"
PLAYWRIGHT_ENV = "PLAYWRIGHT_BROWSERS_PATH"


def browsers_path(environment: Mapping[str, str] | None = None) -> Path:
    """The PHOENIX-owned browser folder (absolute), from the override or default."""
    values = os.environ if environment is None else environment
    chosen = values.get(OVERRIDE_ENV) or str(DEFAULT_BROWSERS_PATH)
    path = Path(chosen)
    if not path.is_absolute():
        raise ValueError("The PHOENIX browser folder must be an absolute path")
    return path


def chromium_installed(path: Path) -> bool:
    """Whether a Playwright Chromium build is present in the folder."""
    return path.is_dir() and any(path.glob("chromium-*"))


def install_command(path: Path) -> tuple[tuple[str, ...], dict[str, str]]:
    """The explicit setup command and its environment; the caller decides to run it."""
    environment = dict(os.environ)
    environment[PLAYWRIGHT_ENV] = str(path)
    return (sys.executable, "-m", "playwright", "install", "chromium"), environment


def use_phoenix_browsers(path: Path) -> None:
    """Point this process's Playwright at PHOENIX's folder (no download)."""
    if not path.is_absolute():
        raise ValueError("The PHOENIX browser folder must be an absolute path")
    os.environ[PLAYWRIGHT_ENV] = str(path)
