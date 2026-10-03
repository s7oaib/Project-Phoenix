"""Explicit local SQLite construction and location preparation."""

from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from phoenix.core.config import AppSettings


def database_path(settings: AppSettings) -> Path:
    """Return the configured database path without touching the filesystem."""
    return settings.data_dir / "phoenix.db"


def database_url(settings: AppSettings) -> URL:
    """Build a SQLite URL without interpreting path characters as URL syntax."""
    return URL.create("sqlite", database=str(database_path(settings)))


def prepare_data_directory(settings: AppSettings) -> None:
    """Explicitly create the data location before connection or migration."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)


def create_engine_for_settings(settings: AppSettings) -> Engine:
    """Create a lazy engine; connection and schema changes remain explicit."""
    return create_engine(database_url(settings))


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a factory without opening a session or connection."""
    return sessionmaker(bind=engine)
