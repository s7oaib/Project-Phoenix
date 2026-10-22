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

PHX-123 (ADR-184): that folder may be writable by other local users, so the
setup step records a SHA-256 manifest of every executable and library in the
Playwright build folders in a per-user location
(``%LOCALAPPDATA%\\PHOENIX\\browser-manifest.json``), and BrowserTool verifies
the folder against it before every launch; any difference (changed, missing or
extra file, other folder, no manifest) refuses the launch.
"""

import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

DEFAULT_BROWSERS_PATH = Path("D:/Phoenix/browsers")
OVERRIDE_ENV = "PHOENIX_BROWSERS_PATH"
PLAYWRIGHT_ENV = "PLAYWRIGHT_BROWSERS_PATH"
MANIFEST_VERSION = 1
MANIFEST_FILE = "browser-manifest.json"
_BUILD = re.compile(r"[a-z][a-z_]*-[0-9]+")  # chromium-1243, ffmpeg-1011, ...
_EXECUTABLE = (".exe", ".dll")
_CHUNK = 1 << 20


class BrowserNotTrusted(Exception):
    """The browser folder does not match the owner's recorded manifest."""


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


def manifest_path(environment: Mapping[str, str] | None = None) -> Path:
    """The per-user manifest location (never inside the browser folder)."""
    values = os.environ if environment is None else environment
    base = values.get("LOCALAPPDATA", "")
    if not base or not Path(base).is_absolute():
        raise ValueError("LOCALAPPDATA must be an absolute path")
    return Path(base) / "PHOENIX" / MANIFEST_FILE


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def browser_executables(path: Path) -> dict[str, str]:
    """SHA-256 of every .exe/.dll in the Playwright build folders, by relative path."""
    hashes: dict[str, str] = {}
    if not path.is_dir():
        return hashes
    for build in sorted(path.iterdir()):
        if not build.is_dir() or not _BUILD.fullmatch(build.name):
            continue
        for folder, _, names in os.walk(build, followlinks=False):
            for name in names:
                if name.lower().endswith(_EXECUTABLE):
                    file = Path(folder, name)
                    hashes[file.relative_to(path).as_posix()] = _sha256(file)
    return dict(sorted(hashes.items()))


def write_manifest(path: Path, manifest: Path) -> int:
    """Record the folder's executables (owner-run setup only); returns the count."""
    files = browser_executables(path)
    if not files:
        raise BrowserNotTrusted("No browser executables to record")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    partial = manifest.with_name(manifest.name + ".partial")
    partial.write_text(
        json.dumps(
            {"version": MANIFEST_VERSION, "browsers_path": str(path), "files": files},
            indent=1,
        ),
        encoding="utf-8",
    )
    os.replace(partial, manifest)
    return len(files)


def verify_browsers(path: Path, manifest: Path) -> None:
    """Raise BrowserNotTrusted unless the folder matches the manifest exactly."""
    try:
        recorded = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BrowserNotTrusted("No readable browser manifest") from None
    if (
        type(recorded) is not dict
        or recorded.get("version") != MANIFEST_VERSION
        or recorded.get("browsers_path") != str(path)
        or type(recorded.get("files")) is not dict
        or not recorded["files"]
    ):
        raise BrowserNotTrusted("Browser manifest is for another folder")
    try:
        actual = browser_executables(path)
    except OSError:
        raise BrowserNotTrusted("Browser folder is unreadable") from None
    if actual != recorded["files"]:
        raise BrowserNotTrusted("Browser executables changed since setup")


def verify_launch_browsers() -> None:
    """Verify the folder this process's Playwright launches from (ADR-184)."""
    try:
        chosen = os.environ.get(PLAYWRIGHT_ENV)
        path = Path(chosen) if chosen else browsers_path()
        manifest = manifest_path()
    except ValueError:
        raise BrowserNotTrusted("Browser folder or manifest location invalid") from None
    verify_browsers(path, manifest)


def use_phoenix_browsers(path: Path) -> None:
    """Point this process's Playwright at PHOENIX's folder (no download)."""
    if not path.is_absolute():
        raise ValueError("The PHOENIX browser folder must be an absolute path")
    os.environ[PLAYWRIGHT_ENV] = str(path)
