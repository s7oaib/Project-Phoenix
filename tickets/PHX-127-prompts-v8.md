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

## Live results
See below.
