"""PHX-124: the control smoke must release its audit DB so the report is written."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from phoenix.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phoenix_control_smoke_script", ROOT / "scripts" / "phoenix_control_smoke.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load()


def test_audit_db_is_released_on_exit(tmp_path: Path) -> None:
    folder = tmp_path / "run"
    folder.mkdir()
    registry = ToolRegistry(smoke.control_tool_definitions())
    with smoke._audit(folder, registry) as audit:
        assert audit.list_records() == ()
    # Strict removal: on Windows this raised WinError 32 while the engine's
    # pooled connection still held smoke-audit.db open.
    shutil.rmtree(folder)
    assert not folder.exists()


def test_tools_report_is_written_even_if_temp_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handles: list[sqlite3.Connection] = []

    def run_tools(directory: Path) -> dict[str, Any]:
        # Simulate any leftover open handle in the temp folder.
        handles.append(sqlite3.connect(directory / "smoke-audit.db"))
        handles[0].execute("create table t (x)")
        return {"steps": [{"status": "success"}], "audit_rows": 0}

    monkeypatch.setattr(smoke, "run_tools", run_tools)
    try:
        assert smoke.main(["--tools", "--out", str(tmp_path / "out")]) == 0
    finally:
        for handle in handles:
            handle.close()
    report = json.loads(
        (tmp_path / "out" / "phoenix-control-tools-smoke.json").read_text()
    )
    assert report["steps"] == [{"status": "success"}]
