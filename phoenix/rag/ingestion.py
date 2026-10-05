"""Explicit admitted document ingestion; untrusted provenance grants no authority.

Callers supply bytes, parser, limits, schema setup and authorization. No source
reference is opened; concrete parsing, vectors, retrieval and citations are later.
"""

import asyncio
import hashlib
import json
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Annotated, Protocol, Self, TypeVar, cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ModelWrapValidatorHandler,
    StringConstraints,
    TypeAdapter,
    field_serializer,
    field_validator,
    model_validator,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from phoenix.agents.registry import (
    AgentDefinition,
    AgentRegistry,
    build_default_agent_registry,
)
from phoenix.core.event_bus import EventBus, EventBusFullError
from phoenix.core.events import EventEnvelope, EventType
from phoenix.persistence.documents import DocumentChunkRow, DocumentRow

_Identifier = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
_Hash = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    ),
]
_Positive = Annotated[int, Field(strict=True, ge=1)]
_Nonnegative = Annotated[int, Field(strict=True, ge=0)]
_identifier = TypeAdapter(_Identifier)
_Model = TypeVar("_Model", bound=BaseModel)


class DocumentIngestionError(ValueError):
    """Safe explicit ingestion failure, without private evidence or SQL."""


class DocumentInputError(DocumentIngestionError):
    """Invalid exact input or dependency contract."""


class DocumentLimitError(DocumentIngestionError):
    """Explicit source, parsed-character or retained-chunk bound exceeded."""


class DocumentParserError(DocumentIngestionError):
    """Parser failure or invalid/uncorrelated parsed evidence."""


class DocumentAdmissionError(DocumentIngestionError):
    """Policy failure or malformed/uncorrelated admission evidence."""


class DocumentSecretRejectedError(DocumentAdmissionError):
    """Secret rejection before acquiring a database session."""


class DocumentPolicyRejectedError(DocumentAdmissionError):
    """Policy rejection before acquiring a database session."""


class DocumentConflictError(DocumentIngestionError):
    """Existing durable document ID; original document/chunks are preserved."""


class DuplicateActiveDocumentError(DocumentIngestionError):
    """An active ID already belongs to another ingestion."""


class DocumentPersistenceError(DocumentIngestionError):
    """Atomic persistence failed; no silent repair or retry."""


class DocumentEventError(DocumentIngestionError):
    """Publication failed after durable document/chunk commit."""


def _snapshot(value: object, contract: type[BaseModel]) -> dict[str, object]:
    if type(value) is not contract or not isinstance(value, BaseModel):
        raise ValueError("Expected an exact document contract")
    return {**vars(value), **(value.__pydantic_extra__ or {})}


def _validated(value: object, contract: type[_Model]) -> _Model:
    return contract.model_validate(_snapshot(value, contract))


def _json_value(value: object, active: set[int] | None = None) -> JsonValue:
    # Match PHX-030's finite JSON snapshot semantics without memory-service coupling.
    if value is None or type(value) in (str, bool, int):
        return cast(JsonValue, value)
    if type(value) is float:
        if not isfinite(value):
            raise ValueError("Metadata numbers must be finite")
        return value
    if not isinstance(value, (Mapping, list, tuple)):
        raise ValueError("Metadata must contain JSON data only")
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError("Metadata cannot contain cycles")
    active.add(identity)
    try:
        if isinstance(value, Mapping):
            if any(type(key) is not str for key in value):
                raise ValueError("Metadata object keys must be strings")
            return {key: _json_value(item, active) for key, item in value.items()}
        return [_json_value(item, active) for item in value]
    finally:
        active.remove(identity)


def _freeze(value: JsonValue) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
    )

    @model_validator(mode="wrap")
    @classmethod
    def exact_snapshot(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        if isinstance(value, BaseModel):
            value = _snapshot(value, cls)
        return handler(value)

    @field_validator("metadata", mode="before", check_fields=False)
    @classmethod
    def json_object(cls, value: object) -> JsonValue:
        if not isinstance(value, Mapping):
            raise ValueError("Metadata must be a JSON object")
        return _json_value(value)

    @field_validator("metadata", check_fields=False)
    @classmethod
    def freeze_metadata(cls, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        return cast(Mapping[str, JsonValue], _freeze(dict(value)))

    @field_serializer("metadata", check_fields=False)
    def serialize_metadata(self, value: Mapping[str, JsonValue]) -> JsonValue:
        return _json_value(value)

    @field_validator(
        "source_timestamp", "indexed_at", mode="before", check_fields=False
    )
    @classmethod
    def datetime_input(cls, value: object) -> object:
        if value is not None and not isinstance(value, (datetime, str)):
            raise ValueError(
                "Timestamp requires an aware datetime or ISO representation"
            )
        return value

    @field_validator("source_timestamp", "indexed_at", check_fields=False)
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @field_validator("text", "content", "path", "section", check_fields=False)
    @classmethod
    def meaningful(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Expected meaningful text")
        return value

    @field_validator(
        "verdict", "event_delivery", "status", mode="before", check_fields=False
    )
    @classmethod
    def textual_enum(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("Expected a textual vocabulary value")
        return value


class DocumentSource(_Contract):
    document_id: _Identifier = Field(default_factory=lambda: str(uuid4()))
    source_type: _Identifier
    source_ref: _Identifier
    media_type: _Identifier
    content_bytes: Annotated[bytes, Field(strict=True, min_length=1)] = Field(
        repr=False
    )
    source_timestamp: AwareDatetime | None = None
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict, repr=False)


class DocumentIngestionLimits(_Contract):
    max_source_bytes: _Positive
    max_parsed_chars: _Positive
    max_chunks: _Positive
    chunk_size_chars: _Positive
    chunk_overlap_chars: _Nonnegative

    @model_validator(mode="after")
    def progress(self) -> Self:
        if self.chunk_overlap_chars >= self.chunk_size_chars:
            raise ValueError("Chunk overlap must be smaller than chunk size")
        return self


class ParsedSegment(_Contract):
    text: _Text = Field(repr=False)
    path: _Text | None = None
    page: _Positive | None = None
    section: _Text | None = None
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict, repr=False)


class ParsedDocument(_Contract):
    document_id: _Identifier
    parser_id: _Identifier
    parser_version: _Identifier
    segments: Annotated[tuple[ParsedSegment, ...], Field(min_length=1)]

    @field_validator("segments", mode="before")
    @classmethod
    def ordered_segments(cls, value: object) -> tuple[object, ...]:
        if not isinstance(value, (tuple, list)):
            raise ValueError("Segments require an ordered sequence")
        return tuple(
            _snapshot(segment, ParsedSegment)
            if isinstance(segment, BaseModel)
            else segment
            for segment in value
        )


class DocumentParser(Protocol):
    async def parse(self, source: DocumentSource) -> ParsedDocument:
        """Return untrusted parsed evidence or propagate failure/cancellation."""
        ...


class DocumentRecord(_Contract):
    id: _Identifier
    source_type: _Identifier
    source_ref: _Identifier
    media_type: _Identifier
    source_timestamp: AwareDatetime | None = None
    source_hash: _Hash
    parser_id: _Identifier
    parser_version: _Identifier
    chunk_count: _Positive
    indexed_at: AwareDatetime
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict, repr=False)


class DocumentChunkRecord(_Contract):
    id: _Identifier
    document_id: _Identifier
    chunk_index: _Nonnegative
    content: _Text = Field(repr=False)
    content_hash: _Hash
    path: _Text | None = None
    page: _Positive | None = None
    section: _Text | None = None
    source_timestamp: AwareDatetime | None = None
    parser_version: _Identifier
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict, repr=False)


class DocumentAdmissionVerdict(StrEnum):
    ACCEPTED = "accepted"
    REJECTED_SECRET = "rejected_secret"
    REJECTED_POLICY = "rejected_policy"


class DocumentAdmissionDecision(_Contract):
    document_id: _Identifier
    verdict: DocumentAdmissionVerdict


class DocumentAdmissionPolicy(Protocol):
    def evaluate(
        self, document: DocumentRecord, chunks: tuple[DocumentChunkRecord, ...]
    ) -> DocumentAdmissionDecision:
        """Only correlated accepted evidence permits the atomic transaction."""
        ...


class DocumentEventDelivery(StrEnum):
    PUBLISHED = "published"
    REJECTED_FULL = "rejected_full"


class DocumentIngestionResult(_Contract):
    document: DocumentRecord
    chunk_ids: Annotated[tuple[_Identifier, ...], Field(min_length=1)]
    event_delivery: DocumentEventDelivery

    @field_validator("document", mode="before")
    @classmethod
    def exact_document(cls, value: object) -> DocumentRecord:
        return _validated(value, DocumentRecord)

    @field_validator("chunk_ids", mode="before")
    @classmethod
    def ordered_ids(cls, value: object) -> object:
        if not isinstance(value, (tuple, list)):
            raise ValueError("Chunk IDs require an ordered sequence")
        return tuple(value)

    @model_validator(mode="after")
    def chunk_evidence(self) -> Self:
        if len(self.chunk_ids) != self.document.chunk_count or len(
            set(self.chunk_ids)
        ) != len(self.chunk_ids):
            raise ValueError("Chunk IDs must be unique and match document count")
        return self


class DocumentCancellationStatus(StrEnum):
    ACCEPTED = "accepted"
    NOT_FOUND = "not_found"


class DocumentCancellationResult(_Contract):
    document_id: _Identifier
    status: DocumentCancellationStatus


@dataclass
class _ActiveIngestion:
    task: asyncio.Task[object]
    cancellation_requested: bool = False


def _proposal(
    source: DocumentSource, parsed: ParsedDocument, limits: DocumentIngestionLimits
) -> tuple[DocumentRecord, tuple[DocumentChunkRecord, ...]]:
    if sum(len(segment.text) for segment in parsed.segments) > limits.max_parsed_chars:
        raise DocumentLimitError("Parsed character limit exceeded")
    chunks: list[DocumentChunkRecord] = []
    seen: set[str] = set()
    step = limits.chunk_size_chars - limits.chunk_overlap_chars
    for segment in parsed.segments:
        for start in range(0, len(segment.text), step):
            end = min(start + limits.chunk_size_chars, len(segment.text))
            content = segment.text[start:end]
            comparison = " ".join(
                unicodedata.normalize("NFKC", content).split()
            ).casefold()
            if content.strip() and comparison not in seen:
                if len(chunks) >= limits.max_chunks:
                    raise DocumentLimitError("Retained chunk limit exceeded")
                seen.add(comparison)
                index = len(chunks)
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                identity = str(
                    uuid5(
                        NAMESPACE_URL,
                        json.dumps(
                            [source.document_id, index, content_hash],
                            ensure_ascii=True,
                            separators=(",", ":"),
                        ),
                    )
                )
                chunks.append(
                    DocumentChunkRecord(
                        id=identity,
                        document_id=source.document_id,
                        chunk_index=index,
                        content=content,
                        content_hash=content_hash,
                        path=segment.path,
                        page=segment.page,
                        section=segment.section,
                        source_timestamp=source.source_timestamp,
                        parser_version=parsed.parser_version,
                        metadata=segment.metadata,
                    )
                )
            if end == len(segment.text):
                break
    if not chunks:
        raise DocumentLimitError("No meaningful retained chunks")
    document = DocumentRecord(
        id=source.document_id,
        source_type=source.source_type,
        source_ref=source.source_ref,
        media_type=source.media_type,
        source_timestamp=source.source_timestamp,
        source_hash=hashlib.sha256(source.content_bytes).hexdigest(),
        parser_id=parsed.parser_id,
        parser_version=parsed.parser_version,
        chunk_count=len(chunks),
        indexed_at=datetime.now(UTC),
        metadata=source.metadata,
    )
    return document, tuple(chunks)


def _row_values(record: DocumentRecord | DocumentChunkRecord) -> dict[str, object]:
    values = record.model_dump()
    values["metadata_json"] = values.pop("metadata")
    return values


class DocumentIngestionService:
    """One process/event loop; caller tasks own parsing and cooperative cancellation."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        event_bus: EventBus,
        admission_policy: DocumentAdmissionPolicy,
        limits: DocumentIngestionLimits,
        registry: AgentRegistry | None = None,
    ) -> None:
        if not isinstance(sessions, sessionmaker) or not isinstance(
            event_bus, EventBus
        ):
            raise DocumentInputError("Expected a session factory and EventBus")
        if not callable(getattr(admission_policy, "evaluate", None)):
            raise DocumentAdmissionError("An explicit admission policy is required")
        try:
            self._limits = _validated(limits, DocumentIngestionLimits)
            selected = build_default_agent_registry() if registry is None else registry
            fields = _snapshot(selected, AgentRegistry)
            fields["agents"] = tuple(
                _validated(agent, AgentDefinition) for agent in selected.agents
            )
            AgentRegistry.model_validate(fields).require("knowledge_rag")
        except (ValueError, TypeError, LookupError, AttributeError, RecursionError):
            raise DocumentInputError(
                "Invalid limits or knowledge_rag registry"
            ) from None
        self._sessions = sessions
        self._event_bus = event_bus
        self._admission_policy = admission_policy
        self._active: dict[str, _ActiveIngestion] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def agent_id(self) -> str:
        return "knowledge_rag"

    @property
    def active_document_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise DocumentIngestionError(
                "Ingestion service belongs to another event loop"
            )
        self._loop = loop

    @staticmethod
    def _check_cancellation(operation: _ActiveIngestion) -> None:
        if operation.cancellation_requested or operation.task.cancelling():
            raise asyncio.CancelledError

    def _admit(
        self, document: DocumentRecord, chunks: tuple[DocumentChunkRecord, ...]
    ) -> None:
        try:
            decision = _validated(
                self._admission_policy.evaluate(
                    _validated(document, DocumentRecord),
                    tuple(_validated(chunk, DocumentChunkRecord) for chunk in chunks),
                ),
                DocumentAdmissionDecision,
            )
        except Exception:
            raise DocumentAdmissionError("Document admission failed") from None
        if decision.document_id != document.id:
            raise DocumentAdmissionError("Admission document ID mismatch")
        if decision.verdict is DocumentAdmissionVerdict.REJECTED_SECRET:
            raise DocumentSecretRejectedError("Secret-bearing document rejected")
        if decision.verdict is DocumentAdmissionVerdict.REJECTED_POLICY:
            raise DocumentPolicyRejectedError("Document policy rejected persistence")

    def _persist(
        self, document: DocumentRecord, chunks: tuple[DocumentChunkRecord, ...]
    ) -> None:
        try:
            with self._sessions() as session:
                with session.begin():
                    if session.get(DocumentRow, document.id) is not None:
                        raise DocumentConflictError("Document ID already exists")
                    session.add(DocumentRow(**_row_values(document)))
                    # Parent flush is inside the same transaction, never its own commit.
                    session.flush()
                    session.add_all(DocumentChunkRow(**_row_values(c)) for c in chunks)
                    session.commit()
        except IntegrityError as error:
            if getattr(
                error.orig, "sqlite_errorname", None
            ) == "SQLITE_CONSTRAINT_PRIMARYKEY" and "documents.id" in str(error.orig):
                raise DocumentConflictError("Document ID already exists") from None
            raise DocumentPersistenceError(
                "Document database operation failed"
            ) from None
        except SQLAlchemyError:
            raise DocumentPersistenceError(
                "Document database operation failed"
            ) from None

    async def ingest(
        self, *, source: DocumentSource, parser: DocumentParser
    ) -> DocumentIngestionResult:
        try:
            source = _validated(source, DocumentSource)
        except (ValueError, TypeError, RecursionError):
            raise DocumentInputError("Invalid document source") from None
        if len(source.content_bytes) > self._limits.max_source_bytes:
            raise DocumentLimitError("Source byte limit exceeded")
        if not callable(getattr(parser, "parse", None)):
            raise DocumentParserError("An explicit parser collaborator is required")
        self._bind_loop()
        if source.document_id in self._active:
            raise DuplicateActiveDocumentError("Document ID is already active")
        task = asyncio.current_task()
        if task is None or any(active.task is task for active in self._active.values()):
            raise DocumentIngestionError("Caller task already owns an active ingestion")
        operation = _ActiveIngestion(task)
        self._active[source.document_id] = operation
        try:
            try:
                parsed = await parser.parse(_validated(source, DocumentSource))
                self._check_cancellation(operation)
                parsed = _validated(parsed, ParsedDocument)
            except Exception:
                self._check_cancellation(operation)
                raise DocumentParserError("Document parsing failed") from None
            if parsed.document_id != source.document_id:
                raise DocumentParserError("Parser document ID mismatch")
            try:
                document, chunks = _proposal(source, parsed, self._limits)
            except DocumentLimitError:
                raise
            except (ValueError, TypeError, RecursionError):
                raise DocumentParserError("Invalid parsed chunk evidence") from None
            self._admit(document, chunks)
            self._check_cancellation(operation)
            self._persist(document, chunks)
            envelope = EventEnvelope(
                event_type=EventType.DOCUMENT_INDEXED,
                agent_id="knowledge_rag",
                correlation_id=document.id,
                payload={
                    "document_id": document.id,
                    "chunk_count": document.chunk_count,
                },
            )
            try:
                await self._event_bus.publish(envelope)
            except EventBusFullError:
                delivery = DocumentEventDelivery.REJECTED_FULL
            except Exception:
                self._check_cancellation(operation)
                raise DocumentEventError(
                    "Document and chunks committed; event publication failed"
                ) from None
            else:
                delivery = DocumentEventDelivery.PUBLISHED
            self._check_cancellation(operation)
            return DocumentIngestionResult(
                document=document,
                chunk_ids=tuple(c.id for c in chunks),
                event_delivery=delivery,
            )
        finally:
            del self._active[source.document_id]

    async def cancel(self, document_id: str) -> DocumentCancellationResult:
        self._bind_loop()
        try:
            identity = _identifier.validate_python(document_id)
        except ValueError:
            raise DocumentInputError("Invalid document ID") from None
        operation = self._active.get(identity)
        status = DocumentCancellationStatus.NOT_FOUND
        if operation is not None:
            operation.cancellation_requested = True
            operation.task.cancel()
            status = DocumentCancellationStatus.ACCEPTED
        return DocumentCancellationResult(document_id=identity, status=status)
