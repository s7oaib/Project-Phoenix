"""PHX-131 pre-triage rule (ADR-227): ``triage_outcome`` accepts ``rule``.

``rule`` means PHOENIX's deterministic pre-triage rule chose the path without
a model call; it only ever chooses ``direct``. Existing rows are unchanged.
"""

from alembic import op

revision: str = "0010_triage_rule"
down_revision: str | None = "0009_default_mission_path"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("missions", recreate="always") as batch:
        batch.drop_constraint("ck_missions_triage_outcome", type_="check")
        batch.create_check_constraint(
            "ck_missions_triage_outcome",
            "triage_outcome IN ('decided', 'fallback', 'client', 'rule')",
        )
        batch.create_check_constraint(
            "ck_missions_triage_rule",
            "triage_outcome <> 'rule' OR path = 'direct'",
        )


def downgrade() -> None:
    # Before PHX-131 PHOENIX itself decided every untold path: 'decided'.
    op.execute(
        "UPDATE missions SET triage_outcome = 'decided' WHERE triage_outcome = 'rule'"
    )
    with op.batch_alter_table("missions", recreate="always") as batch:
        batch.drop_constraint("ck_missions_triage_rule", type_="check")
        batch.drop_constraint("ck_missions_triage_outcome", type_="check")
        batch.create_check_constraint(
            "ck_missions_triage_outcome",
            "triage_outcome IN ('decided', 'fallback', 'client')",
        )
