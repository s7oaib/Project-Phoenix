# PHX-005 — SQLite / SQLAlchemy / Alembic Foundation

## Status
DONE

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

### Verified completion — 2026-10-04

- Added persistence `database.py`/`base.py`, standard `alembic.ini`, Alembic
  environment/template, and schema-empty `0001_baseline.py`. Added 12 persistence
  test cases; updated bootstrap dependency assertion, `pyproject.toml`, `uv.lock`,
  and ADR-020. API, core settings, desktop, and ignore rules were unchanged.
- Runtime: `sqlalchemy>=2,<3` locked 2.1.3; `alembic>=1,<2` locked 1.20.0.
  Alembic adds transitive Mako 1.4.3 / MarkupSafe 3.0.4. SQLite uses Python's
  built-in driver; no extra drivers, domain schemas, or helper frameworks.
- Convention: `AppSettings.data_dir / "phoenix.db"`; `database_url()` returns a
  SQLAlchemy `URL.create("sqlite", database=...)` object preserving path syntax.
  `create_engine_for_settings()` is lazy; `create_session_factory()` returns a
  typed `sessionmaker[Session]`; `Base.metadata` initially contains no tables.
  `prepare_data_directory()` is explicit, idempotent directory preparation.
- Alembic uses a caller `sqlalchemy.url` override or the settings-derived URL,
  without directory preparation. ConfigParser overrides escape `%` as `%%`.
  `0001` has no parent and performs no domain schema actions in either direction.
- Real verification strategy: `Config` from committed `alembic.ini`, temporary
  path URL override, explicit preparation, then `command.upgrade(config, "head")`,
  `command.current(config)`, `command.downgrade(config, "base")`, and re-upgrade.
  Current printed `0001 (head)`; inspected revision matched `0001`. Exact tables
  after upgrade: `["alembic_version"]`; downgrade cleared its revision and
  re-upgrade restored `0001`. Percent-path overrides and settings fallback pass.
- Commands/results:
  - `uv add "sqlalchemy>=2,<3" "alembic>=1,<2"`: PASS.
  - `uv lock` / `uv sync --locked`: PASS; 34 resolved packages.
  - `uv run --locked pytest tests/test_persistence.py`: PASS; 12 cases.
  - `uv run --locked pytest -q -s tests/test_persistence.py::test_alembic_baseline_lifecycle`:
    PASS; real upgrade/current/downgrade/re-upgrade evidence above.
  - `uv run --locked pytest`: PASS; 50 total tests.
  - `uv run --locked ruff check .`: PASS.
  - `uv run --locked ruff format --check .`: PASS; 21 files.
  - `uv run --locked mypy`: PASS; 19 source files.
  - `uv run --locked mypy alembic/env.py alembic/versions/0001_baseline.py`:
    PASS; both migration Python files.
  - `uv build`: PASS; wheel and source distribution in ignored `dist/`.
  - `npm test`: PASS; four workspace/rendering/security tests.
  - `npm run typecheck --workspace @phoenix/desktop`: PASS.
  - `npm run build --workspace @phoenix/desktop`: PASS.
  - `git diff --check`: PASS.
- Side effects: fresh-process guards block SQLite connections, path writes,
  directory creation, sockets, subprocesses, async tasks, and future subsystem
  imports while constructing settings, persistence objects, and the API. Data
  paths remain absent until explicit preparation/connection/migration. Sessions
  are usable via SQLAlchemy 2 `SELECT` tests. Missing directories raise explicit
  `OperationalError`; no repair/deletion/fallback occurs. All writes use pytest
  temporary paths, never configured real/default Phoenix storage.
- Security: existing database/journal/WAL/SHM and environment/build/cache ignore
  rules verified. No credentials, secret fields, real dotenv, DB artifacts, or
  developer-specific DB paths committed. Health remains exactly status/service/
  version, without persistence dependency. No frontend/database bridge added.
- INV-001 through INV-024 reviewed: preserved or unaffected, notably INV-001,
  INV-003, INV-004, INV-008, INV-011, INV-018, INV-020, INV-021, and INV-024.
  ADR-020 records persistence ownership, filename, and explicit migration policy.
- Limitations: no domain schema, API database wiring, async driver, backup, or
  recovery. Alembic assets are used from the repository checkout. SQLAlchemy's
  cold import requests standard-library platform metadata; on Windows Python may
  launch `ver`. The isolation test initializes that OS metadata before installing
  guards, without pre-importing Phoenix or SQLAlchemy. No Phoenix background work
  starts. The existing Starlette HTTPX deprecation warning remains; tests pass.
- All acceptance checks passed before marking DONE. PHX-006 remains TODO.
