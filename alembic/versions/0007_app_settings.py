"""PHX-104 user-writable settings (only the document workspace root in v1)."""

import sqlalchemy as sa

from alembic import op

revision: str = "0007_app_settings"
down_revision: str | None = "0006_missions"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_app_settings"),
        sa.CheckConstraint("key IN ('workspace_root')", name="ck_app_settings_key"),
        sa.CheckConstraint("length(value) > 0", name="ck_app_settings_value"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
