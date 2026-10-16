"""PHX-102 chat history, missions, trace summaries and permission evidence."""

import sqlalchemy as sa

from alembic import op

revision: str = "0006_missions"
down_revision: str | None = "0005_audit_events"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_conversations"),
        sa.CheckConstraint("updated_at >= created_at", name="ck_conversations_updated"),
    )
    op.create_index("ix_conversations_created_at", "conversations", ["created_at"])
    op.create_table(
        "messages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation_id_conversations",
        ),
        sa.CheckConstraint("role IN ('user', 'phoenix')", name="ck_messages_role"),
        sa.CheckConstraint("length(text) > 0", name="ck_messages_text"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_created_at", "messages", ["created_at"])
    op.create_table(
        "missions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=False),
        sa.Column("answer_message_id", sa.String(), nullable=True),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.Column("responder_error", sa.String(), nullable=True),
        sa.Column("rerun_of", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_missions"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_missions_conversation_id_conversations",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], name="fk_missions_message_id_messages"
        ),
        sa.ForeignKeyConstraint(
            ["answer_message_id"],
            ["messages.id"],
            name="fk_missions_answer_message_id_messages",
        ),
        sa.ForeignKeyConstraint(
            ["rerun_of"], ["missions.id"], name="fk_missions_rerun_of_missions"
        ),
        sa.CheckConstraint("path IN ('direct', 'complex')", name="ck_missions_path"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled', "
            "'waiting_permission', 'superseded')",
            name="ck_missions_status",
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (failure_code IS NOT NULL)",
            name="ck_missions_failure",
        ),
        sa.CheckConstraint(
            "CASE WHEN failure_code = 'responder_output' "
            "THEN responder_error IS NOT NULL ELSE responder_error IS NULL END",
            name="ck_missions_responder_error",
        ),
        sa.CheckConstraint(
            "(status = 'completed') = (answer_message_id IS NOT NULL)",
            name="ck_missions_answer",
        ),
        sa.CheckConstraint(
            "rerun_of IS NULL OR rerun_of <> id", name="ck_missions_rerun_of"
        ),
    )
    op.create_index("ix_missions_conversation_id", "missions", ["conversation_id"])
    op.create_index("ix_missions_status", "missions", ["status"])
    op.create_index("ix_missions_created_at", "missions", ["created_at"])
    op.create_index("ix_missions_rerun_of", "missions", ["rerun_of"])
    op.create_table(
        "mission_traces",
        sa.Column("mission_id", sa.String(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("trace", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("mission_id", name="pk_mission_traces"),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name="fk_mission_traces_mission_id_missions",
        ),
        sa.CheckConstraint(
            "CASE WHEN json_valid(trace) THEN json_type(trace) = 'object' ELSE 0 END",
            name="ck_mission_traces_trace_object",
        ),
    )
    op.create_table(
        "permission_requests",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("mission_id", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("tool_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_permission_requests"),
        sa.ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name="fk_permission_requests_mission_id_missions",
        ),
        sa.UniqueConstraint("mission_id", name="uq_permission_requests_mission_id"),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied')",
            name="ck_permission_requests_status",
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)",
            name="ck_permission_requests_decided",
        ),
    )
    op.create_index("ix_permission_requests_status", "permission_requests", ["status"])
    op.create_table(
        "permission_grants",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("actor_id", sa.String(), nullable=False),
        sa.Column("tool_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("risk_level", sa.String(), nullable=False),
        sa.Column("duration", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("user_decision", sa.String(), nullable=False),
        sa.Column("issued_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("approved_request_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_permission_grants"),
        sa.CheckConstraint(
            "duration IN ('session', 'persistent')",
            name="ck_permission_grants_duration",
        ),
        sa.CheckConstraint(
            "(duration = 'session') = (session_id IS NOT NULL)",
            name="ck_permission_grants_session",
        ),
        sa.CheckConstraint(
            "risk_level IN ('P1', 'P2', 'P3')", name="ck_permission_grants_risk"
        ),
        sa.CheckConstraint(
            "user_decision = 'approved'", name="ck_permission_grants_user_decision"
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_permission_grants_revoked",
        ),
    )
    op.create_index("ix_permission_grants_tool_id", "permission_grants", ["tool_id"])


def downgrade() -> None:
    op.drop_index("ix_permission_grants_tool_id", table_name="permission_grants")
    op.drop_table("permission_grants")
    op.drop_index("ix_permission_requests_status", table_name="permission_requests")
    op.drop_table("permission_requests")
    op.drop_table("mission_traces")
    op.drop_index("ix_missions_rerun_of", table_name="missions")
    op.drop_index("ix_missions_created_at", table_name="missions")
    op.drop_index("ix_missions_status", table_name="missions")
    op.drop_index("ix_missions_conversation_id", table_name="missions")
    op.drop_table("missions")
    op.drop_index("ix_messages_created_at", table_name="messages")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_created_at", table_name="conversations")
    op.drop_table("conversations")
