# PHX-121 — Combined planner

## Status
IN_PROGRESS (implemented and tested; the v6 prompt text awaits owner approval)

## Phase
M3 capabilities (follow-up to PHX-120 and the 2026-10-21 release train)

## Objective
One AIZEN planner prompt that can plan any mix of the enabled capability
families (documents read/create, knowledge_rag, web_research, media,
computer_control, plus text), used whenever more than one family is enabled.
Before this, the planner was picked by precedence (ADR-160/ADR-165), so with
capability tools always wired AIZEN never planned computer-control steps, and
with web research on it never planned document writes or media.

## Authoritative documents
- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `DECISIONS.md` ADR-160, ADR-165, ADR-166

## Dependencies
PHX-110, PHX-111, PHX-112, PHX-113, PHX-120 (all merged on main 68655f7).

## Requirements
1. New prompt text only as new constants under `phoenix-prompts-v6`; every v5
   constant byte-identical.
2. The domain list is built from the enabled capabilities; nothing about
   disabled ones.
3. Keep the v5 planners' safety rules: no invented tool names, one action per
   step, irreversible (P4) actions only when the user explicitly asked, never
   plan to approve permissions.
4. Combined planner when two or more families are enabled; per-family planners
   unchanged otherwise (ADR-166).

## Non-goals
No assignment, specialist, verifier, grant or permission change. No server
wiring change. No change to the control-v1 prompt set.

## Expected files/components
- `phoenix/agents/prompts.py` — v6: `COMBINED_PLANNING_SYSTEM`,
  `COMBINED_PLANNING_LINES`.
- `phoenix/agents/combined_planning.py` — family rule, line builder,
  `CombinedPlanningResponder`.
- `phoenix/agents/runtime.py` — `_planning_responder` per mission.
- Tests in `tests/test_phoenix_runtime.py`, `tests/test_media_document_steps.py`,
  `tests/test_computer_control_steps.py`, `tests/test_web_research.py`;
  pins in `tests/test_prompt_injection_defenses.py`.

## Verification
- Planner choice per configuration (one family keeps its planner; two or more
  get the combined planner with exactly the enabled lines).
- Each line appears only when its capability is enabled.
- Scripted model: with control + capabilities on, a browser step is planned,
  assigned and run; control alone keeps `CONTROL_PLANNING_SYSTEM`.
- Hash pins for the v6 constants; every v5 hash unchanged.
- Full matrix `uv run python scripts/test_matrix.py --allow-dirty`.

## Acceptance criteria
- [x] Combined planner chosen with two or more families (ADR-166).
- [x] Lines only for enabled capabilities.
- [x] Control steps planned when control and capabilities are both on.
- [x] v5 constants byte-identical; v6 constants hash-pinned.
- [ ] Owner approves the exact v6 text below.

## Owner approval needed

`PROMPT_SET_VERSION = "phoenix-prompts-v6"`. v6 adds exactly these two
constants (hashes are the first 128 bits of sha256, pinned in
`tests/test_phoenix_runtime.py`).

### `COMBINED_PLANNING_SYSTEM` — `5c8fdae04360164d035ef8b7e72beac6`

`{max_steps}` is the plan limit; `{capabilities}` is replaced by the enabled
lines below, one per line.

```text
PHOENIX ROLE: planner
You are AIZEN, the planning strategist. Break the user's request into between 1
and {max_steps} ordered steps that together produce the answer. Each step is one
short, self-contained objective that does exactly one of these:
- reasoning and writing text alone
{capabilities}
Plan only the kinds of step listed above, and do not name tools, services or
operations that are not listed. Plan an action that cannot be undone only when
the user explicitly asked for it. Never plan a step that grants or approves a
permission. Do not include changing, replacing or deleting files or a final
"answer the user" step; PHOENIX adds the final answer step itself. PHOENIX and
the user decide whether each action may run.
Reply with exactly one JSON object and nothing else, in this form:
{{"steps": [{{"objective": "..."}}]}}
```

(The doubled braces render as single braces.)

### `COMBINED_PLANNING_LINES` (in this order; each used only when enabled)

| key | enabled when | text | sha256/128 |
|---|---|---|---|
| `documents` | filesystem tool (workspace) | `- reading one named file, listing one directory or inspecting one file's metadata in the user's workspace` | `b159c166d68e8c86d231c786376f705c` |
| `read_document` | PHX-113 document tool | `- reading the text of one PDF, DOCX, Markdown or CSV document in the user's workspace` | `0110241e410d87db18aa605236a015c9` |
| `create_document` | PHX-113 document tool | `- creating one new Markdown, DOCX or CSV file in the user's workspace` | `2ce3fc72e798c71aca8f75e6b568228b` |
| `knowledge_rag` | knowledge source with something indexed | `- looking up facts in the user's uploaded documents and saved memories` | `5f645909649a4a64356066dae5cd7f1e` |
| `web_research` | web research tool | `- searching the web for one topic and reading the top results, when the request needs current, online or source-backed information` | `cdd7c1802c22785bb4df818e262334c8` |
| `spotify` | Spotify tool | `- one Spotify search, play or pause request` | `e384b370ef41d674a4abd98499b5697e` |
| `youtube` | YouTube tool | `- one YouTube search, open, play or pause request` | `ff6a96e2cea42bd82eafe484ee381373` |
| `browser` | control runner with the browser | `- one short task in PHOENIX's own isolated web browser` | `a7085594a2bbdfeddd9c0d6a8ef35de2` |
| `windows` | control runner with Windows UI | `- inspecting or operating one open desktop application window` | `61a545bd720b3d3d893c9d3442ce8185` |
| `screen` | control runner with screenshots | `- describing one open window's screenshot` | `bf6c44ede2c2369fed11be1d4a5ac6e9` |
| `terminal` | control runner with terminal profiles | `- running one configured terminal command` | `e5f8bddd75eb6c206eb70d745f8ab918` |

Example: the desktop backend with a workspace (filesystem, documents, YouTube,
browser, Windows UI; no web research, nothing indexed) fills `{capabilities}`
with the `documents`, `read_document`, `create_document`, `youtube`, `browser`
and `windows` lines.

## Completion evidence
See the PHX-121 commits on `feature/PHX-121-combined-planner` and ADR-166.
