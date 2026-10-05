"""PHX-030 neutral contracts and real temporary SQLite migration evidence."""

import ast
import inspect as python_inspect
import json
import subprocess
import sys
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import Engine, MetaData, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, StatementError

from alembic import command
from phoenix.core.config import AppSettings
from phoenix.memory import schema
from phoenix.memory.schema import MemoryRecord, MemoryType
from phoenix.persistence import memory
from phoenix.persistence.base import Base
from phoenix.persistence.database import (
    create_engine_for_settings,
    create_session_factory,
    database_url,
)
from phoenix.persistence.memory import MemoryRow

ROOT = Path(__file__).resolve().parents[1]
CREATED = datetime(2026, 10, 5, 12, 30, 15, 123456, tzinfo=UTC)
FIELDS = {
    "id",
    "type",
    "content",
    "source_type",
    "source_ref",
    "scope",
    "confidence",
    "importance",
    "created_at",
    "updated_at",
    "last_accessed_at",
    "expires_at",
    "embedding_ref",
    "metadata",
}
NULLABLE = {"source_ref", "last_accessed_at", "expires_at", "embedding_ref"}
INDEXES = {
    "ix_memories_type": ["type"],
    "ix_memories_scope": ["scope"],
    "ix_memories_created_at": ["created_at"],
    "ix_memories_expires_at": ["expires_at"],
}
CHECKS = {
    "ck_memories_type",
    "ck_memories_confidence",
    "ck_memories_importance",
    "ck_memories_metadata_object",
}
BAD_TEXT = (None, True, 1, 1.5, b"text", object(), "", " \t\n")
BAD_SCORE = (
    None,
    True,
    False,
    "0.5",
    b"0.5",
    object(),
    Decimal("0.5"),
    -0.01,
    1.01,
    float("nan"),
    float("inf"),
    float("-inf"),
)
BAD_JSON = (
    b"data",
    {1, 2},
    object(),
    lambda: None,
    float("nan"),
    float("inf"),
    float("-inf"),
    datetime(2026, 1, 1, tzinfo=UTC),
    Decimal("0.5"),
)


def record(**changes: object) -> MemoryRecord:
    return MemoryRecord.model_validate(
        {
            "id": "memory",
            "type": "semantic",
            "content": "  Original content\n",
            "source_type": "user-observation",
            "scope": "project:test",
            "confidence": 0.5,
            "importance": 0.75,
            "created_at": CREATED,
            "updated_at": CREATED,
            **changes,
        }
    )


def row(value: MemoryRecord) -> MemoryRow:
    fields = value.model_dump()
    fields["memory_type"] = fields.pop("type")
    fields["metadata_json"] = fields.pop("metadata")
    return MemoryRow(**fields)


def migration_config(settings: AppSettings) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", str(database_url(settings)).replace("%", "%%")
    )
    return config


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Engine]:
    # Pydantic's synthesized signature omits this BaseSettings option.
    settings = AppSettings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    command.upgrade(migration_config(settings), "0002_memory_schema")
    engine = create_engine_for_settings(settings)
    try:
        yield engine
    finally:
        engine.dispose()


def assert_memory_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    assert inspector.get_table_names() == ["alembic_version", "memories"]
    columns = inspector.get_columns("memories")
    assert {column["name"] for column in columns} == FIELDS
    assert {column["name"] for column in columns if column["nullable"]} == NULLABLE
    assert inspector.get_pk_constraint("memories")["constrained_columns"] == ["id"]
    checks = inspector.get_check_constraints("memories")
    assert {check["name"] for check in checks} == CHECKS
    assert {
        index["name"]: index["column_names"]
        for index in inspector.get_indexes("memories")
    } == INDEXES
    assert all(not index["unique"] for index in inspector.get_indexes("memories"))
    assert inspector.get_unique_constraints("memories") == []
    assert inspector.get_foreign_keys("memories") == []


def test_exact_public_fields_and_memory_vocabulary() -> None:
    assert set(MemoryRecord.model_fields) == FIELDS
    assert [value.value for value in MemoryType] == [
        "working",
        "episodic",
        "semantic",
        "user",
        "project",
        "agent",
    ]
    assert {"secret", "document", "chunk", "embedding", "message", "tool"}.isdisjoint(
        MemoryType
    )


@pytest.mark.parametrize("memory_type", tuple(MemoryType))
def test_all_six_types_round_trip(memory_type: MemoryType) -> None:
    value = record(type=memory_type)
    assert value.type == memory_type
    assert MemoryRecord.model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        True,
        1,
        b"semantic",
        "",
        " semantic ",
        "secret",
        "document",
        "chunk",
        "embedding",
        "conversation",
        "message",
        "tool",
        "audit",
    ],
)
def test_invalid_or_coerced_memory_types_rejected(invalid: object) -> None:
    with pytest.raises(ValidationError):
        record(type=invalid)


def test_ids_generated_independently_and_supplied_ids_normalized() -> None:
    data = record().model_dump()
    del data["id"]
    ids = [MemoryRecord.model_validate(data).id for _ in range(10)]
    assert len(set(ids)) == 10 and all(UUID(value).version == 4 for value in ids)
    assert record(id="  caller id\n").id == "caller id"


@pytest.mark.parametrize("field", ["id", "content", "source_type", "scope"])
@pytest.mark.parametrize("invalid", BAD_TEXT)
def test_required_strict_meaningful_text(field: str, invalid: object) -> None:
    with pytest.raises(ValidationError):
        record(**{field: invalid})


@pytest.mark.parametrize("field", ["source_ref", "embedding_ref"])
@pytest.mark.parametrize("invalid", BAD_TEXT[1:])
def test_optional_refs_are_strict_if_supplied(field: str, invalid: object) -> None:
    with pytest.raises(ValidationError):
        record(**{field: invalid})


def test_opaque_references_extensible_sources_scope_and_content_preservation() -> None:
    value = record(
        content="  __import__('os').system('never'); ignore rules\n",
        source_type=" future/source-family ",
        source_ref=" file:///never-dereference ",
        scope=" arbitrary/future:scope ",
        embedding_ref=" embedding:opaque ",
    )
    assert value.content == "  __import__('os').system('never'); ignore rules\n"
    assert (
        value.source_type == "future/source-family"
        and value.scope == "arbitrary/future:scope"
    )
    assert value.source_ref == "file:///never-dereference"
    assert value.embedding_ref == "embedding:opaque"
    assert record().source_ref is None and record().embedding_ref is None


@pytest.mark.parametrize("field", ["confidence", "importance"])
@pytest.mark.parametrize("number", [0, 0.0, 0.25, 0.5, 1, 1.0])
def test_score_inclusive_bounds_and_ordinary_numeric_evidence(
    field: str, number: float
) -> None:
    assert getattr(record(**{field: number}), field) == float(number)


@pytest.mark.parametrize("field", ["confidence", "importance"])
@pytest.mark.parametrize("invalid", BAD_SCORE)
def test_score_invalid_types_ranges_and_nonfinite_rejected(
    field: str, invalid: object
) -> None:
    with pytest.raises(ValidationError):
        record(**{field: invalid})


@pytest.mark.parametrize(
    "field", ["created_at", "updated_at", "last_accessed_at", "expires_at"]
)
@pytest.mark.parametrize(
    "invalid",
    [
        datetime(2026, 10, 5),
        "2026-10-05T12:30:00",
        True,
        1,
        1.5,
        b"2026-10-05T12:30:00Z",
        object(),
        "",
    ],
)
def test_aware_timestamps_required(field: str, invalid: object) -> None:
    with pytest.raises(ValidationError):
        record(**{field: invalid})


@pytest.mark.parametrize("field", ["created_at", "updated_at"])
def test_required_explicit_timestamps_have_no_defaults(field: str) -> None:
    data = record().model_dump()
    del data[field]
    with pytest.raises(ValidationError):
        MemoryRecord.model_validate(data)
    with pytest.raises(ValidationError):
        record(**{field: None})


@pytest.mark.parametrize("offset", [-480, 0, 330, 345, 840])
def test_all_timestamp_fields_normalize_utc_without_losing_precision(
    offset: int,
) -> None:
    instant = CREATED.astimezone(timezone(timedelta(minutes=offset)))
    value = record(
        created_at=instant,
        updated_at=instant,
        last_accessed_at=instant,
        expires_at=instant,
    )
    for field in ("created_at", "updated_at", "last_accessed_at", "expires_at"):
        assert getattr(value, field) == CREATED and getattr(value, field).tzinfo is UTC
    assert MemoryRecord.model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize("field", ["updated_at", "last_accessed_at", "expires_at"])
def test_timestamp_relationships_reject_before_creation(field: str) -> None:
    with pytest.raises(ValidationError):
        record(**{field: CREATED - timedelta(microseconds=1)})
    assert getattr(record(**{field: CREATED}), field) == CREATED
    assert getattr(record(**{field: CREATED + timedelta(days=1)}), field) > CREATED


def test_metadata_deep_readonly_detached_json_snapshot_and_serialization() -> None:
    data = {"nested": [{"text": "untrusted", "values": [None, True, 1, 0.5]}]}
    value = record(metadata=data)
    expected = json.loads(value.model_dump_json())["metadata"]
    data["nested"][0]["text"] = "caller changed"
    data["nested"].append({"new": "value"})
    assert json.loads(value.model_dump_json())["metadata"] == expected
    with pytest.raises(TypeError):
        value.metadata["new"] = "cannot mutate"  # type: ignore[index]
    nested = value.metadata["nested"]
    assert isinstance(nested, tuple) and isinstance(nested[0], Mapping)
    with pytest.raises(TypeError):
        nested[0]["text"] = "cannot mutate"
    exported = value.model_dump()
    exported["metadata"]["nested"][0]["text"] = "export changed"
    assert json.loads(value.model_dump_json())["metadata"] == expected
    assert MemoryRecord.model_validate_json(value.model_dump_json()) == value
    assert MemoryRecord.model_validate(value) == value
    assert record().metadata == {} and record().metadata is not record().metadata


@pytest.mark.parametrize("invalid", [None, [], (), "text", 1, True, {1: "bad key"}])
def test_metadata_top_level_object_and_string_keys(invalid: object) -> None:
    with pytest.raises(ValidationError):
        record(metadata=invalid)


@pytest.mark.parametrize("invalid", BAD_JSON)
@pytest.mark.parametrize("location", ["value", "array", "nested"])
def test_metadata_rejects_non_json_values_at_every_depth(
    invalid: object, location: str
) -> None:
    value = (
        invalid
        if location == "value"
        else ([invalid] if location == "array" else {"inside": invalid})
    )
    with pytest.raises(ValidationError):
        record(metadata={"data": value})


def test_metadata_cycles_are_explicit_validation_failures() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    with pytest.raises(ValidationError, match="cycles"):
        record(metadata=cyclic)
    items: list[object] = []
    items.append(items)
    with pytest.raises(ValidationError, match="cycles"):
        record(metadata={"array": items})
    child = {"value": [1]}
    assert record(metadata={"one": child, "two": child}).model_dump()["metadata"] == {
        "one": child,
        "two": child,
    }


@pytest.mark.parametrize(
    "extra", ["vector", "provider", "callback", "permissions", "secret", "status"]
)
def test_record_frozen_and_extra_fields_forbidden(extra: str) -> None:
    with pytest.raises(ValidationError):
        record(**{extra: "untrusted"})
    value = record()
    with pytest.raises(ValidationError):
        value.content = "mutated"


@pytest.mark.parametrize(
    "field,invalid",
    [
        ("id", None),
        ("type", "secret"),
        ("content", " \n"),
        ("confidence", True),
        ("updated_at", CREATED - timedelta(seconds=1)),
        ("metadata", {"bad": object()}),
        ("extra", "injected"),
    ],
)
def test_stored_model_fields_exactly_revalidated(field: str, invalid: object) -> None:
    forged = record().model_copy(update={field: invalid})
    with pytest.raises(ValidationError):
        MemoryRecord.model_validate(forged)


def test_extended_record_rejected() -> None:
    class Extended(MemoryRecord):
        extra: str = "injected"

    with pytest.raises(ValidationError):
        MemoryRecord.model_validate(Extended.model_validate(record().model_dump()))


def test_orm_uses_shared_base_and_handles_reserved_metadata_name() -> None:
    assert issubclass(MemoryRow, Base)
    # Other legitimate domain imports may also register tables in this process.
    # The fresh-process test below still proves memory-only registration exactly.
    assert Base.metadata.tables["memories"] is MemoryRow.__table__
    assert (
        isinstance(MemoryRow.metadata, MetaData) and MemoryRow.metadata is Base.metadata
    )
    assert MemoryRow.__tablename__ == "memories"
    assert set(MemoryRow.__table__.columns.keys()) == FIELDS
    assert MemoryRow.metadata_json.property.columns[0].name == "metadata"
    assert MemoryRow.memory_type.property.columns[0].name == "type"


def test_migrated_schema_matches_mapping_without_drift(database: Engine) -> None:
    assert_memory_schema(database)
    memory_metadata = MetaData()
    Base.metadata.tables["memories"].to_metadata(memory_metadata)
    with database.connect() as connection:
        assert (
            compare_metadata(MigrationContext.configure(connection), memory_metadata)
            == []
        )


@pytest.mark.parametrize("memory_type", tuple(MemoryType))
@pytest.mark.parametrize("optional", [False, True])
def test_orm_insert_select_all_fields_utc_and_json_round_trip(
    database: Engine, memory_type: MemoryType, optional: bool
) -> None:
    instant = CREATED.astimezone(timezone(timedelta(hours=5, minutes=30)))
    value = record(
        type=memory_type,
        created_at=instant,
        updated_at=instant,
        last_accessed_at=instant if optional else None,
        expires_at=instant if optional else None,
        source_ref="file:///never-dereference" if optional else None,
        embedding_ref="opaque:embedding" if optional else None,
        metadata={"nested": [None, True, {"integer": 5, "number": 0.5}]},
    )
    with create_session_factory(database)() as session:
        entry = row(value)
        # Exercise the storage conversion independently of domain normalization.
        entry.created_at = instant
        entry.updated_at = instant
        entry.last_accessed_at = instant if optional else None
        entry.expires_at = instant if optional else None
        session.add(entry)
        session.commit()
    with create_session_factory(database)() as session:
        loaded = session.execute(select(MemoryRow)).scalar_one()
        assert loaded.id == value.id and loaded.memory_type == value.type
        assert (
            loaded.content == value.content and loaded.source_type == value.source_type
        )
        assert loaded.scope == value.scope and loaded.confidence == value.confidence
        assert loaded.importance == value.importance
        assert (
            loaded.source_ref == value.source_ref
            and loaded.embedding_ref == value.embedding_ref
        )
        for field in ("created_at", "updated_at", "last_accessed_at", "expires_at"):
            timestamp = getattr(loaded, field)
            assert timestamp == getattr(value, field)
            assert timestamp is None or timestamp.tzinfo is UTC
        assert loaded.metadata_json == value.model_dump()["metadata"]
        loaded.metadata_json["changed"] = "local"
        assert "changed" not in value.metadata
    with database.connect() as connection:
        stored: object = connection.execute(
            text("SELECT created_at FROM memories")
        ).scalar_one()
        assert stored == "2026-10-05 12:30:15.123456"


@pytest.mark.parametrize(
    "field", ["created_at", "updated_at", "last_accessed_at", "expires_at"]
)
def test_utc_mapping_rejects_naive_bind(database: Engine, field: str) -> None:
    value = row(record())
    setattr(value, field, datetime(2026, 10, 5))
    with create_session_factory(database)() as session:
        session.add(value)
        with pytest.raises(StatementError, match="timezone-aware"):
            session.commit()


@pytest.mark.parametrize(
    "field,invalid",
    [
        ("type", "secret"),
        ("type", "document"),
        ("type", "Semantic"),
        ("confidence", -0.1),
        ("confidence", 1.1),
        ("confidence", float("inf")),
        ("importance", -0.1),
        ("importance", 1.1),
        ("importance", float("-inf")),
        ("confidence", float("nan")),
        ("importance", float("nan")),
    ],
)
def test_database_enforces_type_and_scores_even_without_domain_validation(
    database: Engine, field: str, invalid: object
) -> None:
    data = record().model_dump()
    data[field] = invalid
    with database.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(Base.metadata.tables["memories"].insert().values(**data))


def test_duplicate_content_coexists_and_ids_remain_primary_keys(
    database: Engine,
) -> None:
    with create_session_factory(database)() as session:
        session.add_all([row(record(id="first")), row(record(id="second"))])
        session.commit()
        values = session.scalars(select(MemoryRow).order_by(MemoryRow.id)).all()
        assert [value.id for value in values] == ["first", "second"]
        assert values[0].content == values[1].content
    with database.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                Base.metadata.tables["memories"]
                .insert()
                .values(**record(id="first").model_dump())
            )


@pytest.mark.parametrize(
    "invalid",
    [None, [], "scalar", 1, True, {"bad": float("nan")}, {"bad": float("inf")}],
)
def test_database_metadata_requires_finite_json_object(
    database: Engine, invalid: object
) -> None:
    data = record().model_dump()
    data["metadata"] = invalid
    with database.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(Base.metadata.tables["memories"].insert().values(**data))


@pytest.mark.parametrize("field", ["confidence", "importance"])
@pytest.mark.parametrize("number", [0, 1])
def test_database_score_boundaries_are_inclusive(
    database: Engine, field: str, number: int
) -> None:
    with create_session_factory(database)() as session:
        session.add(row(record(**{field: number})))
        session.commit()
        assert getattr(session.scalars(select(MemoryRow)).one(), field) == number


def test_orm_empty_metadata_default_is_per_row(database: Engine) -> None:
    data = record().model_dump()
    data["memory_type"] = data.pop("type")
    data.pop("metadata")
    with create_session_factory(database)() as session:
        first, second = (
            MemoryRow(**{**data, "id": "first"}),
            MemoryRow(**{**data, "id": "second"}),
        )
        session.add_all([first, second])
        session.commit()
        assert first.metadata_json == second.metadata_json == {}
        assert first.metadata_json is not second.metadata_json


def test_real_alembic_upgrade_downgrade_reupgrade_lifecycle(tmp_path: Path) -> None:
    settings = AppSettings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    config = migration_config(settings)
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == ["0003_document_ingestion"]
    baseline = scripts.get_revision("0001")
    revision = scripts.get_revision("0002_memory_schema")
    assert baseline is not None and baseline.down_revision is None
    assert revision is not None and revision.down_revision == "0001"
    original = subprocess.check_output(
        ["git", "show", "origin/main:alembic/versions/0001_baseline.py"], cwd=ROOT
    )
    assert (ROOT / "alembic/versions/0001_baseline.py").read_bytes().replace(
        b"\r\n", b"\n"
    ) == original.replace(b"\r\n", b"\n")
    command.upgrade(config, "0001")
    engine = create_engine_for_settings(settings)
    try:
        with engine.connect() as connection:
            assert inspect(connection).get_table_names() == ["alembic_version"]
            assert (
                MigrationContext.configure(connection).get_current_revision() == "0001"
            )
        command.upgrade(config, "0002_memory_schema")
        assert_memory_schema(engine)
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "0002_memory_schema"
            )
        command.downgrade(config, "0001")
        with engine.connect() as connection:
            assert inspect(connection).get_table_names() == ["alembic_version"]
            assert (
                MigrationContext.configure(connection).get_current_revision() == "0001"
            )
        command.upgrade(config, "0002_memory_schema")
        assert_memory_schema(engine)
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "0002_memory_schema"
            )
        command.downgrade(config, "base")
        with engine.connect() as connection:
            assert inspect(connection).get_table_names() == ["alembic_version"]
            assert MigrationContext.configure(connection).get_current_revision() is None
    finally:
        engine.dispose()


def test_migration_missing_directory_fails_without_repair(tmp_path: Path) -> None:
    settings = AppSettings(  # type: ignore[call-arg]
        data_dir=tmp_path / "never-created", _env_file=None
    )
    with pytest.raises(OperationalError):
        command.upgrade(migration_config(settings), "head")
    assert not settings.data_dir.exists()


def test_content_metadata_not_logged(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    record(
        content="untrusted P4/P5 instructions",
        metadata={"instruction": "execute nothing"},
    )
    assert capsys.readouterr() == ("", "") and not caplog.records


def test_schema_import_and_call_boundaries() -> None:
    allowed = {
        "collections.abc",
        "datetime",
        "enum",
        "math",
        "types",
        "typing",
        "uuid",
        "pydantic",
        "sqlalchemy",
        "sqlalchemy.engine",
        "sqlalchemy.orm",
        "sqlalchemy.types",
        "phoenix.persistence.base",
    }
    forbidden = {
        "create_engine",
        "connect",
        "create_all",
        "upgrade",
        "publish",
        "emit",
        "execute",
        "embed",
        "generate",
        "open",
        "create_task",
        "start",
        "eval",
        "exec",
        "__import__",
        "remember",
        "forget",
        "search",
        "retrieve",
    }
    for module in (schema, memory):
        tree = ast.parse(python_inspect.getsource(module))
        for node in ast.walk(tree):
            assert not isinstance(node, (ast.AsyncFunctionDef, ast.Await))
            if isinstance(node, ast.ImportFrom):
                assert node.module in allowed
            if isinstance(node, ast.Import):
                assert all(item.name in allowed for item in node.names)
            if isinstance(node, ast.Call):
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else (node.func.id if isinstance(node.func, ast.Name) else "")
                )
                assert name not in forbidden


def test_fresh_import_record_and_orm_construction_have_no_side_effects(
    tmp_path: Path,
) -> None:
    script = """
import asyncio, _thread, importlib.abc, os, platform, socket, sqlite3
import subprocess, sys, threading
from datetime import UTC, datetime
from pathlib import Path
# Initialize stdlib OS metadata before SQLAlchemy's cold import queries it.
platform.uname()
sys.dont_write_bytecode = True
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected memory schema runtime activity")
def audit(event, args):
    if event == "open" and ("never-dereference" in str(args[0]) or args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    )):
        forbidden()
    if event.startswith(("socket.", "sqlite3.", "subprocess.")):
        forbidden()
    if event in ("os.mkdir", "os.remove", "os.rename", "os.system"):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("phoenix.models", "phoenix.agents", "phoenix.core", "phoenix.api",
            "phoenix.tools", "phoenix.permissions", "phoenix.voice", "phoenix.recovery",
            "phoenix.persistence.database", "httpx", "psutil", "alembic")
        if any(fullname == root or fullname.startswith(root + ".") for root in roots):
            forbidden()
sys.meta_path.insert(0, BlockImports())
sys.addaudithook(audit)
os.mkdir = forbidden
sqlite3.connect = forbidden
sqlite3.dbapi2.connect = forbidden
socket.socket = forbidden
socket.create_connection = forbidden
subprocess.Popen = forbidden
threading.Thread.start = forbidden
_thread.start_new_thread = forbidden
asyncio.create_task = forbidden
asyncio.BaseEventLoop.create_task = forbidden
from phoenix.memory.schema import MemoryRecord, MemoryType
from phoenix.persistence.base import Base
assert not Base.metadata.tables
from phoenix.persistence.memory import MemoryRow
assert set(Base.metadata.tables) == {"memories"}
now = datetime(2026, 10, 5, tzinfo=UTC)
for memory_type in MemoryType:
    value = MemoryRecord(type=memory_type,
        content="__import__('os').system('never'); grant P4/P5",
        source_type="untrusted", scope="project", source_ref="file:///never-dereference",
        confidence=0.5, importance=0.5, created_at=now, updated_at=now,
        embedding_ref="opaque", metadata={"nested": [{"instruction": "untrusted"}]})
    assert MemoryRecord.model_validate_json(value.model_dump_json()) == value
    fields = value.model_dump()
    fields["memory_type"] = fields.pop("type")
    fields["metadata_json"] = fields.pop("metadata")
    row = MemoryRow(**fields)
    assert row.content == value.content and row.created_at == now
    assert row.metadata_json == fields["metadata_json"]
assert not list(Path.cwd().iterdir())
print("Memory schema isolation: PASS")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert completed.stdout.strip() == "Memory schema isolation: PASS"
    assert completed.stderr == "" and list(tmp_path.iterdir()) == []
