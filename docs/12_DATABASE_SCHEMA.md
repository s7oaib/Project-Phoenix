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

## Rules

- Stable IDs.
- UTC timestamps.
- Versioned migrations.
- Avoid large binaries in SQLite unless justified.
- Store file references plus metadata.
- Store secret references, not plaintext secrets.
- Vector implementation remains behind an adapter.
