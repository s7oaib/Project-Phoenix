# PHX-100 — Backend composition root and entrypoint

Status: DONE (owner confirmed recommendations R1–R9 on 2026-10-16)
Phase: 10 — Product Integration (milestone M1, backend)
Branch: `feature/PHX-100-backend-composition-root` (local commits only; no push, no PR)
Base: main `47bb91e` (includes PHX-046)

## Objective

Give PHOENIX one process that a desktop shell can start. It should:

- build every backend service once, from settings;
- report honest readiness;
- print one machine-readable line once it is listening;
- shut down cleanly.

Later tickets add the API (PHX-104), events (PHX-103), the mission runner (PHX-102) and the Tauri sidecar (PHX-106) on top of it.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md` (especially INV-001, INV-011, INV-019, INV-020, INV-021, INV-022, INV-023), `/MASTER_SPEC.md`
- `docs/01_ARCHITECTURE.md`, `docs/09_SECURITY.md`, `docs/13_API_CONTRACTS.md`, `docs/14_EVENT_SYSTEM.md`
- ADR-004 (FastAPI is transport), ADR-080/081/082 (runtime, structured providers, tool steps)

## Findings (main `47bb91e`)

- **API.** `phoenix/api/app.py` serves only `GET /health`. `tests/test_api.py` requires `create_app()` to import no subsystem and to register no startup or shutdown handlers. So the composition root must be a separate module that wraps `create_app()`.
- **Frozen paths.**
  - `tests/test_prompt_injection_defenses.py` hash-pins every `phoenix/`, `alembic/` and `apps/desktop/` path on `origin/main`.
  - `tests/test_screenshot_vision_loop.py` and `tests/test_windows_ui_automation.py` run `git diff origin/main -- phoenix/api phoenix/core ...`. Once committed, a new file in those directories also shows up in that diff.
  - `tests/workspace.test.mjs` byte-pins `pyproject.toml` and `uv.lock`.
  - So `AppSettings` (`phoenix/core/config.py`) cannot gain fields, and no dependency or `[project.scripts]` entry can be added.
- **Settings.** `AppSettings` has `api_host="127.0.0.1"`, `api_port=8000` (`ge=1`, so 0 is not allowed) and `data_dir`. Colibri also defaults to port 8000.
- **Persistence.**
  - `create_engine_for_settings` and `create_session_factory` are lazy.
  - `alembic/env.py` honours a `sqlalchemy.url` override and refuses non-SQLite URLs.
  - The head revision is `0005_audit_events`.
  - `alembic/` and `alembic.ini` sit in the repository root and are **not** in the wheel (`packages = ["phoenix"]`).
- **Dependencies.** `uvicorn==0.54.0` is already a locked runtime dependency. Neither `websockets` nor `wsproto` is installed, so real uvicorn cannot serve WebSockets yet (this matters for PHX-103).
- **Runtime.** `PhoenixRuntime(providers, structured_providers, capability_registry, lifecycle, agent_registry, tier_table, limits, filesystem_tool)` takes no event bus, audit trail or policy. `cancel(request_id)` and `active_request_ids` support cooperative cancellation (INV-019).
- **Live wiring.** `scripts/phoenix_mission_smoke.py` already shows the pattern: two `OllamaProvider`s, a default one and `think=False`, plus tier-table capability registration that skips missing models.
- **Uvicorn shutdown.** Uvicorn 0.54 already handles SIGINT, SIGTERM and Windows SIGBREAK (CTRL_BREAK). It drains connections before running lifespan shutdown. Its default access log writes to **stdout**.

## Owner decisions

- 2026-10-16 (roadmap): PHX-100 is the composition root and backend entrypoint. It adds no routes beyond readiness; PHX-104 owns the REST API.
- The brief for this ticket fixes these points:
  - new modules only;
  - `python -m phoenix.api`, not `[project.scripts]`;
  - loopback-only `--host`;
  - `--port 0` allowed;
  - one JSON ready line on stdout;
  - no default FilesystemTool workspace (PHX-108);
  - services held in one typed container on `app.state`;
  - fakes and temporary SQLite in tests;
  - ADR-084.

## Recommendations (R1–R9, confirmed by the owner 2026-10-16)

- **R1 — Migrate at startup.** The lifespan runs `alembic upgrade head` on the configured SQLite database before building services. A desktop app has no separate operator step, and migrations are versioned and forward-only (INV-020). If the migration fails, startup fails, so no ready line is printed.
- **R2 — Default port 0.** `--port` defaults to `0`, an OS-assigned free port reported in the ready line. Port 8000 is avoided because Colibri uses it. `AppSettings.api_host` and `AppSettings.api_port` are **not** read by the entrypoint: the file is frozen and its 8000 default collides with Colibri. The CLI flags are authoritative.
- **R3 — Pre-bind the socket.** The entrypoint binds the loopback socket itself, with `SO_EXCLUSIVEADDRUSE` on Windows, and hands it to uvicorn. That way the real port is known before the app is built, which PHX-101's Host check needs.
- **R4 — Ollama base URL.** `ServerConfig.ollama_base_url` (default `http://127.0.0.1:11434`) lives in the new `phoenix/api/server.py`, because `AppSettings` is frozen. There is no CLI flag; PHX-108 settings will expose it.
- **R5 — Readiness semantics.** `GET /ready` returns:
  - `unavailable` (HTTP 503) if the services are not built, or if the database is not at the Alembic head or cannot be read;
  - `degraded` (HTTP 200) if the database is fine but some provider is not `healthy`, or no tier model is registered;
  - `ready` (HTTP 200) otherwise.

  Each provider probe and the database check have a bounded timeout (default 2 s). A hung or failing probe reports the provider as `unavailable`, never as a crash (INV-021, INV-022). Only provider IDs and statuses are returned: no health `detail`, URLs or versions.
- **R6 — Register models once.** Tier-table models are registered at startup, and only for providers whose bounded health probe is healthy. Missing models are skipped, as in the smoke script. They are **not** refreshed on later readiness calls (no side effects in a GET), so a backend started before Ollama stays `degraded` until restart. A refresh path belongs to PHX-102 or PHX-104 (see Q2).
- **R7 — Tool registry contents.** The container's `ToolRegistry` holds the seven filesystem definitions, because `FilesystemTool` requires all of them and `AuditTrail` validates against this registry. No `FilesystemTool` is built. `PermissionPolicy()` is the empty default policy.
- **R8 — Mission cancellation on shutdown.** When uvicorn starts shutting down, the entrypoint cancels every active runtime mission *before* draining connections, so a long mission cannot hold shutdown for its 1800 s budget. The lifespan cancels again on exit. Graceful drain is bounded to 10 s.
- **R9 — Logging.** Logs go to stderr, and the uvicorn access log is disabled. Stdout carries only the ready line, which keeps the PHX-106 handshake unambiguous.

## Open questions

- **Q1 — Packaging migrations.** `alembic/` and `alembic.ini` are not in the wheel. That is fine when running from the repository, but PHX-106 and PHX-092 must bundle them, or move migration scripts into the package (that changes frozen paths and needs owner approval).
- **Q2 — Model registration after startup.** Should a later healthy probe register models, e.g. a mission-runner refresh before each mission (PHX-102) or an explicit `POST /models/refresh` (PHX-104)?
- **Q3 — WebSocket server dependency.** Serving `WS /events` (PHX-103) under real uvicorn needs `websockets` or `wsproto`, which is a new locked dependency (PHX-072 gate).
- **Q4 — Parent-death detection.** If the Tauri parent dies, the backend should probably exit when stdin reaches EOF. That is recommended for PHX-106 and not implemented here.

## Requirements

1. **`phoenix/api/server.py` (new).**
   - `ServerConfig`: Ollama base URL and probe timeouts.
   - `ProviderSet`.
   - `ollama_providers(config)`: the default `OllamaProvider` plus `OllamaProvider(think=False)` as the structured provider.
   - `BackendServices`: a frozen typed container.
   - `build_server_app(settings, *, config=None, provider_factory=None)`. It returns `create_app()` plus a lifespan that:
     - prepares the data directory and migrates to head (R1);
     - builds the engine and session factory, the default agent registry, the tier table, `ModelCapabilityRegistry` (R6), `ModelLifecycleManager` (capacity 1, INV-023), the providers, `EventBus`, the `ToolRegistry` (R7), `PermissionPolicy`, `AuditTrail` and `PhoenixRuntime`, each exactly once;
     - stores the container on `app.state.phoenix`.

     On exit, the lifespan cancels active missions, closes the providers and disposes of the engine. A failed startup closes everything it had already opened.
   - `GET /ready` (R5).
2. **`phoenix/api/launcher.py` and `phoenix/api/__main__.py` (new).** `python -m phoenix.api [--host H] [--port N] [--data-dir D]`.
   - Hosts other than `127.0.0.1` or `::1` are refused with exit code 2 and a clear stderr message, before anything binds.
   - `--port 0` means an OS-assigned port.
   - Uvicorn runs programmatically on the pre-bound socket (R3), with lifespan `on`.
   - After startup, exactly one line goes to stdout: `{"event":"phoenix.backend.ready","host":...,"port":N,"pid":P,"version":...}`.
   - Graceful shutdown on SIGINT, SIGTERM and CTRL_BREAK, with missions cancelled first (R8).
3. **Frozen-file handling.** No existing file under `phoenix/`, `alembic/` or `apps/` changes. Only the new `phoenix/api` paths are excluded, each by name with a reason, from the two `git diff origin/main` guards in `tests/test_screenshot_vision_loop.py` and `tests/test_windows_ui_automation.py`.
4. **Records.** ADR-084 in `DECISIONS.md`. `GET /ready` is listed in `docs/13_API_CONTRACTS.md`. A Phase 10 section is added to `FEATURE_TICKETS.md`.

## Non-goals

- Any other API route (PHX-104).
- WebSocket or events streaming (PHX-103).
- Mission persistence or a runner (PHX-102).
- API authentication (PHX-101).
- A Tauri sidecar (PHX-106).
- Settings UI or a filesystem workspace (PHX-108).
- Colibri wiring (PHX-116).
- New dependencies.
- Edits to frozen files.

## Tests

New `tests/test_api_server.py`, mapped in `tests/matrix.toml` as `unit, integration, api_contract, migrations`. It uses fakes only: a fake `ModelProvider` and SQLite under `tmp_path`. An autouse guard makes any httpx send or outbound `socket.create_connection` fail the test. It covers:

- lifespan startup and shutdown;
- services built once and stored on `app.state`;
- providers closed and the engine disposed;
- cleanup after a failed startup;
- the database migrated to head;
- readiness when healthy (`ready`), unavailable, raising or hung (`degraded`, bounded time), with no models (`degraded`), and with the database behind head (`unavailable`, 503), with no URL or detail echoed;
- missing tier models skipped;
- the default Ollama factory (default plus `think=False`, no I/O at construction);
- active missions cancelled at shutdown (INV-019);
- the CLI refusing non-loopback hosts and invalid ports;
- port 0 yielding a real bound port in the single JSON ready line, with nothing else on stdout.

`tests/test_api.py` stays unchanged and green.

## Acceptance

- Targeted tests pass.
- `ruff check`, `ruff format --check` and strict mypy are clean on the new modules and tests.
- `python scripts/test_matrix.py --validate-only` passes, and `git diff --check` is clean.
- No existing `phoenix/`, `alembic/` or `apps/` file changes.
- The full matrix runs on the final PHX-101 tip (see PHX-101 completion evidence).

## Completion evidence

- **Changed files.**
  - New: `phoenix/api/server.py`, `phoenix/api/launcher.py`, `phoenix/api/__main__.py`, `tests/test_api_server.py`, this ticket.
  - Edited: `tests/test_screenshot_vision_loop.py` and `tests/test_windows_ui_automation.py` (three named `:(exclude)` pathspecs each, with a reason comment), `tests/matrix.toml`, `DECISIONS.md` (ADR-084), `docs/13_API_CONTRACTS.md` (`GET /ready`), `FEATURE_TICKETS.md` (Phase 10).
- **Frozen files.** No existing `phoenix/`, `alembic/`, `apps/`, `pyproject.toml` or `uv.lock` file changed, and `tests/test_api.py` is unchanged. The guard tests pass with the exclusions.
- **Targeted checks (implementation commit `00cfbf0`).**
  - `pytest tests/test_api_server.py tests/test_api.py tests/test_persistence.py tests/test_bootstrap.py` plus the three frozen-file guard tests: 64 passed.
  - `ruff check`, `ruff format --check` and `mypy --strict` on `phoenix/api` and `tests/test_api_server.py`: clean.
  - `python scripts/test_matrix.py --validate-only`: OK. `git diff --check`: clean.
- **Full matrix.** Run once on the final PHX-101 tip; see PHX-101 completion evidence (all 13 gates PASS).
- **Live startup smoke (2026-10-16 20:09 IST, PHX-101 tip, local Ollama; health and model listing only, no generation).**
  - Ready line after 2.3 s, with a non-zero OS-assigned port.
  - `GET /ready` returned 200 `ready`: Ollama healthy, `qwen2.5-coder:7b`, `qwen3.5:2b`, `qwen3.5:4b` and `qwen3.5:9b` registered, database at head.
  - CTRL_BREAK gave a graceful uvicorn shutdown with no further stdout. The process exit code was 3 because uvicorn re-raises the captured signal (see limitations).
- **Independent review follow-up (2026-10-16).** No auth bypass, token leak or bind defect was found. Fixed:
  - **D1, concurrent startups racing the Alembic upgrade.** The upgrade now runs under an OS-level exclusive lock file in the data directory, with a bounded wait that fails with `MigrationLockTimeout`. Three concurrent subprocess upgrades now all reach head; without the lock, the same test fails 3/3. A second backend instance on one data directory remains possible; PHX-106 owns single-instance enforcement.
  - `/docs`, `/redoc`, `/openapi.json` and the OAuth2 redirect are removed.
  - The `ReadinessResponse` and ADR-084 wording now says the PHOENIX version is returned, and that provider URLs and versions are not echoed.
  - `PhoenixServer.shutdown` always drains, even if mission cancellation raises.
  - `/ready` keeps at most one database probe in flight.

  Each fix has a test.
- **Limitations.**
  - Q1–Q4 above remain open.
  - After a signal-driven shutdown, uvicorn re-raises the signal, so on Windows the exit code after CTRL_BREAK is 3. That is the same number as uvicorn's startup-failure code, so PHX-106 must not treat exit code 3 alone as a startup failure; the ready line is the signal.
