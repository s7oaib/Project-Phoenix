"""PHX-105 request triage: a mission's path may wait for PHOENIX to decide it.

``missions.path`` becomes nullable (NULL until triage decides) and the new
``triage_outcome`` records who decided: ``decided`` (the triage model),
``fallback`` (triage failed, so ``complex``) or ``client``. Every earlier
mission's path was the client's choice, so existing rows become ``client``.
"""

import sqlalchemy as sa

from alembic import op

revision: str = "0008_mission_triage"
down_revision: str | None = "0007_app_settings"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("missions", sa.Column("triage_outcome", sa.String(), nullable=True))
    op.execute("UPDATE missions SET triage_outcome = 'client'")
    with op.batch_alter_table("missions", recreate="always") as batch:
        batch.alter_column("path", existing_type=sa.String(), nullable=True)
        batch.create_check_constraint(
            "ck_missions_triage_outcome",
            "triage_outcome IN ('decided', 'fallback', 'client')",
        )
        batch.create_check_constraint(
            "ck_missions_triage_decided",
            "(path IS NULL) = (triage_outcome IS NULL)",
        )
        batch.create_check_constraint(
            "ck_missions_triage_fallback",
            "triage_outcome <> 'fallback' OR path = 'complex'",
        )


def downgrade() -> None:
    # A mission still waiting for triage takes the fallback path, COMPLEX.
    op.execute(
        "UPDATE missions SET path = 'complex', triage_outcome = 'fallback' "
        "WHERE path IS NULL"
    )
    with op.batch_alter_table("missions", recreate="always") as batch:
        batch.drop_constraint("ck_missions_triage_fallback", type_="check")
        batch.drop_constraint("ck_missions_triage_decided", type_="check")
        batch.drop_constraint("ck_missions_triage_outcome", type_="check")
        batch.alter_column("path", existing_type=sa.String(), nullable=False)
        batch.drop_column("triage_outcome")
