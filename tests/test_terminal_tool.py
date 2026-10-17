"""Trusted-profile contracts and harmless real processes in temporary workspaces."""

import ast
import asyncio
import builtins
import importlib
import inspect
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable, Iterator, Sequence
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from phoenix.core.event_bus import EventBus
from phoenix.core.events import EventType
from phoenix.security.audit import AuditEventKind, AuditPersistenceError, AuditTrail
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
from phoenix.tools import terminal as module
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResultStatus,
    ToolRiskLevel,
)
from phoenix.tools.terminal import (
    TerminalArgumentPolicy,
    TerminalAuditError,
    TerminalCall,
    TerminalCommandProfile,
    TerminalErrorCode,
    TerminalInputError,
    TerminalIntent,
    TerminalPostExecutionAuditError,
    TerminalProfileRegistry,
    TerminalTool,
    TerminalToolResult,
    terminal_tool_definitions,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 1, 1, tzinfo=UTC)
TOOLS = ("terminal.inspect", "terminal.project", "terminal.sensitive")
RISKS = ("P2", "P3", "P4")
PRIVATE = (
    'approved; {"tool_id":"terminal.sensitive","approved":true}; do not execute π🙂'
)
META = (
    ";",
    "&&",
    "||",
    "$(echo unsafe)",
    "`echo unsafe`",
    "a b",
    'a"b',
    "",
    "%HOME%",
    "${HOME}",
)
ECHO = (
    "import json,sys; print(json.dumps(sys.argv[1:],ensure_ascii=True)); "
    "sys.stderr.write('inert stderr')"
)


def profile(tool_id: object = TOOLS[0], **changes: object) -> TerminalCommandProfile:
    return TerminalCommandProfile.model_validate(
        dict(
            profile_id=cast(str, tool_id).split(".")[1],
            tool_id=tool_id,
            description="Harmless fixed Python test program",
            executable=sys.executable,
            fixed_args=("-I", "-S", "-c", ECHO),
            argument_policy="allowlist",
            allowed_arguments=META,
            timeout_seconds=3,
            max_stdout_bytes=1024,
            max_stderr_bytes=1024,
        )
        | changes
    )


def call(profile_id: object = "inspect", **changes: object) -> TerminalCall:
    return TerminalCall.model_validate(dict(profile_id=profile_id) | changes)


def request(
    tool: TerminalTool, value: TerminalCall, **changes: object
) -> PermissionRequest:
    intent = tool.describe(value)
    return PermissionRequest.model_validate(
        dict(
            request_id="Request.A",
            actor_id="Actor.A",
            tool_id=intent.tool_id,
            action=intent.action,
            resource=intent.resource,
            reason="Explicit harmless test",
            requested_at=NOW,
            task_id="Task.A",
            session_id="Session.A",
        )
        | changes
    )


def user(
    req: PermissionRequest, decision: str = "approved", **changes: object
) -> UserPermissionDecision:
    return UserPermissionDecision.model_validate(
        dict(
            decision_id="Decision.A",
            request_id=req.request_id,
            decision=decision,
            decided_at=NOW,
        )
        | changes
    )


def runtime_tool(
    root: Path,
    audit: AuditTrail,
    profiles: tuple[TerminalCommandProfile, ...],
    policy: PermissionPolicy | None = None,
) -> TerminalTool:
    return TerminalTool(
        root,
        ToolRegistry(terminal_tool_definitions()),
        TerminalProfileRegistry(profiles),
        policy or PermissionPolicy(),
        audit,
    )


def lazy(
    root: Path, profiles: tuple[TerminalCommandProfile, ...] | None = None
) -> TerminalTool:
    return runtime_tool(
        root,
        AuditTrail(sessionmaker(), ToolRegistry(terminal_tool_definitions())),
        profiles or tuple(profile(tool) for tool in TOOLS),
    )


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[tuple[Path, TerminalTool, AuditTrail, Engine]]:
    root = tmp_path / "workspace"
    root.mkdir()
    db = tmp_path / "audit.db"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db}".replace("%", "%%"))
    command.upgrade(config, "0005_audit_events")
    engine = create_engine(f"sqlite:///{db}")
    audit = AuditTrail(
        sessionmaker(bind=engine), ToolRegistry(terminal_tool_definitions())
    )
    try:
        yield (
            root,
            runtime_tool(root, audit, tuple(profile(tool) for tool in TOOLS)),
            audit,
            engine,
        )
    finally:
        engine.dispose()


def execute(tool: TerminalTool, value: TerminalCall) -> TerminalToolResult:
    req = request(tool, value)
    return tool.execute(value, req, user_decision=user(req))


def forbidden(*args: object, **kwargs: object) -> object:
    raise AssertionError("Unexpected I/O or execution")


def guard_target(guard: pytest.MonkeyPatch) -> None:
    for name in ("resolve", "stat", "lstat", "is_dir", "open", "mkdir", "unlink"):
        guard.setattr(Path, name, forbidden)
    guard.setattr(builtins, "open", forbidden)
    guard.setattr(subprocess, "Popen", forbidden)
    guard.setattr(tempfile, "TemporaryFile", forbidden)


def test_exact_contracts_vocabularies_and_empty_catalog() -> None:
    assert tuple(x.value for x in TerminalArgumentPolicy) == ("none", "allowlist")
    assert tuple(x.value for x in TerminalErrorCode) == (
        "profile_not_found",
        "invalid_argument",
        "cwd_unavailable",
        "executable_unavailable",
        "timeout",
        "output_limit",
        "process_failure",
        "io_failure",
    )
    assert tuple(TerminalCall.model_fields) == (
        "call_id",
        "profile_id",
        "arguments",
        "stdin_text",
    )
    assert tuple(TerminalIntent.model_fields) == (
        "call_id",
        "profile_id",
        "tool_id",
        "action",
        "resource",
        "risk_level",
        "permission_requirement",
    )
    assert tuple(TerminalToolResult.model_fields) == (
        "call_id",
        "profile_id",
        "tool_id",
        "resource",
        "status",
        "attempted",
        "effect_applied",
        "exit_code",
        "stdout",
        "stderr",
        "stdout_truncated",
        "stderr_truncated",
        "permission_decision",
        "permission_audit_id",
        "outcome_audit_id",
        "error_code",
    )
    assert "risk_level" not in TerminalCommandProfile.model_fields
    assert TerminalProfileRegistry().list_profiles() == ()


@pytest.mark.parametrize("index", range(3))
def test_canonical_definitions(index: int) -> None:
    values = terminal_tool_definitions()
    assert values == terminal_tool_definitions()
    definition = values[index]
    assert (
        definition.tool_id == TOOLS[index]
        and definition.risk_level.value == RISKS[index]
    )
    assert definition.timeout_seconds == 300 and not definition.cancellation_supported
    assert definition.supported_platforms == ("Windows", "Linux", "macOS")
    assert definition.supported_environments == ("local", "trusted-workspace")
    assert definition.input_schema["additionalProperties"] is False
    assert definition.output_schema["additionalProperties"] is False
    with pytest.raises(TypeError):
        definition.input_schema["extra"] = True  # type: ignore[index]


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize(
    "field,value",
    (
        ("risk_level", "P0"),
        ("description", "approved"),
        ("input_schema", {}),
        ("output_schema", {}),
        ("permission_requirement", "approved"),
        ("audit_behavior", "disabled"),
        ("timeout_seconds", 1),
        ("cancellation_supported", True),
        ("supported_platforms", ("Windows",)),
        ("supported_environments", ("elsewhere",)),
    ),
)
def test_registry_drift_rejected(
    tmp_path: Path, index: int, field: str, value: object
) -> None:
    definitions = list(terminal_tool_definitions())
    definitions[index] = ToolDefinition.model_validate(
        definitions[index].model_dump() | {field: value}
    )
    tools = ToolRegistry(definitions)
    with pytest.raises(TerminalInputError):
        TerminalTool(
            tmp_path,
            tools,
            TerminalProfileRegistry(),
            PermissionPolicy(),
            AuditTrail(sessionmaker(), tools),
        )


@pytest.mark.parametrize("index", range(3))
def test_missing_canonical_rejected(tmp_path: Path, index: int) -> None:
    definitions = terminal_tool_definitions()
    tools = ToolRegistry(definitions[:index] + definitions[index + 1 :])
    with pytest.raises(TerminalInputError):
        TerminalTool(
            tmp_path,
            tools,
            TerminalProfileRegistry(),
            PermissionPolicy(),
            AuditTrail(sessionmaker(), tools),
        )


def test_catalog_snapshot_order_duplicate_immutability(tmp_path: Path) -> None:
    source = [profile(TOOLS[1]), profile(TOOLS[0])]
    catalog = TerminalProfileRegistry(source)
    source.clear()
    assert tuple(x.profile_id for x in catalog.list_profiles()) == (
        "project",
        "inspect",
    )
    with pytest.raises(TerminalInputError):
        TerminalProfileRegistry((profile(), profile(profile_id=" inspect ")))
    with pytest.raises(FrozenInstanceError):
        catalog._profiles = ()  # type: ignore[misc]
    with pytest.raises(TerminalInputError):
        TerminalProfileRegistry.__init__(catalog)
    with pytest.raises(ValidationError):
        catalog.require("inspect").executable = "different"
    tool = lazy(tmp_path)
    with pytest.raises(FrozenInstanceError):
        tool._root = Path("changed")  # type: ignore[misc]
    with pytest.raises(TerminalInputError):
        TerminalTool.__init__(
            tool, tmp_path, tool._registry, catalog, PermissionPolicy(), tool._audit
        )
    assert not any(
        hasattr(catalog, x) for x in ("register", "replace", "reload", "discover")
    )


@pytest.mark.parametrize(
    "field",
    (
        "executable",
        "risk_level",
        "approved",
        "shell",
        "environment",
        "cwd",
        "timeout_seconds",
        "audit_behavior",
        "handler",
        "command",
        "script",
    ),
)
def test_call_cannot_override_authority(field: str) -> None:
    with pytest.raises(ValueError):
        call(**{field: True})


@pytest.mark.parametrize("bad", (None, True, 1, b"value", "", " "))
@pytest.mark.parametrize("field", ("profile_id", "call_id"))
def test_strict_identity(field: str, bad: object) -> None:
    with pytest.raises(ValueError):
        call(**{field: bad})


@pytest.mark.parametrize(
    "bad",
    (
        None,
        "string",
        {"unordered"},
        [True],
        [1],
        [b"text"],
        ["a\x00b"],
        ["\ud800"],
        ["x" * 4097],
        ["x"] * 65,
    ),
)
def test_strict_argument_contract(bad: object) -> None:
    with pytest.raises(ValueError):
        call(arguments=bad)


@pytest.mark.parametrize(
    "bad",
    (True, 1, b"text", "\ud800", "π" * 32769),
    ids=("bool", "int", "bytes", "surrogate", "oversize"),
)
def test_stdin_contract(bad: object) -> None:
    with pytest.raises(ValueError):
        call(stdin_text=bad)


@pytest.mark.parametrize("bad", (0, -1, True, "1", float("nan"), float("inf"), 301))
def test_profile_timeout_bounded(bad: object) -> None:
    with pytest.raises(ValueError):
        profile(timeout_seconds=bad)


@pytest.mark.parametrize("field", ("max_stdout_bytes", "max_stderr_bytes"))
@pytest.mark.parametrize("bad", (0, -1, True, "1", 1.5, 1048577))
def test_output_budget_contract(field: str, bad: object) -> None:
    with pytest.raises(ValueError):
        profile(**{field: bad})


@pytest.mark.parametrize(
    "bad",
    (
        "python",
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "bash",
        "sh",
        "run.cmd",
        "run.bat",
    ),
)
def test_shell_and_relative_executable_rejected(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        profile(executable=bad)
    if bad != "python":
        with pytest.raises(ValueError):
            profile(executable=str(tmp_path / bad))


@pytest.mark.parametrize(
    "bad",
    (
        "../x",
        "/x",
        "C:/x",
        "C:x",
        "a\\b",
        "a:b",
        "a/../b",
        "a/./b",
        "a//b",
        "a/",
        "CON.txt",
        "com1",
        "nul",
        "x.",
        "x ",
        "a\x00b",
        "\\\\server\\share",
    ),
)
def test_profile_cwd_grammar(bad: str) -> None:
    with pytest.raises(ValueError):
        profile(working_directory=bad)


@pytest.mark.parametrize(
    "bad",
    (
        "PATH",
        "HOME",
        "USERPROFILE",
        "COMSPEC",
        "PYTHONPATH",
        "HTTP_PROXY",
        "API_KEY",
        "LANGUAGE",
        "systemroot",
    ),
)
def test_environment_is_explicit_nonsecret_allowlist(bad: str) -> None:
    with pytest.raises(ValueError):
        profile(inherited_environment=(bad,))


@pytest.mark.parametrize(
    "bad", (None, True, 1, b"x", "relative", "C:relative", "\\rooted")
)
def test_root_explicit_absolute(tmp_path: Path, bad: object) -> None:
    with pytest.raises(TerminalInputError):
        lazy(cast(Path, bad))


def test_call_defaults_normalization_detachment() -> None:
    a, b = call(), call()
    assert UUID(a.call_id).version == 4 and a.call_id != b.call_id
    assert call(call_id=" Case.Call ", profile_id=" inspect ").call_id == "Case.Call"
    source = [";", "&&"]
    value = call(arguments=source)
    source.clear()
    assert value.arguments == (";", "&&")
    assert call(stdin_text="").stdin_text == ""
    assert len(cast(str, call(stdin_text="π" * 32768).stdin_text).encode()) == 65536
    with pytest.raises(ValidationError):
        value.profile_id = "changed"


@pytest.mark.parametrize(
    "variant", ("extra", "stored_extra", "malformed_extra", "subclass", "forged_enum")
)
def test_exact_profiles_revalidated(variant: str) -> None:
    value = profile()
    if variant == "extra":
        value = value.model_copy(update={"approved": True})
    elif variant == "stored_extra":
        object.__setattr__(value, "__pydantic_extra__", {"approved": True})
    elif variant == "malformed_extra":
        object.__setattr__(value, "__pydantic_extra__", ["approved"])
    elif variant == "subclass":

        class Derived(TerminalCommandProfile):
            pass

        value = Derived.model_validate(value.model_dump())
    else:
        forged = str.__new__(TerminalArgumentPolicy, "allowlist")
        object.__setattr__(forged, "_value_", "allowlist")
        object.__setattr__(forged, "_name_", "ALLOWLIST")
        value = value.model_copy(update={"argument_policy": forged})
    with pytest.raises(TerminalInputError):
        TerminalProfileRegistry((value,))


@pytest.mark.parametrize("contract", ("call", "request"))
@pytest.mark.parametrize("variant", ("copy", "extras", "malformed", "subclass"))
def test_exact_call_request_before_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, contract: str, variant: str
) -> None:
    tool = lazy(tmp_path)
    accepted = call("project")
    req = request(tool, accepted)
    value = accepted if contract == "call" else req
    if variant == "copy":
        value = value.model_copy(update={"approved": True})
    elif variant == "extras":
        object.__setattr__(value, "__pydantic_extra__", {"approved": True})
    elif variant == "malformed":
        object.__setattr__(value, "__pydantic_extra__", ["approved"])
    else:

        class DerivedCall(TerminalCall):
            pass

        class DerivedRequest(PermissionRequest):
            pass

        value = (
            DerivedCall.model_validate(value.model_dump())
            if contract == "call"
            else DerivedRequest.model_validate(value.model_dump())
        )
    with monkeypatch.context() as guard:
        guard_target(guard)
        guard.setattr(PermissionEngine, "evaluate", forbidden)
        guard.setattr(sessionmaker, "__call__", forbidden)
        with pytest.raises(TerminalInputError):
            tool.execute(
                cast(TerminalCall, value) if contract == "call" else accepted,
                cast(PermissionRequest, value) if contract == "request" else req,
            )


def test_describe_pure_and_resource_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as guard:
        guard_target(guard)
        guard.setattr(sessionmaker, "__call__", forbidden)
        guard.setattr(PermissionEngine, "evaluate", forbidden)
        tool = lazy(tmp_path / "absent")
        value = call(arguments=(";",))
        intent = tool.describe(value)
        assert intent.action == "execute" and intent.risk_level is ToolRiskLevel.P2
        assert tool.describe(value) == intent
        assert tool.describe(call(arguments=("&&",))).resource != intent.resource
        assert tool.describe(call("project")).resource != intent.resource
        assert json.loads(intent.resource)[:2] == ["inspect", str(tmp_path / "absent")]
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize(
    "variant", ("missing", "argument", "stdin", "none_policy", "over_budget")
)
def test_invalid_profile_input_before_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    tool = lazy(tmp_path)
    value = call(
        profile_id="missing" if variant == "missing" else "inspect",
        arguments=("unlisted",) if variant == "argument" else (),
        stdin_text="input" if variant == "stdin" else None,
    )
    if variant == "none_policy":
        tool = lazy(tmp_path, (profile(argument_policy="none", allowed_arguments=()),))
        value = call(arguments=(";",))
    elif variant == "over_budget":
        text_arg = "a" * 4090
        tool = lazy(tmp_path, (profile(allowed_arguments=(text_arg,)),))
        value = call(arguments=(text_arg,) * 4)
    with monkeypatch.context() as guard:
        guard_target(guard)
        guard.setattr(PermissionEngine, "evaluate", forbidden)
        with pytest.raises(TerminalInputError) as caught:
            tool.describe(value)
    assert caught.value.error_code is (
        TerminalErrorCode.PROFILE_NOT_FOUND
        if variant == "missing"
        else TerminalErrorCode.INVALID_ARGUMENT
    )


@pytest.mark.parametrize("index", range(3))
@pytest.mark.parametrize("decision", (None, "denied"))
def test_nonallowed_no_target_io(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    monkeypatch: pytest.MonkeyPatch,
    index: int,
    decision: str | None,
) -> None:
    _, tool, audit, _ = runtime
    accepted = call(TOOLS[index].split(".")[1])
    req = request(tool, accepted)
    with monkeypatch.context() as guard:
        guard_target(guard)
        result = tool.execute(
            accepted, req, user_decision=user(req, decision) if decision else None
        )
    assert (
        result.status is ToolResultStatus.DENIED
        and not result.attempted
        and not result.effect_applied
    )
    assert result.stdout == result.stderr == "" and result.exit_code is None
    assert result.permission_decision.status is (
        PermissionStatus.DENIED if decision else PermissionStatus.APPROVAL_REQUIRED
    )
    rows = audit.list_records()
    assert len(rows) == (0 if index == 0 else 1)
    assert all(x.event_kind is AuditEventKind.PERMISSION_DECISION for x in rows)


@pytest.mark.parametrize("field", ("tool_id", "action", "resource"))
def test_request_correlation_zero_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    tool = lazy(tmp_path)
    value = call("sensitive")
    req = request(tool, value, **{field: "other"})
    with monkeypatch.context() as guard:
        guard_target(guard)
        guard.setattr(PermissionEngine, "evaluate", forbidden)
        with pytest.raises(TerminalInputError):
            tool.execute(value, req, user_decision=user(req))


@pytest.mark.parametrize(
    "variant", ("profile", "arguments", "root", "configuration", "stdin")
)
def test_approval_cannot_change_invocation(tmp_path: Path, variant: str) -> None:
    base = profile(stdin_allowed=True)
    tool = lazy(tmp_path, (base, profile(TOOLS[1])))
    value = call(stdin_text="old", arguments=(";",))
    req = request(tool, value)
    if variant == "profile":
        value = call("project")
    elif variant == "arguments":
        value = call(stdin_text="old", arguments=("&&",))
    elif variant == "stdin":
        value = call(stdin_text="new", arguments=(";",))
    elif variant == "root":
        tool = lazy(tmp_path / "other", (base,))
    else:
        tool = lazy(tmp_path, (profile(stdin_allowed=True, fixed_args=("--version",)),))
    with pytest.raises(TerminalInputError):
        tool.execute(value, req, user_decision=user(req))


@pytest.mark.parametrize("index", range(3))
def test_permission_once_real_execution(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    monkeypatch: pytest.MonkeyPatch,
    index: int,
) -> None:
    _, tool, _, _ = runtime
    original = PermissionEngine.evaluate
    seen: list[str] = []

    def evaluate(
        self: PermissionEngine, req: PermissionRequest, **kwargs: object
    ) -> PermissionDecision:
        seen.append(req.request_id)
        return original(self, req, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(PermissionEngine, "evaluate", evaluate)
    result = execute(tool, call(TOOLS[index].split(".")[1], arguments=META))
    assert seen == ["Request.A"] and result.status is ToolResultStatus.SUCCESS
    assert json.loads(result.stdout) == list(META) and result.stderr == "inert stderr"
    assert result.effect_applied == (index != 0)


@pytest.mark.parametrize("index", (0, 1))
@pytest.mark.parametrize("duration", ("once", "task", "session", "persistent"))
def test_grants_and_p3_scope(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine], index: int, duration: str
) -> None:
    _, tool, _, _ = runtime
    value = call(TOOLS[index].split(".")[1])
    req = request(tool, value)
    data: dict[str, object] = dict(
        grant_id="Grant.A",
        actor_id=req.actor_id,
        tool_id=req.tool_id,
        action=req.action,
        resource=req.resource,
        reason=req.reason,
        risk_level=RISKS[index],
        duration=duration,
        issued_at=NOW - timedelta(days=1),
        user_decision="approved",
    )
    key = {"once": "request_id", "task": "task_id", "session": "session_id"}.get(
        duration
    )
    if key:
        data[key] = getattr(req, key)
    result = tool.execute(value, req, grants=(PermissionGrant.model_validate(data),))
    assert result.attempted == (index == 0 or duration in ("task", "session"))


@pytest.mark.parametrize("index", (0, 1, 2))
def test_policy_and_p4_bypass(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine], index: int
) -> None:
    root, original, audit, _ = runtime
    value = call(TOOLS[index].split(".")[1])
    req = request(original, value)
    data = dict(
        rule_id="Rule.A",
        actor_id=req.actor_id,
        tool_id=req.tool_id,
        action=req.action,
        resource=req.resource,
        reason=req.reason,
        risk_level=RISKS[index],
    )
    if index == 2:
        with pytest.raises(ValueError):
            PermissionPolicyRule.model_validate(data)
        data["risk_level"] = "P3"
    tool = runtime_tool(
        root,
        audit,
        tuple(profile(x) for x in TOOLS),
        PermissionPolicy(rules=(PermissionPolicyRule.model_validate(data),)),
    )
    result = tool.execute(value, req)
    assert result.attempted == (index != 2)
    if index != 2:
        assert result.permission_decision.reason_code is DecisionReason.MATCHING_POLICY
    else:
        with pytest.raises(TerminalInputError):
            tool.execute(value, req, user_decision=user(req, request_id="other"))


@pytest.mark.parametrize("duration", ("once", "task", "session", "persistent"))
def test_p4_grants_never_allow(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine], duration: str
) -> None:
    _, tool, _, _ = runtime
    value = call("sensitive")
    req = request(tool, value)
    data: dict[str, object] = dict(
        grant_id="g",
        actor_id=req.actor_id,
        tool_id=req.tool_id,
        action=req.action,
        resource=req.resource,
        reason=req.reason,
        risk_level="P4",
        duration=duration,
        issued_at=NOW,
        user_decision="approved",
    )
    key = {"once": "request_id", "task": "task_id", "session": "session_id"}.get(
        duration
    )
    if key:
        data[key] = getattr(req, key)
    with pytest.raises(ValueError):
        PermissionGrant.model_validate(data)
    data["risk_level"] = "P3"
    assert (
        tool.execute(value, req, grants=(PermissionGrant.model_validate(data),)).status
        is ToolResultStatus.DENIED
    )


@pytest.mark.parametrize("index", (1, 2))
def test_durable_pre_audit_before_every_target_action(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    monkeypatch: pytest.MonkeyPatch,
    index: int,
) -> None:
    _, tool, audit, engine = runtime
    original = module._cwd
    observed: list[str] = []

    def checked(root: Path, relative: str) -> Path:
        with engine.connect() as connection:
            rows: Sequence[str] = (
                connection.execute(text("SELECT event_kind FROM audit_events"))
                .scalars()
                .all()
            )
        assert rows == ["permission_decision"]
        observed.extend(rows)
        return original(root, relative)

    monkeypatch.setattr(module, "_cwd", checked)
    result = execute(tool, call(TOOLS[index].split(".")[1]))
    assert (
        observed == ["permission_decision"]
        and result.status is ToolResultStatus.SUCCESS
    )
    rows = audit.list_records()
    assert len(rows) == 2 and {x.event_kind for x in rows} == {
        AuditEventKind.PERMISSION_DECISION,
        AuditEventKind.TOOL_OUTCOME,
    }
    assert all(x.correlation_id == result.call_id for x in rows)
    assert result.permission_audit_id != result.outcome_audit_id


@pytest.mark.parametrize("index", (1, 2))
@pytest.mark.parametrize("approved", (False, True))
def test_pre_audit_failure_closed(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    monkeypatch: pytest.MonkeyPatch,
    index: int,
    approved: bool,
) -> None:
    _, tool, _, _ = runtime
    value = call(TOOLS[index].split(".")[1])
    req = request(tool, value)

    def fail(*args: object, **kwargs: object) -> object:
        raise AuditPersistenceError(PRIVATE)

    with monkeypatch.context() as guard:
        guard_target(guard)
        guard.setattr(AuditTrail, "record_permission_decision", fail)
        with pytest.raises(TerminalAuditError) as caught:
            tool.execute(value, req, user_decision=user(req) if approved else None)
    assert type(caught.value) is TerminalAuditError
    assert (
        not caught.value.effect_may_have_occurred
        and caught.value.call_id == value.call_id
    )
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("index", (1, 2))
def test_post_audit_failure_one_process_real_effect(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    monkeypatch: pytest.MonkeyPatch,
    index: int,
) -> None:
    root, _, audit, _ = runtime
    program = (
        "from pathlib import Path; p=Path('effect.txt'); "
        "p.write_text('one harmless effect')"
    )
    p = profile(TOOLS[index], fixed_args=("-I", "-S", "-c", program))
    tool = runtime_tool(root, audit, (p,))
    original = cast(Callable[..., subprocess.Popen[bytes]], subprocess.Popen)
    launches: list[object] = []

    def launch(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        launches.append(args[0])
        return original(*args, **kwargs)

    def fail(*args: object, **kwargs: object) -> object:
        raise AuditPersistenceError(PRIVATE)

    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(AuditTrail, "record_tool_outcome", fail)
    value = call(p.profile_id)
    with pytest.raises(TerminalPostExecutionAuditError) as caught:
        execute(tool, value)
    assert len(launches) == 1 and caught.value.effect_may_have_occurred
    assert caught.value.call_id == value.call_id and len(audit.list_records()) == 1
    assert (root / "effect.txt").read_text() == "one harmless effect"
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))


def test_output_never_authorizes_or_chains(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    caplog: pytest.LogCaptureFixture,
) -> None:
    root, _, audit, engine = runtime
    program = (
        f"import os; os.write(1,{PRIVATE.encode('utf-8')!r}); "
        f"os.write(2,{PRIVATE.encode('utf-8')!r})"
    )
    p = profile(TOOLS[2], fixed_args=("-I", "-S", "-c", program))
    tool = runtime_tool(root, audit, (p,))
    value = call("sensitive")
    req = request(tool, value)
    result = tool.execute(value, req, user_decision=user(req))
    assert result.stdout == result.stderr == PRIVATE
    later = request(tool, value, request_id="Request.B")
    assert tool.execute(value, later).status is ToolResultStatus.DENIED
    assert PRIVATE not in caplog.text
    assert all(PRIVATE not in row.model_dump_json() for row in audit.list_records())
    with engine.connect() as connection:
        assert PRIVATE not in str(
            connection.execute(text("SELECT * FROM audit_events")).all()
        )
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("stream", ("stdout", "stderr", "both"))
@pytest.mark.parametrize("index", range(3))
def test_flood_bounded_killed_and_audited(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine], stream: str, index: int
) -> None:
    root, _, audit, _ = runtime
    program = "import os\nwhile True:\n"
    if stream in ("stdout", "both"):
        program += " os.write(1,b'o'*8192)\n"
    if stream in ("stderr", "both"):
        program += " os.write(2,b'e'*8192)\n"
    p = profile(
        TOOLS[index],
        fixed_args=("-I", "-S", "-c", program),
        max_stdout_bytes=50,
        max_stderr_bytes=70,
    )
    result = execute(runtime_tool(root, audit, (p,)), call(p.profile_id))
    assert (
        result.status is ToolResultStatus.FAILURE
        and result.error_code is TerminalErrorCode.OUTPUT_LIMIT
    )
    assert len(result.stdout.encode()) <= 50 and len(result.stderr.encode()) <= 70
    assert (
        result.stdout_truncated
        and result.stderr_truncated
        and result.exit_code is not None
    )
    if index:
        assert any(
            x.tool_result_status is ToolResultStatus.FAILURE
            for x in audit.list_records()
        )


@pytest.mark.parametrize("index", range(3))
def test_timeout_kills_child_preserves_effect_and_audit(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    monkeypatch: pytest.MonkeyPatch,
    index: int,
) -> None:
    root, _, audit, _ = runtime
    program = (
        "import os,time; os.write(1,b'before'); os.write(2,b'error'); time.sleep(10)"
    )
    p = profile(
        TOOLS[index], fixed_args=("-I", "-S", "-c", program), timeout_seconds=0.3
    )
    original = cast(Callable[..., subprocess.Popen[bytes]], subprocess.Popen)
    children: list[subprocess.Popen[bytes]] = []

    def launch(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", launch)
    started = time.monotonic()
    result = execute(runtime_tool(root, audit, (p,)), call(p.profile_id))
    assert time.monotonic() - started < 3
    assert (
        result.status is ToolResultStatus.TIMEOUT
        and result.error_code is TerminalErrorCode.TIMEOUT
    )
    assert result.stdout == "before" and result.stderr == "error"
    assert result.stdout_truncated and result.stderr_truncated
    assert len(children) == 1 and children[0].poll() is not None
    assert result.effect_applied == (index != 0)
    if index:
        assert any(
            x.tool_result_status is ToolResultStatus.TIMEOUT
            for x in audit.list_records()
        )


@pytest.mark.parametrize("exit_code", (0, 7))
@pytest.mark.parametrize("index", range(3))
def test_exit_code_and_effect_evidence(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine], index: int, exit_code: int
) -> None:
    root, _, audit, _ = runtime
    p = profile(
        TOOLS[index],
        fixed_args=("-I", "-S", "-c", f"import sys; sys.exit({exit_code})"),
    )
    result = execute(runtime_tool(root, audit, (p,)), call(p.profile_id))
    assert result.exit_code == exit_code and result.status is (
        ToolResultStatus.SUCCESS if exit_code == 0 else ToolResultStatus.FAILURE
    )
    assert result.error_code is (
        None if exit_code == 0 else TerminalErrorCode.PROCESS_FAILURE
    )
    assert result.effect_applied == (index != 0)


@pytest.mark.parametrize(
    "raw,expected",
    (
        (b"", ""),
        (b"a\r\nb\n", "a\r\nb\n"),
        (b"\xff\xfe", "\ufffd\ufffd"),
        ("π🙂".encode(), "π🙂"),
    ),
)
def test_decoding_exact_newlines_or_utf8_replacement(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine], raw: bytes, expected: str
) -> None:
    root, _, audit, _ = runtime
    p = profile(fixed_args=("-I", "-S", "-c", f"import os; os.write(1,{raw!r})"))
    result = execute(runtime_tool(root, audit, (p,)), call())
    assert result.stdout == expected and result.status is ToolResultStatus.SUCCESS


def test_exact_output_boundary_no_false_truncation(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
) -> None:
    root, _, audit, _ = runtime
    p = profile(
        fixed_args=(
            "-I",
            "-S",
            "-c",
            "import os; os.write(1,b'a'*50); os.write(2,b'b'*70)",
        ),
        max_stdout_bytes=50,
        max_stderr_bytes=70,
    )
    result = execute(runtime_tool(root, audit, (p,)), call())
    assert result.status is ToolResultStatus.SUCCESS
    assert result.stdout == "a" * 50 and result.stderr == "b" * 70
    assert not result.stdout_truncated and not result.stderr_truncated


@pytest.mark.parametrize("input_text", (None, "", "π\r\n" * 1000))
def test_bounded_stdin_and_no_threads(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    monkeypatch: pytest.MonkeyPatch,
    input_text: str | None,
) -> None:
    root, _, audit, _ = runtime
    p = profile(
        fixed_args=(
            "-I",
            "-S",
            "-c",
            "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())",
        ),
        stdin_allowed=True,
        max_stdout_bytes=65536,
    )
    with monkeypatch.context() as guard:
        guard.setattr(threading.Thread, "start", forbidden)
        guard.setattr(subprocess.Popen, "communicate", forbidden)
        result = execute(runtime_tool(root, audit, (p,)), call(stdin_text=input_text))
    assert (
        result.stdout == (input_text or "")
        and result.status is ToolResultStatus.SUCCESS
    )
    assert list(root.iterdir()) == []


def test_unconsumed_max_stdin_does_not_block(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
) -> None:
    root, _, audit, _ = runtime
    p = profile(stdin_allowed=True, fixed_args=("-I", "-S", "-c", "pass"))
    result = execute(runtime_tool(root, audit, (p,)), call(stdin_text="x" * 65536))
    assert result.status is ToolResultStatus.SUCCESS


@pytest.mark.parametrize("index", range(3))
def test_unavailable_executable_safe(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine], index: int
) -> None:
    root, _, audit, _ = runtime
    p = profile(TOOLS[index], executable=str(root.parent / "missing-executable.exe"))
    result = execute(runtime_tool(root, audit, (p,)), call(p.profile_id))
    assert result.status is ToolResultStatus.UNAVAILABLE_DEPENDENCY
    assert result.error_code is TerminalErrorCode.EXECUTABLE_UNAVAILABLE
    assert result.attempted and not result.effect_applied and result.exit_code is None


@pytest.mark.parametrize("variant", ("missing", "file", "reparse", "outside"))
def test_cwd_rejects_missing_nondirectory_and_escapes(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
) -> None:
    root, _, audit, _ = runtime
    target = root / "subdir"
    if variant == "file":
        target.write_text("preserved")
    elif variant != "missing":
        target.mkdir()
    p = profile(TOOLS[1], working_directory="subdir")
    original_lstat = Path.lstat
    original_resolve = cast(Callable[..., Path], Path.resolve)
    if variant == "reparse":

        def reparse(self: Path) -> os.stat_result:
            return (
                cast(
                    os.stat_result,
                    SimpleNamespace(
                        st_mode=stat.S_IFDIR,
                        st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
                    ),
                )
                if self == target
                else original_lstat(self)
            )

        monkeypatch.setattr(Path, "lstat", reparse)
    elif variant == "outside":

        def outside(self: Path, *args: object, **kwargs: object) -> Path:
            return (
                root.parent
                if self == target
                else original_resolve(self, *args, **kwargs)
            )

        monkeypatch.setattr(Path, "resolve", outside)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = execute(runtime_tool(root, audit, (p,)), call("project"))
    assert (
        result.error_code is TerminalErrorCode.CWD_UNAVAILABLE
        and not result.effect_applied
    )
    assert len(audit.list_records()) == 2


def test_fixed_cwd_and_environment_no_ambient_credentials(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, audit, _ = runtime
    (root / "subdir").mkdir()
    monkeypatch.setenv("PHOENIX_TEST_PRIVATE", PRIVATE)
    monkeypatch.setenv("PYTHONPATH", PRIVATE)
    program = (
        "import os,json; print(json.dumps([os.getcwd(),"
        "os.environ.get('PHOENIX_TEST_PRIVATE'),os.environ.get('PYTHONPATH')]))"
    )
    p = profile(working_directory="subdir", fixed_args=("-I", "-S", "-c", program))
    original = cast(Callable[..., subprocess.Popen[bytes]], subprocess.Popen)
    observed: list[dict[str, object]] = []

    def launch(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        assert isinstance(args[0], list) and kwargs["shell"] is False
        assert set(cast(dict[str, str], kwargs["env"])) <= {"SystemRoot"}
        observed.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)
    result = execute(runtime_tool(root, audit, (p,)), call())
    assert json.loads(result.stdout) == [str(root / "subdir"), None, None]
    assert len(observed) == 1


@pytest.mark.parametrize("where", ("launch", "reader"))
@pytest.mark.parametrize("error", (OSError, FileNotFoundError))
def test_raw_errors_private_and_audited(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    where: str,
    error: type[OSError],
) -> None:
    _, tool, audit, _ = runtime

    def fail(*args: object, **kwargs: object) -> object:
        raise error(PRIVATE)

    monkeypatch.setattr(
        subprocess if where == "launch" else module,
        "Popen" if where == "launch" else "_reader",
        fail,
    )
    result = execute(tool, call("project"))
    assert result.error_code is (
        TerminalErrorCode.EXECUTABLE_UNAVAILABLE
        if where == "launch" and error is FileNotFoundError
        else TerminalErrorCode.IO_FAILURE
    )
    assert result.effect_applied == (where == "reader")
    assert PRIVATE not in result.model_dump_json() and PRIVATE not in caplog.text
    assert len(audit.list_records()) == 2


@pytest.mark.skipif(os.name != "nt", reason="Real NTFS junction requires Windows")
def test_real_junction_cwd_escape_zero_launch(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, audit, _ = runtime
    outside = root.parent / "outside"
    outside.mkdir()
    # Reuse the existing test-only native helper without changing mypy's module map.
    create_junction = cast(
        Callable[[Path, Path], None],
        importlib.import_module("tests.test_filesystem_tool").junction,
    )
    create_junction(outside, root / "link")
    p = profile(TOOLS[1], working_directory="link")
    tool = runtime_tool(root, audit, (p,))
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = execute(tool, call("project"))
    assert result.error_code is TerminalErrorCode.CWD_UNAVAILABLE
    assert result.attempted and not result.effect_applied
    assert len(audit.list_records()) == 2 and list(outside.iterdir()) == []


def test_stdin_file_failure_is_safe_io_failure(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, audit, _ = runtime
    tool = runtime_tool(root, audit, (profile(TOOLS[1], stdin_allowed=True),))

    def fail(*args: object, **kwargs: object) -> object:
        raise FileNotFoundError(PRIVATE)

    monkeypatch.setattr(tempfile, "TemporaryFile", fail)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = execute(tool, call("project", stdin_text="input"))
    assert result.error_code is TerminalErrorCode.IO_FAILURE
    assert result.attempted and not result.effect_applied
    assert PRIVATE not in result.model_dump_json() and len(audit.list_records()) == 2


def test_missing_database_fails_before_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'absent' / 'audit.db'}")
    audit = AuditTrail(
        sessionmaker(bind=engine), ToolRegistry(terminal_tool_definitions())
    )
    try:
        tool = runtime_tool(tmp_path, audit, (profile(TOOLS[1]),))
        monkeypatch.setattr(subprocess, "Popen", forbidden)
        with pytest.raises(TerminalAuditError):
            execute(tool, call("project"))
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "change",
    (
        {"status": "success"},
        {"attempted": True},
        {"effect_applied": True},
        {"exit_code": 0},
        {"stdout": PRIVATE},
        {"stderr": PRIVATE},
        {"stdout_truncated": True},
        {"outcome_audit_id": "fake"},
        {"error_code": "io_failure"},
        {"extra": True},
        {"attempted": 1},
    ),
)
def test_denied_result_contradictions(
    tmp_path: Path, change: dict[str, object]
) -> None:
    tool = lazy(tmp_path)
    value = call()
    result = tool.execute(value, request(tool, value))
    data = result.model_dump(mode="python")
    data["permission_decision"] = result.permission_decision
    with pytest.raises(ValueError):
        TerminalToolResult.model_validate(data | change)


@pytest.mark.parametrize(
    "change",
    (
        {"exit_code": 1},
        {"stdout_truncated": True},
        {"attempted": False},
        {"effect_applied": False},
        {"permission_audit_id": None},
        {"outcome_audit_id": None},
        {"error_code": "process_failure"},
        {"status": "cancelled"},
        {"status": "partial_success"},
        {"status": "timeout"},
        {"permission_audit_id": "same", "outcome_audit_id": "same"},
        {"exit_code": True},
        {"stdout": b"bytes"},
    ),
)
def test_success_result_contradictions(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine], change: dict[str, object]
) -> None:
    _, tool, _, _ = runtime
    result = execute(tool, call("project"))
    data = result.model_dump(mode="python")
    data["permission_decision"] = result.permission_decision
    with pytest.raises(ValueError):
        TerminalToolResult.model_validate(data | change)


@pytest.mark.parametrize("error", ("timeout", "output_limit"))
@pytest.mark.parametrize("index", (1, 2))
def test_interrupted_result_cannot_discard_possible_effect(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine], error: str, index: int
) -> None:
    root, _, audit, _ = runtime
    p = profile(TOOLS[index])
    result = execute(runtime_tool(root, audit, (p,)), call(p.profile_id))
    data = result.model_dump(mode="python") | {
        "permission_decision": result.permission_decision,
        "status": "timeout" if error == "timeout" else "failure",
        "error_code": error,
        "stdout_truncated": True,
        "stderr_truncated": True,
    }
    assert TerminalToolResult.model_validate(data).effect_applied
    with pytest.raises(ValueError):
        TerminalToolResult.model_validate(data | {"effect_applied": False})


def test_result_frozen_detached_and_roundtrip(
    runtime: tuple[Path, TerminalTool, AuditTrail, Engine],
) -> None:
    _, tool, _, _ = runtime
    result = execute(tool, call("project"))
    assert TerminalToolResult.model_validate(result) == result
    with pytest.raises(ValidationError):
        result.stdout = "changed"
    data = result.model_dump(mode="python")
    data["permission_decision"] = PermissionDecision.model_validate(
        data["permission_decision"]
    )
    assert TerminalToolResult.model_validate(data) == result
    data["permission_decision"] = result.permission_decision.model_copy(
        update={"tool_id": "changed"}
    )
    assert result.permission_decision.tool_id == "terminal.project"
    decoded = json.loads(result.model_dump_json())
    decoded["permission_decision"] = PermissionDecision.model_validate(
        decoded["permission_decision"]
    )
    assert TerminalToolResult.model_validate(decoded) == result


def test_static_security_and_scope() -> None:
    source = inspect.getsource(module)
    tree = ast.parse(source)
    imports = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert {x for x in imports if x and x.startswith("phoenix.")} == {
        "phoenix.security.audit",
        "phoenix.security.permissions",
        "phoenix.tools.registry",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in ("eval", "exec", "__import__", "compile")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in (
                "communicate",
                "run",
                "system",
                "popen",
                "publish",
                "subscribe",
                "import_module",
            )
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    assert (
                        isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is False
                    )
    assert len(EventType) == 32
    assert ScriptDirectory.from_config(
        Config(str(ROOT / "alembic.ini"))
    ).get_heads() == ["0007_app_settings"]
    assert tuple(x for x in vars(TerminalTool) if not x.startswith("_")) == (
        "describe",
        "execute",
    )


def test_guarded_uncached_module_and_contracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import phoenix.tools

    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        resolved = name
        caller = args[0] if args else None
        if len(args) > 3 and args[3] and isinstance(caller, dict):
            resolved = importlib.util.resolve_name(
                "." * cast(int, args[3]) + name, caller["__package__"]
            )
        assert resolved in sys.modules, f"Unexpected uncached subsystem: {resolved}"
        return original_import(name, *args, **kwargs)  # type: ignore[arg-type]

    with monkeypatch.context() as guard:
        guard.setattr(phoenix.tools, "terminal", module)
        guard.delitem(sys.modules, "phoenix.tools.terminal")
        guard.setattr(sys, "dont_write_bytecode", True)
        guard_target(guard)
        guard.setattr(builtins, "__import__", guarded_import)
        for target, names in (
            (socket, ("socket", "create_connection")),
            (sqlite3, ("connect",)),
            (threading.Thread, ("start",)),
            (asyncio, ("create_task",)),
            (sessionmaker, ("__call__",)),
            (Engine, ("connect",)),
            (command, ("upgrade",)),
            (EventBus, ("publish", "subscribe")),
        ):
            for name in names:
                guard.setattr(target, name, forbidden)
        fresh = importlib.import_module("phoenix.tools.terminal")
        assert fresh is not module
        p = fresh.TerminalCommandProfile(
            profile_id="test",
            tool_id="terminal.inspect",
            description="harmless",
            executable=sys.executable,
        )
        tools = ToolRegistry(fresh.terminal_tool_definitions())
        audit = AuditTrail(sessionmaker(), tools)
        tool = fresh.TerminalTool(
            tmp_path / "absent",
            tools,
            fresh.TerminalProfileRegistry((p,)),
            PermissionPolicy(),
            audit,
        )
        value = fresh.TerminalCall(profile_id="test")
        intent = tool.describe(value)
        req = PermissionRequest(
            request_id="r",
            actor_id="a",
            tool_id=intent.tool_id,
            action=intent.action,
            resource=intent.resource,
            reason="test",
            requested_at=NOW,
        )
        result = tool.execute(value, req)
        assert result.status is ToolResultStatus.DENIED and not result.attempted
    assert not (tmp_path / "absent").exists()
