# 12 — Database Schema

V1 structured store: SQLite.
ORM: SQLAlchemy 2.
Migrations: Alembic.

## Planned tables

- app_settings
- agents
- agent_preferences
- sessions
- conversations
- messages
- tasks
- task_steps
- task_dependencies
- tool_calls
- permission_requests
- permission_grants
- audit_events
- memories
- documents
- document_chunks
- embedding_records
- schedules
- model_profiles
- model_benchmarks
- provider_health
- voice_profiles

## Mission tables (PHX-102, ADR-086)

Migration `0006_missions` adds `conversations`, `messages`, `missions` (the
planned `tasks`), `mission_traces`, `permission_requests` and
`permission_grants`. Messages hold only the user's text and PHOENIX's final
answer; traces hold hashes, lengths, IDs and statuses only. Chat history is
deleted after 30 days; grants are kept until revoked.

## Settings table (PHX-104, ADR-089)

Migration `0007_app_settings` adds `app_settings` (the planned table): one
key-value row per user-writable setting. In v1 the only key is
`workspace_root`, the validated document workspace folder.

## Rules

- Stable IDs.
- UTC timestamps.
- Versioned migrations.
- Avoid large binaries in SQLite unless justified.
- Store file references plus metadata.
- Store secret references, not plaintext secrets.
- Vector implementation remains behind an adapter.
