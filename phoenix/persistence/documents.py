"""Shared-Base document mappings; construction does no I/O or source access."""

from datetime import datetime

from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from phoenix.persistence.base import Base
from phoenix.persistence.memory import UTCDateTime


class DocumentRow(Base):
    __tablename__ = "documents"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_documents"),
        CheckConstraint("chunk_count >= 1", name="ck_documents_chunk_count"),
        CheckConstraint(
            "CASE WHEN json_valid(metadata) "
            "THEN json_type(metadata) = 'object' ELSE 0 END",
            name="ck_documents_metadata_object",
        ),
        Index("ix_documents_source_type", "source_type"),
        Index("ix_documents_source_ref", "source_ref"),
        Index("ix_documents_indexed_at", "indexed_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_ref: Mapped[str] = mapped_column(String, nullable=False)
    media_type: Mapped[str] = mapped_column(String, nullable=False)
    source_timestamp: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    source_hash: Mapped[str] = mapped_column(String, nullable=False)
    parser_id: Mapped[str] = mapped_column(String, nullable=False)
    parser_version: Mapped[str] = mapped_column(String, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    metadata_json: Mapped[dict[str, JsonValue]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )


class DocumentChunkRow(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_document_chunks"),
        ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_chunks_document_id_documents",
        ),
        UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_id_chunk_index",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_document_chunks_chunk_index"),
        CheckConstraint("page IS NULL OR page >= 1", name="ck_document_chunks_page"),
        CheckConstraint(
            "CASE WHEN json_valid(metadata) "
            "THEN json_type(metadata) = 'object' ELSE 0 END",
            name="ck_document_chunks_metadata_object",
        ),
        Index("ix_document_chunks_document_id", "document_id"),
        Index("ix_document_chunks_content_hash", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(String, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str | None] = mapped_column(String, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String, nullable=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(
        UTCDateTime(), nullable=True
    )
    parser_version: Mapped[str] = mapped_column(String, nullable=False)
    metadata_json: Mapped[dict[str, JsonValue]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
