# 22 — Recovery & Fail-Safe

## MAHORAGA

Purpose: bounded adaptive recovery.

May:
- retry with adjusted parameters
- switch tool
- switch provider/model
- choose alternate agent
- re-plan a bounded subgraph
- wait/backoff

Must:
- classify failure
- obey max attempts
- emit recovery events
- re-evaluate permission when scope/risk changes

May not:
- bypass permission
- hide failure
- loop indefinitely
- escalate destructively without approval

## KAIDO

Purpose: essential degraded capability.

Possible triggers:
- primary model provider unavailable
- resource pressure
- deep provider unavailable
- critical subsystem degradation

KAIDO supports basic conversation, diagnostics, recovery guidance, and safe limited tool routing. It is not normal high-quality mode.

## MAHORAGA in missions (PHX-109, ADR-093)

`PhoenixRuntime` retries a failed mission generation only with MAHORAGA's approval. The approval comes from `MahoragaRecovery.recover()`, using a deterministic policy responder and no model.

| Failure | Action | Bound |
| --- | --- | --- |
| `responder_output`: `not_json`, `schema` or `truncated` | Retry the same generation (`retry_same`). A truncated specialist or final answer retries on the structured think:false provider when one exists (`adjust_parameters`). | Once per generation |
| `provider_unavailable`, `provider_error` | Wait `recovery_backoff_seconds` (2 s), re-check that provider's health, and retry only if it is `HEALTHY`. | Once per generation |
| Planner `schema`, still failing after its retry | PHOENIX answers on the DIRECT path (`switch_agent`). The COMPLEX task ends `FAILED`, the trace keeps `path="complex"`, and the answer is unverified DIRECT output. | One action |
| `verification_failed`, `timeout`, cancellation, permission decisions, tool outcomes, `prompt_boundary`, `no_eligible_model`, internal errors | Never retried | — |

- **Budget (INV-009).** A mission takes at most three recovery actions (`RuntimeLimits.max_recovery_actions`; 0 disables recovery). When the budget is exhausted, or a retry fails again, the mission fails with the original failure code.
- **Verification (INV-008).** LEVI still verifies every retried output.
- **Deadlines and cancellation (INV-019).** A retry runs under the deadlines it failed under, and cancellation still wins.
- **Leases (INV-023).** Only one generation holds the lease at a time.
- **Observability (INV-022).** `MissionTrace.recoveries` records each decision with closed values only. `recovery.started`, `recovery.completed` and `recovery.exhausted` (agent `adaptive_recovery`) publish it.
- **Not retried.** Document tool steps are not retried.
- **KAIDO.** It is not wired into missions yet.
