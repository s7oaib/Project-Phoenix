"""Portable vector evidence, actual SQLite atomicity, math and isolation boundaries."""

import ast
import inspect as python_inspect
import math
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
from sqlalchemy import Engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from phoenix.core.config import AppSettings
from phoenix.core.events import EventType
from phoenix.persistence.base import Base
from phoenix.persistence.database import (
    create_engine_for_settings,
    create_session_factory,
    database_url,
)
from phoenix.persistence.embeddings import EmbeddingRecordRow
from phoenix.persistence.memory import UTCDateTime
from phoenix.rag import sqlite_vector_store as adapter
from phoenix.rag import vector_store as contracts
from phoenix.rag.sqlite_vector_store import SqliteVectorStore
from phoenix.rag.vector_store import (
    VectorBatch,
    VectorConflictError,
    VectorCorruptionError,
    VectorDimensionError,
    VectorInputError,
    VectorMatch,
    VectorPersistenceError,
    VectorRecord,
    VectorSearchLimitError,
    VectorSearchQuery,
    VectorStore,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 5, 12, 30, 15, 123456, tzinfo=UTC)
FIELDS = {
    "id",
    "source_kind",
    "source_id",
    "source_hash",
    "provider_id",
    "model_id",
    "dimensions",
    "vector",
    "created_at",
}
BAD_IDS = (None, True, 1, 1.5, b"id", object(), "", " \t\n")


def record(**changes: object) -> VectorRecord:
    return VectorRecord.model_validate(
        dict(
            id="first",
            source_kind="document_chunk",
            source_id="opaque:source",
            source_hash="a" * 64,
            provider_id="provider",
            model_id="model",
            dimensions=2,
            vector=(1, 0),
            created_at=NOW,
        )
        | changes
    )


def query(**changes: object) -> VectorSearchQuery:
    return VectorSearchQuery.model_validate(
        dict(
            provider_id="provider",
            model_id="model",
            vector=(1, 0),
            limit=10,
        )
        | changes
    )


def match(**changes: object) -> VectorMatch:
    return VectorMatch.model_validate(
        dict(
            record_id="first",
            source_kind="document_chunk",
            source_id="opaque:source",
            source_hash="a" * 64,
            provider_id="provider",
            model_id="model",
            score=0.5,
        )
        | changes
    )


def config(tmp_path: Path) -> Config:
    settings = AppSettings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    result = Config(str(ROOT / "alembic.ini"))
    result.set_main_option(
        "sqlalchemy.url", str(database_url(settings)).replace("%", "%%")
    )
    return result


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Engine]:
    command.upgrade(config(tmp_path), "head")
    settings = AppSettings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    engine = create_engine_for_settings(settings)
    try:
        yield engine
    finally:
        engine.dispose()


def store(database: Engine, bound: int = 100) -> SqliteVectorStore:
    return SqliteVectorStore(
        create_session_factory(database), max_search_candidates=bound
    )


def rows(database: Engine) -> tuple[VectorRecord, ...]:
    with database.connect() as connection:
        return tuple(
            VectorRecord.model_validate(dict(row))
            for row in connection.execute(
                select(EmbeddingRecordRow.__table__).order_by(EmbeddingRecordRow.id)
            ).mappings()
        )


def safe(error: BaseException) -> None:
    rendered = "".join(traceback.format_exception(error))
    for private in (
        "private-sql",
        "private-parameter",
        "private-db-path",
        "private-detail",
    ):
        assert private not in rendered


def test_interface_exact_fields_no_update_and_required_config(database: Engine) -> None:
    with pytest.raises(TypeError):
        VectorStore()  # type: ignore[abstract]
    assert isinstance(store(database), VectorStore)
    assert set(VectorRecord.model_fields) == FIELDS
    assert set(VectorSearchQuery.model_fields) == {
        "provider_id",
        "model_id",
        "vector",
        "limit",
        "source_kind",
        "source_ids",
    }
    assert set(VectorMatch.model_fields) == {
        "record_id",
        "source_kind",
        "source_id",
        "source_hash",
        "provider_id",
        "model_id",
        "score",
    }
    assert VectorStore.__abstractmethods__ == {
        "add",
        "add_many",
        "get",
        "delete",
        "search",
    }
    assert not hasattr(store(database), "replace") and not hasattr(
        store(database), "update"
    )
    assert (
        python_inspect.signature(SqliteVectorStore)
        .parameters["max_search_candidates"]
        .default
        is python_inspect.Parameter.empty
    )


@pytest.mark.parametrize(
    "field", ["id", "source_kind", "source_id", "provider_id", "model_id"]
)
@pytest.mark.parametrize("bad", BAD_IDS)
def test_record_strict_identifiers(field: str, bad: object) -> None:
    with pytest.raises(ValidationError):
        record(**{field: bad})


@pytest.mark.parametrize(
    "factory,field",
    [
        (query, "provider_id"),
        (query, "model_id"),
        (query, "source_kind"),
        (match, "record_id"),
    ],
)
@pytest.mark.parametrize("bad", BAD_IDS)
def test_other_identifiers(factory: object, field: str, bad: object) -> None:
    if factory is query and field == "source_kind" and bad is None:
        assert query(source_kind=None).source_kind is None
        return
    with pytest.raises(ValidationError):
        factory(**{field: bad})  # type: ignore[operator]


@pytest.mark.parametrize("factory", [record, match])
@pytest.mark.parametrize(
    "bad", [None, True, 1, "a" * 63, "a" * 65, "A" * 64, "g" * 64, " " + "a" * 64]
)
def test_hash_contract(factory: object, bad: object) -> None:
    with pytest.raises(ValidationError):
        factory(source_hash=bad)  # type: ignore[operator]


@pytest.mark.parametrize("factory", [record, query])
@pytest.mark.parametrize(
    "bad",
    [
        (),
        [],
        "text",
        b"vector",
        {1, 2},
        None,
        (True, 0),
        (False, 1),
        ("1", 0),
        (None, 1),
        (object(), 1),
        (math.nan, 1),
        (math.inf, 1),
        (-math.inf, 1),
        (0, 0),
        (-0.0, 0),
        (10**400, 1),
    ],
)
def test_vector_numbers_shape_and_zero_norm(factory: object, bad: object) -> None:
    with pytest.raises((ValueError, TypeError)):
        factory(vector=bad)  # type: ignore[operator]


@pytest.mark.parametrize("bad", [None, True, False, 0, -1, "2", 2.0, 1, 3])
def test_dimension_contract(bad: object) -> None:
    with pytest.raises(ValidationError):
        record(dimensions=bad)


@pytest.mark.parametrize(
    "bad",
    [None, True, 1, 1.5, b"time", NOW.replace(tzinfo=None), "2026-10-05T12:30:00"],
)
def test_timestamp_contract(bad: object) -> None:
    with pytest.raises(ValidationError):
        record(created_at=bad)


def test_detached_ordered_frozen_vectors_utc_and_json() -> None:
    original = [1, -0.5]
    local = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    value = record(
        id=" ID ",
        source_kind=" future_kind ",
        source_id=" Source ",
        vector=original,
        created_at=local,
    )
    original.reverse()
    assert (
        value.id == "ID"
        and value.source_id == "Source"
        and value.source_kind == "future_kind"
    )
    assert (
        value.vector == (1.0, -0.5)
        and value.created_at == NOW
        and value.created_at.tzinfo is UTC
    )
    assert record(created_at=local.isoformat()).created_at == NOW
    for model in (value, query(), match(), VectorBatch(records=(value,))):
        assert (
            type(model).model_validate(model.model_dump()) == model
            if not isinstance(model, VectorBatch)
            else type(model).model_validate(model) == model
        )
        with pytest.raises(ValidationError):
            setattr(model, next(iter(type(model).model_fields)), None)
        with pytest.raises(ValidationError):
            type(model).model_validate(model.model_copy(update={"extra": "private"}))
    assert VectorRecord.model_validate_json(value.model_dump_json()) == value
    assert VectorSearchQuery.model_validate_json(query().model_dump_json()) == query()
    assert VectorMatch.model_validate_json(match().model_dump_json()) == match()
    assert "vector=" not in repr(value)


@pytest.mark.parametrize("bad", [None, True, False, 0, -1, "1", 1.5, 101])
def test_query_limit_contract(bad: object) -> None:
    with pytest.raises(ValidationError):
        query(limit=bad)
    assert query(limit=100).limit == 100


@pytest.mark.parametrize(
    "bad",
    [
        "source",
        {"source"},
        ("x", " x "),
        (True,),
        (1,),
        ("",),
        tuple(str(i) for i in range(2001)),
    ],
)
def test_source_ids_contract(bad: object) -> None:
    with pytest.raises(ValidationError):
        query(source_ids=bad)


def test_source_ids_detached_order_case_and_empty_semantics() -> None:
    ids = [" z ", "a", "A"]
    value = query(source_ids=ids)
    ids.reverse()
    assert value.source_ids == ("z", "a", "A")
    assert query(source_ids=()).source_ids == ()
    assert (
        len(query(source_ids=tuple(str(i) for i in range(2000))).source_ids or ())
        == 2000
    )
    for field in ("provider_id", "model_id", "vector", "limit"):
        data = query().model_dump()
        data.pop(field)
        with pytest.raises(ValidationError):
            VectorSearchQuery.model_validate(data)


@pytest.mark.parametrize(
    "bad", [None, True, False, "0", math.nan, math.inf, -math.inf, -1.01, 1.01]
)
def test_match_score_contract(bad: object) -> None:
    with pytest.raises(ValidationError):
        match(score=bad)


@pytest.mark.parametrize("bad", [None, True, False, 0, -1, "10", 1.5])
def test_constructor_config_strict(database: Engine, bad: object) -> None:
    with pytest.raises(VectorInputError):
        SqliteVectorStore(
            create_session_factory(database), max_search_candidates=cast(int, bad)
        )
    with pytest.raises(VectorInputError):
        SqliteVectorStore(
            cast(sessionmaker[Session], object()), max_search_candidates=1
        )


@pytest.mark.parametrize("bound", [1, 2, 10**100])
def test_positive_candidate_config_does_not_overflow_sql_limit(
    database: Engine, bound: int
) -> None:
    service = store(database, bound)
    service.add(record())
    assert service.search(query())[0].record_id == "first"


def test_exact_boundaries_reject_subclasses_and_forged_copies(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ExtendedRecord(VectorRecord):
        extra: str = "private"

    class ExtendedQuery(VectorSearchQuery):
        extra: str = "private"

    service = store(database)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid boundary must precede session acquisition")

    monkeypatch.setattr(sessionmaker, "__call__", forbidden)
    invalid_records: tuple[object, ...] = (
        None,
        {},
        True,
        ExtendedRecord.model_validate(record().model_dump()),
        record().model_copy(update={"extra": "private"}),
        record().model_copy(update={"vector": (True, 0)}),
        record().model_copy(update={"created_at": NOW.replace(tzinfo=None)}),
        record().model_copy(update={"dimensions": True}),
    )
    for bad in invalid_records:
        with pytest.raises(VectorInputError):
            service.add(cast(VectorRecord, bad))
    invalid_queries: tuple[object, ...] = (
        None,
        {},
        True,
        ExtendedQuery.model_validate(query().model_dump()),
        query().model_copy(update={"vector": (0, 0)}),
        query().model_copy(update={"limit": True}),
        query().model_copy(update={"source_ids": ("a", " a ")}),
        query().model_copy(update={"extra": "private"}),
    )
    for bad_query in invalid_queries:
        with pytest.raises(VectorInputError):
            service.search(cast(VectorSearchQuery, bad_query))


@pytest.mark.parametrize(
    "records",
    [
        (),
        (record(), record()),
        (record(), record(id="different")),
        (
            record(),
            record(id="different", source_id="different", dimensions=1, vector=(1,)),
        ),
    ],
)
def test_batch_prevalidation_no_session(
    database: Engine, monkeypatch: pytest.MonkeyPatch, records: tuple[VectorRecord, ...]
) -> None:
    service = store(database)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid batch must precede session acquisition")

    monkeypatch.setattr(sessionmaker, "__call__", forbidden)
    with pytest.raises(VectorInputError):
        service.add_many(records)


def test_batch_detaches_records_order_and_spaces() -> None:
    first = record()
    second = record(
        id="second",
        source_id="other",
        model_id="different",
        dimensions=3,
        vector=(1, 0, 0),
    )
    values = [second, first]
    batch = VectorBatch(records=cast(tuple[VectorRecord, ...], values))
    values.reverse()
    object.__setattr__(first, "source_hash", "b" * 64)
    assert batch.records[0] == second and batch.records[1].source_hash == "a" * 64
    assert batch.records[0] is not second


@pytest.mark.parametrize(
    "bad",
    [
        None,
        True,
        {},
        "records",
        {"first"},
        (None,),
        ({},),
        (record().model_copy(update={"extra": "private"}),),
    ],
)
def test_malformed_batch_explicit_before_session(
    database: Engine, monkeypatch: pytest.MonkeyPatch, bad: object
) -> None:
    service = store(database)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid batch must not open a session")

    monkeypatch.setattr(sessionmaker, "__call__", forbidden)
    with pytest.raises(VectorInputError):
        service.add_many(cast(tuple[VectorRecord, ...], bad))


def test_add_many_round_trip_order_detachment_and_no_source_resolution(
    database: Engine,
) -> None:
    service: VectorStore = store(database)
    first = record(id="z", source_kind="future", source_id="../../does-not-exist")
    second = record(id="a", source_id="second", vector=(-1, 0))
    result = service.add_many((first, second))
    assert result == (first, second) and result[0] is not first
    object.__setattr__(first, "vector", (0.0, 1.0))
    assert service.get(" z ") == result[0] and service.get("a") == second
    assert service.get("missing") is None
    with Session(database) as session:
        orm = session.get(EmbeddingRecordRow, "z")
        assert orm is not None and orm.vector == [1.0, 0.0]
    assert len(rows(database)) == 2
    assert all(not isinstance(value, (Session, EmbeddingRecordRow)) for value in result)
    assert rows(database)[1].created_at.tzinfo is UTC


@pytest.mark.parametrize("conflict", ["id", "source"])
def test_durable_conflicts_preserve_original(database: Engine, conflict: str) -> None:
    service = store(database)
    service.add(record())
    original = rows(database)
    bad = record(
        id="first" if conflict == "id" else "new",
        source_id="other" if conflict == "id" else "opaque:source",
        source_hash="b" * 64,
    )
    with pytest.raises(VectorConflictError):
        service.add_many((record(id="new-first", source_id="new-first"), bad))
    assert rows(database) == original


def test_spaces_consistent_dimensions_and_explicit_reembedding(
    database: Engine,
) -> None:
    service = store(database)
    service.add(record())
    different = record(
        id="different", source_id="different", dimensions=3, vector=(1, 2, 3)
    )
    with pytest.raises(VectorDimensionError):
        service.add(different)
    assert len(rows(database)) == 1
    service.add(
        record(id="other-space", provider_id="other", dimensions=3, vector=(1, 2, 3))
    )
    service.add(record(id="other-model", model_id="other", dimensions=1, vector=(1,)))
    assert service.delete("first") is True and service.delete("first") is False
    assert service.add(different) == different
    assert len(rows(database)) == 3


@pytest.mark.parametrize("operation", ["add", "search"])
def test_corrupt_inconsistent_space_fails_even_outside_source_filters(
    database: Engine, operation: str
) -> None:
    service = store(database)
    service.add(record())
    with database.begin() as connection:
        connection.execute(
            Base.metadata.tables["embedding_records"]
            .insert()
            .values(
                **record(
                    id="manual", source_id="manual", dimensions=3, vector=(1, 2, 3)
                ).model_dump()
            )
        )
    original = rows(database)
    with pytest.raises(VectorCorruptionError):
        if operation == "add":
            service.add(record(id="new", source_id="new"))
        else:
            service.search(query(source_ids=("opaque:source",)))
    assert rows(database) == original


@pytest.mark.parametrize("bad", [1.5, "invalid"])
@pytest.mark.parametrize("operation", ["add", "search"])
def test_noninteger_stored_space_dimension_not_coerced(
    database: Engine, bad: object, operation: str
) -> None:
    service = store(database)
    service.add(record())
    with database.begin() as connection:
        connection.execute(
            text("UPDATE embedding_records SET dimensions=:bad"), {"bad": bad}
        )
    with pytest.raises(VectorCorruptionError):
        if operation == "add":
            service.add(record(id="new", source_id="new"))
        else:
            service.search(query())
    assert service.delete("first") is True


@pytest.mark.parametrize("method", ["get", "delete"])
@pytest.mark.parametrize("bad", BAD_IDS)
def test_get_delete_strict_identity(database: Engine, method: str, bad: object) -> None:
    with pytest.raises(VectorInputError):
        getattr(store(database), method)(bad)


def test_delete_exact_case_and_detached_get(database: Engine) -> None:
    service = store(database)
    service.add_many((record(id="ID"), record(id="id", source_id="other")))
    read = service.get("ID")
    assert read is not None
    object.__setattr__(read, "vector", (0.0, 1.0))
    assert service.get("ID") == record(id="ID")
    assert service.delete(" ID ") is True
    assert service.get("id") is not None and service.delete("missing") is False


@pytest.mark.parametrize("failure_at", ["early", "late"])
def test_real_batch_constraint_failure_rolls_back_all_new_rows(
    database: Engine, failure_at: str
) -> None:
    service = store(database)
    service.add(record())
    original = rows(database)
    with database.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_embedding BEFORE INSERT ON embedding_records "
            f"WHEN NEW.id = '{failure_at}' "
            "BEGIN SELECT RAISE(ABORT, 'private-detail'); END"
        )
    with pytest.raises(VectorPersistenceError) as error:
        service.add_many(
            (record(id="early", source_id="early"), record(id="late", source_id="late"))
        )
    safe(error.value)
    assert rows(database) == original


@pytest.mark.parametrize("conflict", ["id", "source"])
def test_actual_unique_race_rolls_back_batch(database: Engine, conflict: str) -> None:
    store(database).add(record())
    original = rows(database)

    class StaleConflict(Session):
        def scalar(self, *args: object, **kwargs: object) -> None:
            return None

    service = SqliteVectorStore(
        sessionmaker(database, class_=StaleConflict), max_search_candidates=10
    )
    bad = record(
        id="first" if conflict == "id" else "duplicate",
        source_id="else" if conflict == "id" else "opaque:source",
    )
    with pytest.raises(VectorConflictError):
        service.add_many((record(id="new", source_id="new"), bad))
    assert rows(database) == original


@pytest.mark.parametrize("operation", ["add", "delete"])
def test_commit_failure_rolls_back_closes_and_redacts(
    database: Engine, operation: str
) -> None:
    store(database).add(record())
    original = rows(database)
    closed: list[bool] = []

    class FailingCommit(Session):
        def commit(self) -> None:
            self.flush()
            raise OperationalError(
                "private-sql",
                {"vector": "private-parameter"},
                Exception("private-db-path"),
            )

        def close(self) -> None:
            closed.append(True)
            super().close()

    service = SqliteVectorStore(
        sessionmaker(database, class_=FailingCommit), max_search_candidates=10
    )
    with pytest.raises(VectorPersistenceError) as error:
        if operation == "add":
            service.add(record(id="new", source_id="new"))
        else:
            service.delete("first")
    safe(error.value)
    assert closed == [True] and rows(database) == original


def test_writer_reservation_prevents_competing_space_dimensions(
    database: Engine,
) -> None:
    # Compete through another real connection at the first writer's commit boundary.
    @event.listens_for(database, "connect")
    def no_busy_wait(connection: object, context: object) -> None:
        connection.execute("PRAGMA busy_timeout=0")  # type: ignore[attr-defined]

    competitor = store(database)
    different = record(
        id="different", source_id="different", dimensions=3, vector=(1, 2, 3)
    )
    observations: list[bool] = []

    class CompetingCommit(Session):
        def commit(self) -> None:
            with pytest.raises(VectorPersistenceError):
                competitor.add(different)
            observations.append(True)
            super().commit()

    first = SqliteVectorStore(
        sessionmaker(database, class_=CompetingCommit), max_search_candidates=10
    )
    first.add(record())
    assert observations == [True] and rows(database) == (record(),)
    with pytest.raises(VectorDimensionError):
        competitor.add(different)
    assert rows(database) == (record(),)


def test_cosine_scores_deterministic_ties_limit_and_unchanged_storage(
    database: Engine,
) -> None:
    service = store(database)
    values = (
        record(id="z-positive", source_id="z", vector=(2, 0)),
        record(id="a-positive", source_id="a", vector=(1, 0)),
        record(id="diagonal", source_id="d", vector=(1, 1)),
        record(id="zero", source_id="zero", vector=(0, 1)),
        record(id="negative", source_id="neg", vector=(-1, 0)),
    )
    service.add_many(values)
    original = rows(database)
    matches = service.search(query())
    assert [m.record_id for m in matches] == [
        "a-positive",
        "z-positive",
        "diagonal",
        "zero",
        "negative",
    ]
    assert [m.score for m in matches] == pytest.approx([1, 1, 1 / math.sqrt(2), 0, -1])
    assert service.search(query(limit=2)) == matches[:2]
    assert service.search(query()) == matches
    assert rows(database) == original
    assert all(not hasattr(m, "vector") and not hasattr(m, "content") for m in matches)
    assert matches[0].source_id == "a" and matches[0].source_hash == "a" * 64


@pytest.mark.parametrize(
    "vector",
    [
        (1e308, 1e308),
        (5e-324, 5e-324),
        (1e308, 5e-324),
        (-1e308, -1e308),
        (1e-300, 1e-300),
        (3, 4),
    ],
)
def test_finite_extremes_do_not_overflow_or_underflow_cosine(
    database: Engine, vector: tuple[float, float]
) -> None:
    service = store(database)
    value = record(vector=vector)
    service.add(value)
    assert service.get("first") == value
    result = service.search(query(vector=vector))[0]
    assert math.isfinite(result.score) and result.score == pytest.approx(1)
    assert -1 <= result.score <= 1
    assert service.search(query(vector=tuple(-v for v in vector)))[
        0
    ].score == pytest.approx(-1)


def test_only_tiny_cosine_overshoot_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    for score, expected in ((1 + 1e-15, 1), (-1 - 1e-15, -1)):
        monkeypatch.setattr(adapter, "fsum", lambda values, score=score: score)
        assert adapter._cosine((1.0,), (1.0,)) == expected
    for bad in (1.001, -1.001, math.inf, math.nan):
        monkeypatch.setattr(adapter, "fsum", lambda values, score=bad: score)
        with pytest.raises(VectorCorruptionError):
            adapter._cosine((1.0,), (1.0,))


def test_search_exact_spaces_filters_case_and_empty_candidates(
    database: Engine,
) -> None:
    service = store(database)
    service.add_many(
        (
            record(id="a", source_kind="memory", source_id="shared"),
            record(id="b", source_kind="document_chunk", source_id="shared"),
            record(id="c", source_kind="Memory", source_id="Other"),
            record(
                id="d",
                source_kind="memory",
                source_id="other",
                model_id="other",
                dimensions=3,
                vector=(1, 0, 0),
            ),
            record(
                id="e",
                source_kind="memory",
                source_id="other",
                provider_id="other",
                dimensions=1,
                vector=(1,),
            ),
        )
    )
    assert [m.record_id for m in service.search(query())] == ["a", "b", "c"]
    assert [m.record_id for m in service.search(query(source_kind="memory"))] == ["a"]
    assert [m.record_id for m in service.search(query(source_ids=("shared",)))] == [
        "a",
        "b",
    ]
    assert [
        m.record_id
        for m in service.search(query(source_kind="Memory", source_ids=("Other",)))
    ] == ["c"]
    assert service.search(query(source_ids=())) == ()
    assert service.search(query(source_ids=("missing",), vector=(1,))) == ()
    assert service.search(query(model_id="MODEL")) == ()
    assert service.search(query(provider_id="PROVIDER")) == ()
    assert [
        m.record_id for m in service.search(query(model_id="other", vector=(1, 0, 0)))
    ] == ["d"]
    with pytest.raises(VectorDimensionError):
        service.search(query(vector=(1,)))


def test_candidate_bound_precedes_vector_loading_and_math(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = store(database, 2)
    service.add_many(tuple(record(id=str(i), source_id=str(i)) for i in range(3)))

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Overflow must precede vector reconstruction/calculation")

    with monkeypatch.context() as blocked:
        blocked.setattr(adapter, "_unit", forbidden)
        blocked.setattr(adapter, "_record", forbidden)
        with pytest.raises(VectorSearchLimitError):
            service.search(query(limit=1))
    assert len(service.search(query(source_ids=("0", "1")))) == 2
    assert (
        len(
            service.search(
                query(
                    source_ids=tuple(str(i) for i in range(2000)), source_kind="absent"
                )
            )
        )
        == 0
    )
    assert len(rows(database)) == 3
    assert service.max_search_candidates == 2
    with pytest.raises(AttributeError):
        service.max_search_candidates = 99  # type: ignore[misc]


def test_search_uses_one_read_snapshot_when_external_write_follows_count(
    database: Engine,
) -> None:
    with database.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar() == "wal"
    service = store(database, 2)
    service.add_many((record(id="a", source_id="a"), record(id="b", source_id="b")))
    added: list[bool] = []

    def external_write(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if "count(*)" in statement and not added:
            added.append(True)
            with database.begin() as other:
                other.execute(
                    Base.metadata.tables["embedding_records"]
                    .insert()
                    .values(**record(id="c", source_id="c").model_dump())
                )

    event.listen(database, "after_cursor_execute", external_write)
    try:
        assert [m.record_id for m in service.search(query())] == ["a", "b"]
    finally:
        event.remove(database, "after_cursor_execute", external_write)
    assert added == [True] and len(rows(database)) == 3
    with pytest.raises(VectorSearchLimitError):
        service.search(query())


@pytest.mark.parametrize(
    "field,bad",
    [
        ("vector", [True, 0]),
        ("vector", ["1", 0]),
        ("vector", [0, 0]),
        ("vector", []),
        ("vector", [1]),
        ("vector", [1, 2, 3]),
        ("source_hash", "BAD"),
        ("source_kind", " "),
        ("created_at", "private-detail"),
    ],
)
@pytest.mark.parametrize("operation", ["get", "search"])
def test_stored_corruption_fails_without_silent_skip(
    database: Engine, field: str, bad: object, operation: str
) -> None:
    service = store(database)
    service.add(record())
    with database.begin() as connection:
        # Database permits these values; reconstruction remains authoritative.
        if field == "created_at":
            connection.execute(
                text("UPDATE embedding_records SET created_at=:bad"), {"bad": bad}
            )
        else:
            connection.execute(
                Base.metadata.tables["embedding_records"]
                .update()
                .values(**{field: bad})
            )
    with pytest.raises(VectorCorruptionError) as error:
        if operation == "get":
            service.get("first")
        else:
            service.search(query(limit=1))
    safe(error.value)
    # Explicit exact deletion can remove malformed vector evidence without reading it.
    assert service.delete("first") is True


@pytest.mark.parametrize("bad", ["{invalid", '{"object":1}', "[NaN,1]", "[Infinity,1]"])
def test_manually_corrupt_json_rejected(database: Engine, bad: str) -> None:
    service = store(database)
    service.add(record())
    with database.begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            text("UPDATE embedding_records SET vector=:bad"), {"bad": bad}
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")
    for method in (lambda: service.get("first"), lambda: service.search(query())):
        with pytest.raises(VectorCorruptionError):
            method()


@pytest.mark.parametrize("method", ["add", "add_many", "get", "delete", "search"])
def test_database_error_safe_and_missing_directory_not_repaired(
    tmp_path: Path, method: str
) -> None:
    settings = AppSettings(data_dir=tmp_path / "private-db-path", _env_file=None)  # type: ignore[call-arg]
    engine = create_engine_for_settings(settings)
    try:
        service = store(engine)
        argument: object = {
            "add": record(),
            "add_many": (record(),),
            "get": "first",
            "delete": "first",
            "search": query(),
        }[method]
        with pytest.raises(VectorPersistenceError) as error:
            getattr(service, method)(argument)
        safe(error.value)
        assert not settings.data_dir.exists()
    finally:
        engine.dispose()


def assert_embedding_schema(database: Engine) -> None:
    inspector = inspect(database)
    assert inspector.get_table_names() == [
        "alembic_version",
        # PHX-104 (ADR-089) adds app_settings in 0007_app_settings.
        "app_settings",
        "audit_events",
        # PHX-102 (ADR-086) adds the mission tables in 0006_missions.
        "conversations",
        "document_chunks",
        "documents",
        "embedding_records",
        "memories",
        "messages",
        "mission_traces",
        "missions",
        "permission_grants",
        "permission_requests",
    ]
    columns = inspector.get_columns("embedding_records")
    assert {c["name"] for c in columns} == FIELDS
    assert not any(c["nullable"] for c in columns)
    assert {c["name"]: str(c["type"]) for c in columns} == {
        "id": "VARCHAR",
        "source_kind": "VARCHAR",
        "source_id": "VARCHAR",
        "source_hash": "VARCHAR",
        "provider_id": "VARCHAR",
        "model_id": "VARCHAR",
        "dimensions": "INTEGER",
        "vector": "JSON",
        "created_at": "DATETIME",
    }
    assert inspector.get_pk_constraint("embedding_records") == {
        "name": "pk_embedding_records",
        "constrained_columns": ["id"],
    }
    assert inspector.get_unique_constraints("embedding_records") == [
        {
            "name": "uq_embedding_records_source_model",
            "column_names": ["source_kind", "source_id", "provider_id", "model_id"],
        }
    ]
    assert {
        c["name"]: c["sqltext"]
        for c in inspector.get_check_constraints("embedding_records")
    } == {
        "ck_embedding_records_dimensions": "dimensions >= 1",
        "ck_embedding_records_vector_array": (
            "CASE WHEN json_valid(vector) THEN json_type(vector) = 'array' ELSE 0 END"
        ),
    }
    assert inspector.get_foreign_keys("embedding_records") == []
    assert {
        i["name"]: i["column_names"] for i in inspector.get_indexes("embedding_records")
    } == {
        "ix_embedding_records_source": ["source_kind", "source_id"],
        "ix_embedding_records_model": ["provider_id", "model_id"],
        "ix_embedding_records_created_at": ["created_at"],
    }
    assert all(not i["unique"] for i in inspector.get_indexes("embedding_records"))


def test_shared_base_mapping_exact_schema_and_full_head_no_drift(
    database: Engine,
) -> None:
    assert issubclass(EmbeddingRecordRow, Base)
    assert EmbeddingRecordRow.metadata is Base.metadata
    assert set(EmbeddingRecordRow.__table__.columns.keys()) == FIELDS
    assert isinstance(
        EmbeddingRecordRow.__table__.columns["created_at"].type, UTCDateTime
    )
    assert set(Base.metadata.tables) == {
        "audit_events",
        "memories",
        "documents",
        "document_chunks",
        "embedding_records",
        # PHX-102 (ADR-086): alembic/env.py registers the mission rows.
        "conversations",
        "messages",
        "missions",
        "mission_traces",
        "permission_requests",
        "permission_grants",
        # PHX-104 (ADR-089): alembic/env.py registers the settings row.
        "app_settings",
    }
    assert_embedding_schema(database)
    with database.connect() as connection:
        assert (
            compare_metadata(MigrationContext.configure(connection), Base.metadata)
            == []
        )


@pytest.mark.parametrize(
    "field,bad",
    [
        ("dimensions", 0),
        ("dimensions", -1),
        ("vector", {}),
        ("vector", None),
        ("vector", "text"),
    ],
)
def test_database_constraints_independent_of_domain_validation(
    database: Engine, field: str, bad: object
) -> None:
    values = record().model_dump()
    values[field] = bad
    with database.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                Base.metadata.tables["embedding_records"].insert().values(**values)
            )


def test_utc_orm_round_trip_microseconds_and_no_vector_renormalization(
    database: Engine,
) -> None:
    local = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    values = record(vector=(3, 4)).model_dump()
    values["created_at"] = local
    with Session(database) as session:
        row = EmbeddingRecordRow(**values)
        session.add(row)
        session.commit()
        assert row.created_at == NOW and row.created_at.tzinfo is UTC
        assert row.created_at.microsecond == 123456 and row.vector == [3.0, 4.0]
    assert store(database).get("first") == record(vector=(3, 4))


def seed_sources(database: Engine) -> None:
    with database.begin() as connection:
        connection.execute(
            Base.metadata.tables["memories"]
            .insert()
            .values(
                id="memory",
                type="semantic",
                content="Ordinary untrusted evidence",
                source_type="operator",
                scope="project",
                confidence=0.5,
                importance=0.5,
                created_at=NOW,
                updated_at=NOW,
                embedding_ref="preserve-existing-ref",
                metadata={},
            )
        )
        connection.execute(
            Base.metadata.tables["documents"]
            .insert()
            .values(
                id="document",
                source_type="operator",
                source_ref="opaque:document",
                media_type="custom/type",
                source_hash="a" * 64,
                parser_id="neutral",
                parser_version="1",
                chunk_count=1,
                indexed_at=NOW,
                metadata={},
            )
        )
        connection.execute(
            Base.metadata.tables["document_chunks"]
            .insert()
            .values(
                id="chunk",
                document_id="document",
                chunk_index=0,
                content="Original chunk",
                content_hash="b" * 64,
                parser_version="1",
                metadata={},
            )
        )


def source_snapshot(database: Engine) -> tuple[tuple[dict[str, object], ...], ...]:
    with database.connect() as connection:
        return tuple(
            tuple(
                dict(row)
                for row in connection.execute(
                    select(Base.metadata.tables[table])
                ).mappings()
            )
            for table in ("memories", "documents", "document_chunks")
        )


def test_source_rows_neither_resolved_nor_mutated_and_nothing_logged(
    database: Engine,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_sources(database)
    previous = source_snapshot(database)
    statements: list[str] = []

    def observe(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(database, "before_cursor_execute", observe)
    try:
        service = store(database)
        service.add_many(
            (
                record(id="memory-vector", source_kind="memory", source_id="memory"),
                record(id="chunk-vector", source_id="chunk"),
            )
        )
        assert service.get("memory-vector") is not None
        assert len(service.search(query())) == 2
        assert service.delete("memory-vector") is True
        assert service.delete("chunk-vector") is True
    finally:
        event.remove(database, "before_cursor_execute", observe)
    assert all(
        not any(
            table in statement for table in ("memories", "documents", "document_chunks")
        )
        for statement in statements
    )
    assert source_snapshot(database) == previous
    assert caplog.records == [] and capsys.readouterr() == ("", "")


def test_real_migration_lifecycle_preserves_all_previous_sources(
    tmp_path: Path,
) -> None:
    migration = config(tmp_path)
    scripts = ScriptDirectory.from_config(migration)
    assert scripts.get_heads() == ["0007_app_settings"]
    revision = scripts.get_revision("0004_embedding_records")
    assert revision is not None and revision.down_revision == "0003_document_ingestion"
    for path in (
        "alembic/versions/0001_baseline.py",
        "alembic/versions/0002_memory_schema.py",
        "alembic/versions/0003_document_ingestion.py",
        "phoenix/models/embeddings.py",
        "phoenix/memory/schema.py",
        "phoenix/memory/service.py",
        "phoenix/rag/ingestion.py",
        "phoenix/persistence/memory.py",
        "phoenix/persistence/documents.py",
    ):
        previous = subprocess.check_output(
            ["git", "show", f"origin/main:{path}"], cwd=ROOT
        )
        assert (ROOT / path).read_bytes().replace(b"\r\n", b"\n") == previous.replace(
            b"\r\n", b"\n"
        )
    settings = AppSettings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    engine = create_engine_for_settings(settings)
    try:
        command.upgrade(migration, "0001")
        assert inspect(engine).get_table_names() == ["alembic_version"]
        command.upgrade(migration, "0002_memory_schema")
        assert inspect(engine).get_table_names() == ["alembic_version", "memories"]
        command.upgrade(migration, "0003_document_ingestion")
        assert inspect(engine).get_table_names() == [
            "alembic_version",
            "document_chunks",
            "documents",
            "memories",
        ]
        seed_sources(engine)
        previous_sources = source_snapshot(engine)
        command.upgrade(migration, "head")
        assert_embedding_schema(engine)
        store(engine).add(record())
        assert source_snapshot(engine) == previous_sources
        command.downgrade(migration, "0003_document_ingestion")
        assert inspect(engine).get_table_names() == [
            "alembic_version",
            "document_chunks",
            "documents",
            "memories",
        ]
        assert source_snapshot(engine) == previous_sources
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "0003_document_ingestion"
            )
        command.upgrade(migration, "head")
        assert_embedding_schema(engine)
        assert source_snapshot(engine) == previous_sources and rows(engine) == ()
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "0007_app_settings"
            )
            assert (
                compare_metadata(MigrationContext.configure(connection), Base.metadata)
                == []
            )
    finally:
        engine.dispose()


def test_contract_extra_fields_and_exact_subclasses() -> None:
    for value in (record(), query(), match(), VectorBatch(records=(record(),))):
        contract = type(value)
        data = dict(vars(value))
        with pytest.raises(ValidationError):
            contract.model_validate({**data, "extra": "private"})
        extended = cast(
            type[BaseModel],
            type(
                "Extended",
                (contract,),
                {"__annotations__": {"extra": str}, "extra": "private"},
            ),
        )
        with pytest.raises(ValidationError):
            contract.model_validate(extended.model_validate(data))


def test_scope_ast_frozen_events_and_unchanged_dependencies() -> None:
    allowed = {
        "abc",
        "datetime",
        "math",
        "typing",
        "pydantic",
        "sqlalchemy",
        "sqlalchemy.exc",
        "sqlalchemy.orm",
        "phoenix.persistence.embeddings",
        "phoenix.rag.vector_store",
    }
    for module in (contracts, adapter):
        for node in ast.walk(ast.parse(python_inspect.getsource(module))):
            if isinstance(node, ast.Import):
                assert all(name.name in allowed for name in node.names)
            if isinstance(node, ast.ImportFrom):
                assert node.module in allowed
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                assert name not in {
                    "embed",
                    "generate",
                    "retrieve",
                    "publish",
                    "subscribe",
                    "load_extension",
                    "create_task",
                    "start",
                    "dispose",
                    "upgrade",
                    "open",
                    "resolve",
                    "eval",
                    "exec",
                }
    assert len(EventType) == 32
    # PHX-050 owns the authorized Python dependency promotion and its exact-diff
    # gate in test_browser_tool; PHX-033 still freezes desktop dependencies.
    for path in ("package.json", "package-lock.json"):
        previous = subprocess.check_output(
            ["git", "show", f"origin/main:{path}"], cwd=ROOT
        )
        assert (ROOT / path).read_bytes().replace(b"\r\n", b"\n") == previous.replace(
            b"\r\n", b"\n"
        )


def test_fresh_import_constructor_and_mapping_no_runtime_side_effects(
    tmp_path: Path,
) -> None:
    script = r"""
import asyncio, _thread, importlib.abc, os, platform, socket, sqlite3
import subprocess, sys, threading
from datetime import UTC, datetime
from pathlib import Path
platform.uname()  # Existing PHX-005 Windows stdlib platform initialization precedent.
sys.dont_write_bytecode = True
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected vector construction activity")
def audit(event, args):
    if event == "open":
        if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            forbidden()
    if event.startswith(("socket.", "sqlite3.", "subprocess.")):
        forbidden()
    if event in ("os.mkdir", "os.remove", "os.rename", "os.system"):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("phoenix.models", "phoenix.memory", "phoenix.agents", "phoenix.api",
            "phoenix.tools", "phoenix.permissions", "phoenix.voice", "phoenix.core",
            "phoenix.persistence.database", "phoenix.persistence.documents",
            "phoenix.rag.ingestion", "alembic", "httpx", "psutil", "numpy", "scipy",
            "sqlite_vec", "faiss", "chromadb")
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
from phoenix.rag.vector_store import (
    VectorRecord, VectorBatch, VectorSearchQuery, VectorMatch, VectorStore)
from phoenix.rag.sqlite_vector_store import SqliteVectorStore
from phoenix.persistence.embeddings import EmbeddingRecordRow
from phoenix.persistence.base import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.engine import Engine
Engine.connect = forbidden
sessionmaker.__call__ = forbidden
now = datetime(2026, 10, 5, tzinfo=UTC)
record = VectorRecord(id="first", source_kind="future", source_id="opaque:missing",
    source_hash="a"*64, provider_id="neutral", model_id="model", dimensions=2,
    vector=[1, 0], created_at=now)
batch = VectorBatch(records=[record])
query = VectorSearchQuery(provider_id="neutral", model_id="model",
    vector=[1, 0], limit=1)
match = VectorMatch(record_id="first", source_kind="future", source_id="opaque:missing",
    source_hash="a"*64, provider_id="neutral", model_id="model", score=1)
EmbeddingRecordRow(**record.model_dump())
assert VectorStore.__abstractmethods__ == {"add", "add_many", "get", "delete", "search"}
engine = create_engine("sqlite:///never-created.db")
store = SqliteVectorStore(sessionmaker(bind=engine), max_search_candidates=10)
assert store.max_search_candidates == 10
assert set(Base.metadata.tables) == {"memories", "embedding_records"}
assert not list(Path.cwd().iterdir())
print("Vector store isolation: PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert result.stdout.strip() == "Vector store isolation: PASS"
    assert result.stderr == "" and list(tmp_path.iterdir()) == []
