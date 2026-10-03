"""Deterministic hardware evidence, degraded probes, and read-only boundaries."""

import json
import subprocess
import sys
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psutil
import pytest
from pydantic import ValidationError

from phoenix.core import hardware
from phoenix.core.hardware import (
    CheckStatus,
    DiskInfo,
    GpuInfo,
    HardwareSnapshot,
    ProfileStatus,
    assess_blaze6,
    parse_nvidia_output,
    scan_hardware,
)


@pytest.fixture
def blaze6() -> HardwareSnapshot:
    return HardwareSnapshot(
        platform="win32",
        cpu_model="13th Gen Intel(R) Core(TM) i7-13650HX",
        physical_cores=14,
        logical_processors=20,
        ram_bytes=24 * 2**30,
        disks=(
            DiskInfo(
                device="D:",
                mount="D:/",
                total_bytes=500 * 2**30,
                free_bytes=100 * 2**30,
            ),
        ),
        gpus=(
            GpuInfo(
                name="NVIDIA GeForce RTX 3050 Laptop GPU",
                vram_mib=6144,
                driver_version="581.80",
            ),
        ),
    )


def test_snapshot_contract(blaze6: HardwareSnapshot) -> None:
    assert blaze6.timestamp.tzinfo is UTC
    assert blaze6.physical_cores == 14 and blaze6.logical_processors == 20
    assert isinstance(blaze6.ram_bytes, int)
    assert blaze6.disks[0].total_bytes == 500 * 2**30
    assert blaze6.disks[0].free_bytes == 100 * 2**30
    assert json.loads(blaze6.model_dump_json())["gpus"][0]["vram_mib"] == 6144
    with pytest.raises(ValidationError):
        blaze6.cpu_model = "changed"


@pytest.mark.parametrize(
    "bad",
    [
        {"physical_cores": 0},
        {"logical_processors": -1},
        {"ram_bytes": "24 GB"},
        {"timestamp": "2026-01-01T00:00:00"},
    ],
)
def test_invalid_snapshot(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        HardwareSnapshot.model_validate({"platform": "win32", **bad})


def test_nvidia_rows_and_driver() -> None:
    gpus, issues = parse_nvidia_output(
        "NVIDIA GeForce RTX 4090, 24576, 581.80\n"
        " NVIDIA GeForce RTX 3050 Laptop GPU, 6144, 581.80\n"
        "malformed\nOther GPU, invalid, N/A\n"
    )
    assert len(gpus) == 2
    assert gpus[0].vram_mib == 24576
    assert gpus[1].name == "NVIDIA GeForce RTX 3050 Laptop GPU"
    assert gpus[1].vram_mib == 6144 and gpus[1].driver_version == "581.80"
    assert issues == ("nvidia_malformed_row", "nvidia_invalid_vram")


@pytest.mark.parametrize("output", ["", "bad", "GPU, N/A, N/A", "GPU, -1, N/A"])
def test_malformed_rows_do_not_invent_gpus(output: str) -> None:
    gpus, issues = parse_nvidia_output(output)
    assert not gpus and issues


def test_nvidia_fixed_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Mock(
        return_value=subprocess.CompletedProcess(
            args=[], returncode=0, stdout="RTX 3050 Laptop GPU, 6144, N/A\n", stderr=""
        )
    )
    monkeypatch.setattr(subprocess, "run", runner)
    gpus, issues = hardware._nvidia_gpus()
    assert gpus[0].vram_mib == 6144 and gpus[0].driver_version is None
    assert not issues
    runner.assert_called_once_with(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        shell=False,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )


@pytest.mark.parametrize(
    ("error", "issue"),
    [
        (FileNotFoundError(), "nvidia_smi_missing"),
        (subprocess.TimeoutExpired("nvidia-smi", 5), "nvidia_smi_timeout"),
        (PermissionError(), "nvidia_smi_unavailable"),
    ],
)
def test_optional_nvidia_failure(
    error: Exception, issue: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=error))
    assert hardware._nvidia_gpus() == ((), (issue,))


@pytest.mark.parametrize(
    ("returncode", "stdout", "issue"),
    [
        (1, "", "nvidia_smi_failed"),
        (0, "", "nvidia_no_valid_rows"),
        (0, '"unterminated', "nvidia_malformed_csv"),
    ],
)
def test_optional_nvidia_bad_result(
    returncode: int, stdout: str, issue: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        Mock(
            return_value=subprocess.CompletedProcess(
                [], returncode, stdout, "private diagnostic"
            )
        ),
    )
    gpus, issues = hardware._nvidia_gpus()
    assert not gpus and issue in issues
    assert "private diagnostic" not in str(issues)


def test_known_profile_and_free_disk_independence(blaze6: HardwareSnapshot) -> None:
    assert assess_blaze6(blaze6).status is ProfileStatus.MATCHES
    assert all(
        check.status is CheckStatus.MATCH for check in assess_blaze6(blaze6).checks
    )
    changed = blaze6.model_copy(update={"disks": ()})
    assert assess_blaze6(changed) == assess_blaze6(blaze6)
    normalized = blaze6.model_copy(update={"cpu_model": "Intel i7 13650HX"})
    assert assess_blaze6(normalized).status is ProfileStatus.MATCHES


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cpu_model", "Intel i9-13900H"),
        ("physical_cores", 12),
        ("logical_processors", 16),
        ("ram_bytes", 16 * 2**30),
        ("gpu_model", "NVIDIA RTX 4090"),
        ("vram_mib", 4096),
    ],
)
def test_mismatch_evidence(
    field: str, value: str | int, blaze6: HardwareSnapshot
) -> None:
    data = blaze6.model_dump()
    if field == "gpu_model":
        data["gpus"][0]["name"] = value
    elif field == "vram_mib":
        data["gpus"][0]["vram_mib"] = value
    else:
        data[field] = value
    assessment = assess_blaze6(HardwareSnapshot.model_validate(data))
    assert assessment.status is ProfileStatus.DOES_NOT_MATCH
    check = next(check for check in assessment.checks if check.field == field)
    assert check.status is CheckStatus.MISMATCH and check.observed == value
    assert check.expected and check.reason


@pytest.mark.parametrize("ram", [22 * 2**30, 26 * 2**30])
def test_ram_interval_inclusive(ram: int, blaze6: HardwareSnapshot) -> None:
    assert assess_blaze6(blaze6.model_copy(update={"ram_bytes": ram})).status is (
        ProfileStatus.MATCHES
    )


@pytest.mark.parametrize("ram", [22 * 2**30 - 1, 26 * 2**30 + 1])
def test_ram_just_outside_interval(ram: int, blaze6: HardwareSnapshot) -> None:
    assert assess_blaze6(blaze6.model_copy(update={"ram_bytes": ram})).status is (
        ProfileStatus.DOES_NOT_MATCH
    )


def test_matching_gpu_unknown_vram(blaze6: HardwareSnapshot) -> None:
    snapshot = blaze6.model_copy(
        update={"gpus": (GpuInfo(name="NVIDIA GeForce RTX 3050 Laptop GPU"),)}
    )
    assert assess_blaze6(snapshot).status is ProfileStatus.INSUFFICIENT_EVIDENCE


@pytest.mark.parametrize(
    "field", ["cpu_model", "physical_cores", "logical_processors", "ram_bytes", "gpus"]
)
def test_unavailable_is_not_match(field: str, blaze6: HardwareSnapshot) -> None:
    snapshot = blaze6.model_copy(update={field: () if field == "gpus" else None})
    assert assess_blaze6(snapshot).status is ProfileStatus.INSUFFICIENT_EVIDENCE


def test_mismatch_takes_precedence_and_gpu_selection(blaze6: HardwareSnapshot) -> None:
    assert (
        assess_blaze6(
            blaze6.model_copy(update={"cpu_model": None, "physical_cores": 12})
        ).status
        is ProfileStatus.DOES_NOT_MATCH
    )
    other = GpuInfo(name="NVIDIA RTX 4090", vram_mib=24576)
    snapshot = blaze6.model_copy(update={"gpus": (other, *blaze6.gpus)})
    assert assess_blaze6(snapshot).status is ProfileStatus.MATCHES


def test_scan_mocked_facts_and_inaccessible_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hardware, "_cpu_model", lambda: "i7-13650HX")
    monkeypatch.setattr(psutil, "cpu_count", lambda logical=True: 20 if logical else 14)
    monkeypatch.setattr(
        psutil, "virtual_memory", lambda: SimpleNamespace(total=24 * 2**30)
    )
    monkeypatch.setattr(
        psutil,
        "disk_partitions",
        lambda all=False: [
            SimpleNamespace(device="D:", mountpoint="D:/", opts="rw,fixed"),
            SimpleNamespace(device="E:", mountpoint="E:/", opts="rw,fixed"),
            SimpleNamespace(device="Z:", mountpoint="Z:/", opts="rw,remote"),
        ],
    )

    def usage(mount: str) -> SimpleNamespace:
        if mount == "E:/":
            raise PermissionError()
        return SimpleNamespace(total=500 * 2**30, free=50 * 2**30)

    monkeypatch.setattr(psutil, "disk_usage", usage)
    monkeypatch.setattr(hardware, "_nvidia_gpus", lambda: ((), ("nvidia_smi_missing",)))
    snapshot = scan_hardware()
    assert snapshot.timestamp.tzinfo is UTC and snapshot.ram_bytes == 24 * 2**30
    assert len(snapshot.disks) == 2 and snapshot.disks[0].free_bytes == 50 * 2**30
    assert snapshot.disks[1].total_bytes is None
    assert "disk_usage_unavailable" in snapshot.issues and not snapshot.gpus


def test_import_models_and_explicit_scan_are_read_only(tmp_path: Path) -> None:
    script = """
import asyncio
import importlib.abc
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading

def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected I/O or background work")

def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()

class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("fastapi", "sqlalchemy", "alembic", "phoenix.api",
                 "phoenix.persistence", "phoenix.core.events", "phoenix.core.event_bus",
                 "phoenix.models", "phoenix.agents", "phoenix.tools",
                 "phoenix.permissions", "phoenix.voice")
        if any(fullname == root or fullname.startswith(root + ".") for root in roots):
            forbidden()

sys.dont_write_bytecode = True
sys.addaudithook(audit)
sys.meta_path.insert(0, BlockImports())
os.mkdir = forbidden
sqlite3.connect = forbidden
sqlite3.dbapi2.connect = forbidden
socket.socket.connect = forbidden
socket.socket.bind = forbidden
subprocess.Popen = forbidden
asyncio.create_task = forbidden
asyncio.BaseEventLoop.create_task = forbidden
threading.Thread.start = forbidden

import psutil
original_usage = psutil.disk_usage
original_count = psutil.cpu_count
original_memory = psutil.virtual_memory
original_partitions = psutil.disk_partitions
psutil.disk_usage = forbidden
psutil.cpu_count = forbidden
psutil.virtual_memory = forbidden
psutil.disk_partitions = forbidden
if sys.platform == "win32":
    import winreg
    original_open = winreg.OpenKey
    winreg.OpenKey = forbidden

# Import and all model/assessment construction while probes are blocked.
from phoenix.core import hardware
snapshot = hardware.HardwareSnapshot(platform="test")
hardware.GpuInfo(name="fixture")
hardware.DiskInfo(device="D:", mount="D:/")
hardware.assess_blaze6(snapshot)
hardware.HardwareSnapshot(platform="test")
psutil.disk_usage = original_usage
psutil.cpu_count = original_count
psutil.virtual_memory = original_memory
psutil.disk_partitions = original_partitions
if sys.platform == "win32":
    winreg.OpenKey = original_open

def query(argv, **kwargs):
    assert argv == ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader,nounits"]
    assert kwargs == dict(shell=False, capture_output=True, text=True, timeout=5,
                          check=False)
    return subprocess.CompletedProcess(argv, 0, "RTX 3050 Laptop GPU, 6144, 581.80", "")

subprocess.run = query
snapshot = hardware.scan_hardware()
assert snapshot.timestamp.utcoffset().total_seconds() == 0
assert not list(Path.cwd().iterdir())
print("Hardware import/construction/read-only scan: PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "Hardware import/construction/read-only scan: PASS"
