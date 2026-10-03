# PHX-003 — Tauri + React Desktop Foundation

## Status
DONE

## Phase
Phase 0 — Foundation

## Objective
Create the minimal Tauri 2 + React + TypeScript + Vite desktop application foundation for PHOENIX while preserving the frontend/backend boundary and avoiding premature product features.

## Authoritative documents
- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `/docs/01_ARCHITECTURE.md`
- `/docs/13_API_CONTRACTS.md`
- `/docs/20_UI_UX_SPEC.md`
- `/docs/21_FRONTEND.md`
- `/docs/25_DEPENDENCIES.md`

## Dependencies
- PHX-001 — Repository Bootstrap: DONE
- PHX-002 — FastAPI Backend Foundation: DONE

## Requirements
- Scaffold the existing `apps/desktop/` workspace as a Tauri 2 desktop application using React, TypeScript, and Vite.
- Keep the desktop application inside the existing npm workspace; do not create a second frontend repository or package manager.
- Add only the runtime/build dependencies required for this foundation.
- Establish strict TypeScript configuration and reproducible npm scripts for development, type checking, web build, Tauri development/build commands, and targeted verification.
- Establish the minimal Tauri Rust boundary under `apps/desktop/src-tauri/` with valid Tauri 2 configuration and Cargo metadata.
- Use least-privilege Tauri capabilities/permissions. Do not enable shell, filesystem, process, updater, global-shortcut, clipboard, HTTP, or other privileged plugins merely for future use.
- The frontend must not call Ollama, Colibri, SQLite, privileged OS APIs, or future Phoenix tools directly.
- Do not duplicate PHX-002 backend behavior inside Tauri/Rust.
- Render only a minimal Phoenix foundation screen sufficient to prove React/Vite/Tauri integration; do not implement the later application shell, navigation, chat, voice, agents, tasks, permissions, memory, files, settings, observability, or other feature screens.
- Preserve text accessibility and semantic HTML in the minimal foundation UI.
- Keep backend tests/tooling green.

## Non-goals
- No full UI shell/navigation; PHX-080 owns that work.
- No backend HTTP integration beyond what is strictly needed to prove the desktop scaffold; do not add speculative API clients.
- No WebSocket/event integration.
- No Zustand, TanStack Query, React Flow/XYFlow, shadcn/ui, Motion, or feature-state architecture unless strictly required by the scaffold; those belong to later frontend/UI tickets.
- No voice, agents, models, permissions, memory, RAG, computer control, research, media, or document features.
- No Tauri privileged plugins for future capabilities.
- No installer/release packaging work beyond validating the Tauri build configuration; PHX-092 owns packaging.
- No Docker/cloud/CI/CD.

## Expected files/components
Use the existing `apps/desktop/` workspace. A minimal implementation will normally include React/Vite/TypeScript source/configuration plus `src-tauri/` Cargo/Tauri configuration. Do not create empty future feature directories solely to mirror the eventual architecture.

## Verification
At minimum run the existing Python checks relevant to preserving PHX-001/002 plus desktop checks such as:
- `npm ci --ignore-scripts` or the repository-approved install flow
- `npm test`
- desktop TypeScript type check
- desktop Vite production build
- `cargo check` for the Tauri crate
- `cargo test` if the generated crate has applicable tests
- a Tauri configuration/CLI validation command when available and safe
- `git diff --check`

Do not launch a long-running GUI/dev server as acceptance evidence. If Rust/Tauri host prerequisites are missing, distinguish an implementation defect from an environment prerequisite and report it explicitly rather than faking success.

## Acceptance criteria
- Existing `apps/desktop/` becomes a valid Tauri 2 + React + TypeScript + Vite application foundation.
- Desktop package remains part of the root npm workspace with lockfile reproducibility.
- Minimal React foundation screen builds successfully.
- Tauri Rust/configuration boundary validates successfully on the development machine, or any external host prerequisite blocker is explicitly evidenced.
- TypeScript strictness and build/type-check scripts exist and pass.
- No direct provider/database/privileged-tool access is introduced in the frontend.
- No unnecessary Tauri plugin capability is enabled.
- Existing PHX-001 and PHX-002 tests remain green.
- No secrets or generated build/runtime artifacts are committed.
- Relevant invariants remain satisfied.
- Ticket status and `/FEATURE_TICKETS.md` are updated only after all acceptance criteria pass.

## Completion evidence
Report:
- changed files
- frontend/Rust dependencies added with classification and reason
- exact npm/Tauri/Cargo commands and results
- minimal rendered UI contract
- Tauri capability/permission state
- invariant IDs reviewed
- decisions added, if any
- environment prerequisites discovered
- assumptions and known limitations

### Verified completion — 2026-10-04

- Implemented the minimal React/TypeScript/Vite workspace and Tauri 2 native
  boundary under `apps/desktop/`. No source correction was needed during recovery.
- Runtime npm dependencies: React and React DOM 19.3.0. Build/dev dependencies:
  TypeScript 6.0.3, Vite 8.3.2, React Vite plugin 6.1.1, Tauri CLI 2.12.1,
  React/React DOM type packages 19.3.0, and Node types 22.20.5. The root npm
  lockfile captures resolved versions; Node >=22.12.0 reflects Vite requirements.
- Direct Rust dependencies remain `tauri = "2"` and `tauri-build = "2"`.
  Cargo generated `src-tauri/Cargo.lock`; it resolves these to 2.12.1 and 2.7.1.
  The application lockfile is retained for version control and was not edited manually.
- Toolchain: `rustc 1.99.0 (b940084d7 2026-09-28)` and
  `cargo 1.99.0 (5f94df478 2026-08-27)`, with active/default
  `stable-x86_64-pc-windows-msvc`. Verification commands prepended the existing
  user Cargo bin directory to process PATH because Codex retained its old PATH.
  No host software or persistent environment changes were made during recovery.
- Native verification:
  - `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml`: PASS.
  - `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml`: PASS;
    zero Rust tests, zero failures; the minimal crate has no custom Rust behavior.
  - `npm run tauri:info --workspace @phoenix/desktop`: PASS; stable MSVC Rust,
    C++ Build Tools, and WebView2 available; React/Vite configuration detected.
  - `npm run tauri:build --workspace @phoenix/desktop`: PASS;
    `tauri build --no-bundle` produced the optimized Windows executable in ignored
    `src-tauri/target/release/`, including a successful frontend build hook.
- Frontend verification:
  - `npm install --package-lock-only --ignore-scripts`: PASS.
  - `npm ci --ignore-scripts`: PASS; zero reported vulnerabilities.
  - `npm test`: PASS; four workspace/rendering/security checks.
  - `npm run typecheck --workspace @phoenix/desktop`: PASS; strict TypeScript.
  - `npm run build --workspace @phoenix/desktop`: PASS.
- Backend regression:
  - `uv sync --locked`: PASS.
  - `uv run --locked pytest`: PASS; 19 tests.
  - `uv run --locked ruff check .`: PASS.
  - `uv run --locked ruff format --check .`: PASS; 14 files.
  - `uv run --locked mypy`: PASS; 14 source files.
  - `uv build`: PASS; wheel and source distribution.
  - `/health` remains `{"status":"ok","service":"phoenix-api","version":"0.1.0"}`.
- `git diff --check`: PASS; generated environments, frontend/native builds,
  schemas, caches, and logs remain ignored. No secrets, compiled binaries, or
  machine-specific private paths are staged or committed.
- UI contract: semantic main/heading/paragraphs render only `PHOENIX`,
  `Rise from the ashes.`, and `Desktop foundation operational.`
- Capabilities: main window only, no IPC permissions, plugins, or custom Rust
  commands. Production CSP remains restrictive; development permits only the
  checked React refresh preamble hash, loopback HMR, and inline CSS updates.
- Reviewed INV-001 through INV-024: preserved or unaffected, especially INV-001,
  INV-003, INV-004, INV-011, INV-012, INV-015, INV-016, INV-021, and INV-024.
  No provider/database/tool calls or backend duplication introduced.
- Decisions: ADR-018 preserved; no additional ADR required.
- Limitations: GUI interaction and installer packaging were not tested. The
  existing third-party Starlette HTTPX deprecation warning remains; tests pass.
- All acceptance checks passed before status updates. No staging, commit, push,
  pull request, merge, or PHX-004 implementation performed.
