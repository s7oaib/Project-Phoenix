# PHOENIX Meaningful Decisions Log

Record only meaningful decisions future engineers must preserve.

## ADR-001 — PHOENIX is supreme orchestrator
Status: Accepted.

## ADR-002 — Voice-first, not voice-only
Status: Accepted.

## ADR-003 — Tauri 2 desktop
Status: Accepted.

## ADR-004 — FastAPI is transport, not architecture
Status: Accepted.

## ADR-005 — Custom orchestration core
Status: Accepted. Do not replace with LangChain/CrewAI/AutoGen without an explicit new decision.

## ADR-006 — Ollama default provider
Status: Accepted.

## ADR-007 — Colibri optional deep provider
Status: Accepted; benchmark-gated.

## ADR-008 — SQLite local persistence
Status: Accepted.

## ADR-009 — Deterministic PermissionEngine
Status: Accepted.

## ADR-010 — Neutral internal agent IDs
Status: Accepted.

## ADR-011 — Wake phrase
Status: Accepted: `Rise, Phoenix`.

## ADR-012 — Development hardware profile
Status: Accepted: `BLAZE-6`.

## ADR-013 — One heavy generation by default
Status: Accepted for BLAZE-6.

## ADR-014 — Voice provider abstraction
Status: Accepted.

## ADR-015 — Unauthorized exact voice-actor cloning is not required
Status: Accepted. Distribution path uses original/licensed/consented profiles.

## ADR-016 — Python bootstrap packaging and tooling
Status: Accepted for PHX-001.

Use a root `phoenix` package, Hatchling as the build backend, and uv with a committed
lockfile for a Python 3.11-only development environment. Pytest verifies packaging;
Ruff provides lint/format checks; mypy provides strict type checks. These are pinned
development/build dependencies, with no application runtime dependencies added.

## ADR-017 — npm desktop workspace foundation
Status: Accepted for PHX-001.

Use a private root npm workspace containing `apps/desktop`, with a committed npm
lockfile and a declared npm version. Node's built-in test runner validates workspace
metadata without extra dependencies. PHX-003 introduces the approved Tauri 2 / React /
TypeScript / Vite scaffold and dependencies; PHX-001 reserves its package boundary.

## ADR-018 — Desktop application identity and initial IPC boundary
Status: Accepted for PHX-003.

Use `io.github.s7oaib.phoenix` as the stable Tauri application identifier, scoped
to the Project Phoenix repository owner. The foundation enables no frontend IPC
permissions, plugins, or custom Rust commands. Future backend functionality must
continue through FastAPI and Phoenix's deterministic permission/tool boundaries;
adding a Tauri capability requires an explicit, scoped feature requirement.

Production CSP permits local scripts/styles and denies network connections. The
development CSP allows the exact Vite React refresh preamble by SHA-256 hash,
loopback HMR, and Vite's inline CSS updates. A targeted test checks the preamble
hash against the installed plugin so dependency updates cannot silently break it.

## ADR-019 — Typed configuration namespace and loading boundary
Status: Accepted for PHX-004.

Use `AppSettings` in `phoenix/core/config.py` with Pydantic v2 settings and only
the `PHOENIX_ENVIRONMENT`, `PHOENIX_API_HOST`, `PHOENIX_API_PORT`, and
`PHOENIX_DATA_DIR` fields. Precedence is explicit constructor values, process
environment, UTF-8 `.env` in the working directory, then code defaults. Use
`_env_file=None` to disable dotenv loading for isolated callers/tests.

Unknown constructor/dotenv keys are rejected; the standard process source reads
only declared fields and ignores other environment variables, including unknown
Phoenix-prefixed keys. Tests lock down this distinction. Invalid declared values
raise validation errors without a fallback. Configuration loading reads inputs
without creating directories or initializing runtime subsystems. No import-time
instance, singleton, or cache is provided.

The portable data default is `<home>/.phoenix/data`; `D:/Phoenix/data` is only the
optional BLAZE-6 example override. Directory creation belongs to a later runtime
owner. FastAPI remains independent of loading settings until a concrete transport
consumer requires them; `/health` exposes only its existing three fields.

## ADR-020 — Local persistence ownership and explicit migrations
Status: Accepted for PHX-005.

The V1 structured database is `AppSettings.data_dir / "phoenix.db"`, using
SQLAlchemy 2 and Python's built-in SQLite driver. SQLAlchemy URL construction,
lazy engines, session factories, and the single declarative `Base` belong to
`phoenix/persistence/`; core and FastAPI do not own database initialization.

Directory preparation is a separate explicit operation. Imports, settings,
URL/engine/session construction, and API construction do not connect or create
database files. Callers explicitly prepare the location and connect or migrate;
errors propagate without deleting/recreating data.

Alembic is authoritative for schema evolution. Its environment uses a caller URL
override or the PHX-004 settings-derived URL; it never prepares directories or
starts the API. The schema-empty `0001` baseline introduces only Alembic's version
tracking. Future domain tables require their own ticket and versioned migration.

## ADR-021 — Immutable in-process events and atomic bounded fan-out
Status: Accepted for PHX-006.

The core event foundation uses Pydantic envelopes with the frozen 32 event names,
UTC timestamps, UUID event IDs, caller-supplied/generated correlation strings,
and debug/info/warning/error/critical severity. Payload JSON objects/arrays are
snapshotted into read-only mappings/tuples and serialized back into JSON objects/
arrays. Publication carries information only; it never authorizes execution.

Use a memory-only asyncio bus on one event loop, with one bounded queue per
subscriber (default capacity 256). Under a bus lock, check every active queue
before enqueueing without intervening awaits. A full queue rejects the whole
publication with `EventBusFullError`; no partial delivery or silent drop occurs.
Concurrent publication order is the lock acquisition order shared by subscribers.

Subscription close is explicit and idempotent: remove the subscription, discard
its pending buffer, and wake receivers with `SubscriptionClosedError`. No hidden
workers, retries, producers, or automatic logging exist. Transport and persistence
remain outside the core bus; WebSocket streaming and durable audit are later work.
