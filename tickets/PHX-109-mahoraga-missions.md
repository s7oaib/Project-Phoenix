# PHX-109 — MAHORAGA bounded recovery in missions

Status: IN_PROGRESS (implemented with the recommended defaults; R1–R16 await the owner's review)
Phase: 10 — Product Integration (milestone M3, capabilities)
Branch: `feature/PHX-109-mahoraga-recovery` (local commits only; no push, no PR)
Base: `origin/main` `1b51f26` (PHX-105, PR #79)

## Objective

Raise mission success without risking false success. A mission retries a model generation that failed transiently (the provider dropped) or structurally (output not JSON, failing its schema, or truncated), only with MAHORAGA's approval and within its finite budget. An AIZEN plan that still fails its schema falls back, observably, to PHOENIX's DIRECT path. LEVI's rejection is never retried.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/INVARIANTS.md` (INV-008, INV-009, INV-019, INV-021, INV-022, INV-023), `/MASTER_SPEC.md`
- `docs/22_RECOVERY_FAILSAFE.md`
- ADR-081 ("MAHORAGA and KAIDO stay deferred"), ADR-083, ADR-087, ADR-090; PHX-027, PHX-028, PHX-029, PHX-103, PHX-105

## Motivating evidence (FYP configuration 2, 2026-10-18)

| Run | Failures |
| --- | --- |
| Forced COMPLEX, 16 missions | 10 failed: 4 `responder_output/schema` (the planner or the assignment emitted 6 to 19 tokens, for example an empty plan on a trivial task), 3 `truncated` (specialist thinking hit the 4,096-token cap), 1 `not_json`, 2 `verification_failed` |
| Triage, 16 missions | 5 failed: 2 `provider_unavailable` (Ollama dropped mid-mission), 2 `verification_failed`, 1 `not_json` |

Completed missions were always correct, so bounded retries of transient and structural failures should raise success without risking false success.

## Owner instruction (2026-10-18)

The owner said "do which is recommended". The recommended defaults below are implemented, and each R item is **applied under the owner's overnight instruction (2026-10-18)**, pending the owner's review.

## Recommendations (R1–R16)

Each item below is applied under the owner's overnight instruction (2026-10-18).

- **R1 — Placement.** A new module, `phoenix/agents/mission_recovery.py`, holds the classification, the record type, MAHORAGA's deterministic policy responder and the subject graph. `runtime.py` wraps each mission generation in one bounded recovery helper. `responders.py`, `recovery.py` and `failsafe.py` are byte-identical.
- **R2 — MAHORAGA decides, with no model.** Every recovery decision goes through the existing `MahoragaRecovery.recover()` (PHX-027). Its responder is `PolicyResponder`, PHOENIX's fixed policy, so no generation is spent on recovery. The mission budget is MAHORAGA's own: `attempts_used` is the number of actions already taken in the mission, and `max_attempts` is `RuntimeLimits.max_recovery_actions`. MAHORAGA correlates a failure with a graph step and its handoff. Generations outside the mission graph (direct, planner, assignment) and LEVI's own generations have no such step, so each recovery describes the failed generation as a one-step evidence graph with fixed text, owned by the failed agent and handed to it by MAHORAGA (`adaptive_recovery`). It is evidence only, never a mission graph or a grant.
- **R3 — Retryable classes.** `responder_output` with `not_json`, `schema` or `truncated`, plus `provider_unavailable` and `provider_error`. Each is retried at most once per generation. `oversized`, `invalid_text`, `unknown_step` and `disallowed_domain` are not transient and are never retried.
- **R4 — Truncated thinking.** A truncated specialist or final answer (the thinking roles) retries on the structured think:false provider (`adjust_parameters`, recorded as an observable fallback) when one is configured for that provider ID. The retry keeps the thinking roles' 4,096-token cap, all of which can now go to the answer. Without a structured provider, it retries the same provider (`retry_same`). A truncated structured role always retries the same provider.
- **R5 — Failed plan falls back to DIRECT.** After its one retry, a planner `schema` failure falls back to the DIRECT path (MAHORAGA's `switch_agent`, replacement `phoenix`). The runtime sees only the closed code, and `responders.py` stays byte-identical, so an empty plan cannot be told apart from other plan schema failures (too many steps, an extra key, a blank objective). The fallback therefore applies to every planner `schema` failure. The fallback is observable and never counts as COMPLEX success:
  - the COMPLEX task ends `FAILED`, with the plan's codes on its `task.state_changed` event;
  - `MissionTrace.path` stays the decided `complex`;
  - the answer is PHOENIX's DIRECT output, which is unverified, as for any DIRECT answer (ADR-081);
  - a trace validator rejects a fallback whose task `COMPLETED`.

  If the DIRECT answer also fails, the mission fails with the plan's `schema` code. A `not_json` or `truncated` plan does not fall back.
- **R6 — Provider failures.** The retry waits `RuntimeLimits.recovery_backoff_seconds` (2 s by default, at most 30 s), then re-checks that provider's health on its default instance. If the provider is not `HEALTHY`, no retry is made: the outcome is `unavailable` and the mission fails with the original code. A provider that is unhealthy when the mission starts fails at the health probe, as before, and is never retried.
- **R7 — KAIDO deferred.** Wiring `KaidoFailSafe` (a T0, 8K degraded answer) needs a `DEGRADED` task outcome, a degraded answer path, `phoenix.degraded` events and API exposure, which is not small. A provider that stays down therefore fails the mission explicitly. KAIDO stays a later PHX-109 or PHX-115 step.
- **R8 — LEVI's rejection stands.** `verification_failed` is never retried (INV-008). Generating a new candidate after LEVI rejected one is a product decision for the owner. Only LEVI's own failed generation (not JSON, schema or truncated output, or a provider failure) is retried, and then LEVI still has to produce a passing verdict.
- **R9 — Never retried.** `timeout`, cancellation, `waiting_permission`, `permission_denied`, `tool_rejected`, `tool_failed`, `prompt_boundary`, `no_eligible_model` and internal errors stay explicit (INV-021). Triage keeps its own COMPLEX fallback (ADR-090) and is not retried.
- **R10 — Document steps are not retried.** A PHX-046 document step's generation would repeat a permissioned tool call, so it is not retried. Its LEVI verification generation is retried like any other.
- **R11 — Task state.** A retry stays in the current task state (`PLANNING`, `RUNNING` or `VERIFYING`). `RECOVERING` is reachable only from `FAILED` or `VERIFYING`, so using it would publish a task failure before every retry. A retry runs under the step, verification, planning and mission deadlines it failed under. Cancellation stays cooperative (INV-019): a cancel during the backoff or the retry ends the mission `CANCELLED`, with the recovery recorded as `cancelled`.
- **R12 — Budget.** At most one retry per generation, and at most three recovery actions per mission (`RuntimeLimits.max_recovery_actions`, 0 to 3, default 3; 0 disables recovery). The direct fallback is an action. When MAHORAGA reports the budget exhausted, the mission fails with the original code. When a retry fails again with any retryable failure, it also keeps the original code. Any other failure during a retry (for example a timeout) propagates with its own code.
- **R13 — Observability (INV-022).**
  - `MissionTrace.recoveries` lists one `RecoveryRecord` per decision, in decision order. Each holds `recovery_id`, `role`, `agent_id`, `step_id`, `recovery_class`, `responder_error`, `action` and `outcome` (`recovered`, `failed`, `unavailable`, `cancelled` or `exhausted`). It holds no text.
  - Traces stored before PHX-109 still load (`recoveries` defaults to empty). No migration is needed, because the trace is stored as JSON.
  - Events use MAHORAGA's existing types, with `agent_id="adaptive_recovery"`: `recovery.started` (with `action`), `recovery.completed` (with `outcome` and `duration_ms`) and `recovery.exhausted`. The PHX-103 allowlist gains these three types with closed-enum fields only. The vocabulary stays at 32.
- **R14 — One lease at a time (INV-023).** A retry starts only after the failed generation released its heavy lease, so generations never overlap.
- **R15 — Existing tests.** Three existing assertions change with the new behavior:
  - `test_invalid_plan_output_fails_explicitly` runs with a zero budget and keeps checking PHX-029's explicit failure;
  - `test_responder_output_failure_publishes_agent_failed` expects the two recovery events;
  - `test_vocabulary_stays_32_and_mapping_uses_existing_types` lists the three recovery types.

  The two provider-error runtime tests now take the 2 s default backoff.
- **R16 — Pins.** `runtime.py` and `mission_events.py` move from the PHX-105 pin to the PHX-109 implementation commit (`PHX_109_COMMIT` / `PHX_109_PATHS`) in a separate pin commit, so each path keeps exactly one baseline. `mission_recovery.py` is new, so it is protected once it is on `origin/main`. The PR must merge with a merge commit.

## Requirements

1. `phoenix/agents/mission_recovery.py` (new).
2. `runtime.py`: the recovery helper around each generation, the direct fallback, `RuntimeLimits.max_recovery_actions` and `recovery_backoff_seconds`, and `MissionTrace.recoveries`.
3. `mission_events.py`: the `recovery.*` allowlist (R13).
4. Records: ADR-093, this ticket, `FEATURE_TICKETS.md`, docs/22.

## Non-goals

KAIDO activation (R7); re-generating after LEVI rejects (R8); retrying document steps (R10); model-proposed recovery strategies; new event types; new dependencies; API, persistence or frontend changes; live model runs.

## Tests

New `tests/test_mahoraga_missions.py` (unit, integration, recovery, secret_leakage), with a scripted provider that answers from a queue per role. It covers:

- each retryable class recovering on the second try, on the planner, assignment, specialist, LEVI and final-answer roles and on the DIRECT path, with LEVI still verifying every step;
- a retry that fails again keeping the original code, with never a third attempt;
- the budget exhausted after three actions, keeping the original code and publishing `recovery.exhausted`;
- a zero budget disabling recovery;
- `verification_failed` never retried and never completing;
- the non-transient classes refused by the classifier;
- the empty-plan fallback to DIRECT, recorded in the trace and the events, with a forged "completed" trace rejected;
- a fallback whose DIRECT answer fails keeping the plan's code;
- a `not_json` plan not falling back;
- truncated thinking retrying on the think:false provider, and a structured role retrying the same provider;
- the provider backoff and health re-check, a provider still down failing explicitly without a retry, and a provider failing again after a healthy re-check;
- a step timeout and cancellation not retried, and a cancel during the backoff recorded as `cancelled`;
- the recovery events passing the allowlist with closed values only;
- a sentinel in the request, the model output and the provider errors never reaching the events or the trace;
- one generation and one lease at a time;
- record validators, and pre-PHX-109 traces loading.

## Open issues (for the owner)

- R5's fallback answers on the unverified DIRECT path. That is PHX-029's DIRECT contract, and the result shows the COMPLEX task failed. The owner may prefer an explicit failure instead.
- The live effect on the FYP configuration 2 success rate is not measured yet. It needs a live re-run (main session).

## Acceptance

- [x] Bounded recovery, direct fallback, budget and observability implemented
- [x] Targeted tests, ruff, ruff format and strict mypy pass
- [ ] Owner reviews R1–R16
- [ ] Live FYP configuration 2 re-run (main session)
