# 13 — API Contracts

FastAPI is the local transport layer.

## REST API v1 (PHX-104, ADR-089)

Every route needs the PHX-101 token, Host and Origin; a missing or wrong token
is 401 on every route, before routing. Bodies and responses are Pydantic
schemas with `extra="forbid"`, so an unknown field is 422. Path IDs are
canonical lowercase UUIDs (anything else is 422). Lists take `limit` (1-200,
default 50) and `offset` (0-1,000,000) and return
`{"items", "limit", "offset", "total"}`, oldest first.

| Route | Success | Body / query | Notes |
| --- | --- | --- | --- |
| `GET /health` | 200 | | PHX-002, unchanged |
| `GET /ready` | 200 / 503 | | PHX-100, unchanged |
| `POST /chat` | 202 | `{"text", "conversation_id"?, "path"?}` | Queues a mission and returns `{"mission_id", "conversation_id", "status": "queued", "rerun_of": null}` at once. A missing `conversation_id` starts a conversation. `path` is optional (PHX-105, ADR-090): omitted (or `null`), PHOENIX triages the request when it runs (falling back to `complex` if triage fails); `direct` or `complex` is the client's choice and is used as is. Text: 1-16,000 characters, not blank. |
| `GET /missions` | 200 | `conversation_id`?, `limit`, `offset` | Mission summaries: IDs, status, `path` (`null` until triage decides), `triage_outcome` (`decided`, `fallback`, `client`, or `null` before a decision), failure code, responder error, `rerun_of`, timestamps. No text. |
| `GET /missions/{mission_id}` | 200 | | Summary plus `request_text` (the user's message), `answer_text` (only when completed), `rerun_id`, `task_id`, `task_states` and the `permission_request` if any. |
| `POST /missions/{mission_id}/cancel` | 202 | | `{"outcome": "cancelled"}` (was queued) or `"cancelling"` (running; the final state follows). 409 if already settled. A `waiting_permission` mission is not cancelled here: deny its request (INV-019). |
| `POST /missions/{mission_id}/run-again` | 202 | | Linked re-run of a `failed/interrupted` mission; 409 otherwise or if it already ran again. The re-run reuses the original's path and `triage_outcome`; one interrupted before triage decided is triaged again. |
| `GET /permissions` | 200 | `limit`, `offset` | Pending requests: `request_id`, `mission_id`, `status`, `tool_id`, `action`, `resource` (absolute path), `risk_level`, timestamps. |
| `GET /permissions/{request_id}` | 200 | | One request, pending or decided. |
| `POST /permissions/{request_id}/approve` | 202 | `{"duration": "session" \| "always"}` | Stores the user's grant (SESSION for this launch, or PERSISTENT until revoked), supersedes the waiting mission and queues the linked re-run: `{"request_id", "duration", "superseded_mission_id", "mission_id"}`. The tool, action, resource and actor come from the stored request; a body naming them is 422. 409 if not pending, and 409 `workspace_changed` if the resource is outside the active workspace root (deny it instead). |
| `POST /permissions/{request_id}/deny` | 200 | none, or `{}` | The mission fails `permission_denied`. 409 if not pending. |
| `GET /grants` | 200 | `include_inactive`, `limit`, `offset` | Grants this launch can use; `include_inactive=true` adds `expired` and `revoked`. Duration is `session` or `always`. |
| `POST /grants/{grant_id}/revoke` | 200 | | Later missions no longer receive it; 409 if already revoked. |
| `GET /conversations` | 200 | `limit`, `offset` | IDs and timestamps. |
| `GET /conversations/{conversation_id}/messages` | 200 | `limit`, `offset` | The user's messages and PHOENIX's final answers. |
| `DELETE /conversations/{conversation_id}` | 204 | | Deletes it with its missions and traces (cancels its running mission). |
| `DELETE /conversations` | 204 | | Clears all chat history; grants stay. |
| `GET /agents` | 200 | | `agent_id`, `alias`, `role` per registry agent. |
| `GET /models` | 200 | | Per tier-table model: `roles` (placement), `registered` (at startup), `provider_status` (bounded probe as `/ready`), `generating` (holds the lease), `context_limit_tokens`. |
| `GET /settings` | 200 | | `workspace` (`root`, `status`: `unset`, `active` or `unavailable`, `updated_at`), and read-only `model_tiers`, `default_tier`, `retention_days`, `api` (host, port). |
| `PATCH /settings` | 200 | `{"workspace_root"}` | The only writable setting: the folder document steps may read. Validated (below), stored, and used by missions that start afterwards; a running mission keeps its workspace. |

Voice (PHX-114, ADR-108): voice is off until requested. Each route returns the
voice status `{"state", "code", "wake_phrase", "session_id", "conversation_id",
"mission_id", "components"}`:

- `state`: off, starting, sleeping, listening, thinking, speaking,
  degraded_voice, text_only or unavailable.
- `code`: `null`, `models_missing`, `dependency_unavailable`,
  `microphone_unavailable` or `voice_failure`.
- `components`: six `{"id", "status"}` entries for `wake_word`, `vad`, `stt`,
  `tts`, `conversation` and `audio_output`, each `available`, `degraded` or
  `unavailable`.

The status never holds a transcript or answer text. A spoken request becomes a
normal chat mission in `conversation_id`, so its text and answer are read like
any chat.

| Route | Success | Body / query | Notes |
| --- | --- | --- | --- |
| `GET /voice/status` | 200 | | The current voice status. |
| `POST /voice/start` | 202 | none | Starts listening for "Rise, Phoenix"; models load in the background (`starting`). Idempotent while running. A failure shows as `unavailable` with its code, never as an HTTP error. |
| `POST /voice/stop` | 200 | none | Stops listening and speaking and releases the microphone (`off`). |

Workspace validation (422, code per rule): `workspace_invalid` (not absolute,
UNC or device path, a `..` segment, control characters, over 4,096
characters), `workspace_not_found`, `workspace_not_directory`, `workspace_link`
(a symlink, junction or other reparse point, or a real path that differs from
the given one), `workspace_drive_root`, `workspace_system_directory` (the
Windows, Program Files and ProgramData folders from the environment, the
Windows API and fixed defaults, or inside them, and any `$Recycle.Bin` or
`System Volume Information`), `workspace_app_data` (the user's AppData tree)
and `workspace_data_directory` (overlaps PHOENIX's data directory, by real
path). The root is stored in its canonical real-path spelling. Before every
mission that would use it, the root is validated again: if it fails (for
example, the folder was replaced by a junction), that mission fails
`tool_rejected`, the workspace becomes `unavailable` and later missions run
without document steps. During a mission, FilesystemTool checks the root again
on every call (PHX-048): if it is no longer a real directory at its canonical
path (deleted, or replaced by a symlink or junction), the call fails
`root_unavailable` with nothing read or written, and the document step fails
`tool_failed`. Changing the workspace never touches grants: they
match exact absolute resources, so one for a file outside the new root never
applies.

Errors: 404 `not_found` (unknown ID or route), 409 `invalid_state` or
`workspace_changed`, 413 `payload_too_large` (a request body over 64 KiB, after
authentication), 422 `invalid_request` (or a `workspace_*` code), 503
`service_unavailable` (the backend is starting or stopping). Responses never contain raw model output,
tool output or file content. `/files/upload` and `/memory/search` are deferred
to PHX-110 and do not exist (404).

## WebSocket

- `WS /events` (PHX-103, ADR-087). Authenticated like every route; a failure
  closes 1008 before accept, and only `phoenix.v1` is echoed. Frames are
  server-to-client JSON event envelopes (`event_id`, `event_type`, `timestamp`,
  `task_id`, `agent_id`, `payload`, `severity`, `correlation_id`) holding IDs,
  states, codes and timings only (`docs/14_EVENT_SYSTEM.md`). Client frames are
  ignored, and there is no replay. A client whose bounded buffer fills is closed
  with 1013 (try again later), and so is every client if the bridge itself
  overflows. Beyond the connection cap (default 16) the upgrade is refused with
  HTTP 503 and `Retry-After`; shutdown closes with 1001 (1012 from the server
  under the launcher). After any close the client reconnects and reloads state
  over REST, the source of truth.

## Rules

- Pydantic schemas define contracts.
- Errors are structured: `{"error": {"code", "message", "request_id"}}` (PHX-101).
- Every HTTP request and WebSocket requires the per-launch token, the bound loopback Host and, when present, a Tauri Origin (PHX-101; `docs/09_SECURITY.md`, "Local API").
- Long work returns mission IDs (202); task IDs appear only inside mission detail.
- Live progress goes through events.
- Privileged operations still pass PermissionEngine.
