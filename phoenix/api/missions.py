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

Events (PHX-103, ADR-087): with an optional sink, every status change the
service records is published as a ``task.state_changed`` event with
``scope="mission"``, the mission ID as correlation ID, and the status, closed
codes and ``rerun_of`` only: queued, running, each settled outcome,
superseded, cancelled, failed (including ``interrupted`` and
``permission_denied``). Publishing never blocks or fails the service; refusals
are counted in ``dropped_events``.

Runtime (PHX-104, ADR-089): ``use_runtime`` replaces the runtime that missions
starting from then on run on (the backend builds a new one when the user sets
the document workspace). A mission keeps the runtime it started on, and
cancelling it goes to that runtime, so a running mission's workspace never
changes. An optional preflight, given with the runtime, runs after a mission is
claimed and before the runtime runs it; a failure code it returns fails the
mission with that code instead (the workspace is re-validated this way).

Triage (PHX-105, ADR-090): a mission submitted without a path is stored with
none, and PHOENIX triages it when it runs; the run's decision (path and who
decided) is recorded when it settles. A re-run, from ``run_again`` or an
approval, reuses the original's decision; one that never got a decision (it
was interrupted before triage finished) is triaged again.

This module is application logic for the backend process, not transport: it
imports no FastAPI; the PHX-104 routes call it.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal, ParamSpec, TypeVar
from uuid import uuid4

from pydantic import JsonValue, ValidationError

from phoenix.agents.mission_events import EventPublisher, EventSink
from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest
from phoenix.agents.runtime import (
    MissionKind,
    MissionResult,
    MissionTrace,
    PhoenixRuntime,
)
from phoenix.agents.tool_steps import document_grants
from phoenix.agents.triage import TriageOutcome
from phoenix.core.events import EventType
from phoenix.persistence.missions import (
    ACTIVE_STATUSES,
    INTERNAL_ERROR,
    INTERRUPTED,
    PERMISSION_DENIED,
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
# PHX-104: None to run the mission, or a failure code to fail it instead.
Preflight = Callable[[], Awaitable[str | None]]


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
    runtime: PhoenixRuntime  # the runtime this mission started on (PHX-104)
    preflight: Preflight | None = None
    cancel: _Reason | None = None


_INTERNAL_ERROR = Settlement(status=MissionStatus.FAILED, failure_code=INTERNAL_ERROR)


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
        events: EventSink | None = None,
        before_run: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        if retention <= timedelta(0):
            raise ValueError("Retention must be positive")
        if purge_interval_seconds <= 0 or shutdown_grace_seconds <= 0:
            raise ValueError("Intervals must be positive")
        self._runtime = runtime
        self._preflight: Preflight | None = None
        # PHX-108 (ADR-110): awaited before each mission runs (the composition
        # root registers tier models whose provider started late). A failure
        # here is ignored; the run itself reports any missing model.
        self._before_run = before_run
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
        self._skipped_grants = 0
        self._writers = 0  # submit/approve/run_again calls between check and enqueue
        self._events = EventPublisher(events)

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
    def skipped_grants(self) -> int:
        """Stored grant rows that were not valid grants and were not passed."""
        return self._skipped_grants

    @property
    def dropped_events(self) -> int:
        """Mission events the sink refused (PHX-103); observable, not raised."""
        return self._events.dropped

    @property
    def runtime(self) -> PhoenixRuntime:
        """The runtime the next mission starts on (PHX-104)."""
        return self._runtime

    @property
    def running(self) -> bool:
        return self._state == "running"

    def use_runtime(
        self, runtime: PhoenixRuntime, *, preflight: Preflight | None = None
    ) -> None:
        """Run missions that start from now on with runtime (PHX-104).

        A running mission keeps the runtime it started on. preflight, if given,
        is awaited before each of those missions runs; a failure code it
        returns fails that mission with the code.
        """
        if type(runtime) is not PhoenixRuntime:
            raise TypeError("Expected a PhoenixRuntime")
        self._runtime = runtime
        self._preflight = preflight

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

    async def _status(
        self,
        mission_id: str,
        status: MissionStatus,
        *,
        failure_code: str | None = None,
        responder_error: str | None = None,
        rerun_of: str | None = None,
    ) -> None:
        """Publish one recorded status change; content-free (PHX-103)."""
        if not self._events.enabled:
            return
        payload: dict[str, JsonValue] = {
            "scope": "mission",
            "mission_status": status.value,
        }
        for key, value in (
            ("failure_code", failure_code),
            ("responder_error", responder_error),
            ("rerun_of", rerun_of),
        ):
            if value is not None:
                payload[key] = value
        await self._events.publish(
            EventType.TASK_STATE_CHANGED, payload, correlation_id=mission_id
        )

    async def _interrupted(self, mission_ids: tuple[str, ...]) -> None:
        for mission_id in mission_ids:
            await self._status(
                mission_id, MissionStatus.FAILED, failure_code=INTERRUPTED
            )
            if self._events.enabled:
                # A sweep may be long: yield so subscribers keep draining.
                await asyncio.sleep(0)

    # ---- lifecycle

    async def start(self) -> None:
        if self._state != "new":
            raise ServiceNotRunningError("Mission service can start only once")
        self._recovered = await self._io(self._store.interrupt, self._now())
        await self._interrupted(self._recovered)
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
            await current.runtime.cancel(current.mission_id)
        await self._record_interrupted((MissionStatus.QUEUED,))
        if active is not None:
            _, pending = await asyncio.wait({active}, timeout=self._grace)
            if pending:
                active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        # A submit, approve or run_again that passed its check before stopping
        # may still be writing a queued row; let it finish (bounded) so the
        # sweep below catches it. It is not enqueued (see _write_and_enqueue).
        try:
            async with asyncio.timeout(self._grace):
                while self._writers:
                    await asyncio.sleep(0.01)
        except TimeoutError:
            pass  # a later write stays queued until the next start recovers it
        # Anything still queued or running (e.g. a run cut off above) is interrupted.
        await self._record_interrupted(ACTIVE_STATUSES)
        self._state = "stopped"

    async def _record_interrupted(self, statuses: tuple[MissionStatus, ...]) -> None:
        # Shutdown must finish even if the database is gone; rows left active
        # are marked interrupted by the next start instead (owner decision 4).
        try:
            interrupted = await self._io(self._store.interrupt, self._now(), statuses)
        except Exception:
            self._record_failures += 1
            return
        await self._interrupted(interrupted)

    async def _run_loop(self) -> None:
        while True:
            mission_id = await self._queue.get()
            task = asyncio.create_task(self._execute(mission_id))
            self._active = task
            # wait() never cancels the mission task if this loop is cancelled.
            await asyncio.wait({task})
            self._active = None
            if task.cancelled() or task.exception() is not None:
                # The outcome could not be recorded; a row left active is
                # marked interrupted by stop() or the next start. Counted, and
                # the queue goes on.
                self._record_failures += 1

    async def _purge_loop(self) -> None:
        while True:
            await asyncio.sleep(self._purge_interval)
            try:
                await self.purge()
            except Exception:
                self._purge_failures += 1

    async def _execute(self, mission_id: str) -> None:
        current = _Current(mission_id, self._runtime, self._preflight)
        self._current = current  # set before claiming so cancel can see it
        try:
            try:
                claimed = await self._io(self._store.claim, mission_id, self._now())
            except Exception:
                # Never lose a dequeued mission: it fails explicitly (R15).
                await self._record(self._store.fail_queued, mission_id)
                return
            if claimed is None:
                return  # cancelled or deleted while queued
            await self._status(mission_id, MissionStatus.RUNNING)
            try:
                refused = None
                if self._before_run is not None:
                    with contextlib.suppress(Exception):
                        await self._before_run()
                if current.preflight is not None:
                    refused = await current.preflight()
                found = await self._io(self._store.active_grants, self._session_id)
                self._skipped_grants += found.skipped
                grants = self._usable(found.grants)
                if current.cancel is not None:
                    outcome = self._cancelled(current, None)
                elif refused is not None:
                    outcome = Settlement(
                        status=MissionStatus.FAILED, failure_code=refused
                    )
                else:
                    # No await between this check and run() registering the
                    # mission, so a cancel either is seen here or reaches run().
                    # PHX-105: no stored path means PHOENIX triages; a stored
                    # one is reused with who decided it (client, or a re-run's
                    # original triage).
                    result = await current.runtime.run(
                        PhoenixRequest(request_id=mission_id, text=claimed.text),
                        None
                        if claimed.path is None
                        else OrchestrationPath(claimed.path),
                        grants=grants,
                        session_id=self._session_id,
                        decided_by=TriageOutcome(claimed.triage_outcome or "client"),
                    )
                    outcome = self._settlement(current, result)
            except Exception:
                # A store, runtime or contract defect: explicit, never success.
                outcome = _INTERNAL_ERROR
            # The run is over: a cancel from here on finds nothing to cancel.
            self._current = None
            try:
                if await self._io(self._store.settle, mission_id, outcome, self._now()):
                    await self._status(
                        mission_id,
                        outcome.status,
                        failure_code=outcome.failure_code,
                        responder_error=outcome.responder_error,
                    )
            except Exception:
                if outcome is _INTERNAL_ERROR:
                    raise  # counted by the run loop
                # e.g. an unstorable trace: still record an explicit failure.
                await self._record(self._settle_failed, mission_id)
        finally:
            self._current = None

    def _settle_failed(self, mission_id: str, failure_code: str, now: datetime) -> bool:
        return self._store.settle(mission_id, _INTERNAL_ERROR, now)

    async def _record(
        self, function: Callable[[str, str, datetime], bool], mission_id: str
    ) -> None:
        """Best-effort internal_error record; a failure is counted, not raised."""
        try:
            recorded = await self._io(function, mission_id, INTERNAL_ERROR, self._now())
        except Exception:
            self._record_failures += 1
            return
        if recorded:
            await self._status(
                mission_id, MissionStatus.FAILED, failure_code=INTERNAL_ERROR
            )

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
        """The run's outcome plus its path decision (PHX-105), if it made one."""
        outcome = self._outcome(current, result)
        path, decided = result.trace.path, result.trace.triage_outcome
        if path is None or decided is None:
            return outcome
        return replace(outcome, path=path.value, triage_outcome=decided.value)

    def _outcome(self, current: _Current, result: MissionResult) -> Settlement:
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
        self,
        conversation_id: str,
        text: str,
        path: OrchestrationPath | str | None = None,
    ) -> str:
        """Store the user's message and queue a mission for it; returns its ID.

        Without a path PHOENIX triages the request when it runs (PHX-105); a
        given path is the client's choice and is used as is.
        """
        self._require_running()
        mission_id = str(uuid4())
        try:
            directive = None if path is None else OrchestrationPath(path)
            request = PhoenixRequest(request_id=mission_id, text=text)
        except (ValueError, ValidationError):
            raise InvalidRequestError("Invalid mission text or path") from None
        await self._write_and_enqueue(
            mission_id,
            None,
            self._store.submit,
            conversation_id=conversation_id,
            mission_id=mission_id,
            message_id=str(uuid4()),
            text=request.text,
            path=None if directive is None else directive.value,
            now=self._now(),
        )
        return mission_id

    async def run_again(self, mission_id: str) -> str:
        """Queue a linked re-run of an interrupted mission; returns the new ID."""
        self._require_running()
        new_id = str(uuid4())
        await self._write_and_enqueue(
            new_id, mission_id, self._store.rerun, mission_id, new_id, self._now()
        )
        return new_id

    async def cancel(self, mission_id: str) -> CancelOutcome:
        """Cancel a queued mission now, or ask the running one to stop."""
        if await self._io(self._store.cancel_queued, mission_id, self._now()):
            await self._status(mission_id, MissionStatus.CANCELLED)
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
        await current.runtime.cancel(current.mission_id)  # NOT_FOUND before run()
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

    async def get_permission_request(self, request_id: str) -> PermissionRequestRecord:
        record = await self._io(self._store.get_permission_request, request_id)
        if record is None:
            raise NotFoundError("Unknown permission request")
        return record

    async def list_grants(
        self, *, include_inactive: bool = False
    ) -> tuple[GrantRecord, ...]:
        """Grants this launch can use; include_inactive adds EXPIRED (earlier
        launches' SESSION grants) and REVOKED ones, each with its status."""
        return await self._io(
            self._store.list_grants, self._session_id, include_inactive=include_inactive
        )

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
        await self._write_and_enqueue(
            new_id, record.mission_id, self._store.approve, request_id, grant, new_id
        )
        await self._status(record.mission_id, MissionStatus.SUPERSEDED)
        return new_id

    async def deny(self, request_id: str) -> None:
        """The user denied: the mission fails ``permission_denied``."""
        record = None
        if self._events.enabled:
            record = await self._io(self._store.get_permission_request, request_id)
        await self._guarded(self._store.deny, request_id, self._now())
        if record is not None:
            await self._status(
                record.mission_id,
                MissionStatus.FAILED,
                failure_code=PERMISSION_DENIED,
            )

    async def revoke(self, grant_id: str) -> bool:
        """Revoke a grant; later runs no longer receive it. False if already.

        Grants are read once when a mission starts, so a mission already
        running keeps the grants it started with.
        """
        return await self._guarded(self._store.revoke, grant_id, self._now())

    async def _write_and_enqueue(
        self,
        queued_id: str,
        rerun_of: str | None,
        function: Callable[_P, object],
        /,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> None:
        """Write a queued row, then enqueue it only if still running.

        Once stop() begins nothing new is enqueued; stop() waits for writes in
        flight and its final sweep marks their rows interrupted. The queued
        event is published inside that window, so it precedes the sweep's.
        """
        self._require_running()
        self._writers += 1
        try:
            await self._guarded(function, *args, **kwargs)
            await self._status(queued_id, MissionStatus.QUEUED, rerun_of=rerun_of)
        finally:
            self._writers -= 1
        if self._state == "running":
            self._queue.put_nowait(queued_id)

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
