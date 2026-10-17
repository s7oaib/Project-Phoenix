"""Bounded untrusted parsing, provenance, atomic SQLite and cooperative lifetimes."""

import ast
import asyncio
import hashlib
import inspect as python_inspect
import subprocess
import sys
import traceback
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pydantic import BaseModel, ValidationError
from sqlalchemy import Engine, MetaData, event, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from phoenix.agents.registry import AgentDefinition, AgentRegistry
from phoenix.core.config import AppSettings
from phoenix.core.event_bus import EventBus
from phoenix.core.events import EventEnvelope, EventSeverity, EventType
from phoenix.persistence.base import Base
from phoenix.persistence.database import (
    create_engine_for_settings,
    create_session_factory,
    database_url,
)
from phoenix.persistence.documents import DocumentChunkRow, DocumentRow
from phoenix.persistence.memory import UTCDateTime
from phoenix.rag import ingestion as module
from phoenix.rag.ingestion import (
    DocumentAdmissionDecision,
    DocumentAdmissionError,
    DocumentAdmissionVerdict,
    DocumentCancellationResult,
    DocumentCancellationStatus,
    DocumentChunkRecord,
    DocumentConflictError,
    DocumentEventDelivery,
    DocumentEventError,
    DocumentIngestionError,
    DocumentIngestionLimits,
    DocumentIngestionResult,
    DocumentIngestionService,
    DocumentInputError,
    DocumentLimitError,
    DocumentParserError,
    DocumentPersistenceError,
    DocumentPolicyRejectedError,
    DocumentRecord,
    DocumentSecretRejectedError,
    DocumentSource,
    DuplicateActiveDocumentError,
    ParsedDocument,
    ParsedSegment,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 5, 12, 30, 15, 123456, tzinfo=UTC)
UNSET = object()
BAD_IDS = (None, True, 1, 1.5, b"id", object(), "", " \t\n")
DOC_FIELDS = {
    "id",
    "source_type",
    "source_ref",
    "media_type",
    "source_timestamp",
    "source_hash",
    "parser_id",
    "parser_version",
    "chunk_count",
    "indexed_at",
    "metadata",
}
CHUNK_FIELDS = {
    "id",
    "document_id",
    "chunk_index",
    "content",
    "content_hash",
    "path",
    "page",
    "section",
    "source_timestamp",
    "parser_version",
    "metadata",
}


def source(**changes: object) -> DocumentSource:
    return DocumentSource.model_validate(
        dict(
            document_id="first",
            source_type="operator",
            source_ref="opaque:private-source",
            media_type="custom/type",
            content_bytes=b"exact supplied bytes",
            source_timestamp=NOW,
            metadata={"nested": [{"label": "private-metadata"}]},
        )
        | changes
    )


def limits(**changes: object) -> DocumentIngestionLimits:
    return DocumentIngestionLimits.model_validate(
        dict(
            max_source_bytes=100,
            max_parsed_chars=100,
            max_chunks=10,
            chunk_size_chars=10,
            chunk_overlap_chars=2,
        )
        | changes
    )


def segment(**changes: object) -> ParsedSegment:
    return ParsedSegment.model_validate(
        dict(
            text="abcdefghijklmno",
            path="opaque:segment",
            page=2,
            section="Section",
            metadata={"locator": ["original"]},
        )
        | changes
    )


def parsed(**changes: object) -> ParsedDocument:
    return ParsedDocument.model_validate(
        dict(
            document_id="first",
            parser_id="neutral-parser",
            parser_version="1",
            segments=(segment(),),
        )
        | changes
    )


def document(**changes: object) -> DocumentRecord:
    return DocumentRecord.model_validate(
        dict(
            id="first",
            source_type="operator",
            source_ref="opaque:source",
            media_type="custom/type",
            source_timestamp=NOW,
            source_hash=hashlib.sha256(b"bytes").hexdigest(),
            parser_id="neutral-parser",
            parser_version="1",
            chunk_count=1,
            indexed_at=NOW,
            metadata={},
        )
        | changes
    )


def chunk(**changes: object) -> DocumentChunkRecord:
    return DocumentChunkRecord.model_validate(
        dict(
            id="chunk",
            document_id="first",
            chunk_index=0,
            content="Original text",
            content_hash=hashlib.sha256(b"Original text").hexdigest(),
            path=None,
            page=None,
            section=None,
            source_timestamp=NOW,
            parser_version="1",
            metadata={},
        )
        | changes
    )


class Parser:
    def __init__(self, result: object = UNSET) -> None:
        self.result = result
        self.calls: list[DocumentSource] = []

    async def parse(self, value: DocumentSource) -> ParsedDocument:
        self.calls.append(value)
        if isinstance(self.result, BaseException):
            raise self.result
        if self.result is not UNSET:
            return cast(ParsedDocument, self.result)
        return parsed(document_id=value.document_id)


class Policy:
    def __init__(self, verdict: str = "accepted", result: object = UNSET) -> None:
        self.verdict, self.result = verdict, result
        self.calls: list[tuple[DocumentRecord, tuple[DocumentChunkRecord, ...]]] = []

    def evaluate(
        self, value: DocumentRecord, chunks: tuple[DocumentChunkRecord, ...]
    ) -> DocumentAdmissionDecision:
        self.calls.append((value, chunks))
        if isinstance(self.result, BaseException):
            raise self.result
        if self.result is not UNSET:
            return cast(DocumentAdmissionDecision, self.result)
        return DocumentAdmissionDecision.model_validate(
            {"document_id": value.id, "verdict": self.verdict}
        )


class SpyBus(EventBus):
    def __init__(self, capacity: int = 256) -> None:
        super().__init__(capacity)
        self.attempts: list[EventEnvelope] = []

    async def publish(self, value: EventEnvelope) -> None:
        self.attempts.append(value)
        await super().publish(value)


def config(tmp_path: Path) -> Config:
    settings = AppSettings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    result = Config(str(ROOT / "alembic.ini"))
    result.set_main_option(
        "sqlalchemy.url", str(database_url(settings)).replace("%", "%%")
    )
    return result


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Engine]:
    command.upgrade(config(tmp_path), "0003_document_ingestion")
    settings = AppSettings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    engine = create_engine_for_settings(settings)

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection: object, record: object) -> None:
        # Explicit test/caller connection configuration, never a service mutation.
        connection.execute("PRAGMA foreign_keys = ON")  # type: ignore[attr-defined]

    try:
        yield engine
    finally:
        engine.dispose()


def make(
    database: Engine,
    policy: Policy | None = None,
    bus: EventBus | None = None,
    bounds: DocumentIngestionLimits | None = None,
    registry: AgentRegistry | None = None,
) -> DocumentIngestionService:
    return DocumentIngestionService(
        create_session_factory(database),
        bus or SpyBus(),
        policy or Policy(),
        limits() if bounds is None else bounds,
        registry,
    )


def ingest(
    service: DocumentIngestionService,
    value: DocumentSource | None = None,
    parser: Parser | None = None,
) -> DocumentIngestionResult:
    return asyncio.run(
        service.ingest(
            source=source() if value is None else value,
            parser=parser or Parser(),
        )
    )


def stored(
    database: Engine,
) -> tuple[tuple[DocumentRecord, ...], tuple[DocumentChunkRecord, ...]]:
    with database.connect() as connection:
        documents = tuple(
            DocumentRecord.model_validate(dict(row))
            for row in connection.execute(
                select(DocumentRow.__table__).order_by(DocumentRow.id)
            ).mappings()
        )
        chunks = tuple(
            DocumentChunkRecord.model_validate(dict(row))
            for row in connection.execute(
                select(DocumentChunkRow.__table__).order_by(
                    DocumentChunkRow.document_id, DocumentChunkRow.chunk_index
                )
            ).mappings()
        )
    return documents, chunks


@pytest.mark.parametrize("alias", [None, "Renamed", "VEGAPUNK"])
def test_authoritative_identity_alias_independence(
    database: Engine, alias: str | None
) -> None:
    registry = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id="knowledge_rag",
                alias=alias,
                professional_role="Display only",
            ),
        )
    )
    bus = SpyBus()
    service = make(database, registry=registry, bus=bus)
    assert service.agent_id == "knowledge_rag" and service.active_document_ids == ()
    result = ingest(service)
    assert result.document.id == "first" and bus.attempts[0].agent_id == "knowledge_rag"
    assert service.active_document_ids == ()


@pytest.mark.parametrize("identity", ["VEGAPUNK", "other"])
def test_alias_only_and_missing_identity_fail(database: Engine, identity: str) -> None:
    registry = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id=identity,
                alias="knowledge_rag",
                professional_role="Knowledge",
            ),
        )
    )
    with pytest.raises(DocumentInputError):
        make(database, registry=registry)


def test_default_registry_required_policy_limits_and_vocabularies(
    database: Engine,
) -> None:
    assert make(database).agent_id == "knowledge_rag"
    signature = python_inspect.signature(DocumentIngestionService)
    for required in ("admission_policy", "limits"):
        assert signature.parameters[required].default is python_inspect.Parameter.empty
    for bad in (None, True, object()):
        with pytest.raises(DocumentAdmissionError):
            DocumentIngestionService(
                create_session_factory(database),
                EventBus(),
                cast(module.DocumentAdmissionPolicy, bad),
                limits(),
            )
    assert [v.value for v in DocumentAdmissionVerdict] == [
        "accepted",
        "rejected_secret",
        "rejected_policy",
    ]
    assert [v.value for v in DocumentEventDelivery] == ["published", "rejected_full"]
    assert [v.value for v in DocumentCancellationStatus] == ["accepted", "not_found"]


@pytest.mark.parametrize(
    "field", ["document_id", "source_type", "source_ref", "media_type"]
)
@pytest.mark.parametrize("bad", BAD_IDS)
def test_source_strict_strings(field: str, bad: object) -> None:
    with pytest.raises(ValidationError):
        source(**{field: bad})


@pytest.mark.parametrize(
    "bad", [None, True, 1, "bytes", bytearray(b"bytes"), memoryview(b"x"), b""]
)
def test_strict_nonempty_bytes(bad: object) -> None:
    with pytest.raises(ValidationError):
        source(content_bytes=bad)


def test_source_ids_timestamp_metadata_snapshot_and_json() -> None:
    data = source().model_dump()
    data.pop("document_id")
    assert (
        DocumentSource.model_validate(data).document_id
        != DocumentSource.model_validate(data).document_id
    )
    assert (
        source(
            document_id=" custom ", source_type="extensible", media_type="other"
        ).document_id
        == "custom"
    )
    local = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert source(source_timestamp=local).source_timestamp == NOW
    assert source(source_timestamp=local.isoformat()).source_timestamp == NOW
    original: dict[str, object] = {"items": [{"label": "original"}]}
    value = source(metadata=original)
    cast(list[object], original["items"]).append("mutated")
    assert value.model_dump()["metadata"] == {"items": [{"label": "original"}]}
    with pytest.raises(TypeError):
        value.metadata["mutate"] = "bad"  # type: ignore[index]
    assert DocumentSource.model_validate(value.model_dump()) == value
    # Serialized strings cannot enter the explicitly strict in-memory bytes boundary.
    with pytest.raises(ValidationError):
        DocumentSource.model_validate_json(value.model_dump_json())


@pytest.mark.parametrize("factory", [source, document, chunk])
@pytest.mark.parametrize(
    "bad", [datetime(2026, 10, 5), "2026-10-05T00:00:00", True, 1, 1.5, b"time"]
)
def test_naive_or_coerced_source_timestamp_rejected(
    factory: object, bad: object
) -> None:
    with pytest.raises(ValidationError):
        factory(source_timestamp=bad)  # type: ignore[operator]


@pytest.mark.parametrize("factory", [source, segment, document, chunk])
@pytest.mark.parametrize(
    "bad",
    [
        None,
        [],
        "text",
        {1: "bad"},
        {"x": b"bytes"},
        {"x": object()},
        {"x": {1}},
        {"x": float("nan")},
        {"x": float("inf")},
    ],
)
def test_all_metadata_is_finite_json(factory: object, bad: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        factory(metadata=bad)  # type: ignore[operator]


def test_metadata_cycles_rejected() -> None:
    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    with pytest.raises(ValidationError):
        source(metadata=cycle)


@pytest.mark.parametrize(
    "field", ["max_source_bytes", "max_parsed_chars", "max_chunks", "chunk_size_chars"]
)
@pytest.mark.parametrize("bad", [None, True, False, 0, -1, "1", 1.5, float("inf")])
def test_positive_strict_limits(field: str, bad: object) -> None:
    with pytest.raises(ValidationError):
        limits(**{field: bad})


@pytest.mark.parametrize("bad", [None, True, -1, "0", 1.5, 10, 11])
def test_overlap_progress(bad: object) -> None:
    with pytest.raises(ValidationError):
        limits(chunk_overlap_chars=bad)
    assert limits(chunk_overlap_chars=0).chunk_overlap_chars == 0


@pytest.mark.parametrize(
    "field,bad",
    [
        ("text", ""),
        ("text", " \t"),
        ("text", True),
        ("text", b"text"),
        ("path", " "),
        ("path", True),
        ("section", ""),
        ("section", 1),
        ("page", True),
        ("page", 0),
        ("page", "1"),
        ("page", 1.5),
    ],
)
def test_segment_locator_and_text_contracts(field: str, bad: object) -> None:
    with pytest.raises(ValidationError):
        segment(**{field: bad})


@pytest.mark.parametrize("field", ["parser_id", "parser_version", "document_id"])
@pytest.mark.parametrize("bad", BAD_IDS)
def test_parsed_strict_identifiers(field: str, bad: object) -> None:
    with pytest.raises(ValidationError):
        parsed(**{field: bad})


@pytest.mark.parametrize("bad", [(), None, {}, "text", set()])
def test_segments_nonempty_ordered(bad: object) -> None:
    with pytest.raises(ValidationError):
        parsed(segments=bad)


@pytest.mark.parametrize(
    "factory",
    [
        source,
        limits,
        segment,
        parsed,
        document,
        chunk,
        lambda: DocumentAdmissionDecision(
            document_id="first", verdict=DocumentAdmissionVerdict.ACCEPTED
        ),
        lambda: DocumentIngestionResult(
            document=document(),
            chunk_ids=("chunk",),
            event_delivery=DocumentEventDelivery.PUBLISHED,
        ),
        lambda: DocumentCancellationResult(
            document_id="first", status=DocumentCancellationStatus.NOT_FOUND
        ),
    ],
)
def test_public_models_frozen_extra_forbidden_and_copied_extra_rejected(
    factory: object,
) -> None:
    value = factory()  # type: ignore[operator]
    contract = type(value)
    with pytest.raises(ValidationError):
        setattr(value, next(iter(contract.model_fields)), None)
    with pytest.raises(ValidationError):
        contract.model_validate({**value.model_dump(), "extra": "private"})
    with pytest.raises(ValidationError):
        contract.model_validate(value.model_copy(update={"extra": "private"}))


@pytest.mark.parametrize(
    "factory,field", [(document, "source_hash"), (chunk, "content_hash")]
)
@pytest.mark.parametrize("bad", ["a" * 63, "a" * 65, "A" * 64, "z" * 64, True, 1])
def test_hash_shape_strict(factory: object, field: str, bad: object) -> None:
    with pytest.raises(ValidationError):
        factory(**{field: bad})  # type: ignore[operator]


@pytest.mark.parametrize(
    "factory,field,bad",
    [
        (document, "chunk_count", True),
        (document, "chunk_count", 0),
        (chunk, "chunk_index", True),
        (chunk, "chunk_index", -1),
        (document, "indexed_at", NOW.replace(tzinfo=None)),
    ],
)
def test_record_counts_indices_and_indexed_timestamp(
    factory: object, field: str, bad: object
) -> None:
    with pytest.raises(ValidationError):
        factory(**{field: bad})  # type: ignore[operator]


def test_exact_domain_field_sets() -> None:
    assert set(DocumentRecord.model_fields) == DOC_FIELDS
    assert set(DocumentChunkRecord.model_fields) == CHUNK_FIELDS
    assert set(DocumentAdmissionDecision.model_fields) == {"document_id", "verdict"}


@pytest.mark.parametrize(
    "field,bad",
    [
        ("document_id", True),
        ("metadata", {"x": object()}),
        ("content_bytes", "text"),
        ("source_timestamp", NOW.replace(tzinfo=None)),
        ("injected", "private"),
    ],
)
def test_forged_source_rejected_before_parser(
    database: Engine, field: str, bad: object
) -> None:
    parser, policy, bus = Parser(), Policy(), SpyBus()
    service = make(database, policy, bus)
    with pytest.raises(DocumentInputError):
        ingest(service, source().model_copy(update={field: bad}), parser)
    assert not parser.calls and not policy.calls and not bus.attempts
    assert service.active_document_ids == () and stored(database) == ((), ())


def test_source_size_gate_before_parser_admission_and_session(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    parser, policy, bus = Parser(), Policy(), SpyBus()
    service = make(database, policy, bus, limits(max_source_bytes=1))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Gate must precede session acquisition")

    monkeypatch.setattr(sessionmaker, "__call__", forbidden)
    with pytest.raises(DocumentLimitError):
        ingest(service, parser=parser)
    assert not parser.calls and not policy.calls and not bus.attempts
    assert service.active_document_ids == ()


@pytest.mark.parametrize(
    "bad",
    [
        None,
        True,
        {},
        parsed(document_id="other"),
        parsed().model_copy(update={"extra": "private"}),
        parsed().model_copy(update={"segments": ()}),
        parsed().model_copy(
            update={"segments": (segment().model_copy(update={"page": True}),)}
        ),
        parsed().model_copy(
            update={
                "segments": (
                    segment().model_copy(update={"metadata": {"x": object()}}),
                )
            }
        ),
        RuntimeError("private-parser-content"),
    ],
)
def test_malformed_parser_no_admission_or_session(
    database: Engine, monkeypatch: pytest.MonkeyPatch, bad: object
) -> None:
    parser, policy, bus = Parser(bad), Policy(), SpyBus()
    service = make(database, policy, bus)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Parser validation must precede persistence")

    monkeypatch.setattr(sessionmaker, "__call__", forbidden)
    with pytest.raises(DocumentParserError) as error:
        ingest(service, parser=parser)
    assert "private-parser-content" not in str(error.value)
    assert len(parser.calls) == 1 and not policy.calls and not bus.attempts
    assert service.active_document_ids == ()


def test_extended_parser_and_segments_fail(database: Engine) -> None:
    class Extended(ParsedDocument):
        extra: str = "private"

    class ExtendedSegment(ParsedSegment):
        extra: str = "private"

    for bad in (
        Extended.model_validate(parsed().model_dump()),
        parsed().model_copy(
            update={
                "segments": (ExtendedSegment.model_validate(segment().model_dump()),)
            }
        ),
    ):
        service = make(database)
        with pytest.raises(DocumentParserError):
            ingest(service, parser=Parser(bad))
        assert service.active_document_ids == () and stored(database) == ((), ())


@pytest.mark.parametrize("bounds", [limits(max_parsed_chars=14), limits(max_chunks=1)])
def test_parsed_and_chunk_limits_before_admission(
    database: Engine, bounds: DocumentIngestionLimits
) -> None:
    policy, bus = Policy(), SpyBus()
    service = make(database, policy, bus, bounds)
    with pytest.raises(DocumentLimitError):
        ingest(service)
    assert not policy.calls and not bus.attempts and stored(database) == ((), ())
    assert service.active_document_ids == ()


def test_exact_windows_overlap_provenance_hashes_and_result(database: Engine) -> None:
    policy, parser, bus = Policy(), Parser(), SpyBus()
    service = make(database, policy, bus)
    original = source()
    result = ingest(service, original, parser)
    assert len(parser.calls) == len(policy.calls) == len(bus.attempts) == 1
    assert parser.calls[0] == original and parser.calls[0] is not original
    documents, chunks = stored(database)
    assert documents == (result.document,)
    assert (
        result.document.source_hash
        == hashlib.sha256(original.content_bytes).hexdigest()
    )
    assert result.document.indexed_at.tzinfo is UTC
    assert result.document.source_timestamp == NOW
    assert result.document.metadata == original.metadata
    assert [c.content for c in chunks] == ["abcdefghij", "ijklmno"]
    assert [c.chunk_index for c in chunks] == [0, 1]
    assert result.chunk_ids == tuple(c.id for c in chunks)
    for value in chunks:
        assert value.path == segment().path and value.page == 2
        assert value.section == "Section" and value.metadata == segment().metadata
        assert value.source_timestamp == NOW and value.parser_version == "1"
        assert (
            value.content_hash
            == hashlib.sha256(value.content.encode("utf-8")).hexdigest()
        )
    envelope = bus.attempts[0]
    assert envelope.event_type is EventType.DOCUMENT_INDEXED
    assert envelope.agent_id == "knowledge_rag" and envelope.correlation_id == "first"
    assert envelope.severity is EventSeverity.INFO and envelope.task_id is None
    assert dict(envelope.payload) == {"document_id": "first", "chunk_count": 2}
    assert all(
        private is None or private not in envelope.model_dump_json()
        for private in (
            original.source_ref,
            "private-metadata",
            segment().text,
            segment().path,
        )
    )
    assert result.event_delivery is DocumentEventDelivery.PUBLISHED
    assert "content_bytes" not in DocumentRow.__table__.columns
    assert all(
        not isinstance(v, (Session, DocumentRow)) for v in (result, result.document)
    )
    assert service.active_document_ids == ()


@pytest.mark.parametrize(
    "overlap,expected",
    [
        (0, ["abcd", "efgh"]),
        (1, ["abcd", "defg", "gh"]),
        (3, ["abcd", "bcde", "cdef", "defg", "efgh"]),
    ],
)
def test_overlap_windows_deterministic(
    database: Engine, overlap: int, expected: list[str]
) -> None:
    bounds = limits(chunk_size_chars=4, chunk_overlap_chars=overlap)
    ingest(
        make(database, bounds=bounds),
        parser=Parser(parsed(segments=(segment(text="abcdefgh"),))),
    )
    assert [c.content for c in stored(database)[1]] == expected


def test_whitespace_filter_nfkc_dedup_first_original_provenance(
    database: Engine,
) -> None:
    segments = (
        segment(text="  ＨＥＬＬＯ\tWorld  ", page=1),
        segment(text="hello world", page=2),
        segment(text="HELLO   WORLD", page=3),
        segment(text="    unique    ", page=None, path=None, section=None),
    )
    parser = Parser(parsed(segments=segments))
    ingest(
        make(
            database,
            bounds=limits(chunk_size_chars=100, chunk_overlap_chars=0, max_chunks=2),
        ),
        parser=parser,
    )
    values = stored(database)[1]
    assert [c.content for c in values] == [segments[0].text, segments[3].text]
    assert [c.chunk_index for c in values] == [0, 1]
    assert values[0].page == 1 and values[0].path == segments[0].path
    assert all(v is None for v in (values[1].page, values[1].path, values[1].section))


def test_whitespace_only_windows_skipped(database: Engine) -> None:
    parser = Parser(parsed(segments=(segment(text="    X    Y"),)))
    ingest(
        make(database, bounds=limits(chunk_size_chars=4, chunk_overlap_chars=0)),
        parser=parser,
    )
    assert [c.content for c in stored(database)[1]] == ["X   ", " Y"]


def test_durable_duplicate_preserved_and_cross_document_provenance(
    database: Engine,
) -> None:
    async def scenario() -> None:
        bus = SpyBus()
        service = make(database, bus=bus)
        first = await service.ingest(source=source(), parser=Parser())
        previous = stored(database)
        with pytest.raises(DocumentConflictError):
            await service.ingest(
                source=source(content_bytes=b"different"), parser=Parser()
            )
        assert stored(database) == previous and len(bus.attempts) == 1
        second = await service.ingest(
            source=source(document_id="second"), parser=Parser()
        )
        docs, values = stored(database)
        assert len(docs) == 2 and len(values) == 4
        assert first.document.source_ref == second.document.source_ref
        assert first.chunk_ids != second.chunk_ids
        assert values[0].content == values[2].content
        assert service.active_document_ids == ()

    asyncio.run(scenario())


def test_same_evidence_derives_identical_ids_across_services(
    database: Engine, tmp_path: Path
) -> None:
    # Compare two independent temporary databases, keeping the same explicit source ID.
    first = ingest(make(database))
    other_path = tmp_path / "other"
    other_path.mkdir()
    command.upgrade(config(other_path), "0003_document_ingestion")
    settings = AppSettings(data_dir=other_path, _env_file=None)  # type: ignore[call-arg]
    other = create_engine_for_settings(settings)
    try:
        second = ingest(make(other))
        assert first.chunk_ids == second.chunk_ids
        assert [c.content_hash for c in stored(database)[1]] == [
            c.content_hash for c in stored(other)[1]
        ]
    finally:
        other.dispose()


@pytest.mark.parametrize(
    "result,expected",
    [
        (
            DocumentAdmissionDecision(
                document_id="first", verdict=DocumentAdmissionVerdict.REJECTED_SECRET
            ),
            DocumentSecretRejectedError,
        ),
        (
            DocumentAdmissionDecision(
                document_id="first", verdict=DocumentAdmissionVerdict.REJECTED_POLICY
            ),
            DocumentPolicyRejectedError,
        ),
        (
            DocumentAdmissionDecision(
                document_id="other", verdict=DocumentAdmissionVerdict.ACCEPTED
            ),
            DocumentAdmissionError,
        ),
        (None, DocumentAdmissionError),
        (True, DocumentAdmissionError),
        ({}, DocumentAdmissionError),
        (
            DocumentAdmissionDecision(
                document_id="first", verdict=DocumentAdmissionVerdict.ACCEPTED
            ).model_copy(update={"verdict": "fake"}),
            DocumentAdmissionError,
        ),
        (
            DocumentAdmissionDecision(
                document_id="first", verdict=DocumentAdmissionVerdict.ACCEPTED
            ).model_copy(update={"private": "injected"}),
            DocumentAdmissionError,
        ),
        (RuntimeError("private-policy-content"), DocumentAdmissionError),
    ],
)
def test_rejected_or_malformed_admission_precedes_session(
    database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    result: object,
    expected: type[Exception],
) -> None:
    policy, bus, parser = Policy(result=result), SpyBus(), Parser()
    service = make(database, policy, bus)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Rejected admission must not acquire a session")

    monkeypatch.setattr(sessionmaker, "__call__", forbidden)
    with pytest.raises(expected) as error:
        ingest(service, parser=parser)
    assert "private-policy-content" not in "".join(
        traceback.format_exception(error.value)
    )
    assert len(policy.calls) == len(parser.calls) == 1 and bus.attempts == []
    assert service.active_document_ids == ()


@pytest.mark.parametrize("bad", [None, True, 1, b"accepted", "allow", "Accepted"])
def test_invalid_admission_vocabularies(bad: object) -> None:
    with pytest.raises(ValidationError):
        DocumentAdmissionDecision.model_validate(
            {"document_id": "first", "verdict": bad}
        )


def test_extended_admission_and_nested_forgery(database: Engine) -> None:
    class Extended(DocumentAdmissionDecision):
        extra: str = "private"

    service = make(
        database,
        Policy(
            result=Extended(
                document_id="first",
                verdict=DocumentAdmissionVerdict.ACCEPTED,
            )
        ),
    )
    with pytest.raises(DocumentAdmissionError):
        ingest(service)
    assert stored(database) == ((), ())
    with pytest.raises(ValidationError):
        DocumentIngestionResult(
            document=document().model_copy(update={"chunk_count": True}),
            chunk_ids=("chunk",),
            event_delivery=DocumentEventDelivery.PUBLISHED,
        )


def test_parser_and_policy_cannot_mutate_persistence_evidence(database: Engine) -> None:
    class MutatingParser(Parser):
        async def parse(self, value: DocumentSource) -> ParsedDocument:
            result = await super().parse(value)
            object.__setattr__(value, "content_bytes", b"mutated")
            object.__setattr__(value, "source_ref", "mutated")
            return result

    class MutatingPolicy(Policy):
        def evaluate(
            self, value: DocumentRecord, chunks: tuple[DocumentChunkRecord, ...]
        ) -> DocumentAdmissionDecision:
            result = super().evaluate(value, chunks)
            object.__setattr__(value, "source_hash", "0" * 64)
            object.__setattr__(chunks[0], "content", "Mutated by policy")
            return result

    result = ingest(make(database, MutatingPolicy()), parser=MutatingParser())
    assert (
        result.document.source_hash
        == hashlib.sha256(source().content_bytes).hexdigest()
    )
    assert result.document.source_ref == source().source_ref
    assert stored(database)[1][0].content == "abcdefghij"


@pytest.mark.parametrize("failure_at", [0, 1])
def test_chunk_failure_rolls_back_document_and_all_chunks(
    database: Engine, failure_at: int
) -> None:
    with database.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE TRIGGER reject_chunk BEFORE INSERT ON document_chunks "
            f"WHEN NEW.chunk_index = {failure_at} "
            "BEGIN SELECT RAISE(ABORT, 'private-db-detail'); END"
        )
    bus = SpyBus()
    service = make(database, bus=bus)
    with pytest.raises(DocumentPersistenceError) as error:
        ingest(service)
    assert "private-db-detail" not in "".join(traceback.format_exception(error.value))
    assert stored(database) == ((), ()) and bus.attempts == []
    assert service.active_document_ids == ()


def test_commit_failure_rolls_back_and_closes(database: Engine) -> None:
    state: list[str] = []

    class FailingSession(Session):
        def commit(self) -> None:
            self.flush()
            raise OperationalError(
                "private SQL", {"text": "private-content"}, Exception("private-path")
            )

        def close(self) -> None:
            state.append("closed")
            super().close()

    bus = SpyBus()
    service = DocumentIngestionService(
        sessionmaker(database, class_=FailingSession), bus, Policy(), limits()
    )
    with pytest.raises(DocumentPersistenceError) as error:
        ingest(service)
    rendered = "".join(traceback.format_exception(error.value))
    assert all(
        private not in rendered
        for private in ("private SQL", "private-content", "private-path")
    )
    assert stored(database) == ((), ()) and state == ["closed"] and bus.attempts == []
    assert service.active_document_ids == ()


def test_pk_race_conflict_preserves_original(database: Engine) -> None:
    ingest(make(database))
    original = stored(database)

    class StaleLookup(Session):
        def get(self, *args: object, **kwargs: object) -> None:
            return None

    bus = SpyBus()
    service = DocumentIngestionService(
        sessionmaker(database, class_=StaleLookup), bus, Policy(), limits()
    )
    with pytest.raises(DocumentConflictError):
        ingest(service)
    assert stored(database) == original and bus.attempts == []


def test_missing_database_location_safe_no_repair(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path / "never-created", _env_file=None)  # type: ignore[call-arg]
    engine = create_engine_for_settings(settings)
    try:
        service = make(engine)
        with pytest.raises(DocumentPersistenceError) as error:
            ingest(service)
        assert str(settings.data_dir) not in str(error.value)
        assert not settings.data_dir.exists() and service.active_document_ids == ()
    finally:
        engine.dispose()


def test_event_only_after_atomic_commit_and_close(database: Engine) -> None:
    state: list[str] = []

    class TrackingSession(Session):
        def close(self) -> None:
            state.append("closed")
            super().close()

    @event.listens_for(TrackingSession, "after_commit")
    def committed(session: Session) -> None:
        state.append("committed")

    class ObservingBus(SpyBus):
        async def publish(self, value: EventEnvelope) -> None:
            assert state == ["committed", "closed"]
            assert len(stored(database)[0]) == 1 and len(stored(database)[1]) == 2
            await super().publish(value)

    bus = ObservingBus()
    service = DocumentIngestionService(
        sessionmaker(database, class_=TrackingSession), bus, Policy(), limits()
    )
    ingest(service)
    assert len(bus.attempts) == 1


def test_real_bus_full_preserves_rows_no_partial_delivery_or_retry(
    database: Engine,
) -> None:
    async def scenario() -> None:
        bus = SpyBus(capacity=1)
        full = await bus.subscribe()
        await bus.publish(EventEnvelope(event_type=EventType.TASK_CREATED))
        empty = await bus.subscribe()
        service = make(database, bus=bus)
        result = await service.ingest(source=source(), parser=Parser())
        assert result.event_delivery is DocumentEventDelivery.REJECTED_FULL
        assert len(stored(database)[0]) == 1 and len(stored(database)[1]) == 2
        assert empty._queue.empty() and len(bus.attempts) == 2
        assert service.active_document_ids == ()
        await full.close()
        normal = await service.ingest(
            source=source(document_id="second"), parser=Parser()
        )
        assert normal.event_delivery is DocumentEventDelivery.PUBLISHED
        assert (await empty.get()).correlation_id == "second"
        await empty.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", [asyncio.CancelledError(), RuntimeError("private-event-content")]
)
def test_event_failure_durable_rows_and_cleanup(
    database: Engine, failure: BaseException
) -> None:
    class FailingBus(SpyBus):
        async def publish(self, value: EventEnvelope) -> None:
            self.attempts.append(value)
            raise failure

    bus = FailingBus()
    service = make(database, bus=bus)
    expected = (
        asyncio.CancelledError
        if isinstance(failure, asyncio.CancelledError)
        else DocumentEventError
    )
    with pytest.raises(expected) as error:
        ingest(service)
    assert "private-event-content" not in str(error.value)
    assert len(stored(database)[0]) == 1 and len(stored(database)[1]) == 2
    assert len(bus.attempts) == 1 and service.active_document_ids == ()


class GatedParser(Parser):
    def __init__(self, suppress: bool = False, clear_counter: bool = False) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.suppress = suppress
        self.clear_counter = clear_counter

    async def parse(self, value: DocumentSource) -> ParsedDocument:
        self.calls.append(value)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            if not self.suppress:
                raise
            if self.clear_counter:
                task = asyncio.current_task()
                assert task is not None
                while task.cancelling():
                    task.uncancel()
        return parsed(document_id=value.document_id)


@pytest.mark.parametrize(
    "suppress,clear", [(False, False), (True, False), (True, True)]
)
def test_active_cancel_duplicate_isolation_and_id_reuse(
    database: Engine, suppress: bool, clear: bool
) -> None:
    async def scenario() -> None:
        policy, bus = Policy(), SpyBus()
        service = make(database, policy, bus)
        first, second = GatedParser(suppress, clear), GatedParser()
        task_a = asyncio.create_task(
            service.ingest(source=source(document_id="z"), parser=first)
        )
        task_b = asyncio.create_task(
            service.ingest(source=source(document_id="a"), parser=second)
        )
        try:
            await asyncio.wait_for(first.started.wait(), 2)
            await asyncio.wait_for(second.started.wait(), 2)
            assert service.active_document_ids == ("a", "z")
            duplicate = Parser()
            with pytest.raises(DuplicateActiveDocumentError):
                await service.ingest(source=source(document_id="z"), parser=duplicate)
            assert duplicate.calls == [] and service.active_document_ids == ("a", "z")
            assert (
                await service.cancel("missing")
            ).status is DocumentCancellationStatus.NOT_FOUND
            assert (
                await service.cancel(" z ")
            ).status is DocumentCancellationStatus.ACCEPTED
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task_a, 2)
            assert service.active_document_ids == ("a",) and not task_b.done()
            assert (
                policy.calls == []
                and bus.attempts == []
                and stored(database) == ((), ())
            )
            second.release.set()
            await asyncio.wait_for(task_b, 2)
            assert (
                service.active_document_ids == () and stored(database)[0][0].id == "a"
            )
            assert (
                await service.cancel("z")
            ).status is DocumentCancellationStatus.NOT_FOUND
            await service.ingest(source=source(document_id="z"), parser=Parser())
            assert [d.id for d in stored(database)[0]] == ["a", "z"]
        finally:
            for task in (task_a, task_b):
                if not task.done():
                    task.cancel()
            await asyncio.gather(task_a, task_b, return_exceptions=True)

    asyncio.run(scenario())


def test_nested_same_caller_task_rejected(database: Engine) -> None:
    async def scenario() -> None:
        service = make(database)

        class Nested(Parser):
            async def parse(self, value: DocumentSource) -> ParsedDocument:
                with pytest.raises(DocumentIngestionError, match="Caller task"):
                    await service.ingest(
                        source=source(document_id="nested"), parser=Parser()
                    )
                assert service.active_document_ids == ("first",)
                return await super().parse(value)

        await service.ingest(source=source(), parser=Nested())
        assert service.active_document_ids == () and len(stored(database)[0]) == 1

    asyncio.run(scenario())


def test_cross_loop_ingestion_and_cancel_fail(database: Engine) -> None:
    service = make(database)
    ingest(service)
    with pytest.raises(DocumentIngestionError, match="another event loop"):
        ingest(service, source(document_id="second"))
    with pytest.raises(DocumentIngestionError, match="another event loop"):
        asyncio.run(service.cancel("first"))


@pytest.mark.parametrize("bad", BAD_IDS)
def test_cancel_strict_id(database: Engine, bad: object) -> None:
    service = make(database)
    with pytest.raises(DocumentInputError):
        asyncio.run(service.cancel(cast(str, bad)))


def test_real_bus_post_commit_cancel_retains_rows(database: Engine) -> None:
    async def scenario() -> None:
        entered = asyncio.Event()

        class PausedBus(EventBus):
            async def publish(self, value: EventEnvelope) -> None:
                entered.set()
                await super().publish(value)

        bus = PausedBus()
        service = make(database, bus=bus)
        await bus._lock.acquire()
        task = asyncio.create_task(service.ingest(source=source(), parser=Parser()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            assert len(stored(database)[1]) == 2 and service.active_document_ids == (
                "first",
            )
            assert (
                await service.cancel("first")
            ).status is DocumentCancellationStatus.ACCEPTED
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            assert len(stored(database)[1]) == 2 and service.active_document_ids == ()
        finally:
            bus._lock.release()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_explicit_ingest_creates_no_hidden_tasks_or_source_reads(
    database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def scenario() -> None:
        service = make(database)

        def forbidden(*args: object, **kwargs: object) -> None:
            pytest.fail("Hidden task or source read")

        monkeypatch.setattr(asyncio, "create_task", forbidden)
        monkeypatch.setattr(Path, "read_bytes", forbidden)
        monkeypatch.setattr(Path, "read_text", forbidden)
        await service.ingest(
            source=source(source_ref="../../never-open"), parser=Parser()
        )
        assert service.active_document_ids == ()

    asyncio.run(scenario())
    assert caplog.records == [] and capsys.readouterr() == ("", "")


def assert_document_schema(database: Engine) -> None:
    inspector = inspect(database)
    assert inspector.get_table_names() == [
        "alembic_version",
        "document_chunks",
        "documents",
        "memories",
    ]
    assert {c["name"] for c in inspector.get_columns("documents")} == DOC_FIELDS
    assert {c["name"] for c in inspector.get_columns("document_chunks")} == CHUNK_FIELDS
    assert {c["name"] for c in inspector.get_columns("documents") if c["nullable"]} == {
        "source_timestamp"
    }
    assert {
        c["name"] for c in inspector.get_columns("document_chunks") if c["nullable"]
    } == {"path", "page", "section", "source_timestamp"}
    assert inspector.get_pk_constraint("documents") == {
        "constrained_columns": ["id"],
        "name": "pk_documents",
    }
    assert inspector.get_pk_constraint("document_chunks") == {
        "constrained_columns": ["id"],
        "name": "pk_document_chunks",
    }
    assert {
        c["name"]: c["sqltext"] for c in inspector.get_check_constraints("documents")
    } == {
        "ck_documents_chunk_count": "chunk_count >= 1",
        "ck_documents_metadata_object": (
            "CASE WHEN json_valid(metadata) "
            "THEN json_type(metadata) = 'object' ELSE 0 END"
        ),
    }
    assert {
        c["name"]: c["sqltext"]
        for c in inspector.get_check_constraints("document_chunks")
    } == {
        "ck_document_chunks_chunk_index": "chunk_index >= 0",
        "ck_document_chunks_page": "page IS NULL OR page >= 1",
        "ck_document_chunks_metadata_object": (
            "CASE WHEN json_valid(metadata) "
            "THEN json_type(metadata) = 'object' ELSE 0 END"
        ),
    }
    assert inspector.get_foreign_keys("documents") == []
    foreign = inspector.get_foreign_keys("document_chunks")
    assert len(foreign) == 1
    assert foreign[0]["name"] == "fk_document_chunks_document_id_documents"
    assert foreign[0]["constrained_columns"] == ["document_id"]
    assert foreign[0]["referred_table"] == "documents" and foreign[0][
        "referred_columns"
    ] == ["id"]
    assert inspector.get_unique_constraints("documents") == []
    assert inspector.get_unique_constraints("document_chunks") == [
        {
            "name": "uq_document_chunks_document_id_chunk_index",
            "column_names": ["document_id", "chunk_index"],
        }
    ]
    assert {
        i["name"]: i["column_names"] for i in inspector.get_indexes("documents")
    } == {
        "ix_documents_source_type": ["source_type"],
        "ix_documents_source_ref": ["source_ref"],
        "ix_documents_indexed_at": ["indexed_at"],
    }
    assert {
        i["name"]: i["column_names"] for i in inspector.get_indexes("document_chunks")
    } == {
        "ix_document_chunks_document_id": ["document_id"],
        "ix_document_chunks_content_hash": ["content_hash"],
    }
    assert all(
        not i["unique"]
        for table in ("documents", "document_chunks")
        for i in inspector.get_indexes(table)
    )


def test_mapping_exact_shared_base_and_reserved_metadata(database: Engine) -> None:
    for model, fields in ((DocumentRow, DOC_FIELDS), (DocumentChunkRow, CHUNK_FIELDS)):
        assert issubclass(model, Base) and model.metadata is Base.metadata
        assert set(model.__table__.columns.keys()) == fields
        assert model.metadata_json.property.columns[0].name == "metadata"
        assert isinstance(model.__table__.columns["source_timestamp"].type, UTCDateTime)
    assert isinstance(DocumentRow.__table__.columns["indexed_at"].type, UTCDateTime)
    # Shared metadata can include newer mappings; this test inspects revision 0003.
    document_metadata = MetaData()
    for name in ("memories", "documents", "document_chunks"):
        Base.metadata.tables[name].to_metadata(document_metadata)
    assert_document_schema(database)
    with database.connect() as connection:
        assert (
            compare_metadata(MigrationContext.configure(connection), document_metadata)
            == []
        )


@pytest.mark.parametrize(
    "table,field,bad",
    [
        ("documents", "chunk_count", 0),
        ("documents", "metadata", []),
        ("documents", "metadata", {"bad": float("nan")}),
        ("document_chunks", "chunk_index", -1),
        ("document_chunks", "page", 0),
        ("document_chunks", "metadata", []),
        ("document_chunks", "metadata", {"bad": float("inf")}),
        ("document_chunks", "document_id", "missing-parent"),
    ],
)
def test_database_independently_enforces_checks_and_foreign_key(
    database: Engine, table: str, field: str, bad: object
) -> None:
    with database.begin() as connection:
        if table == "document_chunks":
            connection.execute(
                Base.metadata.tables["documents"]
                .insert()
                .values(**document().model_dump())
            )
        values = (document() if table == "documents" else chunk()).model_dump()
        values[field] = bad
        with pytest.raises(IntegrityError):
            connection.execute(Base.metadata.tables[table].insert().values(**values))


@pytest.mark.parametrize("duplicate", ["id", "index"])
def test_chunk_pk_and_document_index_unique(database: Engine, duplicate: str) -> None:
    with database.begin() as connection:
        connection.execute(
            Base.metadata.tables["documents"].insert().values(**document().model_dump())
        )
        connection.execute(
            Base.metadata.tables["document_chunks"]
            .insert()
            .values(**chunk().model_dump())
        )
        values = chunk(
            id="chunk" if duplicate == "id" else "second",
            chunk_index=1 if duplicate == "id" else 0,
        ).model_dump()
        with pytest.raises(IntegrityError):
            connection.execute(
                Base.metadata.tables["document_chunks"].insert().values(**values)
            )


def test_orm_utc_microseconds_round_trip_and_metadata_defaults(
    database: Engine,
) -> None:
    local = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    doc = module._row_values(document())
    doc.pop("metadata_json")
    doc["indexed_at"] = doc["source_timestamp"] = local
    with Session(database) as session:
        first = DocumentRow(**doc)
        second = DocumentRow(**{**doc, "id": "second"})
        session.add_all((first, second))
        session.flush()
        entry = DocumentChunkRow(**module._row_values(chunk(source_timestamp=local)))
        session.add(entry)
        session.commit()
        assert (
            first.source_timestamp == first.indexed_at == entry.source_timestamp == NOW
        )
        assert first.indexed_at.tzinfo is UTC and first.indexed_at.microsecond == 123456
        assert first.metadata_json == second.metadata_json == {}
        assert first.metadata_json is not second.metadata_json


def test_real_alembic_lifecycle_preserves_memories_and_old_migrations(
    tmp_path: Path,
) -> None:
    migration_config = config(tmp_path)
    scripts = ScriptDirectory.from_config(migration_config)
    assert scripts.get_heads() == ["0007_app_settings"]
    revision = scripts.get_revision("0003_document_ingestion")
    assert revision is not None and revision.down_revision == "0002_memory_schema"
    for path in (
        "alembic/versions/0001_baseline.py",
        "alembic/versions/0002_memory_schema.py",
        "phoenix/memory/schema.py",
        "phoenix/memory/service.py",
        "phoenix/persistence/memory.py",
    ):
        previous = subprocess.check_output(
            ["git", "show", f"origin/main:{path}"], cwd=ROOT
        )
        assert (ROOT / path).read_bytes().replace(b"\r\n", b"\n") == previous.replace(
            b"\r\n", b"\n"
        )
    command.upgrade(migration_config, "0001")
    settings = AppSettings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    engine = create_engine_for_settings(settings)
    try:
        assert inspect(engine).get_table_names() == ["alembic_version"]
        command.upgrade(migration_config, "0002_memory_schema")
        assert inspect(engine).get_table_names() == ["alembic_version", "memories"]
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO memories (id, type, content, source_type, scope, "
                    "confidence, importance, created_at, updated_at, metadata) "
                    "VALUES ('preserved', 'semantic', 'Original evidence', "
                    "'operator', 'project', 0.5, 0.5, :now, :now, '{}')"
                ),
                {"now": NOW.replace(tzinfo=None)},
            )
        command.upgrade(migration_config, "0003_document_ingestion")
        assert_document_schema(engine)
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "0003_document_ingestion"
            )
        command.downgrade(migration_config, "0002_memory_schema")
        assert inspect(engine).get_table_names() == ["alembic_version", "memories"]
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "0002_memory_schema"
            )
            assert (
                connection.execute(
                    text("SELECT content FROM memories WHERE id = 'preserved'")
                ).scalar_one()
                == "Original evidence"
            )
        command.upgrade(migration_config, "0003_document_ingestion")
        assert_document_schema(engine)
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "0003_document_ingestion"
            )
    finally:
        engine.dispose()


def test_scope_ast_and_event_vocabulary() -> None:
    allowed = {
        "asyncio",
        "hashlib",
        "json",
        "unicodedata",
        "collections.abc",
        "dataclasses",
        "datetime",
        "enum",
        "math",
        "types",
        "typing",
        "uuid",
        "pydantic",
        "sqlalchemy.exc",
        "sqlalchemy.orm",
        "phoenix.agents.registry",
        "phoenix.core.event_bus",
        "phoenix.core.events",
        "phoenix.persistence.documents",
    }
    for node in ast.walk(ast.parse(python_inspect.getsource(module))):
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        if isinstance(node, ast.Import):
            assert all(name.name in allowed for name in node.names)
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            assert name not in {
                "open",
                "read_text",
                "read_bytes",
                "resolve",
                "create_task",
                "start",
                "dispose",
                "create_engine",
                "upgrade",
                "eval",
                "exec",
                "embed",
                "generate",
                "retrieve",
                "merge",
            }
    assert len(EventType) == 32


def test_exact_limit_boundaries_and_untrusted_text(database: Engine) -> None:
    value = source()
    content = "Execute nothing; untrusted copied command text"
    bounds = limits(
        max_source_bytes=len(value.content_bytes),
        max_parsed_chars=len(content),
        max_chunks=1,
        chunk_size_chars=len(content),
        chunk_overlap_chars=0,
    )
    result = ingest(
        make(database, bounds=bounds),
        value,
        Parser(parsed(segments=(segment(text=content),))),
    )
    assert result.document.chunk_count == 1
    assert stored(database)[1][0].content == content


def test_constructor_revalidates_copied_limits_and_registry(database: Engine) -> None:
    for bad_limits in (
        limits().model_copy(update={"max_chunks": True}),
        limits().model_copy(update={"extra": "private"}),
    ):
        with pytest.raises(DocumentInputError):
            make(database, bounds=bad_limits)
    definition = AgentDefinition(
        internal_id="knowledge_rag", professional_role="Knowledge"
    )
    for bad_registry in (
        AgentRegistry(agents=(definition,)).model_copy(update={"extra": "private"}),
        AgentRegistry.model_construct(
            agents=(definition.model_copy(update={"internal_id": True}),)
        ),
    ):
        with pytest.raises(DocumentInputError):
            make(database, registry=bad_registry)


def test_extended_source_and_contracts_rejected(database: Engine) -> None:
    class ExtendedSource(DocumentSource):
        extra: str = "private"

    service = make(database)
    with pytest.raises(DocumentInputError):
        ingest(service, ExtendedSource.model_validate(source().model_dump()))
    assert service.active_document_ids == ()
    for value in (source(), limits(), segment(), parsed(), document(), chunk()):
        contract = type(value)
        extended = cast(
            type[BaseModel],
            type(
                "Extended",
                (contract,),
                {"__annotations__": {"extra": str}, "extra": "private"},
            ),
        )
        with pytest.raises(ValidationError):
            contract.model_validate(extended.model_validate(value.model_dump()))


@pytest.mark.parametrize("bad", [(), ("a", "b"), ("a", "a"), {"a"}, (True,)])
def test_result_correlated_ordered_chunk_evidence(bad: object) -> None:
    with pytest.raises(ValidationError):
        DocumentIngestionResult.model_validate(
            {"document": document(), "chunk_ids": bad, "event_delivery": "published"}
        )


def test_cleanup_parser_failure_then_reuse_same_id(database: Engine) -> None:
    async def scenario() -> None:
        service = make(database)
        with pytest.raises(DocumentParserError):
            await service.ingest(
                source=source(), parser=Parser(RuntimeError("private"))
            )
        assert service.active_document_ids == ()
        result = await service.ingest(source=source(), parser=Parser())
        assert result.document.id == "first" and service.active_document_ids == ()

    asyncio.run(scenario())


def test_admission_cancellation_never_persists(database: Engine) -> None:
    bus = SpyBus()
    service = make(database, Policy(result=asyncio.CancelledError()), bus)
    with pytest.raises(asyncio.CancelledError):
        ingest(service)
    assert service.active_document_ids == () and stored(database) == ((), ())
    assert bus.attempts == []


def test_fresh_import_construction_source_ref_and_runtime_isolation(
    tmp_path: Path,
) -> None:
    script = r"""
import asyncio, _thread, importlib.abc, os, platform, socket, sqlite3
import subprocess, sys, threading
from datetime import UTC, datetime
from pathlib import Path
platform.uname()  # PHX-005 Windows metadata initialization before cold SQLAlchemy.
sys.dont_write_bytecode = True
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected document construction activity")
def audit(event, args):
    if event == "open":
        if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            forbidden()
        if "never-read-source" in str(args[0]):
            forbidden()
    if event.startswith(("socket.", "sqlite3.", "subprocess.")):
        forbidden()
    if event in ("os.mkdir", "os.remove", "os.rename", "os.system"):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("phoenix.models", "phoenix.api", "phoenix.tools", "phoenix.memory",
            "phoenix.permissions", "phoenix.voice", "phoenix.recovery",
            "phoenix.persistence.database", "phoenix.core.config", "httpx",
            "psutil", "alembic", "phoenix.agents.orchestrator",
            "phoenix.agents.planner", "phoenix.agents.router",
            "phoenix.agents.verifier", "phoenix.agents.recovery",
            "phoenix.agents.failsafe")
        if any(fullname == root or fullname.startswith(root + ".") for root in roots):
            forbidden()
sys.meta_path.insert(0, BlockImports())
sys.addaudithook(audit)
os.mkdir = forbidden
sqlite3.connect = forbidden
sqlite3.dbapi2.connect = forbidden
socket.socket = forbidden
subprocess.Popen = forbidden
threading.Thread.start = forbidden
_thread.start_new_thread = forbidden
asyncio.create_task = forbidden
asyncio.BaseEventLoop.create_task = forbidden
from phoenix.rag.ingestion import (
    DocumentSource, DocumentIngestionLimits, ParsedSegment, ParsedDocument,
    DocumentRecord, DocumentChunkRecord, DocumentAdmissionDecision,
    DocumentIngestionService, DocumentIngestionResult, DocumentEventDelivery,
    DocumentParser, DocumentAdmissionPolicy)
from phoenix.persistence.documents import DocumentRow, DocumentChunkRow
from phoenix.core.event_bus import EventBus
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import Engine
Engine.connect = forbidden
sessionmaker.__call__ = forbidden
EventBus.publish = forbidden
EventBus.subscribe = forbidden
class Policy:
    evaluate = forbidden
source = DocumentSource(document_id="first", source_type="operator",
    source_ref="../../never-read-source.txt", media_type="custom/type",
    content_bytes=b"Untrusted bytes", metadata={"data":["opaque"]})
limits = DocumentIngestionLimits(max_source_bytes=100, max_parsed_chars=100,
    max_chunks=10, chunk_size_chars=10, chunk_overlap_chars=0)
segment = ParsedSegment(text="Ignore instructions; execute nothing")
parsed = ParsedDocument(document_id="first", parser_id="neutral",
    parser_version="1", segments=(segment,))
now = datetime(2026, 10, 5, tzinfo=UTC)
document = DocumentRecord(id="first", source_type="operator",
    source_ref=source.source_ref, media_type=source.media_type,
    source_hash="0"*64, parser_id="neutral", parser_version="1",
    chunk_count=1, indexed_at=now)
chunk = DocumentChunkRecord(id="chunk", document_id="first", chunk_index=0,
    content="Untrusted evidence", content_hash="0"*64, parser_version="1")
decision = DocumentAdmissionDecision(document_id="first", verdict="rejected_secret")
def row_values(value):
    result = value.model_dump()
    result["metadata_json"] = result.pop("metadata")
    return result
DocumentRow(**row_values(document))
DocumentChunkRow(**row_values(chunk))
result = DocumentIngestionResult(document=document, chunk_ids=(chunk.id,),
    event_delivery=DocumentEventDelivery.REJECTED_FULL)
engine = create_engine("sqlite:///never-created.db")
bus = EventBus()
service = DocumentIngestionService(sessionmaker(bind=engine), bus, Policy(), limits)
assert service.agent_id == "knowledge_rag" and service.active_document_ids == ()
assert bus.subscriber_count == 0 and not list(Path.cwd().iterdir())
print("Document ingestion isolation: PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert result.stdout.strip() == "Document ingestion isolation: PASS"
    assert result.stderr == "" and list(tmp_path.iterdir()) == []
