"""Admitted durable storage, privacy and post-commit observability on temp SQLite."""

import ast
import asyncio
import inspect as python_inspect
import subprocess
import sys
import traceback
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import Engine, event, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from phoenix.agents.registry import AgentDefinition, AgentRegistry
from phoenix.core.config import AppSettings
from phoenix.core.event_bus import EventBus
from phoenix.core.events import EventEnvelope, EventSeverity, EventType
from phoenix.memory import service as module
from phoenix.memory.schema import MemoryRecord, MemoryType
from phoenix.memory.service import (
    MemoryAdmissionDecision,
    MemoryAdmissionError,
    MemoryAdmissionVerdict,
    MemoryConflictError,
    MemoryCreateResult,
    MemoryDataError,
    MemoryEventDelivery,
    MemoryEventError,
    MemoryInputError,
    MemoryListQuery,
    MemoryNotFoundError,
    MemoryPersistenceError,
    MemoryPolicyRejectedError,
    MemorySecretRejectedError,
    PuddingMemoryService,
)
from phoenix.persistence.database import (
    create_engine_for_settings,
    create_session_factory,
    database_url,
)
from phoenix.persistence.memory import MemoryRow

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 5, 12, 30, 15, 123456, tzinfo=UTC)
BAD_IDS = (None, True, 1, 1.5, b"id", object(), "", " \t\n")


def record(**changes: object) -> MemoryRecord:
    return MemoryRecord.model_validate(
        dict(
            id="first",
            type="semantic",
            content="Untrusted descriptive evidence",
            source_type="operator",
            source_ref="opaque:source",
            scope="project",
            confidence=0.5,
            importance=0.5,
            created_at=NOW,
            updated_at=NOW,
            last_accessed_at=NOW,
            expires_at=NOW,
            embedding_ref="opaque:embedding",
            metadata={"nested": [{"description": "private-metadata-marker"}]},
        )
        | changes
    )


class Policy:
    def __init__(self, verdict: str = "accepted", result: object = None) -> None:
        self.verdict = verdict
        self.result = result
        self.calls: list[MemoryRecord] = []

    def evaluate(self, value: MemoryRecord) -> MemoryAdmissionDecision:
        self.calls.append(value)
        if isinstance(self.result, BaseException):
            raise self.result
        if self.result is not None:
            return cast(MemoryAdmissionDecision, self.result)
        return MemoryAdmissionDecision.model_validate(
            {"memory_id": value.id, "verdict": self.verdict}
        )


class SpyBus(EventBus):
    def __init__(self, capacity: int = 256) -> None:
        super().__init__(capacity)
        self.attempts: list[EventEnvelope] = []

    async def publish(self, value: EventEnvelope) -> None:
        self.attempts.append(value)
        await super().publish(value)


@pytest.fixture
def database(tmp_path: Path) -> Iterator[Engine]:
    settings = AppSettings(data_dir=tmp_path, _env_file=None)  # type: ignore[call-arg]
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", str(database_url(settings)).replace("%", "%%")
    )
    command.upgrade(config, "head")
    engine = create_engine_for_settings(settings)
    try:
        yield engine
    finally:
        engine.dispose()


def make(
    database: Engine,
    policy: Policy | None = None,
    bus: EventBus | None = None,
    registry: AgentRegistry | None = None,
) -> PuddingMemoryService:
    return PuddingMemoryService(
        create_session_factory(database), bus or SpyBus(), policy or Policy(), registry
    )


def create(service: PuddingMemoryService, value: MemoryRecord) -> MemoryCreateResult:
    return asyncio.run(service.create(value))


@pytest.mark.parametrize("result", [None, True, object()])
def test_non_contract_admission_never_opens_session(
    database: Engine, monkeypatch: pytest.MonkeyPatch, result: object
) -> None:
    class InvalidPolicy:
        def evaluate(self, value: MemoryRecord) -> MemoryAdmissionDecision:
            return cast(MemoryAdmissionDecision, result)

    bus = SpyBus()
    service = PuddingMemoryService(
        create_session_factory(database), bus, InvalidPolicy()
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Admission rejection must precede session acquisition")

    monkeypatch.setattr(sessionmaker, "__call__", forbidden)
    with pytest.raises(MemoryAdmissionError):
        create(service, record())
    assert bus.attempts == []


def test_admission_cancellation_never_opens_session(
    database: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy = Policy(result=asyncio.CancelledError())
    bus = SpyBus()
    service = make(database, policy=policy, bus=bus)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Admission cancellation must precede session acquisition")

    monkeypatch.setattr(sessionmaker, "__call__", forbidden)
    with pytest.raises(asyncio.CancelledError):
        create(service, record())
    assert len(policy.calls) == 1 and bus.attempts == []


def test_vocabularies_contracts_and_no_default_policy(database: Engine) -> None:
    assert [v.value for v in MemoryAdmissionVerdict] == [
        "accepted",
        "rejected_secret",
        "rejected_policy",
    ]
    assert [v.value for v in MemoryEventDelivery] == ["published", "rejected_full"]
    signature = python_inspect.signature(PuddingMemoryService)
    assert (
        signature.parameters["admission_policy"].default
        is python_inspect.Parameter.empty
    )
    for invalid in (None, object(), True):
        with pytest.raises(MemoryAdmissionError):
            PuddingMemoryService(create_session_factory(database), EventBus(), invalid)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        PuddingMemoryService(create_session_factory(database), EventBus())  # type: ignore[call-arg]
    assert make(database).agent_id == "memory"


@pytest.mark.parametrize("alias", [None, "Renamed", "PUDDING"])
def test_alias_independence(database: Engine, alias: str | None) -> None:
    registry = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id="memory", alias=alias, professional_role="Display only"
            ),
        )
    )
    bus = SpyBus()
    result = create(make(database, bus=bus, registry=registry), record())
    assert result.record == record() and bus.attempts[0].agent_id == "memory"


@pytest.mark.parametrize("identity", ["PUDDING", "other"])
def test_alias_only_or_missing_identity_fails(database: Engine, identity: str) -> None:
    registry = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id=identity,
                alias="memory",
                professional_role="Long-term memory",
            ),
        )
    )
    with pytest.raises(MemoryInputError):
        make(database, registry=registry)


@pytest.mark.parametrize(
    "contract,data",
    [
        (MemoryAdmissionDecision, {"memory_id": "first", "verdict": "accepted"}),
        (MemoryListQuery, {}),
        (MemoryCreateResult, {"record": record(), "event_delivery": "published"}),
    ],
)
def test_public_contract_frozen_extra_forbidden_and_exact_revalidated(
    contract: type[module._Contract], data: dict[str, object]
) -> None:
    value = contract.model_validate(data)
    with pytest.raises(ValidationError):
        setattr(value, next(iter(contract.model_fields)), None)
    with pytest.raises(ValidationError):
        contract.model_validate({**data, "extra": "private"})
    with pytest.raises(ValidationError):
        contract.model_validate(value.model_copy(update={"injected": "private"}))

    class Extended(contract):  # type: ignore[valid-type,misc]
        extra: str = "private"

    with pytest.raises(ValidationError):
        contract.model_validate(Extended.model_validate(data))


@pytest.mark.parametrize("verdict", [None, True, 1, b"accepted", "allow", "Accepted"])
def test_invalid_admission_vocabulary(verdict: object) -> None:
    with pytest.raises(ValidationError):
        MemoryAdmissionDecision.model_validate(
            {"memory_id": "first", "verdict": verdict}
        )


@pytest.mark.parametrize("operation", ["create", "replace"])
@pytest.mark.parametrize(
    "result,expected",
    [
        (
            MemoryAdmissionDecision(
                memory_id="first", verdict=MemoryAdmissionVerdict.REJECTED_SECRET
            ),
            MemorySecretRejectedError,
        ),
        (
            MemoryAdmissionDecision(
                memory_id="first", verdict=MemoryAdmissionVerdict.REJECTED_POLICY
            ),
            MemoryPolicyRejectedError,
        ),
        (
            MemoryAdmissionDecision(
                memory_id="other", verdict=MemoryAdmissionVerdict.ACCEPTED
            ),
            MemoryAdmissionError,
        ),
        ({"memory_id": "first", "verdict": "accepted"}, MemoryAdmissionError),
        (
            MemoryAdmissionDecision(
                memory_id="first", verdict=MemoryAdmissionVerdict.ACCEPTED
            ).model_copy(update={"verdict": "fake"}),
            MemoryAdmissionError,
        ),
        (
            MemoryAdmissionDecision(
                memory_id="first", verdict=MemoryAdmissionVerdict.ACCEPTED
            ).model_copy(update={"private": "injected"}),
            MemoryAdmissionError,
        ),
        (RuntimeError("private-content-marker"), MemoryAdmissionError),
    ],
)
def test_rejected_or_malformed_policy_never_opens_session(
    database: Engine,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    result: object,
    expected: type[Exception],
) -> None:
    bus, policy = SpyBus(), Policy(result=result)
    service = make(database, policy, bus)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Admission must precede session access")

    monkeypatch.setattr(sessionmaker, "__call__", forbidden)
    with pytest.raises(expected) as error:
        create(service, record()) if operation == "create" else service.replace(
            record()
        )
    assert "private-content-marker" not in str(error.value)
    assert len(policy.calls) == 1 and bus.attempts == []


@pytest.mark.parametrize(
    "field,bad",
    [
        ("id", None),
        ("id", b"bad"),
        ("content", " "),
        ("metadata", {"bad": object()}),
        ("confidence", True),
        ("importance", float("nan")),
        ("created_at", datetime(2026, 10, 5)),
        ("updated_at", NOW - timedelta(seconds=1)),
        ("injected", "private-content-marker"),
    ],
)
@pytest.mark.parametrize("operation", ["create", "replace"])
def test_forged_record_rejected_before_policy(
    database: Engine, field: str, bad: object, operation: str
) -> None:
    bus, policy = SpyBus(), Policy()
    service = make(database, policy, bus)
    with pytest.raises(MemoryInputError):
        forged = record().model_copy(update={field: bad})
        create(service, forged) if operation == "create" else service.replace(forged)
    assert policy.calls == [] and bus.attempts == [] and service.list_records() == ()


def test_subclass_and_mapping_are_not_record_inputs(database: Engine) -> None:
    class Extended(MemoryRecord):
        extra: str = "private"

    for bad in (Extended.model_validate(record().model_dump()), record().model_dump()):
        with pytest.raises(MemoryInputError):
            create(make(database), cast(MemoryRecord, bad))


@pytest.mark.parametrize("memory_type", list(MemoryType))
def test_create_all_fields_detached_event_and_get(
    database: Engine, memory_type: MemoryType
) -> None:
    policy, bus = Policy(), SpyBus()
    service = make(database, policy, bus)
    original = record(type=memory_type)
    result = create(service, original)
    assert len(policy.calls) == 1 and result.record == original
    assert result.record is not original and result.record is not policy.calls[0]
    assert result.event_delivery is MemoryEventDelivery.PUBLISHED
    assert len(bus.attempts) == 1
    envelope = bus.attempts[0]
    assert envelope.event_type is EventType.MEMORY_CREATED
    assert envelope.agent_id == "memory" and envelope.correlation_id == original.id
    assert envelope.task_id is None and envelope.severity is EventSeverity.INFO
    assert dict(envelope.payload) == {
        "memory_id": original.id,
        "memory_type": memory_type.value,
    }
    assert original.content not in envelope.model_dump_json()
    assert "private-metadata-marker" not in envelope.model_dump_json()
    loaded = service.get(" first ")
    assert type(loaded) is MemoryRecord and loaded == original
    assert loaded.created_at.tzinfo is UTC
    assert service.get("missing") is None
    assert service.list_records() == (original,)  # Expired at NOW, still inspectable.
    with pytest.raises(TypeError):
        loaded.metadata["mutate"] = "bad"  # type: ignore[index]
    exported = result.record.model_dump()
    exported["metadata"]["nested"].append("changed")
    assert service.get(original.id) == original


def test_duplicate_id_preserved_and_content_not_deduplicated(database: Engine) -> None:
    bus, policy = SpyBus(), Policy()
    service = make(database, policy, bus)
    first = record()
    create(service, first)
    with pytest.raises(MemoryConflictError):
        create(service, record(content="Contradictory replacement"))
    assert service.get(first.id) == first and len(bus.attempts) == 1
    create(service, record(id="second"))
    create(service, record(id="third", content="Contradictory evidence"))
    assert len(service.list_records()) == 3 and len(policy.calls) == 4


def test_event_observes_commit_and_closed_session(database: Engine) -> None:
    state: list[str] = []

    class TrackingSession(Session):
        def close(self) -> None:
            state.append("closed")
            super().close()

    class CommitBus(SpyBus):
        async def publish(self, value: EventEnvelope) -> None:
            assert state == ["committed", "closed"]
            with Session(database) as independent:
                assert independent.get(MemoryRow, "first") is not None
            await super().publish(value)

    event.listen(
        TrackingSession, "after_commit", lambda session: state.append("committed")
    )
    service = PuddingMemoryService(
        sessionmaker(database, class_=TrackingSession), CommitBus(), Policy()
    )
    create(service, record())


def test_real_backpressure_no_partial_delivery_and_no_retry(database: Engine) -> None:
    async def scenario() -> None:
        bus = SpyBus(capacity=1)
        full = await bus.subscribe()
        await bus.publish(EventEnvelope(event_type=EventType.TASK_CREATED))
        empty = await bus.subscribe()
        service = make(database, bus=bus)
        result = await service.create(record())
        assert result.event_delivery is MemoryEventDelivery.REJECTED_FULL
        assert len(bus.attempts) == 2 and service.get("first") == record()
        assert empty._queue.empty()
        assert (await full.get()).event_type is EventType.TASK_CREATED
        await full.close()
        normal = await service.create(record(id="second"))
        assert normal.event_delivery is MemoryEventDelivery.PUBLISHED
        assert (await empty.get()).correlation_id == "second"
        await empty.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure", [asyncio.CancelledError(), RuntimeError("private-content-marker")]
)
def test_publication_failure_preserves_durable_row(
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
        else MemoryEventError
    )
    with pytest.raises(expected):
        create(service, record())
    assert service.get("first") == record() and len(bus.attempts) == 1


@pytest.mark.parametrize("operation", ["create", "replace", "delete"])
def test_commit_failure_rolls_back_closes_and_redacts(
    database: Engine, operation: str
) -> None:
    good = make(database)
    create(good, record())
    state: list[str] = []

    class FailingSession(Session):
        def commit(self) -> None:
            self.flush()
            raise OperationalError(
                "private SQL marker",
                {"content": "private-content-marker"},
                Exception("private-db-path"),
            )

        def close(self) -> None:
            state.append("closed")
            super().close()

    bus = SpyBus()
    service = PuddingMemoryService(
        sessionmaker(database, class_=FailingSession), bus, Policy()
    )
    with pytest.raises(MemoryPersistenceError) as error:
        if operation == "create":
            create(service, record(id="second"))
        elif operation == "replace":
            service.replace(record(content="new evidence"))
        else:
            service.delete("first")
    rendered = "".join(traceback.format_exception(error.value))
    assert all(
        marker not in rendered
        for marker in (
            "private SQL marker",
            "private-content-marker",
            "private-db-path",
        )
    )
    assert state == ["closed"] and bus.attempts == []
    assert good.list_records() == (record(),)


@pytest.mark.parametrize(
    "operation", ["create", "get", "list_records", "replace", "delete"]
)
def test_missing_database_location_is_safe_no_repair(
    tmp_path: Path, operation: str
) -> None:
    settings = AppSettings(data_dir=tmp_path / "never-created", _env_file=None)  # type: ignore[call-arg]
    engine = create_engine_for_settings(settings)
    try:
        service = make(engine)
        with pytest.raises(MemoryPersistenceError) as error:
            if operation == "create":
                create(service, record())
            elif operation == "replace":
                service.replace(record())
            elif operation == "get":
                service.get("first")
            elif operation == "delete":
                service.delete("first")
            else:
                service.list_records()
        assert str(settings.data_dir) not in str(error.value)
        assert not settings.data_dir.exists()
    finally:
        engine.dispose()


@pytest.mark.parametrize("memory_id", BAD_IDS)
@pytest.mark.parametrize("operation", ["get", "delete"])
def test_strict_ids(database: Engine, memory_id: object, operation: str) -> None:
    with pytest.raises(MemoryInputError):
        getattr(make(database), operation)(memory_id)


@pytest.mark.parametrize(
    "field,bad",
    [
        *(("limit", value) for value in (None, True, 0, -1, 501, "1", 1.5)),
        *(("offset", value) for value in (None, False, -1, "0", 1.5)),
        *(("scope", value) for value in BAD_IDS),
        *(("source_type", value) for value in BAD_IDS),
        *(("type", value) for value in (True, 1, b"user", "unknown")),
    ],
)
def test_list_query_invalid_fields(field: str, bad: object) -> None:
    if bad is None and field in ("scope", "source_type"):
        assert getattr(MemoryListQuery.model_validate({field: bad}), field) is None
    else:
        with pytest.raises(ValidationError):
            MemoryListQuery.model_validate({field: bad})


def test_query_forgery_and_unknown_filters_fail(database: Engine) -> None:
    service = make(database)
    bad: object
    for bad in (
        MemoryListQuery().model_copy(update={"limit": 999}),
        MemoryListQuery().model_copy(update={"content": "search"}),
        {},
    ):
        with pytest.raises(MemoryInputError):
            service.list_records(cast(MemoryListQuery, bad))
    assert set(MemoryListQuery.model_fields) == {
        "type",
        "scope",
        "source_type",
        "limit",
        "offset",
    }


def test_list_exact_filters_combination_order_and_pagination(database: Engine) -> None:
    service = make(database)
    values = [
        record(id="z", type="user"),
        record(id="a", type="user", scope="other"),
        record(id="b", type="project", source_type="model"),
        record(id="early", created_at=NOW - timedelta(days=1)),
    ]
    for value in values:
        create(service, value)
    assert [r.id for r in service.list_records()] == ["early", "a", "b", "z"]
    for query, expected in [
        (MemoryListQuery(type=MemoryType.USER), ["a", "z"]),
        (MemoryListQuery(scope=" project "), ["early", "b", "z"]),
        (MemoryListQuery(source_type="operator"), ["early", "a", "z"]),
        (
            MemoryListQuery(
                type=MemoryType.USER, scope="project", source_type="operator"
            ),
            ["z"],
        ),
        (MemoryListQuery(scope="pro%"), []),
        (MemoryListQuery(limit=2, offset=1), ["a", "b"]),
        (MemoryListQuery(offset=100), []),
    ]:
        assert [r.id for r in service.list_records(query)] == expected
    assert isinstance(service.list_records(), tuple)
    assert all(service.get(value.id) == value for value in values)


def test_list_default_and_max_are_bounded(database: Engine) -> None:
    service = make(database)
    with Session(database) as session:
        session.add_all(
            MemoryRow(**module._row_values(record(id=f"id-{i:03}"))) for i in range(503)
        )
        session.commit()
    assert len(service.list_records()) == 100
    assert len(service.list_records(MemoryListQuery(limit=500))) == 500
    assert len(service.list_records(MemoryListQuery(limit=500, offset=500))) == 3


@pytest.mark.parametrize("delta", [0, 1])
def test_replace_exact_id_equal_or_later_timestamp(
    database: Engine, delta: int
) -> None:
    policy, bus = Policy(), SpyBus()
    service = make(database, policy, bus)
    create(service, record())
    create(service, record(id="second"))
    replacement = record(
        content="Explicit contradictory evidence",
        updated_at=NOW + timedelta(seconds=delta),
        metadata={"replacement": [1, 2]},
        type="user",
        source_ref=None,
        embedding_ref=None,
    )
    result = service.replace(replacement)
    assert result == replacement and result is not replacement
    assert service.get("first") == replacement and service.get("second") == record(
        id="second"
    )
    assert len(policy.calls) == 3 and len(bus.attempts) == 2
    assert service.delete(" first ") is True and service.delete("first") is False
    assert service.get("first") is None and service.get("second") is not None
    assert len(bus.attempts) == 2


@pytest.mark.parametrize("case", ["missing", "created", "earlier"])
def test_replace_conflicts_preserve_original(database: Engine, case: str) -> None:
    bus = SpyBus()
    service = make(database, bus=bus)
    original = record(updated_at=NOW + timedelta(seconds=2))
    create(service, original)
    expected: type[Exception]
    if case == "missing":
        replacement, expected = record(id="other"), MemoryNotFoundError
    elif case == "created":
        replacement, expected = (
            record(created_at=NOW - timedelta(seconds=1)),
            MemoryConflictError,
        )
    else:
        replacement, expected = record(), MemoryConflictError
    with pytest.raises(expected):
        service.replace(replacement)
    assert service.list_records() == (original,) and len(bus.attempts) == 1


@pytest.mark.parametrize(
    "field,bad",
    [
        ("content", " "),
        ("source_type", ""),
        ("scope", " "),
        ("updated_at", "2000-01-01 00:00:00.000000"),
        ("created_at", "private-invalid-datetime"),
    ],
)
@pytest.mark.parametrize("operation", ["get", "list", "replace"])
def test_stored_corruption_fails_closed(
    database: Engine, field: str, bad: str, operation: str
) -> None:
    service = make(database)
    create(service, record())
    with database.begin() as connection:
        connection.execute(
            text(f"UPDATE memories SET {field} = :bad WHERE id = 'first'"), {"bad": bad}
        )
    with pytest.raises(MemoryDataError) as error:
        if operation == "get":
            service.get("first")
        elif operation == "list":
            service.list_records()
        else:
            service.replace(record())
    assert "private-invalid-datetime" not in str(error.value)


def test_schema_unchanged(database: Engine) -> None:
    scripts = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert scripts.get_heads() == ["0002_memory_schema"]
    assert inspect(database).get_table_names() == ["alembic_version", "memories"]
    with database.connect() as connection:
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0002_memory_schema"
        )
    for path in (
        "alembic/versions/0001_baseline.py",
        "alembic/versions/0002_memory_schema.py",
        "phoenix/memory/schema.py",
        "phoenix/persistence/memory.py",
    ):
        previous = subprocess.check_output(
            ["git", "show", f"origin/main:{path}"], cwd=ROOT
        )
        assert (ROOT / path).read_bytes().replace(b"\r\n", b"\n") == previous.replace(
            b"\r\n", b"\n"
        )
    assert len(EventType) == 32


def test_production_scope_has_no_execution_or_retrieval() -> None:
    tree = ast.parse(python_inspect.getsource(module))
    allowed = {
        "collections.abc",
        "contextlib",
        "enum",
        "typing",
        "pydantic",
        "sqlalchemy",
        "sqlalchemy.exc",
        "sqlalchemy.orm",
        "phoenix.agents.registry",
        "phoenix.core.event_bus",
        "phoenix.core.events",
        "phoenix.memory.schema",
        "phoenix.persistence.memory",
    }
    for node in ast.walk(tree):
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
                "like",
                "merge",
            }


def test_policy_mutation_does_not_mutate_persistence_evidence(database: Engine) -> None:
    class MutatingPolicy(Policy):
        def evaluate(self, value: MemoryRecord) -> MemoryAdmissionDecision:
            decision = super().evaluate(value)
            object.__setattr__(value, "content", "Changed by collaborator")
            return decision

    service = make(database, MutatingPolicy())
    create(service, record())
    assert service.get("first") == record()


def test_extended_and_forged_decision_id_rejected(database: Engine) -> None:
    class Extended(MemoryAdmissionDecision):
        extra: str = "display"

    for supplied in (
        Extended(memory_id="first", verdict=MemoryAdmissionVerdict.ACCEPTED),
        MemoryAdmissionDecision(
            memory_id="first", verdict=MemoryAdmissionVerdict.ACCEPTED
        ).model_copy(update={"memory_id": True}),
    ):
        service = make(database, Policy(result=supplied))
        with pytest.raises(MemoryAdmissionError):
            create(service, record())
        assert service.list_records() == ()


def test_rejected_replace_retains_existing_row(database: Engine) -> None:
    good = make(database)
    create(good, record())
    for verdict in ("rejected_secret", "rejected_policy"):
        bus = SpyBus()
        service = make(database, Policy(verdict), bus)
        with pytest.raises(MemoryAdmissionError):
            service.replace(record(content="Rejected replacement"))
        assert good.get("first") == record() and bus.attempts == []


@pytest.mark.parametrize("field", ["memory_id", "verdict"])
@pytest.mark.parametrize("bad", BAD_IDS)
def test_decision_strict_fields(field: str, bad: object) -> None:
    with pytest.raises(ValidationError):
        MemoryAdmissionDecision.model_validate(
            {"memory_id": "first", "verdict": "accepted", field: bad}
        )


def test_pk_race_is_conflict_without_overwrite(database: Engine) -> None:
    # Simulate a stale pre-insert lookup using real SQLite PK enforcement.
    good = make(database)
    create(good, record())

    class StaleLookup(Session):
        def get(self, *args: object, **kwargs: object) -> None:
            return None

    bus = SpyBus()
    service = PuddingMemoryService(
        sessionmaker(database, class_=StaleLookup), bus, Policy()
    )
    with pytest.raises(MemoryConflictError):
        create(service, record(content="Must not overwrite"))
    assert good.get("first") == record() and bus.attempts == []


def test_json_decoder_corruption_safe(database: Engine) -> None:
    service = make(database)
    create(service, record())
    with database.begin() as connection:
        # Simulate external corruption; the production service never disables checks.
        connection.exec_driver_sql("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            text("UPDATE memories SET metadata = :value"),
            {"value": "private-invalid-json"},
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints = OFF")
    with pytest.raises(MemoryDataError) as error:
        service.get("first")
    assert "private-invalid-json" not in str(error.value)


def test_real_bus_cancellation_after_commit(database: Engine) -> None:
    async def scenario() -> None:
        bus = EventBus()
        service = make(database, bus=bus)
        await bus._lock.acquire()
        task = asyncio.create_task(service.create(record()))
        try:
            await asyncio.sleep(0)
            assert service.get("first") == record()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert service.get("first") == record()
        finally:
            bus._lock.release()

    asyncio.run(scenario())


def test_fresh_import_and_construction_are_inert(tmp_path: Path) -> None:
    script = r"""
import asyncio, _thread, importlib.abc, os, platform, socket, sqlite3
import subprocess, sys, threading
from datetime import UTC, datetime
from pathlib import Path
platform.uname()  # PHX-005 Windows stdlib metadata cache before cold SQLAlchemy import.
sys.dont_write_bytecode = True
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected memory service construction activity")
def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()
    if event.startswith(("socket.", "sqlite3.", "subprocess.")):
        forbidden()
    if event in ("os.mkdir", "os.remove", "os.rename", "os.system"):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("phoenix.models", "phoenix.api", "phoenix.tools",
            "phoenix.permissions", "phoenix.voice", "phoenix.recovery",
            "phoenix.persistence.database", "phoenix.core.config", "httpx",
            "psutil", "alembic", "phoenix.agents.orchestrator",
            "phoenix.agents.planner", "phoenix.agents.router",
            "phoenix.agents.verifier",
            "phoenix.agents.recovery", "phoenix.agents.failsafe")
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
from phoenix.memory.service import (
    MemoryAdmissionDecision, MemoryAdmissionVerdict, MemoryListQuery,
    MemoryCreateResult, MemoryEventDelivery, PuddingMemoryService
)
from phoenix.memory.schema import MemoryRecord
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
engine = create_engine("sqlite:///never-created.db")
bus = EventBus()
service = PuddingMemoryService(sessionmaker(bind=engine), bus, Policy())
assert service.agent_id == "memory" and bus.subscriber_count == 0
now = datetime(2026, 10, 5, tzinfo=UTC)
record = MemoryRecord(id="first", type="semantic", content="Untrusted evidence",
    source_type="operator", scope="project", confidence=0.5, importance=0.5,
    created_at=now, updated_at=now, metadata={"description":["opaque"]})
decision = MemoryAdmissionDecision(
    memory_id=record.id, verdict=MemoryAdmissionVerdict.ACCEPTED)
assert decision.memory_id == record.id and MemoryListQuery().limit == 100
result = MemoryCreateResult(
    record=record, event_delivery=MemoryEventDelivery.REJECTED_FULL)
assert result.record == record
assert not list(Path.cwd().iterdir())
print("PUDDING service isolation: PASS")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert completed.stdout.strip() == "PUDDING service isolation: PASS"
    assert completed.stderr == "" and list(tmp_path.iterdir()) == []
