"""Shared-Base memory mapping; importing/constructing rows performs no I/O."""

from datetime import UTC, datetime

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from phoenix.persistence.base import Base


class UTCDateTime(TypeDecorator[datetime]):
    """Store naive UTC in SQLite DateTime; restore UTC awareness on reads."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("Memory persistence requires timezone-aware datetimes")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None else None


class MemoryRow(Base):
    """One durable row; callers explicitly own validation, sessions and migrations."""

    __tablename__ = "memories"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_memories"),
        CheckConstraint(
            "type IN ('working', 'episodic', 'semantic', 'user', 'project', 'agent')",
            name="ck_memories_type",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_memories_confidence"
        ),
        CheckConstraint(
            "importance >= 0 AND importance <= 1", name="ck_memories_importance"
        ),
        CheckConstraint(
            "CASE WHEN json_valid(metadata) "
            "THEN json_type(metadata) = 'object' ELSE 0 END",
            name="ck_memories_metadata_object",
        ),
        Index("ix_memories_type", "type"),
        Index("ix_memories_scope", "scope"),
        Index("ix_memories_created_at", "created_at"),
        Index("ix_memories_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    memory_type: Mapped[str] = mapped_column("type", String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    scope: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    importance: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    embedding_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    metadata_json: Mapped[dict[str, JsonValue]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
