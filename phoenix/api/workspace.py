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
   (Windows: the Windows, Program Files and ProgramData folders, taken from the
   environment, from the Windows API and from fixed defaults, so a sanitised
   environment cannot hide them; POSIX: ``/bin``, ``/boot``, ``/dev``,
   ``/etc``, ``/lib``, ``/lib64``, ``/proc``, ``/run``, ``/sbin``, ``/sys``,
   ``/usr``, ``/var``), or anything inside them, and any ``$Recycle.Bin`` or
   ``System Volume Information`` folder.
6. ``workspace_app_data``: the user's application-data tree (``AppData`` with
   Roaming, Local and LocalLow, which also holds the temp folder): other
   programs' settings, caches and tokens, not the user's documents.
7. ``workspace_data_directory``: overlaps PHOENIX's own data directory (the
   database and migration lock), either way round, compared by real path.

The accepted root is returned in its canonical (real-path) spelling, so the
resource strings in permission requests and grants are stable.

Path checks are not a race-proof sandbox (see FilesystemTool). The stored root
is therefore validated again before every mission that would use it
(``MissionService.use_runtime(..., preflight=...)``): if it fails, for example
because the folder was replaced by a junction, that mission fails
``tool_rejected``, the workspace becomes ``unavailable`` and later missions run
without document steps. FilesystemTool still checks every read; hardening its
own root check is follow-up work (ADR-089).

Changing the workspace builds a new FilesystemTool and runtime; missions that
start afterwards use them, and a running mission keeps its own
(``MissionService.use_runtime``). Grants are never touched: they match an exact
absolute resource, and every resource a document step can request lies inside
the current root, so a grant for a path outside it can never apply. Approvals
are refused for requests outside the current root (``covers``).
"""

import asyncio
import os
import stat
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from phoenix.agents.runtime import PhoenixRuntime
from phoenix.api.missions import MissionService, ServiceNotRunningError
from phoenix.persistence.settings import SettingsStore
from phoenix.tools.filesystem import FilesystemInputError

MAX_WORKSPACE_CHARS = 4096
# A mission refused because its workspace no longer validates (an existing
# MissionFailureCode, so the PHX-103 event allowlist carries it).
WORKSPACE_FAILURE_CODE = "tool_rejected"
_WINDOWS_SYSTEM_VARIABLES = (
    "SystemRoot",
    "windir",
    "ProgramFiles",
    "ProgramFiles(x86)",
    "ProgramW6432",
    "ProgramData",
)
_WINDOWS_SYSTEM_DEFAULTS = (
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
)
# FOLDERID_Windows, ProgramFiles, ProgramFilesX86, ProgramData.
_SYSTEM_FOLDER_IDS = (
    "F38BF404-1D43-42F2-9305-67DE0B28FC23",
    "905E63B6-C1BF-494E-B29C-65B732D3D21A",
    "7C5A40EF-A0FB-4BFC-874A-C0F2E0B9FA8E",
    "62AB5D82-FDC1-4DC3-A9DD-070D1D495D97",
)
_APP_DATA_VARIABLES = ("APPDATA", "LOCALAPPDATA")
# FOLDERID_RoamingAppData, LocalAppData, LocalAppDataLow.
_APP_DATA_FOLDER_IDS = (
    "3EB685DB-65F9-4CF6-A03A-E3EF65729F3D",
    "F1B32785-6FBA-4FCF-9D55-7B8E7F157091",
    "A520A1A4-1780-4FF6-BD18-167343C5AF16",
)
_REFUSED_SEGMENTS = ("$recycle.bin", "system volume information")
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
    APP_DATA = "workspace_app_data"
    DATA_DIRECTORY = "workspace_data_directory"


class WorkspaceError(ValueError):
    """A refused workspace root; the message never contains the path."""

    def __init__(self, code: WorkspaceErrorCode) -> None:
        super().__init__(code.value)
        self.code = code


class WorkspaceStatus(StrEnum):
    UNSET = "unset"
    ACTIVE = "active"
    UNAVAILABLE = "unavailable"  # stored, but no longer valid


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


def _known_folders(folder_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Windows known folders through the shell API; () elsewhere or on failure."""
    if sys.platform != "win32":
        return ()
    import ctypes
    from ctypes import wintypes

    class _Guid(ctypes.Structure):
        _fields_ = [
            ("data1", wintypes.DWORD),
            ("data2", wintypes.WORD),
            ("data3", wintypes.WORD),
            ("data4", ctypes.c_ubyte * 8),
        ]

    found: list[str] = []
    for folder_id in folder_ids:
        value = uuid.UUID(folder_id)
        guid = _Guid(
            value.fields[0],
            value.fields[1],
            value.fields[2],
            (ctypes.c_ubyte * 8)(*value.bytes[8:]),
        )
        path = ctypes.c_wchar_p()
        try:
            result = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(path)
            )
            if result == 0 and path.value:
                found.append(path.value)
        except OSError:
            continue
        finally:
            if path:
                ctypes.windll.ole32.CoTaskMemFree(path)
    buffer = ctypes.create_unicode_buffer(32768)
    try:
        if ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, 32768):
            found.append(buffer.value)
    except OSError:
        pass
    return tuple(found)


def _absolute(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if os.path.isabs(value)))


def system_directories(
    environ: Mapping[str, str] | None = None, platform: str = sys.platform
) -> tuple[str, ...]:
    """OS and program folders: environment, Windows API and fixed defaults."""
    if platform != "win32":
        return _POSIX_SYSTEM_DIRECTORIES
    source = os.environ if environ is None else environ
    found = tuple(source.get(name, "") for name in _WINDOWS_SYSTEM_VARIABLES)
    return _absolute(
        (*found, *_known_folders(_SYSTEM_FOLDER_IDS), *_WINDOWS_SYSTEM_DEFAULTS)
    )


def app_data_directories(
    environ: Mapping[str, str] | None = None, platform: str = sys.platform
) -> tuple[str, ...]:
    """The user's AppData tree (with its Roaming, Local and LocalLow folders)."""
    if platform != "win32":
        return ()
    source = os.environ if environ is None else environ
    folders = (
        *(source.get(name, "") for name in _APP_DATA_VARIABLES),
        *_known_folders(_APP_DATA_FOLDER_IDS),
    )
    parents = tuple(
        os.path.dirname(os.path.normpath(folder))
        for folder in folders
        if os.path.isabs(folder)
        and os.path.basename(os.path.dirname(os.path.normpath(folder))).lower()
        == "appdata"
    )
    profile = source.get("USERPROFILE", "")
    home = (os.path.join(profile, "AppData"),) if os.path.isabs(profile) else ()
    return _absolute((*folders, *parents, *home))


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
    raw: object,
    *,
    data_dir: Path,
    system: tuple[str, ...] | None = None,
    app_data: tuple[str, ...] | None = None,
) -> str:
    """Return the canonical absolute root, or raise WorkspaceError."""
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
    if Path(real).parent == Path(real):
        raise WorkspaceError(WorkspaceErrorCode.DRIVE_ROOT)
    for directory in system_directories() if system is None else system:
        if _inside(real, directory):
            raise WorkspaceError(WorkspaceErrorCode.SYSTEM_DIRECTORY)
    segments = {part.lower() for part in Path(real).parts}
    if segments & set(_REFUSED_SEGMENTS):
        raise WorkspaceError(WorkspaceErrorCode.SYSTEM_DIRECTORY)
    for directory in app_data_directories() if app_data is None else app_data:
        if _inside(real, directory):
            raise WorkspaceError(WorkspaceErrorCode.APP_DATA)
    data = os.path.realpath(data_dir)
    if _inside(real, data) or _inside(data, real):
        raise WorkspaceError(WorkspaceErrorCode.DATA_DIRECTORY)
    return real


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WorkspaceService:
    """The stored workspace root and the runtime built for it."""

    def __init__(
        self,
        *,
        store: SettingsStore,
        missions: MissionService,
        build_runtime: Callable[[str | None], PhoenixRuntime],
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

    def covers(self, resource: str) -> bool:
        """True if resource lies inside the active root (approvals, D2)."""
        state = self._state
        return (
            state.status == WorkspaceStatus.ACTIVE
            and state.root is not None
            and _inside(resource, state.root)
        )

    def _validate(self, raw: object) -> str:
        return validate_workspace_root(raw, data_dir=self._data_dir)

    def _runtime_for(self, root: str) -> PhoenixRuntime:
        try:
            return self._build(root)
        except FilesystemInputError:
            raise WorkspaceError(WorkspaceErrorCode.INVALID) from None

    def _use(self, root: str, runtime: PhoenixRuntime) -> None:
        self._missions.use_runtime(runtime, preflight=self._preflight(root))

    def _preflight(self, root: str) -> Callable[[], Awaitable[str | None]]:
        async def check() -> str | None:
            """Validate the root again before a mission runs on it (D1)."""
            try:
                valid = await asyncio.to_thread(self._validate, root)
            except WorkspaceError:
                valid = None
            if valid == root:
                return None
            async with self._lock:
                state = self._state
                if state.root == root and state.status == WorkspaceStatus.ACTIVE:
                    self._missions.use_runtime(self._build(None))
                    self._state = WorkspaceState(
                        root, WorkspaceStatus.UNAVAILABLE, state.updated_at
                    )
            return WORKSPACE_FAILURE_CODE

        return check

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
                if root != stored.value:
                    raise WorkspaceError(WorkspaceErrorCode.LINK)
                runtime = self._runtime_for(root)
            except WorkspaceError:
                self._state = WorkspaceState(
                    stored.value, WorkspaceStatus.UNAVAILABLE, stored.updated_at
                )
                return self._state
            self._use(root, runtime)
            self._state = WorkspaceState(
                root, WorkspaceStatus.ACTIVE, stored.updated_at
            )
            return self._state

    async def set_root(self, raw: object) -> WorkspaceState:
        """Validate, build the runtime, store, then use it for later missions.

        Nothing changes unless every step succeeds.
        """
        if not self._missions.running:
            raise ServiceNotRunningError("Mission service is not running")
        async with self._lock:
            root = await asyncio.to_thread(self._validate, raw)
            runtime = self._runtime_for(root)
            now = self._clock().astimezone(UTC)
            await asyncio.to_thread(self._store.set_workspace_root, root, now)
            self._use(root, runtime)
            self._state = WorkspaceState(root, WorkspaceStatus.ACTIVE, now)
            return self._state
