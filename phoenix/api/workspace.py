"""PHX-104 document workspace: validation, storage and runtime wiring (ADR-089).

The workspace root is the one directory PHX-046 document steps may read (through
FilesystemTool, ToolRegistry and PermissionEngine). It is the only setting the
user can write in v1 (``PATCH /settings``).

Validation (``validate_workspace_root``) accepts only an existing, canonical,
local directory, checked in this order (each failure has its own code):

1. ``workspace_invalid``: a string of 1-4096 characters without control
   characters, absolute (on Windows: drive letter and root), with no ``..``
   segment, and not UNC or a device path (``\\\\server\\share``, ``\\\\?\\``,
   ``\\\\.\\``, ``//``). This is decided from the text alone, so a network path
   is never touched.
2. ``workspace_not_found`` / ``workspace_not_directory``.
3. ``workspace_link``: the directory is a symlink, junction or other reparse
   point, or its real path differs from the given one (a link, junction,
   ``subst`` drive or 8.3 short name somewhere in the chain). FilesystemTool
   refuses links inside the workspace; this applies the same rule to the root.
4. ``workspace_drive_root``: a filesystem or drive root (far too broad).
5. ``workspace_system_directory``: the operating system or installed programs
   (Windows: ``%SystemRoot%``, Program Files, ``%ProgramData%``; POSIX:
   ``/bin``, ``/boot``, ``/dev``, ``/etc``, ``/lib``, ``/proc``, ``/run``,
   ``/sbin``, ``/sys``, ``/usr``, ``/var``), or anything inside them.
6. ``workspace_data_directory``: overlaps PHOENIX's own data directory (the
   database and migration lock), either way round.

Path checks are not a race-proof sandbox (see FilesystemTool); they decide what
the user may choose, and FilesystemTool still checks every read.

Changing the workspace builds a new FilesystemTool and runtime; missions that
start afterwards use them, and a running mission keeps its own
(``MissionService.use_runtime``). Grants are never touched: they match an exact
absolute resource, and every resource a document step can request lies inside
the current root, so a grant for a path outside it can never apply.
"""

import asyncio
import os
import stat
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from phoenix.agents.runtime import PhoenixRuntime
from phoenix.api.missions import MissionService
from phoenix.persistence.settings import SettingsStore

MAX_WORKSPACE_CHARS = 4096
_WINDOWS_SYSTEM_VARIABLES = (
    "SystemRoot",
    "windir",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "ProgramData",
)
_POSIX_SYSTEM_DIRECTORIES = (
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib64",
    "/proc",
    "/run",
    "/sbin",
    "/sys",
    "/usr",
    "/var",
)


class WorkspaceErrorCode(StrEnum):
    INVALID = "workspace_invalid"
    NOT_FOUND = "workspace_not_found"
    NOT_DIRECTORY = "workspace_not_directory"
    LINK = "workspace_link"
    DRIVE_ROOT = "workspace_drive_root"
    SYSTEM_DIRECTORY = "workspace_system_directory"
    DATA_DIRECTORY = "workspace_data_directory"


class WorkspaceError(ValueError):
    """A refused workspace root; the message never contains the path."""

    def __init__(self, code: WorkspaceErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class WorkspaceStatus(StrEnum):
    UNSET = "unset"
    ACTIVE = "active"
    UNAVAILABLE = "unavailable"  # stored, but no longer valid at startup


@dataclass(frozen=True)
class WorkspaceState:
    root: str | None
    status: WorkspaceStatus
    updated_at: datetime | None


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _inside(path: str, parent: str) -> bool:
    child, base = _key(path), _key(parent)
    try:
        return os.path.commonpath((child, base)) == base
    except ValueError:  # different drives
        return False


def _reparse(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def system_directories(
    environ: Mapping[str, str] | None = None, platform: str = sys.platform
) -> tuple[str, ...]:
    if platform != "win32":
        return _POSIX_SYSTEM_DIRECTORIES
    source = os.environ if environ is None else environ
    found = (source.get(name, "") for name in _WINDOWS_SYSTEM_VARIABLES)
    return tuple(dict.fromkeys(value for value in found if os.path.isabs(value)))


def _checked_text(raw: object) -> str:
    if type(raw) is not str or not 1 <= len(raw) <= MAX_WORKSPACE_CHARS:
        raise WorkspaceError(WorkspaceErrorCode.INVALID)
    if any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise WorkspaceError(WorkspaceErrorCode.INVALID)
    if raw[:2] in ("\\\\", "//", "\\/", "/\\"):  # UNC or device path
        raise WorkspaceError(WorkspaceErrorCode.INVALID)
    if not Path(raw).is_absolute():
        raise WorkspaceError(WorkspaceErrorCode.INVALID)
    if ".." in raw.replace("\\", "/").split("/"):
        raise WorkspaceError(WorkspaceErrorCode.INVALID)
    return os.path.normpath(raw)


def validate_workspace_root(
    raw: object, *, data_dir: Path, system: tuple[str, ...] | None = None
) -> str:
    """Return the normalized absolute root, or raise WorkspaceError."""
    path = _checked_text(raw)
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise WorkspaceError(WorkspaceErrorCode.NOT_FOUND) from None
    except (OSError, ValueError):
        raise WorkspaceError(WorkspaceErrorCode.INVALID) from None
    if _reparse(info):
        raise WorkspaceError(WorkspaceErrorCode.LINK)
    if not stat.S_ISDIR(info.st_mode):
        raise WorkspaceError(WorkspaceErrorCode.NOT_DIRECTORY)
    try:
        real = os.path.realpath(path, strict=True)
    except (OSError, ValueError):
        raise WorkspaceError(WorkspaceErrorCode.LINK) from None
    if _key(real) != _key(path):
        raise WorkspaceError(WorkspaceErrorCode.LINK)
    if Path(path).parent == Path(path):
        raise WorkspaceError(WorkspaceErrorCode.DRIVE_ROOT)
    for directory in system_directories() if system is None else system:
        if _inside(path, directory):
            raise WorkspaceError(WorkspaceErrorCode.SYSTEM_DIRECTORY)
    data = os.path.abspath(data_dir)
    if _inside(path, data) or _inside(data, path):
        raise WorkspaceError(WorkspaceErrorCode.DATA_DIRECTORY)
    return path


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkspaceService:
    """The stored workspace root and the runtime built for it."""

    def __init__(
        self,
        *,
        store: SettingsStore,
        missions: MissionService,
        build_runtime: Callable[[str], PhoenixRuntime],
        data_dir: Path,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._store = store
        self._missions = missions
        self._build = build_runtime
        self._data_dir = data_dir
        self._clock = clock
        self._lock = asyncio.Lock()
        self._state = WorkspaceState(None, WorkspaceStatus.UNSET, None)

    @property
    def state(self) -> WorkspaceState:
        return self._state

    def _validate(self, raw: object) -> str:
        return validate_workspace_root(raw, data_dir=self._data_dir)

    async def load(self) -> WorkspaceState:
        """At startup: re-validate the stored root and use it if still valid.

        A stored root that is no longer valid is reported ``unavailable`` and
        not used; document steps stay off until the user sets a valid one.
        """
        async with self._lock:
            stored = await asyncio.to_thread(self._store.get_workspace_root)
            if stored is None:
                return self._state
            try:
                root = await asyncio.to_thread(self._validate, stored.value)
            except WorkspaceError:
                self._state = WorkspaceState(
                    stored.value, WorkspaceStatus.UNAVAILABLE, stored.updated_at
                )
                return self._state
            self._missions.use_runtime(self._build(root))
            self._state = WorkspaceState(
                root, WorkspaceStatus.ACTIVE, stored.updated_at
            )
            return self._state

    async def set_root(self, raw: object) -> WorkspaceState:
        """Validate, build the runtime, store, then use it for later missions.

        Nothing changes unless every step succeeds.
        """
        async with self._lock:
            root = await asyncio.to_thread(self._validate, raw)
            runtime = self._build(root)
            now = self._clock().astimezone(UTC)
            await asyncio.to_thread(self._store.set_workspace_root, root, now)
            self._missions.use_runtime(runtime)
            self._state = WorkspaceState(root, WorkspaceStatus.ACTIVE, now)
            return self._state
