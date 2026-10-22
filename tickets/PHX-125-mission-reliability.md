# PHX-125 — Mission reliability fixes

## Status
IN_PROGRESS (part A, code-only safety fixes: implemented and tested; part B,
model-visible inputs: owner-gated, on its own branch)

## Phase
M3 capabilities (live-run follow-up, 2026-10-22)

## Objective
Fix three causes of unreliable live missions found in the 2026-10-22
diagnosis, without weakening any guard:
(A) the assignment model names `documents` when it is not offered;
(B) document-create missions read instead of writing, and one completed
claiming a saved file that was never written (silent false success);
(C) 9B verifier and answer turns apply the original request's output format to
intermediate steps, and repeated JSON keys are silently resolved.

## Authoritative documents
- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `DECISIONS.md` ADR-093, ADR-140, ADR-166, ADR-195 to ADR-199

## Dependencies
PHX-109, PHX-113, PHX-120, PHX-121 (main 45dfb66).

## Requirements
Part A, branch `fix/PHX-125a-mission-safety` (code only; no text a model sees
changes):
1. A document step whose objective asks to create or save a file, and the
   final answer of a document mission whose request asks for one, cannot pass
   LEVI unless a `document.create` call succeeded and applied its effect
   (ADR-195). The check only adds a failure; it never passes anything.
2. `parse_json_object` rejects repeated keys as a schema error (ADR-196),
   retried by MAHORAGA within its existing budgets.
3. `DISALLOWED_DOMAIN` is retryable output, bounded by the existing budgets
   (ADR-197).

Part B, branch `feature/PHX-125b-model-inputs` (owner-gated): see that
branch's version of this file.

## Non-goals
New tools, new permissions, a planner-declared operation field, retries of a
LEVI verdict, changes to MAHORAGA budgets.

## Expected files/components
`phoenix/agents/capability_steps.py`, `phoenix/agents/runtime.py`,
`phoenix/agents/responders.py`, `phoenix/agents/mission_recovery.py`, tests in
`tests/test_media_document_steps.py` and `tests/test_mahoraga_missions.py`,
pins in `tests/test_prompt_injection_defenses.py`.

## Verification
- `uv run pytest -q tests/test_media_document_steps.py tests/test_mahoraga_missions.py`
- `uv run python scripts/test_matrix.py --allow-dirty`

## Acceptance criteria
- The captured false-success shape (create step that only read) fails
  `verification_failed` with no file written and LEVI's model not consulted
  for that step.
- A document mission asking for a file that no step wrote fails at the final
  answer.
- A verified create still completes; a verified create does not override a
  failing LEVI model check.
- Text-only steps and missions are unaffected.
- Repeated keys at any depth give `schema`; a disallowed domain is retried
  once and then keeps its code.

## Completion evidence
Report changed files, commands/tests, results, decisions, assumptions, and known limitations.
