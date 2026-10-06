"""Trusted, bounded direct-child execution; output is inert evidence.

Profiles are application configuration, not a sandbox for hostile executables.
No process-tree control, approval authentication or secret integration is provided.
"""

from __future__ import annotations

import ctypes
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import IO, Annotated, Self, TypeVar, cast
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    field_validator,
    model_validator,
)

from phoenix.security.audit import AuditError, AuditTrail, audit_required_for_execution
from phoenix.security.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionGrant,
    PermissionPolicy,
    PermissionRequest,
    PermissionStatus,
    UserPermissionDecision,
)
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolRegistryError,
    ToolResultStatus,
    ToolRiskLevel,
)

if os.name == "nt":
    import msvcrt

_Id = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Text = Annotated[str, StringConstraints(strict=True)]
_Model = TypeVar("_Model", bound=BaseModel)
_MAX_OUTPUT = 1024 * 1024
_MAX_STDIN = 64 * 1024
_MAX_ARGV = 16 * 1024
_TOOLS = ("terminal.inspect", "terminal.project", "terminal.sensitive")
_RISKS = (ToolRiskLevel.P2, ToolRiskLevel.P3, ToolRiskLevel.P4)
_PLATFORMS = ("Windows", "Linux", "macOS")
_ENV_NAMES = ("SystemRoot", "WINDIR", "LANG", "LC_ALL", "LC_CTYPE")


class TerminalArgumentPolicy(StrEnum):
    NONE = "none"
    ALLOWLIST = "allowlist"


class TerminalErrorCode(StrEnum):
    PROFILE_NOT_FOUND = "profile_not_found"
    INVALID_ARGUMENT = "invalid_argument"
    CWD_UNAVAILABLE = "cwd_unavailable"
    EXECUTABLE_UNAVAILABLE = "executable_unavailable"
    TIMEOUT = "timeout"
    OUTPUT_LIMIT = "output_limit"
    PROCESS_FAILURE = "process_failure"
    IO_FAILURE = "io_failure"


class TerminalInputError(ValueError):
    def __init__(
        self, code: TerminalErrorCode = TerminalErrorCode.INVALID_ARGUMENT
    ) -> None:
        super().__init__("Invalid terminal configuration, call or permission evidence")
        self.error_code = code


class TerminalAuditError(Exception):
    def __init__(self, call_id: str) -> None:
        super().__init__("Required terminal permission audit failed")
        self.call_id = call_id
        self.effect_may_have_occurred = False


class TerminalPostExecutionAuditError(TerminalAuditError):
    def __init__(self, call_id: str) -> None:
        Exception.__init__(self, "Terminal outcome audit failed after an attempt")
        self.call_id = call_id
        self.effect_may_have_occurred = True


def _enum(value: object, vocabulary: type[StrEnum]) -> object:
    if type(value) is not str and not any(value is x for x in vocabulary):
        raise ValueError("Expected exact vocabulary text or declared member")
    return value


def _stored(value: BaseModel, contract: type[BaseModel]) -> dict[str, object]:
    if type(value) is not contract:
        raise ValueError("Expected exact contract")
    extras = value.__pydantic_extra__
    if extras is not None and type(extras) is not dict:
        raise ValueError("Invalid stored extras")
    return {**vars(value), **(extras or {})}


def _validated(value: object, contract: type[_Model]) -> _Model:
    if not isinstance(value, BaseModel):
        raise ValueError("Expected exact contract instance")
    return contract.model_validate(_stored(value, contract))


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )

    @model_validator(mode="wrap")
    @classmethod
    def snapshot(cls, value: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        return handler(_stored(value, cls) if isinstance(value, BaseModel) else value)


def _arguments(value: object) -> tuple[str, ...]:
    if type(value) not in (tuple, list):
        raise ValueError("Arguments require an ordered sequence")
    result = tuple(cast(Sequence[object], value))
    if len(result) > 64 or any(type(x) is not str for x in result):
        raise ValueError("Arguments require at most 64 strict strings")
    strings = cast(tuple[str, ...], result)
    try:
        if any("\x00" in x or len(x.encode("utf-8")) > 4096 for x in strings):
            raise ValueError("Invalid argument text")
        if sum(len(x.encode("utf-8")) + 1 for x in strings) > _MAX_ARGV:
            raise ValueError("Arguments exceed byte budget")
    except UnicodeError:
        raise ValueError("Arguments require valid UTF-8 text") from None
    return strings


def _relative(value: str) -> str:
    # PHX-043's conservative grammar, scoped here without changing that runtime.
    if value == ".":
        return value
    if not value or any(char in value for char in '\x00\\:*?<>|"'):
        raise ValueError("Invalid working subdirectory")
    devices = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{suffix}" for prefix in ("COM", "LPT") for suffix in "123456789¹²³"
    }
    for part in value.split("/"):
        if (
            part in ("", ".", "..")
            or part.endswith((".", " "))
            or part.split(".", 1)[0].rstrip(" ").upper() in devices
        ):
            raise ValueError("Invalid working subdirectory component")
    return value


class TerminalCommandProfile(_Contract):
    """Explicit trusted configuration; no risk override, handler or shell field."""

    profile_id: _Id
    tool_id: _Id
    description: Annotated[str, StringConstraints(strict=True, min_length=1)]
    executable: Annotated[str, StringConstraints(strict=True, min_length=1)]
    fixed_args: tuple[_Text, ...] = ()
    argument_policy: TerminalArgumentPolicy = TerminalArgumentPolicy.NONE
    allowed_arguments: tuple[_Text, ...] = ()
    working_directory: _Text = "."
    inherited_environment: tuple[_Id, ...] = ("SystemRoot",)
    stdin_allowed: bool = Field(default=False, strict=True)
    timeout_seconds: Annotated[
        float, Field(strict=True, gt=0, le=300, allow_inf_nan=False)
    ] = 30
    max_stdout_bytes: Annotated[int, Field(strict=True, ge=1, le=_MAX_OUTPUT)] = 65536
    max_stderr_bytes: Annotated[int, Field(strict=True, ge=1, le=_MAX_OUTPUT)] = 65536
    supported_platforms: tuple[_Id, ...] = _PLATFORMS

    @field_validator("fixed_args", "allowed_arguments", mode="before")
    @classmethod
    def argv(cls, value: object) -> tuple[str, ...]:
        return _arguments(value)

    @field_validator("argument_policy", mode="before")
    @classmethod
    def policy(cls, value: object) -> object:
        return _enum(value, TerminalArgumentPolicy)

    @field_validator("inherited_environment", "supported_platforms", mode="before")
    @classmethod
    def sequence(cls, value: object) -> object:
        if type(value) not in (list, tuple):
            raise ValueError("Expected ordered declaration")
        return tuple(cast(Sequence[object], value))

    @field_validator("timeout_seconds", mode="before")
    @classmethod
    def timeout(cls, value: object) -> object:
        if type(value) not in (int, float):
            raise ValueError("Timeout must be an ordinary finite number")
        return value

    @model_validator(mode="after")
    def shape(self) -> Self:
        executable = Path(self.executable)
        if (
            self.tool_id not in _TOOLS
            or not self.description.strip()
            or "\x00" in self.executable
            or not executable.is_absolute()
            or executable.suffix.lower() in (".bat", ".cmd")
            or executable.stem.lower()
            in (
                "cmd",
                "powershell",
                "pwsh",
                "bash",
                "sh",
                "zsh",
                "dash",
                "fish",
                "csh",
                "wscript",
                "cscript",
            )
        ):
            raise ValueError("Invalid trusted executable or canonical tool")
        _relative(self.working_directory)
        if (
            len(set(self.allowed_arguments)) != len(self.allowed_arguments)
            or (
                self.argument_policy == TerminalArgumentPolicy.NONE
                and self.allowed_arguments
            )
            or (
                self.argument_policy == TerminalArgumentPolicy.ALLOWLIST
                and not self.allowed_arguments
            )
        ):
            raise ValueError("Argument policy and allowlist disagree")
        for values, allowed in (
            (self.inherited_environment, _ENV_NAMES),
            (self.supported_platforms, _PLATFORMS),
        ):
            if len(set(values)) != len(values) or any(x not in allowed for x in values):
                raise ValueError("Invalid trusted support or environment declaration")
        if not self.supported_platforms:
            raise ValueError("Platform declaration cannot be empty")
        return self


@dataclass(frozen=True, slots=True, init=False)
class TerminalProfileRegistry:
    """Empty by default; explicit source-ordered configuration snapshots only."""

    _profiles: tuple[TerminalCommandProfile, ...]

    def __init__(self, profiles: Sequence[TerminalCommandProfile] = ()) -> None:
        if hasattr(self, "_profiles"):
            raise TerminalInputError()
        try:
            if type(profiles) not in (tuple, list):
                raise ValueError("Expected ordered profiles")
            accepted = tuple(_validated(x, TerminalCommandProfile) for x in profiles)
            if len({x.profile_id for x in accepted}) != len(accepted):
                raise ValueError("Duplicate normalized profile ID")
        except ValueError:
            raise TerminalInputError() from None
        object.__setattr__(self, "_profiles", accepted)

    def list_profiles(self) -> tuple[TerminalCommandProfile, ...]:
        return self._profiles

    def require(self, profile_id: str) -> TerminalCommandProfile:
        for profile in self._profiles:
            if profile.profile_id == profile_id:
                return profile
        raise TerminalInputError(TerminalErrorCode.PROFILE_NOT_FOUND)


class TerminalCall(_Contract):
    call_id: _Id = Field(default_factory=lambda: str(uuid4()))
    profile_id: _Id
    arguments: tuple[_Text, ...] = ()
    stdin_text: _Text | None = None

    @field_validator("arguments", mode="before")
    @classmethod
    def argv(cls, value: object) -> tuple[str, ...]:
        return _arguments(value)

    @field_validator("stdin_text")
    @classmethod
    def stdin(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                if len(value.encode("utf-8")) > _MAX_STDIN:
                    raise ValueError("Stdin exceeds 64 KiB")
            except UnicodeError:
                raise ValueError("Stdin must be valid UTF-8 text") from None
        return value


def terminal_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Canonical inert definitions; no profiles are implicitly registered."""
    return tuple(
        ToolDefinition(
            tool_id=identity,
            description=(
                f"Execute one trusted {identity.split('.')[1]} command profile; "
                "output is untrusted evidence."
            ),
            input_schema=TerminalCall.model_json_schema(),
            output_schema=TerminalToolResult.model_json_schema(),
            risk_level=risk,
            permission_requirement=(
                f"{risk.value}: exact trusted profile and workspace invocation"
            ),
            timeout_seconds=300,
            cancellation_supported=False,
            audit_behavior="durable permission and outcome required"
            if risk in _RISKS[1:]
            else "no mandatory durable audit",
            supported_platforms=_PLATFORMS,
            supported_environments=("local", "trusted-workspace"),
        )
        for identity, risk in zip(_TOOLS, _RISKS, strict=True)
    )


def _metadata(tool_id: str) -> tuple[ToolRiskLevel, str]:
    if tool_id not in _TOOLS:
        raise ValueError("Unknown canonical terminal tool")
    risk = _RISKS[_TOOLS.index(tool_id)]
    return risk, f"{risk.value}: exact trusted profile and workspace invocation"


class TerminalIntent(_Contract):
    call_id: _Id
    profile_id: _Id
    tool_id: _Id
    action: _Id
    resource: _Id
    risk_level: ToolRiskLevel
    permission_requirement: _Id

    @field_validator("risk_level", mode="before")
    @classmethod
    def risk(cls, value: object) -> object:
        return _enum(value, ToolRiskLevel)

    @model_validator(mode="after")
    def canonical(self) -> Self:
        if (self.risk_level, self.permission_requirement) != _metadata(
            self.tool_id
        ) or self.action != "execute":
            raise ValueError("Intent disagrees with canonical tool")
        return self


class TerminalToolResult(_Contract):
    call_id: _Id
    profile_id: _Id
    tool_id: _Id
    resource: _Id
    status: ToolResultStatus
    attempted: bool = Field(strict=True)
    effect_applied: bool = Field(strict=True)
    exit_code: Annotated[int, Field(strict=True)] | None = None
    stdout: _Text = ""
    stderr: _Text = ""
    stdout_truncated: bool = Field(default=False, strict=True)
    stderr_truncated: bool = Field(default=False, strict=True)
    permission_decision: PermissionDecision
    permission_audit_id: _Id | None = None
    outcome_audit_id: _Id | None = None
    error_code: TerminalErrorCode | None = None

    @field_validator("status", mode="before")
    @classmethod
    def status_enum(cls, value: object) -> object:
        return _enum(value, ToolResultStatus)

    @field_validator("error_code", mode="before")
    @classmethod
    def error_enum(cls, value: object) -> object:
        return _enum(value, TerminalErrorCode) if value is not None else None

    @field_validator("permission_decision", mode="before")
    @classmethod
    def decision(cls, value: object) -> PermissionDecision:
        return _validated(value, PermissionDecision)

    @field_validator("stdout", "stderr")
    @classmethod
    def bounded_text(cls, value: str) -> str:
        # UTF-8 replacement decoding can expand each invalid captured byte to U+FFFD.
        if len(value.encode("utf-8")) > 3 * _MAX_OUTPUT:
            raise ValueError("Output text exceeds absolute capture ceiling")
        return value

    @model_validator(mode="after")
    def consistent(self) -> Self:
        risk, requirement = _metadata(self.tool_id)
        decision = self.permission_decision
        if (decision.tool_id, decision.risk_level, decision.permission_requirement) != (
            self.tool_id,
            risk,
            requirement,
        ):
            raise ValueError("Result permission evidence disagrees with canonical tool")
        allowed = decision.status == PermissionStatus.ALLOWED
        required = audit_required_for_execution(risk)
        if (
            (self.permission_audit_id is not None) != required
            or (self.outcome_audit_id is not None) != (required and allowed)
            or (
                self.outcome_audit_id is not None
                and self.outcome_audit_id == self.permission_audit_id
            )
        ):
            raise ValueError("Result lacks distinct required audit IDs")
        if not allowed:
            if (
                self.status != ToolResultStatus.DENIED
                or self.attempted
                or self.effect_applied
                or self.exit_code is not None
                or self.stdout
                or self.stderr
                or self.stdout_truncated
                or self.stderr_truncated
                or self.error_code is not None
            ):
                raise ValueError("Nonallowed result cannot report execution evidence")
            return self
        if not self.attempted or self.status in (
            ToolResultStatus.DENIED,
            ToolResultStatus.CANCELLED,
            ToolResultStatus.PARTIAL_SUCCESS,
        ):
            raise ValueError("Allowed result requires one synchronous attempt")
        if self.effect_applied and risk == ToolRiskLevel.P2:
            raise ValueError("Read-only profile cannot report mutation")
        if self.status == ToolResultStatus.SUCCESS:
            if (
                self.exit_code != 0
                or self.error_code is not None
                or self.stdout_truncated
                or self.stderr_truncated
            ):
                raise ValueError("Success requires complete zero-exit evidence")
        elif self.status == ToolResultStatus.TIMEOUT:
            if self.error_code != TerminalErrorCode.TIMEOUT:
                raise ValueError("Timeout requires timeout evidence")
        elif self.status == ToolResultStatus.UNAVAILABLE_DEPENDENCY:
            if self.error_code != TerminalErrorCode.EXECUTABLE_UNAVAILABLE:
                raise ValueError("Unavailable executable evidence required")
        elif self.status == ToolResultStatus.FAILURE:
            if self.error_code not in (
                TerminalErrorCode.CWD_UNAVAILABLE,
                TerminalErrorCode.OUTPUT_LIMIT,
                TerminalErrorCode.PROCESS_FAILURE,
                TerminalErrorCode.IO_FAILURE,
            ):
                raise ValueError("Failure requires safe execution error")
        else:
            raise ValueError("Invalid execution result state")
        if (
            self.status == ToolResultStatus.SUCCESS
            or self.error_code == TerminalErrorCode.PROCESS_FAILURE
        ):
            if self.exit_code is None or (
                self.error_code == TerminalErrorCode.PROCESS_FAILURE
                and self.exit_code == 0
            ):
                raise ValueError("Process exit evidence required")
            if self.effect_applied != (risk != ToolRiskLevel.P2):
                raise ValueError(
                    "Launched modifying profile must preserve possible effect"
                )
        if self.error_code in (
            TerminalErrorCode.CWD_UNAVAILABLE,
            TerminalErrorCode.EXECUTABLE_UNAVAILABLE,
        ):
            if (
                self.effect_applied
                or self.exit_code is not None
                or self.stdout
                or self.stderr
                or self.stdout_truncated
                or self.stderr_truncated
            ):
                raise ValueError("Pre-launch failure cannot fabricate process evidence")
        if self.error_code == TerminalErrorCode.OUTPUT_LIMIT and not (
            self.stdout_truncated or self.stderr_truncated
        ):
            raise ValueError("Output limit requires truncation evidence")
        if self.error_code in (
            TerminalErrorCode.TIMEOUT,
            TerminalErrorCode.OUTPUT_LIMIT,
        ) and self.effect_applied != (risk != ToolRiskLevel.P2):
            raise ValueError(
                "Interrupted modifying profile must preserve possible effect"
            )
        return self


def _cwd(root: Path, relative: str) -> Path:
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise OSError("Unavailable root")
    current = resolved
    for part in () if relative == "." else relative.split("/"):
        current = current / part
        info = current.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0)
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or not stat.S_ISDIR(info.st_mode)
            or not current.resolve(strict=True).is_relative_to(resolved)
        ):
            raise OSError("Unavailable contained working directory")
    return current


def _reader(stream: IO[bytes]) -> Callable[[int], bytes | None]:
    """Single owner, nonblocking reads; no communicate/read-all or reader thread."""
    fd = stream.fileno()
    if os.name != "nt":
        set_blocking = cast(Callable[[int, bool], None], getattr(os, "set_blocking"))
        set_blocking(fd, False)

        def read_posix(size: int) -> bytes | None:
            try:
                return os.read(fd, size)
            except BlockingIOError:
                return None

        return read_posix
    peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
    peek.argtypes = (
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    peek.restype = ctypes.c_int
    handle = msvcrt.get_osfhandle(fd)

    def read_windows(size: int) -> bytes | None:
        available = ctypes.c_uint32()
        if not peek(handle, None, 0, None, ctypes.byref(available), None):
            code = ctypes.get_last_error()
            if code == 109:  # ERROR_BROKEN_PIPE: EOF, not a private diagnostic.
                return b""
            raise OSError("Pipe observation failed")
        return os.read(fd, min(size, available.value)) if available.value else None

    return read_windows


@dataclass(frozen=True)
class _Outcome:
    status: ToolResultStatus
    effect: bool = False
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    error: TerminalErrorCode | None = None


def _capture(
    process: subprocess.Popen[bytes], profile: TerminalCommandProfile, deadline: float
) -> _Outcome:
    assert process.stdout is not None and process.stderr is not None
    readers = (_reader(process.stdout), _reader(process.stderr))
    buffers = (bytearray(), bytearray())
    bounds = (profile.max_stdout_bytes, profile.max_stderr_bytes)
    error: TerminalErrorCode | None = None
    # Poll both pipes fairly, checking the deadline even while output is continuous.
    while True:
        active = False
        for reader, buffer, bound in zip(readers, buffers, bounds, strict=True):
            chunk = reader(min(4096, bound - len(buffer) + 1))
            if chunk:
                active = True
                remaining = bound - len(buffer)
                buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    error = TerminalErrorCode.OUTPUT_LIMIT
        if error is not None:
            break
        if process.poll() is not None and not active:
            break
        if time.monotonic() >= deadline:
            error = TerminalErrorCode.TIMEOUT
            break
        if not active:
            time.sleep(0.005)
    if error is not None and process.poll() is None:
        process.kill()
    exit_code = process.wait(timeout=1)
    status = (
        ToolResultStatus.TIMEOUT
        if error == TerminalErrorCode.TIMEOUT
        else ToolResultStatus.FAILURE
        if error is not None or exit_code != 0
        else ToolResultStatus.SUCCESS
    )
    return _Outcome(
        status,
        profile.tool_id != _TOOLS[0],
        exit_code,
        bytes(buffers[0]).decode("utf-8", errors="replace"),
        bytes(buffers[1]).decode("utf-8", errors="replace"),
        error is not None,
        error is not None,
        error or (TerminalErrorCode.PROCESS_FAILURE if exit_code != 0 else None),
    )


def _attempt(
    root: Path, profile: TerminalCommandProfile, call: TerminalCall
) -> _Outcome:
    try:
        cwd = _cwd(root, profile.working_directory)
    except (OSError, RuntimeError, ValueError):
        return _Outcome(
            ToolResultStatus.FAILURE, error=TerminalErrorCode.CWD_UNAVAILABLE
        )
    platform = (
        "Windows"
        if os.name == "nt"
        else "macOS"
        if sys.platform == "darwin"
        else "Linux"
        if sys.platform.startswith("linux")
        else "unsupported"
    )
    if platform not in profile.supported_platforms:
        return _Outcome(ToolResultStatus.FAILURE, error=TerminalErrorCode.IO_FAILURE)
    process: subprocess.Popen[bytes] | None = None
    try:
        # Read only explicitly permitted ordinary names, after permission/audit.
        environment = {
            name: os.environ[name]
            for name in profile.inherited_environment
            if name in os.environ
        }
        with ExitStack() as stack:
            stdin: int | IO[bytes] = subprocess.DEVNULL
            if call.stdin_text is not None:
                # Bounded seekable input avoids Windows communicate() worker threads
                # and blocked pipe writes when a child does not consume its stdin.
                source = stack.enter_context(tempfile.TemporaryFile(mode="w+b"))
                source.write(call.stdin_text.encode("utf-8"))
                source.seek(0)
                stdin = cast(IO[bytes], source)
            deadline = time.monotonic() + profile.timeout_seconds
            try:
                process = subprocess.Popen(
                    [profile.executable, *profile.fixed_args, *call.arguments],
                    shell=False,
                    cwd=cwd,
                    env=environment,
                    stdin=stdin,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=False,
                    bufsize=0,
                    close_fds=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except FileNotFoundError:
                return _Outcome(
                    ToolResultStatus.UNAVAILABLE_DEPENDENCY,
                    error=TerminalErrorCode.EXECUTABLE_UNAVAILABLE,
                )
            return _capture(process, profile, deadline)
    except (OSError, ValueError, subprocess.SubprocessError):
        return _Outcome(
            ToolResultStatus.FAILURE,
            process is not None and profile.tool_id != _TOOLS[0],
            error=TerminalErrorCode.IO_FAILURE,
        )
    finally:
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=1)
            except (OSError, subprocess.SubprocessError):
                pass  # Bounded best effort; never launch or retry another process.
            finally:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            pass  # Closing a failed pipe cannot skip the outcome audit.


@dataclass(frozen=True, slots=True, init=False)
class TerminalTool:
    _root: Path
    _registry: ToolRegistry
    _profiles: TerminalProfileRegistry
    _permissions: PermissionEngine
    _audit: AuditTrail

    def __init__(
        self,
        root: str | Path,
        registry: ToolRegistry,
        profiles: TerminalProfileRegistry,
        policy: PermissionPolicy,
        audit_trail: AuditTrail,
    ) -> None:
        if hasattr(self, "_root"):
            raise TerminalInputError()
        try:
            if type(root) not in (str, type(Path())) or "\x00" in str(root):
                raise ValueError("Expected explicit native absolute root")
            path = Path(root)
            if not path.is_absolute():
                raise ValueError("Expected explicit absolute root")
            path = Path(os.path.normpath(str(path)))
            if (
                type(registry) is not ToolRegistry
                or type(profiles) is not TerminalProfileRegistry
                or type(audit_trail) is not AuditTrail
            ):
                raise ValueError("Expected exact authority dependencies")
            snapshot = ToolRegistry(registry.list_tools())
            for canonical in terminal_tool_definitions():
                definition = snapshot.require(canonical.tool_id)
                _enum(definition.risk_level, ToolRiskLevel)
                if definition != canonical:
                    raise ValueError("Noncanonical terminal declaration")
            catalog = TerminalProfileRegistry(profiles.list_profiles())
            permissions = PermissionEngine(
                snapshot, _validated(policy, PermissionPolicy)
            )
        except (ValueError, ToolRegistryError):
            raise TerminalInputError() from None
        object.__setattr__(self, "_root", path)
        object.__setattr__(self, "_registry", snapshot)
        object.__setattr__(self, "_profiles", catalog)
        object.__setattr__(self, "_permissions", permissions)
        object.__setattr__(self, "_audit", audit_trail)

    def describe(self, call: TerminalCall) -> TerminalIntent:
        try:
            call = _validated(call, TerminalCall)
            profile = self._profiles.require(call.profile_id)
            if (
                (
                    profile.argument_policy == TerminalArgumentPolicy.NONE
                    and call.arguments
                )
                or any(x not in profile.allowed_arguments for x in call.arguments)
                or (call.stdin_text is not None and not profile.stdin_allowed)
                or sum(
                    len(x.encode("utf-8")) + 1
                    for x in (profile.executable, *profile.fixed_args, *call.arguments)
                )
                > _MAX_ARGV
            ):
                raise ValueError("Unsupported profile input")
            definition = self._registry.require(profile.tool_id)
            cwd = (
                self._root
                if profile.working_directory == "."
                else self._root.joinpath(*profile.working_directory.split("/"))
            )
            # Bind exact configuration and invocation without exposing arguments/stdin.
            digest = sha256(
                json.dumps(
                    [profile.model_dump(mode="json"), call.arguments, call.stdin_text],
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()
            resource = json.dumps(
                [profile.profile_id, str(cwd), digest],
                ensure_ascii=True,
                separators=(",", ":"),
            )
            return TerminalIntent(
                call_id=call.call_id,
                profile_id=profile.profile_id,
                tool_id=definition.tool_id,
                action="execute",
                resource=resource,
                risk_level=definition.risk_level,
                permission_requirement=definition.permission_requirement,
            )
        except TerminalInputError:
            raise
        except (ValueError, ToolRegistryError):
            raise TerminalInputError() from None

    def execute(
        self,
        call: TerminalCall,
        request: PermissionRequest,
        *,
        grants: Sequence[PermissionGrant] = (),
        user_decision: UserPermissionDecision | None = None,
    ) -> TerminalToolResult:
        try:
            call = _validated(call, TerminalCall)
            intent = self.describe(call)
            profile = self._profiles.require(call.profile_id)
            request = _validated(request, PermissionRequest)
            if (request.tool_id, request.action, request.resource) != (
                intent.tool_id,
                intent.action,
                intent.resource,
            ):
                raise ValueError("Permission request does not correlate")
            decision = self._permissions.evaluate(
                request, grants=grants, user_decision=user_decision
            )
        except TerminalInputError:
            raise
        except (ValueError, ToolRegistryError):
            raise TerminalInputError() from None
        permission_id = outcome_id = None
        required = audit_required_for_execution(intent.risk_level)
        if required:
            try:
                permission_id = self._audit.record_permission_decision(
                    request,
                    decision,
                    occurred_at=datetime.now(UTC),
                    correlation_id=call.call_id,
                ).audit_id
            except AuditError:
                raise TerminalAuditError(call.call_id) from None
        allowed = decision.status == PermissionStatus.ALLOWED
        outcome = (
            _attempt(self._root, profile, call)
            if allowed
            else _Outcome(ToolResultStatus.DENIED)
        )
        if required and allowed:
            try:
                outcome_id = self._audit.record_tool_outcome(
                    request,
                    decision,
                    outcome.status,
                    occurred_at=datetime.now(UTC),
                    correlation_id=call.call_id,
                ).audit_id
            except AuditError:
                raise TerminalPostExecutionAuditError(call.call_id) from None
        return TerminalToolResult(
            call_id=call.call_id,
            profile_id=profile.profile_id,
            tool_id=intent.tool_id,
            resource=intent.resource,
            status=outcome.status,
            attempted=allowed,
            effect_applied=outcome.effect,
            exit_code=outcome.exit_code,
            stdout=outcome.stdout,
            stderr=outcome.stderr,
            stdout_truncated=outcome.stdout_truncated,
            stderr_truncated=outcome.stderr_truncated,
            permission_decision=decision,
            permission_audit_id=permission_id,
            outcome_audit_id=outcome_id,
            error_code=outcome.error,
        )
