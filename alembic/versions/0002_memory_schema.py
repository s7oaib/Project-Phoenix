"""PHX-030 memory domain table only; no service or vector schema."""

import sqlalchemy as sa

from alembic import op

revision: str = "0002_memory_schema"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_ref", sa.String(), nullable=True),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("embedding_ref", sa.String(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_memories"),
        sa.CheckConstraint(
            "type IN ('working', 'episodic', 'semantic', 'user', 'project', 'agent')",
            name="ck_memories_type",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_memories_confidence"
        ),
        sa.CheckConstraint(
            "importance >= 0 AND importance <= 1", name="ck_memories_importance"
        ),
        sa.CheckConstraint(
            "CASE WHEN json_valid(metadata) "
            "THEN json_type(metadata) = 'object' ELSE 0 END",
            name="ck_memories_metadata_object",
        ),
    )
    op.create_index("ix_memories_type", "memories", ["type"])
    op.create_index("ix_memories_scope", "memories", ["scope"])
    op.create_index("ix_memories_created_at", "memories", ["created_at"])
    op.create_index("ix_memories_expires_at", "memories", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_memories_expires_at", table_name="memories")
    op.drop_index("ix_memories_created_at", table_name="memories")
    op.drop_index("ix_memories_scope", table_name="memories")
    op.drop_index("ix_memories_type", table_name="memories")
    op.drop_table("memories")
