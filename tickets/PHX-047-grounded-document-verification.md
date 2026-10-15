# PHX-047 — Grounded document verification

Status: IN_PROGRESS (the exact `phoenix-prompts-v3` text awaits owner approval in the PR)
Phase: 4 — Tools, Permissions & Security (integration)
Branch: `feature/PHX-047-grounded-document-verification` (local commits only)
Base: main `47bb91e` (includes PHX-046)

## Objective

Let LEVI's model verifier judge a document step against the file the step read,
so a faithful file-derived answer can pass verification. Run the
`document_specialist` proposal turn with thinking off.

## Live-evidence finding (2026-10-16)

Source: `validation/PHX-046-mission-smoke-20261016-003757`.

- The first live mission did the following:
  - planned with qwen3.5:9b;
  - assigned `documents` (qwen3.5:4b);
  - proposed `read_text smoke.txt`, which PermissionEngine ALLOWED through the caller SESSION grant;
  - read the file and answered with 2 characters (the expected `42`).
- LEVI's deterministic `tool_evidence` check passed. Then LEVI's model verifier (qwen3.5:9b, `think:false`) failed the step, and the mission ended `verification_failed`.
- Cause: the known ADR-082 limitation. LEVI's model never saw the tool output, and `VERIFICATION_SYSTEM` says to fail a check when unsure, so it could not confirm a file-derived fact.
- Also, the `document_specialist` used the default provider with thinking on. It spent about 34 s and 1,240 output tokens on a 74-character JSON tool request.

## Authoritative documents

- `/AGENTS.md`, `/INVARIANTS.md` (INV-008, INV-017, INV-021)
- ADR-081, ADR-082, PHX-046

## Owner decisions (2026-10-16)

Recorded in ADR-083.

| # | Topic | Decision |
|---|---|---|
| 1 | Grounded LEVI | For document steps only, LEVI's model receives the step's tool output as a guarded, untrusted `TOOL_OUTPUT` item (the same bounded rendering and content the `document_answer` turn got), alongside the objective and candidate items. Text steps and the final answer are unchanged. The deterministic checks (`tool_evidence`, `plain_text`, `size`) still run first, and the model is skipped if any fails. The verdict stays `all(checks)`. Tool output never enters `MissionTrace`, errors or logs. |
| 2 | Prompts | `phoenix-prompts-v3`: a new role `PHOENIX ROLE: document_verifier` (`DOCUMENT_VERIFICATION_SYSTEM`) plus its guard task text `DOCUMENT_CANDIDATE_TASK`. The text `VERIFICATION_SYSTEM` and every other v2 constant stay byte-identical. The owner approves the exact text in the PR. |
| 3 | Thinking | The `document_specialist` proposal turn uses the structured (`think:false`) providers. `document_answer` and the text specialists keep the default providers. |
| 4 | Model selection | The document verifier follows the existing verifier rule: verifier tier, preferring a model other than the producer's. |

## Implementation

- `phoenix/agents/tool_steps.py`:
  - new `DocumentVerificationResponder` (a `ModelVerificationResponder` subclass);
  - shared `_tool_output_item`;
  - `DocumentStepRunner.perform(..., proposal_binding=...)`.
- `phoenix/agents/runtime.py`: passes a structured `proposal_binding`, and wraps `DocumentVerificationResponder` in `ToolEvidenceVerificationResponder` for document steps.
- `phoenix/agents/prompts.py`: v3 additions only.
- `phoenix/agents/responders.py` is byte-identical to `origin/main`.
- Re-pin, as in PHX-046: the implementation lands in commit 1, and commit 2 pins `prompts.py`, `runtime.py` and `tool_steps.py` to commit 1 (`PHX_047_COMMIT` / `PHX_047_PATHS`). This replaces the PHX-046 pin for the first two files and the `origin/main` baseline for `tool_steps.py`, so each path has exactly one baseline. The PR must merge with a merge commit.

## Tests

All tests are offline and use the scripted provider. There are no new test files, so `tests/matrix.toml` is unchanged.

- `tests/test_phoenix_mission_e2e.py`:
  - **LEVI input.** On a document step, LEVI gets exactly `objective`, `tool_output` and `candidate`. The `tool_output` item is identical to the answer turn's (origin `tool_output`, source = call ID). Text-step and final-answer LEVI prompts have no tool output and no file content.
  - **Verifier model.** The verifier is qwen3.5:9b, not the 4b producer.
  - **Grounded verifier.** A grounded scripted verifier passes an answer the file supports (COMPLETED). It fails a contradicting answer (`verification_failed`, no final answer).
  - **Injected file content.** Injected instructions in the file reach LEVI only inside the guarded item, flagged high severity. Each LEVI reply has a fixed outcome:
    - a pass completes the mission;
    - a fail check fails it;
    - a `tool_request` or an added `verdict` field is `responder_output`.

    In every case only the one PHOENIX-issued read runs.
  - **Deterministic failure.** When a deterministic check fails, no `document_verifier` generation runs.
  - **Providers.** `document_specialist` and `document_verifier` go to the structured provider; `document_answer`, `specialist` and `final_answer` go to the default provider.
  - **Trace.** The trace never contains file content, even when a model check summary quotes the file.
  - **Existing tests.** Updated for the new `document_verifier` role.
- `tests/test_phoenix_runtime.py`:
  - the version is `phoenix-prompts-v3`, and role markers are unique;
  - every v2 constant (including `VERIFICATION_SYSTEM`) is hash-pinned as unchanged, and v3 adds only the two new constants.
- `tests/test_prompt_injection_defenses.py`: the PHX-047 pin (commit 2).

## Non-goals

- Changing the text-step or final-answer verification.
- New tools.
- Retries.
- MAHORAGA/KAIDO.
- Events.
- Persistence.
- API/frontend.
- Running a live model: the main session runs the live smoke afterwards.

## Completion evidence

Recorded in the PR and the final report: changed files, test and gate results,
commit SHAs. The prompt text awaits owner approval. The live smoke run
(`scripts/phoenix_mission_smoke.py`, unchanged: it already reports the prompt set
version from the trace) is pending.
