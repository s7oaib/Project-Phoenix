# PHX-134 — 4B planner

## Status
DONE (local branch `feature/PHX-134-4b-planner`; not pushed)

## Objective
Faster multi-step missions by planning (AIZEN) and assigning steps on
qwen3.5:4b, if accuracy holds. Reversible, measured.

## Result
- `PHOENIX_PLANNER_TIER` = `9b` (default) | `4b`; unknown values refused
  (ADR-240, ADR-241). LEVI unchanged.
- Live mission smoke ×3 each way: 9B 3/3, median 162.1 s; 4B 2/3
  (1 verification_failed), median 91.8 s. Success lower → 9B stays default.
- Tests: `tests/test_planner_tier.py`.
