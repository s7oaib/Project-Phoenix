"""PHX-108 saved default mission path (ADR-111).

``app_settings`` accepts a second key, ``default_mission_path``, whose value
must be ``auto``, ``direct`` or ``complex``. Existing rows are unchanged.
"""

from alembic import op

revision: str = "0009_default_mission_path"
down_revision: str | None = "0008_mission_triage"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings", recreate="always") as batch:
        batch.drop_constraint("ck_app_settings_key", type_="check")
        batch.create_check_constraint(
            "ck_app_settings_key",
            "key IN ('workspace_root', 'default_mission_path')",
        )
        batch.create_check_constraint(
            "ck_app_settings_mission_path",
            "key <> 'default_mission_path' OR value IN ('auto', 'direct', 'complex')",
        )


def downgrade() -> None:
    op.execute("DELETE FROM app_settings WHERE key = 'default_mission_path'")
    with op.batch_alter_table("app_settings", recreate="always") as batch:
        batch.drop_constraint("ck_app_settings_mission_path", type_="check")
        batch.drop_constraint("ck_app_settings_key", type_="check")
        batch.create_check_constraint(
            "ck_app_settings_key", "key IN ('workspace_root')"
        )
