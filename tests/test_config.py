"""PHX-004 settings contracts with isolated environment and filesystem input."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from phoenix.core.config import AppSettings


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in os.environ:
        if key.upper().startswith("PHOENIX_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def test_portable_defaults() -> None:
    # Pydantic's synthesized static signature omits this BaseSettings option.
    settings = AppSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.environment == "development"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000
    assert isinstance(settings.data_dir, Path)
    assert settings.data_dir == Path.home() / ".phoenix" / "data"
    assert set(AppSettings.model_fields) == {
        "environment",
        "api_host",
        "api_port",
        "data_dir",
        "web_research_enabled",  # PHX-111
    }
    assert settings.web_research_enabled is False


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIX_ENVIRONMENT", "test")
    monkeypatch.setenv("PHOENIX_API_HOST", "localhost")
    monkeypatch.setenv("PHOENIX_API_PORT", "9000")
    monkeypatch.setenv("PHOENIX_DATA_DIR", "environment-data")
    monkeypatch.setenv("PHOENIX_WEB_RESEARCH_ENABLED", "true")
    settings = AppSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.model_dump() == {
        "environment": "test",
        "api_host": "localhost",
        "api_port": 9000,
        "data_dir": Path("environment-data"),
        "web_research_enabled": True,
    }


def test_dotenv_and_precedence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "PHOENIX_ENVIRONMENT=production\nPHOENIX_API_HOST=localhost\n"
        "PHOENIX_API_PORT=9001\nPHOENIX_DATA_DIR=dotenv-data\n",
        encoding="utf-8",
    )
    dotenv_settings = AppSettings()
    assert dotenv_settings.model_dump() == {
        "environment": "production",
        "api_host": "localhost",
        "api_port": 9001,
        "data_dir": Path("dotenv-data"),
        "web_research_enabled": False,
    }
    monkeypatch.setenv("PHOENIX_API_PORT", "9002")
    assert AppSettings().api_port == 9002
    assert AppSettings(api_port=9003).api_port == 9003
    assert (
        AppSettings(_env_file=None).environment == "development"  # type: ignore[call-arg]
    )


@pytest.mark.parametrize("environment", ["development", "test", "production"])
def test_supported_environment(
    environment: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHOENIX_ENVIRONMENT", environment)
    assert AppSettings().environment == environment


@pytest.mark.parametrize(
    ("key", "value", "field", "error_type"),
    [
        ("PHOENIX_ENVIRONMENT", "staging", "environment", "literal_error"),
        ("PHOENIX_API_PORT", "0", "api_port", "greater_than_equal"),
        ("PHOENIX_API_PORT", "-1", "api_port", "greater_than_equal"),
        ("PHOENIX_API_PORT", "65536", "api_port", "less_than_equal"),
        ("PHOENIX_API_PORT", "not-a-port", "api_port", "int_parsing"),
    ],
)
def test_invalid_environment_values(
    key: str, value: str, field: str, error_type: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(key, value)
    with pytest.raises(ValidationError) as failure:
        AppSettings()
    assert failure.value.errors()[0]["loc"] == (field,)
    assert failure.value.errors()[0]["type"] == error_type


@pytest.mark.parametrize("port", [1, 65535])
def test_inclusive_port_bounds(port: int) -> None:
    assert AppSettings(api_port=port).api_port == port


def test_invalid_path_type() -> None:
    with pytest.raises(ValidationError) as failure:
        AppSettings.model_validate({"data_dir": 123})
    assert failure.value.errors()[0]["loc"] == ("data_dir",)


def test_unknown_constructor_key() -> None:
    with pytest.raises(ValidationError) as failure:
        AppSettings(api_prt=9000)  # type: ignore[call-arg]  # Intentional invalid input.
    assert failure.value.errors()[0]["type"] == "extra_forbidden"


def test_unknown_dotenv_key(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PHOENIX_API_PRT=9000\n", encoding="utf-8")
    with pytest.raises(ValidationError) as failure:
        AppSettings()
    assert failure.value.errors()[0]["type"] == "extra_forbidden"


def test_unknown_process_key_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # Standard pydantic-settings reads only declared process keys. Dotenv and
    # constructor overrides are strict, so authoritative files reject typos.
    monkeypatch.setenv("PHOENIX_API_PRT", "9000")
    assert AppSettings().api_port == 8000


def test_configured_directory_is_not_created(tmp_path: Path) -> None:
    data_dir = tmp_path / "missing" / "data"
    assert AppSettings(data_dir=data_dir).data_dir == data_dir
    assert not data_dir.parent.exists()


def test_import_and_construction_are_isolated(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PHOENIX_API_PORT=9020\n", encoding="utf-8")
    script = """
import asyncio
import importlib.abc
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys

class BlockSubsystems(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        boundaries = ("api", "agents", "models", "tools", "memory", "permissions",
                      "voice", "recovery", "persistence")
        if fullname == "fastapi" or any(
            fullname == f"phoenix.{name}" or fullname.startswith(f"phoenix.{name}.")
            for name in boundaries
        ):
            raise AssertionError(f"Unexpected subsystem import: {fullname}")

def forbidden(*args, **kwargs):
    raise AssertionError("Settings attempted filesystem/network/process/task work")

sys.dont_write_bytecode = True
sys.meta_path.insert(0, BlockSubsystems())
os.mkdir = forbidden
pathlib.Path.mkdir = forbidden
pathlib.Path.write_text = forbidden
pathlib.Path.write_bytes = forbidden
socket.socket.bind = forbidden
socket.socket.connect = forbidden
subprocess.Popen = forbidden
sqlite3.connect = forbidden
asyncio.create_task = forbidden
asyncio.BaseEventLoop.create_task = forbidden

from phoenix.core.config import AppSettings
assert not any(isinstance(value, AppSettings) for value in
               vars(sys.modules["phoenix.core.config"]).values())
defaults = AppSettings(_env_file=None)
assert defaults.api_port == 8000
configured = AppSettings(data_dir=pathlib.Path("missing") / "data")
assert configured.api_port == 9020
assert not configured.data_dir.exists()
assert defaults is not AppSettings(_env_file=None)
print("Settings import/construction isolation: PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "Settings import/construction isolation: PASS"
