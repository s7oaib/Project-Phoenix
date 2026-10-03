# PHX-002 — FastAPI Backend Foundation

## Status
DONE

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

### Verified completion — 2026-10-04

- Created `phoenix/api/app.py` with `create_app()`, a Pydantic v2
  `HealthResponse`, and `GET /health`; created `tests/test_api.py`.
- Updated `pyproject.toml`, `uv.lock`, and the bootstrap dependency assertion
  to reflect this ticket's approved runtime dependencies. Existing package-boundary
  tests and the npm desktop boundary remain intact.
- Runtime: `fastapi>=0.115,<1` (locked 0.142.2), `pydantic>=2,<3` (2.13.5),
  `uvicorn>=0.30,<1` (0.54.0). Development: `httpx>=0.27,<1` (0.28.1)
  for the in-process API client. No optional dependency bundles added.
- Exact health JSON: `{"status":"ok","service":"phoenix-api","version":"0.1.0"}`.
  The version comes from installed Phoenix distribution metadata, whose source
  is `pyproject.toml`. `ok` reports API responsiveness only.
- Development command:
  `uv run --locked uvicorn phoenix.api.app:create_app --factory --host 127.0.0.1 --port 8000`.
- Verification commands and results:
  - `uv lock`: PASS; 28 packages resolved.
  - `uv sync --locked`: PASS.
  - `uv run --locked pytest tests/test_api.py tests/test_bootstrap.py`: PASS;
    19 tests, including eight API cases and eleven bootstrap cases.
  - `uv run --locked pytest`: PASS; 19 tests.
  - `uv run --locked ruff check .`: PASS.
  - `uv run --locked ruff format --check .`: PASS; 14 files formatted.
    An initial failure in the new test file was corrected with
    `uv run --locked ruff format tests/test_api.py`.
  - `uv run --locked mypy`: PASS; 14 source files.
  - `uv build`: PASS; source distribution and wheel built in ignored `dist/`.
  - `npm test`: PASS; one workspace test.
  - `git diff --check`: PASS.
  - `uv run --locked python -c "from uvicorn.importer import import_from_string; from fastapi import FastAPI; factory = import_from_string('phoenix.api.app:create_app'); app = factory(); assert isinstance(app, FastAPI); print('Factory import/create and Uvicorn target: PASS')"`: PASS.
- The fresh-interpreter factory test blocks Phoenix subsystem imports, socket
  binding/connections, subprocess launches, and background task creation.
  Import and app creation succeed, and no startup/shutdown callbacks are registered.
- Reviewed INV-001 through INV-024: satisfied or unaffected. In particular,
  INV-001, INV-003, INV-011, INV-012, INV-015, INV-016, and INV-021 are preserved.
  FastAPI imports remain exclusively inside `phoenix/api/` in application code.
- Security review: no credentials, tokens, real environment files, databases,
  model files, runtime logs, or generated artifacts added to Git. Nothing staged;
  `.venv/`, `dist/`, and tool caches are ignored. Health exposes only the three
  documented fields. No permissions or privileged capabilities introduced.
- Decisions: None; the factory and transport boundary implement already-approved
  requirements without an additional architecture decision.
- Assumption: run through the installed uv environment so distribution metadata
  is available, consistent with PHX-001 packaging.
- Limitations: this is transport health only. Starlette emits a deprecation warning
  for its HTTPX test client; all tests pass. uv used its copy fallback because the
  cache and workspace are on different filesystems. No network server was started.
- All acceptance criteria passed before updating status. No push, pull request,
  merge, or later-ticket implementation performed.
