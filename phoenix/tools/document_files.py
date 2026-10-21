"""PHX-113 permissioned workspace documents: parse to text, or create new files.

``document.read`` (P1) reads one PDF, DOCX, Markdown or CSV file inside the
explicit workspace root and returns its text through the PHX-072 parsers. The
text is untrusted data: callers must only show it to models as guarded tool
output (INV-017). ``document.create`` (P2, docs/08 "create new safe output")
generates one Markdown or DOCX file through the PHX-073 generator, or one CSV
file through the standard library, and creates it exclusively: an existing
target is never overwritten (``already_exists``, no effect). There is no
overwrite, delete or directory creation. After writing, the file is read back
and its bytes compared before success is reported (docs/19).

Workspace rules are FilesystemTool's own (PHX-048): the same path validator,
the root checked on every call, links and junctions refused, containment
checked after resolution. PermissionEngine decides every call; nothing runs
unless it returns ALLOWED. P1/P2 calls write no audit rows (INV-007 covers
P3/P4). Errors are closed codes without content or native diagnostics. A call
cancelled while its thread is writing cannot be undone: the file may exist.
Like the PHX-072/073 components it owns, one tool serves one event loop.
"""

import asyncio
import csv
import io
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Self, cast
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

from phoenix.documents.generation import (
    DocumentFormat,
    DocumentGenerationError,
    DocumentGenerationErrorCode,
    DocumentGenerationLimits,
    DocumentGenerationRequest,
    DocumentGenerator,
    ParagraphBlock,
    ReportDocument,
    ReportSection,
)
from phoenix.documents.parsers import (
    UploadDocument,
    UploadParserError,
    UploadParserErrorCode,
    UploadParserLimits,
    UploadRouter,
)
from phoenix.security.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionGrant,
    PermissionPolicy,
    PermissionRequest,
    PermissionStatus,
)

# FilesystemTool's own workspace rules, reused rather than re-implemented.
from phoenix.tools.filesystem import (
    FilesystemErrorCode,
    FilesystemOperation,
    _Failure,
    _os_code,
    _path,
    _target,
)
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolRegistryError,
    ToolResultStatus,
    ToolRiskLevel,
)

_Id = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Positive = Annotated[int, Field(strict=True, gt=0)]
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
# OWASP CSV injection: a cell starting with one of these may run as a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class DocumentFileOperation(StrEnum):
    READ = "read"
    CREATE = "create"


class DocumentFileFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    MARKDOWN = "markdown"
    CSV = "csv"


_FORMATS = {
    ".pdf": DocumentFileFormat.PDF,
    ".docx": DocumentFileFormat.DOCX,
    ".md": DocumentFileFormat.MARKDOWN,
    ".markdown": DocumentFileFormat.MARKDOWN,
    ".csv": DocumentFileFormat.CSV,
}
READ_EXTENSIONS: tuple[str, ...] = (".pdf", ".docx", ".md", ".markdown", ".csv")
CREATE_EXTENSIONS: tuple[str, ...] = (".md", ".docx", ".csv")


class DocumentFileErrorCode(StrEnum):
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    NOT_FILE = "not_file"
    NOT_DIRECTORY = "not_directory"
    LIMIT_EXCEEDED = "limit_exceeded"
    PATH_ESCAPE = "path_escape"
    SYMLINK_FORBIDDEN = "symlink_forbidden"
    ROOT_UNAVAILABLE = "root_unavailable"
    IO_FAILURE = "io_failure"
    UNSUPPORTED_FORMAT = "unsupported_format"
    MALFORMED_DOCUMENT = "malformed_document"
    ENCRYPTED_DOCUMENT = "encrypted_document"
    NO_EXTRACTABLE_TEXT = "no_extractable_text"
    GENERATION_FAILED = "generation_failed"
    VERIFICATION_FAILED = "verification_failed"


class DocumentFileInputError(ValueError):
    """Invalid configuration, call or permission evidence; nothing was attempted."""


def _extension(relative_path: str) -> str:
    name = relative_path.rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _plain(value: str, bound: int) -> str:
    if len(value) > bound or any(
        (ord(c) < 32 and c not in "\t\n\r") or 127 <= ord(c) <= 159 for c in value
    ):
        raise ValueError("Text must be bounded plain text")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("Text must be valid UTF-8") from None
    return value


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        revalidate_instances="always",
        hide_input_in_errors=True,
    )

    @model_validator(mode="wrap")
    @classmethod
    def snapshot(cls, value: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        if isinstance(value, BaseModel):
            if type(value) is not cls:
                raise ValueError("Expected the exact contract type")
            value = {**vars(value), **(value.__pydantic_extra__ or {})}
        return handler(value)

    @field_validator("operation", mode="before", check_fields=False)
    @classmethod
    def operation_member(cls, value: object) -> object:
        if isinstance(value, DocumentFileOperation):
            return value
        if type(value) is not str:
            raise ValueError("Expected an operation")
        return DocumentFileOperation(value)


class DocumentSectionSpec(_Contract):
    """One generated section: a heading and its plain paragraphs (never markup)."""

    heading: Annotated[str, StringConstraints(strict=True, min_length=1)]
    paragraphs: Annotated[tuple[str, ...], Field(min_length=1, max_length=50)]

    @model_validator(mode="after")
    def plain_text(self) -> Self:
        if not self.heading.strip():
            raise ValueError("Heading must be meaningful")
        _plain(self.heading, 200)
        for paragraph in self.paragraphs:
            if not paragraph.strip():
                raise ValueError("Paragraph must be meaningful")
            _plain(paragraph, 5000)
        return self


class DocumentFileCall(_Contract):
    call_id: _Id = Field(default_factory=lambda: str(uuid4()))
    operation: DocumentFileOperation
    relative_path: Annotated[str, StringConstraints(strict=True, min_length=1)]
    title: str | None = Field(default=None, repr=False)
    sections: tuple[DocumentSectionSpec, ...] | None = Field(default=None, repr=False)
    rows: tuple[tuple[str, ...], ...] | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def call_shape(self) -> Self:
        # Same component rules as FilesystemTool; "." never names a document.
        _path(self.relative_path, FilesystemOperation.READ_TEXT)
        name = self.relative_path.rsplit("/", 1)[-1]
        if ".." in name or len(name) > 255:
            raise ValueError("Invalid document file name")
        extension = _extension(self.relative_path)
        content = (self.title, self.sections, self.rows)
        if self.operation == DocumentFileOperation.READ:
            if extension not in READ_EXTENSIONS or content != (None, None, None):
                raise ValueError("Read takes one supported document path only")
            return self
        if extension not in CREATE_EXTENSIONS:
            raise ValueError("Create supports Markdown, DOCX and CSV only")
        if extension == ".csv":
            if self.title is not None or self.sections is not None or not self.rows:
                raise ValueError("CSV creation takes rows only")
            width = len(self.rows[0])
            if not 1 <= width <= 50 or len(self.rows) > 1000:
                raise ValueError("CSV rows exceed the shape bound")
            for row in self.rows:
                if len(row) != width:
                    raise ValueError("CSV rows must have equal width")
                for cell in row:
                    _plain(cell, 2000)
            return self
        if self.rows is not None or self.title is None or not self.sections:
            raise ValueError("Document creation takes a title and sections")
        if not self.title.strip() or len(self.sections) > 20:
            raise ValueError("Document title or sections out of bounds")
        _plain(self.title, 200)
        return self


class DocumentFileLimits(_Contract):
    """Explicit bounds; parsed text over max_text_chars fails, never truncates."""

    max_source_bytes: _Positive = 4 * 1024 * 1024
    max_text_chars: _Positive = 20000
    max_output_bytes: _Positive = 1024 * 1024


def _risk(operation: DocumentFileOperation) -> ToolRiskLevel:
    return (
        ToolRiskLevel.P1
        if operation == DocumentFileOperation.READ
        else ToolRiskLevel.P2
    )


def document_file_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Pure canonical metadata in declaration order; no executable handlers."""
    descriptions = {
        DocumentFileOperation.READ: (
            "Parse one PDF, DOCX, Markdown or CSV workspace file to untrusted text."
        ),
        DocumentFileOperation.CREATE: (
            "Exclusively create one Markdown, DOCX or CSV workspace file;"
            " never overwrites."
        ),
    }
    schema = DocumentFileCall.model_json_schema()
    result: list[ToolDefinition] = []
    for operation in DocumentFileOperation:
        risk = _risk(operation)
        result.append(
            ToolDefinition(
                tool_id=f"document.{operation.value}",
                description=descriptions[operation],
                input_schema=schema,
                output_schema={"type": "object"},
                risk_level=risk,
                permission_requirement=f"{risk.value}: exact workspace document scope",
                timeout_seconds=30,
                cancellation_supported=False,
                audit_behavior="no mandatory durable audit",
                supported_platforms=("Windows", "Linux", "macOS"),
                supported_environments=("local", "trusted-workspace"),
            )
        )
    return tuple(result)


def _definition(operation: DocumentFileOperation) -> ToolDefinition:
    return document_file_tool_definitions()[
        tuple(DocumentFileOperation).index(operation)
    ]


class DocumentFileIntent(_Contract):
    call_id: _Id
    tool_id: _Id
    operation: DocumentFileOperation
    action: _Id
    resource: _Id
    risk_level: ToolRiskLevel
    permission_requirement: _Id

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


_READ_KEYS = frozenset({"text", "format", "segments", "size_bytes"})
_CREATE_KEYS = frozenset({"format", "bytes_written", "sha256", "verified"})


class DocumentFileResult(_Contract):
    call_id: _Id
    tool_id: _Id
    operation: DocumentFileOperation
    resource: _Id
    status: ToolResultStatus
    attempted: bool = Field(strict=True)
    effect_applied: bool = Field(strict=True)
    permission_decision: PermissionDecision
    output: Mapping[str, object] | None = Field(default=None, repr=False)
    error_code: DocumentFileErrorCode | None = None

    @field_validator("permission_decision", mode="before")
    @classmethod
    def decision_snapshot(cls, value: object) -> PermissionDecision:
        return PermissionDecision.model_validate(value)

    @field_validator("output", mode="plain")
    @classmethod
    def output_snapshot(cls, value: object) -> Mapping[str, object] | None:
        if value is None:
            return None
        if type(value) not in (dict, MappingProxyType):
            raise ValueError("Output must be an object")
        return MappingProxyType(dict(cast(Mapping[str, object], value)))

    @field_serializer("output")
    def output_json(self, value: Mapping[str, object] | None) -> object:
        return None if value is None else dict(value)

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
        if decision.status != PermissionStatus.ALLOWED:
            if (
                self.status != ToolResultStatus.DENIED
                or self.attempted
                or self.effect_applied
                or self.output is not None
                or self.error_code is not None
            ):
                raise ValueError("Nonallowed result cannot report an attempt or output")
            return self
        if not self.attempted:
            raise ValueError("Allowed result requires an attempt")
        create = self.operation == DocumentFileOperation.CREATE
        if self.status == ToolResultStatus.SUCCESS:
            output = self.output
            if output is None or self.error_code is not None:
                raise ValueError("Success needs output and no error")
            if create:
                if (
                    set(output) != _CREATE_KEYS
                    or output["verified"] is not True
                    or not self.effect_applied
                    or type(output["bytes_written"]) is not int
                    or type(output["sha256"]) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", str(output["sha256"])) is None
                ):
                    raise ValueError("Create success evidence is contradictory")
            elif (
                set(output) != _READ_KEYS
                or self.effect_applied
                or type(output["text"]) is not str
                or not str(output["text"]).strip()
                or type(output["segments"]) is not int
                or type(output["size_bytes"]) is not int
            ):
                raise ValueError("Read success evidence is contradictory")
            if output["format"] not in {item.value for item in DocumentFileFormat}:
                raise ValueError("Unknown document format")
        elif self.status == ToolResultStatus.FAILURE:
            if self.output is not None or self.error_code is None:
                raise ValueError("Failure needs a closed error and no output")
            if self.effect_applied and not create:
                raise ValueError("A read cannot report an effect")
        else:
            raise ValueError("Allowed result is success or failure")
        return self


_FILESYSTEM_CODES = {
    FilesystemErrorCode.NOT_FOUND: DocumentFileErrorCode.NOT_FOUND,
    FilesystemErrorCode.ALREADY_EXISTS: DocumentFileErrorCode.ALREADY_EXISTS,
    FilesystemErrorCode.NOT_FILE: DocumentFileErrorCode.NOT_FILE,
    FilesystemErrorCode.NOT_DIRECTORY: DocumentFileErrorCode.NOT_DIRECTORY,
    FilesystemErrorCode.LIMIT_EXCEEDED: DocumentFileErrorCode.LIMIT_EXCEEDED,
    FilesystemErrorCode.PATH_ESCAPE: DocumentFileErrorCode.PATH_ESCAPE,
    FilesystemErrorCode.SYMLINK_FORBIDDEN: DocumentFileErrorCode.SYMLINK_FORBIDDEN,
    FilesystemErrorCode.ROOT_UNAVAILABLE: DocumentFileErrorCode.ROOT_UNAVAILABLE,
}
_PARSER_CODES = {
    UploadParserErrorCode.UNSUPPORTED_FORMAT: DocumentFileErrorCode.UNSUPPORTED_FORMAT,
    UploadParserErrorCode.MEDIA_TYPE_MISMATCH: DocumentFileErrorCode.UNSUPPORTED_FORMAT,
    UploadParserErrorCode.PARSER_UNAVAILABLE: DocumentFileErrorCode.UNSUPPORTED_FORMAT,
    UploadParserErrorCode.SOURCE_TOO_LARGE: DocumentFileErrorCode.LIMIT_EXCEEDED,
    UploadParserErrorCode.LIMIT_EXCEEDED: DocumentFileErrorCode.LIMIT_EXCEEDED,
    UploadParserErrorCode.ENCRYPTED_DOCUMENT: DocumentFileErrorCode.ENCRYPTED_DOCUMENT,
    UploadParserErrorCode.NO_EXTRACTABLE_TEXT: (
        DocumentFileErrorCode.NO_EXTRACTABLE_TEXT
    ),
}


class _Fail(Exception):
    def __init__(self, code: DocumentFileErrorCode, *, effect: bool = False) -> None:
        self.code = code
        self.effect = effect


def _workspace_failure(error: BaseException) -> _Fail:
    if isinstance(error, _Failure):
        return _Fail(
            _FILESYSTEM_CODES.get(error.code, DocumentFileErrorCode.IO_FAILURE)
        )
    if isinstance(error, OSError):
        return _Fail(
            _FILESYSTEM_CODES.get(_os_code(error), DocumentFileErrorCode.IO_FAILURE)
        )
    return _Fail(DocumentFileErrorCode.IO_FAILURE)


def _read_bytes(root: Path, relative: str, bound: int) -> bytes:
    try:
        target, info = _target(root, relative)
        if info is None:
            raise _Fail(DocumentFileErrorCode.NOT_FOUND)
        if not stat.S_ISREG(info.st_mode):
            raise _Fail(DocumentFileErrorCode.NOT_FILE)
        if info.st_size > bound:
            raise _Fail(DocumentFileErrorCode.LIMIT_EXCEEDED)
        with target.open("rb") as source:
            data = source.read(bound + 1)
        if len(data) > bound:
            raise _Fail(DocumentFileErrorCode.LIMIT_EXCEEDED)
        if not data:
            raise _Fail(DocumentFileErrorCode.NO_EXTRACTABLE_TEXT)
        return data
    except _Fail:
        raise
    except (_Failure, OSError, ValueError, RuntimeError) as error:
        raise _workspace_failure(error) from None


def _absent(root: Path, relative: str) -> None:
    try:
        _, info = _target(root, relative)
    except (_Failure, OSError, ValueError, RuntimeError) as error:
        raise _workspace_failure(error) from None
    if info is not None:
        raise _Fail(DocumentFileErrorCode.ALREADY_EXISTS)


def _create_bytes(root: Path, relative: str, payload: bytes) -> str:
    """Exclusive create (never overwrite), then read back and compare."""
    created = False
    try:
        target, info = _target(root, relative)
        if info is not None:
            raise _Fail(DocumentFileErrorCode.ALREADY_EXISTS)
        # "x" fails if the target appeared after the check: still no overwrite.
        with target.open("xb") as destination:
            created = True
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        checked, after = _target(root, relative)
        if after is None or not stat.S_ISREG(after.st_mode):
            raise _Fail(DocumentFileErrorCode.VERIFICATION_FAILED, effect=True)
        with checked.open("rb") as source:
            echoed = source.read(len(payload) + 1)
        if echoed != payload:
            raise _Fail(DocumentFileErrorCode.VERIFICATION_FAILED, effect=True)
        return sha256(echoed).hexdigest()
    except _Fail as failure:
        failure.effect = failure.effect or created
        raise
    except (_Failure, OSError, ValueError, RuntimeError) as error:
        workspace = _workspace_failure(error)
        workspace.effect = created
        raise workspace from None


def _csv_cell(value: str) -> str:
    if value.startswith(_FORMULA_PREFIXES) and not _NUMBER.fullmatch(value):
        return "'" + value
    return value


def _csv_bytes(rows: Sequence[Sequence[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in rows:
        writer.writerow([_csv_cell(cell) for cell in row])
    return buffer.getvalue().encode("utf-8")


def _segment_text(page: int | None, text: str) -> str:
    # PDF pages keep their 1-based provenance; other formats are in order.
    return text if page is None else f"[page {page}] {text}"


@dataclass(frozen=True, slots=True, init=False)
class DocumentFileTool:
    """Explicit trusted-root capability; only execute performs authorized I/O."""

    _root: Path
    _registry: ToolRegistry
    _permissions: PermissionEngine
    _limits: DocumentFileLimits
    _router: UploadRouter
    _generator: DocumentGenerator

    def __init__(
        self,
        root: str | Path,
        registry: ToolRegistry,
        policy: PermissionPolicy,
        *,
        limits: DocumentFileLimits | None = None,
    ) -> None:
        if hasattr(self, "_root"):
            raise DocumentFileInputError("Document tool is already initialized")
        try:
            if type(root) not in (str, type(Path())) or "\x00" in str(root):
                raise ValueError("Expected explicit absolute workspace root")
            path = Path(root)
            if not path.is_absolute():
                raise ValueError("Expected explicit absolute workspace root")
            path = Path(os.path.normpath(str(path)))
            if type(registry) is not ToolRegistry:
                raise ValueError("Expected exact ToolRegistry")
            snapshot = ToolRegistry(registry.list_tools())
            for canonical in document_file_tool_definitions():
                if snapshot.require(canonical.tool_id) != canonical:
                    raise ValueError("Noncanonical document definition")
            permissions = PermissionEngine(
                snapshot, PermissionPolicy.model_validate(policy)
            )
            bounds = (
                DocumentFileLimits()
                if limits is None
                else DocumentFileLimits.model_validate(limits)
            )
            router = UploadRouter(
                UploadParserLimits(
                    max_source_bytes=bounds.max_source_bytes,
                    max_text_chars=bounds.max_text_chars,
                    max_segments=2000,
                    max_pdf_pages=200,
                    max_pdf_content_stream_bytes=8 * 1024 * 1024,
                    max_docx_entries=500,
                    max_docx_member_uncompressed_bytes=8 * 1024 * 1024,
                    max_docx_total_uncompressed_bytes=32 * 1024 * 1024,
                    max_docx_compression_ratio=100,
                    max_csv_rows=1000,
                    max_csv_columns=50,
                    max_csv_cell_chars=2000,
                )
            )
            generator = DocumentGenerator(
                DocumentGenerationLimits(
                    max_total_input_chars=max(bounds.max_text_chars, 1000),
                    max_sections=20,
                    max_blocks=1000,
                    max_list_items=1,
                    max_table_rows=1,
                    max_table_columns=1,
                    max_cell_chars=1,
                    max_provenance_items=1,
                    max_output_bytes=bounds.max_output_bytes,
                    max_pdf_pages=1,
                )
            )
        except (ValueError, ToolRegistryError, UploadParserError):
            raise DocumentFileInputError(
                "Invalid document tool configuration or authority"
            ) from None
        object.__setattr__(self, "_root", path)
        object.__setattr__(self, "_registry", snapshot)
        object.__setattr__(self, "_permissions", permissions)
        object.__setattr__(self, "_limits", bounds)
        object.__setattr__(self, "_router", router)
        object.__setattr__(self, "_generator", generator)

    def describe(self, call: DocumentFileCall) -> DocumentFileIntent:
        try:
            accepted = DocumentFileCall.model_validate(call)
            tool = self._registry.require(f"document.{accepted.operation.value}")
            return DocumentFileIntent(
                call_id=accepted.call_id,
                tool_id=tool.tool_id,
                operation=accepted.operation,
                action=accepted.operation.value,
                resource=str(self._root.joinpath(*accepted.relative_path.split("/"))),
                risk_level=tool.risk_level,
                permission_requirement=tool.permission_requirement,
            )
        except (ValueError, ToolRegistryError):
            raise DocumentFileInputError("Invalid document call or intent") from None

    async def execute(
        self,
        call: DocumentFileCall,
        request: PermissionRequest,
        *,
        grants: Sequence[PermissionGrant] = (),
    ) -> DocumentFileResult:
        try:
            call = DocumentFileCall.model_validate(call)
            intent = self.describe(call)
            request = PermissionRequest.model_validate(request)
            if (request.tool_id, request.action, request.resource) != (
                intent.tool_id,
                intent.action,
                intent.resource,
            ):
                raise ValueError("Permission request does not correlate")
            decision = self._permissions.evaluate(request, grants=grants)
        except (ValueError, ToolRegistryError):
            raise DocumentFileInputError(
                "Invalid document permission evidence or correlation"
            ) from None
        fields: dict[str, object] = {
            "call_id": call.call_id,
            "tool_id": intent.tool_id,
            "operation": call.operation,
            "resource": intent.resource,
            "permission_decision": decision,
        }
        if decision.status != PermissionStatus.ALLOWED:
            return DocumentFileResult.model_validate(
                fields
                | {
                    "status": ToolResultStatus.DENIED,
                    "attempted": False,
                    "effect_applied": False,
                }
            )
        try:
            if call.operation == DocumentFileOperation.READ:
                output = await self._read(call)
                effect = False
            else:
                output = await self._create(call)
                effect = True
        except _Fail as failure:
            return DocumentFileResult.model_validate(
                fields
                | {
                    "status": ToolResultStatus.FAILURE,
                    "attempted": True,
                    "effect_applied": failure.effect,
                    "error_code": failure.code,
                }
            )
        return DocumentFileResult.model_validate(
            fields
            | {
                "status": ToolResultStatus.SUCCESS,
                "attempted": True,
                "effect_applied": effect,
                "output": output,
            }
        )

    async def _read(self, call: DocumentFileCall) -> dict[str, object]:
        data = await asyncio.to_thread(
            _read_bytes, self._root, call.relative_path, self._limits.max_source_bytes
        )
        name = call.relative_path.rsplit("/", 1)[-1]
        try:
            route = self._router.resolve(
                UploadDocument(
                    document_id=call.call_id, filename=name, content_bytes=data
                )
            )
            parsed = await route.parser.parse(route.source)
        except UploadParserError as error:
            if error.code == UploadParserErrorCode.CANCELLED:
                raise asyncio.CancelledError from None
            raise _Fail(
                _PARSER_CODES.get(error.code, DocumentFileErrorCode.MALFORMED_DOCUMENT)
            ) from None
        except Exception:
            raise _Fail(DocumentFileErrorCode.MALFORMED_DOCUMENT) from None
        text = "\n".join(
            _segment_text(segment.page, segment.text) for segment in parsed.segments
        )
        if len(text) > self._limits.max_text_chars:
            raise _Fail(DocumentFileErrorCode.LIMIT_EXCEEDED)
        return {
            "text": text,
            "format": _FORMATS[_extension(call.relative_path)].value,
            "segments": len(parsed.segments),
            "size_bytes": len(data),
        }

    async def _create(self, call: DocumentFileCall) -> dict[str, object]:
        fmt = _FORMATS[_extension(call.relative_path)]
        # Fail fast before generating; the exclusive open still decides races.
        await asyncio.to_thread(_absent, self._root, call.relative_path)
        if fmt == DocumentFileFormat.CSV:
            assert call.rows is not None
            payload = _csv_bytes(call.rows)
        else:
            assert call.title is not None and call.sections is not None
            try:
                artifact = await self._generator.generate(
                    DocumentGenerationRequest(
                        generation_id=call.call_id,
                        format=DocumentFormat.DOCX
                        if fmt == DocumentFileFormat.DOCX
                        else DocumentFormat.MARKDOWN,
                        suggested_filename=call.relative_path.rsplit("/", 1)[-1],
                        document=ReportDocument(
                            document_id=call.call_id,
                            title=call.title,
                            sections=tuple(
                                ReportSection(
                                    heading=section.heading,
                                    blocks=tuple(
                                        ParagraphBlock(kind="paragraph", text=text)
                                        for text in section.paragraphs
                                    ),
                                )
                                for section in call.sections
                            ),
                        ),
                    )
                )
            except DocumentGenerationError as error:
                if error.code == DocumentGenerationErrorCode.CANCELLED:
                    raise asyncio.CancelledError from None
                raise _Fail(
                    DocumentFileErrorCode.LIMIT_EXCEEDED
                    if error.code == DocumentGenerationErrorCode.LIMIT_EXCEEDED
                    else DocumentFileErrorCode.GENERATION_FAILED
                ) from None
            payload = artifact.artifact_bytes
        if len(payload) > self._limits.max_output_bytes:
            raise _Fail(DocumentFileErrorCode.LIMIT_EXCEEDED)
        digest = await asyncio.to_thread(
            _create_bytes, self._root, call.relative_path, payload
        )
        return {
            "format": fmt.value,
            "bytes_written": len(payload),
            "sha256": digest,
            "verified": True,
        }
