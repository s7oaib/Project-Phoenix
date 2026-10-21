"""Audit evidence contracts and real, temporary SQLite append-only history."""

import ast
import inspect as python_inspect
import os
import subprocess
import sys
import traceback
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from pydantic import BaseModel, ValidationError
from sqlalchemy import (
    CheckConstraint,
    Engine,
    Table,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from phoenix.core.events import EventType
from phoenix.persistence.audit import AuditEventRow
from phoenix.persistence.base import Base
from phoenix.persistence.documents import DocumentChunkRow, DocumentRow
from phoenix.persistence.embeddings import EmbeddingRecordRow
from phoenix.persistence.memory import MemoryRow
from phoenix.security import audit as module
from phoenix.security.audit import (
    AuditConflictError,
    AuditDataError,
    AuditEventKind,
    AuditInputError,
    AuditListQuery,
    AuditPersistenceError,
    AuditRecord,
    AuditTrail,
    audit_required_for_execution,
)
from phoenix.security.permissions import (
    DecisionReason,
    PermissionDecision,
    PermissionEngine,
    PermissionGrant,
    PermissionPolicy,
    PermissionPolicyRule,
    PermissionRequest,
    PermissionStatus,
    UserPermissionDecision,
)
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResultStatus,
    ToolRiskLevel,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 6, 12, 30, 15, 123456, tzinfo=UTC)
PRIVATE = "  Private rationale \u03c0; content-must-not-be-logged\n  "
BAD_TEXT = (None, True, 1, 1.5, b"text", "", " \t\n")
FIELDS = (
    "audit_id",
    "event_kind",
    "occurred_at",
    "request_id",
    "actor_id",
    "tool_id",
    "action",
    "resource",
    "request_reason_hash",
    "risk_level",
    "permission_requirement",
    "permission_status",
    "permission_reason_code",
    "matched_grant_id",
    "matched_policy_rule_id",
    "user_decision_id",
    "task_id",
    "session_id",
    "correlation_id",
    "tool_result_status",
)
ID_FIELDS = (
    "audit_id",
    "request_id",
    "actor_id",
    "tool_id",
    "action",
    "resource",
    "permission_requirement",
)
OPTIONAL_IDS = (
    "matched_grant_id",
    "matched_policy_rule_id",
    "user_decision_id",
    "task_id",
    "session_id",
    "correlation_id",
)
INDEXES = {
    f"ix_audit_events_{name}": [name]
    for name in (
        "request_id",
        "tool_id",
        "occurred_at",
        "risk_level",
    )
}
CHECKS = {
    "ck_audit_events_kind",
    "ck_audit_events_risk",
    "ck_audit_events_permission_status",
    "ck_audit_events_permission_reason",
    "ck_audit_events_reason_hash",
    "ck_audit_events_result_status",
    "ck_audit_events_decision_result",
    "ck_audit_events_outcome_result",
    "ck_audit_events_outcome_allowed",
    "ck_audit_events_outcome_risk",
    "ck_audit_events_outcome_not_denied",
}
TRIGGERS = {"trg_audit_events_no_update", "trg_audit_events_no_delete"}


class Foreign(StrEnum):
    KIND = "permission_decision"
    P0 = "P0"
    P3 = "P3"
    ALLOWED = "allowed"
    REASON = "p0_internal"
    SUCCESS = "success"


def request(risk: str = "P0", **changes: object) -> PermissionRequest:
    return PermissionRequest.model_validate(
        dict(
            request_id="Request.A",
            actor_id="Actor.A",
            tool_id=f"Tool.{risk}",
            action="Act.A",
            resource="opaque:Resource/A",
            reason=PRIVATE,
            requested_at=NOW,
            task_id="Task.A",
            session_id="Session.A",
        )
        | changes
    )


def definition(risk: str = "P0", **changes: object) -> ToolDefinition:
    return ToolDefinition.model_validate(
        dict(
            tool_id=f"Tool.{risk}",
            description="import evil.plugin; trusted?",
            input_schema={"$ref": "https://invalid.example/schema"},
            output_schema={},
            risk_level=risk,
            permission_requirement="approved",
            timeout_seconds=5,
            cancellation_supported=True,
            audit_behavior="disabled",
            supported_platforms=["Windows"],
            supported_environments=["desktop"],
        )
        | changes
    )


def registry() -> ToolRegistry:
    return ToolRegistry([definition(risk.value) for risk in ToolRiskLevel])


def decision(req: PermissionRequest, approval: str | None = None) -> PermissionDecision:
    user = (
        None
        if approval is None
        else UserPermissionDecision.model_validate(
            dict(
                decision_id="User.A",
                request_id=req.request_id,
                decision=approval,
                decided_at=NOW,
            )
        )
    )
    return PermissionEngine(registry()).evaluate(req, user_decision=user)


def record(**changes: object) -> AuditRecord:
    return AuditRecord.model_validate(
        dict(
            audit_id="Audit.A",
            event_kind="permission_decision",
            occurred_at=NOW,
            request_id="Request.A",
            actor_id="Actor.A",
            tool_id="Tool.P0",
            action="Act.A",
            resource="opaque:Resource/A",
            request_reason_hash=sha256(PRIVATE.encode("utf-8")).hexdigest(),
            risk_level="P0",
            permission_requirement="approved",
            permission_status="allowed",
            permission_reason_code="p0_internal",
            task_id="Task.A",
            session_id="Session.A",
        )
        | changes
    )


def lazy_trail() -> AuditTrail:
    return AuditTrail(sessionmaker(bind=create_engine("sqlite://")), registry())


def config(path: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}".replace("%", "%%"))
    return cfg


@pytest.fixture
def database(
    tmp_path: Path,
) -> Iterator[tuple[Engine, sessionmaker[Session], AuditTrail]]:
    path = tmp_path / "audit.db"
    command.upgrade(config(path), "head")
    engine = create_engine(f"sqlite:///{path}")
    factory = sessionmaker(bind=engine)
    try:
        yield engine, factory, AuditTrail(factory, registry())
    finally:
        engine.dispose()


def test_exact_contracts_and_vocabularies() -> None:
    assert tuple(AuditRecord.model_fields) == FIELDS
    assert tuple(AuditListQuery.model_fields) == (
        "event_kind",
        "request_id",
        "actor_id",
        "tool_id",
        "risk_level",
        "task_id",
        "session_id",
        "limit",
        "offset",
    )
    assert [x.value for x in AuditEventKind] == ["permission_decision", "tool_outcome"]
    assert record().risk_level is ToolRiskLevel.P0
    assert record().permission_status is PermissionStatus.ALLOWED
    assert record().permission_reason_code is DecisionReason.P0_INTERNAL
    assert (
        record(
            event_kind="tool_outcome", tool_result_status="success"
        ).tool_result_status
        is ToolResultStatus.SUCCESS
    )
    assert "reason" not in AuditRecord.model_json_schema()["properties"]
    assert PRIVATE not in record().model_dump_json()
    for contract in (record(), AuditListQuery()):
        with pytest.raises(ValidationError):
            setattr(contract, next(iter(type(contract).model_fields)), "changed")
        with pytest.raises(ValidationError):
            type(contract).model_validate(contract.model_dump() | {"extra": True})
        with pytest.raises(ValidationError):
            type(contract).model_validate(contract.model_copy(update={"extra": True}))
        injected = contract.model_copy()
        object.__setattr__(injected, "__pydantic_extra__", {"extra": True})
        with pytest.raises(ValidationError):
            type(contract).model_validate(injected)


@pytest.mark.parametrize("field", ID_FIELDS)
@pytest.mark.parametrize("value", BAD_TEXT)
def test_required_identifier_strictness(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        record(**{field: value})


@pytest.mark.parametrize("field", OPTIONAL_IDS)
@pytest.mark.parametrize("value", BAD_TEXT[1:])
def test_optional_identifier_strictness(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        record(**{field: value})


def test_identity_defaults_case_and_timestamp() -> None:
    data = record().model_dump()
    del data["audit_id"]
    first, second = AuditRecord.model_validate(data), AuditRecord.model_validate(data)
    assert UUID(first.audit_id).version == 4
    assert first.audit_id != second.audit_id
    assert record(audit_id="  Audit.Case  ").audit_id == "Audit.Case"
    shifted = NOW.astimezone(timezone(timedelta(hours=5, minutes=30)))
    assert record(occurred_at=shifted).occurred_at == NOW
    assert record(occurred_at=shifted.isoformat()).occurred_at.tzinfo is UTC
    del data["occurred_at"]
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(data)


@pytest.mark.parametrize(
    "value",
    (NOW.replace(tzinfo=None), "2026-10-06", 0, True, None, b"2026-10-06T12:00:00Z"),
)
def test_invalid_timestamp(value: object) -> None:
    with pytest.raises(ValidationError):
        record(occurred_at=value)


@pytest.mark.parametrize(
    "value",
    ("a" * 63, "a" * 65, "A" * 64, "g" * 64, " " + "a" * 64, b"a" * 64, 1, None),
)
def test_exact_hash(value: object) -> None:
    with pytest.raises(ValidationError):
        record(request_reason_hash=value)


@pytest.mark.parametrize(
    "field,vocabulary",
    (
        ("event_kind", AuditEventKind),
        ("risk_level", ToolRiskLevel),
        ("permission_status", PermissionStatus),
        ("permission_reason_code", DecisionReason),
        ("tool_result_status", ToolResultStatus),
    ),
)
@pytest.mark.parametrize("variant", ("foreign", "forged", "unknown", "space", "bytes"))
def test_enum_boundary(field: str, vocabulary: type[StrEnum], variant: str) -> None:
    member = next(iter(vocabulary))
    if variant == "foreign":
        value: object = Foreign(member.value)
    elif variant == "forged":
        value = str.__new__(vocabulary, member.value)
        object.__setattr__(value, "_name_", member.name)
        object.__setattr__(value, "_value_", member.value)
    elif variant == "unknown":
        value = "unknown"
    elif variant == "space":
        value = member.value + " "
    else:
        value = member.value.encode()
    with pytest.raises(ValidationError):
        record(**{field: value})


@pytest.mark.parametrize(
    "changes",
    (
        {"tool_result_status": "success"},
        {"event_kind": "tool_outcome"},
        {"event_kind": "tool_outcome", "tool_result_status": "denied"},
        {"permission_status": "blocked"},
        {"permission_reason_code": "matching_grant"},
        {"matched_policy_rule_id": "Rule"},
        {"matched_grant_id": "Grant"},
        {"user_decision_id": "User"},
        {"risk_level": "P4"},
    ),
)
def test_structural_contradictions(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        record(**changes)


@pytest.mark.parametrize("risk", tuple(ToolRiskLevel))
def test_required_helper(risk: ToolRiskLevel) -> None:
    assert audit_required_for_execution(risk) is (
        risk in (ToolRiskLevel.P3, ToolRiskLevel.P4)
    )


@pytest.mark.parametrize("value", (None, "P3", 3, True, Foreign.P3))
def test_required_helper_rejects_foreign(value: object) -> None:
    with pytest.raises(AuditInputError):
        audit_required_for_execution(cast(ToolRiskLevel, value))


@pytest.mark.parametrize(
    "field,value",
    (
        ("request_id", "Other"),
        ("actor_id", "Other"),
        ("tool_id", "Tool.P1"),
        ("risk_level", ToolRiskLevel.P1),
        ("permission_requirement", "Other"),
        ("status", PermissionStatus.DENIED),
        ("unexpected", True),
    ),
)
def test_decision_correlation_before_db(
    field: str, value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid evidence opened a session")

    monkeypatch.setattr(sessionmaker, "__call__", forbidden)
    req = request()
    dec = decision(req).model_copy(update={field: value})
    with pytest.raises(AuditInputError):
        lazy_trail().record_permission_decision(req, dec, occurred_at=NOW)


@pytest.mark.parametrize(
    "field,value",
    (
        ("request_id", None),
        ("actor_id", True),
        ("tool_id", "Unknown"),
        ("action", b"action"),
        ("resource", ""),
        ("reason", 1),
        ("requested_at", NOW.replace(tzinfo=None)),
        ("task_id", False),
        ("session_id", 1),
        ("risk_level", "P0"),
    ),
)
def test_request_revalidation_before_db(
    field: str, value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    req = request()
    dec = decision(req)
    monkeypatch.setattr(
        sessionmaker, "__call__", lambda *a, **k: pytest.fail("Session opened")
    )
    with pytest.raises(AuditInputError):
        lazy_trail().record_permission_decision(
            req.model_copy(update={field: value}), dec, occurred_at=NOW
        )


@pytest.mark.parametrize("kind", ("request", "decision", "query", "record"))
def test_subclasses_rejected(kind: str) -> None:
    original: BaseModel = {
        "request": request(),
        "decision": decision(request()),
        "query": AuditListQuery(),
        "record": record(),
    }[kind]
    subclass = cast(type[BaseModel], type("Derived", (type(original),), {}))
    derived = subclass.model_validate(original.model_dump())
    trail = lazy_trail()
    if kind == "request":
        with pytest.raises(AuditInputError):
            trail.record_permission_decision(
                cast(PermissionRequest, derived), decision(request()), occurred_at=NOW
            )
    elif kind == "decision":
        with pytest.raises(AuditInputError):
            trail.record_permission_decision(
                request(), cast(PermissionDecision, derived), occurred_at=NOW
            )
    elif kind == "query":
        with pytest.raises(AuditInputError):
            trail.list_records(cast(AuditListQuery, derived))
    else:
        with pytest.raises(ValidationError):
            AuditRecord.model_validate(derived)


@pytest.mark.parametrize("kind", ("permission", "outcome"))
@pytest.mark.parametrize(
    "changes",
    (
        {"occurred_at": NOW - timedelta(microseconds=1)},
        {"occurred_at": NOW.replace(tzinfo=None)},
        {"audit_id": ""},
        {"correlation_id": ""},
        {"correlation_id": True},
    ),
)
def test_invalid_append_arguments(kind: str, changes: dict[str, object]) -> None:
    trail = lazy_trail()
    args = {"occurred_at": NOW} | changes
    with pytest.raises(AuditInputError):
        if kind == "permission":
            trail.record_permission_decision(request(), decision(request()), **args)  # type: ignore[arg-type]
        else:
            trail.record_tool_outcome(
                request(),
                decision(request()),
                ToolResultStatus.SUCCESS,
                **args,  # type: ignore[arg-type]
            )


def test_permission_states_and_private_hash(
    database: tuple[Engine, sessionmaker[Session], AuditTrail],
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine, _, trail = database
    expected = ("allowed", "approval_required", "denied", "blocked")
    for risk, approval, state in zip(
        ("P0", "P3", "P4", "P5"),
        (None, None, "denied", "approved"),
        expected,
        strict=True,
    ):
        req = request(risk)
        dec = decision(req, approval)
        result = trail.record_permission_decision(
            req,
            dec,
            occurred_at=NOW,
            audit_id=f"Audit.{state}",
            correlation_id=" Corr.A ",
        )
        assert result.permission_status.value == state
        assert result.tool_result_status is None
        assert result.correlation_id == "Corr.A"
        assert (result.task_id, result.session_id) == (req.task_id, req.session_id)
        assert result.request_reason_hash == sha256(PRIVATE.encode("utf-8")).hexdigest()
        assert (
            result.request_reason_hash != sha256(PRIVATE.strip().encode()).hexdigest()
        )
        assert trail.get(result.audit_id) == result
    assert PRIVATE not in caplog.text
    assert "reason" not in {
        col["name"] for col in inspect(engine).get_columns("audit_events")
    }
    with engine.connect() as conn:
        for row in conn.execute(text("SELECT * FROM audit_events")):
            assert PRIVATE not in str(tuple(row))


def test_six_outcomes_p0_p3_p4_and_repeated_request(
    database: tuple[Engine, sessionmaker[Session], AuditTrail],
) -> None:
    _, _, trail = database
    ids: set[str] = set()
    for risk in ("P0", "P3", "P4"):
        req = request(risk)
        dec = decision(req, "approved")
        for status in ToolResultStatus:
            if status is ToolResultStatus.DENIED:
                continue
            outcome = trail.record_tool_outcome(req, dec, status, occurred_at=NOW)
            assert outcome.audit_id not in ids
            ids.add(outcome.audit_id)
            assert outcome.event_kind is AuditEventKind.TOOL_OUTCOME
            assert outcome.tool_result_status is status
            assert outcome.risk_level.value == risk
            assert outcome.correlation_id is None
            assert trail.get(outcome.audit_id) == outcome
    assert len(trail.list_records()) == 18
    assert len(trail.list_records(AuditListQuery(request_id="Request.A"))) == 18
    assert not hasattr(outcome, "verified") and not hasattr(outcome, "authorized")


@pytest.mark.parametrize(
    "risk,approval",
    (
        ("P1", None),
        ("P2", None),
        ("P3", None),
        ("P4", None),
        ("P3", "denied"),
        ("P5", "approved"),
    ),
)
def test_nonallowed_outcome_rejected(risk: str, approval: str | None) -> None:
    req = request(risk)
    with pytest.raises(AuditInputError):
        lazy_trail().record_tool_outcome(
            req, decision(req, approval), ToolResultStatus.SUCCESS, occurred_at=NOW
        )


@pytest.mark.parametrize(
    "value",
    (
        ToolResultStatus.DENIED,
        None,
        "success",
        "denied",
        True,
        Foreign.SUCCESS,
    ),
)
def test_invalid_result_status(value: object) -> None:
    with pytest.raises(AuditInputError):
        lazy_trail().record_tool_outcome(
            request(),
            decision(request()),
            cast(ToolResultStatus, value),
            occurred_at=NOW,
        )


def test_evidence_ids_preserved_without_authority(
    database: tuple[Engine, sessionmaker[Session], AuditTrail],
) -> None:
    _, _, trail = database
    req = request("P3")
    scope = {
        name: getattr(req, name)
        for name in ("actor_id", "tool_id", "action", "resource", "reason")
    }
    grant = PermissionGrant.model_validate(
        scope
        | dict(
            grant_id="Grant.Case",
            risk_level="P3",
            duration="task",
            task_id=req.task_id,
            issued_at=NOW,
            user_decision="approved",
        )
    )
    rule = PermissionPolicyRule.model_validate(
        scope | dict(rule_id="Rule.Case", risk_level="P3")
    )
    policy_engine = PermissionEngine(registry(), PermissionPolicy(rules=(rule,)))
    decs = (
        PermissionEngine(registry()).evaluate(req, grants=(grant,)),
        policy_engine.evaluate(req),
        decision(req, "approved"),
    )
    for dec, field, expected in zip(
        decs,
        ("matched_grant_id", "matched_policy_rule_id", "user_decision_id"),
        ("Grant.Case", "Rule.Case", "User.A"),
        strict=True,
    ):
        result = trail.record_tool_outcome(
            req, dec, ToolResultStatus.FAILURE, occurred_at=NOW
        )
        assert getattr(result, field) == expected
        assert trail.get(result.audit_id) == result
    assert policy_engine.policy.rules == (rule,)
    assert (
        PermissionEngine(registry()).evaluate(req).status
        is PermissionStatus.APPROVAL_REQUIRED
    )
    none_req = request(task_id=None, session_id=None)
    row = trail.record_permission_decision(
        none_req, decision(none_req), occurred_at=NOW
    )
    assert row.task_id is row.session_id is None


def test_duplicate_and_exact_get(
    database: tuple[Engine, sessionmaker[Session], AuditTrail],
) -> None:
    _, factory, trail = database
    original = trail.record_permission_decision(
        request(), decision(request()), occurred_at=NOW, audit_id="Audit.Case"
    )
    with pytest.raises(AuditConflictError):
        trail.record_permission_decision(
            request(resource="different"),
            decision(request()),
            occurred_at=NOW,
            audit_id="Audit.Case",
        )
    assert trail.get(" Audit.Case ") == original
    assert trail.get("audit.case") is None
    assert trail.get("missing") is None
    # Returned domain evidence survives the write session and caller engine ownership.
    with factory() as session:
        assert session.get(AuditEventRow, original.audit_id) is not None
    assert original.model_dump()["audit_id"] == "Audit.Case"


@pytest.mark.parametrize("value", BAD_TEXT)
def test_get_rejects_malformed_id(value: object) -> None:
    with pytest.raises(AuditInputError):
        lazy_trail().get(cast(str, value))


@pytest.mark.parametrize(
    "operation", ("raw_update", "raw_delete", "orm_update", "orm_delete")
)
def test_db_mutation_triggers(
    operation: str, database: tuple[Engine, sessionmaker[Session], AuditTrail]
) -> None:
    engine, factory, trail = database
    original = trail.record_permission_decision(
        request(), decision(request()), occurred_at=NOW, audit_id="Original"
    )
    with pytest.raises(IntegrityError, match="Audit history is append-only"):
        if operation.startswith("raw"):
            with engine.begin() as conn:
                sql = (
                    "UPDATE audit_events SET resource='changed' WHERE id='Original'"
                    if operation == "raw_update"
                    else "DELETE FROM audit_events WHERE id='Original'"
                )
                conn.execute(text(sql))
        else:
            with factory.begin() as session:
                row = session.get(AuditEventRow, "Original")
                assert row is not None
                if operation == "orm_update":
                    row.resource = "changed"
                else:
                    session.delete(row)
    assert trail.get("Original") == original
    assert trail.list_records() == (original,)


@pytest.mark.parametrize(
    "field,value",
    (
        ("event_kind", "unknown"),
        ("risk_level", "P6"),
        ("permission_status", "unknown"),
        ("permission_reason_code", "unknown"),
        ("request_reason_hash", "a" * 63),
        ("request_reason_hash", "A" * 64),
        ("tool_result_status", "unknown"),
        ("tool_result_status", "success"),
        ("event_kind", "tool_outcome"),
    ),
)
def test_database_check_enforcement(
    field: str,
    value: object,
    database: tuple[Engine, sessionmaker[Session], AuditTrail],
) -> None:
    _, factory, trail = database
    data = record().model_dump()
    data["id"] = data.pop("audit_id")
    data[field] = value
    with pytest.raises(IntegrityError), factory.begin() as session:
        session.add(AuditEventRow(**data))
    assert trail.list_records() == ()


@pytest.mark.parametrize(
    "field,value",
    (
        ("resource", ""),
        ("permission_reason_code", "matching_grant"),
        ("task_id", ""),
        ("user_decision_id", "unexpected"),
    ),
)
def test_corrupt_rows_revalidated(
    field: str,
    value: object,
    database: tuple[Engine, sessionmaker[Session], AuditTrail],
) -> None:
    _, factory, trail = database
    data = record().model_dump()
    data["id"] = data.pop("audit_id")
    data[field] = value
    with factory.begin() as session:
        session.add(AuditEventRow(**data))
    for read in (lambda: trail.get("Audit.A"), trail.list_records):
        with pytest.raises(AuditDataError) as caught:
            read()
        assert "Invalid stored audit evidence" == str(caught.value)
        assert PRIVATE not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    "field", ("request_id", "actor_id", "tool_id", "task_id", "session_id")
)
@pytest.mark.parametrize("value", BAD_TEXT[1:])
def test_invalid_query_identifier(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AuditListQuery.model_validate({field: value})


@pytest.mark.parametrize(
    "field,value",
    (
        ("limit", 0),
        ("limit", 501),
        ("limit", True),
        ("limit", "1"),
        ("limit", 1.0),
        ("offset", -1),
        ("offset", False),
        ("offset", "0"),
        ("offset", 0.0),
        ("event_kind", "unknown"),
        ("risk_level", "P6"),
        ("extra", 1),
    ),
)
def test_invalid_query_bounds_and_vocabulary(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        AuditListQuery.model_validate({field: value})
    with pytest.raises(AuditInputError):
        lazy_trail().list_records(AuditListQuery().model_copy(update={field: value}))


def test_list_equality_order_pagination(
    database: tuple[Engine, sessionmaker[Session], AuditTrail],
) -> None:
    _, _, trail = database
    for identity, delta, req in (
        ("Z", 0, request()),
        (
            "A",
            0,
            request(
                "P3",
                request_id="Request.%_",
                actor_id="Actor.%_",
                task_id=None,
                session_id=None,
            ),
        ),
        ("B", 1, request("P4")),
    ):
        trail.record_permission_decision(
            req,
            decision(req, "approved"),
            occurred_at=NOW + timedelta(seconds=delta),
            audit_id=identity,
        )
    assert tuple(x.audit_id for x in trail.list_records()) == ("A", "Z", "B")
    assert tuple(
        x.audit_id for x in trail.list_records(AuditListQuery(limit=1, offset=1))
    ) == ("Z",)
    assert AuditListQuery().limit == 100 and AuditListQuery().offset == 0
    assert AuditListQuery(limit=500).limit == 500
    cases = (
        ({"event_kind": "permission_decision"}, ("A", "Z", "B")),
        ({"event_kind": "tool_outcome"}, ()),
        ({"request_id": "Request.%_"}, ("A",)),
        ({"actor_id": "Actor.%_"}, ("A",)),
        ({"tool_id": "Tool.P3"}, ("A",)),
        ({"risk_level": "P4"}, ("B",)),
        ({"task_id": "Task.A"}, ("Z", "B")),
        ({"session_id": "Session.A"}, ("Z", "B")),
        ({"task_id": None}, ("A", "Z", "B")),
        ({"request_id": "Request.%"}, ()),
        ({"actor_id": "Actor.*"}, ()),
        ({"tool_id": "Tool.P"}, ()),
        ({"tool_id": "tool.p3"}, ()),
        ({"actor_id": "Actor.A", "risk_level": "P3"}, ()),
    )
    for filters, expected in cases:
        found = trail.list_records(AuditListQuery.model_validate(filters))
        assert type(found) is tuple
        assert tuple(x.audit_id for x in found) == expected


def assert_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    columns = inspector.get_columns("audit_events")
    assert tuple(col["name"] for col in columns) == ("id", *FIELDS[1:])
    assert {col["name"] for col in columns if col["nullable"]} == set(OPTIONAL_IDS) | {
        "tool_result_status"
    }
    assert all(
        str(col["type"]) == ("DATETIME" if col["name"] == "occurred_at" else "VARCHAR")
        for col in columns
    )
    assert inspector.get_pk_constraint("audit_events") == {
        "constrained_columns": ["id"],
        "name": "pk_audit_events",
    }
    checks = inspector.get_check_constraints("audit_events")
    assert {check["name"] for check in checks} == CHECKS
    mapping = {
        constraint.name: str(constraint.sqltext)
        for constraint in cast(Table, AuditEventRow.__table__).constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {check["name"]: check["sqltext"] for check in checks} == mapping
    indexes = inspector.get_indexes("audit_events")
    assert {index["name"]: index["column_names"] for index in indexes} == INDEXES
    assert all(not index["unique"] for index in indexes)
    assert inspector.get_foreign_keys("audit_events") == []
    assert inspector.get_unique_constraints("audit_events") == []
    with engine.connect() as conn:
        assert (
            set(
                conn.execute(
                    text(
                        "SELECT name FROM sqlite_master WHERE type='trigger' "
                        "AND tbl_name='audit_events'"
                    )
                ).scalars()
            )
            == TRIGGERS
        )
        assert compare_metadata(MigrationContext.configure(conn), Base.metadata) == []


def test_migration_lifecycle_preserves_earlier_schema(tmp_path: Path) -> None:
    path = tmp_path / "lifecycle.db"
    cfg = config(path)
    scripts = ScriptDirectory.from_config(cfg)
    assert scripts.get_heads() == ["0009_default_mission_path"]
    revision = scripts.get_revision("0005_audit_events")
    assert revision is not None and revision.down_revision == "0004_embedding_records"
    engine = create_engine(f"sqlite:///{path}")
    try:
        snapshots: dict[str, object] = {}
        for revision_id in (
            "0001",
            "0002_memory_schema",
            "0003_document_ingestion",
            "0004_embedding_records",
        ):
            command.upgrade(cfg, revision_id)
            assert "audit_events" not in inspect(engine).get_table_names()
        with sessionmaker(bind=engine).begin() as session:
            session.add(
                MemoryRow(
                    id="keep",
                    memory_type="episodic",
                    content="source",
                    source_type="user",
                    scope="project",
                    confidence=1,
                    importance=1,
                    created_at=NOW,
                    updated_at=NOW,
                    metadata_json={},
                )
            )
            session.add(
                DocumentRow(
                    id="doc",
                    source_type="file",
                    source_ref="opaque",
                    media_type="text/plain",
                    source_hash="hash",
                    parser_id="plain",
                    parser_version="1",
                    chunk_count=1,
                    indexed_at=NOW,
                    metadata_json={},
                )
            )
            session.flush()
            session.add(
                DocumentChunkRow(
                    id="chunk",
                    document_id="doc",
                    chunk_index=0,
                    content="source",
                    content_hash="hash",
                    parser_version="1",
                    metadata_json={},
                )
            )
            session.add(
                EmbeddingRecordRow(
                    id="embedding",
                    source_kind="document_chunk",
                    source_id="chunk",
                    source_hash="hash",
                    provider_id="fake",
                    model_id="fake",
                    dimensions=1,
                    vector=[1.0],
                    created_at=NOW,
                )
            )

        def source_rows() -> tuple[tuple[tuple[object, ...], ...], ...]:
            with engine.connect() as conn:
                return tuple(
                    tuple(
                        tuple(row)
                        for row in conn.execute(select(Base.metadata.tables[name]))
                    )
                    for name in (
                        "memories",
                        "documents",
                        "document_chunks",
                        "embedding_records",
                    )
                )

        previous_rows = source_rows()
        for table in inspect(engine).get_table_names():
            if table != "alembic_version":
                snapshots[table] = (
                    inspect(engine).get_columns(table),
                    inspect(engine).get_check_constraints(table),
                    inspect(engine).get_indexes(table),
                )
        command.upgrade(cfg, "head")
        assert source_rows() == previous_rows
        assert AuditEventRow.metadata is Base.metadata
        assert_schema(engine)
        command.downgrade(cfg, "0004_embedding_records")
        assert "audit_events" not in inspect(engine).get_table_names()
        with engine.connect() as conn:
            assert (
                not set(
                    conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='trigger'")
                    ).scalars()
                )
                & TRIGGERS
            )
        assert source_rows() == previous_rows
        for table, before in snapshots.items():
            after = (
                inspect(engine).get_columns(table),
                inspect(engine).get_check_constraints(table),
                inspect(engine).get_indexes(table),
            )
            # SQLAlchemy reflected type instances compare by representation.
            assert repr(after) == repr(before)
        command.upgrade(cfg, "head")
        assert source_rows() == previous_rows
        assert_schema(engine)
    finally:
        engine.dispose()


def test_utc_roundtrip(
    database: tuple[Engine, sessionmaker[Session], AuditTrail],
) -> None:
    _, factory, trail = database
    shifted = NOW.astimezone(timezone(timedelta(hours=-4)))
    result = trail.record_permission_decision(
        request(), decision(request()), occurred_at=shifted
    )
    with factory() as session:
        row = session.get(AuditEventRow, result.audit_id)
        assert row is not None and row.occurred_at == NOW
        assert row.occurred_at.tzinfo is UTC
    fetched = trail.get(result.audit_id)
    assert fetched is not None and fetched.occurred_at.tzinfo is UTC


def test_safe_failure_rollback_close_no_retry(
    database: tuple[Engine, sessionmaker[Session], AuditTrail],
) -> None:
    engine, _, _ = database
    events: list[str] = []

    class FailingSession(Session):
        def commit(self) -> None:
            events.append("commit")
            self.flush()
            raise OperationalError(
                "PRIVATE SQL",
                {"resource": "PRIVATE PARAMETER"},
                RuntimeError("PRIVATE DB PATH"),
            )

        def close(self) -> None:
            events.append("close")
            super().close()

    trail = AuditTrail(sessionmaker(bind=engine, class_=FailingSession), registry())
    with pytest.raises(AuditPersistenceError) as caught:
        trail.record_permission_decision(
            request(), decision(request()), occurred_at=NOW
        )
    formatted = "".join(traceback.format_exception(caught.value))
    assert "PRIVATE SQL" not in formatted and "PRIVATE PARAMETER" not in formatted
    assert "PRIVATE DB PATH" not in formatted and PRIVATE not in formatted
    assert events == ["commit", "close"]
    assert AuditTrail(sessionmaker(bind=engine), registry()).list_records() == ()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1


def test_closed_missing_database_safe_errors(tmp_path: Path) -> None:
    path = tmp_path / "private-db-location" / "secret.db"
    engine = create_engine(f"sqlite:///{path}")
    trail = AuditTrail(sessionmaker(bind=engine), registry())
    try:
        for call in (
            lambda: trail.get("private-ID"),
            trail.list_records,
            lambda: trail.record_permission_decision(
                request(resource="private-resource"),
                decision(request()),
                occurred_at=NOW,
            ),
        ):
            with pytest.raises(AuditPersistenceError) as caught:
                call()
            output = "".join(traceback.format_exception(caught.value))
            for secret in (
                str(path),
                "private-resource",
                PRIVATE,
                "SELECT audit_events",
                "sqlite3.OperationalError",
            ):
                assert secret not in output
        assert not path.parent.exists()
    finally:
        engine.dispose()


def test_immutable_constructor_and_no_mutating_api() -> None:
    trail = lazy_trail()
    with pytest.raises(FrozenInstanceError):
        setattr(trail, "_registry", registry())
    with pytest.raises(AuditInputError):
        AuditTrail.__init__(
            trail, sessionmaker(bind=create_engine("sqlite://")), registry()
        )
    assert {
        name
        for name, value in vars(AuditTrail).items()
        if not name.startswith("_") and callable(value)
    } == {"record_permission_decision", "record_tool_outcome", "get", "list_records"}


def test_import_and_constructor_isolation(tmp_path: Path) -> None:
    script = r"""
import sys, platform, socket, sqlite3, subprocess, threading, asyncio, os, _thread
from datetime import UTC, datetime
sys.dont_write_bytecode = True
platform.machine()
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from phoenix.persistence import memory, documents, embeddings
from phoenix.security.permissions import PermissionEngine
from phoenix.tools.registry import ToolRegistry
from phoenix.core.event_bus import EventBus
engine = create_engine("sqlite://")
factory = sessionmaker(bind=engine)
def forbidden(*a, **k):
    raise AssertionError("Unexpected side effect")
def audit(event, args):
    if event == "open" and args[2] & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
        forbidden()
    if event.startswith(("socket.", "sqlite3.", "subprocess.")):
        forbidden()
    if event in ("os.mkdir", "os.remove", "os.rename", "os.system"):
        forbidden()
sys.addaudithook(audit)
os.mkdir = forbidden
socket.socket = forbidden
sqlite3.connect = forbidden
sqlite3.dbapi2.connect = forbidden
subprocess.Popen = forbidden
threading.Thread.start = forbidden
_thread.start_new_thread = forbidden
asyncio.create_task = forbidden
asyncio.BaseEventLoop.create_task = forbidden
EventBus.publish = forbidden
PermissionEngine.evaluate = forbidden
engine.connect = forbidden
engine.dispose = forbidden
sessionmaker.__call__ = forbidden
from phoenix.security.audit import AuditTrail, AuditListQuery, AuditRecord
from phoenix.persistence.audit import AuditEventRow
AuditTrail(factory, ToolRegistry())
AuditListQuery()
AuditEventRow(id="inert")
AuditRecord(event_kind="permission_decision",
    occurred_at=datetime(2026, 10, 6, tzinfo=UTC),
    request_id="r", actor_id="a", tool_id="t", action="act", resource="opaque",
    request_reason_hash="a"*64, risk_level="P0", permission_requirement="p",
    permission_status="allowed", permission_reason_code="p0_internal")
assert not engine.pool.checkedout() if hasattr(engine.pool, "checkedout") else True
print("guarded import and construction passed")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "guarded import and construction passed" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_static_separation_and_event_vocabulary() -> None:
    assert len(EventType) == 32
    assert not any(x.value.startswith("audit.") for x in EventType)
    tree = ast.parse(python_inspect.getsource(module))
    imports = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert {x for x in imports if x and x.startswith("phoenix.")} == {
        "phoenix.persistence.audit",
        "phoenix.security.permissions",
        "phoenix.tools.registry",
    }
    forbidden = {
        "execute",
        "evaluate",
        "publish",
        "emit",
        "create_all",
        "dispose",
        "mkdir",
        "unlink",
        "Popen",
        "run",
        "load_model",
        "embed",
        "read_text",
        "write_text",
    }
    assert (
        not {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        & forbidden
    )
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id
        in {
            "PermissionEngine",
            "PermissionGrant",
            "UserPermissionDecision",
            "PermissionPolicy",
        }
        for node in ast.walk(tree)
    )


@pytest.mark.parametrize("kind", ("permission", "outcome", "get", "list"))
def test_service_errors_hide_validation_details(kind: str) -> None:
    req = request(resource="opaque-private-resource")
    dec = decision(req)
    trail = lazy_trail()
    malformed = req.model_copy(update={"actor_id": {"private": "evidence"}})
    with pytest.raises(AuditInputError) as caught:
        if kind == "permission":
            trail.record_permission_decision(malformed, dec, occurred_at=NOW)
        elif kind == "outcome":
            trail.record_tool_outcome(
                malformed, dec, ToolResultStatus.SUCCESS, occurred_at=NOW
            )
        elif kind == "get":
            trail.get(cast(str, {"private": "evidence"}))
        else:
            trail.list_records(
                AuditListQuery().model_copy(update={"tool_id": {"private": "evidence"}})
            )
    formatted = "".join(traceback.format_exception(caught.value))
    assert "input_value" not in formatted
    assert PRIVATE not in formatted and req.resource not in formatted
    assert caught.value.__suppress_context__


@pytest.mark.parametrize(
    "field,vocabulary",
    (
        ("risk_level", ToolRiskLevel),
        ("status", PermissionStatus),
        ("reason_code", DecisionReason),
    ),
)
@pytest.mark.parametrize("variant", ("foreign", "forged"))
def test_forged_decision_enum_at_service(
    field: str, vocabulary: type[StrEnum], variant: str
) -> None:
    dec = decision(request())
    member = getattr(dec, field)
    if variant == "foreign":
        value: object = Foreign(member.value)
    else:
        value = str.__new__(vocabulary, member.value)
        object.__setattr__(value, "_name_", member.name)
        object.__setattr__(value, "_value_", member.value)
    with pytest.raises(AuditInputError):
        lazy_trail().record_permission_decision(
            request(), dec.model_copy(update={field: value}), occurred_at=NOW
        )


def test_forged_helper_result_query_and_registry() -> None:
    forged_risk = str.__new__(ToolRiskLevel, "P3")
    object.__setattr__(forged_risk, "_name_", "P3")
    object.__setattr__(forged_risk, "_value_", "P3")
    with pytest.raises(AuditInputError):
        audit_required_for_execution(forged_risk)
    with pytest.raises(AuditInputError):
        lazy_trail().list_records(
            AuditListQuery().model_copy(update={"risk_level": forged_risk})
        )
    forged_result = str.__new__(ToolResultStatus, "success")
    object.__setattr__(forged_result, "_name_", "SUCCESS")
    object.__setattr__(forged_result, "_value_", "success")
    with pytest.raises(AuditInputError):
        lazy_trail().record_tool_outcome(
            request(), decision(request()), forged_result, occurred_at=NOW
        )
    source = registry()
    tool = source.require("Tool.P3")
    object.__setattr__(tool, "risk_level", forged_risk)
    with pytest.raises(AuditInputError):
        AuditTrail(sessionmaker(bind=create_engine("sqlite://")), source)


@pytest.mark.parametrize("model", ("request", "decision", "query"))
@pytest.mark.parametrize("extra", ("copied", "injected"))
def test_all_boundary_extras(model: str, extra: str) -> None:
    value: BaseModel = {
        "request": request(),
        "decision": decision(request()),
        "query": AuditListQuery(),
    }[model]
    if extra == "copied":
        value = value.model_copy(update={"unapproved": True})
    else:
        object.__setattr__(value, "__pydantic_extra__", {"unapproved": True})
    with pytest.raises(AuditInputError):
        trail = lazy_trail()
        if model == "request":
            trail.record_permission_decision(
                cast(PermissionRequest, value), decision(request()), occurred_at=NOW
            )
        elif model == "decision":
            trail.record_permission_decision(
                request(), cast(PermissionDecision, value), occurred_at=NOW
            )
        else:
            trail.list_records(cast(AuditListQuery, value))


@pytest.mark.parametrize("kind", ("request", "decision", "query", "record"))
@pytest.mark.parametrize("malformed", (True, ["unexpected"], "unexpected"))
def test_malformed_extra_storage(kind: str, malformed: object) -> None:
    value: BaseModel = {
        "request": request(),
        "decision": decision(request()),
        "query": AuditListQuery(),
        "record": record(),
    }[kind]
    object.__setattr__(value, "__pydantic_extra__", malformed)
    if kind == "record":
        with pytest.raises(ValidationError):
            AuditRecord.model_validate(value)
        return
    with pytest.raises(AuditInputError):
        trail = lazy_trail()
        if kind == "request":
            trail.record_permission_decision(
                cast(PermissionRequest, value), decision(request()), occurred_at=NOW
            )
        elif kind == "decision":
            trail.record_permission_decision(
                request(), cast(PermissionDecision, value), occurred_at=NOW
            )
        else:
            trail.list_records(cast(AuditListQuery, value))


def test_default_and_maximum_list_bounds(
    database: tuple[Engine, sessionmaker[Session], AuditTrail],
) -> None:
    _, factory, trail = database
    with factory.begin() as session:
        for index in range(105):
            data = record(audit_id=f"Audit.{index:03}").model_dump()
            data["id"] = data.pop("audit_id")
            session.add(AuditEventRow(**data))
    assert len(trail.list_records()) == 100
    assert len(trail.list_records(AuditListQuery(limit=500))) == 105
    assert len(trail.list_records(AuditListQuery(offset=100))) == 5
    assert trail.list_records(AuditListQuery(offset=105)) == ()


def test_primary_key_race_is_safe_conflict(
    database: tuple[Engine, sessionmaker[Session], AuditTrail],
) -> None:
    engine, _, trail = database
    original = trail.record_permission_decision(
        request(), decision(request()), occurred_at=NOW, audit_id="Race"
    )

    class BlindSession(Session):
        def get(self, *args: object, **kwargs: object) -> None:
            return None

    racer = AuditTrail(sessionmaker(bind=engine, class_=BlindSession), registry())
    with pytest.raises(AuditConflictError) as caught:
        racer.record_permission_decision(
            request(), decision(request()), occurred_at=NOW, audit_id="Race"
        )
    assert "INSERT" not in "".join(traceback.format_exception(caught.value))
    assert trail.get("Race") == original


@pytest.mark.parametrize(
    "revision",
    (
        "0001_baseline",
        "0002_memory_schema",
        "0003_document_ingestion",
        "0004_embedding_records",
    ),
)
def test_historical_migrations_byte_unchanged(revision: str) -> None:
    name = f"alembic/versions/{revision}.py"
    previous = subprocess.check_output(["git", "show", f"origin/main:{name}"], cwd=ROOT)
    assert (ROOT / name).read_bytes().replace(b"\r\n", b"\n") == previous.replace(
        b"\r\n", b"\n"
    )
