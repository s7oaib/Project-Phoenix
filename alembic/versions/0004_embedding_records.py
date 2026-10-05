"""PHX-033 portable JSON vector evidence only; no extension or retrieval schema."""

import sqlalchemy as sa

from alembic import op

revision: str = "0004_embedding_records"
down_revision: str | None = "0003_document_ingestion"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "embedding_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source_kind", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("source_hash", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_embedding_records"),
        sa.UniqueConstraint(
            "source_kind",
            "source_id",
            "provider_id",
            "model_id",
            name="uq_embedding_records_source_model",
        ),
        sa.CheckConstraint("dimensions >= 1", name="ck_embedding_records_dimensions"),
        sa.CheckConstraint(
            "CASE WHEN json_valid(vector) THEN json_type(vector) = 'array' ELSE 0 END",
            name="ck_embedding_records_vector_array",
        ),
    )
    op.create_index(
        "ix_embedding_records_source", "embedding_records", ["source_kind", "source_id"]
    )
    op.create_index(
        "ix_embedding_records_model", "embedding_records", ["provider_id", "model_id"]
    )
    op.create_index(
        "ix_embedding_records_created_at", "embedding_records", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_embedding_records_created_at", table_name="embedding_records")
    op.drop_index("ix_embedding_records_model", table_name="embedding_records")
    op.drop_index("ix_embedding_records_source", table_name="embedding_records")
    op.drop_table("embedding_records")
