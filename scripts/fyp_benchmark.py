"""PHOENIX FYP benchmark suite runner (PHX-091, ADR-079).

Loads the fixed, versioned task set (``benchmarks/fyp/tasks-v1.toml``) into the
existing PHX-016 ``BenchmarkSuite`` and runs the unchanged ``BenchmarkRunner``
for each single-model baseline tier through one shared ``ModelLifecycleManager``.
FYP configurations that PHOENIX cannot run yet are always reported as
NOT_RUNNABLE with a reason, and every docs/24 metric is reported as measured or
NOT_RUNNABLE. Nothing is estimated. Reports keep output hashes/lengths only.

Usage::

    python scripts/fyp_benchmark.py --dry-run          # validate task set, print plan
    python scripts/fyp_benchmark.py                    # all baseline tiers (Ollama)
    python scripts/fyp_benchmark.py --model T1=qwen3.5:4b --runs 1
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import sys
import tomllib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python scripts/fyp_benchmark.py`
    sys.path.insert(0, str(ROOT))

from phoenix.models.benchmark import (  # noqa: E402
    BenchmarkCase,
    BenchmarkConfig,
    BenchmarkReport,
    BenchmarkRunner,
    BenchmarkSuite,
    ScoreOutcome,
    ScoringMode,
    ScoringRule,
    TrialKind,
)
from phoenix.models.lifecycle import ModelLifecycleManager  # noqa: E402
from phoenix.models.provider import (  # noqa: E402
    MessageRole,
    ModelMessage,
    ModelProvider,
)

SCHEMA_VERSION = 1
DEFAULT_TASKS = ROOT / "benchmarks" / "fyp" / "tasks-v1.toml"
DEFAULT_OUT = ROOT / ".fyp-bench"
# docs/05 BLAZE-6 tiers used as single-local-model baselines (FYP configuration 1).
DEFAULT_TIERS: tuple[tuple[str, str], ...] = (
    ("T0", "qwen3.5:2b"),
    ("T1", "qwen3.5:4b"),
    ("T2", "qwen3.5:9b"),
    ("T2-code", "qwen2.5-coder:7b"),
)
INTEGRATION_GAP = (
    "PhoenixOrchestrator, AizenPlanner and LeviVerifier accept injected responders "
    "only; no model-backed responders exist yet (PHX-090 end_to_end GAP)."
)
NOT_RUNNABLE_CONFIGS: tuple[dict[str, str], ...] = (
    {
        "id": "phoenix-multi-agent",
        "fyp_configuration": "2",
        "description": "Phoenix multi-agent orchestration",
        "reason": INTEGRATION_GAP,
    },
    {
        "id": "phoenix-adaptive-deep",
        "fyp_configuration": "3",
        "description": "Phoenix + adaptive model routing/deep tier",
        "reason": INTEGRATION_GAP + " Colibri deep tier is not installed.",
    },
)
# Every docs/24 metric: measured from baseline reports, or NOT_RUNNABLE + reason.
METRICS: tuple[tuple[str, str | None], ...] = (
    ("task_success", None),
    ("routing_accuracy", "Needs model-backed PHOENIX routing (configuration 2/3)."),
    ("verification_catch_rate", "Needs a model-backed LEVI responder."),
    ("recovery_success", "Needs model-backed MAHORAGA/KAIDO execution."),
    ("memory_retrieval_accuracy", "Needs an end-to-end RAG answering flow."),
    ("research_evidence_quality", "Needs a model-backed URAHARA research flow."),
    ("computer_control_completion", "Needs model-driven tool execution."),
    ("ttft_latency", None),
    ("ram_vram", None),
    ("token_usage", None),
    ("colibri_disk_io", "Colibri deep tier is not installed."),
    ("user_intervention_count", "Needs interactive end-to-end task execution."),
)
MEASURED_FROM = {
    "task_success": "summary.score_accuracy and summary.success_rate",
    "ttft_latency": "summary.ttft_ms and summary.latency_ms (median/min/max)",
    "ram_vram": "max observed system RAM and GPU memory across measured trials",
    "token_usage": "sum of reported input/output tokens across measured trials",
}

ProviderFactory = Callable[[], AbstractAsyncContextManager[ModelProvider]]


def load_task_set(path: Path) -> tuple[BenchmarkSuite, dict[str, str]]:
    """Return the suite and a case_id -> category map; raises on any invalid case."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    cases: list[BenchmarkCase] = []
    categories: dict[str, str] = {}
    for raw in data.get("case", []):
        mode = ScoringMode(raw["scoring"])
        if mode is ScoringMode.UNSCORED and "expected" in raw:
            raise ValueError(f"unscored case {raw['id']} must not have expected text")
        rule = ScoringRule(mode=mode, expected=raw.get("expected"))
        cases.append(
            BenchmarkCase(
                case_id=raw["id"],
                category=raw["category"],
                messages=(ModelMessage(role=MessageRole.USER, text=raw["prompt"]),),
                scoring=rule,
            )
        )
        categories[raw["id"]] = raw["category"]
    suite = BenchmarkSuite(
        suite_id=data["suite_id"], version=data["version"], cases=tuple(cases)
    )
    return suite, categories


def parse_models(values: list[str]) -> tuple[tuple[str, str], ...]:
    if not values:
        return DEFAULT_TIERS
    parsed = []
    for value in values:
        tier, sep, model = value.partition("=")
        parsed.append((tier, model) if sep else ("custom", tier))
    return tuple(parsed)


def baseline_row(
    tier: str, report: BenchmarkReport, categories: dict[str, str]
) -> dict[str, Any]:
    measured = [t for t in report.trials if t.kind is TrialKind.MEASURED]
    by_category: dict[str, dict[str, int]] = {}
    for trial in measured:
        if trial.scoring_mode is ScoringMode.UNSCORED:
            continue
        row = by_category.setdefault(categories[trial.case_id], {"passed": 0, "n": 0})
        row["n"] += 1
        row["passed"] += trial.score is ScoreOutcome.PASS
    ram = [
        t.resources.max_observed_ram_used_bytes
        for t in measured
        if t.resources.max_observed_ram_used_bytes is not None
    ]
    vram = [
        gpu.max_observed_used_mib for t in measured for gpu in (t.resources.gpus or ())
    ]
    usage = [t.usage for t in measured if t.usage is not None]
    return {
        "fyp_configuration": "1",
        "tier": tier,
        "model_id": report.model_id,
        "status": report.status.value,
        "preflight": report.preflight.value,
        "summary": report.summary.model_dump(mode="json"),
        "category_accuracy": {
            name: {**row, "accuracy": row["passed"] / row["n"]}
            for name, row in sorted(by_category.items())
        },
        "max_ram_used_bytes": max(ram) if ram else None,
        "max_gpu_used_mib": max(vram) if vram else None,
        "input_tokens": sum(u.input_tokens or 0 for u in usage) if usage else None,
        "output_tokens": sum(u.output_tokens or 0 for u in usage) if usage else None,
        "not_assessed": list(report.not_assessed),
    }


def metric_rows() -> list[dict[str, str]]:
    rows = []
    for metric, reason in METRICS:
        if reason is None:
            rows.append(
                {"id": metric, "status": "MEASURED", "source": MEASURED_FROM[metric]}
            )
        else:
            rows.append({"id": metric, "status": "NOT_RUNNABLE", "reason": reason})
    return rows


def _ollama_factory() -> AbstractAsyncContextManager[ModelProvider]:
    from phoenix.models.ollama import OllamaProvider

    return OllamaProvider()


async def run_suite(
    suite: BenchmarkSuite,
    categories: dict[str, str],
    tiers: tuple[tuple[str, str], ...],
    config: BenchmarkConfig,
    provider_factory: ProviderFactory,
    runner_factory: Callable[
        [ModelLifecycleManager], BenchmarkRunner
    ] = BenchmarkRunner,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    manager = ModelLifecycleManager()
    rows: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    async with provider_factory() as provider:
        for tier, model in tiers:
            print(f"fyp: running {tier} {model} ...", flush=True)
            report = await runner_factory(manager).run(
                provider, model, suite=suite, config=config
            )
            print(f"fyp: {tier} {model} {report.status.value}", flush=True)
            rows.append(baseline_row(tier, report, categories))
            reports.append(report.model_dump(mode="json"))
    return rows, reports


def write_summary(results: dict[str, Any], path: Path) -> None:
    lines = [
        f"# PHOENIX FYP benchmark — {results['suite']['id']} "
        f"{results['suite']['version']}",
        "",
        f"{results['started_utc']} → {results['finished_utc']} on "
        f"{results['host']['platform']}. Task set sha256 "
        f"`{results['suite']['sha256'][:16]}…`.",
        "",
        "## Configuration 1 — single local model baseline",
        "",
        "| Tier | Model | Status | Accuracy | Success | TTFT median ms | "
        "Latency median ms | tok/s median | Max VRAM MiB |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in results["configurations"]["baseline"]:
        s = row["summary"]
        vram = row["max_gpu_used_mib"]
        lines.append(
            f"| {row['tier']} | {row['model_id']} | {row['status']} | "
            f"{_pct(s['score_accuracy'])} | {_pct(s['success_rate'])} | "
            f"{_num(s['ttft_ms']['median'])} | {_num(s['latency_ms']['median'])} | "
            f"{_num(s['tokens_per_second']['median'])} | "
            f"{vram if vram is not None else 'n/a'} |"
        )
    lines += ["", "## Configurations 2 and 3", ""]
    for row in results["configurations"]["not_runnable"]:
        lines.append(
            f"- **({row['fyp_configuration']}) {row['description']}** — NOT RUNNABLE: "
            f"{row['reason']}"
        )
    lines += [
        "",
        "## docs/24 metrics",
        "",
        "| Metric | Status | Source / reason |",
        "| --- | --- | --- |",
    ]
    for m in results["metrics"]:
        lines.append(
            f"| {m['id']} | {m['status']} | {m.get('source') or m['reason']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


def main(
    argv: list[str] | None = None, provider_factory: ProviderFactory | None = None
) -> int:
    parser = argparse.ArgumentParser(description="PHOENIX FYP benchmark suite")
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--model", action="append", default=[], help="TIER=model")
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--runs", type=int, default=3)
    # Qwen3.5 thinks by default and OllamaProvider has no think control (ADR-079):
    # the budget must cover hidden reasoning plus the answer.
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    suite, categories = load_task_set(args.tasks)
    tiers = parse_models(args.model)
    config = BenchmarkConfig(
        warmup_runs=args.warmup,
        measured_runs=args.runs,
        per_trial_timeout_seconds=args.timeout,
        max_output_tokens=args.max_output_tokens,
    )
    if args.dry_run:
        print(
            f"fyp: {suite.suite_id} {suite.version}: {len(suite.cases)} cases; "
            f"tiers: {', '.join(f'{t}={m}' for t, m in tiers)}"
        )
        return 0

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat()
    rows, reports = asyncio.run(
        run_suite(suite, categories, tiers, config, provider_factory or _ollama_factory)
    )
    results: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": "phoenix-fyp-benchmark",
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(),
        "suite": {
            "id": suite.suite_id,
            "version": suite.version,
            "path": str(args.tasks),
            "sha256": hashlib.sha256(args.tasks.read_bytes()).hexdigest(),
            "cases": len(suite.cases),
        },
        "config": config.model_dump(mode="json"),
        "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "configurations": {
            "baseline": rows,
            "not_runnable": list(NOT_RUNNABLE_CONFIGS),
        },
        "metrics": metric_rows(),
        "reports": reports,
    }
    (out / "fyp-benchmark-results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    write_summary(results, out / "fyp-benchmark-summary.md")
    print(f"fyp: done - results in {out}")
    statuses = {row["status"] for row in rows}
    return 0 if statuses <= {"success", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
