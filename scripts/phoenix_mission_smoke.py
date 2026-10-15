"""PHOENIX live mission smoke run (PHX-046, ADR-082). Owner-run; not a CI gate.

Runs ONE harmless COMPLEX mission through ``PhoenixRuntime`` against a local
Ollama: AIZEN plans, a document step reads one file from a fresh temporary
workspace under a caller-supplied SESSION grant, LEVI verifies, PHOENIX answers.
The workspace holds only a fixed, non-sensitive smoke file. Nothing is written
outside the temporary workspace and ``--out``; no audit database is used, since
P1 reads have no mandatory audit.

The report keeps only status, codes, timings, token counts, IDs and output
hashes/lengths. It never stores prompts, model outputs or file content.

Usage::

    python scripts/phoenix_mission_smoke.py             # live, needs local Ollama
    python scripts/phoenix_mission_smoke.py --dry-run   # print the plan only
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python scripts/phoenix_mission_smoke.py`
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from phoenix.agents import prompts  # noqa: E402
from phoenix.agents.orchestrator import (  # noqa: E402
    OrchestrationPath,
    PhoenixRequest,
)
from phoenix.agents.runtime import (  # noqa: E402
    DOCUMENT_TOOL_REASON,
    MissionResult,
    PhoenixRuntime,
    default_tier_table,
)
from phoenix.models.lifecycle import ModelLifecycleManager  # noqa: E402
from phoenix.models.provider import ModelProviderError  # noqa: E402
from phoenix.models.registry import ModelCapabilityRegistry  # noqa: E402
from phoenix.security.audit import AuditTrail  # noqa: E402
from phoenix.security.permissions import (  # noqa: E402
    PermissionGrant,
    PermissionPolicy,
)
from phoenix.tools.filesystem import (  # noqa: E402
    FilesystemCall,
    FilesystemTool,
    filesystem_tool_definitions,
)
from phoenix.tools.registry import ToolRegistry  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_OUT = ROOT / ".phoenix-smoke"
SMOKE_FILE = "smoke.txt"
SMOKE_CONTENT = "PHOENIX smoke test file. The magic number is 42.\n"
SMOKE_REQUEST = (
    "Read the file smoke.txt in my workspace and reply with only the magic"
    " number it states."
)
EXPECTED_ANSWER = "42"
SESSION_ID = "phoenix-mission-smoke"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _workspace(directory: Path) -> Path:
    root = directory / "workspace"
    root.mkdir()
    (root / SMOKE_FILE).write_bytes(SMOKE_CONTENT.encode("utf-8"))
    return root


def _filesystem_tool(root: Path) -> FilesystemTool:
    tools = ToolRegistry(filesystem_tool_definitions())
    # P1 reads never touch the audit database, so an unbound session suffices.
    return FilesystemTool(
        root, tools, PermissionPolicy(), AuditTrail(sessionmaker(), tools)
    )


def _grant(tool: FilesystemTool) -> PermissionGrant:
    """The owner's explicit grant for exactly one read of the smoke file."""
    intent = tool.describe(
        FilesystemCall.model_validate(
            {"operation": "read_text", "relative_path": SMOKE_FILE}
        )
    )
    return PermissionGrant.model_validate(
        {
            "grant_id": "phoenix-smoke-grant",
            "actor_id": "document_analyst",
            "tool_id": intent.tool_id,
            "action": intent.action,
            "resource": intent.resource,
            "reason": DOCUMENT_TOOL_REASON,
            "duration": "session",
            "session_id": SESSION_ID,
            "risk_level": intent.risk_level.value,
            "issued_at": datetime.now(UTC) - timedelta(seconds=1),
            "user_decision": "approved",
        }
    )


def _models() -> tuple[str, ...]:
    table = default_tier_table()
    ordered = [ref.model_id for refs in table.roles.values() for ref in refs]
    ordered += [ref.model_id for ref in table.default]
    return tuple(dict.fromkeys(ordered))


def summarize(result: MissionResult, seconds: float) -> dict[str, Any]:
    """Status, codes, timings, counts and hashes only; never text or content."""
    trace = result.trace
    text = result.text
    return {
        "kind": result.kind.value,
        "failure_code": result.failure_code,
        "responder_error": result.responder_error,
        "answer_matches_expected": None
        if text is None
        else text.strip() == EXPECTED_ANSWER,
        "answer_chars": None if text is None else len(text),
        "answer_sha256": None if text is None else _sha256(text),
        "pending_permission_tool": None
        if result.pending_permission is None
        else result.pending_permission.tool_id,
        "mission_seconds": round(seconds, 3),
        "prompt_set_version": trace.prompt_set_version,
        "states": [state.value for state in trace.states],
        "provider_health": [
            item.model_dump(mode="json") for item in trace.provider_health
        ],
        "generations": [item.model_dump(mode="json") for item in trace.generations],
        "verifications": [item.model_dump(mode="json") for item in trace.verifications],
        "tool_calls": [item.model_dump(mode="json") for item in trace.tool_calls],
    }


async def run_smoke(base_url: str, root: Path) -> dict[str, Any]:
    from phoenix.models.ollama import OllamaProvider

    tool = _filesystem_tool(root)
    store = ModelCapabilityRegistry()
    async with (
        OllamaProvider(base_url) as default,
        OllamaProvider(base_url, think=False) as structured,
    ):
        available: list[str] = []
        for model in _models():
            try:
                store.register(await default.capabilities(model))
                available.append(model)
            except ModelProviderError:
                continue  # not installed; the tier table falls back to T0
        runtime = PhoenixRuntime(
            providers=(default,),
            structured_providers=(structured,),
            capability_registry=store,
            lifecycle=ModelLifecycleManager(),
            filesystem_tool=tool,
        )
        started = time.perf_counter()
        result = await runtime.run(
            PhoenixRequest(text=SMOKE_REQUEST),
            OrchestrationPath.COMPLEX,
            grants=(_grant(tool),),
            session_id=SESSION_ID,
        )
        seconds = time.perf_counter() - started
    return {"models_registered": available, **summarize(result, seconds)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    plan = {
        "request_sha256": _sha256(SMOKE_REQUEST),
        "smoke_file_sha256": _sha256(SMOKE_CONTENT),
        "prompt_set_version": prompts.PROMPT_SET_VERSION,
        "models": list(_models()),
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    with tempfile.TemporaryDirectory(prefix="phoenix-smoke-") as directory:
        report = asyncio.run(run_smoke(args.base_url, _workspace(Path(directory))))
    results = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        **plan,
        **report,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "phoenix-mission-smoke.json"
    path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"{results['kind']} -> {path}")
    return 0 if results["kind"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
