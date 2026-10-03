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
