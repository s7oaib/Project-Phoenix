"""Build the PHOENIX Windows installer (PHX-092, ADR-125 to ADR-129).

Stages a self-contained backend under ``build/phoenix-runtime`` and bundles it with
the Tauri desktop shell into an unsigned, per-user NSIS installer:

1. the official CPython embeddable package (pinned version, SHA-256 verified),
   cached in ``build/installer-cache``;
2. every runtime wheel exactly as locked in ``uv.lock`` (``uv export --frozen
   --no-dev``, hash-checked, binary wheels only, installed for win_amd64 /
   CPython 3.11), so dependencies added by later tickets are picked up without
   changing this script;
3. the PHOENIX package from its own wheel (``uv build --wheel``), plus the tracked
   Alembic migrations and ``alembic.ini``;
4. a smoke import with the staged interpreter, and ``runtime-manifest.json``;
5. ``tauri build --bundles nsis`` with ``packaging/windows/tauri.installer.conf.json``
   merged over the app's own config, which maps the staged runtime to
   ``backend/`` next to the executable.

Ollama and models are prerequisites and are never downloaded (ADR-027/028 policy,
ADR-127). Build outputs stay under ``build/`` and the Tauri target directory; none
is committed.

Usage::

    uv run python scripts/build_installer.py              # stage and bundle
    uv run python scripts/build_installer.py --stage-only # backend runtime only

Exit codes: 0 success, 1 build failure, 2 unsupported platform.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
CACHE = BUILD / "installer-cache"
STAGE = BUILD / "phoenix-runtime"
INSTALLER_CONFIG = ROOT / "packaging" / "windows" / "tauri.installer.conf.json"
BUNDLE_DIR = ROOT / "apps" / "desktop" / "src-tauri" / "target" / "release" / "bundle"

# The last CPython 3.11 release with Windows binaries (later 3.11 releases are
# source-only security fixes). The digest was computed from the python.org file and
# its MD5 matched python.org's published checksum (6d9aa08531d48fcc261ba667e2df17c4).
PYTHON_VERSION = "3.11.9"
EMBED_NAME = f"python-{PYTHON_VERSION}-embed-amd64.zip"
EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{EMBED_NAME}"
EMBED_SHA256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"
PYTHON_PLATFORM = "x86_64-pc-windows-msvc"

# python311._pth: the import path of the embedded interpreter. Its presence makes
# CPython ignore PYTHONPATH/PYTHONHOME and the registry; ``import site`` enables
# site-packages and its .pth files (pywin32 needs them).
PTH_NAME = "python311._pth"
PTH_TEXT = "python311.zip\n.\nLib\\site-packages\n..\\app\nimport site\n"

SMOKE = (
    "import importlib.metadata as m, json, site, sys; "
    "import phoenix.api.server, phoenix.api.launcher; "
    "print(json.dumps({'phoenix': m.version('phoenix'), "
    "'python': sys.version.split()[0], 'user_site': site.ENABLE_USER_SITE, "
    "'isolated': bool(sys.flags.ignore_environment)}))"
)

Runner = Callable[[Sequence[str], Path], str]
Downloader = Callable[[str, Path], None]


class BuildError(RuntimeError):
    """A build step failed; the message says which and why."""


def run(command: Sequence[str], cwd: Path) -> str:
    """Runs one command (argument array, no shell); its stdout, or BuildError."""
    shown = " ".join(command)
    print(f"$ {shown}", flush=True)
    executable = shutil.which(command[0]) or command[0]
    result = subprocess.run(
        [executable, *command[1:]],
        cwd=cwd,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise BuildError(f"command failed ({result.returncode}): {shown}")
    return result.stdout


def download(url: str, destination: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - https
        with destination.open("wb") as handle:
            shutil.copyfileobj(response, handle)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_embeddable(
    cache: Path, fetch: Downloader = download, expected: str = EMBED_SHA256
) -> Path:
    """The embeddable zip from the cache or python.org, only if its digest matches."""
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / EMBED_NAME
    if target.is_file() and sha256_of(target) == expected:
        return target
    target.unlink(missing_ok=True)
    partial = target.with_suffix(".part")
    partial.unlink(missing_ok=True)
    fetch(EMBED_URL, partial)
    actual = sha256_of(partial)
    if actual != expected:
        partial.unlink(missing_ok=True)
        raise BuildError(f"{EMBED_NAME} sha256 mismatch: got {actual}")
    partial.replace(target)
    return target


def stage_python(archive: Path, python_dir: Path) -> None:
    """Extracts the interpreter and writes its import path file."""
    with zipfile.ZipFile(archive) as bundle:
        for name in bundle.namelist():
            if name.startswith(("/", "\\")) or ".." in Path(name).parts:
                raise BuildError(f"unsafe path in {archive.name}: {name}")
        bundle.extractall(python_dir)
    if not (python_dir / PTH_NAME).is_file():
        raise BuildError(f"{PTH_NAME} missing from {archive.name}")
    (python_dir / PTH_NAME).write_text(PTH_TEXT, encoding="ascii", newline="\n")
    (python_dir / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)


def export_requirements(runner: Runner, destination: Path) -> list[str]:
    """The locked runtime closure (no dev group, no project) with hashes."""
    runner(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--no-header",
            "--format",
            "requirements-txt",
            "--output-file",
            str(destination),
        ],
        ROOT,
    )
    pins = locked_pins(destination.read_text(encoding="utf-8"))
    if not pins:
        raise BuildError("uv export produced no runtime requirements")
    return pins


def locked_pins(requirements: str) -> list[str]:
    """``name==version`` lines (markers dropped) from an exported requirements file."""
    pins = []
    for line in requirements.splitlines():
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", line)
        if match:
            pins.append(f"{match.group(1)}=={match.group(2)}")
    return pins


def install_wheels(runner: Runner, requirements: Path, site_packages: Path) -> None:
    """Hash-checked binary wheels for the target interpreter; never source builds."""
    runner(
        [
            "uv",
            "pip",
            "install",
            "--target",
            str(site_packages),
            "--python-version",
            "3.11",
            "--python-platform",
            PYTHON_PLATFORM,
            "--only-binary",
            ":all:",
            "--no-deps",
            "--require-hashes",
            "--link-mode",
            "copy",
            "--no-config",
            "--requirement",
            str(requirements),
        ],
        ROOT,
    )


def stage_app(runner: Runner, app_dir: Path, work: Path) -> None:
    """The PHOENIX wheel's contents plus the tracked migrations and alembic.ini."""
    wheels = work / "wheel"
    runner(["uv", "build", "--wheel", "--out-dir", str(wheels)], ROOT)
    built = sorted(wheels.glob("phoenix-*.whl"))
    if len(built) != 1:
        raise BuildError(f"expected one phoenix wheel, found {len(built)}")
    with zipfile.ZipFile(built[0]) as wheel:
        wheel.extractall(app_dir)
    tracked = runner(["git", "ls-files", "-z", "--", "alembic", "alembic.ini"], ROOT)
    names = [name for name in tracked.split("\0") if name]
    if "alembic.ini" not in names or not any(n.startswith("alembic/") for n in names):
        raise BuildError("alembic.ini or alembic/ is not tracked")
    for name in names:
        destination = app_dir / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)


def smoke(runner: Runner, stage: Path) -> dict[str, object]:
    """Imports the backend with the staged interpreter, isolated from the build env."""
    python = stage / "python" / "python.exe"
    # -I exactly as the desktop shell starts it (ADR-125).
    output = runner([str(python), "-I", "-c", SMOKE], stage / "app")
    report: dict[str, object] = json.loads(output.strip().splitlines()[-1])
    if (
        report.get("python") != PYTHON_VERSION
        or not report.get("phoenix")
        or report.get("user_site") is not False
        or report.get("isolated") is not True
    ):
        raise BuildError(f"staged runtime smoke check failed: {report}")
    return report


@dataclass(frozen=True)
class StageResult:
    stage: Path
    packages: tuple[str, ...]
    manifest: dict[str, object]


def stage_runtime(
    runner: Runner = run,
    fetch: Downloader = download,
    stage: Path = STAGE,
    cache: Path = CACHE,
    expected: str = EMBED_SHA256,
) -> StageResult:
    """Builds ``stage`` from scratch: python/, app/ and runtime-manifest.json."""
    if stage.exists():
        shutil.rmtree(stage)
    python_dir = stage / "python"
    site_packages = python_dir / "Lib" / "site-packages"
    archive = fetch_embeddable(cache, fetch, expected)
    stage_python(archive, python_dir)
    with tempfile.TemporaryDirectory(prefix="phoenix-installer-") as scratch:
        work = Path(scratch)
        requirements = work / "requirements.txt"
        pins = export_requirements(runner, requirements)
        install_wheels(runner, requirements, site_packages)
        stage_app(runner, stage / "app", work)
    report = smoke(runner, stage)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "phoenix": report["phoenix"],
        "python": {
            "version": PYTHON_VERSION,
            "source": EMBED_URL,
            "sha256": expected,
        },
        "packages": pins,
    }
    (stage / "runtime-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return StageResult(stage=stage, packages=tuple(pins), manifest=manifest)


def tauri_command(config: Path = INSTALLER_CONFIG) -> list[str]:
    return [
        "npm",
        "run",
        "tauri",
        "--workspace",
        "@phoenix/desktop",
        "--",
        "build",
        "--bundles",
        "nsis",
        "--config",
        str(config),
    ]


def bundle(runner: Runner = run, bundle_dir: Path = BUNDLE_DIR) -> Path:
    """Runs the Tauri NSIS build; the one installer it produced."""
    nsis = bundle_dir / "nsis"
    if nsis.exists():
        shutil.rmtree(nsis)
    runner(tauri_command(), ROOT)
    installers = sorted(nsis.glob("*-setup.exe"))
    if len(installers) != 1:
        raise BuildError(f"expected one NSIS installer, found {len(installers)}")
    return installers[0]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--stage-only", action="store_true", help="stage the backend runtime only"
    )
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("The PHOENIX installer is built on Windows only.", file=sys.stderr)
        return 2
    try:
        result = stage_runtime()
        print(f"staged runtime: {result.stage} ({len(result.packages)} packages)")
        if args.stage_only:
            return 0
        installer = bundle()
    except BuildError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    size = installer.stat().st_size
    print(f"installer: {installer} ({size} bytes, sha256 {sha256_of(installer)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
