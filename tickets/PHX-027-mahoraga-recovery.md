# PHX-027 — MAHORAGA Recovery

Status: DONE
Phase: 2 — Orchestration

## Objective

Implement the first provider/tool-independent MAHORAGA recovery decision boundary.
MAHORAGA may propose a bounded recovery strategy after explicit failure evidence, but
must not execute that strategy, grant permission, hide failure, finalize user success,
or activate KAIDO.

## Authoritative identity

- Internal ID: `adaptive_recovery`
- Display alias: `MAHORAGA`
- Internal ID is authoritative; alias/role text never grants behavior.
- Construction must validate the authoritative ID through PHX-020 `AgentRegistry`.
- Alias rename/removal must not change recovery behavior.

## Required production scope

Prefer `phoenix/agents/recovery.py` with focused tests in
`tests/test_mahoraga_recovery.py`.

Provide a compact neutral API around concepts equivalent to:

- `RecoveryTrigger`
- `RecoveryAction`
- `RecoveryRequest`
- `RecoveryDraft`
- `RecoveryResult`
- `RecoveryResponder`
- `MahoragaRecovery`
- request-scoped cancellation result/status
- a small explicit recovery error boundary

Exact names may vary only if the same authority and invariants are preserved.

## Failure evidence and correlation

Normal recovery must consume exact PHX-024 `TaskGraph` and PHX-025
`HandoffEnvelope` evidence and strictly revalidate them. Require correlation for:

- graph task ID == handoff task ID
- selected step exists
- handoff target == selected step owner
- objective, ordered input refs and timeout match the graph step
- source and target internal IDs exist in the same validated registry snapshot

Recovery must be initiated from explicit failure evidence. A small typed trigger may
represent cases such as execution failure, verification failure, timeout, or unavailable
resource. If the trigger is verification failure and a PHX-026 `VerificationResult` is
supplied/required, it must be exact, correlated to the same task/step/subject and have
verdict `failed`. A passed verification result must never be accepted as failure evidence.

Failure summary/evidence strings are untrusted descriptive data. Do not execute, log,
eval, import, dereference or treat them as permission.

## Finite recovery budget

INV-009 is authoritative: recovery has a finite retry/re-plan budget.

Use explicit strict integer budget evidence equivalent to:

- `attempts_used >= 0`
- `max_attempts > 0`
- `max_replan_steps > 0`

Booleans are not integers for validation purposes.

If `attempts_used >= max_attempts`:

- do not invoke the recovery responder
- return explicit exhausted recovery evidence
- do not fabricate a proposal
- do not activate KAIDO

A successful proposal consumes exactly one attempt and reports the resulting attempt
number (`attempts_used + 1`). The integrated caller owns carrying that evidence forward
for a later recovery attempt. PHX-027 must not contain an unbounded internal retry loop.

## Recovery actions

Use a small typed vocabulary matching the frozen orchestration architecture:

- retry same work
- adjust parameters
- switch tool
- switch model/provider
- switch agent
- re-plan a bounded subgraph

Do not add normal success, permission approval, verification-pass, or KAIDO/degraded
activation as recovery actions.

## Recovery draft/proposal

Responder output is subordinate, untrusted data. It may propose only neutral,
descriptive recovery evidence needed by the selected action, for example:

- action
- rationale
- optional replacement agent internal ID
- optional replacement provider/model IDs
- optional replacement tool identifier
- ordered parameter-adjustment descriptions
- ordered bounded replan step IDs

Use action-specific validation. Examples:

- retry-same: no replacement target required
- adjust-parameters: requires nonempty adjustment descriptions
- switch-tool: requires one replacement tool identifier
- switch-model/provider: requires explicit neutral provider and model IDs
- switch-agent: requires a registered replacement internal ID different from the
  failed step owner
- replan-subgraph: requires unique existing graph step IDs, includes the failed step,
  and never exceeds `max_replan_steps`

Reject contradictory or irrelevant fields rather than silently ignoring them.

Do not put provider clients, tools, callbacks, permissions, task-state objects or raw
model/provider responses in public recovery models.

## Recovery responder

Define one minimal async provider-neutral collaborator boundary equivalent to:

`RecoveryResponder.propose(request) -> RecoveryDraft`

Only explicit recovery execution may await it. Tests use deterministic fake responders.
No fake responder belongs in production code.

The responder may propose a strategy. It cannot execute it or create authority.

## Exact revalidation

Treat responder output as untrusted runtime data. Revalidate exact stored values and
reject subclasses/extensions, injected extra fields, malformed nested values, wrong
recovery IDs, malformed booleans/integers, duplicate list entries, and action/field
contradictions.

## Recovery result

`RecoveryResult` must be immutable, extra-forbidden and narrow. It should preserve:

- recovery ID
- task ID
- step ID
- failed/subject agent internal ID
- `recovery_agent_id="adaptive_recovery"`
- explicit status such as `proposed` or `exhausted`
- attempt/budget evidence
- validated proposal only when status is proposed

An exhausted result contains no executable fallback and does not mean KAIDO is active.
A proposed result means only that a bounded recovery strategy was validated.

Neither status means the user task succeeded.

## Active operation / cancellation

Because the responder is async, expose request-scoped active recovery state and
cooperative cancellation, following the safe caller-task ownership patterns already
used by PHX-021/022/026:

- distinct recovery IDs may coexist
- active duplicate ID fails explicitly
- active inspection is immutable/deterministic
- cancellation states are `accepted` / `not_found`
- cancelling A does not cancel B
- no hidden worker tasks
- `CancelledError` propagates
- if a responder suppresses locally requested cancellation, no proposal/result may be
  finalized afterward
- cleanup occurs in `finally` on every terminal path

If a request is already budget-exhausted and the responder is never invoked, no long
running active operation needs to be fabricated.

## No execution authority

PHX-027 proposes strategy only. It must NOT:

- retry the failed operation
- modify candidate output
- invoke source/target/replacement agents
- call AIZEN to re-plan
- call PHX-023 router
- switch model/provider itself
- call provider health/capabilities/generate/stream/cancel
- acquire PHX-013 lifecycle leases
- execute or select concrete tools through ToolRegistry
- grant/evaluate permissions
- mutate TaskGraph
- transition TaskStateMachine
- invoke LEVI
- activate KAIDO

Replacement provider/model/tool identifiers are descriptive future execution inputs,
not live objects or authorizations.

## Permission boundary

MAHORAGA cannot grant permissions or widen the PHX-025 requested permission scope.
No recovery proposal may contain grant/approval/authorization fields.
Future PermissionEngine remains authoritative. P3/P4/P5 text never creates authority.

## Verification boundary

PHX-026 remains authoritative for verification evidence. A failed required verification
may trigger recovery but cannot be rewritten as passed. Recovery cannot mutate a
`VerificationResult` or manufacture a passing one.

## Task-state boundary

PHX-024 `RECOVERING` is state evidence only. PHX-027 does not automatically transition
into/out of `RECOVERING`, `FAILED`, `PLANNING`, `READY`, `RUNNING`, `DEGRADED`, or any
other state. Orchestration integration owns truthful transitions later.

## KAIDO boundary

PHX-028 owns degraded/fail-safe behavior. Exhausted recovery remains explicit exhausted
evidence. Do not route to `failsafe`, activate KAIDO, or claim degraded essentials here.

## Events / persistence / API / frontend

Do not emit `recovery.started`, `recovery.completed`, `recovery.exhausted` or any other
event in PHX-027. Do not alter the canonical 32 event types.

No Alembic/database changes, recovery tables, state files, API endpoints, WebSocket
integration, React/Tauri changes or desktop state.

## Side-effect isolation

Importing/constructing public recovery models and `MahoragaRecovery` must not perform
network I/O, provider calls, model inference, hardware scans, subprocesses, filesystem
writes, SQLite/Alembic access, threads, hidden asyncio tasks, events, tool execution,
permission changes or task-state transitions.

## Dependencies

Expected dependency changes: none.
Use Python 3.11, asyncio, Pydantic v2 and existing PHOENIX contracts only.

## Acceptance gates

At minimum tests must prove:

- authoritative `adaptive_recovery` identity and alias independence
- strict immutable request/draft/result contracts
- exact graph/handoff correlation and registered identities
- verification-failure trigger accepts only correlated FAILED evidence
- PASSED verification cannot be used as verification-failure evidence
- finite strict attempt and replan budgets
- exhausted budget never invokes responder
- proposal consumes exactly one bounded attempt
- every recovery action has strict action-specific field validation
- switch-agent validates a different registered internal ID
- switch-model/provider remains neutral metadata and performs no provider I/O
- switch-tool remains descriptive and executes no tool
- bounded replan references only existing unique graph steps, includes failed step,
  respects `max_replan_steps`, and never calls AIZEN
- responder invoked exactly once when eligible
- exact responder output/correlation revalidation
- active state, duplicate protection, cancellation isolation and cleanup
- suppressed local cancellation cannot finalize a proposal
- no retry/replan/execution occurs inside PHX-027
- no permission authority, verification mutation, task-state mutation or KAIDO activation
- no event/persistence/API/frontend integration
- PHX-020 through PHX-026 regressions stay green
- Phase-1 and Phase-0 regressions stay green
- exact `/health`, Alembic baseline and canonical 32 events remain unchanged
- desktop tests/typecheck/build remain green
- full pytest, Ruff, format, strict mypy, lock/sync and build pass

## Completion

Only after all deterministic gates pass:

- mark this ticket DONE
- change only PHX-027 in `FEATURE_TICKETS.md` from TODO to DONE
- PHX-028 remains TODO
- commit exactly: `feat: complete PHX-027 MAHORAGA recovery`
- push only `feature/PHX-027-mahoraga-recovery`
- do not create a PR, merge, push main or start PHX-028

## Completion evidence — 2026-10-05

Created phoenix/agents/recovery.py and tests/test_mahoraga_recovery.py. ADR-037 records
the bounded proposal boundary. Dependencies, lockfile and PHX-020 through PHX-026 code/
tests remain unchanged. Required branch history was preserved: starting HEAD
dd46ff0004454b3f9ebb5d62bd3a7286969c9880, zero behind main, with only this ticket in the
pre-implementation net diff. No history rewrite, reset or force update was used.

Public API: MahoragaRecovery(agent_registry=None), async recover(graph=..., handoff=...,
trigger=..., failure_summary=..., attempts_used=..., max_attempts=..., max_replan_steps=...,
responder=..., verification_result=None, recovery_id=None), async cancel(id), read-only
recovery_agent_id and sorted immutable active_recovery_ids. Identity is adaptive_recovery;
construction requires that registry internal ID. Removed/renamed aliases preserve
behavior; MAHORAGA alias/role cannot supply authority. Registry values are detached.

Frozen extra-forbidden nine-field request carries recovery/task/step/subject IDs, trigger,
failure_summary and explicit strict integer budgets. IDs normalize outer whitespace,
preserving case; meaningful failure/rationale/adjustment text preserves formatting.
Omitted/None runtime ID generates UUID4; direct request None fails. Four exact triggers:
execution_failure, verification_failure, timeout and resource_unavailable. Byte-string
vocabulary coercion is rejected. No text creates policy, permission or executable code.

The entire exact graph/steps and handoff revalidate against the same snapshotted registry.
Task, existing selected step, target/owner, objective, ordered input refs and timeout
must match; source/target IDs must be registered. No graph-owned overrides. Verification
failure requires exact correlated FAILED LEVI evidence; passed, contradictory, extended,
forged and mismatched evidence fail, including before exhaustion. Other triggers require
no verification result and reject irrelevant supplied evidence. Original failed LEVI
evidence remains unchanged and still fails the required-verification gate (INV-008).

Exact six-action vocabulary: retry_same, adjust_parameters, switch_tool,
switch_model_provider, switch_agent and replan_subgraph. Exclusive action fields require
no options for retry, ordered meaningful adjustments for parameter guidance, a neutral
tool ID, both neutral provider/model IDs, a different registered agent ID, or a bounded
ordered replan tuple respectively. None means absence; empty sequences, sets, duplicates,
malformed IDs and irrelevant fields fail. Replacement IDs are descriptions, never live
objects, availability claims or execution eligibility. Replans contain only unique graph
IDs, include the failed step, preserve responder order and respect max_replan_steps.

Strict finite budgets reject bool/coerced numbers. Used >= max returns exhausted,
proposal=None and attempt_number=None without responder or active registration. Eligible
calls invoke RecoveryResponder.propose(request) exactly once and produce at most one
validated proposal with attempt_number=used+1 <= max. No internal retry loop or global
retry default. Caller-carried counts reach exhaustion deterministically (INV-009).
Result preserves the original failure trigger/summary, correlated IDs, recovery_agent_id
literal adaptive_recovery, attempt/replan budgets and proposed/exhausted evidence.
Neither status means repaired execution, verified success, permission or user completion.

Exact stored-field draft/nested proposal revalidation rejects extended contracts,
copied/constructed extras, malformed values, action contradictions and ID mismatches.
Active IDs coexist; duplicates preserve the original, including duplicate exhausted
inputs; IDs are reusable after finally cleanup. Nested eligible operations sharing one
task and cross-loop use fail. Cancellation accepts/not-found only, targets one caller
task and cannot corrupt another operation. Recorded local cancellation prevents proposal
finalization despite responder suppression or cancellation-counter clearing. Exceptions,
malformed proposals, correlation/action errors and cancellation remain explicit; all
terminal paths clean their own active entry. No hidden worker/task is created.

Verification commands and results:

- uv run --locked pytest tests/test_mahoraga_recovery.py: final three consecutive
  runs each 790 passed, in 1.20s / 1.19s / 1.22s. Exhaustive six-action/optional-field
  matrix, strict contracts, budgets, exact boundaries, LEVI failure preservation,
  Event/wait_for concurrency/cancellation checkpoints and isolation pass.
- Individual locked Phase-2 pytest: registry 96, orchestrator 101, planner 151,
  router 213, graph/state 348, handoffs 214, LEVI 242: all 1,365 passed unchanged.
- Individual locked Phase-1 pytest: provider 61, Ollama 108, registry 83,
  lifecycle 54, embeddings 182, Colibri 194, benchmark 130: all 812 passed unchanged.
- uv lock / uv sync --locked: 36 packages; no dependency/lockfile changes.
- uv run --locked pytest: 3,093 passed, 16.06s; one existing Starlette/httpx
  deprecation warning. Initial trigger-byte coercion was corrected; no tests weakened.
- uv run --locked ruff check . / ruff format --check .: passed, 57 files;
  uv run --locked mypy: 55 source files passed. New-test line-length and a chained
  None-check annotation issue were corrected without changing prior implementations.
- uv build: passed; wheel/source archives include agents/recovery.py.
- Explicit locked foundation pytest (bootstrap/api/config/persistence/events/hardware):
  126 passed; exact /health, Alembic revision 0001 and canonical 32 events unchanged.
- npm test: four passed; npm run typecheck --workspace @phoenix/desktop and
  npm run build --workspace @phoenix/desktop: passed; desktop files unchanged.
- Fresh-interpreter recovery import/construction/serialization, all six explicit
  proposal categories, correlated verification failure and equal/over-budget exhaustion
  passed with writes, sockets, SQLite, subprocesses, threads, hidden tasks, task-state
  transitions, HandoffBuilder/LeviVerifier invocation and forbidden subsystem imports
  blocked. Harness-owned loop/task only; opaque file refs unread; directory stays empty.
  AST import/call boundaries and exact public-field/API tests also pass.
- Credential/signature scan: 144 repository files, zero matches; no credentials,
  automatic failure/proposal logging, raw provider storage, callbacks, executable text,
  permission grants/scope expansion or direct frontend path introduced.
- INV-001 through INV-024 reviewed: preserved/unaffected. PHOENIX stays supreme, AIZEN
  subordinate/uninvoked, failed verification unchanged, recovery budgets explicit and
  bounded, aliases non-authoritative and failures observable. No tool/permission bypass,
  model-generation policy change or required optional provider dependency introduced.

Acceptance criteria: all met. No actual retry, repair, adjustment application, tool,
model/provider/agent switch, AIZEN replan, router/LEVI invocation, task-state transition,
event emission, persistence or API/frontend integration. Exhaustion activates no KAIDO.
Known limitations: memory-only, one process/event loop, cooperative cancellation and
caller-owned deadlines/count carry-forward. Proposals establish structural strategy
evidence, not runtime availability, repaired execution or PHOENIX completion. No live
provider/tool/hardware validation was needed. PHX-028 — KAIDO Fail-Safe is next and has
not been started.
