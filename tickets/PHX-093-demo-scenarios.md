# PHX-093 — Final demo scenarios

## Status
IN_PROGRESS (scenarios, fixtures and the rehearsal script are written and tested
offline on `feature/PHX-093-demo-scenarios`; the live rehearsal on BLAZE-6 and the
recordings await the owner)

## Phase
M5 release

## Objective
Six to eight scripted demos, one per PRD pillar, that the owner can run and record on
the desktop app, plus an automated rehearsal of the backend-driven parts.

## Authoritative documents
- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md`, `/MASTER_SPEC.md`
- `docs/08_PERMISSION_MATRIX.md`, `docs/13_API_CONTRACTS.md`, `docs/21_FRONTEND.md`
- ADR-084, ADR-085, ADR-089, ADR-100 (PHX-118b), ADR-150, ADR-151, ADR-155 to ADR-158,
  ADR-161, ADR-170 to ADR-174

## Dependencies
PHX-100 to PHX-122 on main (backend launcher, REST API, triage, desktop, capabilities,
KAIDO, voice, desktop capability UI).

## Requirements
1. `docs/DEMOS.md`: per demo the goal, prerequisites, fixture files (exact harmless
   contents), exact click/typing steps, expected on-screen result, what to say, a
   fallback and the length; plus a pre-flight checklist (Ollama first, GPU placement,
   Chromium in `D:\Phoenix\browsers`, voice models, environment variables).
2. Demos: (1) chat and triage — DIRECT vs a planned, LEVI-verified file answer
   (AURORA); (2) permissions, including the P4 one-time approval card; (3) memory/RAG
   with Sources; (4) web research with citations; (5) read a PDF/DOCX and create a
   summary `.md`; (6) PHOENIX's own browser on example.com, then a P4 action;
   (7) voice — "Rise, Phoenix"; (8) resilience — KAIDO banner, LEVI rejecting the
   tomato-soup injection.
3. `demos/fixtures/`: the harmless fixture files.
4. `scripts/demo_rehearsal.py`: launches `python -m phoenix.api` (temporary data
   directory, stdin token) and drives demos 1-6 and 8 where possible; `--dry-run` (no
   models) and live mode; PASS/FAIL per demo; never approves P4; P1/P2 approvals only
   for its own fixtures.
5. Offline tests for the rehearsal, mapped in `tests/matrix.toml` and the
   `mypy-scripts` gate.

## Non-goals
Product code or prompt-text changes; making live models deterministic; automating the
microphone, Ollama shutdown or the desktop UI; recording the videos.

## Expected files/components
- `docs/DEMOS.md` (new)
- `demos/fixtures/` (new): `project-notes.txt`, `launch-checklist.txt`,
  `harbour-report.txt`, `garden-club-notes.pdf`, `garden-club-notes.docx`,
  `tomato-soup.txt`
- `scripts/demo_rehearsal.py` (new)
- `tests/test_demo_rehearsal.py` (new), `tests/matrix.toml`
- `.gitattributes` (PDF/DOCX are binary, so the pinned bytes never change)
- `DECISIONS.md` (ADR-175), `FEATURE_TICKETS.md`, this ticket

## Verification
- Every UI label in `docs/DEMOS.md` was taken from `apps/desktop/src` (App.tsx,
  adapters/ConnectedApp.tsx, CapabilityViews.tsx, SetupViews.tsx, core.ts); every
  backend behaviour from the routes, capability flags, permission matrix, KAIDO policy
  and the PHX-118b tomato-soup case.
- `uv run pytest tests/test_demo_rehearsal.py` — the rehearsal against the real
  composition root in process (scripted model, embeddings and Playwright; network
  guard on).
- `uv run python scripts/demo_rehearsal.py --dry-run`.
- Full matrix: `uv run python scripts/test_matrix.py --allow-dirty`.

## Acceptance criteria
- [x] `docs/DEMOS.md` covers the eight demos and the pre-flight checklist.
- [x] Fixtures are harmless, documented byte for byte and SHA-256-pinned.
- [x] The rehearsal reports PASS/FAIL/SKIP/MANUAL per demo, dry-run without models.
- [x] The approval gate admits only P1/P2 on the demo allowlists; P3/P4/P5 are denied
      (tested), and the demo 6 P4 click is shown with its summary and denied.
- [x] Offline tests pass and are mapped in the matrix; `mypy --strict` covers the script.
- [ ] Owner: live rehearsal on BLAZE-6 (`uv run python scripts/demo_rehearsal.py`).
- [ ] Owner: demos 1-8 recorded on the installed desktop app.

## Completion evidence
Branch `feature/PHX-093-demo-scenarios` from `origin/main` 2011b78.

Files: see "Expected files/components".

Decisions: ADR-175 (the rehearsal's approval gate). No product code, prompt text,
dependency, migration or API change.

Tests: `tests/test_demo_rehearsal.py` — 21 passed offline (dry-run and fixture pins,
approval gate for P0/P3/P4/P5/None, exact allowlists, demos 1, 2, 3, 4 (skip and
network-guarded fail), 5, 6 (P4 click denied, nothing clicked), 7, 8 (LEVI rejection,
the verified-injection regression, an engaged KAIDO), error reporting, ready-line
parsing). Full matrix (`--allow-dirty`, 2026-10-22): PASS, every gate green; pytest
16950 passed, 0 failed, 32 skipped. (A first run reported FAIL only for
`tree_modified_by_gates`, because files were edited while it ran; its gates were all
green.)

Assumptions: the installed app lives at `%LOCALAPPDATA%\PHOENIX\phoenix-desktop.exe`
(per-user NSIS default) and inherits the launching shell's environment; demo data in
`D:\Phoenix\demo-data` and `D:\Phoenix\demo-workspace`.

Limitations:
- The live mode was not run here (no GPU/Ollama in this session); its launch, readiness
  and shutdown path is exercised only up to ready-line parsing. Live models vary run to
  run, so a live FAIL means "rehearse again".
- Demo 7 (real microphone) and the KAIDO banner (stopping Ollama) stay manual; the
  rehearsal checks KAIDO is clear beforehand and reports the voice state.
- Demo 5 rehearses the PDF; the DOCX is the documented fallback.
- The desktop approves with "this session" only; there is no grant-revocation UI, so
  demo 2 shows deny and approve, not revoke.
