"""Explicit Alembic execution, independent of the API and future subsystems."""

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from alembic import context
from phoenix.core.config import AppSettings
from phoenix.persistence.base import Base
from phoenix.persistence.database import database_url
from phoenix.persistence.documents import (
    DocumentChunkRow,  # noqa: F401 — register target metadata
    DocumentRow,  # noqa: F401 — register target metadata
)
from phoenix.persistence.memory import (
    MemoryRow,  # noqa: F401 — register target metadata
)


def run_migrations() -> None:
    """Use a caller URL override or validated configuration; never create dirs."""
    configured_url = context.config.get_main_option("sqlalchemy.url")
    url = make_url(configured_url) if configured_url else database_url(AppSettings())
    if url.drivername != "sqlite":
        raise ValueError("Phoenix migrations require the built-in SQLite driver")

    if context.is_offline_mode():
        context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
