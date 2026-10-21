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
- **KAIDO.** PHX-119 wires it (below): exhaustion of a positive budget is
  KAIDO evidence, except for LEVI's own generation.

## KAIDO in missions (PHX-119, ADR-155..ADR-158)

One `KaidoMonitor` per backend turns the provider-health probe that `/ready`
and every mission already make, plus a cheap memory sample, into an engaged or
released state. Every engagement is a PHX-028 `KaidoFailSafe` decision.

| Trigger | Evidence | Scope |
| --- | --- | --- |
| `primary_model_provider_unavailable` | No provider reports `HEALTHY` in that probe | Process |
| `resource_pressure` | Free RAM below 2,048 MiB (psutil), or free VRAM below 512 MiB when a caller's probe reports it | Process |
| `recovery_exhausted` | MAHORAGA reported the mission's budget exhausted (`RecoveryResult` with `status=exhausted`) | One mission |

- **Hysteresis.** KAIDO engages on the first bad observation. A memory
  engagement releases only after two consecutive clear observations
  (`KaidoPolicy.release_after`); a provider engagement releases on the first
  probe with a `HEALTHY` provider (`provider_release_after`, default 1), the
  probe on which PHX-108's `ModelRegistrar` registers its tier models. Memory counts
  as clear only at or above 3,072 MiB RAM and 1,024 MiB VRAM, so a value
  between the thresholds never flaps it. A failing memory probe is no evidence.
- **The KAIDO answer.** One generation of T0 (`qwen3.5:2b`, the tier row
  `failsafe`) on the structured (thinking-off) provider, at most 1,024 output
  tokens, inside an 8K-token budget (a request that does not fit is declined).
  T0 may run on a `HEALTHY` or `DEGRADED` provider, never an `UNAVAILABLE` one.
  No triage, plan, tools, permissions, MAHORAGA retry or LEVI check. The text
  starts with a fixed degraded label and the answer is never verified.
- **Process-wide.** While engaged, every mission is that one reply. If KAIDO
  cannot answer, the mission fails explicitly (`provider_unavailable`,
  `no_eligible_model` or the generation's own code).
- **After exhaustion.** The COMPLEX task first ends `FAILED` with the original
  codes. If KAIDO answers, the task moves to `DEGRADED` and the mission
  completes with the labelled answer; if it cannot, the original failure
  stands. A zero budget, a LEVI rejection and an exhausted LEVI generation
  never reach KAIDO (INV-008).
- **Observability (INV-022).** `MissionTrace.kaido` (trigger, scope,
  capabilities, recovery ID, outcome, `verified=false`), `phoenix.degraded` on
  engagement (process, correlated by the failsafe ID; mission, by the mission
  ID) and `phoenix.ready` on release, KAIDO's `agent.*` events (agent
  `failsafe`, role `kaido`), `GET /ready` → `kaido`, and
  `GET /missions/{id}` → `kaido`.
- **Authority.** KAIDO's capabilities are basic conversation, diagnostics and
  recovery guidance only; it routes no tools and grants nothing (INV-010).
