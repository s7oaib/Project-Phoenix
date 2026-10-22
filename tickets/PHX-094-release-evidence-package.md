# PHX-094 — Release evidence package

## Status
IN_PROGRESS (package written on `feature/PHX-094-release-evidence`; PENDING cells for the
Colibri gate re-run, the control smoke re-run and the demo rehearsal are filled by the
release coordinator)

## Phase
9 — Validation & Release (milestone M5)

## Objective
Define "production done" for PHOENIX v0.1.0 (Full PRD v1) and record honest evidence for
every claim: tests, live acceptance, security review, FYP numbers, limitations and
owner-only items.

## Authoritative documents
- `/AGENTS.md`
- `/PRD.md`
- `/docs/24_TEST_EVALUATION.md`
- `/docs/security/SECURITY_REVIEW_2026-10-22.md`

## Dependencies
PHX-090, PHX-091, PHX-093, PHX-116, PHX-117, PHX-123, PHX-124.

## Requirements
1. `docs/release/RELEASE_CHECKLIST.md`: one row per PRD V1 requirement and per AGENTS.md
   protected-architecture rule, status DONE / DONE-WITH-LIMITS / OPEN / MANUAL, with
   evidence.
2. `docs/release/RELEASE_EVIDENCE_2026-10-22.md`: test matrix, live acceptance, security
   summary, FYP numbers, known limitations, owner-only items.
3. `docs/24_TEST_EVALUATION.md`: the 2026-10-22 configuration (2) re-run and the live
   LEVI catch rate.
4. `FEATURE_TICKETS.md`: statuses that are verifiably DONE.

## Non-goals
No code changes. No raw traces, prompts, answers or private content in the repository.

## Acceptance criteria
- Every row cites a file, test, ADR, PR or CI run.
- Unfinished runs are marked `PENDING: <what>`, never guessed.
- The docs checks pass: `tests/test_test_matrix.py`, `tests/test_fyp_benchmark.py`,
  `tests/test_fyp_multi_agent.py`, `tests/test_metric_benchmark.py`.

## Completion evidence
Docs checks: `uv run pytest tests/test_test_matrix.py tests/test_fyp_benchmark.py tests/test_fyp_multi_agent.py tests/test_metric_benchmark.py -q`: 74 passed. Open items: checklist rows C4 (keyboard/mouse
fallback) and D2 (live document creation) are OPEN and need an owner decision.
