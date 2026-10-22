# PHX-127 — Demo reliability prompts (phoenix-prompts-v8)

## Status
IN_PROGRESS (implemented and tested on a local branch; awaiting PR review)

## Phase
M5 release hardening

## Objective
Apply the owner-approved PHX-126 prompt proposals 2 to 6 verbatim, so the
model-only (class c) demo failures 3 to 6 get the owner's chosen guidance.
Proposal 1 (a read_text example line) is declined and not added.

## Authoritative documents
- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `DECISIONS.md` ADR-205
- `tickets/PHX-126-demo-flow.md` ("Proposals for the owner")

## Dependencies
PHX-125b (`phoenix-prompts-v7`, PR #111), PHX-126 (root causes and proposals).

## Approved by owner 2026-10-22: exact before/after text

Only the line wrapping around each insertion moved; the wording of every
other sentence and every other constant is byte-identical (hash-pinned).

### #2 `COMBINED_PLANNING_LINES` (phoenix/agents/prompts.py)
- `"knowledge_rag"` before: `- looking up facts in the user's uploaded documents and saved memories`
- after: `- looking up facts in files the user uploaded to PHOENIX (not workspace files) and in saved memories`
- `"documents"` before: `- reading one named file, listing one directory or inspecting one file's metadata in the user's workspace`
- after: `- reading one named file, listing one directory or inspecting one file's metadata in the user's workspace (not uploaded files)`

### #3 research rules (`RESEARCH_ANSWER_SYSTEM`)
Added after "...a quote copied word for word from that source's content that
supports the claim.":
`Quote only text from a source's content item, never its title.`

Placement note: the ticket says "research specialist rules". The research
role that writes quotes is `research_answer` (it introduces itself as
"PHOENIX's web research specialist"); `RESEARCH_SPECIALIST_SYSTEM` only
proposes a search query and never quotes, so the sentence goes into
`RESEARCH_ANSWER_SYSTEM`. Wording unchanged.

### #4 `DOCUMENT_CAPABILITY_SYSTEM`
Added after "Created documents hold plain text only.":
`When the objective asks to create a file, propose create_document with the content from the earlier step outputs; do not read again.`

### #5 `COMBINED_PLANNING_SYSTEM`
Added directly after "Plan an action that cannot be undone only when the user
explicitly asked for it.":
`When the user explicitly asks for an action (creating a file, clicking), plan a step for it; never drop it.`

### #6 `CONTROL_SPECIALIST_SYSTEM` (phoenix/agents/control_prompts.py)
Added after "browser actions after "navigate" act on that page.":
`When the objective asks to click a link, propose navigate and then click with a selector such as "a"; PHOENIX asks the user before any click.`

Version note: `CONTROL_SPECIALIST_SYSTEM` belongs to the separate
`phoenix-control-prompts` set, so that set moves to
`phoenix-control-prompts-v2` (a changed constant needs a new version);
`phoenix-prompts` moves to v8. No braces were added, so both `.format()`
renderers are unaffected.

## Non-goals
No permission, P-level, LEVI rule, tool or code-path change. No change to the
rehearsal or smoke scripts. Proposal 1 is not applied.

## Verification
- `uv run pytest tests/test_phoenix_runtime.py tests/test_computer_control_steps.py tests/test_media_document_steps.py tests/test_web_research.py tests/test_prompt_injection_defenses.py`
- `uv run python scripts/test_matrix.py --allow-dirty`
- Live: `scripts/demo_rehearsal.py` 2x, `scripts/phoenix_knowledge_smoke.py --runs 5`,
  `scripts/phoenix_mission_smoke.py` 3x (temp data dirs, Ollama 127.0.0.1:11434).

## Acceptance criteria
- Each approved sentence appears exactly once, only in its own prompt; the two
  planner lines render only when their capability is offered.
- Every other v7 and control-v1 constant is byte-identical (hash pins).
- `prompts.py` and `control_prompts.py` are pinned at the implementation
  commit (two-commit pattern).

## Live results (2026-10-22, Ollama, RTX 3050 6 GB, temp data dirs, demo 7 MANUAL)

Environment note: the first attempt ran with Ollama on CPU only (Ollama had
started without seeing the NVIDIA GPU; every mission fell to KAIDO
`resource_pressure`) and is discarded; two more attempts were cut short by
session restarts. The runs below are complete and GPU-placed (the 9B model is
partly offloaded, as on every 6 GB run).

| Demo | Before (PHX-126, 2 runs) | v8 run 1 | v8 run 2 |
| --- | --- | --- | --- |
| 1 | PASS / PASS | PASS | PASS |
| 2 | PASS / FAIL | PASS | FAIL: deny part PASS; approve part `responder_output` before any read |
| 3 | FAIL / FAIL (`permission_denied`, workspace read) | FAIL (`permission_denied` on `document.read`) | FAIL (`permission_denied` on `filesystem.list_directory`) |
| 4 | FAIL / FAIL (`verification_failed`, title quoted) | FAIL (`verification_failed`) | FAIL (`tool_failed` after the search was approved) |
| 5 | FAIL / FAIL (no create proposed) | FAIL: PDF read approved, no create, `verification_failed` | same as run 1 |
| 6 | FAIL / FAIL (no click proposed) | FAIL: read `responder_output`; click mission asked for `windows.list_windows` (refused), no P4 | FAIL: read `verification_failed`; click mission completed and verified with no P4 request |
| 8 | PASS / PASS | PASS (`levi_rejected`) | PASS (`levi_rejected`) |

Smokes: knowledge `--runs 5` 5/5 completed (v7: 5/5). Mission smoke 2/3
completed (v7: 4/5); the failure is `verification_failed` from the document
verifier on a correct read in the PHX-046 single-family path, which renders
none of the v8 text (`PLANNING_SYSTEM`, `DOCUMENT_SPECIALIST_SYSTEM`).

Reading: v8 did not turn any of demos 3 to 6 into a PASS in two runs. Demo 2
dropped one run, as it did before v8 (1/2 then and now). One behaviour to watch:
in run 2, demo 6's click mission completed and passed LEVI without any click
being requested (before v8 it failed verification). Nothing was clicked, so
no safety property changed, but the demo outcome is less informative. Evidence:
`scratchpad\live3\run1`, `run2`, `knowledge`, `mission1` to `mission3`.

## Test matrix
`uv run python scripts/test_matrix.py --allow-dirty`: every stage PASS except
pytest, 17016 passed, 32 skipped, 1 failed:
`tests/test_model_benchmark.py::test_generation_that_blocks_past_deadline_remains_timeout`,
a wall-clock deadline test (file untouched by this branch). Re-run alone:
`tests/test_model_benchmark.py` 130 passed. A load flake, not a v8 effect.
