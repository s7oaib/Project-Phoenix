# PHX-108 — Settings, onboarding and capability views

Status: DONE (recommended options applied under the owner's instruction "use recommended options for everything"; the owner reviews R1–R12 in the PR, and merging confirms them)
Phase: 10 — Product Integration (milestone M2, desktop)
Branch: `feature/PHX-108-settings-onboarding` (local commits only; no push, no PR)
Base: `origin/main` `5722431` (PR #85)

## Objective

Make first use and setup obvious and make the remaining placeholder views honest and live. A first-run onboarding flow walks the user through the order that matters (model runtime running, required models present, storage, document folder); a writable Settings view replaces the read-only overview; the Computer Control, Research and Media placeholders become real views driven by backend capability flags. Fix the owner's rehearsal defect: if PHOENIX starts before Ollama, every mission fails `no_eligible_model` until a restart.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md` (INV-004, INV-011, INV-012, INV-015, INV-020, INV-021, INV-022), `/MASTER_SPEC.md`
- `docs/13_API_CONTRACTS.md`, `docs/20_UI_UX_SPEC.md`, `docs/21_FRONTEND.md`
- ADR-089 (REST v1), ADR-091 (native proxy), ADR-092 (adapters); tickets PHX-104, PHX-106, PHX-107

## Owner input

- Rehearsal findings (2026-10-19): start Ollama first or every mission fails `no_eligible_model` (tier models were registered once, at startup); onboarding must make the order obvious: Ollama present/running, `qwen3.5:4b` and `qwen3.5:9b` present, data/storage location, document folder.
- "Use recommended options for everything"; decisions recorded as ADR-110 to ADR-114.

## Recommendations (R1–R12, applied)

- **R1 — Late model registration (ADR-110).** A `ModelRegistrar` in the composition root replaces the one-shot startup registration. `refresh()` is single-flight, asks only for tier models still missing, and bounds each provider call by the probe timeout. It runs at startup (bounded by the startup timeout, as before), in a background loop (every 10 s while a provider is unavailable, every 60 s while it answers but a tier model is not installed; nothing once all are registered), before each mission while a *required* model is missing (`MissionService(before_run=...)`, so the first mission after Ollama starts works with no re-check), and on `GET /ready` (only when a provider answers), `GET /models` and `GET /onboarding`. Registered models are never removed. New `ServerConfig` fields: `model_refresh_seconds`, `model_refresh_idle_seconds`.
- **R2 — Onboarding checks (ADR-113).** `GET /onboarding` returns, in order, the runtime state per provider (`running`, `degraded`, `not_running`: installed but not answering, `not_detected`), each tier model (`required`, `registered`, backend-built `install_command` while missing), storage (`data_dir`, `free_bytes`, `database_ready`) and the workspace, plus `complete`. Required models are `qwen3.5:4b` and `qwen3.5:9b` (T1/T2). Detection only looks for the runtime executable (`PATH`, then `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`); nothing is started, installed or downloaded.
- **R3 — Not persisted.** Onboarding is derived from live checks, not a stored "first run done" flag: the first-run screen appears whenever setup is incomplete at launch and the user may continue without finishing (text chat works without a workspace, INV-015). Dismissal lasts for the launch.
- **R4 — Capability flags (ADR-112).** `GET /capabilities` returns a fixed table in `phoenix/api/capabilities.py`: `workspace_documents` (enabled, PHX-046), `memory` (PHX-110), `web_research` (PHX-111), `computer_control` (PHX-112), `media` and `document_generation` (PHX-113), `voice` (PHX-114), each with its ticket and carrying agent. PHX-110 to PHX-114 enable their capability by flipping `enabled` in the same change that wires it into missions. Flags grant nothing; PermissionEngine stays authoritative (INV-004).
- **R5 — Saved default mission path (ADR-111).** `PATCH /settings` also accepts `default_mission_path` (`auto`, `direct`, `complex`); at least one field is required and the workspace, if given, is applied first (a refused workspace saves nothing). Stored in `app_settings` under a second key; migration `0009_default_mission_path` widens the key check and constrains the value. `POST /chat` is unchanged: the desktop sends the saved choice as `path` (`auto` omits it).
- **R6 — Proxy allowlist (ADR-113).** Exactly two routes are added to the Rust allowlist, `GET /onboarding` and `GET /capabilities`, with no query keys and no body (18 → 20). Cargo tests admit them and refuse other methods, queries, trailing slashes and sub-paths; `workspace.test.mjs` pins the exact 20-entry table and checks every fixed route the adapters name. PHX-101 security covers both (401 without the token, 503 while starting).
- **R7 — First-run screen (ADR-114).** When the backend is ready and setup is incomplete, ConnectedApp shows the four steps instead of App, with "Re-check" (reloads onboarding, models and settings) and "Continue to PHOENIX" / "Continue without finishing". It re-checks every 3 s while shown, so it updates as the user starts Ollama or pulls a model. Provider names and install commands come from the backend, so webview code still never names a provider.
- **R8 — Settings view (writable).** Document workspace folder form, default mission path (select + Save), read-only model tiers ("model selection arrives in a later ticket"), Voice and Memory placeholders driven by the `voice`/`memory` flags, retention, and the PermissionEngine note.
- **R9 — Computer Control, Research and Media.** Each shows its flag(s) with the carrying agent: "not yet enabled — arrives with PHX-11x; PHOENIX does not attempt it in missions until then", or "enabled — ask PHOENIX in Home / Chat", or "status unavailable" when not connected. No actions.
- **R10 — App.tsx, minimal.** Changes only after `export const diagnosticMetricNames` (historical byte checks unchanged): optional `panels` for exactly the five views App has no port for, and an optional header `statusLine` (resolves the PHX-107 R11 copy gap for the header). Default App is unchanged in behaviour and markup.
- **R11 — Connection bar.** The workspace folder form moves to Settings and Onboarding; the bar shows the workspace read-only ("change it in Settings") and labels its selector "Mission path (this launch)", which starts from the saved default.
- **R12 — Frozen files.** `server.py`, `missions.py`, `routes/__init__.py`, `routes/schemas.py`, `routes/system.py`, `persistence/settings.py`, `proxy.rs`, `core.ts`, `ConnectedApp.tsx` and `connected.css` move to a PHX-108 hash pin (`PHX_108_COMMIT`), landed as an implementation commit then a pin commit; **the PR must merge with a merge commit** so the pinned SHA stays reachable. The two `git diff origin/main` guards exclude the five new files.

## Non-goals

- Starting, installing or pulling models from PHOENIX (the user runs the shown command).
- Model selection or tier editing, voice settings, memory settings (PHX-110, PHX-114 and later).
- Computer control, web research, media or document generation in missions (PHX-111 to PHX-113).
- Changing the data directory at runtime (`PHOENIX_DATA_DIR` before launch, as before).
- Packaging and the release executable (owned by another agent).

## Changed files

- Backend: `phoenix/api/server.py` (ModelRegistrar, loop, readiness refresh, config), `phoenix/api/missions.py` (`before_run`), `phoenix/api/onboarding.py` (new), `phoenix/api/capabilities.py` (new), `phoenix/api/routes/setup.py` (new), `phoenix/api/routes/__init__.py`, `phoenix/api/routes/schemas.py`, `phoenix/api/routes/system.py`, `phoenix/persistence/settings.py`, `alembic/versions/0009_default_mission_path.py` (new).
- Native: `apps/desktop/src-tauri/src/proxy.rs`.
- Desktop: `apps/desktop/src/App.tsx`, `apps/desktop/src/adapters/core.ts`, `ConnectedApp.tsx`, `connected.css`, `SetupViews.tsx` (new).
- Tests: `tests/test_settings_onboarding.py` (new), `tests/test_rest_api.py`, `tests/desktop_adapters.mjs`, `tests/workspace.test.mjs`, `tests/test_prompt_injection_defenses.py` (pin), the two diff guards, migration-head constants, `tests/matrix.toml`.
- Docs: `DECISIONS.md` (ADR-110 to ADR-114), `docs/13_API_CONTRACTS.md`, `docs/21_FRONTEND.md`, `FEATURE_TICKETS.md`, this ticket.

## Verification

- `tests/test_settings_onboarding.py`: Ollama started after PHOENIX is picked up by `/ready`, by the onboarding re-check, by the background loop and, with no request at all, by the next mission (which completes); the loop backs off once the provider answers and never re-asks for registered models; refresh is single-flight; missions never probe for optional models; onboarding states and order, required/optional models, install commands, storage, completion; degraded runtime; capability table, agents exist, read-only, a flipped flag is served; token and 503 on the new routes; no provider detail echoed; default mission path saved across restart, combined patch, bad bodies, refused workspace saves nothing; migration 0009 constraints and downgrade.
- `tests/test_rest_api.py`: exact route table (+2), settings body.
- Cargo `proxy` tests: 20 routes, new routes admitted GET-only with no query/body.
- `npm test`: setup readers; store loads setup state and applies the saved default; default-path PATCH and refusals; first-run screen order, texts, dismissal; install command; re-check and polling; complete setup skips the screen; Settings, capability views and flipped flag; App panels only for the five views; bar no longer edits the workspace; exact allowlist.
- Full matrix: `uv run python scripts/test_matrix.py` (results in the completion evidence below).

## Acceptance criteria

- Starting Ollama after PHOENIX needs no restart: the next mission, the onboarding re-check or the 10 s loop registers the tier models.
- First run makes the order obvious and every check reflects the backend.
- Settings persists the workspace and the default mission path; model and voice sections are honest placeholders.
- Computer Control, Research and Media reflect backend flags and change only by flipping a flag.
- Exactly two new proxied routes, tested; token never reaches JS; CSP and capabilities unchanged.
- All matrix gates pass.

## Completion evidence

See the PHX-108 commits and the final report: changed files above, commands and results of the targeted tests and the full matrix, ADR-110 to ADR-114, limitations (below).

## Known limitations

- A mission that started before the provider came up still fails explicitly (`provider_unavailable`); the user sends it again.
- Runtime detection looks only at `PATH` and the default per-user install folder; a custom install location reads "not found" while the runtime is stopped (it reads "running" once started).
- The first-run screen's 3 s re-check probes the provider while shown; it stops once the user continues.
- Manual native WebView2 acceptance of the new views is not performed here.
