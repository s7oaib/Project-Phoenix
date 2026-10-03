# PHX-005 — SQLite / SQLAlchemy / Alembic Foundation

## Status
TODO

## Phase
Phase 0 — Foundation

## Objective
Establish PHOENIX's minimal local persistence foundation using SQLite, SQLAlchemy 2, and Alembic without introducing domain tables owned by later tickets.

## Authoritative documents
- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `/docs/01_ARCHITECTURE.md`
- `/docs/09_SECURITY.md`
- `/docs/12_DATABASE_SCHEMA.md`
- `/tickets/PHX-004-configuration-foundation.md`

## Dependencies
- PHX-001 — Repository Bootstrap: DONE
- PHX-002 — FastAPI Backend Foundation: DONE
- PHX-003 — Tauri + React Desktop Foundation: DONE
- PHX-004 — Configuration Foundation: DONE

## Requirements
- Add SQLAlchemy 2 and Alembic as explicit runtime persistence dependencies.
- Keep all database-specific implementation inside `phoenix/persistence/` and Alembic migration infrastructure.
- Use SQLite as the V1 structured store.
- Derive the database location from validated PHX-004 configuration rather than hard-coding machine paths.
- Prefer a database path under `AppSettings.data_dir`; choose one stable filename and document it if it becomes an architectural convention.
- Provide small explicit persistence APIs for database URL construction, engine creation, session-factory creation, and metadata/base ownership.
- Do not create engines, directories, connections, files, or sessions at module import time.
- Do not create the configured data directory merely by importing persistence code or constructing configuration.
- SQLite connections must use safe, explicit SQLAlchemy 2 patterns.
- Establish Alembic configuration/environment/scripts so `upgrade head` works against a caller-selected local SQLite database.
- Include a versioned baseline migration that proves migration plumbing without pre-implementing domain schemas owned by later tickets.
- Tests must use temporary paths and must not touch the user's real Phoenix data directory.
- Migration tests must prove upgrade/current behavior against a temporary SQLite database.
- Preserve FastAPI `/health` exactly as-is.
- Preserve PHX-003 desktop behavior and permissions.

## Non-goals
- No planned domain tables such as agents, sessions, tasks, messages, memories, documents, permissions, audit events, schedules, model profiles, or voice profiles.
- No VectorStore/sqlite-vec integration.
- No repository/service layer for future domain entities.
- No database initialization during app import or FastAPI app construction.
- No automatic migration on API startup.
- No event bus/WebSockets.
- No hardware scanner.
- No frontend persistence access.
- No secrets stored in SQLite.
- No cloud/PostgreSQL/MySQL support.

## Security / data rules
- Database files and SQLite journals/WAL artifacts remain ignored by Git.
- Never store plaintext credentials or secrets.
- Do not expose database paths through `/health`.
- Database/migration failures must be explicit; do not silently recreate or delete databases.
- Tests must not delete arbitrary paths outside their temporary directory.

## Verification
At minimum run:
- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`
- an Alembic upgrade/current verification against a temporary SQLite database
- `npm test`
- `npm run typecheck --workspace @phoenix/desktop`
- `npm run build --workspace @phoenix/desktop`
- `git diff --check`

Also prove import/configuration/app construction does not create a database file or configured data directory.

## Acceptance criteria
- SQLAlchemy 2 + SQLite persistence boundary exists under `phoenix/persistence/`.
- Alembic is configured and can migrate a temporary local database to head.
- Baseline migration is versioned and contains no speculative domain schema.
- Database path is configuration-derived and portable.
- Engine/session creation is explicit and testable.
- No import-time database/filesystem/network side effects exist.
- No domain tables owned by later tickets are introduced.
- PHX-001 through PHX-004 regressions remain green.
- Desktop regressions remain green.
- No secrets or generated database/build artifacts are committed.
- Relevant invariants remain satisfied.
- Ticket/index are marked DONE only after all required checks pass.

## Completion evidence
Report:
- changed files
- dependencies added and classifications
- database URL/path convention
- engine/session API
- Alembic structure and baseline revision
- exact verification commands/results
- side-effect evidence
- security/invariant review
- decisions added, if any
- known limitations
