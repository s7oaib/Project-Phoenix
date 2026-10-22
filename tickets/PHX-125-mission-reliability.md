# PHX-125 — Mission reliability fixes

## Status
IN_PROGRESS (part A, code-only safety fixes: implemented and tested; part B,
model-visible inputs: approved by owner 2026-10-22, on its own branch)

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

Part B, branch `feature/PHX-125b-model-inputs` (approved by owner 2026-10-22;
exact text under "Owner approval needed"):
4. Render the documents routing sentence only when `documents` is offered
   (ADR-198).
5. Remove "Original request" from the capability tool-proposal, capability
   answer and verifier inputs and the document verifier input (ADR-198).
6. New conditional rules: knowledge routing, read answers (ADR-199).
7. `phoenix-prompts-v7`; every other v6 constant byte-identical.

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

## Owner approval needed (part B, `phoenix-prompts-v7`)

Approved by owner 2026-10-22 (all four items below). Every other v6 constant is byte-identical
(hash-pinned in `tests/test_phoenix_runtime.py`).

1. `ASSIGNMENT_SYSTEM` (changed). Before (v6), between "names above." and
   "Reply with":

   ```
   If "documents" is one of the allowed domains, assign every step that reads a
   file, lists a directory or inspects a file's metadata to "documents".
   ```

   After (v7): that place holds `{rules}`, and the same two lines, byte for
   byte, are the new constant `ASSIGNMENT_DOCUMENTS_RULE`, rendered only when
   `documents` is offered. With `documents` offered the rendered prompt is
   byte-identical to v6; without it the sentence is absent.

2. `ASSIGNMENT_KNOWLEDGE_RULE` (new; rendered into `{rules}` after the
   documents rule, only when `knowledge_rag` is offered):

   ```
   If "knowledge_rag" is one of the allowed domains, assign every step that looks
   up, searches or reads the user's uploaded documents or saved memories to
   "knowledge_rag".
   ```

3. `DOCUMENT_READ_ANSWER_RULE` (new; appended on its own line after
   `DOCUMENT_CAPABILITY_ANSWER_SYSTEM`, which is unchanged, only when the
   step's tool call read a file: `document.read` or filesystem `read_text`):

   ```
   For a read, report only what the file says; never say that a file was or will
   be created or saved.
   ```

4. Removed model input (code, not a constant). Before: the user turn of the
   capability tool proposal (`document_capability`, `media_specialist`), the
   capability answer (`document_capability_answer`, `media_answer`), the
   capability verifier (`capability_verifier`) and the document verifier
   (`document_verifier`) began with

   ```
   Original request:
   <the user's request>
   ```

   After: that block is gone; the inputs start at "Expected output:". The
   final answer and final verifier still receive the original request.

## Live verification (branch B, 2026-10-22, qwen3.5 4B/9B on Ollama, temp data dirs)

| Check | Before (v6, diagnosis) | After (v7 + part A) |
|---|---|---|
| `phoenix_knowledge_smoke.py --runs 5` | 2/5 completed (3 `disallowed_domain`) | 5/5 completed, 5/5 cited |
| `phoenix_mission_smoke.py` x5 | 5/6 completed | 4/5 completed (1 `verification_failed`) |
| Document create (read report.pdf, write out/summary.md) | 0/4 written; 1 false success (completed, no file) | 0/5 written; 0 false successes (3 `verification_failed` caught by ADR-195 on a create step that read, 2 `not_json` proposals) |

Open: the 4B document specialist still proposes `read_document` for most
create steps and sometimes returns non-JSON proposals (capability proposals
are not retried, PHX-109 R10); one run reached an approved `document.create`
and then failed `not_json` on the re-run proposal. Document create is not
yet reliable live; it now fails honestly instead of claiming a file.

## Completion evidence
Report changed files, commands/tests, results, decisions, assumptions, and known limitations.
