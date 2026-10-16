# PHX-047 — Grounded document verification

Status: DONE (`phoenix-prompts-v3` approved by the owner 2026-10-16, including the round-5 sentence; live acceptance 3/5 accepted, with remaining variance assigned to PHX-109)
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

### Round 3 (live acceptance on `ac37229` + `7c33b9d`)

- r1 and r3 completed with the correct answer, and every LEVI check passed.
- In r2, the 4B assignment (62 characters) misrouted the file step to `intelligence`. The text specialist (4B, thinking on) then spent the full 2048-token cap thinking and returned 0 characters, so the mission ended `responder_output/truncated`.
- r4 and r5 were interrupted by a session restart and do not count.

### Round 4 (5-run acceptance on `cdf3b9b`, GPU enabled)

- 2 of 5 runs passed. The 9B assignment routed correctly in all 5 runs, and nothing truncated.
- **Extra check keys.** LEVI (9B, `think:false`) added extra keys to check objects (for example `"completed_step_objective": true`). The strict checks wire rejected them, so missions whose checks all passed failed with `responder_output/schema`.
- **Ungrounded s2.** Two-step plans (s1 reads the file, s2 "state the number" in a text domain) failed at s2. The text `verifier` had no evidence and failed a correct `42` as unverifiable.
- **Provider drop.** One transient `provider_unavailable` stays a failure; PHX-109 owns retries.

### Round 5 (on `4d634bd`)

- **Pass rates.** Acceptance batch 1 passed 3 of 5; batch 2 passed 2 of 4 counted runs; an 8-run diagnostic passed 5 of 8.
- **Diagnostic failures (runs 3 and 7).** The `document_answer` was a correct sentence ("The magic number stated in the file is 42."). The `document_verifier` failed it for breaking the original request's "reply with only the magic number". That rule applies only to PHOENIX's final answer. Bare `42` step answers always passed.
- **Empty assignment.** An occasional `{"assignments": []}` (19 characters) appeared on the first call in a fresh process near Ollama "llm server error" model reloads, 3 times. It fails explicitly as `schema`. No change is needed here; PHX-109 owns retries.

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
| 8 | Thinking token cap (round 3) | The new `RuntimeLimits.max_thinking_output_tokens` (default 4096) applies to roles on the default (thinking) providers: `specialist` and `final_answer`. Structured roles keep `max_output_tokens` (2048). `max_output_chars` still bounds every visible answer. |
| 9 | Assignment tier (round 3) | `default_tier_table` gains an `assignment` row: qwen3.5:9b, then 4b, then 2b. The agent ID stays `phoenix`, and a table without the row falls back to `phoenix`. PHOENIX direct stays on 4b. The verifier still prefers a non-producer model. |
| 10 | Lenient check objects (round 4) | Every LEVI role (`verifier`, `step_verifier`, `document_verifier`, `final_verifier`) drops unknown keys in each check, unread. Required keys keep strict types. The top level stays strict: one object, only a non-empty `checks` list, within `max_checks`. This is implemented in `tool_steps.py`, because `responders.py` is frozen. Planner and assignment stay strict. |
| 11 | Grounded intermediate text steps (round 4) | A text step that received earlier verified outputs is verified by the new `PHOENIX ROLE: step_verifier` (`STEP_VERIFICATION_SYSTEM`, task `STEP_CANDIDATE_TASK`). It receives those outputs as guarded `MODEL_OUTPUT` items. A text step with no earlier outputs keeps the byte-identical `VERIFICATION_SYSTEM`. The acceptance bar stays at 4 of 5 runs. |
| 12 | Step-scoped format (round 5, approved by the owner 2026-10-16) | `DOCUMENT_VERIFICATION_SYSTEM` and `STEP_VERIFICATION_SYSTEM` gain one sentence before the reply-format paragraph: "The original request's output format (for example "reply with only ...") applies to PHOENIX's final answer, not to this step; do not fail this step for wording or format beyond its expected output and completion rule." `FINAL_VERIFICATION_SYSTEM`, `VERIFICATION_SYSTEM` and every other constant are unchanged. |

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
  - **Lenient checks (round 4).** For `verifier`, `step_verifier` and `final_verifier`:
    - extra check keys are dropped and never reach the trace;
    - extras cannot pass a failed check;
    - a missing key, a wrong type, a blank summary, a top-level `verdict`, zero or seven checks, a non-list `checks`, or a bare list fails as `schema`/`not_json`.
  - **Step verifier (round 4).** s1 (no earlier outputs) uses the unchanged `VERIFICATION_SYSTEM`, with only objective and candidate. s2 uses `step_verifier`, with `objective`, `s1` and `candidate`, all `model_output`.
  - **Step-scoped format (round 5).**
    - The sentence is in the document and step verifier prompts, before the reply-format paragraph.
    - It is absent from the final and text verifier prompts.
    - The unchanged v3 additions are hash-pinned.
  - **Tier table (round 3).** `assignment` is 9b, then 4b, then 2b; `phoenix` stays 4b, then 2b. A table without an `assignment` row uses the `phoenix` row, and the direct path stays on 4b.
  - **Token caps (round 3).** `specialist` and `final_answer` requests carry 4096. `direct`, `planner`, `assignment`, `verifier` and `final_verifier` carry 2048. An oversized specialist answer is still `oversized`.
- `tests/test_phoenix_mission_e2e.py` (round 4): the s2 `step_verifier` gets s1 exactly as s2 saw it, and never the tool output. `document_verifier` drops unknown check keys.
- `tests/test_phoenix_mission_e2e.py` (round 3): all five document and LEVI roles carry 2048, `specialist` and `final_answer` carry 4096, and assignment runs on 9b.
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
- Round 2: implementation `73a4f007fab4cf65d4e504e5503fbbec588bdf80`, pin `ac37229`.
- Round 3: implementation `e47834a3e82d8f10dfde8479c89cea20aa5d369a`, pin `cdf3b9b`.
- Round 4: implementation `327f4e4abc746b5d0b6d4d9faf8066b3360689a6`, pin `4d634bd`.
- Round 5 (owner-approved 2026-10-16): implementation `47aa471712c6c5ea3452cf5a45c3ed390470e185`, which `PHX_047_COMMIT` now pins (followed by its pin commit).

**Owner approval (2026-10-16).** The owner approved the `phoenix-prompts-v3` text, including the round-5 sentence, and accepted PHX-047 at 3 of 5 live passes. The remaining variance goes to PHX-109 (MAHORAGA bounded retries), where it will be measured again.

**Live acceptance** (`scripts/phoenix_mission_smoke.py`, BLAZE-6). Conditions: AC power, RTX 3050 on CUDA, Ollama 0.40.4 desktop app, 16K context. Evidence is under `validation/PHX-047-acceptance-*`. Rounds 3 to 5 used the owner's 4-of-5 bar.

| Round | Result | Notes |
| --- | --- | --- |
| 3 | 2/5 | Assignment misroutes, a step truncated by thinking, ungrounded final LEVI. |
| 4 | 3/5 and 2/4 | Extra keys and two-step grounding fixed. The intermediate verifiers enforced the user's "reply with only" format, and three empty `{"assignments": []}` outputs appeared near Ollama `llm server error` model reloads. |
| 5 | 3/5 | Run r2: planner JSON failed the schema. Run r5: the document verifier failed a correct sentence. |

**Not affected by these failures:**
- **Explicit failures:** every failure ended as an explicit `failed` with a coded reason.
- **Correct answers:** every completed mission returned the correct answer, verified by grounded LEVI checks. No wrong answer was ever reported as verified (INV-008).

**8-run diagnostic** (raw outputs printed locally, harmless smoke text only):
- Bare "42" step answers always passed.
- Sentence answers failed about half the time, until the round-5 sentence.

**Follow-ups:**
- PHX-109: bounded retries per failure class.
- Schema-constrained decoding (Ollama `format`): a later provider ticket.
