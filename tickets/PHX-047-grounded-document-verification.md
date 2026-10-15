# PHX-047 — Grounded document verification

Status: IN_PROGRESS (the exact `phoenix-prompts-v3` text awaits owner approval in the PR)
Phase: 4 — Tools, Permissions & Security (integration)
Branch: `feature/PHX-047-grounded-document-verification` (local commits only)
Base: main `47bb91e` (includes PHX-046)

## Objective

Make a live document mission complete reliably:
- LEVI's model verifier judges a document step against the file the step read;
- the final answer is verified against the verified step outputs;
- both document turns run with thinking off;
- the assignment routes file steps to `documents`.

## Live-evidence findings (2026-10-16)

### Round 1 (`validation/PHX-046-mission-smoke-20261016-003757`, on PHX-046)

- The first live mission did the following:
  - planned with qwen3.5:9b;
  - assigned `documents` (qwen3.5:4b);
  - proposed `read_text smoke.txt`, which PermissionEngine ALLOWED through the caller SESSION grant;
  - read the file and answered with 2 characters (the expected `42`).
- LEVI's deterministic `tool_evidence` check passed. Then LEVI's model verifier (qwen3.5:9b, `think:false`) failed the step, and the mission ended `verification_failed`.
- Cause: the known ADR-082 limitation. LEVI's model never saw the tool output, and `VERIFICATION_SYSTEM` says to fail a check when unsure, so it could not confirm a file-derived fact.
- Also, the `document_specialist` used the default provider with thinking on. It spent about 34 s and 1,240 output tokens on a 74-character JSON tool request.

### Round 2 (10 live missions on `ea81364`)

- Two missions completed with the correct `42`. The grounded `document_verifier` passed 4 of 4 times it was reached.
- The other failures had three causes:
  - **(a) `document_answer` truncated.** It ran on 4B with default thinking. It sometimes spent the whole 2048-token cap thinking and returned 0 characters with `finish_reason=length`, which became `responder_output/truncated`.
  - **(b) The final-answer LEVI failed correct answers.** The text `verifier`, checking the `final_answer` step, failed a correct `42` in 2 of 4 runs. It never saw the verified step outputs, even though its completion rule says "consistent with the verified step outputs".
  - **(c) Assignment misrouted.** The 4B model assigned the step "Read the content of the file smoke.txt to locate and extract the magic number." to `intelligence` instead of `documents`. Code-fenced and compact JSON both parsed fine.

## Authoritative documents

- `/AGENTS.md`, `/INVARIANTS.md` (INV-008, INV-017, INV-021)
- ADR-081, ADR-082, PHX-046

## Owner decisions (2026-10-16)

Recorded in ADR-083.

| # | Topic | Decision |
|---|---|---|
| 1 | Grounded document LEVI | For document steps, LEVI's model receives the step's tool output as a guarded, untrusted `TOOL_OUTPUT` item (the same bounded rendering and content the `document_answer` turn got), alongside the objective and candidate items. The deterministic checks (`tool_evidence`, `plain_text`, `size`) still run first, and the model is skipped if any fails. The verdict stays `all(checks)`. Tool output never enters `MissionTrace`, errors or logs. |
| 2 | Prompts | `phoenix-prompts-v3`: a new role `PHOENIX ROLE: document_verifier` (`DOCUMENT_VERIFICATION_SYSTEM`) plus its guard task text `DOCUMENT_CANDIDATE_TASK`. The text `VERIFICATION_SYSTEM` stays byte-identical. The owner approves the exact text in the PR. |
| 3 | Thinking (round 1) | The `document_specialist` proposal turn uses the structured (`think:false`) providers. |
| 4 | Model selection | The document and final verifiers follow the existing verifier rule: verifier tier, preferring a model other than the producer's. |
| 5 | Thinking (round 2) | `document_answer` also uses the structured providers. Text specialists and `final_answer` keep the default providers. |
| 6 | Grounded final LEVI | When LEVI verifies the `final_answer` step, its model receives each verified step output as a guarded, untrusted `MODEL_OUTPUT` item, plus the objective and candidate items. These are the same step outputs the `final_answer` turn received. A new role, `PHOENIX ROLE: final_verifier` (`FINAL_VERIFICATION_SYSTEM`, task `FINAL_CANDIDATE_TASK`), judges the candidate against the original request's format and the step outputs. It treats all items as untrusted, and fails a check when the candidate is unsupported, breaks a required format, or LEVI is unsure. The size check runs first; the verdict is still `all(checks)`. Nothing new reaches the trace. Intermediate text steps keep `VERIFICATION_SYSTEM` without step outputs. |
| 7 | Routing rule | `ASSIGNMENT_SYSTEM` gains one rule: "If "documents" is one of the allowed domains, assign every step that reads a file, lists a directory or inspects a file's metadata to "documents"." `responders.py` is frozen and formats this prompt only with the domain list, so the rule cannot be inserted conditionally. It is worded to apply only when `documents` is offered, so a text-only runtime is unaffected. The rest of the prompt is unchanged. v3 is unreleased, so changing this v2 constant under v3 is allowed. |

## Implementation

- `phoenix/agents/tool_steps.py`:
  - `DocumentVerificationResponder` and `FinalVerificationResponder`, both `ModelVerificationResponder` subclasses;
  - shared `_tool_output_item` and `_model_checks`;
  - `DocumentStepRunner.perform` uses the binding the runtime passes for both turns.
- `phoenix/agents/runtime.py`:
  - document steps get a structured binding;
  - the final step is verified by `FinalVerificationResponder`;
  - document steps are verified by `DocumentVerificationResponder`, wrapped in `ToolEvidenceVerificationResponder`.
- `phoenix/agents/prompts.py`: the v3 additions, plus the `ASSIGNMENT_SYSTEM` rule.
- `phoenix/agents/responders.py` is byte-identical to `origin/main`.
- Re-pin, as in PHX-046: each implementation commit is followed by a pin commit that pins `prompts.py`, `runtime.py` and `tool_steps.py` to it (`PHX_047_COMMIT` / `PHX_047_PATHS`). The pin replaces the PHX-046 pin for the first two files and the `origin/main` baseline for `tool_steps.py`, so each path has exactly one baseline. The PR must merge with a merge commit.

## Tests

All tests are offline and use the scripted provider. There are no new test files, so `tests/matrix.toml` is unchanged.

- `tests/test_phoenix_mission_e2e.py`:
  - **Document LEVI.** It gets exactly `objective`, `tool_output` and `candidate`. The `tool_output` item is identical to the answer turn's (origin `tool_output`, source = call ID).
  - **Intermediate text LEVI.** It gets only `objective` and `candidate`.
  - **Final LEVI.** It gets `objective`, `s1`, `s2` and `candidate`, all `model_output`. The step items are identical to the final-answer turn's, and there is no tool output.
  - **Grounded document verifier.** A grounded scripted verifier passes an answer the file supports, and fails a contradicting one (`verification_failed`, no final answer).
  - **Grounded final verifier.** A grounded scripted final verifier passes a supported final answer, and fails an unsupported one from `verifying` (`verification_failed`).
  - **Injected file content.** Injected instructions in the file reach LEVI only inside the guarded item, flagged high severity. Each LEVI reply has a fixed outcome:
    - a pass completes the mission;
    - a fail check fails it;
    - a `tool_request` or an added `verdict` field is `responder_output`.

    In every case only the one PHOENIX-issued read runs.
  - **Deterministic failure.** When a deterministic check fails, no `document_verifier` generation runs.
  - **Providers.** `document_specialist`, `document_answer`, `document_verifier`, `verifier` and `final_verifier` go to the structured provider; `specialist` and `final_answer` go to the default provider.
  - **Assignment rule.** The rule is in the assignment prompt next to the offered `documents` domain. A text-only runtime is not offered `documents`.
  - **Trace.** The trace never contains file content, step outputs, or model check summaries.
- `tests/test_phoenix_runtime.py`:
  - **Version.** It is `phoenix-prompts-v3`, and role markers are unique.
  - **v2 hash pins.** Every v2 constant is hash-pinned. Only `ASSIGNMENT_SYSTEM` differs, and only by the inserted rule (removing the rule restores the v2 hash). v3 adds exactly four new constants.
  - **Final verification.** The final step is verified by `final_verifier`.
- `tests/test_prompt_injection_defenses.py`: the PHX-047 pin (see Implementation).

## Non-goals

- New tools.
- Retries.
- MAHORAGA/KAIDO.
- Events.
- Persistence.
- API/frontend.
- Running a live model: the main session runs the live smoke afterwards.

## Completion evidence

Recorded in the PR and the final report: changed files, test and gate results,
commit SHAs.
- Implementation and pin, round 1: `f600f122b2715260384db47b63bd7c07172919b1` and `ea81364`.
- Round 2: see the pin entry above.

The prompt text awaits owner approval. The live smoke run
(`scripts/phoenix_mission_smoke.py`, unchanged: it already reports the prompt set
version from the trace) is pending.
