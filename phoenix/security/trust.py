"""Trust mode (ADR-305 to ADR-309): in-memory, default OFF, resets with the process.

While ON, PermissionEngine auto-approves P1-P3 inside the file scope below and
a P4 once per task after the user's first P4 approval of that task. Payments
and sending messages are never auto-approved; P5 stays blocked. This module only
answers scope questions and holds the state; nothing here creates a grant from
model output.
"""

import ctypes
import os
import re
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

TRUST_REASON = "trust_mode"
_NEVER_AUTO = frozenset(
    "pay payment payments purchase checkout buy send email mail message messages sms transfer".split()
)
_DENY_SEGMENTS = frozenset(
    {".ssh", ".gnupg", ".aws", ".mozilla", "keepass", "1password", "bitwarden", "lastpass"}
)
_DENY_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".kdbx", ".kdb")
_USER_FOLDERS = ("Desktop", "Documents", "Downloads")
_PROJECT_FOLDERS = ("Projects", "source", "repos", "dev", "code")
_PATHLIKE = re.compile(r"^(?:[A-Za-z]:|\\\\|//|/|\.\.?[\\/]|file:)|[\\/]")


def never_auto(tool_id: str, action: str) -> bool:
    """Payments and sending messages always need the user, per action."""
    return bool(_NEVER_AUTO & set(re.split(r"[^a-z0-9]+", f"{tool_id} {action}".lower())))


def _long(path: str) -> str:
    """Expand 8.3 short names (Windows) on the deepest existing ancestor."""
    if sys.platform != "win32":
        return path
    head, tail = path, []
    while head and not os.path.exists(head):
        parent, name = os.path.split(head)
        if parent == head:
            break
        head, tail = parent, [name, *tail]
    buf = ctypes.create_unicode_buffer(32768)
    if head and ctypes.windll.kernel32.GetLongPathNameW(head, buf, 32768):  # type: ignore[attr-defined]
        head = buf.value
    return os.path.join(head, *tail) if tail else head


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)).casefold()


def _inside(path: str, root: str) -> bool:
    child, base = _key(path), _key(root)
    try:
        return os.path.commonpath((child, base)) == base
    except ValueError:  # other drive
        return False


class TrustState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.enabled = False
        self.since: datetime | None = None
        self.workspace_root: str | None = None
        self._tasks: dict[str, str] = {}  # task_id -> approval id (P4, once per task)
        self._grants: set[str] = set()

    def set(self, enabled: bool) -> None:
        with self._lock:
            if enabled and not self.enabled:
                self.since = datetime.now(UTC)
            if not enabled:  # kill switch: nothing survives
                self.since = None
                self._tasks.clear()
                self._grants.clear()
            self.enabled = enabled

    # -- P4: one approval per task ----------------------------------------
    def approve_task(self, task_id: str, approval_id: str) -> None:
        with self._lock:
            if self.enabled:
                self._tasks[task_id] = approval_id

    def revoke_task(self, task_id: str) -> None:
        with self._lock:
            self._tasks.pop(task_id, None)

    def task_approval(self, task_id: str | None) -> str | None:
        return self._tasks.get(task_id) if self.enabled and task_id else None

    # -- minted P1-P3 grants (ids only; the engine builds the grant) -------
    def register_grant(self, grant_id: str) -> None:
        with self._lock:
            if self.enabled:
                self._grants.add(grant_id)

    def is_trust_grant(self, grant_id: str) -> bool:
        return self.enabled and grant_id in self._grants

    @property
    def grant_ids(self) -> frozenset[str]:
        return frozenset(self._grants)

    # -- scope ---------------------------------------------------------------
    @staticmethod
    def _home() -> Path:
        return Path(os.path.expanduser("~"))

    def allow(self) -> list[str]:
        home = self._home()
        roots = [str(home / n) for n in (*_USER_FOLDERS, *_PROJECT_FOLDERS)]
        return [*roots, *([self.workspace_root] if self.workspace_root else [])]

    def deny(self) -> list[str]:
        env = os.environ
        roots = [
            env.get(k, "")
            for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData")
        ]
        home = self._home()
        roots += [str(home / "AppData"), f"other users: {home.parent}{os.sep}*"]
        roots += [f"**{os.sep}{s}" for s in sorted(_DENY_SEGMENTS)]
        roots += [".env*", *(f"*{s}" for s in _DENY_SUFFIXES)]
        return [r for r in roots if r]

    def path_allowed(self, resource: str) -> bool:
        """Hardened check: case, .., symlinks/junctions, UNC, 8.3 names, ADS."""
        if not resource or "\x00" in resource:
            return False
        p = resource[7:] if resource.lower().startswith("file:") else resource
        p = p.lstrip("/") if re.match(r"^/[A-Za-z]:", p) else p
        if p.startswith("\\\\?\\") and not p.upper().startswith("\\\\?\\UNC\\"):
            p = p[4:]
        if p.startswith(("\\\\", "//")):  # UNC, \\.\ devices, \\?\UNC
            return False
        if ":" in (p[2:] if re.match(r"^[A-Za-z]:", p) else p):
            return False  # alternate data stream / malformed drive
        if not os.path.isabs(p):
            if not self.workspace_root:
                return False
            p = os.path.join(self.workspace_root, p)
        # Windows drops trailing dots/spaces from every component.
        p = os.sep.join(seg.rstrip(". ") or seg for seg in re.split(r"[\\/]", p))
        long = _long(os.path.normpath(p))
        real = os.path.realpath(long)  # resolves symlinks and junctions
        for candidate in (long, real):
            if self._denied(candidate):
                return False
        roots = [r for r in self.allow() if r]
        return any(
            _inside(real, os.path.realpath(_long(r))) for r in roots
        )

    def _denied(self, path: str) -> bool:
        key = _key(path)
        parts = key.replace("\\", "/").split("/")
        name = parts[-1]
        if set(parts) & _DENY_SEGMENTS or name.startswith(".env") or name.endswith(_DENY_SUFFIXES):
            return True
        env = os.environ
        for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"):
            root = env.get(var)
            if root and _inside(path, root):
                return True
        home = self._home()
        if _inside(path, str(home / "AppData")):
            return True
        users = home.parent
        if users != users.parent and _inside(path, str(users)) and not _inside(path, str(home)):
            return True
        return False

    def resource_allowed(self, resource: str) -> bool:
        """Path-like resources must pass the scope; URLs and ids are not paths."""
        if "://" in resource and not resource.lower().startswith("file:"):
            return True
        return self.path_allowed(resource) if _PATHLIKE.search(resource) else True

    def snapshot(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "since": self.since.isoformat() if self.since else None,
            "allow": self.allow(),
            "deny": self.deny(),
        }


TRUST = TrustState()


def recycle(path: str | Path) -> None:
    """Delete to the Windows Recycle Bin (never a hard delete); else refuse."""
    if sys.platform != "win32":
        raise OSError("Recycle Bin is unavailable on this platform")
    # ponytail: spawns PowerShell (~0.5 s); SHFileOperationW via ctypes if that matters.
    script = (
        "Add-Type -AssemblyName Microsoft.VisualBasic;"
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
        "$env:PHX_RECYCLE,'OnlyErrorDialogs','SendToRecycleBin')"
    )
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, "PHX_RECYCLE": str(path)},
        capture_output=True,
        timeout=30,
    )
    if done.returncode or os.path.lexists(path):
        raise OSError("Recycle Bin delete failed")
