"""PHX-102 chat history, missions, trace summaries and permission evidence.

Rows plus MissionStore (ADR-086). Each store method is one short synchronous
transaction, serialized in-process by a lock, and every status change is
compare-and-set (``UPDATE ... WHERE status = ...``), so a stale or racing writer
changes nothing instead of overwriting a newer state.

Content policy: the only free text stored is the user's message and PHOENIX's
final completed answer (``messages``) plus the pending PermissionRequest,
including its resource path, so the user can decide on it. Traces are the
runtime's MissionTrace: hashes, lengths, IDs and statuses only. Grants are
created only by ``approve`` from a user decision (INV-003).
"""

import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlalchemy import (
    JSON,
    CheckConstraint,
    ColumnElement,
    ForeignKeyConstraint,
    Index,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    delete,
    literal_column,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, sessionmaker

from phoenix.persistence.base import Base
from phoenix.persistence.memory import UTCDateTime
from phoenix.security.permissions import (
    GrantDuration,
    PermissionGrant,
    PermissionRequest,
)


class MissionStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING_PERMISSION = "waiting_permission"
    SUPERSEDED = "superseded"


ACTIVE_STATUSES = (MissionStatus.QUEUED, MissionStatus.RUNNING)


class MessageRole(StrEnum):
    USER = "user"
    PHOENIX = "phoenix"


class RequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


MissionPath = Literal["direct", "complex"]
# Mirrors phoenix.agents.triage.TriageOutcome (tests assert equality); the
# persistence layer imports no agents module.
TriageDecider = Literal["decided", "fallback", "client"]
TRIAGE_OUTCOMES: tuple[TriageDecider, ...] = ("decided", "fallback", "client")

# Failure codes the store and service add to the runtime's MissionFailureCode.
PERMISSION_DENIED = "permission_denied"
INTERRUPTED = "interrupted"
INTERNAL_ERROR = "internal_error"


class ConversationRow(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_conversations"),
        CheckConstraint("updated_at >= created_at", name="ck_conversations_updated"),
        Index("ix_conversations_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MessageRow(Base):
    __tablename__ = "messages"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_messages"),
        ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_messages_conversation_id_conversations",
        ),
        CheckConstraint("role IN ('user', 'phoenix')", name="ck_messages_role"),
        CheckConstraint("length(text) > 0", name="ck_messages_text"),
        Index("ix_messages_conversation_id", "conversation_id"),
        Index("ix_messages_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MissionRow(Base):
    __tablename__ = "missions"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_missions"),
        ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name="fk_missions_conversation_id_conversations",
        ),
        ForeignKeyConstraint(
            ["message_id"], ["messages.id"], name="fk_missions_message_id_messages"
        ),
        ForeignKeyConstraint(
            ["answer_message_id"],
            ["messages.id"],
            name="fk_missions_answer_message_id_messages",
        ),
        ForeignKeyConstraint(
            ["rerun_of"], ["missions.id"], name="fk_missions_rerun_of_missions"
        ),
        CheckConstraint("path IN ('direct', 'complex')", name="ck_missions_path"),
        # PHX-105 (ADR-090): a NULL path is still to be triaged; once a path is
        # set, triage_outcome records who decided it.
        CheckConstraint(
            "triage_outcome IN ('decided', 'fallback', 'client')",
            name="ck_missions_triage_outcome",
        ),
        CheckConstraint(
            "(path IS NULL) = (triage_outcome IS NULL)",
            name="ck_missions_triage_decided",
        ),
        CheckConstraint(
            "triage_outcome <> 'fallback' OR path = 'complex'",
            name="ck_missions_triage_fallback",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled', "
            "'waiting_permission', 'superseded')",
            name="ck_missions_status",
        ),
        CheckConstraint(
            "(status = 'failed') = (failure_code IS NOT NULL)",
            name="ck_missions_failure",
        ),
        CheckConstraint(
            "CASE WHEN failure_code = 'responder_output' "
            "THEN responder_error IS NOT NULL ELSE responder_error IS NULL END",
            name="ck_missions_responder_error",
        ),
        CheckConstraint(
            "(status = 'completed') = (answer_message_id IS NOT NULL)",
            name="ck_missions_answer",
        ),
        CheckConstraint(
            "rerun_of IS NULL OR rerun_of <> id", name="ck_missions_rerun_of"
        ),
        Index("ix_missions_conversation_id", "conversation_id"),
        Index("ix_missions_status", "status"),
        Index("ix_missions_created_at", "created_at"),
        Index("ix_missions_rerun_of", "rerun_of"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String, nullable=False)
    message_id: Mapped[str] = mapped_column(String, nullable=False)
    answer_message_id: Mapped[str | None] = mapped_column(String, nullable=True)
    path: Mapped[str | None] = mapped_column(String, nullable=True)
    triage_outcome: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String, nullable=True)
    responder_error: Mapped[str | None] = mapped_column(String, nullable=True)
    rerun_of: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class MissionTraceRow(Base):
    __tablename__ = "mission_traces"
    __table_args__ = (
        PrimaryKeyConstraint("mission_id", name="pk_mission_traces"),
        ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name="fk_mission_traces_mission_id_missions",
        ),
        CheckConstraint(
            "CASE WHEN json_valid(trace) THEN json_type(trace) = 'object' ELSE 0 END",
            name="ck_mission_traces_trace_object",
        ),
    )

    mission_id: Mapped[str] = mapped_column(String, primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    trace: Mapped[dict[str, JsonValue]] = mapped_column(JSON, nullable=False)


class PermissionRequestRow(Base):
    __tablename__ = "permission_requests"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_permission_requests"),
        ForeignKeyConstraint(
            ["mission_id"],
            ["missions.id"],
            name="fk_permission_requests_mission_id_missions",
        ),
        UniqueConstraint("mission_id", name="uq_permission_requests_mission_id"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'denied')",
            name="ck_permission_requests_status",
        ),
        CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)",
            name="ck_permission_requests_decided",
        ),
        Index("ix_permission_requests_status", "status"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    mission_id: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    tool_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class PermissionGrantRow(Base):
    __tablename__ = "permission_grants"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_permission_grants"),
        CheckConstraint(
            "duration IN ('session', 'persistent')",
            name="ck_permission_grants_duration",
        ),
        CheckConstraint(
            "(duration = 'session') = (session_id IS NOT NULL)",
            name="ck_permission_grants_session",
        ),
        CheckConstraint(
            "risk_level IN ('P1', 'P2', 'P3')", name="ck_permission_grants_risk"
        ),
        CheckConstraint(
            "user_decision = 'approved'", name="ck_permission_grants_user_decision"
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= issued_at",
            name="ck_permission_grants_revoked",
        ),
        Index("ix_permission_grants_tool_id", "tool_id"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    tool_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    risk_level: Mapped[str] = mapped_column(String, nullable=False)
    duration: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    user_decision: Mapped[str] = mapped_column(String, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # Provenance only: the request the user approved. No foreign key, because
    # grants outlive the 30-day chat retention that purges requests.
    approved_request_id: Mapped[str] = mapped_column(String, nullable=False)


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ConversationRecord(_Record):
    conversation_id: str
    created_at: datetime
    updated_at: datetime


class MessageRecord(_Record):
    message_id: str
    conversation_id: str
    role: MessageRole
    text: str
    created_at: datetime


class PermissionRequestRecord(_Record):
    mission_id: str
    status: RequestStatus
    decided_at: datetime | None
    request: PermissionRequest


class MissionRecord(_Record):
    mission_id: str
    conversation_id: str
    message_id: str
    answer_message_id: str | None
    path: MissionPath | None  # None until triage decides (PHX-105)
    triage_outcome: TriageDecider | None
    status: MissionStatus
    failure_code: str | None
    responder_error: str | None
    rerun_of: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    permission_request: PermissionRequestRecord | None


class GrantStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"  # a SESSION grant from an earlier launch
    REVOKED = "revoked"


class GrantRecord(_Record):
    grant: PermissionGrant
    approved_request_id: str
    revoked_at: datetime | None
    status: GrantStatus


class ActiveGrants(_Record):
    grants: tuple[PermissionGrant, ...]
    skipped: int  # stored rows that are not valid PermissionGrants


class ClaimedMission(_Record):
    mission_id: str
    text: str
    path: MissionPath | None  # None: PHOENIX triages the request (PHX-105)
    triage_outcome: TriageDecider | None


class PurgeCounts(_Record):
    conversations: int
    messages: int
    missions: int


@dataclass(frozen=True)
class Settlement:
    """A finished run, recorded only if the mission is still RUNNING.

    ``path`` and ``triage_outcome`` (PHX-105) are the decision the run made;
    they are recorded only on a mission still waiting for triage's decision.
    """

    status: MissionStatus
    trace: dict[str, Any] | None = None
    answer_text: str | None = None
    failure_code: str | None = None
    responder_error: str | None = None
    pending: PermissionRequest | None = None
    path: str | None = None
    triage_outcome: str | None = None


class MissionStoreError(Exception):
    """A lookup or state precondition failed; nothing was changed."""


class RecordNotFoundError(MissionStoreError, LookupError):
    pass


class InvalidStateError(MissionStoreError):
    pass


_T = TypeVar("_T")
_SETTLED = (
    MissionStatus.COMPLETED,
    MissionStatus.FAILED,
    MissionStatus.CANCELLED,
    MissionStatus.WAITING_PERMISSION,
)


def _order(column: Any) -> tuple[Any, ...]:
    # created_at ties (same clock tick) fall back to insertion order.
    return (column, literal_column("rowid"))


class MissionStore:
    """Synchronous, thread-safe access to PHX-102 tables (call via to_thread)."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory
        self._lock = threading.Lock()

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        with self._lock, self._sessions.begin() as session:
            yield session

    def _read(self, query: Callable[[Session], _T]) -> _T:
        with self._lock, self._sessions() as session:
            return query(session)

    # ---- conversations and submission

    def create_conversation(self, conversation_id: str, now: datetime) -> None:
        with self._transaction() as session:
            session.add(
                ConversationRow(id=conversation_id, created_at=now, updated_at=now)
            )

    def submit(
        self,
        *,
        conversation_id: str,
        mission_id: str,
        message_id: str,
        text: str,
        path: str | None,
        now: datetime,
    ) -> None:
        """Store the message and queue its mission; a None path is triaged."""
        with self._transaction() as session:
            conversation = session.get(ConversationRow, conversation_id)
            if conversation is None:
                raise RecordNotFoundError("Unknown conversation")
            conversation.updated_at = max(conversation.updated_at, now)
            session.add(
                MessageRow(
                    id=message_id,
                    conversation_id=conversation_id,
                    role=MessageRole.USER.value,
                    text=text,
                    created_at=now,
                )
            )
            session.flush()
            session.add(
                MissionRow(
                    id=mission_id,
                    conversation_id=conversation_id,
                    message_id=message_id,
                    path=path,
                    triage_outcome=None if path is None else "client",
                    status=MissionStatus.QUEUED.value,
                    created_at=now,
                )
            )

    def rerun(self, mission_id: str, new_mission_id: str, now: datetime) -> None:
        """Queue a linked re-run of an interrupted mission (the "run again" path)."""
        with self._transaction() as session:
            original = session.get(MissionRow, mission_id)
            if original is None:
                raise RecordNotFoundError("Unknown mission")
            if (original.status, original.failure_code) != (
                MissionStatus.FAILED.value,
                INTERRUPTED,
            ):
                raise InvalidStateError("Only interrupted missions can run again")
            # Atomic under the lock: a repeated call never queues a second run.
            if session.scalars(
                select(MissionRow.id).where(MissionRow.rerun_of == mission_id)
            ).first():
                raise InvalidStateError("Mission was already run again")
            self._queue_rerun(session, original, new_mission_id, now)

    @staticmethod
    def _queue_rerun(
        session: Session, original: MissionRow, new_mission_id: str, now: datetime
    ) -> None:
        conversation = session.get(ConversationRow, original.conversation_id)
        assert conversation is not None  # a mission keeps its conversation
        conversation.updated_at = max(conversation.updated_at, now)
        session.add(
            MissionRow(
                id=new_mission_id,
                conversation_id=original.conversation_id,
                message_id=original.message_id,
                # PHX-105: a re-run reuses the original's decision; one that
                # never got one is triaged again.
                path=original.path,
                triage_outcome=original.triage_outcome,
                status=MissionStatus.QUEUED.value,
                rerun_of=original.id,
                created_at=now,
            )
        )

    # ---- runner transitions (compare-and-set)

    def claim(self, mission_id: str, now: datetime) -> ClaimedMission | None:
        """QUEUED -> RUNNING; None if the mission is no longer queued."""
        with self._transaction() as session:
            changed = session.execute(
                update(MissionRow)
                .where(
                    MissionRow.id == mission_id,
                    MissionRow.status == MissionStatus.QUEUED.value,
                )
                .values(status=MissionStatus.RUNNING.value, started_at=now)
            )
            if _rowcount(changed) != 1:
                return None
            mission = session.get(MissionRow, mission_id)
            assert mission is not None
            message = session.get(MessageRow, mission.message_id)
            assert message is not None  # retention keeps referenced messages
            return ClaimedMission(
                mission_id=mission_id,
                text=message.text,
                path=_path(mission.path),
                triage_outcome=_decider(mission.triage_outcome),
            )

    def settle(self, mission_id: str, outcome: Settlement, now: datetime) -> bool:
        """RUNNING -> outcome, atomically with its trace, answer or request."""
        if outcome.status not in _SETTLED:
            raise ValueError("Not a settled mission status")
        with self._transaction() as session:
            mission = session.get(MissionRow, mission_id)
            if mission is None or mission.status != MissionStatus.RUNNING.value:
                return False
            # PHX-105: triage's decision fills a NULL path once; a decided
            # path never changes.
            decision: dict[str, str] = {}
            if mission.path is None and outcome.path is not None:
                path, decider = _path(outcome.path), _decider(outcome.triage_outcome)
                if path is None or decider is None:
                    raise ValueError("A decided path needs its triage outcome")
                decision = {"path": path, "triage_outcome": decider}
            answer_id: str | None = None
            if outcome.status == MissionStatus.COMPLETED:
                if outcome.answer_text is None:
                    raise ValueError("A completed mission needs its answer")
                answer_id = f"{mission_id}:answer"
                session.add(
                    MessageRow(
                        id=answer_id,
                        conversation_id=mission.conversation_id,
                        role=MessageRole.PHOENIX.value,
                        text=outcome.answer_text,
                        created_at=now,
                    )
                )
                session.flush()
            changed = session.execute(
                update(MissionRow)
                .where(
                    MissionRow.id == mission_id,
                    MissionRow.status == MissionStatus.RUNNING.value,
                )
                .values(
                    status=outcome.status.value,
                    answer_message_id=answer_id,
                    failure_code=outcome.failure_code,
                    responder_error=outcome.responder_error,
                    finished_at=None
                    if outcome.status == MissionStatus.WAITING_PERMISSION
                    else now,
                    **decision,
                )
                .execution_options(synchronize_session=False)
            )
            if _rowcount(changed) != 1:
                raise InvalidStateError("Mission changed while settling")
            if outcome.trace is not None:
                session.add(
                    MissionTraceRow(
                        mission_id=mission_id, recorded_at=now, trace=outcome.trace
                    )
                )
            if outcome.pending is not None:
                pending = outcome.pending
                session.add(
                    PermissionRequestRow(
                        id=pending.request_id,
                        mission_id=mission_id,
                        actor_id=pending.actor_id,
                        tool_id=pending.tool_id,
                        action=pending.action,
                        resource=pending.resource,
                        reason=pending.reason,
                        requested_at=pending.requested_at,
                        task_id=pending.task_id,
                        session_id=pending.session_id,
                        status=RequestStatus.PENDING.value,
                    )
                )
            conversation = session.get(ConversationRow, mission.conversation_id)
            assert conversation is not None
            conversation.updated_at = max(conversation.updated_at, now)
            return True

    def cancel_queued(self, mission_id: str, now: datetime) -> bool:
        with self._transaction() as session:
            changed = session.execute(
                update(MissionRow)
                .where(
                    MissionRow.id == mission_id,
                    MissionRow.status == MissionStatus.QUEUED.value,
                )
                .values(status=MissionStatus.CANCELLED.value, finished_at=now)
            )
            return _rowcount(changed) == 1

    def fail_queued(self, mission_id: str, failure_code: str, now: datetime) -> bool:
        """QUEUED -> FAILED with the given code; False if no longer queued."""
        with self._transaction() as session:
            changed = session.execute(
                update(MissionRow)
                .where(
                    MissionRow.id == mission_id,
                    MissionRow.status == MissionStatus.QUEUED.value,
                )
                .values(
                    status=MissionStatus.FAILED.value,
                    failure_code=failure_code,
                    finished_at=now,
                )
            )
            return _rowcount(changed) == 1

    def interrupt(
        self, now: datetime, statuses: Sequence[MissionStatus] = ACTIVE_STATUSES
    ) -> tuple[str, ...]:
        """Mark missions in the given states FAILED/interrupted; nothing re-runs."""
        wanted = [status.value for status in statuses]
        with self._transaction() as session:
            ids = tuple(
                session.scalars(
                    select(MissionRow.id)
                    .where(MissionRow.status.in_(wanted))
                    .order_by(*_order(MissionRow.created_at))
                )
            )
            session.execute(
                update(MissionRow)
                .where(MissionRow.status.in_(wanted))
                .values(
                    status=MissionStatus.FAILED.value,
                    failure_code=INTERRUPTED,
                    finished_at=now,
                )
            )
            return ids

    # ---- permission decisions (user evidence only)

    def approve(
        self, request_id: str, grant: PermissionGrant, new_mission_id: str
    ) -> str:
        """Store the user's grant and queue the linked re-run; returns the grant ID.

        The request must be pending and its mission waiting. An identical
        active grant is reused, so the runtime never sees duplicate scopes.
        """
        now = grant.issued_at
        with self._transaction() as session:
            request = session.get(PermissionRequestRow, request_id)
            if request is None:
                raise RecordNotFoundError("Unknown permission request")
            if (
                grant.actor_id,
                grant.tool_id,
                grant.action,
                grant.resource,
                grant.reason,
            ) != (
                request.actor_id,
                request.tool_id,
                request.action,
                request.resource,
                request.reason,
            ):
                raise InvalidStateError("Grant scope differs from the request")
            mission = session.get(MissionRow, request.mission_id)
            assert mission is not None
            if (
                request.status != RequestStatus.PENDING.value
                or mission.status != MissionStatus.WAITING_PERMISSION.value
            ):
                raise InvalidStateError("Permission request is not pending")
            request.status = RequestStatus.APPROVED.value
            request.decided_at = now
            mission.status = MissionStatus.SUPERSEDED.value
            mission.finished_at = now
            existing = session.scalars(
                select(PermissionGrantRow.id).where(
                    PermissionGrantRow.actor_id == grant.actor_id,
                    PermissionGrantRow.tool_id == grant.tool_id,
                    PermissionGrantRow.action == grant.action,
                    PermissionGrantRow.resource == grant.resource,
                    PermissionGrantRow.reason == grant.reason,
                    PermissionGrantRow.risk_level == grant.risk_level.value,
                    PermissionGrantRow.duration == grant.duration.value,
                    _same(PermissionGrantRow.session_id, grant.session_id),
                    PermissionGrantRow.revoked_at.is_(None),
                )
            ).first()
            grant_id = grant.grant_id if existing is None else existing
            if existing is None:
                session.add(
                    PermissionGrantRow(
                        id=grant.grant_id,
                        actor_id=grant.actor_id,
                        tool_id=grant.tool_id,
                        action=grant.action,
                        resource=grant.resource,
                        reason=grant.reason,
                        risk_level=grant.risk_level.value,
                        duration=grant.duration.value,
                        session_id=grant.session_id,
                        user_decision=grant.user_decision,
                        issued_at=grant.issued_at,
                        approved_request_id=request_id,
                    )
                )
            self._queue_rerun(session, mission, new_mission_id, now)
            return grant_id

    def approve_once(self, request_id: str, new_mission_id: str, now: datetime) -> None:
        """PHX-112: record a one-time approval (no grant row) and queue the re-run.

        For P4 requests, which have no grant form: the decision lives only in
        the service's memory for that one re-run (ADR-151), so nothing durable
        can authorize a later P4 action.
        """
        with self._transaction() as session:
            request = session.get(PermissionRequestRow, request_id)
            if request is None:
                raise RecordNotFoundError("Unknown permission request")
            mission = session.get(MissionRow, request.mission_id)
            assert mission is not None
            if (
                request.status != RequestStatus.PENDING.value
                or mission.status != MissionStatus.WAITING_PERMISSION.value
            ):
                raise InvalidStateError("Permission request is not pending")
            request.status = RequestStatus.APPROVED.value
            request.decided_at = now
            mission.status = MissionStatus.SUPERSEDED.value
            mission.finished_at = now
            self._queue_rerun(session, mission, new_mission_id, now)

    def deny(self, request_id: str, now: datetime) -> None:
        with self._transaction() as session:
            request = session.get(PermissionRequestRow, request_id)
            if request is None:
                raise RecordNotFoundError("Unknown permission request")
            mission = session.get(MissionRow, request.mission_id)
            assert mission is not None
            if (
                request.status != RequestStatus.PENDING.value
                or mission.status != MissionStatus.WAITING_PERMISSION.value
            ):
                raise InvalidStateError("Permission request is not pending")
            request.status = RequestStatus.DENIED.value
            request.decided_at = now
            mission.status = MissionStatus.FAILED.value
            mission.failure_code = PERMISSION_DENIED
            mission.finished_at = now

    def revoke(self, grant_id: str, now: datetime) -> bool:
        """True if revoked now; False if it was already revoked."""
        with self._transaction() as session:
            grant = session.get(PermissionGrantRow, grant_id)
            if grant is None:
                raise RecordNotFoundError("Unknown grant")
            if grant.revoked_at is not None:
                return False
            grant.revoked_at = max(now, grant.issued_at)
            return True

    def active_grants(self, session_id: str) -> ActiveGrants:
        """Unrevoked persistent grants plus this session's session grants.

        A stored row that is not a valid PermissionGrant is skipped and
        counted, never raised: one bad row must not block every mission.
        """

        def query(session: Session) -> ActiveGrants:
            rows = session.scalars(
                select(PermissionGrantRow)
                .where(
                    PermissionGrantRow.revoked_at.is_(None),
                    or_(
                        PermissionGrantRow.duration == GrantDuration.PERSISTENT.value,
                        (PermissionGrantRow.duration == GrantDuration.SESSION.value)
                        & (PermissionGrantRow.session_id == session_id),
                    ),
                )
                .order_by(*_order(PermissionGrantRow.issued_at))
            )
            grants = [_valid_grant(row) for row in rows]
            valid = tuple(grant for grant in grants if grant is not None)
            return ActiveGrants(grants=valid, skipped=len(grants) - len(valid))

        return self._read(query)

    def list_grants(
        self, session_id: str, *, include_inactive: bool = False
    ) -> tuple[GrantRecord, ...]:
        """Grants this launch can use.

        With include_inactive, also EXPIRED (SESSION grants of earlier
        launches) and REVOKED ones. Rows that are not valid grants are skipped.
        """

        def query(session: Session) -> tuple[GrantRecord, ...]:
            records: list[GrantRecord] = []
            for row in session.scalars(
                select(PermissionGrantRow).order_by(
                    *_order(PermissionGrantRow.issued_at)
                )
            ):
                grant = _valid_grant(row)
                if grant is None:
                    continue
                if row.revoked_at is not None:
                    status = GrantStatus.REVOKED
                elif (
                    grant.duration == GrantDuration.SESSION
                    and grant.session_id != session_id
                ):
                    status = GrantStatus.EXPIRED
                else:
                    status = GrantStatus.ACTIVE
                if include_inactive or status == GrantStatus.ACTIVE:
                    records.append(
                        GrantRecord(
                            grant=grant,
                            approved_request_id=row.approved_request_id,
                            revoked_at=row.revoked_at,
                            status=status,
                        )
                    )
            return tuple(records)

        return self._read(query)

    # ---- reads

    def get_mission(self, mission_id: str) -> MissionRecord | None:
        def query(session: Session) -> MissionRecord | None:
            row = session.get(MissionRow, mission_id)
            return None if row is None else _mission(session, row)

        return self._read(query)

    def list_missions(
        self, conversation_id: str | None = None
    ) -> tuple[MissionRecord, ...]:
        def query(session: Session) -> tuple[MissionRecord, ...]:
            statement = select(MissionRow).order_by(*_order(MissionRow.created_at))
            if conversation_id is not None:
                statement = statement.where(
                    MissionRow.conversation_id == conversation_id
                )
            rows = list(session.scalars(statement))
            # One query for every listed mission's request (no N+1).
            ids = statement.with_only_columns(MissionRow.id).order_by(None)
            requests = {
                request.mission_id: request
                for request in session.scalars(
                    select(PermissionRequestRow).where(
                        PermissionRequestRow.mission_id.in_(ids)
                    )
                )
            }
            return tuple(_record(row, requests.get(row.id)) for row in rows)

        return self._read(query)

    def list_conversations(self) -> tuple[ConversationRecord, ...]:
        def query(session: Session) -> tuple[ConversationRecord, ...]:
            rows = session.scalars(
                select(ConversationRow).order_by(*_order(ConversationRow.created_at))
            )
            return tuple(
                ConversationRecord(
                    conversation_id=row.id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            )

        return self._read(query)

    def list_messages(self, conversation_id: str) -> tuple[MessageRecord, ...] | None:
        def query(session: Session) -> tuple[MessageRecord, ...] | None:
            if session.get(ConversationRow, conversation_id) is None:
                return None
            rows = session.scalars(
                select(MessageRow)
                .where(MessageRow.conversation_id == conversation_id)
                .order_by(*_order(MessageRow.created_at))
            )
            return tuple(
                MessageRecord(
                    message_id=row.id,
                    conversation_id=row.conversation_id,
                    role=MessageRole(row.role),
                    text=row.text,
                    created_at=row.created_at,
                )
                for row in rows
            )

        return self._read(query)

    def get_permission_request(self, request_id: str) -> PermissionRequestRecord | None:
        def query(session: Session) -> PermissionRequestRecord | None:
            row = session.get(PermissionRequestRow, request_id)
            return None if row is None else _request(row)

        return self._read(query)

    def list_pending_requests(self) -> tuple[PermissionRequestRecord, ...]:
        def query(session: Session) -> tuple[PermissionRequestRecord, ...]:
            rows = session.scalars(
                select(PermissionRequestRow)
                .where(PermissionRequestRow.status == RequestStatus.PENDING.value)
                .order_by(*_order(PermissionRequestRow.requested_at))
            )
            return tuple(_request(row) for row in rows)

        return self._read(query)

    def get_trace(self, mission_id: str) -> dict[str, Any] | None:
        def query(session: Session) -> dict[str, Any] | None:
            row = session.get(MissionTraceRow, mission_id)
            return None if row is None else dict(row.trace)

        return self._read(query)

    # ---- retention and deletion (grants are never deleted here)

    def purge(self, cutoff: datetime) -> PurgeCounts:
        """Delete chat history created before cutoff.

        Settled missions (with trace and request) older than cutoff go first;
        then old messages no remaining mission references; then old
        conversations left empty. Queued and running missions are never purged.
        """
        with self._transaction() as session:
            expired = select(MissionRow.id).where(
                MissionRow.created_at < cutoff,
                MissionRow.status.not_in([s.value for s in ACTIVE_STATUSES]),
            )
            missions = _delete_missions(session, expired)
            referenced = select(MissionRow.message_id).union(
                select(MissionRow.answer_message_id).where(
                    MissionRow.answer_message_id.is_not(None)
                )
            )
            messages = _rowcount(
                session.execute(
                    delete(MessageRow).where(
                        MessageRow.created_at < cutoff,
                        MessageRow.id.not_in(referenced),
                    )
                )
            )
            conversations = _rowcount(
                session.execute(
                    delete(ConversationRow).where(
                        ConversationRow.created_at < cutoff,
                        ConversationRow.id.not_in(select(MessageRow.conversation_id)),
                        ConversationRow.id.not_in(select(MissionRow.conversation_id)),
                    )
                )
            )
            return PurgeCounts(
                conversations=conversations, messages=messages, missions=missions
            )

    def delete_conversation(self, conversation_id: str) -> tuple[str, ...] | None:
        """Delete one conversation and everything in it; None if unknown."""
        with self._transaction() as session:
            if session.get(ConversationRow, conversation_id) is None:
                return None
            selected = select(MissionRow.id).where(
                MissionRow.conversation_id == conversation_id
            )
            ids = tuple(session.scalars(selected))
            _delete_missions(session, selected)
            session.execute(
                delete(MessageRow).where(MessageRow.conversation_id == conversation_id)
            )
            session.execute(
                delete(ConversationRow).where(ConversationRow.id == conversation_id)
            )
            return ids

    def clear_all(self) -> tuple[str, ...]:
        """Delete all chat history; grants stay until revoked."""
        with self._transaction() as session:
            ids = tuple(session.scalars(select(MissionRow.id)))
            session.execute(delete(MissionTraceRow))
            session.execute(delete(PermissionRequestRow))
            session.execute(update(MissionRow).values(rerun_of=None))
            session.execute(delete(MissionRow))
            session.execute(delete(MessageRow))
            session.execute(delete(ConversationRow))
            return ids


def _rowcount(result: object) -> int:
    count = getattr(result, "rowcount", None)
    assert isinstance(count, int)
    return count


def _same(column: Any, value: str | None) -> ColumnElement[bool]:
    return column.is_(None) if value is None else column == value  # type: ignore[no-any-return]


def _path(value: str | None) -> MissionPath | None:
    if value is None:
        return None
    if value == "direct":
        return "direct"
    if value == "complex":
        return "complex"
    raise ValueError("Unknown mission path")


def _decider(value: str | None) -> TriageDecider | None:
    if value is None:
        return None
    for known in TRIAGE_OUTCOMES:
        if value == known:
            return known
    raise ValueError("Unknown triage outcome")


def _delete_missions(session: Session, selected: Any) -> int:
    """Delete the selected missions with their traces and requests."""
    ids = list(session.scalars(selected))
    for start in range(0, len(ids), 500):  # stay far below SQLite's variable cap
        chunk = ids[start : start + 500]
        session.execute(
            delete(MissionTraceRow).where(MissionTraceRow.mission_id.in_(chunk))
        )
        session.execute(
            delete(PermissionRequestRow).where(
                PermissionRequestRow.mission_id.in_(chunk)
            )
        )
        session.execute(
            update(MissionRow)
            .where(MissionRow.rerun_of.in_(chunk))
            .values(rerun_of=None)
        )
    for start in range(0, len(ids), 500):
        session.execute(
            delete(MissionRow).where(MissionRow.id.in_(ids[start : start + 500]))
        )
    return len(ids)


def _grant(row: PermissionGrantRow) -> PermissionGrant:
    return PermissionGrant.model_validate(
        {
            "grant_id": row.id,
            "actor_id": row.actor_id,
            "tool_id": row.tool_id,
            "action": row.action,
            "resource": row.resource,
            "reason": row.reason,
            "duration": row.duration,
            "risk_level": row.risk_level,
            "issued_at": row.issued_at,
            "session_id": row.session_id,
            "user_decision": row.user_decision,
        }
    )


def _valid_grant(row: PermissionGrantRow) -> PermissionGrant | None:
    try:
        return _grant(row)
    except ValueError:  # pydantic's ValidationError is a ValueError
        return None


def _request(row: PermissionRequestRow) -> PermissionRequestRecord:
    return PermissionRequestRecord(
        mission_id=row.mission_id,
        status=RequestStatus(row.status),
        decided_at=row.decided_at,
        request=PermissionRequest(
            request_id=row.id,
            actor_id=row.actor_id,
            tool_id=row.tool_id,
            action=row.action,
            resource=row.resource,
            reason=row.reason,
            requested_at=row.requested_at,
            task_id=row.task_id,
            session_id=row.session_id,
        ),
    )


def _mission(session: Session, row: MissionRow) -> MissionRecord:
    request = session.scalars(
        select(PermissionRequestRow).where(PermissionRequestRow.mission_id == row.id)
    ).first()
    return _record(row, request)


def _record(row: MissionRow, request: PermissionRequestRow | None) -> MissionRecord:
    return MissionRecord(
        mission_id=row.id,
        conversation_id=row.conversation_id,
        message_id=row.message_id,
        answer_message_id=row.answer_message_id,
        path=_path(row.path),
        triage_outcome=_decider(row.triage_outcome),
        status=MissionStatus(row.status),
        failure_code=row.failure_code,
        responder_error=row.responder_error,
        rerun_of=row.rerun_of,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        permission_request=None if request is None else _request(request),
    )
