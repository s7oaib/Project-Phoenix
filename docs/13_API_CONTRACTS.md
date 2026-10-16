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

- `WS /events`

## Rules

- Pydantic schemas define contracts.
- Errors are structured.
- Long work returns task IDs.
- Live progress goes through events.
- Privileged operations still pass PermissionEngine.
