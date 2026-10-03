# PHX-004 — Configuration Foundation

## Status
TODO

## Phase
Phase 0 — Foundation

## Objective
Create PHOENIX's minimal typed application-configuration foundation. Configuration must remain framework-independent, deterministic, local-first, testable without filesystem mutation, and safe for later backend subsystems. Establish authoritative environment-variable names and precedence without implementing secrets storage, database setup, provider configuration, or frontend settings UI.

## Authoritative documents
- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `/docs/01_ARCHITECTURE.md`
- `/docs/06_HARDWARE_PROFILE.md`
- `/docs/09_SECURITY.md`
- `/docs/13_API_CONTRACTS.md`
- `/tickets/PHX-002-fastapi-backend-foundation.md`
- `/tickets/PHX-003-tauri-react-desktop-foundation.md`

## Dependencies
- PHX-001 — Repository Bootstrap: DONE
- PHX-002 — FastAPI Backend Foundation: DONE
- PHX-003 — Tauri + React Desktop Foundation: DONE

## Requirements
- Add a minimal typed settings implementation in a framework-independent Python boundary, preferably under `phoenix/core/`.
- Use Pydantic v2-compatible settings validation; `pydantic-settings` is acceptable if required.
- Establish a clear `PHOENIX_` environment-variable prefix.
- Support deterministic precedence: explicit constructor/override values > process environment > project `.env` file > code defaults, or the documented Pydantic-equivalent ordering.
- Define only configuration required by current/foundation runtime and the next persistence tickets. At minimum cover runtime environment/mode, API bind host, API port, and a local PHOENIX base/data location suitable for later persistence.
- Defaults must remain portable. The BLAZE-6 preference for `D:\Phoenix\` is a deployment preference, not a mandatory cross-machine code default; it may be shown in `.env.example` as an override/example.
- Use typed/validated ports and paths. Configuration parsing must not create directories, open databases, contact providers, bind sockets, or otherwise mutate the machine.
- Invalid configuration must fail explicitly with validation evidence; do not silently coerce obviously invalid values into a usable runtime.
- Update `.env.example` with non-secret PHX-004 variables and safe example values/comments. Do not put credentials or realistic secrets in the file.
- Integrate settings with the FastAPI application factory only as needed to remove hard-coded API metadata/bind assumptions or expose settings through dependency/app state. Keep FastAPI imports out of the settings/core module.
- Preserve the existing `/health` response contract unless a strictly configuration-derived value already required by PHX-002 changes internally; response shape and truthfulness must remain unchanged.
- Preserve the Tauri/React boundary; do not create frontend configuration UI or a Tauri settings bridge in this ticket.
- Add focused tests for defaults, environment overrides, `.env` loading, precedence, validation failures, path typing/port bounds, and app-factory isolation where relevant.

## Non-goals
- No secret vault/keyring/credential manager; later security/integration tickets own secret storage.
- No database/SQLAlchemy/Alembic initialization; PHX-005 owns persistence.
- No event bus; PHX-006 owns events.
- No hardware scanner; PHX-007 owns BLAZE-6 detection.
- No Ollama/Colibri/provider URLs, model profiles, agent configuration, memory/RAG configuration, permissions, voice, web research, media, computer control, or scheduling.
- No frontend settings screen, Zustand store, TanStack Query integration, or Tauri IPC command.
- No automatic directory creation or migration.
- No Docker/cloud/CI configuration.

## Configuration safety
- `.env` remains ignored; `.env.example` remains tracked.
- Secrets must not be logged, serialized into normal diagnostics, embedded into tests, or added to `.env.example`.
- Unknown/malformed Phoenix configuration must have deliberate behavior documented by tests; avoid accidental acceptance of misspelled authoritative keys.
- Do not expose configuration values through `/health` except the already-approved service/version/status contract.

## Verification
At minimum run:
- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`
- `npm test`
- desktop typecheck/build if touched
- `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml` if desktop files are touched
- `git diff --check`

Also demonstrate with targeted tests/commands that settings can be loaded with defaults and overrides without creating configured directories or initializing future subsystems.

## Acceptance criteria
- Typed framework-independent configuration foundation exists.
- `PHOENIX_` environment namespace and precedence are explicit and tested.
- Current required fields have validated portable defaults.
- BLAZE-6 `D:\Phoenix\` storage preference is optional/configurable rather than hard-coded as mandatory.
- `.env.example` documents only safe non-secret configuration.
- Invalid port/type/value inputs fail explicitly.
- Configuration loading causes no filesystem/database/network/process side effects.
- FastAPI remains a transport adapter and `/health` contract remains unchanged.
- PHX-001/002/003 regression checks remain green.
- No future subsystem configuration or frontend settings UI is implemented.
- No secrets or generated artifacts are committed.
- Relevant invariants remain satisfied.
- Ticket status and `/FEATURE_TICKETS.md` are updated only after all acceptance criteria pass.

## Completion evidence
Report:
- changed files
- dependencies added with runtime/dev classification
- exact settings fields and environment-variable names
- precedence rules
- `.env.example` contents/semantics
- validation behavior
- side-effect/isolation evidence
- exact commands run and results
- invariant IDs reviewed
- decisions added, if any
- assumptions and known limitations
