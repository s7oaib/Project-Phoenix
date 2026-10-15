"""PHOENIX automated test matrix (PHX-090, ADR-078).

Runs every required gate declared in ``tests/matrix.toml`` in order, maps each test
file to the ``docs/24_TEST_EVALUATION.md`` categories, and writes schema-versioned
results plus a Markdown summary. Categories without automated tests must be declared
as explicit gaps; manual acceptance rows are reported but never counted as PASS.

Usage::

    python scripts/test_matrix.py                 # all gates, clean tree required
    python scripts/test_matrix.py --validate-only # manifest checks only
    python scripts/test_matrix.py --gate pytest   # subset (overall PARTIAL)

Exit codes: 0 success, 1 gate/category/validation failure, 2 usage or environment
error (dirty tree, unknown gate).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "matrix.toml"
DEFAULT_OUT = ROOT / ".test-matrix"

# Category ids exactly as listed in docs/24_TEST_EVALUATION.md.
SOFTWARE = (
    "unit",
    "integration",
    "api_contract",
    "migrations",
    "model_provider_contracts",
    "routing",
    "permissions",
    "tools",
    "recovery",
    "voice_states",
    "frontend",
    "end_to_end",
)
SECURITY = (
    "prompt_injection",
    "path_traversal",
    "command_argument_injection",
    "permission_bypass",
    "secret_leakage",
    "malicious_documents",
    "forged_tool_results",
)
CATEGORIES = SOFTWARE + SECURITY
TEST_FILE_PATTERNS = ("test_*.py", "*.test.mjs")


@dataclass(frozen=True)
class Gate:
    id: str
    command: tuple[str, ...]
    timeout: int
    results: str | None = None  # "junit:<path>" or "node-spec"


@dataclass(frozen=True)
class Manifest:
    gates: tuple[Gate, ...]
    files: dict[str, tuple[str, ...]]
    gaps: dict[str, str]
    manual: tuple[dict[str, Any], ...]


@dataclass
class FileOutcome:
    passed: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass
class GateResult:
    id: str
    command: list[str]
    status: str
    exit_code: int | None
    duration_s: float
    log: str
    counts: dict[str, int] = field(default_factory=dict)


def load_manifest(path: Path) -> Manifest:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported manifest schema_version in {path}")
    gates = tuple(
        Gate(
            id=g["id"],
            command=tuple(g["command"]),
            timeout=int(g.get("timeout", 1800)),
            results=g.get("results"),
        )
        for g in data.get("gate", [])
    )
    files: dict[str, tuple[str, ...]] = {}
    for entry in data.get("file", []):
        if entry["path"] in files:
            raise ValueError(f"duplicate manifest entry: {entry['path']}")
        files[entry["path"]] = tuple(entry["categories"])
    gaps = {g["category"]: g["reason"] for g in data.get("gap", [])}
    manual = tuple(dict(m) for m in data.get("manual", []))
    return Manifest(gates, files, gaps, manual)


def discover_test_files(root: Path) -> list[str]:
    found = {
        p.relative_to(root).as_posix()
        for pattern in TEST_FILE_PATTERNS
        for p in (root / "tests").glob(pattern)
    }
    return sorted(found)


def validate(manifest: Manifest, test_files: list[str], root: Path) -> list[str]:
    """Return every manifest problem; an empty list means the matrix is complete."""
    errors: list[str] = []
    for path in test_files:
        if path not in manifest.files:
            errors.append(f"unmapped test file: {path}")
    for path, categories in manifest.files.items():
        if not (root / path).is_file():
            errors.append(f"manifest entry points to missing file: {path}")
        if not categories:
            errors.append(f"no categories for {path}")
        for category in categories:
            if category not in CATEGORIES:
                errors.append(f"unknown category {category!r} for {path}")
    mapped = {c for cats in manifest.files.values() for c in cats}
    for category, reason in manifest.gaps.items():
        if category not in CATEGORIES:
            errors.append(f"unknown gap category {category!r}")
        elif category in mapped:
            errors.append(f"category {category} is both mapped and declared a gap")
        elif not reason.strip():
            errors.append(f"gap {category} needs a reason")
    for category in CATEGORIES:
        if category not in mapped and category not in manifest.gaps:
            errors.append(f"category {category} has no tests and no declared gap")
    for row in manifest.manual:
        for key in ("id", "description", "evidence"):
            if not str(row.get(key, "")).strip():
                errors.append(f"manual row missing {key}: {row}")
    ids = [g.id for g in manifest.gates]
    if len(ids) != len(set(ids)):
        errors.append("duplicate gate ids")
    return errors


def _expand(value: str, out: Path) -> str:
    return value.replace("{out}", str(out))


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return completed.stdout.strip()


def _text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def run_gate(gate: Gate, out: Path) -> GateResult:
    command = [_expand(part, out) for part in gate.command]
    log = out / "logs" / f"{gate.id}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    executable = shutil.which(command[0])
    if executable is None:
        log.write_text(f"executable not found: {command[0]}\n", encoding="utf-8")
        return GateResult(gate.id, command, "MISSING_TOOL", None, 0.0, str(log))
    start = time.monotonic()
    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=gate.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = f"{_text(exc.stdout)}\n--- stderr ---\n{_text(exc.stderr)}"
        log.write_text(f"TIMEOUT after {gate.timeout}s\n{output}", encoding="utf-8")
        duration = round(time.monotonic() - start, 1)
        return GateResult(gate.id, command, "TIMEOUT", None, duration, str(log))
    duration = round(time.monotonic() - start, 1)
    log.write_text(
        f"{completed.stdout}\n--- stderr ---\n{completed.stderr}", encoding="utf-8"
    )
    status = "PASS" if completed.returncode == 0 else "FAIL"
    result = GateResult(
        gate.id, command, status, completed.returncode, duration, str(log)
    )
    if gate.results == "node-spec":
        result.counts = parse_node_spec(completed.stdout)
    elif gate.results and gate.results.startswith("junit:"):
        junit = Path(_expand(gate.results.removeprefix("junit:"), out))
        if junit.is_file():
            totals = FileOutcome()
            for outcome in parse_junit(junit).values():
                totals.passed += outcome.passed
                totals.failed += outcome.failed
                totals.skipped += outcome.skipped
            result.counts = {
                "passed": totals.passed,
                "failed": totals.failed,
                "skipped": totals.skipped,
            }
    return result


def log_tail(log: Path, lines: int = 80) -> str:
    """Last lines of a gate log, prefixed for readability in CI output."""
    text = log.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(f"matrix|  {line}" for line in text[-lines:])


def parse_node_spec(stdout: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in ("tests", "pass", "fail", "skipped"):
        match = re.search(rf"^\S*\s*{key} (\d+)\s*$", stdout, re.MULTILINE)
        if match:
            counts[key] = int(match.group(1))
    return counts


def parse_junit(path: Path) -> dict[str, FileOutcome]:
    """Per-test-file outcomes from pytest JUnit XML (classname tests.test_x[.Cls])."""
    outcomes: dict[str, FileOutcome] = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        parts = (case.get("classname") or "").split(".")
        if len(parts) < 2:
            continue
        outcome = outcomes.setdefault(f"{parts[0]}/{parts[1]}.py", FileOutcome())
        if case.find("failure") is not None or case.find("error") is not None:
            outcome.failed += 1
        elif case.find("skipped") is not None:
            outcome.skipped += 1
        else:
            outcome.passed += 1
    return outcomes


def file_outcomes(
    manifest: Manifest, results: list[GateResult], out: Path
) -> dict[str, FileOutcome]:
    outcomes: dict[str, FileOutcome] = {}
    by_id = {r.id: r for r in results}
    for gate in manifest.gates:
        result = by_id.get(gate.id)
        if result is None or not gate.results:
            continue
        if gate.results.startswith("junit:"):
            junit = Path(_expand(gate.results.removeprefix("junit:"), out))
            if junit.is_file():
                outcomes.update(parse_junit(junit))
        elif gate.results == "node-spec":
            counts = result.counts
            node_files = [p for p in manifest.files if p.endswith(".test.mjs")]
            if len(node_files) == 1 and "tests" in counts:
                outcomes[node_files[0]] = FileOutcome(
                    passed=counts.get("pass", 0),
                    failed=counts.get("fail", 0) + (result.exit_code != 0),
                    skipped=counts.get("skipped", 0),
                )
    return outcomes


def category_rows(
    manifest: Manifest, outcomes: dict[str, FileOutcome]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for category in CATEGORIES:
        kind = "software" if category in SOFTWARE else "security"
        if category in manifest.gaps:
            rows.append(
                {
                    "id": category,
                    "kind": kind,
                    "status": "GAP",
                    "files": [],
                    "reason": manifest.gaps[category],
                }
            )
            continue
        files = sorted(p for p, cats in manifest.files.items() if category in cats)
        seen = [outcomes[p] for p in files if p in outcomes]
        if not files:
            status = "UNMAPPED"  # never a vacuous PASS
        elif any(o.failed for o in seen):
            status = "FAIL"
        elif len(seen) == len(files) and all(o.passed for o in seen):
            status = "PASS"
        else:
            status = "NOT_RUN"
        rows.append(
            {
                "id": category,
                "kind": kind,
                "status": status,
                "files": files,
                "tests_passed": sum(o.passed for o in seen),
                "tests_failed": sum(o.failed for o in seen),
            }
        )
    return rows


def write_summary(results: dict[str, Any], path: Path) -> None:
    lines = [
        f"# PHOENIX test matrix — {results['overall']}",
        "",
        f"Source `{results['source']['head']}` "
        f"(dirty allowed: {results['source']['dirty_allowed']}); "
        f"{results['started_utc']} → {results['finished_utc']}.",
        "",
        "## Gates",
        "",
        "| Gate | Status | Exit | Seconds | Counts |",
        "| --- | --- | --- | --- | --- |",
    ]
    for g in results["gates"]:
        lines.append(
            f"| {g['id']} | {g['status']} | {g['exit_code']} | "
            f"{g['duration_s']} | {g['counts'] or ''} |"
        )
    lines += [
        "",
        "## Categories (docs/24)",
        "",
        "| Category | Kind | Status | Files / reason |",
        "| --- | --- | --- | --- |",
    ]
    for c in results["categories"]:
        detail = c.get("reason") or f"{len(c['files'])} files"
        lines.append(f"| {c['id']} | {c['kind']} | {c['status']} | {detail} |")
    lines += ["", "## Manual rows (never counted as automated PASS)", ""]
    for m in results["manual"]:
        lines.append(f"- **{m['id']}** — {m['description']} Evidence: {m['evidence']}")
    if results["validation_errors"]:
        lines += ["", "## Manifest errors", ""]
        lines += [f"- {e}" for e in results["validation_errors"]]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PHOENIX automated test matrix")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--gate", action="append", default=[], help="run only these")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    errors = validate(manifest, discover_test_files(ROOT), ROOT)
    if args.validate_only:
        for error in errors:
            print(f"matrix: {error}", file=sys.stderr)
        print("matrix: manifest OK" if not errors else "matrix: manifest INVALID")
        return 1 if errors else 0

    unknown = set(args.gate) - {g.id for g in manifest.gates}
    if unknown:
        print(f"matrix: unknown gate(s): {sorted(unknown)}", file=sys.stderr)
        return 2
    status_before = _git("status", "--porcelain")
    if status_before and not args.allow_dirty:
        print("matrix: working tree is dirty; use --allow-dirty", file=sys.stderr)
        return 2

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(UTC).isoformat()
    selected = [g for g in manifest.gates if not args.gate or g.id in args.gate]
    gate_results: list[GateResult] = []
    for gate in selected:
        print(f"matrix: running {gate.id} ...", flush=True)
        result = run_gate(gate, out)
        print(f"matrix: {gate.id} {result.status} ({result.duration_s}s)", flush=True)
        if result.status != "PASS":
            # CI consoles are often the only place a failure is visible.
            print(log_tail(Path(result.log)), flush=True)
        gate_results.append(result)
    status_after = _git("status", "--porcelain")

    categories = category_rows(manifest, file_outcomes(manifest, gate_results, out))
    partial = len(selected) != len(manifest.gates)
    failed = (
        errors
        or any(r.status != "PASS" for r in gate_results)
        or any(c["status"] in ("FAIL", "UNMAPPED") for c in categories)
        or (not partial and any(c["status"] == "NOT_RUN" for c in categories))
        or status_after != status_before
    )
    overall = "FAIL" if failed else ("PARTIAL" if partial else "PASS")
    results: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": "phoenix-test-matrix",
        "overall": overall,
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(),
        "source": {
            "head": _git("rev-parse", "HEAD"),
            "branch": _git("branch", "--show-current"),
            "dirty_allowed": args.allow_dirty,
            "status_before": status_before,
            "status_after": status_after,
            "tree_modified_by_gates": status_after != status_before,
        },
        "platform": {
            "os": platform.platform(),
            "python": sys.version.split()[0],
            "ci": bool(os.environ.get("CI")),
        },
        "manifest": str(args.manifest),
        "validation_errors": errors,
        "gates": [r.__dict__ for r in gate_results],
        "categories": categories,
        "manual": [
            {k: m[k] for k in ("id", "description", "evidence")} | {"status": "MANUAL"}
            for m in manifest.manual
        ],
    }
    (out / "test-matrix-results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    write_summary(results, out / "test-matrix-summary.md")
    print(f"matrix: {overall} - results in {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
