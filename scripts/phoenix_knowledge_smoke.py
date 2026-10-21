"""PHOENIX live knowledge-mission smoke run (PHX-110, ADR-130 to ADR-134).

Owner- or coordinator-run; not a CI gate. In a fresh temporary data directory
it migrates SQLite, uploads one fixed, non-sensitive smoke document and saves
one user note through ``KnowledgeService`` (EmbeddingGemma on the local
Ollama), then runs ``--runs`` COMPLEX missions through ``PhoenixRuntime`` with
that knowledge source: AIZEN plans, a knowledge step retrieves and cites,
LEVI checks the citations, PHOENIX answers.

The report keeps only status, codes, timings, counts, citation markers and
hashes. It never stores prompts, model outputs or source content.

Usage::

    python scripts/phoenix_knowledge_smoke.py              # live, needs Ollama
    python scripts/phoenix_knowledge_smoke.py --runs 5     # acceptance batch
    python scripts/phoenix_knowledge_smoke.py --dry-run    # print the plan only
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python scripts/phoenix_knowledge_smoke.py`
    sys.path.insert(0, str(ROOT))

from alembic.config import Config  # noqa: E402
from sqlalchemy import Engine, create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from alembic import command  # noqa: E402
from phoenix.agents import prompts  # noqa: E402
from phoenix.agents.orchestrator import (  # noqa: E402
    OrchestrationPath,
    PhoenixRequest,
)
from phoenix.agents.runtime import (  # noqa: E402
    MissionResult,
    PhoenixRuntime,
    default_tier_table,
)
from phoenix.api.knowledge import EMBEDDING_MODEL_ID, KnowledgeService  # noqa: E402
from phoenix.models.lifecycle import ModelLifecycleManager  # noqa: E402
from phoenix.models.provider import ModelProviderError  # noqa: E402
from phoenix.models.registry import ModelCapabilityRegistry  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_OUT = ROOT / ".phoenix-smoke"
SMOKE_FILE = "harbour-report.txt"
SMOKE_CONTENT = (
    "Harbour project status report.\n"
    "The harbour project budget for the pilot phase is 4700 euros.\n"
    "The pilot phase ends in March and the steering group meets monthly.\n"
)
SMOKE_NOTE = "I prefer short answers that name their source."
SMOKE_REQUEST = (
    "According to my uploaded harbour project report, what is the budget for"
    " the pilot phase? Answer in one sentence and cite the source."
)
EXPECTED_NUMBER = "4700"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _models() -> tuple[str, ...]:
    table = default_tier_table()
    ordered = [ref.model_id for refs in table.roles.values() for ref in refs]
    ordered += [ref.model_id for ref in table.default]
    return tuple(dict.fromkeys(ordered))


def _migrated(directory: Path) -> Engine:
    database = directory / "phoenix.db"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}".replace("%", "%%"))
    command.upgrade(config, "head")
    return create_engine(f"sqlite:///{database}")


def summarize(result: MissionResult, seconds: float) -> dict[str, Any]:
    """Status, codes, timings, counts and markers only; never text or content."""
    trace = result.trace
    text = result.text
    return {
        "kind": result.kind.value,
        "failure_code": result.failure_code,
        "responder_error": result.responder_error,
        "answer_has_expected_number": None if text is None else EXPECTED_NUMBER in text,
        "answer_chars": None if text is None else len(text),
        "answer_sha256": None if text is None else _sha256(text),
        "answer_markers": []
        if text is None
        else [c.marker for c in result.citations if c.marker in text],
        "mission_seconds": round(seconds, 3),
        "prompt_set_version": trace.prompt_set_version,
        "states": [state.value for state in trace.states],
        "roles": [item.role for item in trace.generations],
        "generations": [item.model_dump(mode="json") for item in trace.generations],
        "verifications": [item.model_dump(mode="json") for item in trace.verifications],
        "retrievals": [item.model_dump(mode="json") for item in trace.retrievals],
        "citations": [
            {
                "marker": c.marker,
                "source_kind": c.source_kind,
                "title_matches_upload": c.title == SMOKE_FILE,
                "chunk_index": c.chunk_index,
            }
            for c in result.citations
        ],
        "recoveries": [item.model_dump(mode="json") for item in trace.recoveries],
    }


async def run_smoke(base_url: str, directory: Path, runs: int) -> dict[str, Any]:
    engine = _migrated(directory)
    try:
        return await _missions(base_url, sessionmaker(bind=engine), runs)
    finally:
        engine.dispose()  # before the temporary directory is removed


async def _missions(
    base_url: str, sessions: sessionmaker[Session], runs: int
) -> dict[str, Any]:
    from phoenix.models.ollama import OllamaProvider
    from phoenix.models.ollama_embeddings import OllamaEmbeddingProvider

    lifecycle = ModelLifecycleManager()
    store = ModelCapabilityRegistry()
    async with (
        OllamaProvider(base_url) as default,
        OllamaProvider(base_url, think=False) as structured,
        OllamaEmbeddingProvider(base_url) as embeddings,
    ):
        available: list[str] = []
        for model in _models():
            try:
                store.register(await default.capabilities(model))
                available.append(model)
            except ModelProviderError:
                continue  # not installed; the tier table falls back to T0
        knowledge = KnowledgeService(
            sessions=sessions, embedding=embeddings, lifecycle=lifecycle
        )
        started = time.perf_counter()
        document = await knowledge.upload(SMOKE_FILE, SMOKE_CONTENT.encode("utf-8"))
        await knowledge.remember(SMOKE_NOTE)
        indexing_seconds = time.perf_counter() - started
        runtime = PhoenixRuntime(
            providers=(default,),
            structured_providers=(structured,),
            capability_registry=store,
            lifecycle=lifecycle,
            knowledge=knowledge,
        )
        missions: list[dict[str, Any]] = []
        for _ in range(runs):
            started = time.perf_counter()
            result = await runtime.run(
                PhoenixRequest(text=SMOKE_REQUEST), OrchestrationPath.COMPLEX
            )
            missions.append(summarize(result, time.perf_counter() - started))
    return {
        "models_registered": available,
        "embedding_model": EMBEDDING_MODEL_ID,
        "document_chunks": document.chunk_count,
        "indexing_seconds": round(indexing_seconds, 3),
        "completed": sum(m["kind"] == "completed" for m in missions),
        "cited": sum(bool(m["answer_markers"]) for m in missions),
        "missions": missions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--runs", type=int, default=1, choices=range(1, 21))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    plan = {
        "request_sha256": _sha256(SMOKE_REQUEST),
        "smoke_file_sha256": _sha256(SMOKE_CONTENT),
        "prompt_set_version": prompts.PROMPT_SET_VERSION,
        "embedding_model": EMBEDDING_MODEL_ID,
        "models": list(_models()),
        "runs": args.runs,
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    with tempfile.TemporaryDirectory(prefix="phoenix-knowledge-smoke-") as folder:
        report = asyncio.run(run_smoke(args.base_url, Path(folder), args.runs))
    results = {
        "schema_version": SCHEMA_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        **plan,
        **report,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "phoenix-knowledge-smoke.json"
    path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"{results['completed']}/{args.runs} completed -> {path}")
    return 0 if results["completed"] == args.runs else 1


if __name__ == "__main__":
    sys.exit(main())
