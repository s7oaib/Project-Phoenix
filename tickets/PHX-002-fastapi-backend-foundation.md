# PHX-002 — FastAPI Backend Foundation

## Status
TODO

## Phase
Phase 0 — Foundation

## Objective
Create the minimal Python 3.11 FastAPI transport foundation for PHOENIX: an application factory, clear API/transport boundary, structured health contract, local development entry point, and targeted tests. Keep Phoenix core/business logic independent of FastAPI.

## Authoritative documents
- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `/docs/01_ARCHITECTURE.md`
- `/docs/02_SYSTEM_FLOW.md`
- `/docs/13_API_CONTRACTS.md`
- `/docs/23_OBSERVABILITY.md`

## Dependencies
- PHX-001 — Repository Bootstrap: DONE

## Requirements
- Add FastAPI and Pydantic v2 compatible runtime dependencies using the existing uv/Hatchling project setup.
- Provide a FastAPI application factory rather than a module-global architecture dependency.
- Keep FastAPI-specific code inside the API/transport boundary.
- Implement `GET /health` with a typed response schema.
- Health response must report real transport/process readiness only; do not fabricate model, agent, database, tool, voice, or provider health before those subsystems exist.
- Include a minimal application/version identifier in the health response.
- Provide explicit error/exception handling only where needed for this foundation; do not invent a full error framework.
- Establish a local development server entry point/command using a minimal ASGI server dependency if required.
- Add targeted API tests covering app creation, `/health`, response schema, expected status/content type, and unknown-route behavior.
- Preserve existing bootstrap tests and tooling.
- Keep application services/core logic framework-independent.

## Non-goals
- No PHOENIX orchestrator or agent behavior.
- No Ollama/Colibri/model integration.
- No database, SQLAlchemy models, or Alembic migrations.
- No event bus/WebSocket implementation.
- No full configuration system; PHX-004 owns typed application configuration.
- No authentication/authorization system.
- No ToolRegistry or PermissionEngine.
- No voice, memory, RAG, computer control, web research, media, or document features.
- No frontend/Tauri application scaffold.
- No Docker/cloud/CI/CD infrastructure.

## Expected files/components
Use the existing `phoenix/api/` boundary. Prefer a small structure such as an app factory plus health route/schema modules only if the separation is justified. Do not create empty future modules.

## Verification
At minimum run:
- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`
- `npm test` to prove PHX-001 workspace checks remain green
- `git diff --check`

Also verify the FastAPI app can be imported/created without side effects and that `/health` never claims unavailable subsystems are healthy.

## Acceptance criteria
- FastAPI backend foundation exists behind the API/transport boundary.
- `GET /health` returns a typed, deterministic, truthful health payload.
- App factory is testable without starting a network server.
- Runtime/development dependency classification is explicit and minimal.
- No FastAPI dependency leaks into Phoenix core/agent/model/tool/memory/permission modules.
- All PHX-001 tests still pass.
- New PHX-002 API tests pass.
- Ruff, format, mypy, build, npm bootstrap test, and diff checks pass.
- No secrets or generated runtime artifacts are committed.
- Relevant invariants remain satisfied.
- Ticket status and `/FEATURE_TICKETS.md` are updated only after all acceptance criteria pass.

## Completion evidence
Report:
- changed files
- dependencies added with runtime/dev classification
- exact commands run and results
- health response contract
- invariant IDs reviewed
- decisions added, if any
- assumptions
- known limitations
