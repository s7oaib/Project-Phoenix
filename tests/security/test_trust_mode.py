"""Trust mode (ADR-305 to ADR-309): default off, scope, P1-P4 handling, kill switch."""

import os
import sys
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from phoenix.api.routes.permissions import router
from phoenix.security.permissions import (
    DecisionReason,
    PermissionEngine,
    PermissionRequest,
    PermissionStatus,
)
from phoenix.security.trust import TRUST, never_auto, recycle
from phoenix.tools.registry import ToolDefinition, ToolRegistry

NOW = datetime(2026, 11, 5, tzinfo=UTC)
REAL_SYSTEM_ROOT = os.environ.get("SystemRoot", "")


def _tool(tool_id: str, risk: str) -> ToolDefinition:
    return ToolDefinition.model_validate(
        dict(
            tool_id=tool_id,
            description="t",
            input_schema={},
            output_schema={},
            risk_level=risk,
            permission_requirement="approved",
            timeout_seconds=5,
            cancellation_supported=True,
            audit_behavior="disabled",
            supported_platforms=["Windows"],
            supported_environments=["desktop"],
        )
    )


ENGINE = PermissionEngine(
    ToolRegistry(
        [
            _tool("t.p1", "P1"),
            _tool("t.p2", "P2"),
            _tool("t.p3", "P3"),
            _tool("t.p4", "P4"),
            _tool("t.p5", "P5"),
            _tool("pay.p4", "P4"),
        ]
    )
)


def req(tool="t.p1", resource="https://example.com", task="task1", rid="r1", action="act"):
    return PermissionRequest(
        request_id=rid,
        actor_id="a",
        tool_id=tool,
        action=action,
        resource=resource,
        reason="because",
        requested_at=NOW,
        task_id=task,
    )


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "Users" / "me"
    for d in ("Desktop", "Documents", "Downloads", "AppData", ".ssh"):
        (h / d).mkdir(parents=True)
    (tmp_path / "Users" / "other").mkdir()
    monkeypatch.setenv("USERPROFILE", str(h))
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    TRUST.workspace_root = None
    TRUST.set(False)
    yield h
    TRUST.set(False)
    TRUST.workspace_root = None


def status(r):
    return ENGINE.evaluate(r).status


def test_default_off_and_prompts():
    assert TRUST.enabled is False and TRUST.snapshot()["since"] is None
    for t in ("t.p1", "t.p2", "t.p3", "t.p4"):
        assert status(req(t)) == PermissionStatus.APPROVAL_REQUIRED


def test_on_off_and_snapshot():
    TRUST.set(True)
    snap = TRUST.snapshot()
    assert snap["enabled"] and snap["since"] and snap["allow"] and snap["deny"]
    TRUST.set(False)
    assert not TRUST.enabled and TRUST.snapshot()["since"] is None


@pytest.mark.parametrize("tool", ["t.p1", "t.p2", "t.p3"])
def test_p1_p3_auto_as_trust_grant(tool):
    TRUST.set(True)
    d = ENGINE.evaluate(req(tool))
    assert d.status == PermissionStatus.ALLOWED
    assert d.reason_code == DecisionReason.MATCHING_GRANT
    assert d.matched_grant_id in TRUST.grant_ids


def test_p3_without_task_or_session_still_prompts():
    TRUST.set(True)
    assert status(req("t.p3", task=None)) == PermissionStatus.APPROVAL_REQUIRED


def test_p4_once_per_task():
    TRUST.set(True)
    assert status(req("t.p4")) == PermissionStatus.APPROVAL_REQUIRED
    TRUST.approve_task("task1", "trust-approval-1")
    d = ENGINE.evaluate(req("t.p4", rid="r2"))
    assert d.status == PermissionStatus.ALLOWED and d.user_decision_id == "trust-approval-1"
    assert status(req("t.p4", task="task2")) == PermissionStatus.APPROVAL_REQUIRED


def test_p5_never():
    TRUST.set(True)
    TRUST.approve_task("task1", "x")
    assert status(req("t.p5")) == PermissionStatus.BLOCKED


def test_payments_and_send_never_auto():
    TRUST.set(True)
    TRUST.approve_task("task1", "x")
    assert status(req("pay.p4")) == PermissionStatus.APPROVAL_REQUIRED
    assert status(req("t.p4", action="send_email")) == PermissionStatus.APPROVAL_REQUIRED
    assert status(req("t.p1", action="send_message")) == PermissionStatus.APPROVAL_REQUIRED
    assert never_auto("browser.click", "purchase") and not never_auto("t.p4", "delete_file")


def test_minted_grant_dies_with_kill_switch():
    TRUST.set(True)
    d = ENGINE.evaluate(req("t.p1"))
    gid = d.matched_grant_id
    TRUST.set(False)
    assert gid not in TRUST.grant_ids
    assert status(req("t.p1", rid="r9")) == PermissionStatus.APPROVAL_REQUIRED


def test_kill_switch_mid_task():
    TRUST.set(True)
    TRUST.approve_task("task1", "x")
    assert status(req("t.p4")) == PermissionStatus.ALLOWED
    assert status(req("t.p2", rid="r5")) == PermissionStatus.ALLOWED
    TRUST.set(False)
    assert status(req("t.p4", rid="r6")) == PermissionStatus.APPROVAL_REQUIRED
    assert status(req("t.p2", rid="r7")) == PermissionStatus.APPROVAL_REQUIRED
    TRUST.set(True)  # turning it back on does not resurrect the task approval
    assert status(req("t.p4", rid="r8")) == PermissionStatus.APPROVAL_REQUIRED


# -- file scope ---------------------------------------------------------------
def test_allowed_and_denied_paths(home, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    TRUST.workspace_root = str(ws)
    TRUST.set(True)
    ok = [home / "Desktop" / "a.txt", home / "Documents" / "x" / "b.md", ws / "c.py"]
    for p in ok:
        assert TRUST.path_allowed(str(p)), p
        assert status(req("t.p3", resource=str(p))) == PermissionStatus.ALLOWED
    assert TRUST.path_allowed("notes.txt")  # relative -> workspace
    bad = [
        tmp_path / "Windows" / "System32" / "x.dll",
        tmp_path / "Program Files" / "app" / "a.exe",
        home / "AppData" / "Local" / "x",
        home / ".ssh" / "id_rsa",
        home / "Desktop" / ".env",
        home / "Desktop" / ".env.local",
        home / "Desktop" / "server.pem",
        home / "Desktop" / "SERVER.KEY",
        home / "Desktop" / "pw.kdbx",
        tmp_path / "Users" / "other" / "Desktop" / "a.txt",
        tmp_path / "elsewhere" / "a.txt",
    ]
    for p in bad:
        assert not TRUST.path_allowed(str(p)), p
        assert status(req("t.p3", resource=str(p))) == PermissionStatus.APPROVAL_REQUIRED


def test_hardening_case_dotdot_unc_ads(home):
    TRUST.set(True)
    d = home / "Desktop"
    assert not TRUST.path_allowed(str(d / ".." / "AppData" / "x"))
    assert not TRUST.path_allowed(str(d / ".." / ".." / "other" / "Desktop"))
    assert not TRUST.path_allowed(str(d / ".." / ".SSH" / "id"))
    assert not TRUST.path_allowed(str(home / "APPDATA" / "x"))
    assert not TRUST.path_allowed(str(home / "appdata. " / "x"))
    assert not TRUST.path_allowed("\\\\server\\share\\a.txt")
    assert not TRUST.path_allowed("//server/share/a.txt")
    assert not TRUST.path_allowed("\\\\?\\UNC\\server\\share\\a.txt")
    assert not TRUST.path_allowed(str(d / "a.txt") + ":stream")
    assert not TRUST.path_allowed("")
    assert not TRUST.path_allowed("relative.txt")  # no workspace set
    if sys.platform == "win32":
        assert TRUST.path_allowed(str(d).upper() + "\\a.txt")


def test_symlink_escape(home):
    TRUST.set(True)
    link = home / "Desktop" / "lnk"
    try:
        link.symlink_to(home / "AppData", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert not TRUST.path_allowed(str(link / "secret"))


@pytest.mark.skipif(sys.platform != "win32", reason="8.3 names are Windows only")
def test_short_name_cannot_hide_denied_dir(home):
    import ctypes

    TRUST.set(True)
    (home / "AppData" / "LongFolderName").mkdir()
    buf = ctypes.create_unicode_buffer(500)
    if not ctypes.windll.kernel32.GetShortPathNameW(
        str(home / "AppData" / "LongFolderName"), buf, 500
    ):
        pytest.skip("no short names")
    assert not TRUST.path_allowed(buf.value + "\\file")


def test_non_path_resources_unaffected():
    TRUST.set(True)
    assert status(req("t.p1", resource="https://evil.example/a?b=c")) == PermissionStatus.ALLOWED
    assert status(req("t.p1", resource="spotify:track:1")) == PermissionStatus.ALLOWED


def test_routes_get_post():
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    c = TestClient(app)
    g = c.get("/v1/trust").json()
    assert g["enabled"] is False and g["since"] is None and g["deny"] and "allow" in g
    on = c.post("/v1/trust", json={"enabled": True}).json()
    assert on["enabled"] and on["since"]
    assert c.get("/v1/trust").json()["enabled"] is True
    assert c.post("/v1/trust", json={"enabled": False}).json()["enabled"] is False
    assert c.post("/v1/trust", json={"enabled": "maybe"}).status_code == 422
    assert c.post("/v1/trust", json={}).status_code == 422


def test_recycle(tmp_path, monkeypatch):
    monkeypatch.setenv("SystemRoot", REAL_SYSTEM_ROOT)  # the fixture fakes it
    f = tmp_path / "gone.txt"
    f.write_text("x")
    if sys.platform == "win32":
        recycle(f)
        assert not f.exists()
    else:
        with pytest.raises(OSError):
            recycle(f)
