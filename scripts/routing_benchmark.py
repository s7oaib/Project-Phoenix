"""PHOENIX request-triage routing benchmark (PHX-105, ADR-090).

Runs PHOENIX's triage responder alone (``ModelTriageResponder``, the
``TRIAGE_SYSTEM`` prompt, one structured thinking-off generation per case)
against the fixed, versioned routing set ``benchmarks/routing/routing-v1.toml``
and reports the docs/24 ``routing_accuracy`` metric: accuracy, a confusion
matrix, per-category accuracy, latency and the fallback count. A triage
failure counts as the product's fallback, COMPLEX, and is reported with its
closed reason. Results are schema-versioned JSON plus a Markdown summary in
the gitignored ``.routing-bench/``; they hold IDs, labels, codes, timings and
output hashes/lengths only, never model output.

Usage::

    python scripts/routing_benchmark.py --dry-run     # validate the set, no models
    python scripts/routing_benchmark.py               # qwen3.5:4b, think off (Ollama)
    python scripts/routing_benchmark.py --model qwen3.5:2b
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import re
import statistics
import sys
import time
import tomllib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # allow `python scripts/routing_benchmark.py`
    sys.path.insert(0, str(ROOT))

from phoenix.agents import prompts  # noqa: E402
from phoenix.agents.orchestrator import PhoenixRequest  # noqa: E402
from phoenix.agents.responders import GenerationRecord, ModelBinding  # noqa: E402
from phoenix.agents.runtime import RuntimeLimits  # noqa: E402
from phoenix.agents.triage import ModelTriageResponder, fallback_reason  # noqa: E402
from phoenix.models.lifecycle import ModelLifecycleManager  # noqa: E402
from phoenix.models.provider import ModelProvider  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_SET = ROOT / "benchmarks" / "routing" / "routing-v1.toml"
DEFAULT_OUT = ROOT / ".routing-bench"
# The triage tier's first choice (T1); T0 qwen3.5:2b is its fallback.
DEFAULT_MODEL = "qwen3.5:4b"
LABELS = ("direct", "complex")
CATEGORIES = (
    "factual",
    "rewrite_format",
    "workspace_files",
    "multi_step",
    "ambiguous",
    "adversarial",
)
CASE_KEYS = {"id", "category", "label", "request", "rationale"}
MAX_REQUEST_CHARS = 1000
WARMUP_REQUEST = "Say hello."
GENERATION_FIELDS = {"finish_reason", "output_tokens", "output_chars", "output_sha256"}
_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")

ProviderFactory = Callable[[], AbstractAsyncContextManager[ModelProvider]]
# Where Ollama placed the model, from /api/ps; None when it is not loaded.
ComputeProbe = Callable[[str], dict[str, Any] | None]
OLLAMA_URL = "http://127.0.0.1:11434"


@dataclass(frozen=True)
class RoutingCase:
    case_id: str
    category: str
    label: str
    request: str
    rationale: str


@dataclass(frozen=True)
class RoutingSet:
    suite_id: str
    version: str
    cases: tuple[RoutingCase, ...]


class CpuOnlyError(RuntimeError):
    """The model would run without GPU offload and --allow-cpu was not given."""


def load_routing_set(path: Path) -> RoutingSet:
    """Return the validated set; raises ValueError on any invalid entry."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(data) != {"suite_id", "version", "case"}:
        raise ValueError("routing set needs exactly suite_id, version and case")
    suite_id, version = data["suite_id"], data["version"]
    if not isinstance(suite_id, str) or not suite_id:
        raise ValueError("suite_id must be a non-empty string")
    match = _VERSION.fullmatch(version) if isinstance(version, str) else None
    if match is None:
        raise ValueError("version must be MAJOR.MINOR.PATCH")
    if path.name != f"routing-v{match.group(1)}.toml":
        raise ValueError("file name must match the major version")
    cases: list[RoutingCase] = []
    for raw in data["case"]:
        if not isinstance(raw, dict) or set(raw) != CASE_KEYS:
            raise ValueError(f"case needs exactly {sorted(CASE_KEYS)}")
        if not all(isinstance(raw[key], str) for key in CASE_KEYS):
            raise ValueError("case fields must be strings")
        case = RoutingCase(
            case_id=raw["id"],
            category=raw["category"],
            label=raw["label"],
            request=raw["request"],
            rationale=raw["rationale"],
        )
        if _ID.fullmatch(case.case_id) is None:
            raise ValueError(f"invalid case id {case.case_id!r}")
        if case.category not in CATEGORIES:
            raise ValueError(f"unknown category in {case.case_id}")
        if case.label not in LABELS:
            raise ValueError(f"unknown label in {case.case_id}")
        for text, limit in ((case.request, MAX_REQUEST_CHARS), (case.rationale, 200)):
            if not text.strip() or "\n" in text or len(text) > limit:
                raise ValueError(f"text must be one bounded line in {case.case_id}")
        cases.append(case)
    ids = [case.case_id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate case id")
    if {case.label for case in cases} != set(LABELS):
        raise ValueError("both labels must be represented")
    return RoutingSet(suite_id=suite_id, version=version, cases=tuple(cases))


def composition(routing: RoutingSet) -> dict[str, dict[str, int]]:
    """Counts per category and label (categories in their fixed order)."""
    counts: dict[str, dict[str, int]] = {
        category: dict.fromkeys(LABELS, 0) for category in CATEGORIES
    }
    for case in routing.cases:
        counts[case.category][case.label] += 1
    return {name: row for name, row in counts.items() if sum(row.values())}


async def run_cases(
    routing: RoutingSet,
    provider: ModelProvider,
    model_id: str,
    *,
    limits: RuntimeLimits,
    warmup: int = 1,
) -> list[dict[str, Any]]:
    """One triage generation per case, exactly as the runtime makes it."""
    manager = ModelLifecycleManager()
    records: list[GenerationRecord] = []
    responder = ModelTriageResponder(
        ModelBinding(
            agent_id="phoenix",
            provider=provider,
            model_id=model_id,
            lifecycle=manager,
            max_output_tokens=limits.max_output_tokens,
            max_output_chars=limits.max_output_chars,
            record=records.append,
        )
    )
    for _ in range(warmup):
        try:
            async with asyncio.timeout(limits.triage_timeout_seconds):
                await responder.classify(PhoenixRequest(text=WARMUP_REQUEST))
        except Exception:
            pass  # warm-up only loads the model; its outcome is not scored
    rows: list[dict[str, Any]] = []
    for case in routing.cases:
        records.clear()
        started = time.perf_counter()
        try:
            async with asyncio.timeout(limits.triage_timeout_seconds):
                path = await responder.classify(PhoenixRequest(text=case.request))
            predicted, outcome, reason = path.value, "decided", None
        except Exception as error:
            # The product's fallback (ADR-090): COMPLEX, with a closed reason.
            predicted, outcome = "complex", "fallback"
            reason = fallback_reason(error).value
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        # Hashes, lengths and counts only (ADR-029); never the output itself.
        generation = (
            records[-1].model_dump(mode="json", include=GENERATION_FIELDS)
            if records
            else dict.fromkeys(GENERATION_FIELDS)
        )
        rows.append(
            {
                "id": case.case_id,
                "category": case.category,
                "label": case.label,
                "predicted": predicted,
                "correct": predicted == case.label,
                "outcome": outcome,
                "reason": reason,
                "latency_ms": latency_ms,
                **generation,
            }
        )
        print(
            f"routing: {case.case_id} {case.label} -> {predicted} ({outcome})",
            flush=True,
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    confusion = {label: dict.fromkeys(LABELS, 0) for label in LABELS}
    categories: dict[str, dict[str, int]] = {}
    reasons: dict[str, int] = {}
    for row in rows:
        confusion[row["label"]][row["predicted"]] += 1
        item = categories.setdefault(row["category"], {"correct": 0, "n": 0})
        item["n"] += 1
        item["correct"] += row["correct"]
        if row["reason"] is not None:
            reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1
    correct = sum(row["correct"] for row in rows)
    decided = [row for row in rows if row["outcome"] == "decided"]
    latencies = sorted(row["latency_ms"] for row in rows)
    return {
        "total": len(rows),
        "correct": correct,
        "accuracy": correct / len(rows) if rows else None,
        # Accuracy over the cases triage itself decided (fallbacks excluded).
        "decided_accuracy": sum(row["correct"] for row in decided) / len(decided)
        if decided
        else None,
        "fallbacks": len(rows) - len(decided),
        "fallback_reasons": dict(sorted(reasons.items())),
        "confusion": confusion,  # label -> predicted -> count
        "per_category": {
            name: {**item, "accuracy": item["correct"] / item["n"]}
            for name, item in categories.items()
        },
        "latency_ms": {
            "median": statistics.median(latencies) if latencies else None,
            # Nearest rank.
            "p95": latencies[math.ceil(0.95 * len(latencies)) - 1]
            if latencies
            else None,
            "min": latencies[0] if latencies else None,
            "max": latencies[-1] if latencies else None,
        },
    }


def compute_from_ps(entry: dict[str, Any]) -> dict[str, Any]:
    size = int(entry.get("size") or 0)
    vram = int(entry.get("size_vram") or 0)
    device = "gpu" if size and vram >= size else "partial" if vram else "cpu"
    return {
        "device": device,
        "size_bytes": size,
        "size_vram_bytes": vram,
        "gpu_offload_fraction": round(vram / size, 4) if size else 0.0,
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


def _ollama_factory() -> AbstractAsyncContextManager[ModelProvider]:
    from phoenix.models.ollama import OllamaProvider

    # Triage runs on the structured (thinking off) providers (ADR-080/081).
    return OllamaProvider(think=False)


async def _measure(
    routing: RoutingSet,
    model_id: str,
    factory: ProviderFactory,
    limits: RuntimeLimits,
    warmup: int,
) -> list[dict[str, Any]]:
    async with factory() as provider:
        return await run_cases(
            routing, provider, model_id, limits=limits, warmup=warmup
        )


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def write_summary(results: dict[str, Any], path: Path) -> None:
    summary = results["summary"]
    suite = results["suite"]
    latency = summary["latency_ms"]
    lines = [
        f"# PHOENIX routing benchmark — {suite['id']} {suite['version']}",
        "",
        f"{results['started_utc']} → {results['finished_utc']} on "
        f"{results['host']['platform']}. Set sha256 `{suite['sha256'][:16]}…`. "
        f"Model {results['model']['model_id']} (think off, device "
        f"{results['compute'].get('device', 'not_probed')}); prompts "
        f"{results['prompt_set_version']}.",
        "",
        f"- **Accuracy:** {_pct(summary['accuracy'])} "
        f"({summary['correct']}/{summary['total']}; fallbacks count as complex)",
        f"- **Accuracy where triage decided:** {_pct(summary['decided_accuracy'])}",
        f"- **Fallbacks:** {summary['fallbacks']} {summary['fallback_reasons'] or ''}",
        f"- **Latency ms:** median {latency['median']}, p95 {latency['p95']}, "
        f"min {latency['min']}, max {latency['max']}",
        "",
        "## Confusion matrix (rows: label, columns: predicted)",
        "",
        "| Label | direct | complex |",
        "| --- | --- | --- |",
    ]
    for label in LABELS:
        row = summary["confusion"][label]
        lines.append(f"| {label} | {row['direct']} | {row['complex']} |")
    lines += ["", "## Per category", "", "| Category | Correct | Accuracy |"]
    lines.append("| --- | --- | --- |")
    for name, item in summary["per_category"].items():
        lines.append(
            f"| {name} | {item['correct']}/{item['n']} | {_pct(item['accuracy'])} |"
        )
    wrong = [row for row in results["cases"] if not row["correct"]]
    if wrong:
        lines += ["", "## Misrouted cases", ""]
        lines += [
            f"- `{row['id']}`: {row['label']} → {row['predicted']} ({row['outcome']}"
            + (f", {row['reason']})" if row["reason"] else ")")
            for row in wrong
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(
    argv: list[str] | None = None,
    provider_factory: ProviderFactory | None = None,
    compute_probe: ComputeProbe | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="PHOENIX routing benchmark")
    parser.add_argument("--set", type=Path, default=DEFAULT_SET, dest="routing_set")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument(
        "--allow-cpu", action="store_true", help="record a CPU-only run deliberately"
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    routing = load_routing_set(args.routing_set)
    counts = composition(routing)
    if args.dry_run:
        labels = {label: sum(row[label] for row in counts.values()) for label in LABELS}
        print(
            f"routing: {routing.suite_id} {routing.version}: {len(routing.cases)} "
            f"cases ({', '.join(f'{k}={v}' for k, v in labels.items())}); "
            f"model {args.model}; prompts {prompts.PROMPT_SET_VERSION}"
        )
        for name, row in counts.items():
            print(f"routing:   {name}: direct={row['direct']} complex={row['complex']}")
        return 0

    limits = RuntimeLimits()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat()
    # Live runs always probe placement; injected providers (tests) only if given.
    probe = compute_probe or (ollama_compute if provider_factory is None else None)
    compute: dict[str, Any] = {"device": "not_probed"}
    if probe is not None:
        compute = probe(args.model) or {"device": "cpu", "reason": "not loaded"}
        if compute["device"] == "cpu" and not args.allow_cpu:
            print(
                f"routing: refused - {args.model} would run on CPU only ({compute}); "
                "enable the GPU or pass --allow-cpu",
                file=sys.stderr,
            )
            return 2
    rows = asyncio.run(
        _measure(
            routing,
            args.model,
            provider_factory or _ollama_factory,
            limits,
            args.warmup,
        )
    )
    results: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": "phoenix-routing-benchmark",
        "metric": "routing_accuracy",
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(),
        "suite": {
            "id": routing.suite_id,
            "version": routing.version,
            "path": str(args.routing_set),
            "sha256": hashlib.sha256(args.routing_set.read_bytes()).hexdigest(),
            "cases": len(routing.cases),
            "composition": counts,
        },
        "model": {"provider_id": "ollama", "model_id": args.model, "think": False},
        "prompt_set_version": prompts.PROMPT_SET_VERSION,
        "config": {
            "warmup": args.warmup,
            "timeout_seconds": limits.triage_timeout_seconds,
            "max_output_tokens": limits.max_output_tokens,
        },
        "compute": compute,
        "compute_policy": {"allow_cpu": args.allow_cpu, "probe": probe is not None},
        "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "summary": summarize(rows),
        "cases": rows,
    }
    (out / "routing-benchmark-results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    write_summary(results, out / "routing-benchmark-summary.md")
    summary = results["summary"]
    print(f"routing: done - accuracy {_pct(summary['accuracy'])}; results in {out}")
    # A run where triage never decided measured nothing but the fallback.
    return 1 if summary["fallbacks"] == summary["total"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
