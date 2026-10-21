"""Colibri deep-tier benchmark gate (PHX-116, ADR-120).

docs/05 says the optional deep tier (Qwen3.6-35B-A3B on Colibri) must be
benchmarked on the actual laptop for RAM, VRAM, NVMe I/O, TTFT, throughput and
quality before PHOENIX may route to it. This script measures exactly that and
returns a PASS / FAIL / INCOMPLETE verdict with every number it used. A FAIL is
a valid FYP outcome: configuration (3) is then reported as measured and
rejected, with these numbers.

PHOENIX never installs, starts or stops Colibri (ADR-028). The operator starts
``coli serve`` on its own port; this script only connects to it through the
unchanged ``ColibriProvider`` and the unchanged PHX-016 ``BenchmarkRunner``.

Phases (each opt-in except the always-recorded host facts)::

    python scripts/colibri_gate.py --dry-run                 # plan only
    python scripts/colibri_gate.py --verify-manifest --io-bench   # disk only
    python scripts/colibri_gate.py --live                    # needs coli serve

``--verify-manifest`` hashes the downloaded container against
``benchmarks/colibri/<container>.sha256``. ``--io-bench`` reads container
shards sequentially (no GPU, no model load). ``--live`` runs the fixed FYP v1
task set through the served model and loads the GPU: run it only when the GPU
is free. Reports keep hashes/lengths only (ADR-029).
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
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
for _path in (ROOT, SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import fyp_benchmark as baseline  # noqa: E402

from phoenix.models.benchmark import (  # noqa: E402
    BenchmarkConfig,
    BenchmarkReport,
    BenchmarkRunner,
    ResourceProbe,
    SystemResourceProbe,
    TrialKind,
    TrialStatus,
)
from phoenix.models.lifecycle import ModelLifecycleManager  # noqa: E402
from phoenix.models.provider import (  # noqa: E402
    ModelProvider,
    ModelProviderError,
    ProviderHealthStatus,
)

TOOL = "phoenix-colibri-gate"
SCHEMA_VERSION = 1
RESULTS_NAME = "colibri-gate-results.json"
SUMMARY_NAME = "colibri-gate-summary.md"
# Colibri's own default; PHOENIX's backend takes an OS-assigned port (ADR-084).
DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_MODEL_ID = "qwen3.6-colibri"
CONTAINER = "qwen36-35b-a3b-colibri-i4-gs64"
DEFAULT_MODEL_DIR = Path(r"D:\Phoenix\models\colibri") / CONTAINER
DEFAULT_MANIFEST = ROOT / "benchmarks" / "colibri" / f"{CONTAINER}.sha256"
# The Colibri engine binary that serves Qwen3.6 (release v2.0.0 names).
ENGINE_PROCESSES = frozenset({"qwen36.exe", "qwen36"})
GIB = 1024**3
MIB = 1024**2
VERDICTS = ("PASS", "FAIL", "INCOMPLETE")


@dataclass(frozen=True)
class Thresholds:
    """ADR-120 gate criteria; every one is reported with its measured value."""

    min_success_rate: float = 0.9
    min_accuracy: float = 0.5
    max_ttft_median_ms: float = 60_000
    min_tokens_per_second: float = 2.0
    min_ram_headroom_bytes: int = int(1.5 * GIB)
    max_vram_fraction: float = 0.98
    # A floor above SATA's ~550 MB/s; the disk's real cost shows in TTFT/tok/s.
    min_read_mb_per_s: float = 600


# ---- host facts and the disk-only phases (no model load, no GPU work)


def host_facts(model_dir: Path, probe: ResourceProbe | None = None) -> dict[str, Any]:
    """RAM, free disk and GPU memory; the GPU query is nvidia-smi read-only."""
    import shutil

    import psutil

    memory = psutil.virtual_memory()
    snapshot = (probe or SystemResourceProbe()).sample()
    anchor = model_dir if model_dir.exists() else Path(model_dir.anchor or ".")
    return {
        "ram_total_bytes": memory.total,
        "ram_available_bytes": memory.available,
        "disk_free_bytes": shutil.disk_usage(anchor).free,
        "gpus": [gpu.model_dump(mode="json") for gpu in snapshot.gpus or ()],
    }


def load_manifest(path: Path) -> dict[str, str]:
    """``sha256  relative/path`` lines (sha256sum format); '#' lines are notes."""
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        if len(digest) != 64 or not name or ".." in Path(name).parts:
            raise ValueError(f"invalid manifest line in {path}")
        entries[name] = digest.lower()
    if not entries:
        raise ValueError(f"empty manifest {path}")
    return entries


def _sha256_file(path: Path, chunk: int = 16 * MIB) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def verify_manifest(
    model_dir: Path,
    manifest: dict[str, str],
    hasher: Callable[[Path], str] = _sha256_file,
) -> dict[str, Any]:
    """Every listed file present with the published sha256."""
    missing, mismatched = [], []
    total = 0
    for name, expected in sorted(manifest.items()):
        path = model_dir / name
        if not path.is_file():
            missing.append(name)
            continue
        total += path.stat().st_size
        if hasher(path) != expected:
            mismatched.append(name)
    return {
        "files": len(manifest),
        "bytes": total,
        "missing": missing,
        "mismatched": mismatched,
        "ok": not missing and not mismatched,
    }


def io_bench(
    files: Iterable[Path],
    limit_bytes: int,
    chunk: int = 16 * MIB,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Sequential buffered reads of container shards, as the engine streams them.

    Buffered reads can hit the OS file cache; shards read longest ago are read
    first, so on a 24 GB machine most of them are cold. The figure is recorded
    with that caveat; the live phase also records the engine's own disk reads.
    """
    read = 0
    started = clock()
    names = []
    for path in files:
        if read >= limit_bytes:
            break
        names.append(path.name)
        with path.open("rb", buffering=0) as handle:
            while read < limit_bytes and (
                block := handle.read(min(chunk, limit_bytes - read))
            ):
                read += len(block)
    seconds = clock() - started
    return {
        "bytes_read": read,
        "seconds": round(seconds, 3),
        "mb_per_s": round(read / MIB / seconds, 1) if seconds > 0 and read else None,
        "files": names,
        "method": "sequential buffered read, 16 MiB blocks; may include OS cache",
    }


def shard_files(model_dir: Path) -> list[Path]:
    return sorted(model_dir.glob("model-*.safetensors"))


# ---- the live phase: the served model through the unchanged PHX-016 harness

ProviderFactory = Callable[[], AbstractAsyncContextManager[ModelProvider]]
ProcessIo = Callable[[], dict[str, int] | None]


def engine_process_io() -> dict[str, int] | None:
    """Disk reads and resident memory of the Colibri engine process, or None."""
    import psutil

    found: dict[str, int] | None = None
    for process in psutil.process_iter(["name"]):
        if (process.info.get("name") or "").lower() not in ENGINE_PROCESSES:
            continue
        try:
            io = process.io_counters()
            rss = process.memory_info().rss
        except (psutil.Error, OSError):
            continue
        found = found or {"read_bytes": 0, "rss_bytes": 0, "processes": 0}
        found["read_bytes"] += io.read_bytes
        found["rss_bytes"] += rss
        found["processes"] += 1
    return found


async def live_run(
    provider_factory: ProviderFactory,
    model_id: str,
    config: BenchmarkConfig,
    tasks: Path,
    process_io: ProcessIo,
    resource_probe: ResourceProbe | None = None,
) -> tuple[dict[str, Any], BenchmarkReport | None]:
    suite, _ = baseline.load_task_set(tasks)
    async with provider_factory() as provider:
        health = await provider.health()
        if health.status != ProviderHealthStatus.HEALTHY:
            return {"endpoint": health.status.value, "refused": True}, None
        try:
            await provider.capabilities(model_id)
        except ModelProviderError:
            return {"endpoint": "model_not_served", "refused": True}, None
        before = await asyncio.to_thread(process_io)
        started = time.perf_counter()
        report = await BenchmarkRunner(
            ModelLifecycleManager(), resource_probe=resource_probe
        ).run(provider, model_id, suite=suite, config=config)
        seconds = time.perf_counter() - started
        after = await asyncio.to_thread(process_io)
    engine: dict[str, Any] | None = None
    if before is not None and after is not None:
        delta = max(after["read_bytes"] - before["read_bytes"], 0)
        engine = {
            "read_bytes": delta,
            "read_mb_per_s_avg": round(delta / MIB / seconds, 1) if seconds else None,
            "rss_bytes_after": after["rss_bytes"],
            "processes": after["processes"],
        }
    return {"endpoint": "healthy", "refused": False, "engine": engine}, report


def live_metrics(report: BenchmarkReport) -> dict[str, Any]:
    """Gate figures from measured trials only (warm-ups stay diagnostic).

    Throughput is output tokens / whole latency per successful trial: the
    harness tokens_per_second excludes hidden reasoning time (ADR-079).
    """
    measured = [t for t in report.trials if t.kind is TrialKind.MEASURED]
    ok = [t for t in measured if t.status is TrialStatus.SUCCESS]
    rates = [
        t.usage.output_tokens / (t.latency_ms / 1000)
        for t in ok
        if t.usage is not None
        and t.usage.output_tokens
        and t.latency_ms is not None
        and t.latency_ms > 0
    ]
    ram = [
        t.resources.max_observed_ram_used_bytes
        for t in measured
        if t.resources.max_observed_ram_used_bytes is not None
    ]
    gpus = [g for t in measured for g in (t.resources.gpus or ())]
    disk = [
        t.resources.system_disk_read_bytes_delta
        for t in measured
        if t.resources.system_disk_read_bytes_delta is not None
    ]
    peak_gpu = max(gpus, key=lambda g: g.max_observed_used_mib, default=None)
    s = report.summary
    return {
        "status": report.status.value,
        "preflight": report.preflight.value,
        "measured_trials": s.measured_trials,
        "success_rate": s.success_rate,
        "score_accuracy": s.score_accuracy,
        "ttft_ms_median": s.ttft_ms.median,
        "latency_ms_median": s.latency_ms.median,
        "tokens_per_second_median": round(statistics.median(rates), 2)
        if rates
        else None,
        "max_ram_used_bytes": max(ram) if ram else None,
        "max_gpu_used_mib": peak_gpu.max_observed_used_mib if peak_gpu else None,
        "gpu_total_mib": peak_gpu.total_mib if peak_gpu else None,
        "system_disk_read_bytes": sum(disk) if disk else None,
        "failures": sorted({t.failure.value for t in measured if t.failure}),
    }


# ---- verdict


def _criterion(
    cid: str, value: float | None, threshold: float, ok: Callable[[float], bool]
) -> dict[str, Any]:
    status = "NOT_MEASURED" if value is None else "PASS" if ok(value) else "FAIL"
    return {"id": cid, "status": status, "value": value, "threshold": threshold}


def evaluate(
    facts: dict[str, Any],
    live: dict[str, Any] | None,
    io: dict[str, Any] | None,
    limits: Thresholds,
) -> tuple[str, list[dict[str, Any]]]:
    """PASS only if every criterion passed; any FAIL rejects the deep tier."""
    live = live or {}
    total_ram = facts.get("ram_total_bytes")
    peak_ram = live.get("max_ram_used_bytes")
    headroom = None if total_ram is None or peak_ram is None else total_ram - peak_ram
    gpu_used, gpu_total = live.get("max_gpu_used_mib"), live.get("gpu_total_mib")
    vram = None if gpu_used is None or not gpu_total else gpu_used / gpu_total
    criteria = [
        _criterion(
            "success_rate",
            live.get("success_rate"),
            limits.min_success_rate,
            lambda v: v >= limits.min_success_rate,
        ),
        _criterion(
            "score_accuracy",
            live.get("score_accuracy"),
            limits.min_accuracy,
            lambda v: v >= limits.min_accuracy,
        ),
        _criterion(
            "ttft_ms_median",
            live.get("ttft_ms_median"),
            limits.max_ttft_median_ms,
            lambda v: v <= limits.max_ttft_median_ms,
        ),
        _criterion(
            "tokens_per_second_median",
            live.get("tokens_per_second_median"),
            limits.min_tokens_per_second,
            lambda v: v >= limits.min_tokens_per_second,
        ),
        _criterion(
            "ram_headroom_bytes",
            headroom,
            limits.min_ram_headroom_bytes,
            lambda v: v >= limits.min_ram_headroom_bytes,
        ),
        _criterion(
            "vram_fraction",
            None if vram is None else round(vram, 4),
            limits.max_vram_fraction,
            lambda v: v <= limits.max_vram_fraction,
        ),
        _criterion(
            "nvme_read_mb_per_s",
            (io or {}).get("mb_per_s"),
            limits.min_read_mb_per_s,
            lambda v: v >= limits.min_read_mb_per_s,
        ),
    ]
    statuses = {c["status"] for c in criteria}
    verdict = "FAIL" if "FAIL" in statuses else "PASS"
    if verdict == "PASS" and "NOT_MEASURED" in statuses:
        verdict = "INCOMPLETE"
    return verdict, criteria


def write_summary(results: dict[str, Any], path: Path) -> None:
    facts = results["host_facts"]
    gpus = ", ".join(f"{g['name']} {g['total_mib']} MiB" for g in facts.get("gpus", []))
    lines = [
        f"# PHOENIX Colibri deep-tier gate — {results['verdict']}",
        "",
        f"{results['started_utc']} → {results['finished_utc']} on "
        f"{results['host']['platform']}. Model `{results['model_id']}` at "
        f"`{results['base_url']}`.",
        "",
        f"- RAM total {facts['ram_total_bytes'] / GIB:.1f} GiB; GPU {gpus or 'n/a'}; "
        f"free disk {facts['disk_free_bytes'] / GIB:.1f} GiB.",
    ]
    manifest = results.get("manifest")
    if manifest is not None:
        lines.append(
            f"- Container manifest: {'OK' if manifest['ok'] else 'FAILED'} "
            f"({manifest['files']} files, {manifest['bytes'] / GIB:.2f} GiB; "
            f"missing {len(manifest['missing'])}, mismatched "
            f"{len(manifest['mismatched'])})."
        )
    engine = (results.get("live_endpoint") or {}).get("engine")
    if engine:
        lines.append(
            f"- Engine disk reads during the run: {engine['read_bytes'] / GIB:.2f} GiB "
            f"({engine['read_mb_per_s_avg']} MB/s average); engine RSS after "
            f"{engine['rss_bytes_after'] / GIB:.2f} GiB."
        )
    lines += [
        "",
        "| Criterion | Status | Value | Threshold |",
        "| --- | --- | --- | --- |",
    ]
    for c in results["criteria"]:
        lines.append(
            f"| {c['id']} | {c['status']} | "
            f"{'n/a' if c['value'] is None else c['value']} | {c['threshold']} |"
        )
    lines += [
        "",
        "TTFT is time to the first visible answer text, so it includes Qwen3.6's "
        "reasoning; throughput is output tokens / whole latency (ADR-079).",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(
    argv: list[str] | None = None,
    provider_factory: ProviderFactory | None = None,
    process_io: ProcessIo | None = None,
    resource_probe: ResourceProbe | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="PHOENIX Colibri deep-tier gate")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--tasks", type=Path, default=baseline.DEFAULT_TASKS)
    parser.add_argument("--out", type=Path, default=baseline.DEFAULT_OUT)
    parser.add_argument("--verify-manifest", action="store_true")
    parser.add_argument("--io-bench", action="store_true")
    parser.add_argument("--io-gib", type=float, default=8.0)
    parser.add_argument(
        "--live", action="store_true", help="benchmark the served model (uses GPU)"
    )
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.warmup < 0 or args.runs < 1 or args.io_gib <= 0:
        parser.error("--warmup >= 0, --runs >= 1 and --io-gib > 0 are required")
    limits = Thresholds()
    config = BenchmarkConfig(
        warmup_runs=args.warmup,
        measured_runs=args.runs,
        per_trial_timeout_seconds=args.timeout,
        max_output_tokens=args.max_output_tokens,
    )
    if args.dry_run:
        suite, _ = baseline.load_task_set(args.tasks)
        print(
            f"colibri-gate: {args.model_id} at {args.base_url}; container "
            f"{args.model_dir}; phases: facts"
            f"{', manifest' if args.verify_manifest else ''}"
            f"{', io-bench' if args.io_bench else ''}"
            f"{', live' if args.live else ''}; live = {len(suite.cases)} cases x "
            f"({args.warmup} warm-up + {args.runs} measured); thresholds "
            f"{json.dumps(asdict(limits))}"
        )
        return 0

    started = datetime.now(UTC).isoformat()
    facts = host_facts(args.model_dir, resource_probe)
    io = None
    if args.io_bench:  # before hashing, which would warm the OS file cache
        io = io_bench(shard_files(args.model_dir), int(args.io_gib * GIB))
        print(f"colibri-gate: sequential read {io['mb_per_s']} MB/s")
    manifest = None
    if args.verify_manifest:
        manifest = verify_manifest(args.model_dir, load_manifest(args.manifest))
        print(f"colibri-gate: manifest {'OK' if manifest['ok'] else 'FAILED'}")
    endpoint: dict[str, Any] | None = None
    live: dict[str, Any] | None = None
    report = None
    if args.live:
        factory = provider_factory or (lambda: _colibri(args.base_url))
        endpoint, report = asyncio.run(
            live_run(
                factory,
                args.model_id,
                config,
                args.tasks,
                process_io or engine_process_io,
                resource_probe,
            )
        )
        if endpoint["refused"]:
            print(
                f"colibri-gate: refused - endpoint {endpoint['endpoint']}; start "
                "`coli serve` first (PHOENIX does not start it)",
                file=sys.stderr,
            )
            return 2
        assert report is not None
        live = live_metrics(report)
    verdict, criteria = evaluate(facts, live, io, limits)
    if manifest is not None and not manifest["ok"]:
        verdict = "FAIL"
        criteria.append(
            {"id": "manifest", "status": "FAIL", "value": None, "threshold": "ok"}
        )
    results: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "model_id": args.model_id,
        "model_dir": str(args.model_dir),
        "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "host_facts": facts,
        "thresholds": asdict(limits),
        "config": config.model_dump(mode="json"),
        "manifest": manifest,
        "io_bench": io,
        "live_endpoint": endpoint,
        "live": live,
        "verdict": verdict,
        "criteria": criteria,
        "report": None if report is None else report.model_dump(mode="json"),
    }
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / RESULTS_NAME).write_text(json.dumps(results, indent=2), encoding="utf-8")
    write_summary(results, out / SUMMARY_NAME)
    print(f"colibri-gate: {verdict} - results in {out}")
    return 0 if verdict == "PASS" else 1


def _colibri(base_url: str) -> AbstractAsyncContextManager[ModelProvider]:
    from phoenix.models.colibri import ColibriProvider

    return ColibriProvider(base_url)


if __name__ == "__main__":
    raise SystemExit(main())
