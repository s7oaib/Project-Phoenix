"""FYP configuration (3): PHOENIX + adaptive routing to the deep tier (PHX-116).

Each v1 task first runs exactly as configuration (2): ``PhoenixRuntime`` on the
local Ollama tiers, triaged by PHOENIX (PHX-105). The deep tier (Qwen3.6-35B-A3B
served by Colibri, docs/05 T3) is used only when that first attempt shows,
through PHOENIX's own closed runtime evidence, that the fast tiers were not good
enough (ADR-121):

- the mission failed with ``verification_failed`` (LEVI rejected the answer) or
  ``responder_output`` (unusable model output after MAHORAGA's retry); or
- it completed only through an unverified DIRECT fallback (PHX-109).

Nothing else escalates: refusals, permission outcomes, tool results,
cancellation, timeouts, provider or routing failures and runtime defects stay
the first attempt's result (INV-008, INV-021). The score is never consulted.
Each request escalates at most once.

The escalated attempt runs the full COMPLEX pipeline with Colibri first in the
tier rows of the text specialists and the final answer; triage, AIZEN,
assignment and LEVI stay on Ollama with thinking off, so LEVI verifies the deep
answer with a different model. Both runtimes share one lifecycle manager: one
heavy generation at a time (INV-023). Reports keep codes, counts, timings and
hashes only (ADR-029).

Reached through ``scripts/fyp_benchmark.py --config phoenix-adaptive-deep``::

    python scripts/fyp_benchmark.py --config phoenix-adaptive-deep --dry-run
    python scripts/fyp_benchmark.py --config phoenix-adaptive-deep \
        --colibri-url http://127.0.0.1:8000/v1 --deep-model qwen3.6-colibri

PHOENIX does not start Colibri (ADR-028); the operator runs ``coli serve``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
for _path in (ROOT, SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import fyp_benchmark as baseline  # noqa: E402
import fyp_multi_agent as multi  # noqa: E402

from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest  # noqa: E402
from phoenix.agents.runtime import (  # noqa: E402
    MissionKind,
    ModelCandidateRef,
    ModelTierTable,
    PhoenixRuntime,
    RuntimeLimits,
    default_tier_table,
)
from phoenix.models.benchmark import (  # noqa: E402
    BenchmarkSuite,
    ResourceProbe,
    ScoreOutcome,
    SystemResourceProbe,
)
from phoenix.models.lifecycle import ModelLifecycleManager  # noqa: E402
from phoenix.models.provider import (  # noqa: E402
    ModelProviderError,
    ProviderHealthStatus,
)
from phoenix.models.registry import ModelCapabilityRegistry  # noqa: E402

CONFIG_ID = "phoenix-adaptive-deep"
TOOL = "phoenix-fyp-adaptive-deep"
SCHEMA_VERSION = 1
RESULTS_NAME = "fyp-adaptive-deep-results.json"
SUMMARY_NAME = "fyp-adaptive-deep-summary.md"
DEEP_PROVIDER = "colibri"
DEFAULT_COLIBRI_URL = "http://127.0.0.1:8000/v1"
DEFAULT_DEEP_MODEL = "qwen3.6-colibri"
# ADR-121: the only first-attempt outcomes that escalate to the deep tier.
ESCALATE_FAILURES = frozenset({"verification_failed", "responder_output"})
ESCALATE_FALLBACK = "unverified_fallback"
POLICY = (
    "Escalate once to the deep tier (COMPLEX, Colibri first for text specialists "
    "and the final answer) when the first attempt failed with "
    f"{' or '.join(sorted(ESCALATE_FAILURES))}, or completed only through an "
    "unverified DIRECT fallback. Never on refusals, permissions, tools, "
    "cancellation, timeouts, provider/routing failures or defects; never on "
    "the score."
)
METRIC_NOTES = {
    "colibri_disk_io": "Measured by scripts/colibri_gate.py --live (engine "
    "process disk reads); this runner records deep-tier generations.",
}


def escalation_reason(record: dict[str, Any]) -> str | None:
    """Why a first attempt escalates, from closed runtime values only."""
    kind = record.get("mission_kind")
    if kind == MissionKind.COMPLETED:
        return (
            ESCALATE_FALLBACK
            if multi.mission_class(record) == "direct_fallback"
            else None
        )
    if kind == MissionKind.FAILED and record.get("failure_code") in ESCALATE_FAILURES:
        return str(record["failure_code"])
    return None


def deep_tier_table(deep_model: str) -> ModelTierTable:
    """The default table with the deep tier first wherever text answers route."""
    base = default_tier_table()
    deep = ModelCandidateRef(provider_id=DEEP_PROVIDER, model_id=deep_model)
    roles = dict(base.roles)
    roles["build_engineer"] = (deep, *base.roles["build_engineer"])
    return ModelTierTable(roles=MappingProxyType(roles), default=(deep, *base.default))


@dataclass(frozen=True)
class AdaptiveRuntimes:
    first: PhoenixRuntime
    deep: PhoenixRuntime
    deep_model: str
    models_registered: tuple[str, ...]


RuntimeFactory = Callable[
    [RuntimeLimits, RuntimeLimits], AbstractAsyncContextManager[AdaptiveRuntimes]
]


class DeepTierUnavailableError(RuntimeError):
    """Colibri is not serving the deep model, so configuration (3) cannot run."""


@asynccontextmanager
async def live_runtimes(
    limits: RuntimeLimits,
    deep_limits: RuntimeLimits,
    base_url: str,
    colibri_url: str,
    deep_model: str,
) -> AsyncIterator[AdaptiveRuntimes]:
    from phoenix.models.colibri import ColibriProvider
    from phoenix.models.ollama import OllamaProvider

    store = ModelCapabilityRegistry()
    lifecycle = ModelLifecycleManager()  # shared: one heavy generation (INV-023)
    async with (
        OllamaProvider(base_url) as default,
        OllamaProvider(base_url, think=False) as structured,
        ColibriProvider(colibri_url) as deep,
    ):
        if (await deep.health()).status != ProviderHealthStatus.HEALTHY:
            raise DeepTierUnavailableError(f"Colibri not healthy at {colibri_url}")
        try:
            store.register(await deep.capabilities(deep_model))
        except ModelProviderError:
            raise DeepTierUnavailableError(f"{deep_model} is not served") from None
        available = [deep_model]
        for model in multi.tier_models():
            try:
                store.register(await default.capabilities(model))
                available.append(model)
            except ModelProviderError:
                continue
        common: dict[str, Any] = {
            "structured_providers": (structured,),
            "capability_registry": store,
            "lifecycle": lifecycle,
        }
        yield AdaptiveRuntimes(
            first=PhoenixRuntime(providers=(default,), limits=limits, **common),
            deep=PhoenixRuntime(
                providers=(default, deep),
                tier_table=deep_tier_table(deep_model),
                limits=deep_limits,
                **common,
            ),
            deep_model=deep_model,
            models_registered=tuple(available),
        )


async def _attempt(
    runtime: PhoenixRuntime,
    text: str,
    path: OrchestrationPath | None,
    base: dict[str, Any],
    scoring: Any,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = await runtime.run(PhoenixRequest(text=text), path)
    except Exception as error:  # a defect: record it, keep measuring
        return multi.error_record(base, error, time.perf_counter() - started)
    seconds = time.perf_counter() - started
    outcome = None if result.text is None else scoring.score(result.text)
    return multi.trial_record(base, outcome, result, seconds)


def _deep_generations(record: dict[str, Any]) -> int:
    return sum(
        r.get("provider_id") == DEEP_PROVIDER for r in record["generation_records"]
    )


def _brief(record: dict[str, Any]) -> dict[str, Any]:
    keys = ("mission_kind", "failure_code", "responder_error", "path", "score")
    return {key: record.get(key) for key in keys} | {
        "mission_ms": record["mission_ms"],
        "verified": record.get("verified", False),
    }


async def run_adaptive(
    suite: BenchmarkSuite,
    categories: dict[str, str],
    path: OrchestrationPath | None,
    warmup: int,
    runs: int,
    runtime_factory: RuntimeFactory,
    limits: RuntimeLimits,
    deep_limits: RuntimeLimits,
    resource_probe: ResourceProbe | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[str, ...]]:
    """Returns (final trials, first-attempt trials, registered models)."""
    finals: list[dict[str, Any]] = []
    firsts: list[dict[str, Any]] = []
    plan = [("warmup", i) for i in range(warmup)]
    plan += [("measured", i) for i in range(runs)]
    total = len(plan) * len(suite.cases)
    async with runtime_factory(limits, deep_limits) as live:
        for kind, run_index in plan:
            for case in suite.cases:
                base = multi._record(
                    case.case_id,
                    categories[case.case_id],
                    case.scoring.mode,
                    kind,
                    run_index,
                )
                text = case.messages[-1].text
                before = await multi._sample(resource_probe)
                first = await _attempt(live.first, text, path, base, case.scoring)
                reason = escalation_reason(first)
                final = first
                if reason is not None:
                    deep = await _attempt(
                        live.deep,
                        text,
                        OrchestrationPath.COMPLEX,
                        base,
                        case.scoring,
                    )
                    final = {
                        **deep,
                        "mission_ms": round(
                            first["mission_ms"] + deep["mission_ms"], 3
                        ),
                        "deep_attempt_ms": deep["mission_ms"],
                    }
                after = await multi._sample(resource_probe)
                deep_gens = _deep_generations(final) if reason else 0
                final["escalation"] = {
                    "reason": reason,
                    "escalated": reason is not None,
                    "deep_generations": deep_gens,
                    "deep_used": deep_gens > 0,
                    "first_attempt": _brief(first),
                }
                final["resources"] = {"before": before, "after": after}
                first["resources"] = final["resources"]
                finals.append(final)
                firsts.append(first)
                code = final["failure_code"]
                print(
                    f"fyp: [{len(finals)}/{total}] {kind} {case.case_id} "
                    f"{'deep(' + reason + ') ' if reason else ''}"
                    f"{final['mission_kind']}{'/' + code if code else ''} "
                    f"score={final['score']} {final['mission_ms'] / 1000:.1f}s",
                    flush=True,
                )
    return finals, firsts, live.models_registered


def escalation_summary(finals: list[dict[str, Any]]) -> dict[str, Any]:
    measured = [t for t in finals if t["kind"] == "measured"]
    escalated = [t for t in measured if t["escalation"]["escalated"]]
    used = [t for t in escalated if t["escalation"]["deep_used"]]
    rescued = [t for t in escalated if t["mission_kind"] == MissionKind.COMPLETED]
    return {
        "measured_missions": len(measured),
        "escalated": len(escalated),
        "escalation_rate": len(escalated) / len(measured) if measured else None,
        "by_reason": dict(
            sorted(Counter(t["escalation"]["reason"] for t in escalated).items())
        ),
        "deep_used": len(used),
        "deep_not_used": len(escalated) - len(used),
        "deep_generations": sum(t["escalation"]["deep_generations"] for t in measured),
        "rescued_completed": len(rescued),
        "rescued_levi_verified": sum(
            multi.mission_class(t) == "levi_verified" for t in rescued
        ),
        "rescued_passes": sum(t["score"] == ScoreOutcome.PASS for t in rescued),
        "deep_attempt_ms": multi._stats(
            t["deep_attempt_ms"] for t in escalated if "deep_attempt_ms" in t
        ),
    }


def metric_rows(summary: dict[str, Any]) -> list[dict[str, str]]:
    rows = multi.metric_rows(summary)
    for row in rows:
        if row["id"] == "colibri_disk_io":
            row["reason"] = METRIC_NOTES["colibri_disk_io"]
        if row["id"] == "routing_accuracy":
            row["reason"] = (
                "Triage accuracy is owned by PHX-105; escalations are counted "
                "by reason in summary.escalation."
            )
    return rows


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def _num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


def write_summary(results: dict[str, Any], path: Path) -> None:
    final, first, esc = (
        results["summary"],
        results["first_attempt_summary"],
        results["summary"]["escalation"],
    )
    lines = [
        f"# PHOENIX FYP benchmark — configuration (3) {CONFIG_ID}",
        "",
        f"{results['started_utc']} → {results['finished_utc']} on "
        f"{results['host']['platform']}. Task set {results['suite']['id']} "
        f"{results['suite']['version']} sha256 `{results['suite']['sha256'][:16]}…`. "
        f"Deep tier `{results['deep_tier']['model']}` on "
        f"`{results['deep_tier']['url']}`. First-attempt path: "
        f"{results['path']['used']}.",
        "",
        f"Policy: {POLICY}",
        "",
        "| | Success excl. fallbacks | Accuracy | Latency median ms "
        "| LEVI-verified passes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, s in (
        ("First attempt = configuration (2), same run", first),
        ("Configuration (3), after escalation", final),
    ):
        lines.append(
            f"| {label} | {_pct(s['success_rate_excl_fallbacks'])} | "
            f"{_pct(s['score_accuracy'])} | "
            f"{_num(s['mission_latency_ms']['median'])} | "
            f"{s['levi_verified_passes']} |"
        )
    lines += [
        "",
        f"- Escalated {esc['escalated']} of {esc['measured_missions']} "
        f"({_pct(esc['escalation_rate'])}); by reason "
        f"{multi._counts(esc['by_reason'])}.",
        f"- Deep tier used in {esc['deep_used']} ({esc['deep_not_used']} escalations "
        f"fell back to Ollama); {esc['deep_generations']} deep generations.",
        f"- Rescued: {esc['rescued_completed']} completed, "
        f"{esc['rescued_levi_verified']} LEVI-verified, {esc['rescued_passes']} "
        f"scored passes; deep attempt median "
        f"{_num(esc['deep_attempt_ms']['median'])} ms.",
        "",
        *multi._verification_lines(final),
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


def main(
    argv: list[str] | None = None,
    runtime_factory: RuntimeFactory | None = None,
    compute_probe: baseline.ComputeProbe | None = None,
    resource_probe: ResourceProbe | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="fyp_benchmark.py --config phoenix-adaptive-deep",
        description="FYP configuration (3): PHOENIX + adaptive deep-tier routing",
    )
    parser.add_argument("--tasks", type=Path, default=baseline.DEFAULT_TASKS)
    parser.add_argument("--out", type=Path, default=baseline.DEFAULT_OUT)
    parser.add_argument("--path", choices=multi.PATHS, default="triage")
    parser.add_argument("--warmup", type=int, default=0)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--base-url", default=baseline.OLLAMA_URL)
    parser.add_argument("--colibri-url", default=DEFAULT_COLIBRI_URL)
    parser.add_argument("--deep-model", default=DEFAULT_DEEP_MODEL)
    parser.add_argument(
        "--mission-timeout",
        type=float,
        default=RuntimeLimits().mission_timeout_seconds,
    )
    # The deep tier decodes at a few tokens per second on BLAZE-6.
    parser.add_argument("--deep-step-timeout", type=float, default=1200)
    parser.add_argument("--deep-mission-timeout", type=float, default=3600)
    parser.add_argument("--allow-cpu", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.warmup < 0 or args.runs < 1:
        parser.error("--warmup must be >= 0 and --runs >= 1")
    suite, categories = baseline.load_task_set(args.tasks)
    limits = RuntimeLimits(mission_timeout_seconds=args.mission_timeout)
    deep_limits = RuntimeLimits(
        mission_timeout_seconds=args.deep_mission_timeout,
        step_timeout_seconds=args.deep_step_timeout,
        verification_timeout_seconds=args.deep_step_timeout,
    )
    if args.dry_run:
        print(
            f"fyp: {CONFIG_ID}: {suite.suite_id} {suite.version}: "
            f"{len(suite.cases)} cases x ({args.warmup} warm-up + {args.runs} "
            f"measured) on path {args.path}; deep tier {args.deep_model} at "
            f"{args.colibri_url}; policy: {POLICY}"
        )
        return 0

    live = runtime_factory is None
    probe = compute_probe or (baseline.ollama_compute if live else None)
    sampler = resource_probe or (SystemResourceProbe() if live else None)
    started = datetime.now(UTC).isoformat()
    try:
        before = multi._placements(probe, args.allow_cpu, check=True)
    except baseline.CpuOnlyError as error:
        print(f"fyp: refused - {error}", file=sys.stderr)
        return 2
    factory: RuntimeFactory = runtime_factory or (
        lambda lim, deep_lim: live_runtimes(
            lim, deep_lim, args.base_url, args.colibri_url, args.deep_model
        )
    )
    try:
        finals, firsts, registered = asyncio.run(
            run_adaptive(
                suite,
                categories,
                None if args.path == "triage" else OrchestrationPath(args.path),
                args.warmup,
                args.runs,
                factory,
                limits,
                deep_limits,
                sampler,
            )
        )
    except DeepTierUnavailableError as error:
        print(f"fyp: refused - {error}; start `coli serve` first", file=sys.stderr)
        return 2
    after = multi._placements(probe, args.allow_cpu, check=False)
    summary = multi.summarize(finals)
    summary["escalation"] = escalation_summary(finals)
    results: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "fyp_configuration": "3",
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
        "path": {"requested": args.path, "used": args.path},
        "trials": {"warmup": args.warmup, "measured": args.runs},
        "runtime_limits": limits.model_dump(mode="json"),
        "deep_runtime_limits": deep_limits.model_dump(mode="json"),
        "deep_tier": {
            "provider": DEEP_PROVIDER,
            "url": args.colibri_url,
            "model": args.deep_model,
            "policy": POLICY,
            "escalate_failures": sorted(ESCALATE_FAILURES),
        },
        "model_policy": {
            "first_tier_table": multi.tier_table_json(),
            "models_registered": list(registered),
            "note": multi.MIXED_MODELS_NOTE,
        },
        "compute": {"allow_cpu": args.allow_cpu, "before": before, "after": after},
        "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "summary": summary,
        "first_attempt_summary": multi.summarize(firsts),
        "metrics": metric_rows(summary),
        "trials_detail": finals,
    }
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / RESULTS_NAME).write_text(json.dumps(results, indent=2), encoding="utf-8")
    write_summary(results, out / SUMMARY_NAME)
    print(f"fyp: done - results in {out}")
    unavailable = {"provider_unavailable", "no_eligible_model"}
    measured = [t for t in finals if t["kind"] == "measured"]
    backend_down = all(t["failure_code"] in unavailable for t in measured)
    return 1 if summary["outcomes"]["error"] or backend_down else 0


if __name__ == "__main__":
    raise SystemExit(main())
