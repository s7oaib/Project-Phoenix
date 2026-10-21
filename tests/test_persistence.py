"""SQLite lifecycle and real Alembic checks, exclusively against temporary data."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from alembic import command
from phoenix.api.app import create_app
from phoenix.core.config import AppSettings
from phoenix.persistence.database import (
    create_engine_for_settings,
    create_session_factory,
    database_path,
    database_url,
    prepare_data_directory,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    for key in os.environ:
        if key.upper().startswith("PHOENIX_"):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
    return AppSettings(data_dir=tmp_path / "data")


@pytest.fixture
def migration_config(settings: AppSettings) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    # ConfigParser needs escaped percent signs; URL.create preserves path syntax.
    config.set_main_option(
        "sqlalchemy.url", str(database_url(settings)).replace("%", "%%")
    )
    return config


def test_path_url_and_lazy_construction(settings: AppSettings) -> None:
    assert database_path(settings) == settings.data_dir / "phoenix.db"
    url = database_url(settings)
    assert url.drivername == "sqlite"
    assert url.database == str(database_path(settings))
    assert str(url).startswith("sqlite:///")
    engine = create_engine_for_settings(settings)
    try:
        factory = create_session_factory(engine)
        with factory() as session:
            assert isinstance(session, Session)
        assert not settings.data_dir.exists()
        # Metadata registration does not initialize a database. Other tests may
        # intentionally import domain mappings into the shared Base.
        assert not database_path(settings).exists()
    finally:
        engine.dispose()


def test_explicit_prepare_connect_and_session(settings: AppSettings) -> None:
    prepare_data_directory(settings)
    prepare_data_directory(settings)  # Idempotent; never recreates/deletes data.
    assert settings.data_dir.is_dir()
    assert not database_path(settings).exists()
    engine = create_engine_for_settings(settings)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
        assert database_path(settings).is_file()
        with create_session_factory(engine)() as session:
            assert session.execute(text("SELECT 2")).scalar_one() == 2
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_missing_directory_failure_propagates(settings: AppSettings) -> None:
    engine = create_engine_for_settings(settings)
    try:
        with pytest.raises(OperationalError), engine.connect():
            pass
        assert not settings.data_dir.exists()
    finally:
        engine.dispose()


@pytest.mark.parametrize("directory", ["space data", "percent%data", "hash#query?data"])
def test_url_preserves_path_syntax(directory: str, settings: AppSettings) -> None:
    settings.data_dir = settings.data_dir.parent / directory
    assert database_url(settings).database == str(database_path(settings))
    # '?' cannot occur in a Windows filename, but remains literal URL data.
    assert not settings.data_dir.exists()


def test_alembic_baseline_lifecycle(
    settings: AppSettings, migration_config: Config
) -> None:
    scripts = ScriptDirectory.from_config(migration_config)
    assert scripts.get_heads() == ["0009_default_mission_path"]
    baseline = scripts.get_revision("0001")
    assert baseline is not None and baseline.down_revision is None
    memory_revision = scripts.get_revision("0002_memory_schema")
    assert memory_revision is not None and memory_revision.down_revision == "0001"
    prepare_data_directory(settings)
    command.upgrade(migration_config, "0001")
    engine = create_engine_for_settings(settings)
    try:
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision() == "0001"
            )
            assert inspect(connection).get_table_names() == ["alembic_version"]
        command.current(migration_config)
        command.downgrade(migration_config, "base")
        with engine.connect() as connection:
            assert MigrationContext.configure(connection).get_current_revision() is None
            assert inspect(connection).get_table_names() == ["alembic_version"]
        command.upgrade(migration_config, "0001")
        with engine.connect() as connection:
            assert (
                MigrationContext.configure(connection).get_current_revision() == "0001"
            )
    finally:
        engine.dispose()


def test_migration_url_override_with_percent_path(settings: AppSettings) -> None:
    settings.data_dir = settings.data_dir.parent / "percent%data"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option(
        "sqlalchemy.url", str(database_url(settings)).replace("%", "%%")
    )
    prepare_data_directory(settings)
    command.upgrade(config, "head")
    assert database_path(settings).is_file()


def test_alembic_uses_settings_when_no_url(
    settings: AppSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PHOENIX_DATA_DIR", str(settings.data_dir))
    prepare_data_directory(settings)
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    assert database_path(settings).is_file()


def test_migrations_reject_non_sqlite(migration_config: Config) -> None:
    migration_config.set_main_option("sqlalchemy.url", "postgresql://localhost/phoenix")
    with pytest.raises(ValueError, match="built-in SQLite"):
        command.upgrade(migration_config, "head")


def test_health_remains_database_independent(settings: AppSettings) -> None:
    with TestClient(create_app()) as client:
        assert client.get("/health").json() == {
            "status": "ok",
            "service": "phoenix-api",
            "version": "0.1.0",
        }
    assert not settings.data_dir.exists()


def test_fresh_import_and_construction_are_side_effect_free(
    settings: AppSettings,
) -> None:
    script = """
import asyncio
import importlib.abc
import os
from pathlib import Path
import platform
import socket
import sqlite3
import subprocess
import sys

# SQLAlchemy queries platform.machine(); on Windows Python's cold platform
# cache can launch `ver`. Initialize OS metadata before guarding runtime work.
platform.uname()

def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected filesystem/database/network/process/task work")

class BlockSubsystems(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        names = ("agents", "models", "tools", "memory", "permissions", "voice",
                 "recovery")
        if any(fullname == f"phoenix.{name}" or
               fullname.startswith(f"phoenix.{name}.") for name in names):
            raise AssertionError(fullname)

sys.dont_write_bytecode = True
sys.meta_path.insert(0, BlockSubsystems())
os.mkdir = forbidden
Path.mkdir = forbidden
Path.write_text = forbidden
Path.write_bytes = forbidden
sqlite3.connect = forbidden
sqlite3.dbapi2.connect = forbidden
socket.socket.connect = forbidden
socket.socket.bind = forbidden
subprocess.Popen = forbidden
asyncio.create_task = forbidden
asyncio.BaseEventLoop.create_task = forbidden

from phoenix.core.config import AppSettings
from phoenix.persistence.base import Base
from phoenix.persistence.database import (
    database_path, database_url, create_engine_for_settings, create_session_factory
)
from phoenix.api.app import create_app
settings = AppSettings(data_dir=Path("never-created") / "data")
assert not settings.data_dir.exists()
assert database_path(settings).name == "phoenix.db"
assert database_url(settings).drivername == "sqlite"
engine = create_engine_for_settings(settings)
with create_session_factory(engine)() as session:
    assert not session.in_transaction()
assert not Base.metadata.tables
create_app()
assert not settings.data_dir.parent.exists()
engine.dispose()
print("Persistence/config/API isolation: PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=settings.data_dir.parent,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert result.stdout.strip() == "Persistence/config/API isolation: PASS"
