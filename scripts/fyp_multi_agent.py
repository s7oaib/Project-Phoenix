"""FYP configuration (2): PHOENIX multi-agent orchestration (PHX-091, ADR-079).

Runs the unchanged v1 task set through ``PhoenixRuntime.run`` against a local
Ollama. By default PHOENIX triages each request (PHX-105) and answers it DIRECT
or COMPLEX; ``--path complex`` forces the full pipeline (AIZEN plans, PHOENIX
assigns and routes, specialists answer, LEVI verifies every step and the final
answer) as a stress test, and ``--path direct`` forces one reply. It uses a
default provider for thinking roles and a ``think=False`` provider for the
structured roles, exactly as ``scripts/phoenix_mission_smoke.py`` builds it.
It has its own mission loop, not the PHX-016 ``BenchmarkRunner``: the runtime
holds the single lifecycle lease per generation, so wrapping it would deadlock
(PHX-029 finding 9).

Scoring is the same deterministic exact/contains rule as configuration (1),
applied to the mission's final answer. Reports keep codes, counts, timings and
answer hashes/lengths only (ADR-029); never prompts or answers.

Reached through ``scripts/fyp_benchmark.py --config phoenix-multi-agent``::

    python scripts/fyp_benchmark.py --config phoenix-multi-agent --dry-run
    python scripts/fyp_benchmark.py --config phoenix-multi-agent \
        --compare <config1-results.json> [--compare <another.json>]
    python scripts/fyp_benchmark.py --config phoenix-multi-agent \
        --from-results .fyp-bench/fyp-multi-agent-results.json --compare <c1.json>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import statistics
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
for _path in (ROOT, SCRIPTS):  # allow running from scripts/ or via importlib
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import fyp_benchmark as baseline  # noqa: E402

from phoenix.agents import prompts  # noqa: E402
from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest  # noqa: E402
from phoenix.agents.runtime import (  # noqa: E402
    MissionKind,
    MissionResult,
    PhoenixRuntime,
    RuntimeLimits,
    default_tier_table,
)
from phoenix.agents.verifier import VerificationVerdict  # noqa: E402
from phoenix.models.benchmark import (  # noqa: E402
    BenchmarkSuite,
    ResourceProbe,
    ScoreOutcome,
    ScoringMode,
    SystemResourceProbe,
)
from phoenix.models.lifecycle import ModelLifecycleManager  # noqa: E402
from phoenix.models.provider import ModelProviderError  # noqa: E402
from phoenix.models.registry import ModelCapabilityRegistry  # noqa: E402

CONFIG_ID = "phoenix-multi-agent"
TOOL = "phoenix-fyp-multi-agent"
SCHEMA_VERSION = 1
RESULTS_NAME = "fyp-multi-agent-results.json"
SUMMARY_NAME = "fyp-multi-agent-summary.md"
COMPARISON_NAME = "fyp-comparison.md"
# "triage" passes no path, so PHOENIX decides per request (PHX-105, ADR-090).
PATHS = ("triage", "complex", "direct")
# Roles served by the think=False structured provider (ADR-081 Q7, ADR-083);
# every other role uses the default provider, i.e. the model's own default
# (Qwen3.5 thinks). Recorded so the comparison states thinking honestly.
STRUCTURED_ROLES = frozenset(
    {
        "triage",
        "direct",
        "planner",
        "assignment",
        "verifier",
        "step_verifier",
        "final_verifier",
        "document_specialist",
        "document_answer",
        "document_verifier",
    }
)
MIXED_MODELS_NOTE = (
    "Configuration (2) is not one model: each role uses the PhoenixRuntime tier "
    "table (Qwen3.5 2B/4B/9B and qwen2.5-coder 7B) and thinking varies by role "
    "(structured roles, including triage and DIRECT answers, think=False; "
    "specialist and final_answer use the model default, which thinks on "
    "Qwen3.5). Its latency is whole-mission wall time: triage plus one DIRECT "
    "reply, or about 6-12 COMPLEX generations including model swaps on the 6 GB "
    "GPU; configuration (1) latency is one generation by one model."
)
ESTIMATE = (
    "a COMPLEX mission takes about 1-2 min (16 forced-COMPLEX cases: about "
    "20-35 min per run); a triaged DIRECT mission takes seconds"
)
# docs/24 metrics for configuration (2): measured (empty reason) or NOT_RUNNABLE.
METRICS: tuple[tuple[str, str], ...] = (
    ("task_success", ""),
    (
        "routing_accuracy",
        "Owned by the PHX-105 routing benchmark (scripts/routing_benchmark.py); "
        "this runner records each mission's decided path and triage outcome.",
    ),
    ("verification_catch_rate", baseline.CATCH_RATE_REASON),
    (
        "recovery_success",
        "MAHORAGA/KAIDO are not model-backed; the runtime fails explicitly with "
        "no retries (ADR-081).",
    ),
    ("memory_retrieval_accuracy", "Needs an end-to-end RAG answering flow."),
    ("research_evidence_quality", "Needs a model-backed URAHARA research flow."),
    ("computer_control_completion", "Needs model-driven tool execution."),
    ("ttft_latency", ""),
    ("ram_vram", ""),
    ("token_usage", ""),
    ("colibri_disk_io", "Colibri deep tier is not part of configuration (2)."),
    (
        "user_intervention_count",
        "The v1 task set is text-only, so no mission can need a permission "
        "decision; waiting_permission counts are still reported.",
    ),
)
MEASURED_FROM = {
    "task_success": "score_accuracy over measured missions (failed missions "
    "score as not passed) plus completed/failed/waiting/cancelled counts",
    "ttft_latency": "whole-mission latency (median/min/max); missions are not "
    "streamed, so TTFT is not observed",
    "ram_vram": "max system RAM and GPU memory sampled at each mission's start "
    "and end (boundary samples, not a continuous peak)",
    "token_usage": "sum of reported input/output tokens over every generation "
    "of every measured mission",
}
NOT_RUNNABLE_CONFIG3 = {
    "id": "phoenix-adaptive-deep",
    "fyp_configuration": "3",
    "description": "Phoenix + adaptive model routing/deep tier",
    "reason": baseline.CONFIG3_REASON,
}


@dataclass(frozen=True)
class LiveRuntime:
    runtime: PhoenixRuntime
    models_registered: tuple[str, ...]


RuntimeFactory = Callable[[RuntimeLimits], AbstractAsyncContextManager[LiveRuntime]]


def tier_models() -> tuple[str, ...]:
    table = default_tier_table()
    ordered = [ref.model_id for refs in table.roles.values() for ref in refs]
    ordered += [ref.model_id for ref in table.default]
    return tuple(dict.fromkeys(ordered))


def tier_table_json() -> dict[str, list[str]]:
    table = default_tier_table()
    rows = {role: [r.model_id for r in refs] for role, refs in table.roles.items()}
    return {**rows, "default": [ref.model_id for ref in table.default]}


@asynccontextmanager
async def ollama_runtime(
    limits: RuntimeLimits, base_url: str = baseline.OLLAMA_URL
) -> AsyncIterator[LiveRuntime]:
    """Live runtime: default + think=False providers, installed tier models only."""
    from phoenix.models.ollama import OllamaProvider

    store = ModelCapabilityRegistry()
    async with (
        OllamaProvider(base_url) as default,
        OllamaProvider(base_url, think=False) as structured,
    ):
        available: list[str] = []
        for model in tier_models():
            try:
                store.register(await default.capabilities(model))
                available.append(model)
            except ModelProviderError:
                continue  # not installed; the tier table falls back to T0
        yield LiveRuntime(
            PhoenixRuntime(
                providers=(default,),
                structured_providers=(structured,),
                capability_registry=store,
                lifecycle=ModelLifecycleManager(),
                limits=limits,
            ),
            tuple(available),
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record(
    case_id: str, category: str, mode: ScoringMode, kind: str, run_index: int
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "category": category,
        "kind": kind,
        "run_index": run_index,
        "scoring_mode": mode.value,
    }


def trial_record(
    base: dict[str, Any],
    outcome: ScoreOutcome | None,
    result: MissionResult,
    seconds: float,
) -> dict[str, Any]:
    """Codes, counts, timings and hashes only; never prompt or answer text."""
    trace = result.trace
    records = trace.generations
    verdicts = Counter(item.verdict for item in trace.verifications)
    text = result.text
    failure = result.failure_code
    responder = result.responder_error
    return {
        **base,
        "mission_kind": result.kind.value,
        "failure_code": None if failure is None else failure.value,
        "responder_error": None if responder is None else responder.value,
        "score": None if outcome is None else outcome.value,
        "mission_ms": round(seconds * 1000, 3),
        "answer_chars": None if text is None else len(text),
        "answer_sha256": None if text is None else _sha256(text),
        "prompt_set_version": trace.prompt_set_version,
        "path": None if trace.path is None else trace.path.value,
        "triage_outcome": None
        if trace.triage_outcome is None
        else trace.triage_outcome.value,
        "triage_reason": None
        if trace.triage_reason is None
        else trace.triage_reason.value,
        "states": [state.value for state in trace.states],
        "generations": len(records),
        "input_tokens": sum(r.input_tokens or 0 for r in records),
        "output_tokens": sum(r.output_tokens or 0 for r in records),
        "generations_without_usage": sum(
            r.input_tokens is None or r.output_tokens is None for r in records
        ),
        "levi_passed": verdicts[VerificationVerdict.PASSED],
        "levi_failed": verdicts[VerificationVerdict.FAILED],
        "generation_records": [r.model_dump(mode="json") for r in records],
    }


def error_record(
    base: dict[str, Any], error: Exception, seconds: float
) -> dict[str, Any]:
    """A runtime defect, recorded by type only (messages may quote content)."""
    return {
        **base,
        "mission_kind": "error",
        "failure_code": "runtime_defect",
        "responder_error": None,
        "error_type": type(error).__name__,
        "score": None,
        "mission_ms": round(seconds * 1000, 3),
        "answer_chars": None,
        "answer_sha256": None,
        "prompt_set_version": None,
        "path": None,
        "triage_outcome": None,
        "triage_reason": None,
        "states": [],
        "generations": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "generations_without_usage": 0,
        "levi_passed": 0,
        "levi_failed": 0,
        "generation_records": [],
    }


async def _sample(probe: ResourceProbe | None) -> dict[str, Any] | None:
    if probe is None:
        return None
    snapshot = await asyncio.to_thread(probe.sample)
    return {
        "ram": snapshot.system_ram_used_bytes,
        "gpu": max((g.used_mib for g in snapshot.gpus or ()), default=None),
    }


async def run_missions(
    suite: BenchmarkSuite,
    categories: dict[str, str],
    path: OrchestrationPath | None,
    warmup: int,
    runs: int,
    runtime_factory: RuntimeFactory,
    limits: RuntimeLimits,
    resource_probe: ResourceProbe | None = None,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    """Missions run one at a time, in suite order, warm-up runs first."""
    trials: list[dict[str, Any]] = []
    plan = [("warmup", i) for i in range(warmup)]
    plan += [("measured", i) for i in range(runs)]
    total = len(plan) * len(suite.cases)
    async with runtime_factory(limits) as live:
        for kind, run_index in plan:
            for case in suite.cases:
                base = _record(
                    case.case_id,
                    categories[case.case_id],
                    case.scoring.mode,
                    kind,
                    run_index,
                )
                before = await _sample(resource_probe)
                started = time.perf_counter()
                try:
                    result = await live.runtime.run(
                        PhoenixRequest(text=case.messages[-1].text), path
                    )
                except Exception as error:  # a defect: record it, keep measuring
                    seconds = time.perf_counter() - started
                    record = error_record(base, error, seconds)
                else:
                    seconds = time.perf_counter() - started
                    text = result.text
                    outcome = None if text is None else case.scoring.score(text)
                    record = trial_record(base, outcome, result, seconds)
                after = await _sample(resource_probe)
                record["resources"] = {"before": before, "after": after}
                trials.append(record)
                code = record["failure_code"]
                print(
                    f"fyp: [{len(trials)}/{total}] {kind} {case.case_id} "
                    f"{record['mission_kind']}{'/' + code if code else ''} "
                    f"score={record['score']} {seconds:.1f}s",
                    flush=True,
                )
    return trials, live.models_registered


def _stats(values: Iterable[float]) -> dict[str, float | int | None]:
    items = list(values)
    return {
        "count": len(items),
        "minimum": min(items) if items else None,
        "median": statistics.median(items) if items else None,
        "maximum": max(items) if items else None,
    }


def _resource_max(measured: list[dict[str, Any]], key: str) -> int | None:
    values = [
        sample[key]
        for trial in measured
        for sample in trial["resources"].values()
        if sample is not None and sample[key] is not None
    ]
    return max(values) if values else None


def summarize(trials: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [t for t in trials if t["kind"] == "measured"]
    kinds = Counter(t["mission_kind"] for t in measured)
    scorable = [t for t in measured if t["scoring_mode"] != ScoringMode.UNSCORED]
    passed = sum(t["score"] == ScoreOutcome.PASS for t in scorable)
    by_category: dict[str, dict[str, int]] = {}
    for trial in scorable:
        row = by_category.setdefault(trial["category"], {"passed": 0, "n": 0})
        row["n"] += 1
        row["passed"] += trial["score"] == ScoreOutcome.PASS
    failures = Counter(
        trial["failure_code"]
        + (f"/{trial['responder_error']}" if trial["responder_error"] else "")
        for trial in measured
        if trial["failure_code"]
    )
    completed = [t for t in measured if t["mission_kind"] == MissionKind.COMPLETED]
    roles: dict[str, dict[str, Any]] = {}
    models: Counter[str] = Counter()
    for trial in measured:
        for record in trial["generation_records"]:
            models[record["model_id"]] += 1
            role = roles.setdefault(
                record["role"],
                {
                    "thinking": "off"
                    if record["role"] in STRUCTURED_ROLES
                    else "model_default",
                    "generations": 0,
                    "models": Counter(),
                },
            )
            role["generations"] += 1
            role["models"][record["model_id"]] += 1
    versions = sorted({t["prompt_set_version"] for t in measured} - {None})
    decided = Counter(str(t["path"]) for t in measured)
    triage = Counter(str(t["triage_outcome"]) for t in measured)
    return {
        "measured_missions": len(measured),
        "outcomes": {
            name: kinds[name]
            for name in (*(kind.value for kind in MissionKind), "error")
        },
        "success_rate": len(completed) / len(measured) if measured else None,
        "scorable_missions": len(scorable),
        "passed_scores": passed,
        "score_accuracy": passed / len(scorable) if scorable else None,
        "category_accuracy": {
            name: {**row, "accuracy": row["passed"] / row["n"]}
            for name, row in sorted(by_category.items())
        },
        "mission_latency_ms": _stats(t["mission_ms"] for t in completed),
        "mission_latency_ms_all": _stats(t["mission_ms"] for t in measured),
        "generations_per_mission": {
            **_stats(t["generations"] for t in measured),
            "total": sum(t["generations"] for t in measured),
        },
        "input_tokens": sum(t["input_tokens"] for t in measured),
        "output_tokens": sum(t["output_tokens"] for t in measured),
        "generations_without_usage": sum(
            t["generations_without_usage"] for t in measured
        ),
        "levi": {
            "passed": sum(t["levi_passed"] for t in measured),
            "failed": sum(t["levi_failed"] for t in measured),
            "missions_with_failed_check": sum(t["levi_failed"] > 0 for t in measured),
            "per_mission": [
                {
                    "case_id": t["case_id"],
                    "run_index": t["run_index"],
                    "passed": t["levi_passed"],
                    "failed": t["levi_failed"],
                }
                for t in measured
            ],
        },
        "failure_codes": dict(sorted(failures.items())),
        "paths": dict(sorted(decided.items())),
        "triage_outcomes": dict(sorted(triage.items())),
        "prompt_set_versions": versions,
        "generations_by_model": dict(sorted(models.items())),
        "roles": {
            name: {**row, "models": dict(sorted(row["models"].items()))}
            for name, row in sorted(roles.items())
        },
        "max_ram_used_bytes": _resource_max(measured, "ram"),
        "max_gpu_used_mib": _resource_max(measured, "gpu"),
    }


def metric_rows() -> list[dict[str, str]]:
    return [
        {"id": metric, "status": "NOT_RUNNABLE", "reason": reason}
        if reason
        else {"id": metric, "status": "MEASURED", "source": MEASURED_FROM[metric]}
        for metric, reason in METRICS
    ]


# ---- comparison with configuration (1)


def load_config1(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if data.get("tool") != "phoenix-fyp-benchmark" or data.get("schema_version") != 1:
        raise ValueError(f"{path} is not a schema v1 fyp_benchmark results file")
    return data


def _per(total: int | None, count: int) -> float | None:
    return None if total is None or not count else total / count


def compare_rows(
    config2: dict[str, Any], config1_files: list[tuple[str, dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Configuration (1) tiers (best first, per file) next to configuration (2)."""
    rows: list[dict[str, Any]] = []
    suite_sha = config2["suite"]["sha256"]
    for source, data in config1_files:
        think = data.get("provider_options", {}).get("think_mode", "default")
        tiers = []
        for row in data["configurations"]["baseline"]:
            s = row["summary"]
            trials = s.get("measured_trials") or 0
            tiers.append(
                {
                    "fyp_configuration": "1",
                    "label": f"{row['tier']} {row['model_id']}",
                    "thinking": "model default" if think == "default" else think,
                    "status": row["status"],
                    "score_accuracy": s["score_accuracy"],
                    "success_rate": s["success_rate"],
                    "latency_median_ms": s["latency_ms"]["median"],
                    "input_tokens_per_task": _per(row["input_tokens"], trials),
                    "output_tokens_per_task": _per(row["output_tokens"], trials),
                    "same_task_set": data["suite"]["sha256"] == suite_sha,
                    "source": source,
                    "best": False,
                }
            )
        tiers.sort(
            key=lambda r: (
                -(r["score_accuracy"] or 0),
                r["latency_median_ms"] or float("inf"),
            )
        )
        if tiers:
            tiers[0]["best"] = True
        rows += tiers
    s2 = config2["summary"]
    n = s2["measured_missions"]
    rows.append(
        {
            "fyp_configuration": "2",
            "label": f"PHOENIX multi-agent ({config2['path']['used']})",
            "thinking": "mixed by role",
            "status": "measured",
            "score_accuracy": s2["score_accuracy"],
            "success_rate": s2["success_rate"],
            "latency_median_ms": s2["mission_latency_ms"]["median"],
            "input_tokens_per_task": _per(s2["input_tokens"], n),
            "output_tokens_per_task": _per(s2["output_tokens"], n),
            "same_task_set": True,
            "source": "configuration (2) results",
            "best": False,
        }
    )
    return rows


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


def comparison_markdown(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Config | Model(s) | Thinking | Accuracy | Success | Latency median ms "
        "| Input tok/task | Output tok/task | Same task set | Source |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        label = f"**{r['label']}** (best)" if r["best"] else r["label"]
        source = r["source"]
        if r["fyp_configuration"] == "1":  # the evidence directory names the run
            source = Path(source).parent.name or source
        lines.append(
            f"| ({r['fyp_configuration']}) | {label} | {r['thinking']} | "
            f"{_pct(r['score_accuracy'])} | {_pct(r['success_rate'])} | "
            f"{_num(r['latency_median_ms'])} | {_num(r['input_tokens_per_task'])} | "
            f"{_num(r['output_tokens_per_task'])} | "
            f"{'yes' if r['same_task_set'] else 'NO'} | {source} |"
        )
    return [
        *lines,
        "",
        f"Note: {MIXED_MODELS_NOTE} Configuration (1) thinking-on output tokens "
        "include hidden reasoning tokens (ADR-079).",
    ]


def _counts(counts: dict[str, int]) -> str:
    return ", ".join(f"{name} {n}" for name, n in counts.items()) or "n/a"


def write_summary(results: dict[str, Any], path: Path) -> None:
    s = results["summary"]
    o = s["outcomes"]
    lat = s["mission_latency_ms"]
    gens = s["generations_per_mission"]
    lines = [
        f"# PHOENIX FYP benchmark — configuration (2) {CONFIG_ID}",
        "",
        f"{results['started_utc']} → {results['finished_utc']} on "
        f"{results['host']['platform']}. Task set {results['suite']['id']} "
        f"{results['suite']['version']} sha256 `{results['suite']['sha256'][:16]}…`. "
        f"Path: {results['path']['used']}; missions by path "
        f"{_counts(s.get('paths', {}))}; triage outcomes "
        f"{_counts(s.get('triage_outcomes', {}))}."
        f" Trials: {results['trials']['warmup']} warm-up, "
        f"{results['trials']['measured']} measured. Prompt set: "
        f"{', '.join(s['prompt_set_versions']) or 'n/a'}.",
        "",
        "| Missions | Completed | Failed | Waiting | Cancelled | Error | Accuracy "
        "| Success | Latency median/min/max ms | Generations median (total) "
        "| Input tokens | Output tokens | LEVI passed/failed |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- "
        "| --- |",
        f"| {s['measured_missions']} | {o['completed']} | {o['failed']} | "
        f"{o['waiting_permission']} | {o['cancelled']} | {o['error']} | "
        f"{_pct(s['score_accuracy'])} | {_pct(s['success_rate'])} | "
        f"{_num(lat['median'])}/{_num(lat['minimum'])}/{_num(lat['maximum'])} | "
        f"{_num(gens['median'])} ({gens['total']}) | {s['input_tokens']} | "
        f"{s['output_tokens']} | {s['levi']['passed']}/{s['levi']['failed']} |",
        "",
        "## Per-category accuracy",
        "",
        "| Category | Passed | N | Accuracy |",
        "| --- | --- | --- | --- |",
    ]
    for name, row in s["category_accuracy"].items():
        lines.append(
            f"| {name} | {row['passed']} | {row['n']} | {_pct(row['accuracy'])} |"
        )
    lines += ["", "## Failure codes", ""]
    codes = s["failure_codes"].items()
    lines += [f"- `{code}`: {n}" for code, n in codes] or ["- none"]
    lines += ["", "## Generations by role", ""]
    for role, row in s["roles"].items():
        models = ", ".join(f"{m} ×{n}" for m, n in row["models"].items())
        lines.append(
            f"- {role} (thinking {row['thinking']}): {row['generations']} — {models}"
        )
    lines += ["", f"Model policy: {MIXED_MODELS_NOTE}"]
    if results.get("comparison"):
        lines += ["", "## Comparison with configuration (1)", ""]
        lines += comparison_markdown(results["comparison"])
    lines += [
        "",
        "## Configuration (3)",
        "",
        f"- NOT RUNNABLE: {NOT_RUNNABLE_CONFIG3['reason']}",
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


def _comparison(
    results: dict[str, Any], config1: list[tuple[str, dict[str, Any]]], out: Path
) -> list[dict[str, Any]]:
    rows = compare_rows(results, config1)
    text = [
        "# PHOENIX FYP comparison — configuration (1) vs (2)",
        "",
        *comparison_markdown(rows),
    ]
    (out / COMPARISON_NAME).write_text("\n".join(text) + "\n", encoding="utf-8")
    return rows


def _placements(
    probe: baseline.ComputeProbe | None, allow_cpu: bool, check: bool
) -> dict[str, Any]:
    """Ollama placement of every tier model; CPU-only refused before the run."""
    placements: dict[str, Any] = {}
    for model in tier_models():
        found = probe(model) if probe is not None else {"device": "not_probed"}
        placement = found if found is not None else {"device": "cpu"}
        placements[model] = placement
        if check and placement["device"] == "cpu" and not allow_cpu:
            raise baseline.CpuOnlyError(
                f"{model} would run on CPU only ({placement}); enable the GPU or "
                "pass --allow-cpu to record a CPU-only run deliberately"
            )
    return placements


def main(
    argv: list[str] | None = None,
    runtime_factory: RuntimeFactory | None = None,
    compute_probe: baseline.ComputeProbe | None = None,
    resource_probe: ResourceProbe | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="fyp_benchmark.py --config phoenix-multi-agent",
        description="FYP configuration (2): PHOENIX multi-agent orchestration",
    )
    parser.add_argument("--tasks", type=Path, default=baseline.DEFAULT_TASKS)
    parser.add_argument("--out", type=Path, default=baseline.DEFAULT_OUT)
    parser.add_argument(
        "--path",
        choices=PATHS,
        default="triage",
        help="triage (default: PHOENIX decides per request), or force complex "
        "(full pipeline stress test) or direct",
    )
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--base-url", default=baseline.OLLAMA_URL)
    parser.add_argument(
        "--mission-timeout",
        type=float,
        default=RuntimeLimits().mission_timeout_seconds,
        help="seconds per mission (RuntimeLimits.mission_timeout_seconds)",
    )
    parser.add_argument(
        "--compare",
        type=Path,
        action="append",
        default=[],
        help="configuration (1) fyp-benchmark-results.json (repeatable)",
    )
    parser.add_argument(
        "--from-results",
        type=Path,
        help="re-render the summary/comparison from an earlier results file",
    )
    parser.add_argument(
        "--allow-cpu", action="store_true", help="record CPU-only runs deliberately"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.warmup < 0 or args.runs < 1:
        parser.error("--warmup must be >= 0 and --runs >= 1")
    try:  # fail fast, before a 30-minute run
        config1 = [(str(p), load_config1(p)) for p in args.compare]
    except (OSError, ValueError) as error:
        parser.error(f"--compare: {error}")

    out = args.out.resolve()
    if args.from_results is not None:
        earlier: dict[str, Any] = json.loads(
            args.from_results.read_text(encoding="utf-8")
        )
        if earlier.get("tool") != TOOL or earlier.get("schema_version") != 1:
            parser.error(f"{args.from_results} is not a schema v1 {TOOL} file")
        out.mkdir(parents=True, exist_ok=True)
        if config1:
            earlier["comparison"] = _comparison(earlier, config1, out)
        write_summary(earlier, out / SUMMARY_NAME)
        print(f"fyp: re-rendered - summary in {out}")
        return 0

    suite, categories = baseline.load_task_set(args.tasks)
    used = args.path
    limits = RuntimeLimits(mission_timeout_seconds=args.mission_timeout)
    missions = len(suite.cases) * (args.warmup + args.runs)
    if args.dry_run:
        print(
            f"fyp: {CONFIG_ID}: {suite.suite_id} {suite.version}: "
            f"{len(suite.cases)} cases x ({args.warmup} warm-up + {args.runs} "
            f"measured) = {missions} missions on path {used}; prompts "
            f"{prompts.PROMPT_SET_VERSION}; models {', '.join(tier_models())}; "
            f"expected {ESTIMATE}."
        )
        return 0

    live = runtime_factory is None
    probe = compute_probe or (baseline.ollama_compute if live else None)
    sampler = resource_probe or (SystemResourceProbe() if live else None)
    started = datetime.now(UTC).isoformat()
    try:
        before = _placements(probe, args.allow_cpu, check=True)
    except baseline.CpuOnlyError as error:
        print(f"fyp: refused - {error}", file=sys.stderr)
        return 2
    factory: RuntimeFactory = runtime_factory or (
        lambda lim: ollama_runtime(lim, args.base_url)
    )
    trials, registered = asyncio.run(
        run_missions(
            suite,
            categories,
            None if used == "triage" else OrchestrationPath(used),
            args.warmup,
            args.runs,
            factory,
            limits,
            sampler,
        )
    )
    after = _placements(probe, args.allow_cpu, check=False)
    summary = summarize(trials)
    results: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "fyp_configuration": "2",
        "config": CONFIG_ID,
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(),
        "suite": {
            "id": suite.suite_id,
            "version": suite.version,
            "path": str(args.tasks),
            "sha256": hashlib.sha256(args.tasks.read_bytes()).hexdigest(),
            "cases": len(suite.cases),
        },
        "path": {
            "requested": args.path,
            "used": used,
        },
        "trials": {"warmup": args.warmup, "measured": args.runs},
        "runtime_limits": limits.model_dump(mode="json"),
        "model_policy": {
            "tier_table": tier_table_json(),
            "models_registered": list(registered),
            "structured_roles": sorted(STRUCTURED_ROLES),
            "structured_roles_think": False,
            "other_roles_think": "model default",
            "note": MIXED_MODELS_NOTE,
        },
        "compute": {
            "allow_cpu": args.allow_cpu,
            "probe": probe is not None,
            "before": before,
            "after": after,
        },
        "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "summary": summary,
        "not_runnable": [NOT_RUNNABLE_CONFIG3],
        "metrics": metric_rows(),
        "trials_detail": trials,
    }
    out.mkdir(parents=True, exist_ok=True)
    if config1:
        results["comparison"] = _comparison(results, config1, out)
    (out / RESULTS_NAME).write_text(json.dumps(results, indent=2), encoding="utf-8")
    write_summary(results, out / SUMMARY_NAME)
    print(f"fyp: done - results in {out}")
    unavailable = {"provider_unavailable", "no_eligible_model"}
    measured = [t for t in trials if t["kind"] == "measured"]
    backend_down = all(t["failure_code"] in unavailable for t in measured)
    return 1 if summary["outcomes"]["error"] or backend_down else 0


if __name__ == "__main__":
    raise SystemExit(main())
