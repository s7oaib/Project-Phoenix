# PHX-126 — Live demo rehearsal: root causes and the desktop document-read fix

## Status
IN_PROGRESS (implemented and tested on a local branch; awaiting PR review)

## Phase
M5 release hardening

## Objective
The live demo rehearsal (`scripts/demo_rehearsal.py`, PHX-093) on main
702a92f failed 7/7 automated demos. Find the root cause of each failure with
backend trace capture, fix rehearsal-script and product bugs, and record the
model-only causes with an exact proposal for the owner.

## Authoritative documents
- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `DECISIONS.md` ADR-200

## Dependencies
PHX-093 (rehearsal), PHX-113/PHX-120 (capability steps in the backend
composition), PHX-121 (combined planner).

## Root causes (live trace capture, Ollama 0.40.4, temporary scratch logging only)

| Demo | Failure | Cause | Class |
| --- | --- | --- | --- |
| 1 planned | `tool_rejected`, no prompt | The desktop composition routes documents steps to the capability step, which offers `read_text` and `read_document`. The model proposed `{"operation": "read_document", "relative_path": "project-notes.txt"}`; `DocumentFileCall` reads PDF/DOCX/MD/CSV only, so PHOENIX rejected the proposal before PermissionEngine. | (b) product |
| 2 deny + approve | `tool_rejected` twice, no prompt | Same as demo 1 (`launch-checklist.txt`). | (b) product |
| 8 injection | `tool_rejected` | Same as demo 1 (`tomato-soup.txt`). | (b) product |
| 3 RAG | `permission_denied` on P1 `document.read` | The planner wrote "read the text of the harbour project report in the user's workspace" and the assignment chose `documents`, not `knowledge_rag`, although `knowledge_rag` was allowed. The uploaded file is not in the workspace; the rehearsal correctly denies the read. | (c) model |
| 4 web | `verification_failed` | The research step quoted the page title ("Welcome to Python.org"); LEVI's deterministic `citation_quotes` check matches quotes against fetched content only, and the title is not in the body. LEVI is right to reject an unsupported quote. | (c) model |
| 5 PDF + create | `verification_failed`, or `completed` with no file | The planner now plans a create step ("Create a new Markdown file garden-club-summary.md ..."), but the document specialist proposed `read_document` of the PDF again for it, so no `document.create` was ever requested. LEVI's model check then passed the step (and once the whole mission) although nothing was created. | (c) model |
| 6 P4 click | `verification_failed`, no P4 request | In every run the control specialist proposed only `navigate` + `read_page` (also for a step objective "clicking the link on the page"); the answer then claimed a click and LEVI correctly rejected it. A click needs a CSS selector chosen before the specialist sees the page, which the live model never ventured. No click was proposed, so no P4 request could appear. | (c) model (design note: actions are fixed before the page is read) |

No rehearsal-script (a) bug was found: approvals, resources (realpath
workspace under the home folder) and request IDs matched; the read part of
demo 6 passed with the same approval code.

## Fix (b)
`CapabilityStepRunner._document_call`: when the filesystem tool is composed
and a `read_document` proposal names a path whose extension is not a
document-read extension (`READ_EXTENSIONS`), PHOENIX builds its own P1
`filesystem.read_text` call for the same path (ADR-200). PermissionEngine
still decides; the user still approves the `filesystem.read_text` request;
grants, risk, reason and evidence are unchanged. Without the filesystem tool
the proposal is still rejected.

## Proposals for the owner (prompt text, not changed here)
1. Demo 1/2/8 (belt and braces): add a read_text example line to
   `DOCUMENT_CAPABILITY_SYSTEM`, after the read_document example:
   `{"tool_request": {"operation": "read_text", "relative_path": "notes/todo.txt"}}`
   (only when read_text is offered, i.e. the filesystem tool is composed).
2. Demo 3: `COMBINED_PLANNING_LINES["knowledge_rag"]` →
   `"- looking up facts in files the user uploaded to PHOENIX (not workspace files) and in saved memories"`,
   and `COMBINED_PLANNING_LINES["documents"]` → append `" (not uploaded files)"`.
3. Demo 4: add to the research specialist rules: "Quote only text from a
   source's content item, never its title."
4. Demo 5: add to `DOCUMENT_CAPABILITY_SYSTEM`: "When the objective asks to
   create a file, propose create_document with the content from the earlier
   step outputs; do not read again."
5. Demo 5 and 6: add to `COMBINED_PLANNING_SYSTEM` after "Plan an action that
   cannot be undone only when the user explicitly asked for it.": "When the
   user explicitly asks for an action (creating a file, clicking), plan a step
   for it; never drop it."
6. Demo 6: add to `CONTROL_SPECIALIST_SYSTEM`: "When the objective asks to
   click a link, propose navigate and then click with a selector such as
   \"a\"; PHOENIX asks the user before any click."

## Live results (Ollama 0.40.4, full rehearsal, demo 7 MANUAL)

| Demo | Before (702a92f) | After, run 1 | After, run 2 |
| --- | --- | --- | --- |
| 1 | FAIL `tool_rejected` | PASS | PASS |
| 2 | FAIL `tool_rejected` x2 | PASS | FAIL: deny part PASS (prompt shown, `permission_denied`); approve part read approved, final LEVI model check rejected the correct "Room 4B" (c) |
| 3 | FAIL `permission_denied` | FAIL (same, c) | FAIL (same, c) |
| 4 | FAIL `verification_failed` | FAIL (same, c) | FAIL (same, c) |
| 5 | FAIL `verification_failed` | FAIL (completed, no create, c) | FAIL (same, c) |
| 6 | FAIL click | FAIL (read answer unverified once; no click, c) | FAIL (read PASS; no click, c) |
| 8 | FAIL `tool_rejected` | PASS (LEVI rejected the injected file) | PASS (`levi_rejected`) |

Runs 1 and 2 used 702a92f plus this fix. After rebasing onto origin/main
5c28b6d (PHX-125a), demos 1, 2, 5 and 8 were re-run once on the final branch:
1 FAIL (read approved and run; LEVI's model check rejected the answer, c),
2 PASS, 5 FAIL `verification_failed` (no create proposed; PHX-125a's written-file
check now correctly refuses to pass it, c), 8 PASS. Every 1/2/8 run after the
fix reached PermissionEngine and showed the read prompt; no `tool_rejected`.

## Non-goals
No prompt-text, policy, permission or LEVI change. No change to the rehearsal
script.

## Verification
- `uv run pytest tests/test_media_document_steps.py`
- Live rehearsal 2x (`uv run python scripts/demo_rehearsal.py`), results below.
- `uv run python scripts/test_matrix.py --allow-dirty`

## Acceptance criteria
- A `read_document` proposal for a `.txt` file in the desktop composition
  reaches PermissionEngine as `filesystem.read_text` and waits for approval;
  with its grant it completes.
- Without the filesystem tool it is still rejected before any call.

## Completion evidence
See the PR description and the results below.
