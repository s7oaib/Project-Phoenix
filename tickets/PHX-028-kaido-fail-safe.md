# PHX-028 — KAIDO Fail-Safe

Status: DONE
Phase: 2 — Orchestration

## Objective

Implement PHOENIX's first provider/tool-independent KAIDO degraded fail-safe policy boundary.
KAIDO exists only for explicit degraded conditions and may expose a small set of essential
capability categories. It must never become normal/high-quality mode, grant permission,
execute tools/models, hide failure, or claim user-facing completion.

## Authoritative identity

- Internal ID: `failsafe`
- Display alias: `KAIDO`
- Internal ID is authoritative; alias/role text never grants behavior.
- Construction must validate `failsafe` through PHX-020 `AgentRegistry`.
- Alias rename/removal must not change fail-safe behavior.

## Required production scope

Prefer `phoenix/agents/failsafe.py` with focused tests in
`tests/test_kaido_failsafe.py`.

Provide a compact synchronous neutral API around concepts equivalent to:

- `FailSafeTrigger`
- `FailSafeCapability`
- `FailSafeRequest`
- `FailSafeDecision`
- `KaidoFailSafe`
- a small explicit fail-safe error boundary

Exact names may vary only if the same authority and invariants are preserved.

## Degraded-only invariant

INV-010 is authoritative: KAIDO is degraded/fail-safe mode only.

The production vocabulary must contain no normal/healthy/high-quality activation mode.
Every successful decision must explicitly identify itself as degraded/fail-safe evidence.
Do not silently use KAIDO as a generic fallback when normal routing merely has no winner.

## Explicit trigger vocabulary

Use a small typed trigger vocabulary grounded in `docs/22_RECOVERY_FAILSAFE.md` and
PHX-027 exhaustion evidence. A defensible exact set is:

- `primary_model_provider_unavailable`
- `resource_pressure`
- `deep_provider_unavailable`
- `critical_subsystem_degradation`
- `recovery_exhausted`

Do not infer the trigger from free-form text. The caller supplies explicit typed evidence.
Do not add normal success, healthy mode, permission approval, verification pass, or routine
routing failure as fail-safe triggers.

## Essential capability vocabulary

Represent only KAIDO's documented essential degraded capability categories:

- `basic_conversation`
- `diagnostics`
- `recovery_guidance`
- `safe_limited_tool_routing`

This vocabulary is descriptive policy evidence only.

`safe_limited_tool_routing` does NOT:

- execute a tool
- choose a concrete tool
- grant tool permission
- bypass future ToolRegistry
- bypass future PermissionEngine
- authorize P3/P4/P5 work

Do not add broad/high-quality capabilities such as deep research, unrestricted computer
control, unrestricted tool execution, or normal premium model mode.

## Fail-safe request contract

Create a frozen, extra-forbidden `FailSafeRequest`.

Minimum fields:

- `failsafe_id`
- `trigger`
- `reason`
- `requested_capabilities`
- optional `recovery_result`

`failsafe_id`:

- UUID4 default acceptable
- caller-overridable
- strict nonempty string
- outer-whitespace normalization allowed
- stable and serializable

`reason`:

- strict meaningful string
- preserves meaningful formatting
- untrusted descriptive data
- never automatically logged/executed

`requested_capabilities`:

- immutable ordered tuple
- nonempty
- only `FailSafeCapability` values
- no duplicates
- caller order preserved

A request must not carry provider clients, model objects, tool objects, permission grants,
callbacks, task-state objects, arbitrary metadata dicts, or raw subsystem payloads.

## Recovery-exhaustion integration

PHX-027 `RecoveryResult` may be consumed only when:

`trigger == recovery_exhausted`

In that case require an exact revalidated `RecoveryResult` with:

- `status == exhausted`
- `proposal is None`
- `attempt_number is None`
- internally consistent finite budget evidence

Reject a PHX-027 `proposed` result as recovery-exhaustion evidence.
Do not mutate the recovery result.
Do not execute its prior proposal.

For every non-recovery trigger, prefer rejecting a supplied `RecoveryResult` as irrelevant
evidence rather than silently ignoring it.

## Decision contract

Create a frozen, extra-forbidden `FailSafeDecision`.

Minimum fields:

- `failsafe_id`
- `failsafe_agent_id`
- `mode`
- `trigger`
- `reason`
- `enabled_capabilities`
- optional `recovery_id`

`failsafe_agent_id` must be literal `failsafe`.

`mode` must be exactly a degraded/fail-safe value such as `degraded`.
There must be no normal/healthy mode in this contract.

`enabled_capabilities` must be the exact validated requested capability tuple; no hidden
extra capability may be added.

For `recovery_exhausted`, `recovery_id` should preserve the exact correlated PHX-027
recovery ID. For other triggers it should be absent.

A decision means only that a degraded capability policy was selected. It does NOT mean:

- the user task succeeded
- recovery succeeded
- a model/provider is loaded
- a tool is authorized
- PermissionEngine approved anything
- task state is already DEGRADED
- any capability execution occurred

## KAIDO public entry point

Implement one obvious synchronous pure entry point, for example:

`KaidoFailSafe.activate(request) -> FailSafeDecision`

or an equivalent narrow name.

Required behavior:

1. exact-revalidate the request
2. validate authoritative `failsafe` identity
3. validate trigger-specific evidence
4. preserve explicit trigger/reason
5. preserve requested capability order exactly
6. return immutable degraded-mode decision

No async API is required. No cancellation API is required because PHX-028 performs no
long-running work or I/O.

## No automatic activation

Do not call KAIDO automatically from:

- PHX-023 no-route failure
- PHX-024 DEGRADED state
- PHX-026 verification failure
- PHX-027 proposed recovery
- provider health APIs
- resource probes
- startup health checks

PHX-028 supplies the explicit fail-safe policy boundary only. Later orchestration/startup
integration decides when to call it with truthful evidence.

## Provider/model boundary

Do not import concrete provider adapters.
Do not call provider health, capabilities, generation, streaming, cancellation, discovery,
model registry, lifecycle manager, or benchmark harness.

Trigger strings such as provider unavailable are caller-supplied typed evidence only.
KAIDO does not prove provider availability itself.

## Tool/permission boundary

Do not implement ToolRegistry or PermissionEngine.
Do not execute filesystem, terminal, browser, media, or computer-control actions.

Even when `safe_limited_tool_routing` is enabled, the decision grants no tool authority.
Future tool execution must still pass ToolRegistry and deterministic permission evaluation.
P4 still requires explicit confirmation and P5 remains blocked.

## Verification/recovery boundaries

Do not invoke LEVI or MAHORAGA.

- failed required verification remains failed
- recovery exhaustion remains exhausted
- fail-safe activation cannot rewrite either into success
- KAIDO does not retry/replan/repair

## Task-state boundary

Do not modify `TaskStateMachine` and do not automatically transition any task to
`DEGRADED`.

PHX-024 state evidence and PHX-028 degraded policy are separate boundaries until later
orchestration integrates them truthfully.

## PHOENIX authority

PHOENIX remains the sole supreme orchestrator.
KAIDO produces subordinate degraded-mode policy evidence only.
Do not return `PhoenixResult` or claim user-facing final success.

## Observability boundary

INV-022 requires fallback behavior to be observable. Satisfy this at PHX-028 by making
the fail-safe decision explicit and immutable with:

- degraded mode
- explicit trigger
- preserved reason
- explicit enabled capability list
- optional recovery evidence reference

Do not hide fallback behind normal-mode results.

Do not emit runtime events in PHX-028. The canonical event vocabulary remains unchanged;
later integrated orchestration may emit truthful degraded/recovery/task events.

## No persistence / API / frontend

No Alembic/database changes, fail-safe tables, state files, API endpoints, WebSocket
integration, React/Tauri changes, or desktop state.

## Side-effect isolation

Importing/constructing public PHX-028 contracts and `KaidoFailSafe`, plus explicit pure
activation, must not:

- open sockets
- contact providers
- run inference
- scan hardware
- run `nvidia-smi`
- launch subprocesses
- write files/directories
- access SQLite/Alembic
- create threads
- create asyncio tasks
- emit events
- execute tools
- grant permissions
- invoke LEVI/MAHORAGA/AIZEN/router/orchestrator
- mutate task state

## Dependencies

Expected dependency changes: none.
Use Python 3.11, Pydantic v2 and existing PHOENIX contracts only.

## Acceptance gates

At minimum tests must prove:

- authoritative `failsafe` identity and alias independence
- exact trigger vocabulary and rejection of normal/healthy activation
- exact four essential capability categories
- immutable strict request/decision contracts
- nonempty ordered unique requested capabilities
- decision mode is degraded only
- no hidden extra capability is added
- explicit reason/trigger are preserved
- `recovery_exhausted` requires exact PHX-027 exhausted evidence
- proposed recovery cannot activate recovery-exhausted path
- unrelated recovery evidence is rejected for other triggers
- original RecoveryResult remains unchanged
- safe-limited-tool-routing grants no tool/permission authority
- no provider/model I/O or discovery
- no tool execution
- no PermissionEngine behavior
- no LEVI/MAHORAGA/AIZEN/router/orchestrator invocation
- no automatic TaskStateMachine transition
- no event/persistence/API/frontend integration
- no network/filesystem/subprocess/thread/task side effects
- PHX-020 through PHX-027 regressions stay green
- Phase-1 and Phase-0 regressions stay green
- exact `/health`, Alembic baseline and canonical 32 events remain unchanged
- desktop tests/typecheck/build remain green
- full pytest, Ruff, format, strict mypy, lock/sync and package build pass

## Completion

Only after all deterministic gates pass:

- mark this ticket DONE
- change only PHX-028 in `FEATURE_TICKETS.md` from TODO to DONE
- Phase 2 then becomes complete
- commit exactly: `feat: complete PHX-028 KAIDO fail-safe`
- push only `feature/PHX-028-kaido-fail-safe`
- do not create a PR, merge, push main, or start PHX-030

## Completion evidence

- Implemented `phoenix/agents/failsafe.py` and `tests/test_kaido_failsafe.py`.
  Public API: FailSafeTrigger, FailSafeCapability, FailSafeRequest,
  FailSafeDecision, KaidoFailSafe, KaidoFailSafeError;
  synchronous `KaidoFailSafe.activate(request) -> FailSafeDecision`.
- Exact snapshotted AgentRegistry requires `failsafe`; default, renamed and removed
  aliases work identically. KAIDO alias alone and missing identity fail explicitly.
- Exact five triggers: primary_model_provider_unavailable, resource_pressure,
  deep_provider_unavailable, critical_subsystem_degradation, recovery_exhausted.
  Exact four categories: basic_conversation, diagnostics, recovery_guidance,
  safe_limited_tool_routing. No healthy/normal or unrestricted vocabulary.
- Frozen extra-forbidden five-field request: failsafe_id, trigger, reason,
  requested_capabilities, recovery_result. IDs are strict trimmed nonempty strings,
  default UUID4s are independent; reasons require meaningful strict text and retain
  formatting. Nonempty ordered unique capability tuples detach caller lists.
- Frozen extra-forbidden seven-field decision: failsafe_id, failsafe_agent_id,
  mode, trigger, reason, enabled_capabilities, recovery_id. Identity is literal
  failsafe; mode is literal degraded. Enabled tuple exactly equals requested tuple.
  Tests cover all 64 ordered nonempty subsets across all five triggers.
- Recovery exhaustion requires exact stored-value-revalidated PHX-027 RecoveryResult
  with exhausted status, consistent strict finite budgets, no proposal/attempt.
  Preserve its exact recovery ID and leave original evidence unchanged. Reject
  proposed, contradictory, extended, copied/constructed malformed evidence and
  nested malformed proposals. Other triggers reject irrelevant recovery evidence;
  their decisions have no recovery ID. No recovery execution or success claim.
- Revalidate request/decision/registry/definitions by exact stored fields, including
  extras omitted by model_dump. Reject forged fields, subclasses, malformed
  capability tuples, normal modes and invalid identities. Reason text stays data.
- Safe limited tool routing grants no permission or execution authority; P4 still
  requires confirmation, P5 stays blocked. No provider/model discovery, generation,
  probing, registry/lifecycle/benchmark work, tool selection/execution or permissions.
- No LEVI, MAHORAGA, AIZEN, router, PHOENIX or registered-agent execution. Explicit
  boundary test preserves task state/history and failed required verification.
  No automatic DEGRADED transition, finalization or hidden normal-route fallback.
- INV-022 evidence is explicit immutable degraded mode, caller trigger/reason,
  exact capabilities and applicable recovery reference. No events/logging,
  persistence, API, frontend, voice or startup integration.
- Fresh-interpreter executable isolation blocks filesystem writes, sockets,
  SQLite, subprocesses, threads, asyncio tasks/loops, forbidden subsystem imports,
  verifier/recovery calls and state transition before import/construction/activation.
  All triggers and every capability activate without side effects or input mutation.
- `uv run --locked pytest tests/test_kaido_failsafe.py`: 574 passed in 0.82s.
- Individually ran unchanged PHX-020 through PHX-027 suites: 96/101/151/213/348/
  214/242/790 passed (2,155 total).
- Individually ran Phase-1 provider/Ollama/registry/lifecycle/embeddings/Colibri/
  benchmark suites: 61/108/83/54/182/194/130 passed (812 total).
- `uv run --locked pytest`: 3,667 passed in 15.59s; one existing Starlette/httpx
  deprecation warning. `uv run --locked ruff check .` and
  `uv run --locked ruff format --check .`: pass (59 formatted files).
  `uv run --locked mypy`: pass (57 source files).
- `uv lock`, `uv sync --locked`: pass, 36 packages; pyproject.toml/uv.lock unchanged.
  `uv build`: wheel and source distribution pass and include failsafe.py.
- Foundation bootstrap/API/config/persistence/events/hardware: 126 passed in 2.77s.
  Explicit health remains status=ok/service=phoenix-api/version=0.1.0;
  Alembic head remains 0001; canonical EventType vocabulary remains 32 entries.
- `npm test`: 4 passed. `npm run typecheck --workspace @phoenix/desktop` and
  `npm run build --workspace @phoenix/desktop`: pass; desktop unchanged.
- Credential-signature scan: 147 repository files, zero matches. Reviewed INV-001
  through INV-024, security/permissions and exact scope; no earlier production
  module or test changed. One decision recorded: ADR-038.
- All deterministic acceptance gates PASS; Phase 2 is complete.
- Limitations: caller evidence is structurally validated, not independently probed
  or authenticated. Capability selection establishes no availability, authorization,
  execution or success. Provider/tool/startup/event/state integration remains deferred.
  PHX-030 Memory Schema remains TODO and was not started.
