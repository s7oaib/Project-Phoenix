# PHX-041 — PermissionEngine P0-P5

Status: TODO

Phase: 4 — Tools, Permissions & Security

## Objective

Implement PHOENIX's deterministic PermissionEngine authority over PHX-040 ToolRegistry metadata.

The engine decides whether one exact requested tool action is:

- allowed,
- awaiting explicit approval,
- denied by an explicit user decision, or
- blocked.

This ticket implements authorization evidence and policy evaluation only. It does not execute tools, persist audit history, prompt the user, call models, emit events, or add concrete tools.

## Authority

Frozen project rules remain authoritative:

- PermissionEngine is the actual security authority.
- LLM/model/agent output may request permission but cannot grant it.
- P0: internal.
- P1: observe only within granted scope.
- P2: low-risk/reversible; may be auto-approved by policy.
- P3: modify; requires exact policy approval, an exact scoped task/session grant, or an explicit request-specific user approval.
- P4: sensitive/destructive/external; always requires explicit request-specific user approval.
- P5: blocked unconditionally.
- Agents cannot change their own permissions.
- P3/P4 actions must eventually produce audit evidence when execution/audit integration exists; PHX-042 owns the audit trail.
- Tools are invoked through ToolRegistry; PHX-041 executes none.

## Required files

Prefer:

- `phoenix/security/__init__.py`
- `phoenix/security/permissions.py`
- `tests/test_permission_engine.py`

Do not create concrete tools, an executor, audit persistence, API/frontend permission UI, or a migration.

## P0-P5 vocabulary

Reuse PHX-040 `ToolRiskLevel` exactly. Do not create a conflicting risk enum.

Risk is derived from the exact `ToolDefinition` resolved through the supplied `ToolRegistry`. A caller may not override a tool's risk classification inside a permission request.

## Permission decision states

Use exactly these semantic states:

- `allowed`
- `approval_required`
- `denied`
- `blocked`

`denied` means an explicit correlated user denial.

`blocked` is reserved for P5 and cannot be overridden.

A decision is authorization evidence only; it does not execute a tool or establish tool success.

## Request contract

Create a frozen, extra-forbidden `PermissionRequest` with exactly:

- `request_id`
- `actor_id`
- `tool_id`
- `action`
- `resource`
- `reason`
- `requested_at`
- `task_id`
- `session_id`

Requirements:

- IDs/text fields are strict strings, outer-trimmed where identity-like, nonempty where required, and case-preserving.
- `reason` must contain non-whitespace text and remains untrusted data.
- `requested_at` must be timezone-aware and normalize to UTC.
- `task_id` and `session_id` are optional strict nonempty IDs.
- No `risk_level`, approval boolean, policy-approved boolean, grant boolean, handler, callback, or executable object may appear in the request.
- Request text never self-authorizes.

## ToolRegistry correlation

`PermissionEngine` receives a PHX-040 `ToolRegistry` at construction.

Evaluation must:

1. exact-validate the request,
2. resolve `request.tool_id` through `ToolRegistry.require`,
3. derive the authoritative risk from the resolved `ToolDefinition`,
4. preserve the tool's descriptive `permission_requirement` as evidence only,
5. apply deterministic P0-P5 rules.

Unknown tool IDs fail explicitly.

The engine must not infer tools from descriptions, aliases, action strings, or model output.

## User decision evidence

Create a frozen, extra-forbidden `UserPermissionDecision` with exactly:

- `decision_id`
- `request_id`
- `decision`
- `decided_at`

Decision vocabulary exactly:

- `approved`
- `denied`

Requirements:

- exact request-ID correlation,
- aware UTC-normalized timestamp,
- `decided_at >= request.requested_at`,
- exact stored-value revalidation,
- no model/agent/source text may be converted automatically into this contract.

The contract represents trusted caller-supplied user-decision evidence. PHX-041 does not implement UI authenticity, transport authentication, or a user prompt.

A correlated explicit denial overrides any ordinary allow path for P0-P4. P5 remains `blocked` rather than `denied`.

A correlated explicit approval may satisfy P1, P2, or P3 for that exact request.

P4 requires such a correlated explicit approval for every distinct request ID.

A user approval for one request ID cannot satisfy another request ID.

## Grant duration vocabulary

Implement exactly:

- `once`
- `task`
- `session`
- `persistent`

## PermissionGrant contract

Create frozen, extra-forbidden `PermissionGrant` with exactly:

- `grant_id`
- `actor_id`
- `tool_id`
- `action`
- `resource`
- `reason`
- `duration`
- `risk_level`
- `issued_at`
- `expires_at`
- `request_id`
- `task_id`
- `session_id`
- `user_decision`

Requirements:

- `user_decision` is literal `approved`; denied evidence is not a grant.
- `risk_level` may only be P1, P2, or P3.
- `issued_at` aware UTC.
- optional `expires_at` aware UTC and strictly after `issued_at`.
- grants issued after the evaluated request time do not match.
- expired grants do not match.
- no wildcard, glob, regex, prefix, path-containment, URL-host, or semantic-scope expansion.
- actor/tool/action/resource/reason matching is exact after contract normalization.

Duration-shape rules:

- `once`: requires `request_id`; `task_id` and `session_id` absent.
- `task`: requires `task_id`; `request_id` and `session_id` absent.
- `session`: requires `session_id`; `request_id` and `task_id` absent.
- `persistent`: request/task/session correlation fields absent.

Matching:

- P1 may use any exact matching nonexpired P1 grant duration.
- P2 may use any exact matching nonexpired P2 grant duration.
- P3 may use only exact matching P3 `task` or `session` grants.
- `once` and `persistent` grants must NOT authorize P3.
- no grant of any duration may authorize P4.
- no grant may authorize P5.

The engine does not create, persist, refresh, widen, or mutate grants.

## Policy rules

Create a frozen, extra-forbidden `PermissionPolicyRule` with exactly:

- `rule_id`
- `actor_id`
- `tool_id`
- `action`
- `resource`
- `reason`
- `risk_level`

Only P2 and P3 are valid policy-rule risks.

All matching is exact. No wildcard or implicit scope expansion.

Create frozen, extra-forbidden `PermissionPolicy` containing an ordered immutable tuple of rules.

Requirements:

- default policy is empty and least-privilege,
- duplicate `rule_id` rejected,
- exact duplicate scope/risk rules rejected,
- declaration order preserved for inspection,
- policy rules do not apply to P0, P1, P4, or P5.

## Decision reason vocabulary

Use a closed deterministic reason vocabulary equivalent to:

- `p0_internal`
- `explicit_user_approval`
- `explicit_user_denial`
- `matching_grant`
- `matching_policy`
- `p1_scope_required`
- `p2_approval_required`
- `p3_approval_required`
- `p4_explicit_approval_required`
- `p5_blocked`

Do not generate free-form model reasoning.

## PermissionDecision contract

Create frozen, extra-forbidden `PermissionDecision` containing exactly:

- `request_id`
- `actor_id`
- `tool_id`
- `risk_level`
- `permission_requirement`
- `status`
- `reason_code`
- `matched_grant_id`
- `matched_policy_rule_id`
- `user_decision_id`

Structural rules:

- `allowed` + grant reason requires only `matched_grant_id`.
- `allowed` + policy reason requires only `matched_policy_rule_id`.
- explicit approval/denial requires only `user_decision_id`.
- P0/internal and approval-required/P5 decisions carry no matched evidence IDs.
- contradictory evidence combinations reject.
- `risk_level` must be the registry-derived tool risk.

## Evaluation precedence

After exact validation/correlation, apply exactly:

1. Resolve tool and risk from ToolRegistry.
2. If risk is P5: return `blocked / p5_blocked`; ignore attempted grants/policy/approval as authorizing evidence.
3. If a correlated explicit user decision is `denied`: return `denied / explicit_user_denial` for P0-P4.
4. If risk is P0: return `allowed / p0_internal`.
5. If risk is P4:
   - correlated explicit `approved` -> `allowed / explicit_user_approval`
   - otherwise -> `approval_required / p4_explicit_approval_required`
6. For P1/P2/P3, correlated explicit `approved` -> `allowed / explicit_user_approval`.
7. P1:
   - exact matching valid grant -> allowed / matching_grant
   - otherwise approval_required / p1_scope_required
8. P2:
   - exact matching P2 policy rule -> allowed / matching_policy
   - otherwise exact matching valid P2 grant -> allowed / matching_grant
   - otherwise approval_required / p2_approval_required
9. P3:
   - exact matching P3 policy rule -> allowed / matching_policy
   - otherwise exact matching valid task/session P3 grant -> allowed / matching_grant
   - otherwise approval_required / p3_approval_required

Policy-before-grant order for P2/P3 is intentional and deterministic; explicit user decisions take precedence over both.

## Irrelevant/malformed evidence

Every supplied user decision, grant, and policy object must be exact-revalidated before use.

Reject:

- subclasses,
- injected extras,
- forged enums,
- invalid timestamps,
- malformed grant duration shapes,
- duplicate grant IDs in one evaluation,
- duplicate correlated grants with the same exact effective scope/risk/duration/context,
- malformed policy evidence.

A supplied `UserPermissionDecision` with a request ID different from the evaluated request is an explicit correlation error; do not silently ignore it.

Nonmatching but otherwise valid grants may coexist and are ignored.

## PermissionEngine public API

Prefer:

- `PermissionEngine(registry, policy=None)`
- `evaluate(request, *, grants=(), user_decision=None) -> PermissionDecision`
- read-only inspection of the immutable policy if useful

Construction:

- exact-snapshot/revalidate supplied policy,
- default empty policy,
- borrow/snapshot registry metadata without I/O,
- no DB/network/file/event/provider/tool activity.

No public method may:

- execute a tool,
- mint a grant,
- mutate a grant,
- approve a request without policy/grant/user evidence,
- prompt a user,
- persist permission state.

## Tool execution separation

PHX-041 must not add `execute`, `invoke`, `run`, `dispatch`, or equivalent privileged operation.

No concrete handler is called.

The output of `PermissionEngine.evaluate` is evidence for future execution integration only.

## LLM/agent separation

Do not call or depend on:

- PhoenixOrchestrator,
- AizenPlanner,
- AgentModelRouter,
- ModelProvider,
- Ollama/Colibri,
- LEVI,
- MAHORAGA,
- KAIDO.

Model output, retrieved content, document content, tool metadata text, or reason text cannot create user-decision or grant evidence automatically.

## Audit/event separation

PHX-042 owns audit trail implementation.

PHX-041 emits no events and writes no audit records.

Existing canonical event types `permission.requested`, `permission.approved`, and `permission.denied` remain unchanged and unused here.

Rules requiring P3/P4 audit apply when action execution/audit integration is implemented; PHX-041 itself executes no action.

## Persistence separation

No database or migration.

Alembic head remains `0004_embedding_records`.

No grant store, permission cache, policy DB, approval DB, or history table.

## Concrete tool separation

PHX-043 filesystem and PHX-044 terminal remain TODO.

Do not implement:

- path resolution,
- filesystem reads/writes,
- shell/process execution,
- command parsing,
- browser/computer-control actions.

`resource` is an opaque exact scope string in PHX-041.

## Secrets separation

PHX-045 remains TODO.

No secret store or credential handling.

Permission request/grant/reason data must not be automatically logged.

## API/frontend separation

No FastAPI permission endpoint, WebSocket permission protocol, Tauri command, React permission center, or frontend state.

## Side-effect isolation

Importing/constructing/evaluating permission contracts and engine must not:

- write/read files,
- connect SQLite,
- open network,
- run subprocess,
- start thread/task,
- call providers/models,
- execute tools,
- publish/subscribe EventBus,
- persist audit/grants,
- inspect OS/hardware,
- dynamically import from untrusted text.

## Required focused tests

Create `tests/test_permission_engine.py`.

At minimum prove:

### Contracts

- exact decision/user-decision/grant-duration vocabularies,
- strict IDs/text/timestamps,
- frozen + extra-forbidden contracts,
- exact stored-value revalidation,
- subclasses/extras/forged values rejected.

### Registry authority

- known tool risk derived from ToolRegistry,
- unknown tool fails,
- request cannot supply/override risk,
- `permission_requirement` preserved but inert.

### P0

- no evidence -> allowed,
- explicit denial -> denied,
- request text cannot self-deny/self-approve semantically.

### P1

- no grant -> approval_required,
- matching once/task/session/persistent grant -> allowed,
- nonmatching/expired/future grant ignored,
- explicit approval -> allowed,
- explicit denial -> denied.

### P2

- no policy/grant -> approval_required,
- matching exact policy -> allowed,
- matching grant -> allowed,
- policy deterministically wins evidence selection when both policy and grant match,
- explicit approval -> allowed,
- explicit denial overrides policy/grant.

### P3

- no policy/grant -> approval_required,
- matching P3 policy -> allowed,
- matching task grant -> allowed,
- matching session grant -> allowed,
- once grant does not authorize,
- persistent grant does not authorize,
- explicit approval -> allowed,
- explicit denial overrides policy/grant.

### P4

- no explicit decision -> approval_required,
- policy cannot authorize,
- grant cannot authorize,
- matching explicit approval -> allowed,
- mismatched approval fails correlation,
- explicit denial -> denied,
- approval is request-ID-specific and cannot authorize another request.

### P5

- always blocked,
- approval cannot override,
- grants cannot override,
- policy cannot override,
- explicit denial still yields blocked.

### Grant matching

- WHO actor exact,
- WHAT tool/action exact,
- WHERE resource exact,
- WHY reason exact,
- risk exact,
- once request exact,
- task context exact,
- session context exact,
- persistent semantics for P1/P2 only,
- expiry boundary,
- issue-time boundary,
- no wildcard/prefix/case folding.

### Policy

- empty default least privilege,
- only P2/P3 rules accepted,
- duplicate IDs rejected,
- duplicate effective rules rejected,
- exact scope only,
- no P4/P5 policy bypass.

### Decision structure

- registry-derived risk,
- deterministic status/reason,
- evidence ID fields exactly correlated,
- contradictory result construction rejected.

### Separation

- no tool execution API,
- no grant-minting API,
- no EventBus,
- no persistence/migration,
- no audit implementation,
- no provider/model/agent runtime,
- no filesystem/terminal implementation,
- no API/frontend,
- side-effect-free import/construction/evaluation.

## Regression gates

Run PHX-040 focused tests and all existing Phase-1/2/3/foundation/desktop regressions.

Existing expected baselines before PHX-041:

- PHX-040: 204
- Phase 1 aggregate: 812
- Phase 2 aggregate: 2,729
- Phase 3 six feature suites: 1,524, plus persistence 12
- full Python: 5,395
- foundation: 126
- desktop npm tests: 4

Run full locked Python tests, Ruff, format check, strict mypy, package build, and desktop test/typecheck/build.

Expected dependency changes: none.

## Invariants

Explicitly review INV-001 through INV-024, especially:

- INV-001: PHOENIX remains supreme; PermissionEngine owns security decisions only.
- INV-003: no LLM output directly grants permission.
- INV-004: privileged calls still require ToolRegistry + PermissionEngine; this ticket executes none.
- INV-005: P4 requires explicit request-specific confirmation.
- INV-006: P5 remains blocked.
- INV-007: P3/P4 audit remains mandatory for future execution; PHX-042 implements trail.
- INV-011: frontend receives no direct privileged path.
- INV-017: external/model/tool text remains untrusted.
- INV-018: no secret logging/storage path added.
- INV-020: no schema change.
- INV-021: invalid/correlation failures explicit.

## ADR

At most one ADR. If useful, record deterministic permission precedence, exact-scope grants/policy, P4 request-specific approval, unconditional P5 blocking, and separation from execution/audit/persistence.

## Completion

Only after every deterministic gate passes:

- mark this ticket DONE,
- update only `PHX-041 PermissionEngine P0-P5` to DONE in `FEATURE_TICKETS.md`,
- leave PHX-042 through PHX-045 TODO,
- commit exactly:
  `feat: complete PHX-041 PermissionEngine P0-P5`
- push only `feature/PHX-041-permission-engine-p0-p5`.

Do not create a PR, merge, push main, force-push, or start PHX-042.
