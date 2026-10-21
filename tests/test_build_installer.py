"""PHX-092 installer build script (ADR-125 to ADR-129), with fakes only.

No download, uv, npm, cargo or staged interpreter runs here: the runner and the
downloader are injected, and every artifact is written under tmp_path.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phoenix_build_installer", ROOT / "scripts" / "build_installer.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bi = _load()

REQUIREMENTS = """\
alembic==1.20.0 \\
    --hash=sha256:aa
    # via phoenix
pywin32==312 ; sys_platform == 'win32' \\
    --hash=sha256:bb
    # via pywinauto
faster-whisper==1.2.0 \\
    --hash=sha256:cc
"""


def embed_zip(path: Path, *, pth: bool = True, extra: str | None = None) -> bytes:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("python.exe", b"MZ")
        archive.writestr("python311.zip", b"PK")
        if pth:
            archive.writestr(bi.PTH_NAME, "python311.zip\n.\n#import site\n")
        if extra is not None:
            archive.writestr(extra, b"x")
    return path.read_bytes()


class FakeRunner:
    """Records commands and imitates uv/git/the staged interpreter."""

    def __init__(self, smoke: dict[str, object] | None = None) -> None:
        self.commands: list[tuple[list[str], Path]] = []
        self.smoke = smoke or {
            "phoenix": "0.1.0",
            "python": bi.PYTHON_VERSION,
            "user_site": False,
            "isolated": True,
        }

    def __call__(self, command: Sequence[str], cwd: Path) -> str:
        command = list(command)
        self.commands.append((command, cwd))
        if command[:2] == ["uv", "export"]:
            output = Path(command[command.index("--output-file") + 1])
            output.write_text(REQUIREMENTS, encoding="utf-8")
        elif command[:3] == ["uv", "pip", "install"]:
            target = Path(command[command.index("--target") + 1])
            (target / "alembic").mkdir(parents=True)
        elif command[:2] == ["uv", "build"]:
            out = Path(command[command.index("--out-dir") + 1])
            out.mkdir(parents=True)
            with zipfile.ZipFile(out / "phoenix-0.1.0-py3-none-any.whl", "w") as w:
                w.writestr("phoenix/api/__main__.py", "")
                w.writestr("phoenix-0.1.0.dist-info/METADATA", "Version: 0.1.0\n")
        elif command[:2] == ["git", "ls-files"]:
            return "alembic.ini\0alembic/env.py\0"
        elif command[1:3] == ["-I", "-c"]:
            return "noise\n" + json.dumps(self.smoke) + "\n"
        return ""

    def find(self, *prefix: str) -> list[str]:
        matches = [c for c, _ in self.commands if c[: len(prefix)] == list(prefix)]
        assert len(matches) == 1, prefix
        return matches[0]


def test_embeddable_python_is_pinned_to_python_org_and_sha256() -> None:
    assert bi.PYTHON_VERSION.startswith("3.11.")
    assert bi.EMBED_URL == (
        f"https://www.python.org/ftp/python/{bi.PYTHON_VERSION}/"
        f"python-{bi.PYTHON_VERSION}-embed-amd64.zip"
    )
    assert len(bi.EMBED_SHA256) == 64 and int(bi.EMBED_SHA256, 16) >= 0


def test_download_is_verified_and_cached(tmp_path: Path) -> None:
    payload = embed_zip(tmp_path / "source.zip")
    digest = hashlib.sha256(payload).hexdigest()
    calls: list[str] = []

    def fetch(url: str, destination: Path) -> None:
        calls.append(url)
        destination.write_bytes(payload)

    cache = tmp_path / "cache"
    first = bi.fetch_embeddable(cache, fetch, expected=digest)
    assert calls == [bi.EMBED_URL] and first.read_bytes() == payload
    assert bi.fetch_embeddable(cache, fetch, expected=digest) == first
    assert calls == [bi.EMBED_URL]  # the verified cache is reused


def test_digest_mismatch_fails_and_keeps_nothing(tmp_path: Path) -> None:
    def fetch(url: str, destination: Path) -> None:
        destination.write_bytes(b"tampered")

    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / bi.EMBED_NAME).write_bytes(b"stale")  # a corrupt cache is replaced
    with pytest.raises(bi.BuildError, match="sha256 mismatch"):
        bi.fetch_embeddable(cache, fetch, expected="0" * 64)
    assert list(cache.iterdir()) == []


def test_python_is_staged_with_an_isolating_import_path(tmp_path: Path) -> None:
    archive = tmp_path / "embed.zip"
    embed_zip(archive)
    python_dir = tmp_path / "python"
    bi.stage_python(archive, python_dir)
    pth = (python_dir / bi.PTH_NAME).read_text(encoding="ascii")
    assert pth.splitlines() == [
        "python311.zip",
        ".",
        "Lib\\site-packages",
        "..\\app",
        "import site",
    ]
    assert (python_dir / "Lib" / "site-packages").is_dir()


@pytest.mark.parametrize("unsafe", ["../escape.txt", "/rooted.txt"])
def test_unsafe_archive_members_are_refused(tmp_path: Path, unsafe: str) -> None:
    archive = tmp_path / "embed.zip"
    embed_zip(archive, extra=unsafe)
    with pytest.raises(bi.BuildError, match="unsafe path"):
        bi.stage_python(archive, tmp_path / "python")
    assert not (tmp_path / "escape.txt").exists()


def test_archive_without_pth_is_refused(tmp_path: Path) -> None:
    archive = tmp_path / "embed.zip"
    embed_zip(archive, pth=False)
    with pytest.raises(bi.BuildError, match="_pth missing"):
        bi.stage_python(archive, tmp_path / "python")


def test_locked_pins_follow_the_lock_generically() -> None:
    # A later ticket's native dependency appears with no change to the script.
    assert bi.locked_pins(REQUIREMENTS) == [
        "alembic==1.20.0",
        "pywin32==312",
        "faster-whisper==1.2.0",
    ]


def test_stage_runtime_uses_the_lock_hashes_and_binary_wheels_only(
    tmp_path: Path,
) -> None:
    archive_source = tmp_path / "embed-source.zip"
    payload = embed_zip(archive_source)

    def fetch(url: str, destination: Path) -> None:
        destination.write_bytes(payload)

    runner = FakeRunner()
    stage = tmp_path / "stage"
    (stage / "leftover").mkdir(parents=True)  # a previous build is discarded
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / bi.EMBED_NAME).write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    result = bi.stage_runtime(runner, fetch, stage=stage, cache=cache, expected=digest)

    export = runner.find("uv", "export")
    for flag in ("--frozen", "--no-dev", "--no-emit-project"):
        assert flag in export
    install = runner.find("uv", "pip", "install")
    assert install[install.index("--only-binary") + 1] == ":all:"
    assert install[install.index("--python-platform") + 1] == "x86_64-pc-windows-msvc"
    assert install[install.index("--python-version") + 1] == "3.11"
    for flag in ("--require-hashes", "--no-deps", "--no-config"):
        assert flag in install
    assert Path(install[install.index("--target") + 1]) == (
        stage / "python" / "Lib" / "site-packages"
    )
    smoke, cwd = next((c, d) for c, d in runner.commands if c[1:3] == ["-I", "-c"])
    assert Path(smoke[0]) == stage / "python" / "python.exe" and cwd == stage / "app"

    assert not (stage / "leftover").exists()
    assert (stage / "app" / "phoenix" / "api" / "__main__.py").is_file()
    assert (stage / "app" / "phoenix-0.1.0.dist-info" / "METADATA").is_file()
    assert (stage / "app" / "alembic.ini").is_file()
    assert (stage / "app" / "alembic" / "env.py").is_file()
    manifest = json.loads((stage / "runtime-manifest.json").read_text("utf-8"))
    assert manifest == result.manifest
    assert manifest["packages"] == list(result.packages) == bi.locked_pins(REQUIREMENTS)
    assert manifest["python"] == {
        "version": bi.PYTHON_VERSION,
        "source": bi.EMBED_URL,
        "sha256": digest,
    }


@pytest.mark.parametrize(
    "smoke",
    [
        {"python": "3.11.4"},
        {"phoenix": ""},
        {"user_site": True},
        {"isolated": False},
    ],
)
def test_smoke_rejects_a_wrong_or_leaky_runtime(
    tmp_path: Path, smoke: dict[str, object]
) -> None:
    runner = FakeRunner()
    runner.smoke = {**runner.smoke, **smoke}
    with pytest.raises(bi.BuildError, match="smoke"):
        bi.smoke(runner, tmp_path)


def test_bundle_builds_only_the_nsis_target_with_the_installer_config(
    tmp_path: Path,
) -> None:
    nsis = tmp_path / "bundle" / "nsis"

    def runner(command: Sequence[str], cwd: Path) -> str:
        assert list(command) == bi.tauri_command()
        nsis.mkdir(parents=True)
        (nsis / "PHOENIX_0.1.0_x64-setup.exe").write_bytes(b"MZ")
        return ""

    assert bi.bundle(runner, tmp_path / "bundle").name == "PHOENIX_0.1.0_x64-setup.exe"
    command = bi.tauri_command()
    assert command[command.index("--bundles") + 1] == "nsis"
    assert Path(command[command.index("--config") + 1]) == bi.INSTALLER_CONFIG
    with pytest.raises(bi.BuildError, match="one NSIS installer"):
        bi.bundle(lambda command, cwd: "", tmp_path / "empty")


def test_installer_config_maps_only_the_staged_runtime_and_adds_no_capability() -> None:
    config = json.loads(bi.INSTALLER_CONFIG.read_text(encoding="utf-8"))
    assert set(config) == {"$schema", "bundle"}  # no app, security or plugin keys
    bundle = config["bundle"]
    assert bundle["active"] is True and bundle["targets"] == ["nsis"]
    assert bundle["resources"] == {"../../../build/phoenix-runtime/": "backend/"}
    nsis = bundle["windows"]["nsis"]
    assert nsis["installMode"] == "currentUser"  # no elevation, no machine install
    assert "certificateThumbprint" not in bundle["windows"]  # unsigned (ADR-128)
    src_tauri = ROOT / "apps" / "desktop" / "src-tauri"
    assert (src_tauri / nsis["installerHooks"]).resolve() == (
        ROOT / "packaging" / "windows" / "installer-hooks.nsh"
    )
    assert (src_tauri / "../../../build/phoenix-runtime").resolve() == bi.STAGE


def test_uninstall_hook_keeps_user_data_unless_opted_in() -> None:
    hooks = (ROOT / "packaging" / "windows" / "installer-hooks.nsh").read_text("utf-8")
    code = [
        line.strip()
        for line in hooks.splitlines()
        if line.strip() and not line.strip().startswith(";")
    ]
    assert code == [
        "!macro NSIS_HOOK_POSTUNINSTALL",
        'RMDir /r /REBOOTOK "$INSTDIR\\backend"',
        'RMDir "$INSTDIR"',
        "${If} $DeleteAppDataCheckboxState = 1",
        "${AndIf} $UpdateMode <> 1",
        '${AndIf} $PROFILE != ""',
        'RMDir /r "$PROFILE\\.phoenix\\data"',
        'RMDir "$PROFILE\\.phoenix"',
        "${EndIf}",
        "!macroend",
    ]


def test_build_outputs_are_ignored_by_git() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "build/" in ignore and "target/" in ignore


def test_main_refuses_other_platforms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bi.os, "name", "posix")
    assert bi.main(["--stage-only"]) == 2
