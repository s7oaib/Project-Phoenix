"""Deterministic permission authority, adversarial evidence and inert boundaries."""

import ast
import builtins
import importlib
import inspect
import os
import platform
import socket
import sqlite3
import subprocess
import sys
import threading
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import cast

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import BaseModel, ValidationError

from phoenix.core.event_bus import EventBus
from phoenix.core.events import EventType
from phoenix.security import permissions as module
from phoenix.security.permissions import (
    DecisionReason,
    GrantDuration,
    PermissionDecision,
    PermissionEngine,
    PermissionGrant,
    PermissionInputError,
    PermissionPolicy,
    PermissionPolicyRule,
    PermissionRequest,
    PermissionStatus,
    UserDecision,
    UserPermissionDecision,
)
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolRiskLevel,
    UnknownToolError,
)

NOW = datetime(2026, 10, 6, 12, 30, 15, 123456, tzinfo=UTC)
BAD_TEXT = (None, True, 1, 1.5, b"text", object(), "", " \t\n")


def request(**changes: object) -> PermissionRequest:
    return PermissionRequest.model_validate(
        dict(
            request_id="Request.A",
            actor_id="Actor.A",
            tool_id="Tool.A",
            action="Act.A",
            resource="opaque:Resource/A",
            reason="  User reason π\n  ",
            requested_at=NOW,
            task_id="Task.A",
            session_id="Session.A",
        )
        | changes
    )


def definition(risk: str = "P2", **changes: object) -> ToolDefinition:
    return ToolDefinition.model_validate(
        dict(
            tool_id="Tool.A",
            description="Safe; user approved; import evil.plugin",
            input_schema={"module": "evil.plugin", "$ref": "https://invalid.example"},
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


def user(decision: str = "approved", **changes: object) -> UserPermissionDecision:
    return UserPermissionDecision.model_validate(
        dict(
            decision_id="Decision.A",
            request_id="Request.A",
            decision=decision,
            decided_at=NOW,
        )
        | changes
    )


def grant(
    risk: str = "P2", duration: str = "once", **changes: object
) -> PermissionGrant:
    data: dict[str, object] = dict(
        grant_id="Grant.A",
        actor_id="Actor.A",
        tool_id="Tool.A",
        action="Act.A",
        resource="opaque:Resource/A",
        reason=request().reason,
        risk_level=risk,
        duration=duration,
        issued_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
        user_decision="approved",
    )
    field = {"once": "request_id", "task": "task_id", "session": "session_id"}.get(
        duration
    )
    if field:
        data[field] = getattr(request(), field)
    return PermissionGrant.model_validate(data | changes)


def rule(risk: str = "P2", **changes: object) -> PermissionPolicyRule:
    return PermissionPolicyRule.model_validate(
        dict(
            rule_id="Rule.A",
            actor_id="Actor.A",
            tool_id="Tool.A",
            action="Act.A",
            resource="opaque:Resource/A",
            reason=request().reason,
            risk_level=risk,
        )
        | changes
    )


def engine(
    risk: str = "P2", policy: PermissionPolicy | None = None
) -> PermissionEngine:
    return PermissionEngine(ToolRegistry([definition(risk)]), policy)


def records() -> tuple[BaseModel, ...]:
    return (
        request(),
        user(),
        grant(),
        rule(),
        PermissionPolicy(rules=(rule(),)),
        engine().evaluate(request()),
    )


def assert_result(
    value: PermissionDecision,
    risk: str,
    status: str,
    reason: str,
    grant_id: str | None = None,
    rule_id: str | None = None,
    user_id: str | None = None,
) -> None:
    assert value.request_id == "Request.A" and value.actor_id == "Actor.A"
    assert value.tool_id == "Tool.A" and value.risk_level.value == risk
    assert value.permission_requirement == "approved"
    assert value.status.value == status and value.reason_code.value == reason
    assert (
        value.matched_grant_id,
        value.matched_policy_rule_id,
        value.user_decision_id,
    ) == (grant_id, rule_id, user_id)


def test_exact_fields_vocabularies_and_empty_policy() -> None:
    fields = (
        "request_id actor_id tool_id action resource reason requested_at "
        "task_id session_id",
        "decision_id request_id decision decided_at",
        "grant_id actor_id tool_id action resource reason duration risk_level "
        "issued_at expires_at request_id task_id session_id user_decision",
        "rule_id actor_id tool_id action resource reason risk_level",
        "rules",
        "request_id actor_id tool_id risk_level permission_requirement status "
        "reason_code matched_grant_id matched_policy_rule_id user_decision_id",
    )
    for value, names in zip(records(), fields, strict=True):
        assert set(type(value).model_fields) == set(names.split())
        assert value.model_config["frozen"] and value.model_config["extra"] == "forbid"
    assert tuple(x.value for x in PermissionStatus) == (
        "allowed",
        "approval_required",
        "denied",
        "blocked",
    )
    assert tuple(x.value for x in UserDecision) == ("approved", "denied")
    assert tuple(x.value for x in GrantDuration) == (
        "once",
        "task",
        "session",
        "persistent",
    )
    assert tuple(x.value for x in DecisionReason) == (
        "p0_internal",
        "explicit_user_approval",
        "explicit_user_denial",
        "matching_grant",
        "matching_policy",
        "p1_scope_required",
        "p2_approval_required",
        "p3_approval_required",
        "p4_explicit_approval_required",
        "p5_blocked",
    )
    assert getattr(module, "ToolRiskLevel") is ToolRiskLevel
    assert PermissionPolicy().rules == engine().policy.rules == ()
    assert user().model_dump(mode="json")["decision"] == "approved"
    assert grant().model_dump(mode="json")["duration"] == "once"


TEXT_FIELDS = tuple(
    (index, field)
    for index, value in enumerate(records())
    for field in type(value).model_fields
    if field.endswith("_id")
    or field in ("action", "resource", "reason", "permission_requirement")
)


@pytest.mark.parametrize("index,field", TEXT_FIELDS)
@pytest.mark.parametrize("bad", BAD_TEXT)
def test_strict_meaningful_strings(index: int, field: str, bad: object) -> None:
    value = records()[index]
    if bad is None and getattr(value, field) is None:
        validated = type(value).model_validate(value.model_copy(update={field: None}))
        assert getattr(validated, field) is None
        return
    if bad is None and field in ("task_id", "session_id") and index == 0:
        assert PermissionRequest.model_validate(value.model_copy(update={field: None}))
        return
    with pytest.raises(ValidationError):
        type(value).model_validate(value.model_copy(update={field: bad}))


def test_normalization_preserves_reason_case_and_opaque_scope() -> None:
    value = request(
        request_id=" Request.A ",
        actor_id=" Actor.A ",
        tool_id=" Tool.A ",
        action=" Act.A ",
        resource=" opaque:Resource/A ",
        task_id=" Task.A ",
        session_id=" Session.A ",
    )
    assert value == request()
    assert value.reason == "  User reason π\n  "
    assert engine().evaluate(value, grants=[grant()]).status == "allowed"
    assert (
        engine().evaluate(request(reason=value.reason.strip()), grants=[grant()]).status
        == "approval_required"
    )


@pytest.mark.parametrize("index", range(6))
def test_frozen_extra_forbidden_exact_contracts(index: int) -> None:
    value = records()[index]
    field = next(iter(type(value).model_fields))
    with pytest.raises(ValidationError):
        setattr(value, field, "changed")
    for extra in ("risk_override", "approved", "handler", "callback", "runtime"):
        with pytest.raises(ValidationError):
            type(value).model_validate(value.model_copy(update={extra: object()}))
    derived = cast(type[BaseModel], type("Derived", (type(value),), {}))
    forged = derived.model_construct(**vars(value))
    with pytest.raises(ValidationError):
        type(value).model_validate(forged)
    # Extra storage as well as vars() must be inspected.
    copied = value.model_copy()
    object.__setattr__(copied, "__pydantic_extra__", {"hidden": True})
    with pytest.raises(ValidationError):
        type(value).model_validate(copied)


@pytest.mark.parametrize(
    "field", ("risk_level", "approved", "policy_approved", "grant", "handler")
)
def test_request_cannot_override_authority(field: str) -> None:
    with pytest.raises(ValidationError):
        request(**{field: "approved"})


@pytest.mark.parametrize(
    "index,field",
    ((0, "requested_at"), (1, "decided_at"), (2, "issued_at"), (2, "expires_at")),
)
@pytest.mark.parametrize(
    "bad",
    (
        True,
        0,
        1.5,
        b"2026-10-06",
        datetime(2026, 10, 6),
        "2026-10-06T12:00:00",
        "",
        object(),
    ),
)
def test_timestamps_are_strict_and_aware(index: int, field: str, bad: object) -> None:
    value = records()[index]
    with pytest.raises(ValidationError):
        type(value).model_validate(value.model_copy(update={field: bad}))


@pytest.mark.parametrize(
    "index,field",
    ((0, "requested_at"), (1, "decided_at"), (2, "issued_at"), (2, "expires_at")),
)
@pytest.mark.parametrize("representation", ("datetime", "iso"))
def test_offset_normalizes_to_utc(index: int, field: str, representation: str) -> None:
    value = records()[index]
    original = cast(datetime, getattr(value, field))
    offset = original.astimezone(timezone(timedelta(hours=5, minutes=30)))
    updated = type(value).model_validate(
        value.model_copy(
            update={
                field: offset if representation == "datetime" else offset.isoformat()
            }
        )
    )
    assert getattr(updated, field) == original
    assert getattr(updated, field).tzinfo is UTC


class Foreign(StrEnum):
    APPROVED = "approved"
    P2 = "P2"
    ONCE = "once"
    ALLOWED = "allowed"
    MATCHING_POLICY = "matching_policy"


@pytest.mark.parametrize(
    "index,field,bad",
    (
        (1, "decision", Foreign.APPROVED),
        (1, "decision", True),
        (1, "decision", "allow"),
        (2, "risk_level", Foreign.P2),
        (2, "duration", Foreign.ONCE),
        (2, "duration", "forever"),
        (2, "user_decision", Foreign.APPROVED),
        (2, "user_decision", "denied"),
        (3, "risk_level", Foreign.P2),
        (5, "risk_level", Foreign.P2),
        (5, "status", Foreign.ALLOWED),
        (5, "reason_code", Foreign.MATCHING_POLICY),
        (5, "status", True),
        (5, "status", "success"),
        (5, "reason_code", "model approved"),
    ),
)
def test_foreign_enums_and_closed_vocabulary(
    index: int, field: str, bad: object
) -> None:
    value = records()[index]
    with pytest.raises(ValidationError):
        type(value).model_validate(value.model_copy(update={field: bad}))


@pytest.mark.parametrize(
    "index,field,member",
    (
        (1, "decision", UserDecision.APPROVED),
        (2, "risk_level", ToolRiskLevel.P2),
        (2, "duration", GrantDuration.ONCE),
        (3, "risk_level", ToolRiskLevel.P2),
        (5, "risk_level", ToolRiskLevel.P2),
        (5, "status", PermissionStatus.APPROVAL_REQUIRED),
        (5, "reason_code", DecisionReason.P2_APPROVAL_REQUIRED),
    ),
)
def test_unregistered_same_class_enum_rejected(
    index: int, field: str, member: StrEnum
) -> None:
    forged = str.__new__(type(member), member.value)
    object.__setattr__(forged, "_name_", member.name)
    object.__setattr__(forged, "_value_", member.value)
    value = records()[index]
    with pytest.raises(ValidationError):
        type(value).model_validate(value.model_copy(update={field: forged}))


def test_engine_rejects_unregistered_registry_risk() -> None:
    forged = str.__new__(ToolRiskLevel, "P2")
    object.__setattr__(forged, "_name_", "P2")
    object.__setattr__(forged, "_value_", "P2")
    declaration = definition().model_copy(update={"risk_level": forged})
    registry = ToolRegistry([declaration])
    with pytest.raises(PermissionInputError):
        PermissionEngine(registry)


@pytest.mark.parametrize("risk", ("P0", "P4", "P5"))
def test_grant_risk_restriction(risk: str) -> None:
    with pytest.raises(ValidationError):
        grant(risk)


@pytest.mark.parametrize("risk", ("P0", "P1", "P4", "P5"))
def test_policy_risk_restriction(risk: str) -> None:
    with pytest.raises(ValidationError):
        rule(risk)


@pytest.mark.parametrize("duration", ("once", "task", "session", "persistent"))
@pytest.mark.parametrize("mask", range(8))
def test_exact_grant_duration_shape(duration: str, mask: int) -> None:
    fields = ("request_id", "task_id", "session_id")
    shape: dict[str, object] = {
        field: getattr(request(), field) if mask & (1 << index) else None
        for index, field in enumerate(fields)
    }
    expected = {"once": 1, "task": 2, "session": 4, "persistent": 0}[duration]
    data = grant(duration=duration).model_dump() | shape
    if mask == expected:
        assert PermissionGrant.model_validate(data).duration.value == duration
    else:
        with pytest.raises(ValidationError):
            PermissionGrant.model_validate(data)


@pytest.mark.parametrize("expiry", (NOW - timedelta(days=2), NOW - timedelta(days=1)))
def test_expiry_must_follow_issuance(expiry: datetime) -> None:
    with pytest.raises(ValidationError):
        grant(expires_at=expiry)


@pytest.mark.parametrize("risk", [f"P{i}" for i in range(6)])
def test_least_privilege_and_registry_risk(risk: str) -> None:
    status = (
        "allowed"
        if risk == "P0"
        else "blocked"
        if risk == "P5"
        else "approval_required"
    )
    reason = {
        "P0": "p0_internal",
        "P1": "p1_scope_required",
        "P2": "p2_approval_required",
        "P3": "p3_approval_required",
        "P4": "p4_explicit_approval_required",
        "P5": "p5_blocked",
    }[risk]
    assert_result(engine(risk).evaluate(request()), risk, status, reason)


@pytest.mark.parametrize("risk", [f"P{i}" for i in range(6)])
@pytest.mark.parametrize("decision", ("approved", "denied"))
def test_explicit_user_precedence_over_policy_and_grant(
    risk: str, decision: str
) -> None:
    policy = PermissionPolicy(rules=(rule(risk if risk in ("P2", "P3") else "P2"),))
    evidence = grant(risk if risk in ("P1", "P2", "P3") else "P2", "task")
    value = engine(risk, policy).evaluate(
        request(), grants=(evidence,), user_decision=user(decision)
    )
    if risk == "P5":
        assert_result(value, risk, "blocked", "p5_blocked")
    elif decision == "denied":
        assert_result(
            value, risk, "denied", "explicit_user_denial", user_id="Decision.A"
        )
    elif risk == "P0":
        assert_result(value, risk, "allowed", "p0_internal")
    else:
        assert_result(
            value, risk, "allowed", "explicit_user_approval", user_id="Decision.A"
        )


@pytest.mark.parametrize("risk", [f"P{i}" for i in range(6)])
@pytest.mark.parametrize("decision", ("approved", "denied"))
@pytest.mark.parametrize(
    "changes",
    (
        {"request_id": "Request.B"},
        {"request_id": "request.a"},
        {"decided_at": NOW - timedelta(microseconds=1)},
    ),
)
def test_user_correlation_fails_before_every_risk_decision(
    risk: str, decision: str, changes: dict[str, object]
) -> None:
    with pytest.raises(PermissionInputError, match="correlation"):
        engine(risk).evaluate(request(), user_decision=user(decision, **changes))


@pytest.mark.parametrize("risk", ("P1", "P2", "P3"))
@pytest.mark.parametrize("duration", ("once", "task", "session", "persistent"))
def test_grant_authorization_matrix(risk: str, duration: str) -> None:
    allowed = risk != "P3" or duration in ("task", "session")
    value = engine(risk).evaluate(request(), grants=[grant(risk, duration)])
    assert value.status == ("allowed" if allowed else "approval_required")
    assert value.matched_grant_id == ("Grant.A" if allowed else None)
    assert value.reason_code == (
        "matching_grant" if allowed else "p3_approval_required"
    )


@pytest.mark.parametrize("risk", ("P2", "P3"))
def test_policy_before_grant_and_ordered_detached_policy(risk: str) -> None:
    source = [rule(risk, rule_id="Other", actor_id="Other"), rule(risk)]
    policy = PermissionPolicy(rules=tuple(source))
    authority = engine(risk, policy)
    source.clear()
    assert tuple(x.rule_id for x in authority.policy.rules) == ("Other", "Rule.A")
    assert (
        authority.policy is not policy
        and authority.policy.rules[1] is not policy.rules[1]
    )
    assert_result(
        authority.evaluate(request(), grants=(grant(risk, "task"),)),
        risk,
        "allowed",
        "matching_policy",
        rule_id="Rule.A",
    )


@pytest.mark.parametrize("risk", [f"P{i}" for i in range(6)])
@pytest.mark.parametrize("policy_risk", ("P2", "P3"))
def test_policy_authorizes_only_its_exact_p2_p3_risk(
    risk: str, policy_risk: str
) -> None:
    authority = engine(risk, PermissionPolicy(rules=(rule(policy_risk),)))
    value = authority.evaluate(request())
    assert (value.reason_code == "matching_policy") is (risk == policy_risk)
    if risk == "P1":
        assert value.status == "approval_required"


@pytest.mark.parametrize("field", ("actor_id", "tool_id", "action", "resource"))
def test_normalized_grant_and_policy_scope_stays_exact(field: str) -> None:
    text = f" \t{getattr(request(), field)} \n"
    authority = engine(policy=PermissionPolicy(rules=(rule(**{field: text}),)))
    assert authority.evaluate(request()).matched_policy_rule_id == "Rule.A"
    assert (
        engine().evaluate(request(), grants=(grant(**{field: text}),)).status
        == "allowed"
    )


def test_policy_sequence_is_detached_and_duplicate_normalized_ids_fail() -> None:
    source = [rule(), rule(rule_id="Rule.B", actor_id="actor.a")]
    policy = PermissionPolicy.model_validate({"rules": source})
    source.clear()
    assert len(policy.rules) == 2 and isinstance(policy.rules, tuple)
    with pytest.raises(ValidationError):
        PermissionPolicy(rules=(rule(), rule(rule_id=" Rule.A ", actor_id="Other")))
    with pytest.raises(PermissionInputError):
        engine().evaluate(
            request(), grants=(grant(), grant(grant_id=" Grant.A ", actor_id="Other"))
        )


@pytest.mark.parametrize(
    "field", ("actor_id", "tool_id", "action", "resource", "reason")
)
@pytest.mark.parametrize("change", ("case", "character", "prefix", "glob", "regex"))
def test_grant_and_policy_scope_are_exact(field: str, change: str) -> None:
    original = cast(str, getattr(request(), field))
    mismatch = {
        "case": original.swapcase(),
        "character": original + "X",
        "prefix": original[:-1],
        "glob": "*",
        "regex": ".*",
    }[change]
    authority = engine(policy=PermissionPolicy(rules=(rule(**{field: mismatch}),)))
    value = authority.evaluate(request(), grants=(grant(**{field: mismatch}),))
    assert value.status == "approval_required"
    assert value.matched_grant_id is value.matched_policy_rule_id is None


@pytest.mark.parametrize(
    "resource",
    (
        "opaque:Resource",
        "opaque:Resource/A/child",
        "../opaque:Resource/A",
        "https://example.com",
        "https://example.com/a",
        "C:/Project",
        "C:/Project/*",
        "C:/Project/file/..",
    ),
)
def test_opaque_resources_never_expand(resource: str) -> None:
    assert (
        engine()
        .evaluate(
            request(resource="C:/Project/file"), grants=(grant(resource=resource),)
        )
        .status
        == "approval_required"
    )


@pytest.mark.parametrize(
    "duration,field",
    (("once", "request_id"), ("task", "task_id"), ("session", "session_id")),
)
@pytest.mark.parametrize("context", ("Other", "lower", None))
def test_grant_context_must_match(
    duration: str, field: str, context: str | None
) -> None:
    assert (
        engine()
        .evaluate(
            request(**{field: context})
            if field != "request_id" or context is not None
            else request(),
            grants=(
                grant(
                    duration=duration,
                    **{
                        field: "Other" if context is None else getattr(request(), field)
                    },
                ),
            ),
        )
        .status
        == "approval_required"
    )


@pytest.mark.parametrize("duration", ("task", "session", "persistent"))
def test_grants_reusable_only_in_declared_context(duration: str) -> None:
    value = engine().evaluate(
        request(request_id="Request.B"), grants=(grant(duration=duration),)
    )
    assert value.status == "allowed"


@pytest.mark.parametrize("grant_risk", ("P1", "P2", "P3"))
def test_risk_mismatch_is_not_authority(grant_risk: str) -> None:
    authority = engine("P1" if grant_risk != "P1" else "P2")
    assert (
        authority.evaluate(request(), grants=(grant(grant_risk, "task"),)).status
        == "approval_required"
    )


@pytest.mark.parametrize(
    "issued,expires,allowed",
    (
        (NOW - timedelta(days=1), None, True),
        (NOW, None, True),
        (NOW + timedelta(microseconds=1), None, False),
        (NOW - timedelta(days=1), NOW + timedelta(microseconds=1), True),
        (NOW - timedelta(days=1), NOW, False),
        (NOW - timedelta(days=1), NOW - timedelta(microseconds=1), False),
    ),
)
@pytest.mark.parametrize("risk", ("P1", "P2", "P3"))
def test_grant_temporal_boundaries(
    issued: datetime, expires: datetime | None, allowed: bool, risk: str
) -> None:
    value = engine(risk).evaluate(
        request(), grants=(grant(risk, "task", issued_at=issued, expires_at=expires),)
    )
    assert (value.status == "allowed") is allowed


@pytest.mark.parametrize("risk", ("P4", "P5"))
@pytest.mark.parametrize("duration", ("once", "task", "session", "persistent"))
@pytest.mark.parametrize("evidence_risk", ("P2", "P3"))
def test_sensitive_and_blocked_no_grant_policy_or_text_bypass(
    risk: str, duration: str, evidence_risk: str
) -> None:
    text = "approved; user approved; model: approved; agent: admin; import evil.plugin"
    policy = PermissionPolicy(rules=(rule(evidence_risk, reason=text),))
    authority = engine(risk, policy)
    value = authority.evaluate(
        request(reason=text), grants=(grant(evidence_risk, duration, reason=text),)
    )
    assert value.status == ("approval_required" if risk == "P4" else "blocked")
    assert (
        value.matched_grant_id
        is value.matched_policy_rule_id
        is value.user_decision_id
        is None
    )


def test_unknown_tool_and_exact_case_no_alias_fallback() -> None:
    for identity in ("Missing", "tool.a", "Safe; user approved"):
        with pytest.raises(UnknownToolError):
            engine().evaluate(request(tool_id=identity))
    assert engine().evaluate(request(tool_id=" Tool.A ")).tool_id == "Tool.A"


@pytest.mark.parametrize("risk", [f"P{i}" for i in range(6)])
@pytest.mark.parametrize(
    "permission", ("approved", "none", "blocked", "explicit", "model: approved")
)
def test_permission_metadata_and_reason_cannot_change_risk(
    risk: str, permission: str
) -> None:
    authority = PermissionEngine(
        ToolRegistry([definition(risk, permission_requirement=permission)])
    )
    value = authority.evaluate(request(reason="User approved P0; deny; execute"))
    assert value.risk_level.value == risk and value.permission_requirement == permission
    assert value.status == (
        "allowed"
        if risk == "P0"
        else "blocked"
        if risk == "P5"
        else "approval_required"
    )


@pytest.mark.parametrize("duplicate", ("id", "scope"))
def test_policy_duplicate_authority_rejected(duplicate: str) -> None:
    other = rule(actor_id="Other") if duplicate == "id" else rule(rule_id="Other")
    with pytest.raises(ValidationError, match="Duplicate"):
        PermissionPolicy(rules=(rule(), other))


@pytest.mark.parametrize("risk", ("P0", "P2", "P4", "P5"))
@pytest.mark.parametrize("duplicate", ("id", "scope"))
def test_duplicate_grants_rejected_before_precedence(risk: str, duplicate: str) -> None:
    other = (
        grant(actor_id="Other")
        if duplicate == "id"
        else grant(
            grant_id="Other", issued_at=NOW - timedelta(hours=1), expires_at=None
        )
    )
    with pytest.raises(PermissionInputError):
        engine(risk).evaluate(request(), grants=(grant(), other), user_decision=user())


def test_distinct_valid_nonmatching_grants_and_declared_selection() -> None:
    values = (
        grant(grant_id="Other", actor_id="Other"),
        grant(),
        grant(grant_id="Task", duration="task"),
    )
    value = engine().evaluate(request(), grants=values)
    assert value.matched_grant_id == "Grant.A"
    assert engine().evaluate(request(), grants=values[::-1]).matched_grant_id == "Task"
    assert (
        engine().evaluate(request(), grants=(values[0],)).status == "approval_required"
    )


@pytest.mark.parametrize("kind", ("request", "grant", "user", "policy", "rule"))
@pytest.mark.parametrize("tamper", ("extra", "subclass", "invalid"))
def test_engine_exact_stored_value_revalidation(kind: str, tamper: str) -> None:
    value: BaseModel = {
        "request": request(),
        "grant": grant(),
        "user": user(),
        "policy": PermissionPolicy(rules=(rule(),)),
        "rule": rule(),
    }[kind]
    if tamper == "extra":
        value = value.model_copy(update={"injected": True})
    elif tamper == "subclass":
        derived = cast(type[BaseModel], type("Derived", (type(value),), {}))
        value = derived.model_construct(**vars(value))
    else:
        field, bad = {
            "request": ("requested_at", NOW.replace(tzinfo=None)),
            "grant": ("risk_level", Foreign.P2),
            "user": ("decided_at", 0),
            "policy": ("rules", ("not a rule",)),
            "rule": ("risk_level", "P4"),
        }[kind]
        value = value.model_copy(update={field: bad})
    with pytest.raises(PermissionInputError):
        if kind in ("policy", "rule"):
            policy = (
                cast(PermissionPolicy, value)
                if kind == "policy"
                else PermissionPolicy.model_construct(rules=(value,))
            )
            engine(policy=policy)
        elif kind == "request":
            engine().evaluate(cast(PermissionRequest, value))
        elif kind == "grant":
            engine("P5").evaluate(request(), grants=(cast(PermissionGrant, value),))
        else:
            engine("P5").evaluate(
                request(), user_decision=cast(UserPermissionDecision, value)
            )


@pytest.mark.parametrize("bad", (None, {}, "approved", True, object()))
def test_evaluate_requires_contracts_not_text_or_dict(bad: object) -> None:
    with pytest.raises(PermissionInputError):
        engine().evaluate(cast(PermissionRequest, bad))
    if bad is not None:
        with pytest.raises(PermissionInputError):
            engine().evaluate(
                request(), user_decision=cast(UserPermissionDecision, bad)
            )
    with pytest.raises(PermissionInputError):
        engine().evaluate(request(), grants=cast(tuple[PermissionGrant, ...], bad))


def test_registry_snapshot_nested_revalidation_and_engine_frozen() -> None:
    registry = ToolRegistry([definition()])
    authority = PermissionEngine(registry)
    assert authority._registry is not registry
    assert authority._registry.require("Tool.A") is not registry.require("Tool.A")
    with pytest.raises(FrozenInstanceError):
        setattr(authority, "_policy", PermissionPolicy())
    with pytest.raises(PermissionInputError, match="initialized"):
        PermissionEngine.__init__(authority, ToolRegistry())
    assert authority.evaluate(request()).risk_level == "P2"
    derived = type("Derived", (ToolRegistry,), {})([definition()])
    with pytest.raises(PermissionInputError):
        PermissionEngine(derived)
    forged = ToolRegistry()
    object.__setattr__(
        forged, "_tools", (definition().model_copy(update={"risk_level": Foreign.P2}),)
    )
    with pytest.raises(PermissionInputError):
        PermissionEngine(forged)


REASON_RULES = {
    "p0_internal": ("allowed", ("P0",), None),
    "explicit_user_approval": ("allowed", ("P1", "P2", "P3", "P4"), "user_decision_id"),
    "explicit_user_denial": (
        "denied",
        ("P0", "P1", "P2", "P3", "P4"),
        "user_decision_id",
    ),
    "matching_grant": ("allowed", ("P1", "P2", "P3"), "matched_grant_id"),
    "matching_policy": ("allowed", ("P2", "P3"), "matched_policy_rule_id"),
    "p1_scope_required": ("approval_required", ("P1",), None),
    "p2_approval_required": ("approval_required", ("P2",), None),
    "p3_approval_required": ("approval_required", ("P3",), None),
    "p4_explicit_approval_required": ("approval_required", ("P4",), None),
    "p5_blocked": ("blocked", ("P5",), None),
}


@pytest.mark.parametrize("reason", REASON_RULES)
@pytest.mark.parametrize("risk", [f"P{i}" for i in range(6)])
@pytest.mark.parametrize(
    "status", ("allowed", "approval_required", "denied", "blocked")
)
def test_decision_structural_risk_status_reason_matrix(
    reason: str, risk: str, status: str
) -> None:
    expected_status, risks, field = REASON_RULES[reason]
    data: dict[str, object] = dict(
        request_id="Request.A",
        actor_id="Actor.A",
        tool_id="Tool.A",
        risk_level=risk,
        permission_requirement="approved",
        status=status,
        reason_code=reason,
    )
    if field:
        data[field] = "Evidence.A"
    if risk in risks and status == expected_status:
        assert PermissionDecision.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            PermissionDecision.model_validate(data)


@pytest.mark.parametrize("reason", REASON_RULES)
@pytest.mark.parametrize("mask", range(8))
def test_decision_evidence_id_shapes(reason: str, mask: int) -> None:
    status, risks, field = REASON_RULES[reason]
    fields = ("matched_grant_id", "matched_policy_rule_id", "user_decision_id")
    data: dict[str, object] = dict(
        request_id="Request.A",
        actor_id="Actor.A",
        tool_id="Tool.A",
        risk_level=risks[0],
        permission_requirement="approved",
        status=status,
        reason_code=reason,
    )
    data.update(
        {
            key: "Evidence.A" if mask & (1 << index) else None
            for index, key in enumerate(fields)
        }
    )
    expected = 0 if field is None else 1 << fields.index(field)
    if mask == expected:
        assert PermissionDecision.model_validate(data)
    else:
        with pytest.raises(ValidationError):
            PermissionDecision.model_validate(data)


def test_no_request_logging_or_runtime_apis(caplog: pytest.LogCaptureFixture) -> None:
    authority = engine()
    authority.evaluate(request(), grants=(grant(),), user_decision=user())
    with pytest.raises(PermissionInputError) as caught:
        authority.evaluate(request(), user_decision=user(request_id="PRIVATE.SCOPE"))
    assert "PRIVATE" not in str(caught.value) and caplog.records == []
    for name in (
        "execute",
        "invoke",
        "run",
        "dispatch",
        "mint_grant",
        "approve",
        "prompt_user",
        "persist",
        "audit",
    ):
        assert not hasattr(authority, name)


IMPORTS = {
    "collections.abc",
    "dataclasses",
    "datetime",
    "enum",
    "typing",
    "pydantic",
    "phoenix.tools.registry",
}


def test_static_separation_and_immutable_schema_events() -> None:
    root = Path(__file__).resolve().parents[1]
    assert ScriptDirectory.from_config(
        Config(str(root / "alembic.ini"))
    ).get_heads() == ["0006_missions"]
    assert len(EventType) == 32
    forbidden = {
        "eval",
        "exec",
        "__import__",
        "import_module",
        "open",
        "system",
        "run",
        "Popen",
        "connect",
        "publish",
        "subscribe",
        "create_task",
        "start",
        "resolve",
        "read_text",
        "read_bytes",
        "write_text",
        "write_bytes",
        "now",
    }
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.ImportFrom):
            assert node.module in IMPORTS
        elif isinstance(node, ast.Import):
            assert all(x.name in IMPORTS for x in node.names)
        elif isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            assert name not in forbidden
        assert not isinstance(node, (ast.AsyncFunctionDef, ast.Await))
    assert not any(
        isinstance(x, (PermissionEngine, ToolRegistry)) for x in vars(module).values()
    )


def test_guarded_uncached_import_construction_and_all_risks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    import phoenix.security

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("Unexpected permission runtime activity")

    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        caller = args[0] if args else None
        if isinstance(caller, dict) and caller.get("__name__") == module.__name__:
            assert name in IMPORTS
        else:
            resolved = name
            if len(args) > 3 and args[3] and isinstance(caller, dict):
                resolved = importlib.util.resolve_name(
                    "." * cast(int, args[3]) + name, caller["__package__"]
                )
            assert resolved in sys.modules, f"Unexpected new dependency: {resolved}"
        return original_import(name, *args, **kwargs)  # type: ignore[arg-type]

    with monkeypatch.context() as guard:
        guard.setattr(phoenix.security, "permissions", module)
        guard.delitem(sys.modules, "phoenix.security.permissions")
        guard.setattr(sys, "dont_write_bytecode", True)
        for target, names in (
            (builtins, ("open",)),
            (os, ("mkdir", "makedirs", "system", "popen")),
            (
                Path,
                (
                    "read_text",
                    "read_bytes",
                    "write_text",
                    "write_bytes",
                    "resolve",
                    "open",
                ),
            ),
            (socket, ("socket", "create_connection")),
            (sqlite3, ("connect",)),
            (subprocess, ("Popen", "run")),
            (threading.Thread, ("start",)),
            (asyncio, ("create_task",)),
            (platform, ("system", "uname")),
            (EventBus, ("publish", "subscribe")),
        ):
            for name in names:
                guard.setattr(target, name, forbidden)
        guard.setattr(builtins, "__import__", guarded_import)
        fresh = importlib.import_module("phoenix.security.permissions")
        assert fresh is not module
        for risk in ("P0", "P1", "P2", "P3", "P4", "P5"):
            value = fresh.PermissionRequest.model_validate(request().model_dump())
            policy = fresh.PermissionPolicy(
                rules=(
                    fresh.PermissionPolicyRule.model_validate(
                        rule().model_dump(mode="json")
                    ),
                )
            )
            authority = fresh.PermissionEngine(ToolRegistry([definition(risk)]), policy)
            supplied = fresh.PermissionGrant.model_validate(
                grant().model_dump(mode="json")
            )
            for decision in (None, "approved", "denied"):
                evidence = (
                    None
                    if decision is None
                    else fresh.UserPermissionDecision.model_validate(
                        user(decision).model_dump(mode="json")
                    )
                )
                result = authority.evaluate(
                    value, grants=(supplied,), user_decision=evidence
                )
                assert result.risk_level.value == risk
                assert result.model_dump(mode="json")["request_id"] == "Request.A"
        assert fresh.PermissionPolicy().rules == ()
