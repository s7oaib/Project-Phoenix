# PHX-122 — Desktop capability UI

## Status
IN_PROGRESS (implemented and tested on `feature/PHX-122-desktop-capability-ui`; awaiting
PR review and the owner's live check in the desktop app)

## Phase
M3 capabilities (desktop)

## Objective
Show in the Tauri/React desktop the capabilities the backend already serves: voice,
files and memory, KAIDO degraded mode, computer-control approval summaries and the web
research launch flag.

## Authoritative documents
- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md`, `/MASTER_SPEC.md`
- `docs/21_FRONTEND.md`, `docs/13_API_CONTRACTS.md`
- ADR-091, ADR-092, ADR-108, ADR-131 to ADR-133, ADR-151, ADR-156, ADR-161

## Dependencies
PHX-107, PHX-108, PHX-110, PHX-111, PHX-112, PHX-114, PHX-119 (all on main).

## Requirements
1. Voice: mic toggle (`POST /voice/start`, `/voice/stop`), live state from
   `GET /voice/status` and `voice.*` events, a clear text fallback when unavailable.
   Voice never approves permissions.
2. Files and memory: upload (8 MiB cap shown), list and delete uploads; list, add,
   search and delete memory notes; answers show their cited `sources`.
3. KAIDO: a "Degraded mode — answers unverified" banner while `/ready` reports
   `kaido.engaged`; KAIDO answers labelled unverified in Chat.
4. Approvals: the backend `summary` shown prominently above the raw details; P4 says
   it is a one-time approval.
5. Settings: web research "enabled for this launch" or "off — start PHOENIX with
   PHOENIX_WEB_RESEARCH_ENABLED=true" (read-only).

## Non-goals
Backend changes; new Tauri commands, capabilities or plugins; recording audio in the
webview; editing memory notes; changing the web-research flag from the UI.

## Expected files/components
`apps/desktop/src-tauri/src/proxy.rs`, `apps/desktop/src/adapters/core.ts`,
`ConnectedApp.tsx`, `SetupViews.tsx`, new `CapabilityViews.tsx`, `connected.css`,
`App.tsx` (`ViewPanel` only), tests, docs, ADR-170 to ADR-174.

## Verification
Rust proxy unit tests (allowed and refused routes, upload decoding), node adapter and
view tests in `tests/desktop_adapters.mjs`, the allowlist and App byte checks in
`tests/workspace.test.mjs`, pins in `tests/test_prompt_injection_defenses.py`, and the
full matrix (`scripts/test_matrix.py`).

## Acceptance criteria
- Each requirement above is visible in the connected desktop and covered by tests.
- The proxy admits exactly 31 routes; everything else is refused before any I/O.
- App.tsx's frozen prefix is unchanged; the token never reaches JavaScript.

## Completion evidence
See the PR description: changed files, commands, results, decisions (ADR-170 to
ADR-174), assumptions and limitations (live desktop check by the owner pending).
