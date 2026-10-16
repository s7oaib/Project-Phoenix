"""PHX-102 mission service: a persisted FIFO runner over PhoenixRuntime (ADR-086).

One mission runs at a time (INV-023); the rest wait in a FIFO queue as
``queued`` and can be cancelled (INV-019). Every outcome is recorded explicitly
(INV-021): a defect raised by the runtime becomes ``failed/internal_error`` and
the queue continues.

Permissions: a mission that needs approval ends ``waiting_permission`` with its
PermissionRequest stored. Only ``approve``, a user decision, creates a grant
(INV-003): "this session" (SESSION, this launch's session ID) or "always"
(PERSISTENT, until revoked). Approval supersedes the waiting mission and queues
a linked re-run of the same request; there is no mid-mission resume (ADR-082).
Each run receives the stored, unrevoked grants that pass ``document_grants``,
plus this launch's session ID.

Lifecycle: ``start`` marks missions left queued or running by an earlier process
``failed/interrupted`` (nothing re-runs; ``run_again`` is the explicit path),
purges chat history older than the retention window and starts the runner and
an hourly purge. ``stop`` marks queued missions interrupted, cancels the running
one and records it interrupted too.

This module is application logic for the backend process, not transport: it
imports no FastAPI, and PHX-104 routes will call it.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, ParamSpec, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest
from phoenix.agents.runtime import (
    MissionKind,
    MissionResult,
    MissionTrace,
    PhoenixRuntime,
)
from phoenix.agents.tool_steps import document_grants
from phoenix.persistence.missions import (
    ACTIVE_STATUSES,
    INTERNAL_ERROR,
    INTERRUPTED,
    ConversationRecord,
    GrantRecord,
    InvalidStateError,
    MessageRecord,
    MissionRecord,
    MissionStatus,
    MissionStore,
    PermissionRequestRecord,
    PurgeCounts,
    RecordNotFoundError,
    Settlement,
)
from phoenix.security.permissions import GrantDuration, PermissionGrant
from phoenix.tools.registry import ToolRegistry

DEFAULT_RETENTION = timedelta(days=30)
DEFAULT_PURGE_INTERVAL_SECONDS = 3600.0
DEFAULT_SHUTDOWN_GRACE_SECONDS = 5.0
USER_GRANT_DURATIONS = (GrantDuration.SESSION, GrantDuration.PERSISTENT)

_P = ParamSpec("_P")
_T = TypeVar("_T")
_Reason = Literal["user", "shutdown", "deleted"]


class MissionServiceError(Exception):
    """A request to the mission service was refused; nothing changed."""


class NotFoundError(MissionServiceError, LookupError):
    """Unknown conversation, mission, permission request or grant."""


class InvalidRequestError(MissionServiceError, ValueError):
    """Invalid text, path or grant choice, or a request outside grant scope."""


class InvalidMissionStateError(MissionServiceError):
    """The record is not in a state that allows this operation."""


class ServiceNotRunningError(MissionServiceError):
    """The service is not started, or is stopping."""


class CancelOutcome(StrEnum):
    CANCELLED = "cancelled"  # was queued; recorded now
    CANCELLING = "cancelling"  # running; the runner records the final state
    NOT_CANCELLABLE = "not_cancellable"  # already settled


@dataclass
class _Current:
    mission_id: str
    cancel: _Reason | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MissionService:
    def __init__(
        self,
        *,
        runtime: PhoenixRuntime,
        store: MissionStore,
        tool_registry: ToolRegistry,
        session_id: str | None = None,
        retention: timedelta = DEFAULT_RETENTION,
        purge_interval_seconds: float = DEFAULT_PURGE_INTERVAL_SECONDS,
        shutdown_grace_seconds: float = DEFAULT_SHUTDOWN_GRACE_SECONDS,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        if retention <= timedelta(0):
            raise ValueError("Retention must be positive")
        if purge_interval_seconds <= 0 or shutdown_grace_seconds <= 0:
            raise ValueError("Intervals must be positive")
        self._runtime = runtime
        self._store = store
        self._tools = tool_registry
        # One ID per backend launch; SESSION grants end with it (owner decision).
        self._session_id = uuid4().hex if session_id is None else session_id
        self._retention = retention
        self._purge_interval = purge_interval_seconds
        self._grace = shutdown_grace_seconds
        self._clock = clock
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._current: _Current | None = None
        self._active: asyncio.Task[None] | None = None
        self._runner: asyncio.Task[None] | None = None
        self._purger: asyncio.Task[None] | None = None
        self._state: Literal["new", "running", "stopping", "stopped"] = "new"
        self._recovered: tuple[str, ...] = ()
        self._purge_failures = 0
        self._record_failures = 0

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def recovered_mission_ids(self) -> tuple[str, ...]:
        """Missions this start marked interrupted (left by an earlier process)."""
        return self._recovered

    @property
    def purge_failures(self) -> int:
        """Periodic purges that raised; observable, and the loop keeps running."""
        return self._purge_failures

    @property
    def record_failures(self) -> int:
        """Outcomes the store could not record; observable, not hidden."""
        return self._record_failures

    @property
    def running_mission_id(self) -> str | None:
        current = self._current
        return None if current is None else current.mission_id

    def _now(self) -> datetime:
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("The clock must return aware datetimes")
        return now.astimezone(UTC)

    @staticmethod
    async def _io(
        function: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs
    ) -> _T:
        return await asyncio.to_thread(function, *args, **kwargs)

    def _require_running(self) -> None:
        if self._state != "running":
            raise ServiceNotRunningError("Mission service is not running")

    # ---- lifecycle

    async def start(self) -> None:
        if self._state != "new":
            raise ServiceNotRunningError("Mission service can start only once")
        self._recovered = await self._io(self._store.interrupt, self._now())
        await self._io(self._store.purge, self._now() - self._retention)
        self._state = "running"
        self._runner = asyncio.create_task(self._run_loop())
        self._purger = asyncio.create_task(self._purge_loop())

    async def stop(self) -> None:
        """Interrupt queued and running missions; idempotent."""
        if self._state != "running":
            return
        self._state = "stopping"
        for task in (self._runner, self._purger):
            assert task is not None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        current, active = self._current, self._active
        if current is not None and current.cancel is None:
            current.cancel = "shutdown"
            await self._runtime.cancel(current.mission_id)
        await self._record_interrupted((MissionStatus.QUEUED,))
        if active is not None:
            _, pending = await asyncio.wait({active}, timeout=self._grace)
            if pending:
                active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        # Anything still queued or running (e.g. a run cut off above) is interrupted.
        await self._record_interrupted(ACTIVE_STATUSES)
        self._state = "stopped"

    async def _record_interrupted(self, statuses: tuple[MissionStatus, ...]) -> None:
        # Shutdown must finish even if the database is gone; rows left active
        # are marked interrupted by the next start instead (owner decision 4).
        try:
            await self._io(self._store.interrupt, self._now(), statuses)
        except Exception:
            self._record_failures += 1

    async def _run_loop(self) -> None:
        while True:
            mission_id = await self._queue.get()
            task = asyncio.create_task(self._execute(mission_id))
            self._active = task
            # wait() never cancels the mission task if this loop is cancelled.
            await asyncio.wait({task})
            self._active = None
            if not task.cancelled() and task.exception() is not None:
                # The store failed to record; the row stays running until the
                # next start marks it interrupted. Counted, and the queue goes on.
                self._record_failures += 1

    async def _purge_loop(self) -> None:
        while True:
            await asyncio.sleep(self._purge_interval)
            try:
                await self.purge()
            except Exception:
                self._purge_failures += 1

    async def _execute(self, mission_id: str) -> None:
        current = _Current(mission_id)
        self._current = current  # set before claiming so cancel can see it
        try:
            claimed = await self._io(self._store.claim, mission_id, self._now())
            if claimed is None:
                return  # cancelled or deleted while queued
            grants = self._usable(
                await self._io(self._store.active_grants, self._session_id)
            )
            if current.cancel is not None:
                outcome = self._cancelled(current, None)
            else:
                # No await between this check and run() registering the
                # mission, so a cancel either is seen here or reaches run().
                try:
                    result = await self._runtime.run(
                        PhoenixRequest(request_id=mission_id, text=claimed.text),
                        OrchestrationPath(claimed.path),
                        grants=grants,
                        session_id=self._session_id,
                    )
                except Exception:
                    # A runtime defect or contract error: explicit, not success.
                    outcome = Settlement(
                        status=MissionStatus.FAILED, failure_code=INTERNAL_ERROR
                    )
                else:
                    outcome = self._settlement(current, result)
            # The run is over: a cancel from here on finds nothing to cancel.
            self._current = None
            await self._io(self._store.settle, mission_id, outcome, self._now())
        finally:
            self._current = None

    @staticmethod
    def _usable(stored: tuple[PermissionGrant, ...]) -> tuple[PermissionGrant, ...]:
        """Stored grants that pass the document-step rules, one per scope."""
        accepted: list[PermissionGrant] = []
        for grant in stored:
            try:
                document_grants([*accepted, grant])
            except ValueError:
                continue  # outside the document scope, or a duplicate scope
            accepted.append(grant)
        return tuple(accepted)

    @staticmethod
    def _cancelled(current: _Current, trace: MissionTrace | None) -> Settlement:
        dump = None if trace is None else trace.model_dump(mode="json")
        if current.cancel == "shutdown":
            return Settlement(
                status=MissionStatus.FAILED, failure_code=INTERRUPTED, trace=dump
            )
        return Settlement(status=MissionStatus.CANCELLED, trace=dump)

    def _settlement(self, current: _Current, result: MissionResult) -> Settlement:
        trace = result.trace.model_dump(mode="json")
        if result.kind == MissionKind.COMPLETED:
            return Settlement(
                status=MissionStatus.COMPLETED, trace=trace, answer_text=result.text
            )
        if result.kind == MissionKind.WAITING_PERMISSION:
            return Settlement(
                status=MissionStatus.WAITING_PERMISSION,
                trace=trace,
                pending=result.pending_permission,
            )
        if result.kind == MissionKind.CANCELLED:
            return self._cancelled(current, result.trace)
        assert result.failure_code is not None
        return Settlement(
            status=MissionStatus.FAILED,
            trace=trace,
            failure_code=result.failure_code.value,
            responder_error=None
            if result.responder_error is None
            else result.responder_error.value,
        )

    # ---- conversations and missions

    async def create_conversation(self) -> str:
        self._require_running()
        conversation_id = str(uuid4())
        await self._io(self._store.create_conversation, conversation_id, self._now())
        return conversation_id

    async def submit(
        self, conversation_id: str, text: str, path: OrchestrationPath | str
    ) -> str:
        """Store the user's message and queue a mission for it; returns its ID."""
        self._require_running()
        mission_id = str(uuid4())
        try:
            directive = OrchestrationPath(path)
            request = PhoenixRequest(request_id=mission_id, text=text)
        except (ValueError, ValidationError):
            raise InvalidRequestError("Invalid mission text or path") from None
        try:
            await self._io(
                self._store.submit,
                conversation_id=conversation_id,
                mission_id=mission_id,
                message_id=str(uuid4()),
                text=request.text,
                path=directive.value,
                now=self._now(),
            )
        except RecordNotFoundError:
            raise NotFoundError("Unknown conversation") from None
        self._queue.put_nowait(mission_id)
        return mission_id

    async def run_again(self, mission_id: str) -> str:
        """Queue a linked re-run of an interrupted mission; returns the new ID."""
        self._require_running()
        new_id = str(uuid4())
        await self._guarded(self._store.rerun, mission_id, new_id, self._now())
        self._queue.put_nowait(new_id)
        return new_id

    async def cancel(self, mission_id: str) -> CancelOutcome:
        """Cancel a queued mission now, or ask the running one to stop."""
        if await self._io(self._store.cancel_queued, mission_id, self._now()):
            return CancelOutcome.CANCELLED  # the runner's claim will now fail
        record = await self._io(self._store.get_mission, mission_id)
        if record is None:
            raise NotFoundError("Unknown mission")
        # Read after the claim committed, so a just-claimed mission is current.
        current = self._current
        if (
            record.status == MissionStatus.RUNNING
            and current is not None
            and current.mission_id == mission_id
        ):
            return await self._cancel_current(current, "user")
        return CancelOutcome.NOT_CANCELLABLE  # settled, or already finishing

    async def _cancel_current(
        self, current: _Current, reason: _Reason
    ) -> CancelOutcome:
        if current.cancel is None or reason == "deleted":
            current.cancel = reason
        await self._runtime.cancel(current.mission_id)  # NOT_FOUND before run()
        return CancelOutcome.CANCELLING

    async def get_mission(self, mission_id: str) -> MissionRecord:
        record = await self._io(self._store.get_mission, mission_id)
        if record is None:
            raise NotFoundError("Unknown mission")
        return record

    async def list_missions(
        self, conversation_id: str | None = None
    ) -> tuple[MissionRecord, ...]:
        return await self._io(self._store.list_missions, conversation_id)

    async def list_conversations(self) -> tuple[ConversationRecord, ...]:
        return await self._io(self._store.list_conversations)

    async def list_messages(self, conversation_id: str) -> tuple[MessageRecord, ...]:
        messages = await self._io(self._store.list_messages, conversation_id)
        if messages is None:
            raise NotFoundError("Unknown conversation")
        return messages

    async def get_trace(self, mission_id: str) -> MissionTrace | None:
        await self.get_mission(mission_id)
        trace = await self._io(self._store.get_trace, mission_id)
        return None if trace is None else MissionTrace.model_validate(trace)

    # ---- permission decisions: the only path that creates grants (INV-003)

    async def list_pending_permissions(self) -> tuple[PermissionRequestRecord, ...]:
        return await self._io(self._store.list_pending_requests)

    async def list_grants(
        self, *, include_revoked: bool = False
    ) -> tuple[GrantRecord, ...]:
        return await self._io(self._store.list_grants, include_revoked=include_revoked)

    async def approve(self, request_id: str, duration: GrantDuration | str) -> str:
        """The user approved: store the grant, supersede, queue the re-run.

        ``duration`` is the user's choice: SESSION ("this session") or
        PERSISTENT ("always"). Returns the re-run's mission ID.
        """
        self._require_running()
        try:
            choice = GrantDuration(duration)
        except ValueError:
            raise InvalidRequestError("Unsupported grant duration") from None
        if choice not in USER_GRANT_DURATIONS:
            raise InvalidRequestError("Unsupported grant duration")
        record = await self._io(self._store.get_permission_request, request_id)
        if record is None:
            raise NotFoundError("Unknown permission request")
        request = record.request
        tool = self._tools.get(request.tool_id)
        if tool is None:
            raise InvalidRequestError("Request is outside the document grant scope")
        try:
            grant = PermissionGrant(
                grant_id=str(uuid4()),
                actor_id=request.actor_id,
                tool_id=request.tool_id,
                action=request.action,
                resource=request.resource,
                reason=request.reason,
                duration=choice,
                risk_level=tool.risk_level,
                issued_at=self._now(),
                session_id=self._session_id
                if choice == GrantDuration.SESSION
                else None,
                user_decision="approved",
            )
            document_grants([grant])
        except (ValueError, ValidationError):
            raise InvalidRequestError(
                "Request is outside the document grant scope"
            ) from None
        new_id = str(uuid4())
        await self._guarded(self._store.approve, request_id, grant, new_id)
        self._queue.put_nowait(new_id)
        return new_id

    async def deny(self, request_id: str) -> None:
        """The user denied: the mission fails ``permission_denied``."""
        await self._guarded(self._store.deny, request_id, self._now())

    async def revoke(self, grant_id: str) -> bool:
        """Revoke a grant; later runs no longer receive it. False if already."""
        return await self._guarded(self._store.revoke, grant_id, self._now())

    async def _guarded(
        self, function: Callable[_P, _T], *args: _P.args, **kwargs: _P.kwargs
    ) -> _T:
        try:
            return await self._io(function, *args, **kwargs)
        except RecordNotFoundError as error:
            raise NotFoundError(str(error)) from None
        except InvalidStateError as error:
            raise InvalidMissionStateError(str(error)) from None

    # ---- retention and deletion

    async def purge(self) -> PurgeCounts:
        """Delete chat history older than the retention window (grants stay)."""
        return await self._io(self._store.purge, self._now() - self._retention)

    async def delete_conversation(self, conversation_id: str) -> None:
        deleted = await self._io(self._store.delete_conversation, conversation_id)
        if deleted is None:
            raise NotFoundError("Unknown conversation")
        await self._cancel_deleted(deleted)

    async def clear_all(self) -> None:
        """Delete every conversation, message, mission and trace; keep grants."""
        await self._cancel_deleted(await self._io(self._store.clear_all))

    async def _cancel_deleted(self, deleted: tuple[str, ...]) -> None:
        current = self._current
        if current is not None and current.mission_id in deleted:
            await self._cancel_current(current, "deleted")
