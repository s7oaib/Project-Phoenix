# PHX-102 — Mission persistence and runner

Status: IN_PROGRESS (implemented; recommendations R1–R14 await owner confirmation)
Phase: 10 — Product Integration (milestone M1, backend)
Branch: `feature/PHX-102-mission-persistence-runner` (local commits only; no push, no PR)
Base: main `d85021b` (includes PHX-047, PHX-100 and PHX-101)

## Objective

Make missions durable and orderly. A user message becomes a queued mission, one mission runs at a time, outcomes and pending permission requests are stored, and the user's approval or denial drives what happens next. History is kept for 30 days. PHX-104 adds the HTTP routes and PHX-103 the events on top of this service.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/INVARIANTS.md` (INV-003, INV-005, INV-007, INV-019, INV-020, INV-021, INV-023), `/MASTER_SPEC.md`
- `docs/12_DATABASE_SCHEMA.md`, `docs/13_API_CONTRACTS.md`, `docs/09_SECURITY.md` ("Local API")
- ADR-081 to ADR-085; PHX-100 and PHX-101

## Owner decisions (2026-10-16 and 2026-10-17)

1. **Concurrency.** One active mission at a time with a FIFO queue. Queued missions show `queued` and can be cancelled. INV-023 already limits heavy generation to one.
2. **Resume after approval** is a linked re-run with the grant. Approving a waiting permission stores the grant, starts a new mission for the same request linked to the original (`rerun_of`), and marks the original `superseded`. No mid-mission checkpointing (ADR-082).
3. **Grant duration is the user's choice.** "This session" is `duration=session`, scoped to the backend's per-launch session ID and ending when the app closes. "Always" is `duration=persistent`, until revoked. Grants are stored per exact actor, tool, action, resource and reason, and are never created from model text (INV-003). Revocation is supported. Only user decisions create grants, recorded as user evidence (`user_decision=approved`).
4. **Restart.** On startup, missions left `running` or `queued` by a previous process are marked `failed` with reason `interrupted` and get a "run again" path (a service method here; an API in a later ticket). Nothing re-runs automatically.
5. **Retention.** Conversations, messages, missions and traces are kept 30 days, then deleted, on startup and periodically while running. Explicit delete-conversation and clear-all methods exist. Grants are not purged by age: session grants end with the session, persistent grants last until revoked.
6. **Content.** Store the user's message text and PHOENIX's final completed answer text (chat history). Traces stay hashes, lengths and statuses only. Raw model output, tool output and file content are never stored. The pending `PermissionRequest`, including its resource path, is stored so the UI can show it.

## Findings (main `d85021b`)

- `PhoenixRuntime.run(request, path, *, grants, session_id)` accepts only SESSION or PERSISTENT P1 document grants (`document_grants`); any other grant is a `PhoenixRuntimeError` before the mission starts. It needs a caller task and allows one active mission per task. `cancel(request_id)` returns `NOT_FOUND` until `run` has registered the mission; registration happens before `run`'s first `await`.
- `WAITING_PERMISSION` carries the unattempted `PermissionRequest`. Its `session_id` is the caller's, and `PermissionEngine` matches a grant only if `issued_at <= requested_at`, so a grant issued at approval matches the re-run's later request.
- `BackendServices` is a frozen dataclass built inside `open_services`, and the launcher calls `cancel_active_missions` before draining. Wiring a new service therefore needs a `server.py` edit (frozen since PHX-100 merged).
- `alembic/env.py` registers every row module (PHX-042 precedent). `tests/test_audit_trail.py` and `tests/test_vector_store_sqlite.py` compare the head schema with the full `Base.metadata`, so the new rows must be registered there or those tests fail when run alone.
- About 30 test files pin the Alembic head, and three pin the full table list.
- The backend runtime has no FilesystemTool until PHX-108, so in production `waiting_permission` cannot occur yet; the tests use a runtime with a workspace.

## Recommendations (R1–R14, awaiting owner confirmation)

- **R1 — Placement.** Rows and `MissionStore` in `phoenix/persistence/missions.py`; `MissionService` in `phoenix/api/missions.py`. `phoenix/agents` may not import persistence (ADR-082 neutrality). The service imports no FastAPI, so the transport stays thin.
- **R2 — Table names.** `missions`, not docs/12's `tasks`, matching the runtime's vocabulary (PHX-104 may still expose `/tasks`). Six tables: `conversations`, `messages`, `missions`, `mission_traces`, `permission_requests`, `permission_grants`. Not created: `sessions`, `task_steps`, `task_dependencies`, `tool_calls` (tool calls live in the trace JSON).
- **R3 — Vocabulary.** Status: `queued`, `running`, `completed`, `failed`, `cancelled`, `waiting_permission`, `superseded`. Failure codes: the runtime's `MissionFailureCode` plus `permission_denied`, `interrupted` and `internal_error`. The database checks structure (failed ⇔ failure code; completed ⇔ answer; responder error ⇔ `responder_output`) but not the failure-code vocabulary, so PHX-109 can add runtime codes without a migration.
- **R4 — Runtime defects.** An unexpected exception from `run` (including a `PhoenixRuntimeError`) is recorded `failed/internal_error` and the queue continues, instead of stopping the runner. If the store itself fails to record an outcome, `record_failures` counts it and the row stays `running` until the next start marks it interrupted.
- **R5 — Shutdown.** `stop()` marks queued missions `failed/interrupted`, cancels the running mission and records it `failed/interrupted` (not `cancelled`: the user did not cancel it), waits up to 5 s and then hard-cancels. It runs through PHX-100's existing `cancel_active_missions` (launcher before drain, and lifespan exit), and is idempotent. Store failures during shutdown are counted, not raised; the next start recovers the rows.
- **R6 — Retention rule.** Age is measured per record from `created_at`, cut off at 30 days. A purge deletes settled missions older than the cutoff (with their trace and permission request), then messages older than the cutoff that no remaining mission references, then old conversations left empty. Queued and running missions are never purged; a re-run keeps its original (older) user message until the re-run itself expires.
- **R7 — Purge interval.** On start, then every 3600 s (`purge_interval_seconds`). A failing periodic purge is counted (`purge_failures`) and the loop continues.
- **R8 — Delete and clear.** `delete_conversation` cancels that conversation's running mission and deletes its queued ones, so nothing is recorded for them afterwards. `clear_all` deletes all chat history and keeps grants; revoking is separate.
- **R9 — Run again.** Only `failed/interrupted` missions can run again. The new mission is queued with `rerun_of`; the original keeps its status (it is not marked superseded).
- **R10 — Approval scope.** The grant is built from the stored request plus the ToolRegistry risk level and must pass `document_grants` (P1, `document_analyst`, fixed reason, the three read-only tools); anything else is refused. Only a `pending` request on a `waiting_permission` mission can be approved or denied. An identical active grant is reused rather than duplicated. Waiting missions survive a restart; approving one after relaunch uses the new launch's session for "this session".
- **R11 — Grants per run.** Unrevoked PERSISTENT grants plus SESSION grants for this launch's session ID, each re-validated with `document_grants`; invalid or duplicate-scope grants are skipped rather than failing the mission.
- **R12 — Cancel semantics.** Queued: `cancelled` immediately. Running: `cancelling`, and the runner records `cancelled`. Waiting for permission: not cancellable (use deny). Settled: not cancellable.
- **R13 — Grant revocation and provenance.** Revocation sets `revoked_at` (kept for history, never purged). `approved_request_id` has no foreign key because grants outlive the 30-day purge of requests.
- **R14 — Queue.** The FIFO is in memory and unbounded; queue order is submit order. Production keeps SQLite's default (no `PRAGMA foreign_keys`); the store deletes in foreign-key order, and the tests run with foreign keys enforced.

## Requirements

1. **Migration `0006_missions`** (new): the six tables of R2, with structural CHECK constraints, foreign keys and indexes. Versioned and reversible (INV-020).
2. **`phoenix/persistence/missions.py`** (new): rows and `MissionStore` (compare-and-set transitions; reads; approve, deny, revoke; purge; delete; clear).
3. **`phoenix/api/missions.py`** (new): `MissionService` with `start`, `stop`, `create_conversation`, `submit`, the single runner, `cancel`, `get_mission`, `list_missions`, `list_conversations`, `list_messages`, `get_trace`, `list_pending_permissions`, `list_grants`, `approve`, `deny`, `revoke`, `run_again`, `purge`, `delete_conversation` and `clear_all`.
4. **Wiring** in `phoenix/api/server.py`: `BackendServices.missions`, started in the lifespan and stopped by `cancel_active_missions`.
5. **`alembic/env.py`** registers the new rows.
6. **Records.** ADR-086; this ticket; `FEATURE_TICKETS.md`; `docs/12_DATABASE_SCHEMA.md`.

## Non-goals

HTTP routes (PHX-104); events (PHX-103); request triage (PHX-105); a workspace FilesystemTool in the backend (PHX-108); recovery or fail-safe (PHX-109); single-instance enforcement (PHX-106); new dependencies.

## Frozen-file handling

- `phoenix/api/server.py` and `alembic/env.py` change. They are pinned to the PHX-102 implementation commit in `tests/test_prompt_injection_defenses.py` (`PHX_102_COMMIT` / `PHX_102_PATHS`, `9ccc5d4522c513832f85da49c44a236284aab7b7`): implementation commit, then pin commit. The PR must merge with a merge commit.
- The same guard's head expectation changes to `0006_missions`, with a comment; nothing else in it changes.
- `tests/test_screenshot_vision_loop.py` and `tests/test_windows_ui_automation.py` exclude the two new modules, the migration and `alembic/env.py` by name, and expect head `0006_missions`.
- `tests/test_retrieval_context.py` (PHX-050 byte guard on `env.py`) allows exactly the new registration block.
- Head expectations in the other tests move to `0006_missions`, four revision counts move from 5 to 6, and three table-list expectations gain the six tables.

## Tests

New `tests/test_mission_service.py`, mapped as `unit, integration, migrations, permissions, permission_bypass`. It uses temporary SQLite with foreign keys enforced, the real runtime with a FilesystemTool workspace, and a scripted provider; an autouse guard refuses network access. It covers: the migration round trip and mapping; schema rejections; FIFO order and one active mission; cancelling queued and running missions (provider cancel reached); waiting permission persisted with its request; approve with each duration (grant scope, linked re-run completes, original superseded); deny; invalid durations; a session grant not used by another session; a persistent grant surviving restart until revoked; out-of-scope stored grants skipped; no grant from model text (behavioural and static); restart recovery and run again; shutdown interruption; runtime defects; input validation; retention purge (on start and periodic; grants kept); delete conversation and clear all; no raw text in storage; compare-and-set under thread races; and the runner and store under concurrent load. A lifespan test checks the wiring.

## Acceptance

- [x] Migration, store, service and wiring implemented
- [x] Targeted tests, ruff, format and strict mypy pass
- [ ] Owner confirms R1–R14
- [ ] Full matrix on the final committed tree (see report)
