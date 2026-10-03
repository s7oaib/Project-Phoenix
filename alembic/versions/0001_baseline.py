"""Schema-empty PHX-005 baseline; Alembic owns its version tracking table."""

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Introduce no Phoenix domain schema."""


def downgrade() -> None:
    """No domain schema exists to revert."""
