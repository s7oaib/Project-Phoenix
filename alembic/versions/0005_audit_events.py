"""PHX-042 append-only permission/outcome evidence, without execution."""

import sqlalchemy as sa

from alembic import op

revision: str = "0005_audit_events"
down_revision: str | None = "0004_embedding_records"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("event_kind", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("request_id", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("tool_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=False),
        sa.Column("request_reason_hash", sa.String(), nullable=False),
        sa.Column("risk_level", sa.String(), nullable=False),
        sa.Column("permission_requirement", sa.String(), nullable=False),
        sa.Column("permission_status", sa.String(), nullable=False),
        sa.Column("permission_reason_code", sa.String(), nullable=False),
        sa.Column("matched_grant_id", sa.String(), nullable=True),
        sa.Column("matched_policy_rule_id", sa.String(), nullable=True),
        sa.Column("user_decision_id", sa.String(), nullable=True),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("correlation_id", sa.String(), nullable=True),
        sa.Column("tool_result_status", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audit_events"),
        sa.CheckConstraint(
            "event_kind IN ('permission_decision', 'tool_outcome')",
            name="ck_audit_events_kind",
        ),
        sa.CheckConstraint(
            "risk_level IN ('P0', 'P1', 'P2', 'P3', 'P4', 'P5')",
            name="ck_audit_events_risk",
        ),
        sa.CheckConstraint(
            "permission_status IN ('allowed', 'approval_required', "
            "'denied', 'blocked')",
            name="ck_audit_events_permission_status",
        ),
        sa.CheckConstraint(
            "permission_reason_code IN ('p0_internal', 'explicit_user_approval', "
            "'explicit_user_denial', 'matching_grant', 'matching_policy', "
            "'p1_scope_required', 'p2_approval_required', 'p3_approval_required', "
            "'p4_explicit_approval_required', 'p5_blocked')",
            name="ck_audit_events_permission_reason",
        ),
        sa.CheckConstraint(
            "length(request_reason_hash) = 64 AND "
            "request_reason_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_audit_events_reason_hash",
        ),
        sa.CheckConstraint(
            "tool_result_status IS NULL OR tool_result_status IN ('success', "
            "'partial_success', 'denied', 'timeout', 'cancelled', 'failure', "
            "'unavailable_dependency')",
            name="ck_audit_events_result_status",
        ),
        sa.CheckConstraint(
            "event_kind <> 'permission_decision' OR tool_result_status IS NULL",
            name="ck_audit_events_decision_result",
        ),
        sa.CheckConstraint(
            "event_kind <> 'tool_outcome' OR tool_result_status IS NOT NULL",
            name="ck_audit_events_outcome_result",
        ),
        sa.CheckConstraint(
            "event_kind <> 'tool_outcome' OR permission_status = 'allowed'",
            name="ck_audit_events_outcome_allowed",
        ),
        sa.CheckConstraint(
            "event_kind <> 'tool_outcome' OR risk_level <> 'P5'",
            name="ck_audit_events_outcome_risk",
        ),
        sa.CheckConstraint(
            "event_kind <> 'tool_outcome' OR tool_result_status <> 'denied'",
            name="ck_audit_events_outcome_not_denied",
        ),
    )
    op.create_index("ix_audit_events_request_id", "audit_events", ["request_id"])
    op.create_index("ix_audit_events_tool_id", "audit_events", ["tool_id"])
    op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
    op.create_index("ix_audit_events_risk_level", "audit_events", ["risk_level"])
    op.execute(
        "CREATE TRIGGER trg_audit_events_no_update BEFORE UPDATE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'Audit history is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER trg_audit_events_no_delete BEFORE DELETE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'Audit history is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_audit_events_no_update")
    op.execute("DROP TRIGGER trg_audit_events_no_delete")
    op.drop_index("ix_audit_events_risk_level", table_name="audit_events")
    op.drop_index("ix_audit_events_occurred_at", table_name="audit_events")
    op.drop_index("ix_audit_events_tool_id", table_name="audit_events")
    op.drop_index("ix_audit_events_request_id", table_name="audit_events")
    op.drop_table("audit_events")
