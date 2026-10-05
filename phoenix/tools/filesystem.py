"""Bounded workspace operations; content is untrusted and never logged.

Trusted integration supplies a narrow non-secret root and authentic permission
evidence. Concurrent hostile path replacement is outside the V1 threat model;
path checks are not a cross-platform, race-proof filesystem sandbox.
"""

import heapq
import os
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Self, TypeVar, cast
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    field_serializer,
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

_MAX_BYTES = 1024 * 1024
_Id = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Model = TypeVar("_Model", bound=BaseModel)


class FilesystemOperation(StrEnum):
    READ_TEXT = "read_text"
    LIST_DIRECTORY = "list_directory"
    STAT = "stat"
    CREATE_TEXT = "create_text"
    CREATE_DIRECTORY = "create_directory"
    WRITE_TEXT = "write_text"
    DELETE_FILE = "delete_file"


class FilesystemErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    NOT_FILE = "not_file"
    NOT_DIRECTORY = "not_directory"
    INVALID_UTF8 = "invalid_utf8"
    LIMIT_EXCEEDED = "limit_exceeded"
    PATH_ESCAPE = "path_escape"
    SYMLINK_FORBIDDEN = "symlink_forbidden"
    ROOT_UNAVAILABLE = "root_unavailable"
    IO_FAILURE = "io_failure"


class FilesystemInputError(ValueError):
    """Safe invalid call, authority or correlation; no private input included."""


class FilesystemAuditError(Exception):
    """Required permission audit failed; no target filesystem access occurred."""

    def __init__(self, call_id: str) -> None:
        super().__init__("Required filesystem permission audit failed")
        self.call_id = call_id
        self.effect_may_have_occurred = False


class FilesystemPostExecutionAuditError(FilesystemAuditError):
    """An attempt already happened; caller must reconcile rather than retry."""

    def __init__(self, call_id: str) -> None:
        Exception.__init__(self, "Filesystem outcome audit failed after execution")
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
        raise ValueError("Expected exact contract")
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

    @field_validator("operation", mode="before", check_fields=False)
    @classmethod
    def operation_member(cls, value: object) -> object:
        return _enum(value, FilesystemOperation)


def _path(value: str, operation: FilesystemOperation) -> str:
    if value == "." and operation in (
        FilesystemOperation.LIST_DIRECTORY,
        FilesystemOperation.STAT,
    ):
        return value
    if not value or any(char in value for char in '\x00\\:*?<>|"'):
        raise ValueError("Invalid workspace-relative path")
    devices = {"CON", "PRN", "AUX", "NUL"} | {
        f"{prefix}{suffix}" for prefix in ("COM", "LPT") for suffix in "123456789¹²³"
    }
    for part in value.split("/"):
        if (
            part in ("", ".", "..")
            or part.endswith((".", " "))
            or part.split(".", 1)[0].rstrip(" ").upper() in devices
        ):
            raise ValueError("Invalid workspace-relative path component")
    return value


class FilesystemCall(_Contract):
    call_id: _Id = Field(default_factory=lambda: str(uuid4()))
    operation: FilesystemOperation
    relative_path: Annotated[str, StringConstraints(strict=True, min_length=1)]
    content: Annotated[str, StringConstraints(strict=True)] | None = None
    max_bytes: Annotated[int, Field(strict=True, ge=1, le=_MAX_BYTES)] | None = None
    max_entries: Annotated[int, Field(strict=True, ge=1, le=1000)] | None = None

    @model_validator(mode="after")
    def call_shape(self) -> Self:
        _path(self.relative_path, self.operation)
        text = self.operation in (
            FilesystemOperation.CREATE_TEXT,
            FilesystemOperation.WRITE_TEXT,
        )
        if (
            (self.content is not None) != text
            or (
                self.max_bytes is not None
                and self.operation != FilesystemOperation.READ_TEXT
            )
            or (
                self.max_entries is not None
                and self.operation != FilesystemOperation.LIST_DIRECTORY
            )
        ):
            raise ValueError("Operation fields disagree")
        if self.content is not None:
            try:
                size = len(self.content.encode("utf-8"))
            except UnicodeError:
                raise ValueError("Content must be valid UTF-8 text") from None
            if size > _MAX_BYTES:
                raise ValueError("Text exceeds byte bound")
        return self


def filesystem_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Pure canonical metadata, in declaration order; no executable handlers."""
    risks = ("P1", "P1", "P1", "P2", "P2", "P3", "P4")
    descriptions = (
        "Read bounded UTF-8 text from one workspace file as untrusted data.",
        "List bounded, sorted immediate workspace directory entries.",
        "Inspect metadata of one workspace file or directory without content.",
        "Exclusively create one bounded UTF-8 workspace file.",
        "Create one workspace directory without creating parents.",
        "Replace bounded UTF-8 content of one existing workspace file.",
        "Delete one regular workspace file with request-specific approval.",
    )
    outputs = (
        {"content": {"type": "string"}, "size_bytes": {"type": "integer"}},
        {
            "entries": {
                "type": "array",
                "maxItems": 1000,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"enum": ["file", "directory", "symlink", "other"]},
                        "size_bytes": {"type": ["integer", "null"]},
                    },
                    "required": ["name", "kind", "size_bytes"],
                    "additionalProperties": False,
                },
            },
            "truncated": {"type": "boolean"},
        },
        {
            "kind": {"enum": ["file", "directory", "other"]},
            "size_bytes": {"type": "integer"},
            "modified_at": {"type": "string", "format": "date-time"},
        },
        {"bytes_written": {"type": "integer", "maximum": _MAX_BYTES}},
        {"created": {"const": True}},
        {"bytes_written": {"type": "integer", "maximum": _MAX_BYTES}},
        {"deleted": {"const": True}},
    )
    result: list[ToolDefinition] = []
    for operation, risk, description, output in zip(
        FilesystemOperation, risks, descriptions, outputs, strict=True
    ):
        properties: dict[str, object] = {
            "call_id": {"type": "string", "minLength": 1},
            "operation": {"const": operation.value},
            "relative_path": {"type": "string", "minLength": 1},
        }
        required = ["operation", "relative_path"]
        if operation in (
            FilesystemOperation.CREATE_TEXT,
            FilesystemOperation.WRITE_TEXT,
        ):
            properties["content"] = {"type": "string", "description": "UTF-8 <= 1 MiB"}
            required.append("content")
        if operation == FilesystemOperation.READ_TEXT:
            properties["max_bytes"] = {
                "type": "integer",
                "minimum": 1,
                "maximum": _MAX_BYTES,
                "default": _MAX_BYTES,
            }
        if operation == FilesystemOperation.LIST_DIRECTORY:
            properties["max_entries"] = {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "default": 200,
            }
        result.append(
            ToolDefinition(
                tool_id=f"filesystem.{operation.value}",
                description=description,
                input_schema={
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": output,
                    "required": list(cast(Mapping[str, object], output)),
                    "additionalProperties": False,
                },
                risk_level=ToolRiskLevel(risk),
                permission_requirement=f"{risk}: exact workspace action/resource scope",
                timeout_seconds=10,
                cancellation_supported=False,
                audit_behavior="durable permission and outcome required"
                if risk in ("P3", "P4")
                else "no mandatory durable audit",
                supported_platforms=("Windows", "Linux", "macOS"),
                supported_environments=("local", "trusted-workspace"),
            )
        )
    return tuple(result)


def _definition(operation: FilesystemOperation) -> ToolDefinition:
    return filesystem_tool_definitions()[tuple(FilesystemOperation).index(operation)]


class FilesystemIntent(_Contract):
    call_id: _Id
    tool_id: _Id
    operation: FilesystemOperation
    action: _Id
    resource: _Id
    risk_level: ToolRiskLevel
    permission_requirement: _Id

    @field_validator("risk_level", mode="before")
    @classmethod
    def risk_member(cls, value: object) -> object:
        return _enum(value, ToolRiskLevel)

    @model_validator(mode="after")
    def canonical(self) -> Self:
        tool = _definition(self.operation)
        if (
            self.tool_id,
            self.action,
            self.risk_level,
            self.permission_requirement,
        ) != (
            tool.tool_id,
            self.operation.value,
            tool.risk_level,
            tool.permission_requirement,
        ):
            raise ValueError("Intent disagrees with canonical metadata")
        return self


def _freeze(value: object, active: set[int]) -> object:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and isfinite(value):
        return value
    if type(value) not in (dict, MappingProxyType, list, tuple) or id(value) in active:
        raise ValueError("Output requires acyclic ordinary JSON data")
    active.add(id(value))
    try:
        if isinstance(value, Mapping):
            if any(type(key) is not str for key in value):
                raise ValueError("Output keys must be strings")
            return MappingProxyType({k: _freeze(v, active) for k, v in value.items()})
        return tuple(_freeze(v, active) for v in cast(Sequence[object], value))
    finally:
        active.remove(id(value))


def _json(value: object) -> object:
    if isinstance(value, Mapping):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_json(v) for v in value]
    return value


class FilesystemToolResult(_Contract):
    call_id: _Id
    tool_id: _Id
    operation: FilesystemOperation
    resource: _Id
    status: ToolResultStatus
    attempted: bool = Field(strict=True)
    effect_applied: bool = Field(strict=True)
    permission_decision: PermissionDecision
    permission_audit_id: _Id | None = None
    outcome_audit_id: _Id | None = None
    output: Mapping[str, object] | None = None
    error_code: FilesystemErrorCode | None = None

    @field_validator("status", mode="before")
    @classmethod
    def status_member(cls, value: object) -> object:
        return _enum(value, ToolResultStatus)

    @field_validator("error_code", mode="before")
    @classmethod
    def error_member(cls, value: object) -> object:
        return _enum(value, FilesystemErrorCode) if value is not None else None

    @field_validator("permission_decision", mode="before")
    @classmethod
    def decision_snapshot(cls, value: object) -> PermissionDecision:
        return _validated(value, PermissionDecision)

    @field_validator("output", mode="plain")
    @classmethod
    def output_snapshot(cls, value: object) -> Mapping[str, object] | None:
        if value is None:
            return None
        if type(value) not in (dict, MappingProxyType):
            raise ValueError("Output must be a JSON object")
        try:
            return cast(Mapping[str, object], _freeze(value, set()))
        except RecursionError:
            raise ValueError("Output nesting exceeds validation depth") from None

    @field_serializer("output")
    def output_json(self, value: Mapping[str, object] | None) -> object:
        return _json(value)

    @model_validator(mode="after")
    def evidence_shape(self) -> Self:
        tool = _definition(self.operation)
        decision = self.permission_decision
        if (
            self.tool_id,
            decision.tool_id,
            decision.risk_level,
            decision.permission_requirement,
        ) != (tool.tool_id, tool.tool_id, tool.risk_level, tool.permission_requirement):
            raise ValueError("Result permission evidence disagrees with canonical tool")
        allowed = decision.status == PermissionStatus.ALLOWED
        if not allowed:
            if (
                self.status != ToolResultStatus.DENIED
                or self.attempted
                or self.effect_applied
                or self.output is not None
                or self.error_code is not None
            ):
                raise ValueError("Nonallowed result cannot report an attempt or output")
        elif not self.attempted or self.status not in (
            ToolResultStatus.SUCCESS,
            ToolResultStatus.PARTIAL_SUCCESS,
            ToolResultStatus.FAILURE,
        ):
            raise ValueError("Allowed result requires an explicit synchronous attempt")
        mandatory = audit_required_for_execution(tool.risk_level)
        if (
            (self.permission_audit_id is not None) != mandatory
            or (self.outcome_audit_id is not None) != (mandatory and allowed)
            or (
                self.outcome_audit_id is not None
                and self.outcome_audit_id == self.permission_audit_id
            )
        ):
            raise ValueError("Result lacks distinct required audit evidence")
        if allowed:
            success = self.status in (
                ToolResultStatus.SUCCESS,
                ToolResultStatus.PARTIAL_SUCCESS,
            )
            mutation = tool.risk_level in (
                ToolRiskLevel.P2,
                ToolRiskLevel.P3,
                ToolRiskLevel.P4,
            )
            if self.effect_applied and not mutation:
                raise ValueError("Observation cannot report mutation")
            if success:
                if (
                    self.output is None
                    or self.error_code is not None
                    or self.effect_applied != mutation
                ):
                    raise ValueError("Success evidence is contradictory")
                _check_output(self.operation, self.output, self.status)
            elif self.output is not None or self.error_code is None:
                raise ValueError("Failure needs a safe error and no output")
        return self


def _check_output(
    operation: FilesystemOperation,
    output: Mapping[str, object],
    status: ToolResultStatus,
) -> None:
    keys = cast(tuple[str, ...], _definition(operation).output_schema["required"])
    if set(output) != set(keys):
        raise ValueError("Output fields disagree with operation")
    for key in ("size_bytes", "bytes_written"):
        if key in output and (
            type(output[key]) is not int or cast(int, output[key]) < 0
        ):
            raise ValueError("Output byte count must be nonnegative integer")
    if "bytes_written" in output and cast(int, output["bytes_written"]) > _MAX_BYTES:
        raise ValueError("Output exceeds byte bound")
    if (
        status == ToolResultStatus.PARTIAL_SUCCESS
        and operation != FilesystemOperation.LIST_DIRECTORY
    ):
        raise ValueError("Only bounded directory listing reports partial success")
    if operation == FilesystemOperation.READ_TEXT:
        if type(output["content"]) is not str:
            raise ValueError("Read output requires text")
        if (
            len(output["content"].encode("utf-8")) != output["size_bytes"]
            or cast(int, output["size_bytes"]) > _MAX_BYTES
        ):
            raise ValueError("Read output byte count disagrees")
    elif operation == FilesystemOperation.LIST_DIRECTORY:
        entries = output["entries"]
        if type(entries) is not tuple or len(entries) > 1000:
            raise ValueError("Listing exceeds entry bound")
        names: list[str] = []
        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {
                "name",
                "kind",
                "size_bytes",
            }:
                raise ValueError("Invalid entry metadata")
            if (
                type(entry["name"]) is not str
                or not entry["name"]
                or entry["kind"] not in ("file", "directory", "symlink", "other")
            ):
                raise ValueError("Invalid entry name or kind")
            if entry["size_bytes"] is not None and (
                type(entry["size_bytes"]) is not int or entry["size_bytes"] < 0
            ):
                raise ValueError("Invalid entry byte count")
            names.append(entry["name"])
        if (
            names != sorted(set(names))
            or type(output["truncated"]) is not bool
            or output["truncated"] != (status == ToolResultStatus.PARTIAL_SUCCESS)
        ):
            raise ValueError("Listing ordering or truncation disagrees")
    elif operation == FilesystemOperation.STAT:
        if (
            output["kind"] not in ("file", "directory", "other")
            or type(output["modified_at"]) is not str
        ):
            raise ValueError("Invalid stat metadata")
        modified = datetime.fromisoformat(output["modified_at"])
        offset = modified.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("Stat timestamp must be UTC")
    elif operation == FilesystemOperation.CREATE_DIRECTORY:
        if output["created"] is not True:
            raise ValueError("Directory creation evidence required")
    elif operation == FilesystemOperation.DELETE_FILE and output["deleted"] is not True:
        raise ValueError("File deletion evidence required")


class _Failure(Exception):
    def __init__(
        self, code: FilesystemErrorCode, *, effect_applied: bool = False
    ) -> None:
        self.code = code
        self.effect_applied = effect_applied


def _link(info: os.stat_result) -> bool:
    # Reject every Windows reparse point conservatively, including junctions.
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _kind(info: os.stat_result) -> str:
    if _link(info):
        return "symlink"
    if stat.S_ISREG(info.st_mode):
        return "file"
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    return "other"


def _target(root: Path, relative: str) -> tuple[Path, os.stat_result | None]:
    try:
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            raise _Failure(FilesystemErrorCode.ROOT_UNAVAILABLE)
    except (OSError, RuntimeError):
        raise _Failure(FilesystemErrorCode.ROOT_UNAVAILABLE) from None
    current = resolved
    info = current.lstat()
    parts = () if relative == "." else tuple(relative.split("/"))
    for index, part in enumerate(parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if index == len(parts) - 1:
                return current, None
            raise
        if _link(info):
            raise _Failure(FilesystemErrorCode.SYMLINK_FORBIDDEN)
        if not current.resolve(strict=True).is_relative_to(resolved):
            raise _Failure(FilesystemErrorCode.PATH_ESCAPE)
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise _Failure(FilesystemErrorCode.NOT_DIRECTORY)
    return current, info


def _os_code(error: OSError) -> FilesystemErrorCode:
    if isinstance(error, FileNotFoundError):
        return FilesystemErrorCode.NOT_FOUND
    if isinstance(error, FileExistsError):
        return FilesystemErrorCode.ALREADY_EXISTS
    if isinstance(error, NotADirectoryError):
        return FilesystemErrorCode.NOT_DIRECTORY
    if isinstance(error, IsADirectoryError):
        return FilesystemErrorCode.NOT_FILE
    return FilesystemErrorCode.IO_FAILURE


def _attempt(
    root: Path, call: FilesystemCall
) -> tuple[
    ToolResultStatus, bool, Mapping[str, object] | None, FilesystemErrorCode | None
]:
    effect = False
    try:
        target, info = _target(root, call.relative_path)
        operation = call.operation
        create = operation in (
            FilesystemOperation.CREATE_TEXT,
            FilesystemOperation.CREATE_DIRECTORY,
        )
        if create:
            if info is not None:
                raise _Failure(FilesystemErrorCode.ALREADY_EXISTS)
        elif info is None:
            raise _Failure(FilesystemErrorCode.NOT_FOUND)
        if operation == FilesystemOperation.LIST_DIRECTORY:
            if info is None or not stat.S_ISDIR(info.st_mode):
                raise _Failure(FilesystemErrorCode.NOT_DIRECTORY)
            bound = call.max_entries or 200
            # Keep only bound+1 names in memory, independent of directory size.
            with os.scandir(target) as children:
                names = heapq.nsmallest(bound + 1, (child.name for child in children))
            entries = []
            for name in names[:bound]:
                child_info = (target / name).lstat()
                entries.append(
                    {
                        "name": name,
                        "kind": _kind(child_info),
                        "size_bytes": None if _link(child_info) else child_info.st_size,
                    }
                )
            truncated = len(names) > bound
            return (
                ToolResultStatus.PARTIAL_SUCCESS
                if truncated
                else ToolResultStatus.SUCCESS,
                False,
                {"entries": entries, "truncated": truncated},
                None,
            )
        if operation == FilesystemOperation.STAT:
            assert info is not None
            output = {
                "kind": _kind(info),
                "size_bytes": info.st_size,
                "modified_at": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
            }
        elif operation == FilesystemOperation.CREATE_DIRECTORY:
            target.mkdir()
            effect = True
            output = {"created": True}
        else:
            if not create and (info is None or not stat.S_ISREG(info.st_mode)):
                raise _Failure(FilesystemErrorCode.NOT_FILE)
            if operation == FilesystemOperation.READ_TEXT:
                bound = call.max_bytes or _MAX_BYTES
                assert info is not None
                if info.st_size > bound:
                    raise _Failure(FilesystemErrorCode.LIMIT_EXCEEDED)
                with target.open("rb") as source:
                    raw = source.read(bound)
                    if os.fstat(source.fileno()).st_size > bound:
                        raise _Failure(FilesystemErrorCode.LIMIT_EXCEEDED)
                try:
                    content = raw.decode("utf-8", errors="strict")
                except UnicodeError:
                    raise _Failure(FilesystemErrorCode.INVALID_UTF8) from None
                output = {"content": content, "size_bytes": len(raw)}
            elif operation == FilesystemOperation.DELETE_FILE:
                target.unlink()
                effect = True
                output = {"deleted": True}
            else:
                assert call.content is not None
                payload = call.content.encode("utf-8")
                if create:
                    # Opening exclusively can itself create the file even if
                    # the write/close later fails; retain truthful effect evidence.
                    with target.open("xb") as destination:
                        effect = True
                        destination.write(payload)
                else:
                    _replace_text(target, payload)
                    effect = True
                output = {"bytes_written": len(payload)}
        return ToolResultStatus.SUCCESS, effect, output, None
    except _Failure as error:
        return (
            ToolResultStatus.FAILURE,
            effect or error.effect_applied,
            None,
            error.code,
        )
    except OSError as error:
        return ToolResultStatus.FAILURE, effect, None, _os_code(error)
    except (ValueError, OverflowError, RuntimeError):
        return ToolResultStatus.FAILURE, effect, None, FilesystemErrorCode.IO_FAILURE


def _replace_text(target: Path, payload: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".phoenix-write-", delete=False
        ) as destination:
            temporary = Path(destination.name)
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Best effort cleanup; never retry the requested mutation.
                pass


@dataclass(frozen=True, slots=True, init=False)
class FilesystemTool:
    """Explicit trusted-root capability; only execute performs authorized I/O."""

    _root: Path
    _registry: ToolRegistry
    _permissions: PermissionEngine
    _audit: AuditTrail

    def __init__(
        self,
        root: str | Path,
        registry: ToolRegistry,
        policy: PermissionPolicy,
        audit_trail: AuditTrail,
    ) -> None:
        if hasattr(self, "_root"):
            raise FilesystemInputError("Filesystem tool is already initialized")
        try:
            if type(root) not in (str, type(Path())) or "\x00" in str(root):
                raise ValueError("Expected explicit absolute workspace root")
            path = Path(root)
            if not path.is_absolute():
                raise ValueError("Expected explicit absolute workspace root")
            path = Path(os.path.normpath(str(path)))
            if (
                type(registry) is not ToolRegistry
                or type(audit_trail) is not AuditTrail
            ):
                raise ValueError("Expected exact authority dependencies")
            snapshot = ToolRegistry(registry.list_tools())
            for canonical in filesystem_tool_definitions():
                registered = snapshot.require(canonical.tool_id)
                _enum(registered.risk_level, ToolRiskLevel)
                if registered != canonical:
                    raise ValueError("Noncanonical filesystem definition")
            permissions = PermissionEngine(
                snapshot, _validated(policy, PermissionPolicy)
            )
        except (ValueError, ToolRegistryError):
            raise FilesystemInputError(
                "Invalid filesystem configuration or authority"
            ) from None
        object.__setattr__(self, "_root", path)
        object.__setattr__(self, "_registry", snapshot)
        object.__setattr__(self, "_permissions", permissions)
        object.__setattr__(self, "_audit", audit_trail)

    def describe(self, call: FilesystemCall) -> FilesystemIntent:
        try:
            accepted = _validated(call, FilesystemCall)
            tool = self._registry.require(f"filesystem.{accepted.operation.value}")
            target = (
                self._root
                if accepted.relative_path == "."
                else self._root.joinpath(*accepted.relative_path.split("/"))
            )
            return FilesystemIntent(
                call_id=accepted.call_id,
                tool_id=tool.tool_id,
                operation=accepted.operation,
                action=accepted.operation.value,
                resource=str(target),
                risk_level=tool.risk_level,
                permission_requirement=tool.permission_requirement,
            )
        except (ValueError, ToolRegistryError):
            raise FilesystemInputError("Invalid filesystem call or intent") from None

    def execute(
        self,
        call: FilesystemCall,
        request: PermissionRequest,
        *,
        grants: Sequence[PermissionGrant] = (),
        user_decision: UserPermissionDecision | None = None,
    ) -> FilesystemToolResult:
        try:
            call = _validated(call, FilesystemCall)
            intent = self.describe(call)
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
        except (ValueError, ToolRegistryError):
            raise FilesystemInputError(
                "Invalid filesystem permission evidence or correlation"
            ) from None
        permission_id = None
        outcome_id = None
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
                raise FilesystemAuditError(call.call_id) from None
        allowed = decision.status == PermissionStatus.ALLOWED
        if allowed:
            status, effect, output, error = _attempt(self._root, call)
            if required:
                try:
                    outcome_id = self._audit.record_tool_outcome(
                        request,
                        decision,
                        status,
                        occurred_at=datetime.now(UTC),
                        correlation_id=call.call_id,
                    ).audit_id
                except AuditError:
                    raise FilesystemPostExecutionAuditError(call.call_id) from None
        else:
            status, effect, output, error = ToolResultStatus.DENIED, False, None, None
        return FilesystemToolResult(
            call_id=call.call_id,
            tool_id=intent.tool_id,
            operation=call.operation,
            resource=intent.resource,
            status=status,
            attempted=allowed,
            effect_applied=effect,
            permission_decision=decision,
            permission_audit_id=permission_id,
            outcome_audit_id=outcome_id,
            output=output,
            error_code=error,
        )
