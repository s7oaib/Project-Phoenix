"""Explicit read-only local hardware facts and evidence-based BLAZE-6 assessment."""

import csv
import io
import re
import subprocess
import sys
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

PositiveInt = Annotated[int, Field(gt=0, strict=True)]
NonnegativeInt = Annotated[int, Field(ge=0, strict=True)]


class HardwareModel(BaseModel):
    """Immutable validated hardware information without discovery on construction."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class GpuInfo(HardwareModel):
    name: str = Field(min_length=1)
    vram_mib: PositiveInt | None = None
    driver_version: str | None = None


class DiskInfo(HardwareModel):
    device: str
    mount: str
    total_bytes: PositiveInt | None = None
    free_bytes: NonnegativeInt | None = None


class HardwareSnapshot(HardwareModel):
    timestamp: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))
    platform: str
    cpu_model: str | None = None
    physical_cores: PositiveInt | None = None
    logical_processors: PositiveInt | None = None
    ram_bytes: PositiveInt | None = None
    disks: tuple[DiskInfo, ...] = ()
    gpus: tuple[GpuInfo, ...] = ()
    issues: tuple[str, ...] = ()

    @field_validator("timestamp")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class CheckStatus(StrEnum):
    MATCH = "match"
    MISMATCH = "mismatch"
    UNAVAILABLE = "unavailable"


class ProfileStatus(StrEnum):
    MATCHES = "matches"
    DOES_NOT_MATCH = "does_not_match"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ProfileCheck(HardwareModel):
    field: str
    observed: str | int | None
    expected: str | int
    status: CheckStatus
    reason: str


class Blaze6Assessment(HardwareModel):
    profile: str = "BLAZE-6"
    status: ProfileStatus
    checks: tuple[ProfileCheck, ...]


def _cpu_model() -> str | None:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "ProcessorNameString")
                return (
                    value.strip() if isinstance(value, str) and value.strip() else None
                )
        except OSError:
            return None
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition(":")
                if separator and key.strip() == "model name" and value.strip():
                    return value.strip()
        except OSError:
            pass
    # A generic architecture label is not evidence of a processor model.
    return None


def parse_nvidia_output(output: str) -> tuple[tuple[GpuInfo, ...], tuple[str, ...]]:
    """Parse independent CSV rows; retain valid evidence and flag invalid rows."""
    gpus: list[GpuInfo] = []
    issues: list[str] = []
    for row in csv.reader(io.StringIO(output), strict=True):
        if not row or all(not item.strip() for item in row):
            continue
        if len(row) != 3 or row[0].strip() in ("", "N/A", "[N/A]"):
            issues.append("nvidia_malformed_row")
            continue
        name, memory, driver = (item.strip() for item in row)
        if not memory.isascii() or not memory.isdecimal() or int(memory) <= 0:
            issues.append("nvidia_invalid_vram")
            continue
        gpus.append(
            GpuInfo(
                name=name,
                vram_mib=int(memory),
                driver_version=None if driver in ("", "N/A", "[N/A]") else driver,
            )
        )
    if not gpus:
        issues.append("nvidia_no_valid_rows")
    return tuple(gpus), tuple(issues)


def _nvidia_gpus() -> tuple[tuple[GpuInfo, ...], tuple[str, ...]]:
    try:
        result = subprocess.run(
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
    except FileNotFoundError:
        return (), ("nvidia_smi_missing",)
    except subprocess.TimeoutExpired:
        return (), ("nvidia_smi_timeout",)
    except (OSError, UnicodeError):
        return (), ("nvidia_smi_unavailable",)
    if result.returncode != 0:
        return (), ("nvidia_smi_failed",)
    try:
        return parse_nvidia_output(result.stdout)
    except csv.Error:
        return (), ("nvidia_malformed_csv",)


def scan_hardware() -> HardwareSnapshot:
    """Read local facts explicitly; only the fixed bounded NVIDIA query runs."""
    import psutil

    issues: list[str] = []
    try:
        physical = psutil.cpu_count(logical=False)
        logical = psutil.cpu_count(logical=True)
    except (OSError, NotImplementedError):
        physical = logical = None
        issues.append("cpu_counts_unavailable")
    try:
        ram = psutil.virtual_memory().total
    except (OSError, NotImplementedError):
        ram = None
        issues.append("ram_unavailable")
    disks: list[DiskInfo] = []
    try:
        partitions = psutil.disk_partitions(all=False)
    except (OSError, NotImplementedError):
        partitions = []
        issues.append("disks_unavailable")
    for partition in partitions:
        # psutil's physical partition list can include remote Windows drives.
        if "remote" in partition.opts.split(",") or partition.device.startswith("\\\\"):
            continue
        total: int | None
        free: int | None
        try:
            usage = psutil.disk_usage(partition.mountpoint)
            total, free = usage.total, usage.free
        except OSError:
            total = free = None
            issues.append("disk_usage_unavailable")
        disks.append(
            DiskInfo(
                device=partition.device,
                mount=partition.mountpoint,
                total_bytes=total,
                free_bytes=free,
            )
        )
    gpus, gpu_issues = _nvidia_gpus()
    return HardwareSnapshot(
        platform=sys.platform,
        cpu_model=_cpu_model(),
        physical_cores=physical,
        logical_processors=logical,
        ram_bytes=ram,
        disks=tuple(disks),
        gpus=gpus,
        issues=tuple(issues) + gpu_issues,
    )


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _gpu_identity(value: str) -> tuple[str, ...]:
    """Ignore compact GB/GiB annotations and optional NVIDIA branding only."""
    tokens = [
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if re.fullmatch(r"[0-9]+g(?:i)?b", token) is None
    ]
    if tokens and tokens[0] == "nvidia":
        tokens.pop(0)
        if tokens and tokens[0] == "geforce":
            tokens.pop(0)
    return tuple(tokens)


def assess_blaze6(snapshot: HardwareSnapshot) -> Blaze6Assessment:
    """Six stable checks; RAM 22..26 GiB inclusive, VRAM exactly 6144 MiB."""
    checks: list[ProfileCheck] = []

    def add(
        field: str, observed: str | int | None, expected: str | int, matches: bool
    ) -> None:
        status = (
            CheckStatus.UNAVAILABLE
            if observed is None
            else CheckStatus.MATCH
            if matches
            else CheckStatus.MISMATCH
        )
        checks.append(
            ProfileCheck(
                field=field,
                observed=observed,
                expected=expected,
                status=status,
                reason="Evidence unavailable"
                if observed is None
                else "Observed evidence matches"
                if matches
                else "Observed evidence contradicts profile",
            )
        )

    add(
        "cpu_model",
        snapshot.cpu_model,
        "i7-13650HX",
        snapshot.cpu_model is not None
        and "i713650hx" in _normalized(snapshot.cpu_model),
    )
    add("physical_cores", snapshot.physical_cores, 14, snapshot.physical_cores == 14)
    add(
        "logical_processors",
        snapshot.logical_processors,
        20,
        snapshot.logical_processors == 20,
    )
    add(
        "ram_bytes",
        snapshot.ram_bytes,
        "22..26 GiB inclusive",
        snapshot.ram_bytes is not None
        and 22 * 2**30 <= snapshot.ram_bytes <= 26 * 2**30,
    )
    gpu = next(
        (
            gpu
            for gpu in snapshot.gpus
            if _gpu_identity(gpu.name) == ("rtx", "3050", "laptop", "gpu")
        ),
        None,
    )
    # Compare VRAM from the matching GPU, never add devices or assume index zero.
    if gpu is None and snapshot.gpus:
        add(
            "gpu_model",
            "; ".join(item.name for item in snapshot.gpus),
            "NVIDIA GeForce RTX 3050 Laptop GPU",
            False,
        )
        add("vram_mib", None, 6144, False)
    else:
        add(
            "gpu_model",
            gpu.name if gpu else None,
            "NVIDIA GeForce RTX 3050 Laptop GPU",
            gpu is not None,
        )
        add(
            "vram_mib",
            gpu.vram_mib if gpu else None,
            6144,
            gpu is not None and gpu.vram_mib == 6144,
        )
    statuses = {check.status for check in checks}
    status = (
        ProfileStatus.DOES_NOT_MATCH
        if CheckStatus.MISMATCH in statuses
        else ProfileStatus.INSUFFICIENT_EVIDENCE
        if CheckStatus.UNAVAILABLE in statuses
        else ProfileStatus.MATCHES
    )
    return Blaze6Assessment(status=status, checks=tuple(checks))
