"""PHX-032 document/chunk evidence only; no vectors or retrieval schema."""

import sqlalchemy as sa

from alembic import op

revision: str = "0003_document_ingestion"
down_revision: str | None = "0002_memory_schema"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=False),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(), nullable=True),
        sa.Column("source_hash", sa.String(), nullable=False),
        sa.Column("parser_id", sa.String(), nullable=False),
        sa.Column("parser_version", sa.String(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("indexed_at", sa.DateTime(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.CheckConstraint("chunk_count >= 1", name="ck_documents_chunk_count"),
        sa.CheckConstraint(
            "CASE WHEN json_valid(metadata) "
            "THEN json_type(metadata) = 'object' ELSE 0 END",
            name="ck_documents_metadata_object",
        ),
    )
    op.create_index("ix_documents_source_type", "documents", ["source_type"])
    op.create_index("ix_documents_source_ref", "documents", ["source_ref"])
    op.create_index("ix_documents_indexed_at", "documents", ["indexed_at"])
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(), nullable=True),
        sa.Column("parser_version", sa.String(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_chunks_document_id_documents",
        ),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            name="uq_document_chunks_document_id_chunk_index",
        ),
        sa.CheckConstraint("chunk_index >= 0", name="ck_document_chunks_chunk_index"),
        sa.CheckConstraint("page IS NULL OR page >= 1", name="ck_document_chunks_page"),
        sa.CheckConstraint(
            "CASE WHEN json_valid(metadata) "
            "THEN json_type(metadata) = 'object' ELSE 0 END",
            name="ck_document_chunks_metadata_object",
        ),
    )
    op.create_index(
        "ix_document_chunks_document_id", "document_chunks", ["document_id"]
    )
    op.create_index(
        "ix_document_chunks_content_hash", "document_chunks", ["content_hash"]
    )


def downgrade() -> None:
    op.drop_index("ix_document_chunks_content_hash", table_name="document_chunks")
    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_index("ix_documents_indexed_at", table_name="documents")
    op.drop_index("ix_documents_source_ref", table_name="documents")
    op.drop_index("ix_documents_source_type", table_name="documents")
    op.drop_table("documents")
