"""PHX-090: self-tests for the automated test matrix runner (scripts/test_matrix.py)."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phoenix_test_matrix", ROOT / "scripts" / "test_matrix.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tm = _load()
ALL = list(tm.CATEGORIES)


def _manifest(tmp: Path, body: str) -> Path:
    path = tmp / "matrix.toml"
    path.write_text("schema_version = 1\n" + body, encoding="utf-8")
    return path


def _repo(tmp: Path, *names: str) -> Path:
    (tmp / "tests").mkdir()
    for name in names:
        (tmp / "tests" / name).write_text("", encoding="utf-8")
    return tmp


def _covering(path: str = "tests/test_a.py") -> str:
    cats = ", ".join(f'"{c}"' for c in ALL)
    return f'[[file]]\npath = "{path}"\ncategories = [{cats}]\n'


def _bullet_ids(text: str) -> list[str]:
    return [
        line[2:].strip().lower().replace("-", "_").replace(" ", "_")
        for line in text.splitlines()
        if line.startswith("- ")
    ]


def test_categories_match_docs_24_exactly() -> None:
    doc = (ROOT / "docs" / "24_TEST_EVALUATION.md").read_text(encoding="utf-8")
    software, rest = doc.split("## Security tests")
    security = rest.split("## FYP")[0]
    assert _bullet_ids(software) == list(tm.SOFTWARE)
    assert _bullet_ids(security) == list(tm.SECURITY)


def test_complete_manifest_validates(tmp_path: Path) -> None:
    root = _repo(tmp_path, "test_a.py")
    manifest = tm.load_manifest(_manifest(tmp_path, _covering()))
    assert tm.validate(manifest, tm.discover_test_files(root), root) == []


def test_unmapped_test_file_is_an_error(tmp_path: Path) -> None:
    root = _repo(tmp_path, "test_a.py", "test_new.py", "ui.test.mjs")
    manifest = tm.load_manifest(_manifest(tmp_path, _covering()))
    errors = tm.validate(manifest, tm.discover_test_files(root), root)
    assert "unmapped test file: tests/test_new.py" in errors
    assert "unmapped test file: tests/ui.test.mjs" in errors


def test_missing_file_unknown_category_and_duplicates(tmp_path: Path) -> None:
    root = _repo(tmp_path, "test_a.py")
    body = _covering() + '[[file]]\npath = "tests/test_gone.py"\ncategories = ["x"]\n'
    errors = tm.validate(tm.load_manifest(_manifest(tmp_path, body)), [], root)
    assert "manifest entry points to missing file: tests/test_gone.py" in errors
    assert "unknown category 'x' for tests/test_gone.py" in errors
    with pytest.raises(ValueError, match="duplicate"):
        tm.load_manifest(_manifest(tmp_path, _covering() + _covering()))


def test_uncovered_category_needs_declared_gap(tmp_path: Path) -> None:
    root = _repo(tmp_path, "test_a.py")
    cats = ", ".join(f'"{c}"' for c in ALL if c != "end_to_end")
    body = f'[[file]]\npath = "tests/test_a.py"\ncategories = [{cats}]\n'
    manifest = tm.load_manifest(_manifest(tmp_path, body))
    assert tm.validate(manifest, ["tests/test_a.py"], root) == [
        "category end_to_end has no tests and no declared gap"
    ]
    gap = '[[gap]]\ncategory = "end_to_end"\nreason = "no full-flow test yet"\n'
    manifest = tm.load_manifest(_manifest(tmp_path, body + gap))
    assert tm.validate(manifest, ["tests/test_a.py"], root) == []
    blank = '[[gap]]\ncategory = "end_to_end"\nreason = " "\n'
    manifest = tm.load_manifest(_manifest(tmp_path, body + blank))
    assert tm.validate(manifest, ["tests/test_a.py"], root) == [
        "gap end_to_end needs a reason"
    ]


def test_gap_cannot_also_be_mapped(tmp_path: Path) -> None:
    root = _repo(tmp_path, "test_a.py")
    gap = '[[gap]]\ncategory = "unit"\nreason = "contradiction"\n'
    manifest = tm.load_manifest(_manifest(tmp_path, _covering() + gap))
    assert tm.validate(manifest, ["tests/test_a.py"], root) == [
        "category unit is both mapped and declared a gap"
    ]


def test_manual_rows_require_evidence(tmp_path: Path) -> None:
    root = _repo(tmp_path, "test_a.py")
    manual = '[[manual]]\nid = "native"\ndescription = "WebView2"\nevidence = ""\n'
    manifest = tm.load_manifest(_manifest(tmp_path, _covering() + manual))
    errors = tm.validate(manifest, ["tests/test_a.py"], root)
    assert len(errors) == 1 and errors[0].startswith("manual row missing evidence")


def test_unsupported_schema_version_rejected(tmp_path: Path) -> None:
    path = tmp_path / "matrix.toml"
    path.write_text("schema_version = 99\n", encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version"):
        tm.load_manifest(path)


def test_junit_maps_outcomes_per_file(tmp_path: Path) -> None:
    xml = tmp_path / "junit.xml"
    xml.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.test_a" name="ok"/>'
        '<testcase classname="tests.test_a.TestCls" name="bad"><failure/></testcase>'
        '<testcase classname="tests.test_b" name="skip"><skipped/></testcase>'
        '<testcase classname="tests.test_b" name="err"><error/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    outcomes = tm.parse_junit(xml)
    assert (outcomes["tests/test_a.py"].passed, outcomes["tests/test_a.py"].failed) == (
        1,
        1,
    )
    assert (
        outcomes["tests/test_b.py"].skipped,
        outcomes["tests/test_b.py"].failed,
    ) == (
        1,
        1,
    )


def test_node_spec_counts_parsed() -> None:
    out = "noise\nℹ tests 181\nℹ pass 180\nℹ fail 1\nℹ skipped 0\n"
    expected = {"tests": 181, "pass": 180, "fail": 1, "skipped": 0}
    assert tm.parse_node_spec(out) == expected


def test_category_status_pass_fail_not_run_and_gap(tmp_path: Path) -> None:
    body = (
        '[[file]]\npath = "tests/test_a.py"\ncategories = ["unit", "tools"]\n'
        '[[file]]\npath = "tests/test_b.py"\ncategories = ["tools"]\n'
        '[[gap]]\ncategory = "end_to_end"\nreason = "none yet"\n'
    )
    manifest = tm.load_manifest(_manifest(tmp_path, body))
    rows = {
        r["id"]: r
        for r in tm.category_rows(
            manifest,
            {
                "tests/test_a.py": tm.FileOutcome(passed=3),
                "tests/test_b.py": tm.FileOutcome(passed=1, failed=1),
            },
        )
    }
    assert rows["unit"]["status"] == "PASS"
    assert rows["tools"]["status"] == "FAIL"
    assert rows["end_to_end"]["status"] == "GAP"
    assert rows["routing"]["status"] == "UNMAPPED"  # no files: never a vacuous PASS
    only_a = tm.category_rows(manifest, {"tests/test_a.py": tm.FileOutcome(passed=1)})
    assert {r["id"]: r for r in only_a}["tools"]["status"] == "NOT_RUN"


def _gate(gid: str, code: str, timeout: int = 60, *extra: str) -> Any:
    command = (sys.executable, "-c", code, *extra)
    return tm.Gate(id=gid, command=command, timeout=timeout)


def test_run_gate_pass_fail_timeout_and_missing_tool(tmp_path: Path) -> None:
    assert tm.run_gate(_gate("ok", "print('hi')"), tmp_path).status == "PASS"
    failed = tm.run_gate(_gate("bad", "import sys; sys.exit(3)"), tmp_path)
    assert (failed.status, failed.exit_code) == ("FAIL", 3)
    slow = tm.run_gate(_gate("slow", "import time; time.sleep(5)", timeout=1), tmp_path)
    assert slow.status == "TIMEOUT"
    missing = tm.run_gate(
        tm.Gate(id="nope", command=("phx-no-such-tool-xyz",), timeout=5), tmp_path
    )
    assert missing.status == "MISSING_TOOL"
    assert Path(failed.log).read_text(encoding="utf-8").count("--- stderr ---") == 1


def test_out_placeholder_expanded(tmp_path: Path) -> None:
    gate = _gate("echo", "import sys; print(sys.argv[1])", 30, "{out}/x")
    result = tm.run_gate(gate, tmp_path)
    assert result.command[-1] == f"{tmp_path}/x"
    assert f"{tmp_path}/x" in Path(result.log).read_text(encoding="utf-8")


def test_log_tail_limits_and_prefixes(tmp_path: Path) -> None:
    log = tmp_path / "gate.log"
    log.write_text("\n".join(f"line{i}" for i in range(200)), encoding="utf-8")
    tail = tm.log_tail(log, 3).splitlines()
    assert tail == ["matrix|  line197", "matrix|  line198", "matrix|  line199"]


def test_failing_gate_output_reaches_console(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    py = sys.executable.replace("\\", "/")
    code = "import sys; print('FAILED tests/x.py::t - boom'); sys.exit(1)"
    gate = f'[[gate]]\nid = "bad"\ncommand = ["{py}", "-c", "{code}"]\n'
    manifest = _manifest(tmp_path, gate + _covering_real())
    args = ["--manifest", str(manifest), "--out", str(tmp_path / "o"), "--allow-dirty"]
    assert tm.main(args) == 1
    assert "matrix|  FAILED tests/x.py::t - boom" in capsys.readouterr().out


def test_failing_gate_tail_prints_on_a_cp1252_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PHX-117 CI finding: the Windows runner console is cp1252, and a pytest
    # log holding U+FFFD crashed the matrix while printing the failing tail.
    assert tm.console_safe("a � → b", "cp1252") == "a ? ? b"
    assert tm.console_safe("a → b", None) == "a → b"
    py = sys.executable.replace("\\", "/")
    # The gate writes "FAILED <U+FFFD as UTF-8> x" to its log.
    code = (
        "import sys; sys.stdout.buffer.write(b'FAILED \\\\xef\\\\xbf\\\\xbd x'); "
        "sys.exit(1)"
    )
    gate = f'[[gate]]\nid = "bad"\ncommand = ["{py}", "-c", "{code}"]\n'
    manifest = _manifest(tmp_path, gate + _covering_real())
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", console)
    args = ["--manifest", str(manifest), "--out", str(tmp_path / "o"), "--allow-dirty"]
    assert tm.main(args) == 1
    console.flush()
    assert "matrix|  FAILED ? x" in raw.getvalue().decode("cp1252")


def _covering_real() -> str:
    """Manifest body mapping every real test file, so validation passes."""
    real = tm.load_manifest(tm.MANIFEST)
    files = "".join(
        f'[[file]]\npath = "{p}"\ncategories = {json.dumps(list(cats))}\n'
        for p, cats in real.files.items()
    )
    gaps = "".join(
        f"[[gap]]\ncategory = {json.dumps(c)}\nreason = {json.dumps(r)}\n"
        for c, r in real.gaps.items()
    )
    return files + gaps + '[[manual]]\nid = "m"\ndescription = "d"\nevidence = "e"\n'


def test_repository_manifest_is_complete() -> None:
    """Every real test file is mapped and every docs/24 category is covered or a gap."""
    manifest = tm.load_manifest(tm.MANIFEST)
    assert tm.validate(manifest, tm.discover_test_files(ROOT), ROOT) == []
    assert manifest.manual, "manual acceptance rows must be listed"


def test_validate_only_cli_reports_ok(capsys: pytest.CaptureFixture[str]) -> None:
    assert tm.main(["--validate-only"]) == 0
    assert "manifest OK" in capsys.readouterr().out


def test_end_to_end_run_with_fake_gates(tmp_path: Path) -> None:
    root_manifest = tm.load_manifest(tm.MANIFEST)
    files = "".join(
        f'[[file]]\npath = "{p}"\ncategories = {json.dumps(list(cats))}\n'
        for p, cats in root_manifest.files.items()
    )
    gaps = "".join(
        f"[[gap]]\ncategory = {json.dumps(c)}\nreason = {json.dumps(r)}\n"
        for c, r in root_manifest.gaps.items()
    )
    manual = '[[manual]]\nid = "m"\ndescription = "d"\nevidence = "e"\n'
    py = sys.executable.replace("\\", "/")
    gate = f'[[gate]]\nid = "fake"\ncommand = ["{py}", "-c", "print(1)"]\n'
    manifest = _manifest(tmp_path, gate + files + gaps + manual)
    out = tmp_path / "out"
    code = tm.main(["--manifest", str(manifest), "--out", str(out), "--allow-dirty"])
    results = json.loads((out / "test-matrix-results.json").read_text(encoding="utf-8"))
    assert results["schema_version"] == 1 and results["gates"][0]["status"] == "PASS"
    # No gate produced per-file results: mapped categories are NOT_RUN, so FAIL.
    assert code == 1 and results["overall"] == "FAIL"
    assert (
        (out / "test-matrix-summary.md")
        .read_text(encoding="utf-8")
        .startswith("# PHOENIX test matrix — FAIL")
    )
    assert results["manual"] == [
        {"id": "m", "description": "d", "evidence": "e", "status": "MANUAL"}
    ]
