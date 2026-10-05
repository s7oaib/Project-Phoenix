"""Shared-Base audit evidence mapping; registration performs no I/O."""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from phoenix.persistence.base import Base
from phoenix.persistence.memory import UTCDateTime


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_audit_events"),
        CheckConstraint(
            "event_kind IN ('permission_decision', 'tool_outcome')",
            name="ck_audit_events_kind",
        ),
        CheckConstraint(
            "risk_level IN ('P0', 'P1', 'P2', 'P3', 'P4', 'P5')",
            name="ck_audit_events_risk",
        ),
        CheckConstraint(
            "permission_status IN ('allowed', 'approval_required', "
            "'denied', 'blocked')",
            name="ck_audit_events_permission_status",
        ),
        CheckConstraint(
            "permission_reason_code IN ('p0_internal', 'explicit_user_approval', "
            "'explicit_user_denial', 'matching_grant', 'matching_policy', "
            "'p1_scope_required', 'p2_approval_required', 'p3_approval_required', "
            "'p4_explicit_approval_required', 'p5_blocked')",
            name="ck_audit_events_permission_reason",
        ),
        CheckConstraint(
            "length(request_reason_hash) = 64 AND "
            "request_reason_hash NOT GLOB '*[^0-9a-f]*'",
            name="ck_audit_events_reason_hash",
        ),
        CheckConstraint(
            "tool_result_status IS NULL OR tool_result_status IN ('success', "
            "'partial_success', 'denied', 'timeout', 'cancelled', 'failure', "
            "'unavailable_dependency')",
            name="ck_audit_events_result_status",
        ),
        CheckConstraint(
            "event_kind <> 'permission_decision' OR tool_result_status IS NULL",
            name="ck_audit_events_decision_result",
        ),
        CheckConstraint(
            "event_kind <> 'tool_outcome' OR tool_result_status IS NOT NULL",
            name="ck_audit_events_outcome_result",
        ),
        CheckConstraint(
            "event_kind <> 'tool_outcome' OR permission_status = 'allowed'",
            name="ck_audit_events_outcome_allowed",
        ),
        CheckConstraint(
            "event_kind <> 'tool_outcome' OR risk_level <> 'P5'",
            name="ck_audit_events_outcome_risk",
        ),
        CheckConstraint(
            "event_kind <> 'tool_outcome' OR tool_result_status <> 'denied'",
            name="ck_audit_events_outcome_not_denied",
        ),
        Index("ix_audit_events_request_id", "request_id"),
        Index("ix_audit_events_tool_id", "tool_id"),
        Index("ix_audit_events_occurred_at", "occurred_at"),
        Index("ix_audit_events_risk_level", "risk_level"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    event_kind: Mapped[str] = mapped_column(String, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    request_id: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    tool_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource: Mapped[str] = mapped_column(String, nullable=False)
    request_reason_hash: Mapped[str] = mapped_column(String, nullable=False)
    risk_level: Mapped[str] = mapped_column(String, nullable=False)
    permission_requirement: Mapped[str] = mapped_column(String, nullable=False)
    permission_status: Mapped[str] = mapped_column(String, nullable=False)
    permission_reason_code: Mapped[str] = mapped_column(String, nullable=False)
    matched_grant_id: Mapped[str | None] = mapped_column(String, nullable=True)
    matched_policy_rule_id: Mapped[str | None] = mapped_column(String, nullable=True)
    user_decision_id: Mapped[str | None] = mapped_column(String, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tool_result_status: Mapped[str | None] = mapped_column(String, nullable=True)
