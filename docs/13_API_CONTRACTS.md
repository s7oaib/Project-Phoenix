# 13 — API Contracts

FastAPI is the local transport layer.

## Initial REST surface

- `GET /health`
- `GET /ready` (PHX-100: ready/degraded/unavailable, provider statuses, registered models, database at head; 503 only when unavailable)
- `GET /agents`
- `GET /models`
- `GET /tasks`
- `GET /tasks/{id}`
- `POST /chat`
- `POST /tasks`
- `POST /tasks/{id}/cancel`
- `GET /permissions`
- `POST /permissions/{id}/approve`
- `POST /permissions/{id}/deny`
- `POST /files/upload`
- `GET /memory/search`
- `GET /settings`
- `PATCH /settings`

## WebSocket

- `WS /events` (PHX-103, ADR-087). Authenticated like every route; a failure
  closes 1008 before accept, and only `phoenix.v1` is echoed. Frames are
  server-to-client JSON event envelopes (`event_id`, `event_type`, `timestamp`,
  `task_id`, `agent_id`, `payload`, `severity`, `correlation_id`) holding IDs,
  states, codes and timings only (`docs/14_EVENT_SYSTEM.md`). Client frames are
  ignored, and there is no replay. A client whose bounded buffer fills is closed
  with 1013 (try again later); shutdown closes with 1001 (1012 from the server
  under the launcher). After any close the client reconnects and reloads state
  over REST, the source of truth.

## Rules

- Pydantic schemas define contracts.
- Errors are structured: `{"error": {"code", "message", "request_id"}}` (PHX-101).
- Every HTTP request and WebSocket requires the per-launch token, the bound loopback Host and, when present, a Tauri Origin (PHX-101; `docs/09_SECURITY.md`, "Local API").
- Long work returns task IDs.
- Live progress goes through events.
- Privileged operations still pass PermissionEngine.
