# PHX-003 — Tauri + React Desktop Foundation

## Status
TODO

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
