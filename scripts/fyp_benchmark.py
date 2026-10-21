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
    python scripts/fyp_benchmark.py --think off        # thinking-off row (PHX-017)
    python scripts/fyp_benchmark.py --config phoenix-multi-agent --help  # config (2)
    python scripts/fyp_benchmark.py --config phoenix-adaptive-deep --help  # config (3)

``--config phoenix-multi-agent`` dispatches to ``scripts/fyp_multi_agent.py`` and
``--config phoenix-adaptive-deep`` to ``scripts/fyp_adaptive_deep.py`` (PHX-116).
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
CONFIG3_REASON = (
    "Not part of a baseline run; measured separately with --config "
    "phoenix-adaptive-deep (PHX-116) once scripts/colibri_gate.py has gated the "
    "Colibri deep tier (T3)."
)
CATCH_RATE_REASON = (
    "Needs fault-injected cases: the v1 set has no known-bad candidates, and a "
    "LEVI rejection hides the rejected answer, so a catch cannot be scored here. "
    "Measured separately by scripts/metric_benchmark.py (PHX-117) on "
    "benchmarks/metrics/verification-v1.toml."
)
# PHX-117 (ADR-117): these datasets exist and are validated by
# scripts/metric_benchmark.py --dry-run; the metric waits for its capability.
MEMORY_REASON = (
    "Needs PHX-110 (memory and RAG in missions); its retrieval QA set "
    "benchmarks/metrics/memory-v1.toml is defined and validated (PHX-117)."
)
RESEARCH_REASON = (
    "Needs PHX-111 (web research in missions); its evidence rubric set "
    "benchmarks/metrics/research-v1.toml is defined and validated (PHX-117)."
)
CONTROL_REASON = (
    "Needs PHX-112 (computer-control steps); its task set "
    "benchmarks/metrics/computer-control-v1.toml is defined and validated (PHX-117)."
)
RECOVERY_REASON = (
    "A single-model baseline has no MAHORAGA; recovery is measured from "
    "configuration (2) MissionTrace.recoveries (PHX-109) and summarized by "
    "scripts/metric_benchmark.py --config2-results (PHX-117)."
)
INTERVENTION_REASON = (
    "A single-model baseline has no permission prompts; measured by "
    "scripts/metric_benchmark.py (PHX-117) on benchmarks/metrics/"
    "interventions-v1.toml and per mission in configuration (2)."
)
NOT_RUNNABLE_CONFIGS: tuple[dict[str, str], ...] = (
    {
        "id": "phoenix-multi-agent",
        "fyp_configuration": "2",
        "description": "Phoenix multi-agent orchestration",
        "reason": "Not part of a baseline run; measured separately with "
        "--config phoenix-multi-agent (PHX-091 continuation).",
    },
    {
        "id": "phoenix-adaptive-deep",
        "fyp_configuration": "3",
        "description": "Phoenix + adaptive model routing/deep tier",
        "reason": CONFIG3_REASON,
    },
)
# Every docs/24 metric: measured from baseline reports, or NOT_RUNNABLE + reason.
METRICS: tuple[tuple[str, str | None], ...] = (
    ("task_success", None),
    (
        "routing_accuracy",
        "Measured separately by scripts/routing_benchmark.py (PHX-105) on"
        " benchmarks/routing/routing-v1.toml; not part of this baseline run.",
    ),
    ("verification_catch_rate", CATCH_RATE_REASON),
    ("recovery_success", RECOVERY_REASON),
    ("memory_retrieval_accuracy", MEMORY_REASON),
    ("research_evidence_quality", RESEARCH_REASON),
    ("computer_control_completion", CONTROL_REASON),
    ("ttft_latency", None),
    ("ram_vram", None),
    ("token_usage", None),
    (
        "colibri_disk_io",
        "Measured by scripts/colibri_gate.py --live (PHX-116), not by a baseline run.",
    ),
    ("user_intervention_count", INTERVENTION_REASON),
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


THINK = {"default": None, "on": True, "off": False}

# Where Ollama actually placed a model, from /api/ps (size_vram vs size). A missing
# or disabled GPU makes Ollama fall back to CPU silently, so this is recorded per
# tier and CPU-only runs are refused unless --allow-cpu (ADR-079 amendment).
ComputeProbe = Callable[[str], dict[str, Any] | None]
OLLAMA_URL = "http://127.0.0.1:11434"


class CpuOnlyError(RuntimeError):
    """A tier would run without GPU offload and --allow-cpu was not given."""


def compute_from_ps(entry: dict[str, Any]) -> dict[str, Any]:
    size = int(entry.get("size") or 0)
    vram = int(entry.get("size_vram") or 0)
    fraction = vram / size if size else 0.0
    device = "gpu" if size and vram >= size else "partial" if vram else "cpu"
    return {
        "device": device,
        "size_bytes": size,
        "size_vram_bytes": vram,
        "gpu_offload_fraction": round(fraction, 4),
    }


def ollama_compute(model: str, base_url: str = OLLAMA_URL) -> dict[str, Any] | None:
    """Load ``model`` (empty prompt, no generation) and report its placement."""
    import httpx

    with httpx.Client(base_url=base_url, timeout=600) as client:
        client.post(
            "/api/generate", json={"model": model, "prompt": ""}
        ).raise_for_status()
        for entry in client.get("/api/ps").raise_for_status().json().get("models", []):
            if model in (entry.get("name"), entry.get("model")):
                return compute_from_ps(entry)
    return None


def _ollama_factory(
    think: bool | None = None,
) -> AbstractAsyncContextManager[ModelProvider]:
    from phoenix.models.ollama import OllamaProvider

    return OllamaProvider(think=think)


async def run_suite(
    suite: BenchmarkSuite,
    categories: dict[str, str],
    tiers: tuple[tuple[str, str], ...],
    config: BenchmarkConfig,
    provider_factory: ProviderFactory,
    runner_factory: Callable[
        [ModelLifecycleManager], BenchmarkRunner
    ] = BenchmarkRunner,
    compute_probe: ComputeProbe | None = None,
    allow_cpu: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    manager = ModelLifecycleManager()
    rows: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    async with provider_factory() as provider:
        for tier, model in tiers:
            before = await _probe(compute_probe, model)
            if before["device"] == "cpu" and not allow_cpu:
                raise CpuOnlyError(
                    f"{tier} {model} would run on CPU only ({before}); enable the "
                    "GPU or pass --allow-cpu to record a CPU-only row deliberately"
                )
            print(f"fyp: running {tier} {model} on {before['device']} ...", flush=True)
            report = await runner_factory(manager).run(
                provider, model, suite=suite, config=config
            )
            after = await _probe(compute_probe, model)
            row = baseline_row(tier, report, categories)
            row["compute"] = {"before": before, "after": after}
            if after["device"] == "cpu" and before["device"] != "cpu" and not allow_cpu:
                row["status"] = "invalid_cpu_fallback"  # GPU lost mid-run
            print(f"fyp: {tier} {model} {row['status']}", flush=True)
            rows.append(row)
            reports.append(report.model_dump(mode="json"))
    return rows, reports


async def _probe(probe: ComputeProbe | None, model: str) -> dict[str, Any]:
    if probe is None:
        return {"device": "not_probed"}
    found = await asyncio.to_thread(probe, model)
    return found if found is not None else {"device": "cpu", "reason": "not loaded"}


def write_summary(results: dict[str, Any], path: Path) -> None:
    lines = [
        f"# PHOENIX FYP benchmark — {results['suite']['id']} "
        f"{results['suite']['version']}",
        "",
        f"{results['started_utc']} → {results['finished_utc']} on "
        f"{results['host']['platform']}. Task set sha256 "
        f"`{results['suite']['sha256'][:16]}…`. Thinking: "
        f"{results['provider_options']['think_mode']}.",
        "",
        "## Configuration 1 — single local model baseline",
        "",
        "| Tier | Model | Device | Status | Accuracy | Success | TTFT median ms | "
        "Latency median ms | tok/s median | Max VRAM MiB |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in results["configurations"]["baseline"]:
        s = row["summary"]
        vram = row["max_gpu_used_mib"]
        lines.append(
            f"| {row['tier']} | {row['model_id']} | {_device(row)} | {row['status']} | "
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


def _device(row: dict[str, Any]) -> str:
    before = row.get("compute", {}).get("before", {})
    after = row.get("compute", {}).get("after", {})
    device = str(before.get("device", "not_probed"))
    if "gpu_offload_fraction" in before:
        device += f" {before['gpu_offload_fraction']:.0%}"
    if after.get("device") not in (None, before.get("device")):
        device += f" → {after['device']}"
    return device


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


CONFIGS = ("baseline", "phoenix-multi-agent", "phoenix-adaptive-deep")


def _multi_agent(argv: list[str]) -> int:
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import fyp_multi_agent

    return fyp_multi_agent.main(argv)


def _adaptive_deep(argv: list[str]) -> int:
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import fyp_adaptive_deep

    return fyp_adaptive_deep.main(argv)


def main(
    argv: list[str] | None = None,
    provider_factory: ProviderFactory | None = None,
    compute_probe: ComputeProbe | None = None,
) -> int:
    dispatch = argparse.ArgumentParser(add_help=False)
    dispatch.add_argument("--config", choices=CONFIGS, default="baseline")
    known, rest = dispatch.parse_known_args(argv)
    if known.config == "phoenix-multi-agent":
        return _multi_agent(rest)
    if known.config == "phoenix-adaptive-deep":
        return _adaptive_deep(rest)
    parser = argparse.ArgumentParser(description="PHOENIX FYP benchmark suite")
    parser.add_argument(
        "--config", choices=CONFIGS, default="baseline", help="FYP configuration"
    )
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--model", action="append", default=[], help="TIER=model")
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--runs", type=int, default=3)
    # Qwen3.5 thinks by default (ADR-079): with thinking on, the budget must cover
    # hidden reasoning plus the answer. --think maps to OllamaProvider (ADR-080).
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--think", choices=tuple(THINK), default="default")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument(
        "--allow-cpu", action="store_true", help="record CPU-only rows deliberately"
    )
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
    think = THINK[args.think]
    # Live runs always probe placement; injected providers (tests) probe only if given.
    probe = compute_probe or (ollama_compute if provider_factory is None else None)
    try:
        rows, reports = asyncio.run(
            run_suite(
                suite,
                categories,
                tiers,
                config,
                provider_factory or (lambda: _ollama_factory(think)),
                compute_probe=probe,
                allow_cpu=args.allow_cpu,
            )
        )
    except CpuOnlyError as error:
        print(f"fyp: refused - {error}", file=sys.stderr)
        return 2
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
        "provider_options": {"think_mode": args.think, "think": think},
        "compute_policy": {"allow_cpu": args.allow_cpu, "probe": probe is not None},
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
