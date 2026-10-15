# PHX-029 — Model-backed orchestration runtime

Status: DONE (APPROVED 2026-10-15; prompt text `phoenix-prompts-v1` approved by the owner 2026-10-15)
Phase: 2 — Orchestration (integration)
Branch: `claude/cranky-wescoff-842753` (uncommitted)
Base: main `fe75e95` (includes PHX-017 and PHX-091)

## Objective

Let a user request flow through real model generations:

```text
PhoenixOrchestrator -> AIZEN planner -> PHOENIX assignment -> router -> task graph
  -> specialist step -> LEVI verification -> PHOENIX-finalized verified result
```

The work adds model-backed implementations of the existing responder protocols and one
PHOENIX-owned runtime that composes them. No existing contract changes.

Permissioned tools and the `end_to_end` matrix gap belong to PHX-046.
PHX-091 configuration (2) follows on from both.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md` (especially INV-001/002/003/008/009/012/017/019/021/022/023), `/MASTER_SPEC.md`
- `docs/01`–`05`, `docs/07`, `docs/08`, `docs/22`, `docs/24`
- PHX-010 to PHX-013, PHX-017, PHX-021 to PHX-028, PHX-055
- ADR-078 (test matrix), ADR-079 (FYP suite), ADR-080 (Ollama thinking control)

## Findings that shape scope (2026-10-15)

Full analysis: `PHX-029-model-backed-orchestration-analysis.md` in the session notes folder.

1. **The complex path is not composed.** `PhoenixOrchestrator` takes a caller-supplied path. For COMPLEX it returns `PLANNING_REQUIRED` and stops. `PhoenixResult` cannot express failed or cancelled. Before this ticket, nothing in `phoenix/agents` called `ModelProvider`.
2. **Existing `phoenix/` files are byte-frozen** by the protected-source guards. Since ADR-080, `ollama.py` is pinned to `b7d9651`. New files are allowed, so **this ticket adds new modules only**.
3. **`AgentModelRouter` routes normal specialists only**, by PHX-023 design. Control roles need a selector that applies the same eligibility rules.
4. **`PlanStep` carries no domain or owner**, and AIZEN has no routing authority. Step-to-domain assignment is a PHOENIX decision.
5. **Self-handoff is forbidden**, so the final answer is a `user_interaction` step.
6. **There is no structured-output support.** JSON is parsed from text and strictly validated.
7. **Task state is task-level.** `VERIFYING → RUNNING` is not a legal edge. Intermediate step verification therefore runs while the task is RUNNING, and VERIFYING is the final-answer verification phase.
8. **Thinking control is per provider instance** (ADR-080: "a caller needing both modes holds two providers").
9. **Lifecycle capacity is 1** (INV-023). The PHX-016 `BenchmarkRunner` cannot wrap the pipeline, because it would deadlock on the single lease.

## Owner decisions (2026-10-15)

The owner chose the recommended option for all 13 questions.

| # | Question | Decision |
|---|---|---|
| Q1 | Scope split | **Three tickets:** PHX-029 (responders and runtime, text-only), PHX-046 (permissioned read-only filesystem steps and the `end_to_end` test), then a PHX-091 configuration (2) runner. |
| Q2 | Runtime location | **New `PhoenixRuntime` module** (`phoenix/agents/runtime.py`, `orchestrator_id="phoenix"`). It composes the frozen components; it is PHOENIX's runtime, not a second orchestrator. |
| Q3 | DIRECT vs COMPLEX | **The caller supplies the path.** Triage is deferred. |
| Q4 | Step domains | **A PHOENIX model picks from an allowlist.** v1: `intelligence`, `scenario_analysis`, `build_engineering`; PHX-046 adds `documents`. |
| Q5 | Model per role | **Approve the provisional table and `ControlRoleModelSelector`.** phoenix → T1 4B; strategist and verifier → T2 9B; build_engineer → T2-code 7B; other specialists → T1; T0 2B is always the last fallback. |
| Q6 | Structured output | **Parse JSON from text now.** Schema-constrained decoding is a later provider ticket. |
| Q7 | Thinking | **`think:false` for structured roles** (PHX-017 is merged). PHOENIX direct and assignment, AIZEN planning and LEVI verification use caller-supplied `structured_providers` (for example `OllamaProvider(think=False)`). Specialist and final-answer steps keep the provider default. No provider change. |
| Q8 | Prompts | **Versioned constants in `phoenix/agents/prompts.py`.** The owner approves the exact text in the PR. Any change bumps `PROMPT_SET_VERSION`. |
| Q9 | Verification | **Every step, deterministic checks first.** The model cannot override a deterministic failure, and LEVI prefers a model other than the producer's. |
| Q10 | Tools | **As proposed:** none in PHX-029; P1 read-only filesystem tools in PHX-046. |
| Q11 | Failures | **Fail explicitly; MAHORAGA and KAIDO deferred.** No hidden retries. |
| Q12 | Events | **No events; return `MissionTrace`.** |
| Q13 | Numbering | **PHX-029 and PHX-046.** |

## Requirements

1. **New modules only.** No edits to any existing `phoenix/` file, `alembic/`, `apps/desktop/` or `pyproject.toml`. No new dependencies.
   - `phoenix/agents/prompts.py`: versioned prompt constants with a role marker on the first line.
   - `phoenix/agents/responders.py`:
     - `ModelDirectResponder`, `ModelPlanningResponder`, `ModelStepAssignmentResponder`, `ModelSpecialistResponder` and `ModelVerificationResponder`;
     - `parse_json_object`;
     - `ResponderOutputError` and `GenerationRecord`.
   - `phoenix/agents/runtime.py`:
     - `PhoenixRuntime`, `ControlRoleModelSelector`, `ModelTierTable` / `default_tier_table` and `RuntimeLimits`;
     - `MissionResult` and `MissionTrace`.
2. **Provider neutrality.**
   - Responders depend only on the `ModelProvider` contract and `ModelLifecycleManager`.
   - No Ollama, httpx, tools, persistence, API or logging imports in the new modules (INV-012).
   - Thinking control is the caller's choice of provider instance.
3. **Leases and cancellation.** Every generation holds `acquire_heavy(...)`. On cancellation, `provider.cancel(operation_id)` is called (shielded, best-effort) and the cancellation re-raises.
4. **Parsing.** Steps:
   1. Reject empty output, or output with `finish_reason="length"`, as truncated.
   2. Apply the size cap.
   3. Strip at most one code fence.
   4. Require exactly one JSON object.
   5. Validate it with a strict `extra="forbid"` wire model.
   6. Revalidate it through the existing contract.

   Responders, not models, assign request, step and check ids. Raw output is never logged or persisted.
5. **Mission flow.** The COMPLEX path:
   1. The orchestrator returns `PLANNING_REQUIRED`.
   2. PLANNING:
      1. provider health snapshot;
      2. AIZEN plan (1 to 4 steps);
      3. PHOENIX assignment;
      4. routing.
   3. READY: build a linear `TaskGraph` plus a `user_interaction` final step.
   4. RUNNING, for each step:
      1. handoff (`verification_required=True`);
      2. specialist output;
      3. LEVI verification.
   5. VERIFYING: verify the final answer.
   6. COMPLETED.

   AIZEN plan objectives and earlier outputs reach later prompts only through `PromptInjectionGuard`, as `MODEL_OUTPUT`. This was the review fix the owner chose ("Guard them").

   The DIRECT path has no LEVI check and is bounded only by the mission deadline.
6. **Result contract.**
   - `MissionResult.kind` is one of `completed | failed | cancelled`.
   - `text` is present only for `completed`.
   - `failed` carries a `MissionFailureCode`: `provider_unavailable`, `provider_error`, `responder_output` (with a `ResponderErrorCode`), `prompt_boundary`, `no_eligible_model`, `verification_failed` or `timeout`.
   - The `ResponderErrorCode` values are `truncated`, `oversized`, `invalid_text`, `not_json`, `schema`, `unknown_step` and `disallowed_domain`.
   - LEVI's plain-text check applies to intermediate steps only. A final answer may be JSON when the request asks for it.
   - A `cancel()` issued through the runtime yields `cancelled`. Cancellation from outside propagates.
   - Unknown exceptions propagate as defects.
7. **Limits** (`RuntimeLimits`):
   - a 4-step plan cap, so at most 2n + 4 = 12 generations;
   - a 2048-token output budget;
   - a 20,000-character output cap;
   - mission, planning, step and verification timeouts.
8. **Tests.** `tests/test_phoenix_runtime.py` is mapped in `tests/matrix.toml` as `unit, integration, routing`.
   - The model is a scripted fake. Everything else is real.
   - One test uses a real `OllamaProvider` over an in-memory `httpx.MockTransport` to show `think:false` reaches only the structured roles. There is no network access.

## Non-goals

- Tools and permissions (PHX-046).
- MAHORAGA or KAIDO model-backed responders.
- Events.
- Persistence.
- API and WebSocket.
- Frontend.
- Memory/RAG.
- Voice.
- Triage.
- Colibri/T3.
- Provider changes.
- Parallel steps.
- Live model runs. Live smoke and PHX-091 configuration (2) runs happen later, on the owner's go-ahead, when the GPU is free.

## Follow-ups

- **PHX-046** (`tickets/PHX-046-permissioned-tool-steps.md`): read-only filesystem tool steps, `waiting_permission`, the `end_to_end` test, and removal of the `[[gap]]`.
- **PHX-091 configuration (2):**
  - Add `--config phoenix-multi-agent` to `scripts/fyp_benchmark.py`. It runs the v1 task set through `PhoenixRuntime.run(COMPLEX)` with live `OllamaProvider`s:
    - a default instance for specialists;
    - a `think=False` instance for structured roles.

    It has its own loop, not `BenchmarkRunner` (finding 9).
  - It reports success, mission latency, token totals, generations per mission, LEVI verdicts, failure codes and the prompt set version.
  - These stay NOT RUNNABLE, each with its reason: routing accuracy, verification catch rate, recovery success, memory/research/computer-control, Colibri I/O, and configuration (3).
  - Risk: exact-match cases may lose points to verbose synthesis. Report this honestly rather than tuning prompts to the test set.

## Verification

- `uv run --locked --no-sync pytest tests/test_phoenix_runtime.py` passes offline.
- `python scripts/test_matrix.py` passes with the new file mapped. The frozen-source guards pass because no existing file changes.
- ruff, ruff format and strict mypy pass.

## Acceptance criteria

- A COMPLEX request completes only after every step and the final answer pass LEVI. Any failure is `failed` with a code, or `cancelled`, never success.
- No model output can assign ids, supply a verdict, pick a domain outside the allowlist, or override a deterministic LEVI check.
- At most one heavy generation runs at a time, and cancellation reaches the provider.
- Structured roles use the structured providers (`think:false`); specialists use the defaults.
- The prompt set is versioned, and **the owner approves its text in the PR**.
- ADR-081 is recorded, and the `FEATURE_TICKETS.md` ledger is updated.

## Completion evidence

- **Prompt approval.** On 2026-10-15 the owner reviewed the `phoenix-prompts-v1` text (all six roles, including the review fixes) and answered "Approve + open PR".

- **Targeted tests.** `uv run --locked --no-sync pytest tests/test_phoenix_runtime.py`: 54 passed, offline. This includes the review fixes: guarded plan objectives, JSON final answers, the `invalid_text` code, the planning and mission deadlines, and the loud settle. Mutation checks on the key safety logic all fail as expected.
- **Matrix.** `python scripts/test_matrix.py --allow-dirty` on `fe75e95` plus this change, run 2026-10-15 13:47:04Z to 13:57:54Z: **PASS**, all 13 gates.
  - Pytest: 15,830 passed, 0 failed, 16 skipped.
  - npm: 181/181.
  - The protected-source guards pass, so no existing `phoenix/` file changed.
  - `end_to_end` remains GAP, as PHX-046 scope.
- **No live model calls.** The live smoke test is pending, on the owner's go-ahead.
