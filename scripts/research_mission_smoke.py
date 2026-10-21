"""PHOENIX live web research mission smoke run (PHX-111). Owner-run; not a CI gate.

Runs ONE COMPLEX mission through ``PhoenixRuntime`` against a local Ollama and
the real DuckDuckGo HTML endpoint: AIZEN plans a web research step, URAHARA
proposes a query, the WebResearchTool searches and reads the top pages under a
caller-supplied SESSION grant for ``search:duckduckgo`` (running this script is
the owner's approval), LEVI checks the citations deterministically, and PHOENIX
answers with a Sources list.

The report keeps only status, codes, timings, token counts, IDs, counts and
hashes. It never stores prompts, queries, page text or model output.

Usage::

    python scripts/research_mission_smoke.py             # live: Ollama + network
    python scripts/research_mission_smoke.py --dry-run   # print the plan only
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python scripts/research_mission_smoke.py`
    sys.path.insert(0, str(ROOT))

from phoenix.agents import prompts  # noqa: E402
from phoenix.agents.orchestrator import (  # noqa: E402
    OrchestrationPath,
    PhoenixRequest,
)
from phoenix.agents.research_steps import RESEARCH_TOOL_REASON  # noqa: E402
from phoenix.agents.runtime import (  # noqa: E402
    MissionResult,
    PhoenixRuntime,
    default_tier_table,
)
from phoenix.models.lifecycle import ModelLifecycleManager  # noqa: E402
from phoenix.models.provider import ModelProviderError  # noqa: E402
from phoenix.models.registry import ModelCapabilityRegistry  # noqa: E402
from phoenix.security.permissions import PermissionGrant  # noqa: E402
from phoenix.tools.registry import ToolRegistry  # noqa: E402
from phoenix.tools.web_research import (  # noqa: E402
    WebResearchTool,
    research_resource,
    web_research_tool_definitions,
)
from phoenix.web.duckduckgo import (  # noqa: E402
    DUCKDUCKGO_PROVIDER_ID,
    DuckDuckGoSearchProvider,
)
from phoenix.web.page_fetch import HttpPageFetcher  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_OUT = ROOT / ".phoenix-smoke"
SMOKE_REQUEST = (
    "Search the web: when was Python 3.13.0 released? Answer in one sentence"
    " and cite your source."
)
EXPECTED_FRAGMENT = "2024"
SESSION_ID = "phoenix-research-smoke"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _grant() -> PermissionGrant:
    """The owner's explicit SESSION grant for searches sent to DuckDuckGo."""
    return PermissionGrant.model_validate(
        {
            "grant_id": "phoenix-research-smoke-grant",
            "actor_id": "web_research",
            "tool_id": "web.research",
            "action": "research",
            "resource": research_resource(DUCKDUCKGO_PROVIDER_ID),
            "reason": RESEARCH_TOOL_REASON,
            "duration": "session",
            "session_id": SESSION_ID,
            "risk_level": "P1",
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
        "answer_contains_expected": None if text is None else EXPECTED_FRAGMENT in text,
        "answer_has_sources": None if text is None else "\n\nSources:\n" in text,
        "answer_chars": None if text is None else len(text),
        "answer_sha256": None if text is None else _sha256(text),
        "mission_seconds": round(seconds, 3),
        "prompt_set_version": trace.prompt_set_version,
        "states": [state.value for state in trace.states],
        "generations": [item.model_dump(mode="json") for item in trace.generations],
        "verifications": [item.model_dump(mode="json") for item in trace.verifications],
        "research_calls": [
            item.model_dump(mode="json") for item in trace.research_calls
        ],
        "recoveries": [item.model_dump(mode="json") for item in trace.recoveries],
    }


async def run_smoke(base_url: str) -> dict[str, Any]:
    from phoenix.models.ollama import OllamaProvider

    store = ModelCapabilityRegistry()
    search = DuckDuckGoSearchProvider()
    fetcher = HttpPageFetcher()
    tool = WebResearchTool(
        ToolRegistry(web_research_tool_definitions()), search, fetcher
    )
    try:
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
                research_tool=tool,
            )
            started = time.perf_counter()
            result = await runtime.run(
                PhoenixRequest(text=SMOKE_REQUEST),
                OrchestrationPath.COMPLEX,
                grants=(_grant(),),
                session_id=SESSION_ID,
            )
            seconds = time.perf_counter() - started
    finally:
        await search.aclose()
        await fetcher.aclose()
    return {"models_registered": available, **summarize(result, seconds)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    plan = {
        "request_sha256": _sha256(SMOKE_REQUEST),
        "search_provider": DUCKDUCKGO_PROVIDER_ID,
        "prompt_set_version": prompts.PROMPT_SET_VERSION,
        "models": list(_models()),
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    report = asyncio.run(run_smoke(args.base_url))
    results = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        **plan,
        **report,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "research-mission-smoke.json"
    path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"{results['kind']} -> {path}")
    return 0 if results["kind"] == "completed" else 1


if __name__ == "__main__":
    sys.exit(main())
