# PHX-104 — REST API v1

Status: IN_PROGRESS (recommendations R1–R17 are for owner confirmation)
Phase: 10 — Product Integration (milestone M1, backend)
Branch: `feature/PHX-104-rest-api-v1` (local commits only; no push, no PR)
Base: `feature/PHX-103-runtime-events-ws` `c986b63` (PR #76, not yet merged; both change pinned API files, so this branch stacks on it)

## Objective

Expose the backend to the desktop shell over authenticated REST: chat, missions, permission decisions, grants, conversations, agents, models and settings. REST is the source of truth; `WS /events` (PHX-103) carries live progress. Make the document workspace settable, so the real `waiting_permission` flow works end to end.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/INVARIANTS.md` (INV-003, INV-004, INV-005, INV-011, INV-019, INV-020, INV-021), `/MASTER_SPEC.md`
- `docs/13_API_CONTRACTS.md`, `docs/09_SECURITY.md` ("Local API"), `docs/12_DATABASE_SCHEMA.md`
- ADR-084 to ADR-088; PHX-100 to PHX-103

## Owner decisions (2026-10-17)

1. **`POST /chat` is asynchronous.** It queues a mission through `MissionService.submit` and returns 202 with the mission ID (and conversation ID) at once. Progress arrives over `WS /events`; the answer comes from `GET /missions/{id}`. No request is held open for a mission.
2. **Scope.** Chat; missions (list, get, cancel, run again); permissions (list pending, approve with `session` or `always`, deny, list grants, revoke); agents (list); models (list with health and placement as already known); settings (GET, and PATCH for the workspace folder only); conversations (list, messages, delete, clear all). `/files/upload` and `/memory/search` are deferred to PHX-110, not even as stubs.
3. **Settings: only the workspace folder is writable.** Validated as an existing local directory (absolute; not a symlink, junction or UNC path; not a drive root or system directory). Stored in the database through migration 0007. On change, the runtime's FilesystemTool is (re)configured for subsequent missions, never for a running one. Model tiers, ports and retention stay read-only. Changing the workspace must not re-validate or carry over grants for paths outside the new root.

## Findings (PHX-103 tip `c986b63`)

- `FilesystemTool` is immutable (one root, set once) and `PhoenixRuntime` takes it only at construction, so a new root needs a new tool and a new runtime. `runtime.py` stays byte-identical; `MissionService` (PHX-103-pinned) gains `use_runtime`.
- `MissionService` sent cancellation to `self._runtime`; after a swap that would miss a mission still running on the earlier runtime. Each mission now keeps the runtime it started on.
- Grants match an exact `(actor, tool, action, resource, reason)` scope, and `FilesystemTool.describe` builds every resource from its own root, so a grant for a path outside the current root can never match a request.
- `alembic/env.py` must register the new row: `tests/test_audit_trail.py` and `tests/test_vector_store_sqlite.py` compare the head schema with the full `Base.metadata`.
- FastAPI 0.142 keeps an included router as one `_IncludedRouter` entry in `app.routes`, so a route listing must look inside it (`original_router.routes`).
- `phoenix/api/security.py` (frozen) maps unknown error codes to the generic message "Request failed.", so new codes reuse the envelope unchanged.

## Recommendations (R1–R17, for owner confirmation)

- **R1 — Placement.** Routes in the new `phoenix/api/routes/` package: `common.py` (services, errors, pagination), `schemas.py`, `missions.py` (chat, missions, conversations), `permissions.py` (decisions, grants), `system.py` (agents, models, settings). The workspace rules and `WorkspaceService` in the new `phoenix/api/workspace.py`; the `app_settings` row and `SettingsStore` in the new `phoenix/persistence/settings.py`. The routes import `server.py` (`BackendServices`, the readiness probe), so `build_server_app` imports the routes when it runs, avoiding an import cycle.
- **R2 — Chat.** `{"text", "conversation_id"?, "path"?}`. Without `conversation_id` a conversation is created. `path` is `direct` (default) or `complex` and is the caller's choice until PHX-105 triage replaces it; `complex` plans steps, including document reads when a workspace is set. Text: 1–16,000 characters and not blank.
- **R3 — Paths.** `/missions` replaces docs/13's draft `/tasks`, and `POST /tasks` is dropped (chat creates missions). Grants live at `/grants` and `/grants/{id}/revoke`, so they cannot collide with `/permissions/{request_id}`. Clear-all is `DELETE /conversations`. `GET /permissions/{request_id}` returns one request, pending or decided.
- **R4 — IDs.** The API speaks mission IDs. Every path ID is a canonical lowercase UUID; anything else is 422 before any lookup. The task ID appears only in mission detail.
- **R5 — Pagination.** `limit` (1–200, default 50) and `offset` (0–1,000,000); responses are `{"items", "limit", "offset", "total"}`, oldest first (the store's order). Pages are cut in memory from the store's lists, which the 30-day retention bounds; a keyset cursor can replace it without changing the envelope. `/agents` and `/models` are short, fixed lists and are not paginated.
- **R6 — Content.** Mission summaries carry IDs, status, path, codes, `rerun_of` and timestamps, but no text. Mission detail adds the user's `request_text`, `answer_text` (completed missions only), `rerun_id`, `task_id`, `task_states` and the permission request. Permission requests carry the tool, action, absolute resource path, P-level risk (from ToolRegistry) and timestamps. Nothing returns raw model output, tool output, file content or other trace fields.
- **R7 — Decisions.** Approve takes `{"duration": "session" | "always"}` (SESSION for this launch, PERSISTENT until revoked) and returns 202 with the superseded mission and the queued re-run. Deny takes no body or `{}`. Any other field is 422 (`extra="forbid"`), so no body can set the tool, action, resource or actor; they come from the stored request (INV-003). Only this authenticated API reaches `approve`/`deny`, so every grant is a user decision.
- **R8 — Status codes.** 202 for work that is queued (chat, approve, cancel, run again); 200 for deny, revoke, settings and reads; 204 for deletes. 404 `not_found`; 409 `invalid_state` (a settled mission cancelled, a non-pending request decided, a second run again, an already revoked grant); 422 `invalid_request` or a `workspace_*` code; 503 `service_unavailable` while the backend starts or stops. New codes keep the PHX-101 envelope and its generic message (`security.py` is frozen); the code carries the meaning.
- **R9 — Workspace validation**, in order, each with its own 422 code. `workspace_invalid`: 1–4,096 characters, no control characters, absolute (on Windows a drive and root), no `..` segment (it would change meaning across links), and not UNC or a device path (`\\server`, `\\?\`, `\\.\`, `//`). These are decided from the text alone, so no network path is ever touched. `workspace_not_found` / `workspace_not_directory`. `workspace_link`: the folder is a symlink, junction or other reparse point, or its real path differs from the given one (a link, junction, `subst` drive or 8.3 short name in the chain); this applies FilesystemTool's own no-links rule to the root. `workspace_drive_root`: a filesystem or drive root is far too broad. `workspace_system_directory`: `%SystemRoot%`, the Program Files folders and `%ProgramData%` on Windows (`/bin`, `/boot`, `/dev`, `/etc`, `/lib`, `/lib64`, `/proc`, `/run`, `/sbin`, `/sys`, `/usr`, `/var` on POSIX), or anything inside them. `workspace_data_directory`: overlapping PHOENIX's data directory either way, which would expose the database. The user's home folder is not refused as such, but the default data directory sits inside it, so the home folder is refused by the last rule.
- **R10 — Storage.** Migration `0007_app_settings` adds `app_settings` (the planned table name in docs/12): `key` (primary key, CHECK `key IN ('workspace_root')`), `value`, `updated_at`. The value is the normalized path. `alembic/env.py` registers the row.
- **R11 — Reconfiguration.** Setting a root validates it, builds a FilesystemTool and a runtime for it, stores it, then calls `MissionService.use_runtime`, all under one lock; if any step fails nothing changes. Missions that start afterwards, including ones already queued, use the new root. A running mission keeps the runtime it started on, and its cancel and shutdown go to that runtime. `BackendServices.runtime` stays the startup runtime (no documents); `cancel_active_missions` covers it and the current one.
- **R12 — Startup.** The stored root is validated again at startup, before the first mission. If it is no longer valid it is reported `unavailable` and document steps stay off until the user sets a valid root. There is no "clear workspace" in v1; PATCH accepts only a valid folder.
- **R13 — Grants across a change.** Grants are never touched, re-validated or copied. A grant for a file outside the new root can never apply, because every resource a document step can request is built inside the current root (tested). A grant for the same absolute file, still inside the new root (for example when the new root is a parent of the old), still applies: it names exactly the file the user approved.
- **R14 — Models.** `GET /models` lists the tier-table models with their placement (`roles`: agent IDs, plus `default`), whether each registered at startup, the provider's status from the same bounded probe as `/ready`, whether it holds the generation lease now, and its known context limit. Nothing is discovered or loaded.
- **R15 — Settings read model.** `workspace` (`root`, `status`, `updated_at`), and read-only `model_tiers`, `default_tier`, `retention_days` (30) and `api` (bound host and port). PATCH accepts only `{"workspace_root"}`; any other field is 422.
- **R16 — Health.** `/health` and `/ready` are unchanged. Every new route is behind the PHX-101 middleware: no token is 401 on every route, tested route by route.
- **R17 — Guards.** `server.py` and `missions.py` move from the PHX-103 pin, and `alembic/env.py` from the PHX-102 pin, to the PHX-104 implementation commit (`PHX_104_COMMIT` / `PHX_104_PATHS`, `f9a075c41ecf56f01f268b0ab75505fb723ae133`), in a separate pin commit; `PHX_102_PATHS` is now empty and `PHX_103_PATHS` keeps `runtime.py` and `tool_steps.py`; each path keeps one baseline. Head expectations move to `0007_app_settings`, revision counts to 7, and table lists gain `app_settings`. The two `git diff origin/main` guards exclude the new modules and the migration by exact name. The PHX-050 `env.py` byte guard strips the one new registration block from both the file and main (so it holds before and after the merge). `tests/test_api_server.py` expects the included REST routes.

## Requirements

1. `phoenix/api/routes/` (new): the routes and schemas of R1–R8.
2. `phoenix/api/workspace.py` (new): `validate_workspace_root`, `WorkspaceService`.
3. `phoenix/persistence/settings.py` (new) and `alembic/versions/0007_app_settings.py` (new); `alembic/env.py` registers the row.
4. `phoenix/api/missions.py`: `use_runtime`, `runtime`, `get_permission_request`; a mission keeps its runtime.
5. `phoenix/api/server.py`: the runtime factory, `SettingsStore` and `WorkspaceService` in `BackendServices`, workspace load before the first mission, the routes, `cancel_active_missions` over both runtimes.
6. Records: ADR-089, this ticket, `FEATURE_TICKETS.md`, `docs/13`, `docs/12`.

## Open issues (for the owner)

- A request pending from an earlier workspace can still be approved: the grant names the old file, and the re-run, under the new root, asks again for the new path. Refusing such an approval with 409 is a possible follow-up.
- Pagination is cut in memory (R5).
- New error codes show the generic message "Request failed." because `security.py` is frozen.
- On this machine a symlink needs a privilege, so the symlink case skips itself; junctions are created and refused.

## Non-goals

`/files/upload`, `/memory/search` (PHX-110); triage (PHX-105); the shell sidecar and adapters (PHX-106/107); onboarding (PHX-108); writable model tiers, ports or retention; new dependencies; new event types.

## Tests

New `tests/test_rest_api.py`, mapped as `unit, integration, api_contract, migrations, permissions, path_traversal, permission_bypass, secret_leakage`. It drives the real composition root through TestClient with a scripted provider; an autouse guard refuses network access. It covers: the exact route table and 404 for `/files/upload`, `/memory/search` and `/tasks`; 401 on every route without a valid token, with nothing changed; 503 without running services; chat returning 202 while the model is still generating, then the answer from `GET /missions/{id}`; chat body validation; pagination bounds; 404 and 422 per route; cancelling queued and running missions and 409 when settled; run again after a restart, once; the waiting-permission flow with `session` and with `always` (workspace set through PATCH, the pending request listed and fetched, approval, the linked re-run completing, the original superseded, the grant listed, a second approval 409); approval bodies that try to set the tool, resource, actor or action, or an unsupported duration, refused with nothing stored; deny giving `permission_denied`; revoke stopping the grant from being used; settings read-only fields and PATCH scope; workspace validation (missing, a file, relative, UNC, device, `..`, NUL, drive root, system directory, data directory, junction, symlink when permitted); persistence across a restart and `unavailable` when the folder disappears; a workspace change applying to later missions only, with the earlier root's grant not applying under the new root; cancel and shutdown reaching a mission on the earlier runtime; conversations (list, messages, delete, clear all with grants kept); agents and models; and no response containing a planted file-content sentinel or intermediate model output.

## Acceptance

- [x] Routes, workspace setting, migration and wiring implemented
- [x] Targeted tests, ruff, format and strict mypy pass
- [ ] Owner confirms R1–R17
- [ ] Full matrix on the final committed tree (see report)
