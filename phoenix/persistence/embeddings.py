"""Shared-Base embedding evidence; no extension, provider or source lookup."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from phoenix.persistence.base import Base
from phoenix.persistence.memory import UTCDateTime


class EmbeddingRecordRow(Base):
    __tablename__ = "embedding_records"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_embedding_records"),
        UniqueConstraint(
            "source_kind",
            "source_id",
            "provider_id",
            "model_id",
            name="uq_embedding_records_source_model",
        ),
        CheckConstraint("dimensions >= 1", name="ck_embedding_records_dimensions"),
        CheckConstraint(
            "CASE WHEN json_valid(vector) THEN json_type(vector) = 'array' ELSE 0 END",
            name="ck_embedding_records_vector_array",
        ),
        Index("ix_embedding_records_source", "source_kind", "source_id"),
        Index("ix_embedding_records_model", "provider_id", "model_id"),
        Index("ix_embedding_records_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_kind: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    source_hash: Mapped[str] = mapped_column(String, nullable=False)
    provider_id: Mapped[str] = mapped_column(String, nullable=False)
    model_id: Mapped[str] = mapped_column(String, nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[list[float]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
