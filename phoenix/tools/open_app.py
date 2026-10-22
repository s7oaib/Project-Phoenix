"""PHX-133 ``desktop.open_app``: launch one allowlisted Windows app, no arguments.

The closed set is ``AppName`` (Notepad, Calculator, File Explorer). Each name
maps to one fixed executable under the Windows directory, resolved once when
the tool is built (``resolve_app_paths``) from the OS API, not PATH or the
environment, and kept only if the file exists. The tool takes an ``AppName``
only, never a path or argument; PermissionEngine decides every call (P1,
ADR-236) and only ALLOWED launches, via ``os.startfile(path)`` (ShellExecute
"open": no shell, no arguments). PHOENIX does not drive, read or close the
app. P1 writes no durable audit row (INV-007 covers P3/P4); the mission trace
records the call.

PHX-136 (ADR-250, ADR-252): under its own scope (``SHORTCUT_RESOURCE``) the
tool also launches an installed app from its Start-menu shortcut. The ``.lnk``
files under the all-users and current-user Start-menu ``Programs`` folders are
scanned once when the tool is built (``scan_shortcuts``); a request names a
shortcut of that index only, never a path, and the ``.lnk`` is started with
``os.startfile`` and no arguments. Uninstaller shortcuts are never indexed.
"""

import ctypes
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PureWindowsPath
from types import MappingProxyType

from phoenix.security.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionGrant,
    PermissionPolicy,
    PermissionRequest,
    PermissionStatus,
)
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResultStatus,
    ToolRiskLevel,
)

OPEN_APP_TOOL_ID = "desktop.open_app"
AppLauncher = Callable[[str], None]
# PHX-136 (ADR-250): the shortcut scope's resource; any indexed Start-menu app.
SHORTCUT_RESOURCE = "start-menu:installed-app-shortcuts"
MAX_SHORTCUTS = 2000


class AppName(StrEnum):
    NOTEPAD = "notepad"
    CALCULATOR = "calculator"
    FILE_EXPLORER = "explorer"


# Relative to the Windows directory; fixed, never user text.
_APP_FILES: Mapping[AppName, tuple[str, ...]] = MappingProxyType(
    {
        AppName.NOTEPAD: ("System32", "notepad.exe"),
        AppName.CALCULATOR: ("System32", "calc.exe"),
        AppName.FILE_EXPLORER: ("explorer.exe",),
    }
)


class OpenAppInputError(ValueError):
    """Not an allowlisted app request; nothing was asked or launched."""


def windows_directory() -> Path | None:
    """The Windows directory from the OS API (not %SystemRoot%), or None."""
    if sys.platform != "win32":
        return None
    buffer = ctypes.create_unicode_buffer(260)
    size = ctypes.windll.kernel32.GetSystemWindowsDirectoryW(buffer, len(buffer))
    if not 0 < size < len(buffer):
        return None
    return Path(buffer.value)


def resolve_app_paths(root: Path | None) -> Mapping[AppName, str]:
    """Absolute path per app under root, for the files that exist."""
    if root is None or not root.is_absolute():
        return MappingProxyType({})
    found = {
        name: str(root.joinpath(*parts))
        for name, parts in _APP_FILES.items()
        if root.joinpath(*parts).is_file()
    }
    return MappingProxyType(found)


def shortcut_key(name: str) -> str:
    """The index key for a shortcut or spoken app name: lower case, one space."""
    return " ".join(name.lower().split())


def start_menu_roots() -> tuple[Path, ...]:
    """The all-users and current-user Start-menu ``Programs`` folders."""
    if sys.platform != "win32":
        return ()
    tail = ("Microsoft", "Windows", "Start Menu", "Programs")
    bases = (os.environ.get("ProgramData"), os.environ.get("APPDATA"))
    return tuple(Path(base).joinpath(*tail) for base in bases if base)


def scan_shortcuts(roots: Sequence[Path]) -> Mapping[str, str]:
    """Shortcut key -> absolute ``.lnk`` path; the first root wins a name."""
    found: dict[str, str] = {}
    for root in roots:
        if not root.is_absolute() or not root.is_dir():
            continue
        try:
            links = sorted(root.rglob("*.lnk"))
        except OSError:
            continue
        for link in links:
            key = shortcut_key(link.stem)
            if key and "uninstall" not in key and link.is_file():
                found.setdefault(key, str(link))
            if len(found) >= MAX_SHORTCUTS:
                return MappingProxyType(found)
    return MappingProxyType(found)


def default_app_launcher(path: str) -> None:
    """Start the executable at path with no arguments; raises if the OS refuses."""
    if sys.platform != "win32":
        raise OSError("Apps open on Windows only")
    os.startfile(path)


def open_app_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            tool_id=OPEN_APP_TOOL_ID,
            description=(
                "Launch one allowlisted Windows app (Notepad, Calculator, File "
                "Explorer) by fixed system path, or (PHX-136) an installed app "
                "by its Start-menu shortcut, with no arguments; no app control."
            ),
            input_schema={
                "type": "object",
                "properties": {"app": {"enum": [name.value for name in AppName]}},
                "required": ["app"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"status": {"type": "string"}},
            },
            risk_level=ToolRiskLevel.P1,
            permission_requirement="allowlisted_app_open",
            timeout_seconds=5.0,
            cancellation_supported=False,
            audit_behavior="no mandatory durable audit",
            supported_platforms=("win32",),
            supported_environments=("local",),
        ),
    )


@dataclass(frozen=True)
class OpenAppResult:
    decision: PermissionDecision
    status: ToolResultStatus
    attempted: bool


class OpenAppTool:
    """Checks the app name, asks PermissionEngine, launches only when ALLOWED."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicy | None = None,
        launcher: AppLauncher = default_app_launcher,
        paths: Mapping[AppName, str] | None = None,
        shortcuts: Mapping[str, str] | None = None,
    ) -> None:
        registry.require(OPEN_APP_TOOL_ID)
        self._engine = PermissionEngine(registry, policy)
        self._launch = launcher
        resolved = resolve_app_paths(windows_directory()) if paths is None else paths
        if any(
            type(name) is not AppName
            or type(path) is not str
            or not PureWindowsPath(path).is_absolute()
            for name, path in resolved.items()
        ):
            raise ValueError("App paths must be absolute, by AppName")
        self._paths: Mapping[AppName, str] = MappingProxyType(dict(resolved))
        # PHX-136: the Start-menu index, scanned once here (startup).
        links = scan_shortcuts(start_menu_roots()) if shortcuts is None else shortcuts
        if any(
            type(key) is not str
            or key != shortcut_key(key)
            or not key
            or type(path) is not str
            or not PureWindowsPath(path).is_absolute()
            or PureWindowsPath(path).suffix.lower() != ".lnk"
            for key, path in links.items()
        ):
            raise ValueError("Shortcuts must be absolute .lnk paths by key")
        self._shortcuts: Mapping[str, str] = MappingProxyType(dict(links))

    @property
    def shortcut_names(self) -> frozenset[str]:
        """PHX-136: the indexed shortcut keys (names only, never paths)."""
        return frozenset(self._shortcuts)

    def open(
        self,
        app: AppName,
        request: PermissionRequest,
        grants: Sequence[PermissionGrant] = (),
    ) -> OpenAppResult:
        if type(app) is not AppName:
            raise OpenAppInputError("Unknown app")
        if request.tool_id != OPEN_APP_TOOL_ID:
            raise OpenAppInputError("Request is for another tool")
        if request.resource == SHORTCUT_RESOURCE:
            raise OpenAppInputError("A fixed app is not a shortcut request")
        decision = self._engine.evaluate(request, grants=grants)
        if decision.status != PermissionStatus.ALLOWED:
            return OpenAppResult(decision, ToolResultStatus.DENIED, attempted=False)
        path = self._paths.get(app)
        if path is None:  # not installed here: nothing to launch
            return OpenAppResult(decision, ToolResultStatus.FAILURE, attempted=False)
        try:
            self._launch(path)
        except Exception:
            return OpenAppResult(decision, ToolResultStatus.FAILURE, attempted=True)
        return OpenAppResult(decision, ToolResultStatus.SUCCESS, attempted=True)

    def open_shortcut(
        self,
        name: str,
        request: PermissionRequest,
        grants: Sequence[PermissionGrant] = (),
    ) -> OpenAppResult:
        """PHX-136: launch an indexed Start-menu shortcut, no arguments."""
        path = self._shortcuts.get(name) if type(name) is str else None
        if path is None:
            raise OpenAppInputError("Unknown shortcut")
        if request.tool_id != OPEN_APP_TOOL_ID:
            raise OpenAppInputError("Request is for another tool")
        if request.resource != SHORTCUT_RESOURCE:
            raise OpenAppInputError("Request is outside the shortcut scope")
        decision = self._engine.evaluate(request, grants=grants)
        if decision.status != PermissionStatus.ALLOWED:
            return OpenAppResult(decision, ToolResultStatus.DENIED, attempted=False)
        try:
            self._launch(path)
        except Exception:
            return OpenAppResult(decision, ToolResultStatus.FAILURE, attempted=True)
        return OpenAppResult(decision, ToolResultStatus.SUCCESS, attempted=True)
