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
                "Explorer) by fixed system path, with no arguments; no app control."
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
