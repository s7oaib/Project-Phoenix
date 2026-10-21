"""PHOENIX live computer-control smoke run (PHX-112). Owner-run; not a CI gate.

Three modes, each harmless and local except one public read-only page:

    uv run python scripts/phoenix_control_smoke.py --dry-run
    uv run python scripts/phoenix_control_smoke.py --tools
    uv run python scripts/phoenix_control_smoke.py --mission

``--tools`` needs no model. It drives the real tools through PermissionEngine
with explicit grants: PHOENIX's own Playwright Chromium (from
``D:\\Phoenix\\browsers``, see scripts/install_control_browser.py) opens
https://example.com, reads it and closes; then the script launches its OWN
fixture window (a small Tk window titled "PHOENIX Control Smoke Fixture") and
lists, inspects, focuses and closes only that window, the close being a P4
action run with an explicit user decision. It never touches any other window.

``--mission`` runs one COMPLEX mission on a local Ollama with computer-control
steps enabled and the DRAFT control prompts (pending owner approval). It plays
the approve -> re-run loop itself for P1/P2 browser requests on
https://example.com only; anything else (including every P4) stops the run.

Reports keep only statuses, codes, tool IDs, timings and hashes/lengths.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python scripts/phoenix_control_smoke.py`
    sys.path.insert(0, str(ROOT))

from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from alembic import command  # noqa: E402
from phoenix.agents import control_prompts  # noqa: E402
from phoenix.agents.control_steps import (  # noqa: E402
    BROWSER_SESSION_ID,
    CONTROL_ACTOR_ID,
    CONTROL_TOOL_REASON,
    MAX_READ_CHARS,
    ControlStepRunner,
    control_permission_policy,
    control_tool_definitions,
)
from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest  # noqa: E402
from phoenix.agents.runtime import (  # noqa: E402
    MissionKind,
    MissionResult,
    PhoenixRuntime,
    default_tier_table,
)
from phoenix.models.lifecycle import ModelLifecycleManager  # noqa: E402
from phoenix.models.provider import ModelProviderError  # noqa: E402
from phoenix.models.registry import ModelCapabilityRegistry  # noqa: E402
from phoenix.security.audit import AuditTrail  # noqa: E402
from phoenix.security.permissions import (  # noqa: E402
    GrantDuration,
    PermissionGrant,
    PermissionRequest,
    UserDecision,
    UserPermissionDecision,
)
from phoenix.tools.browser import (  # noqa: E402
    BrowserCall,
    BrowserOperation,
    BrowserTool,
)
from phoenix.tools.browser_setup import (  # noqa: E402
    browsers_path,
    chromium_installed,
    use_phoenix_browsers,
)
from phoenix.tools.registry import ToolRegistry, ToolRiskLevel  # noqa: E402
from phoenix.tools.windows_ui import (  # noqa: E402
    WindowsUICall,
    WindowsUIOperation,
    WindowsUITool,
)

SCHEMA_VERSION = 1
DEFAULT_OUT = ROOT / ".phoenix-smoke"
PAGE_URL = "https://example.com/"
FIXTURE_TITLE = "PHOENIX Control Smoke Fixture"
FIXTURE_SOURCE = (
    "import tkinter as tk\n"
    "root = tk.Tk()\n"
    f"root.title({FIXTURE_TITLE!r})\n"
    "root.geometry('320x120')\n"
    "root.after(120000, root.destroy)\n"
    "root.mainloop()\n"
)
MISSION_REQUEST = (
    "Open https://example.com in your browser and tell me the page's main heading."
)
SESSION_ID = "phoenix-control-smoke"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _audit(directory: Path, registry: ToolRegistry) -> AuditTrail:
    database = directory / "smoke-audit.db"
    config = Config(str(ROOT / "alembic.ini"))
    url = f"sqlite:///{database}"
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")
    return AuditTrail(sessionmaker(create_engine(url)), registry)


def _request(intent: Any) -> PermissionRequest:
    return PermissionRequest(
        request_id=str(uuid4()),
        actor_id=CONTROL_ACTOR_ID,
        tool_id=intent.tool_id,
        action=intent.action,
        resource=intent.resource,
        reason=CONTROL_TOOL_REASON,
        requested_at=datetime.now(UTC),
        session_id=SESSION_ID,
    )


def _grant(request: PermissionRequest, risk: ToolRiskLevel) -> PermissionGrant:
    return PermissionGrant(
        grant_id=str(uuid4()),
        actor_id=request.actor_id,
        tool_id=request.tool_id,
        action=request.action,
        resource=request.resource,
        reason=request.reason,
        duration=GrantDuration.SESSION,
        risk_level=risk,
        issued_at=datetime.now(UTC) - timedelta(seconds=1),
        session_id=SESSION_ID,
        user_decision="approved",
    )


def _step(tool: Any, call: Any, *, decide: bool = False) -> dict[str, Any]:
    """One action: describe, the owner's grant/decision, then the tool decides."""
    intent = tool.describe(call)
    request = _request(intent)
    grants: tuple[PermissionGrant, ...] = ()
    decision = None
    if decide:
        decision = UserPermissionDecision(
            decision_id=str(uuid4()),
            request_id=request.request_id,
            decision=UserDecision.APPROVED,
            decided_at=datetime.now(UTC),
        )
    else:
        grants = (_grant(request, intent.risk_level),)
    started = time.perf_counter()
    result = tool.execute(call, request, grants=grants, user_decision=decision)
    return {
        "tool_id": intent.tool_id,
        "risk": intent.risk_level.value,
        "decision": result.permission_decision.status.value,
        "status": result.status.value,
        "error": None if result.error_code is None else result.error_code.value,
        "seconds": round(time.perf_counter() - started, 3),
        "_result": result,
    }


def run_tools(directory: Path) -> dict[str, Any]:
    path = browsers_path()
    if not chromium_installed(path):
        raise SystemExit("PHOENIX Chromium missing: run install_control_browser.py")
    use_phoenix_browsers(path)
    registry = ToolRegistry(control_tool_definitions())
    audit = _audit(directory, registry)
    policy = control_permission_policy()
    steps: list[dict[str, Any]] = []
    browser = BrowserTool(registry, policy, audit)
    session = BROWSER_SESSION_ID
    for call in (
        BrowserCall(operation=BrowserOperation.SESSION_OPEN, session_id=session),
        BrowserCall(
            operation=BrowserOperation.NAVIGATE, session_id=session, url=PAGE_URL
        ),
        BrowserCall(
            operation=BrowserOperation.READ_PAGE,
            session_id=session,
            expected_url=PAGE_URL,
            max_chars=MAX_READ_CHARS,
        ),
        BrowserCall(operation=BrowserOperation.SESSION_CLOSE, session_id=session),
    ):
        step = _step(browser, call)
        result = step.pop("_result")
        if call.operation == BrowserOperation.READ_PAGE and result.text is not None:
            step["text_chars"] = len(result.text)
            step["text_sha256"] = _sha256(result.text)
        steps.append(step)
    windows = WindowsUITool(registry, policy, audit)
    fixture = subprocess.Popen([sys.executable, "-c", FIXTURE_SOURCE])
    try:
        target = None
        for _ in range(50):
            listing = _step(
                windows,
                WindowsUICall(operation=WindowsUIOperation.LIST_WINDOWS, max_items=200),
            )
            found = [
                w for w in listing.pop("_result").windows if w.title == FIXTURE_TITLE
            ]
            if len(found) == 1:
                steps.append(listing)
                target = found[0]
                break
            time.sleep(0.2)
        if target is None:
            raise SystemExit("Fixture window did not appear")
        bound = {
            "hwnd": target.hwnd,
            "expected_process_id": target.process_id,
            "expected_class_name": target.class_name,
        }
        for operation, decide in (
            (WindowsUIOperation.INSPECT_WINDOW, False),
            (WindowsUIOperation.FOCUS_WINDOW, False),
            (WindowsUIOperation.CLOSE_WINDOW, True),  # P4: explicit decision
        ):
            fields: dict[str, Any] = {"operation": operation, **bound}
            if operation == WindowsUIOperation.INSPECT_WINDOW:
                fields = {"operation": operation, "hwnd": target.hwnd}
            step = _step(windows, WindowsUICall.model_validate(fields), decide=decide)
            step.pop("_result")
            steps.append(step)
    finally:
        try:
            fixture.wait(timeout=10)
        except subprocess.TimeoutExpired:
            fixture.kill()  # only the fixture process this script started
    return {
        "browsers_path": str(path),
        "steps": steps,
        "audit_rows": len(audit.list_records()),
    }


async def run_mission(base_url: str) -> dict[str, Any]:
    from phoenix.models.ollama import OllamaProvider

    path = browsers_path()
    if not chromium_installed(path):
        raise SystemExit("PHOENIX Chromium missing: run install_control_browser.py")
    use_phoenix_browsers(path)
    registry = ToolRegistry(control_tool_definitions())
    policy = control_permission_policy()
    with tempfile.TemporaryDirectory(prefix="phoenix-control-") as directory:
        audit = _audit(Path(directory), registry)
        runner = ControlStepRunner(
            registry=registry,
            policy=policy,
            browser=BrowserTool(registry, policy, audit),
        )
        store = ModelCapabilityRegistry()
        table = default_tier_table()
        models = [ref.model_id for refs in table.roles.values() for ref in refs]
        models += [ref.model_id for ref in table.default]
        runs: list[dict[str, Any]] = []
        async with (
            OllamaProvider(base_url) as default,
            OllamaProvider(base_url, think=False) as structured,
        ):
            for model in dict.fromkeys(models):
                try:
                    store.register(await default.capabilities(model))
                except ModelProviderError:
                    continue
            runtime = PhoenixRuntime(
                providers=(default,),
                structured_providers=(structured,),
                capability_registry=store,
                lifecycle=ModelLifecycleManager(),
                control=runner,
            )
            grants: list[PermissionGrant] = []
            result: MissionResult | None = None
            for _ in range(5):
                started = time.perf_counter()
                result = await runtime.run(
                    PhoenixRequest(text=MISSION_REQUEST),
                    OrchestrationPath.COMPLEX,
                    grants=tuple(grants),
                    session_id=SESSION_ID,
                )
                pending = result.pending_permission
                runs.append(
                    {
                        "kind": result.kind.value,
                        "failure_code": result.failure_code,
                        "responder_error": result.responder_error,
                        "pending_tool": None if pending is None else pending.tool_id,
                        "seconds": round(time.perf_counter() - started, 3),
                        "control_calls": [
                            item.model_dump(mode="json")
                            for item in result.trace.control_calls
                        ],
                    }
                )
                if result.kind != MissionKind.WAITING_PERMISSION or pending is None:
                    break
                risk = {
                    "browser.session_open": ToolRiskLevel.P2,
                    "browser.navigate": ToolRiskLevel.P2,
                    "browser.read_page": ToolRiskLevel.P1,
                }.get(pending.tool_id)
                if risk is None or (
                    pending.tool_id != "browser.session_open"
                    and "https://example.com" not in pending.resource
                ):
                    break  # never auto-approve anything else, and never P4
                grants.append(_grant(pending, risk))
        runner.close()
        answer = None if result is None else result.text
        return {
            "runs": runs,
            "answer_chars": None if answer is None else len(answer),
            "answer_sha256": None if answer is None else _sha256(answer),
            "audit_rows": len(audit.list_records()),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--tools", action="store_true")
    mode.add_argument("--mission", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    plan = {
        "browsers_path": str(browsers_path()),
        "chromium_installed": chromium_installed(browsers_path()),
        "page_url": PAGE_URL,
        "fixture_window": FIXTURE_TITLE,
        "control_prompt_set_version": control_prompts.CONTROL_PROMPT_SET_VERSION,
        "mission_request_sha256": _sha256(MISSION_REQUEST),
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    if args.tools:
        with tempfile.TemporaryDirectory(prefix="phoenix-control-") as directory:
            report = run_tools(Path(directory))
        name = "phoenix-control-tools-smoke.json"
        ok = all(step["status"] == "success" for step in report["steps"])
    else:
        report = asyncio.run(run_mission(args.base_url))
        name = "phoenix-control-mission-smoke.json"
        ok = report["runs"][-1]["kind"] == "completed"
    results = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        **plan,
        **report,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / name
    path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"{'ok' if ok else 'not ok'} -> {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
