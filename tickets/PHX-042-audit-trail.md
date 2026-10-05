# PHX-042 — Audit trail

Status: TODO

Phase: 4 — Tools, Permissions & Security

## Objective

Implement PHOENIX's first durable append-only audit trail for permission decisions and completed/attempted tool outcomes, without executing tools.

PHX-042 must provide trustworthy durable evidence that future privileged tool pipelines can append after permission evaluation and after an allowed tool attempt. It must satisfy the architectural requirement that P3/P4 execution is auditable while preserving PHX-040 ToolRegistry and PHX-041 PermissionEngine as the authoritative metadata and authorization boundaries.

This ticket does **not** execute tools, prompt users, mint grants, authenticate external approval origins, implement filesystem/terminal tools, expose API/UI, or change the canonical EventType vocabulary.

## Authority

Frozen project rules remain authoritative:

- P3/P4 actions create audit events.
- Privileged tool calls pass through ToolRegistry and PermissionEngine.
- P4 requires explicit request-specific approval.
- P5 is blocked.
- An LLM/model/agent cannot grant permission.
- Secrets must not enter normal logs or conversational memory.
- Tool stdout/stderr, uploaded files, webpages, model output and third-party responses are untrusted.
- Structured persistence uses SQLite + SQLAlchemy 2 + Alembic.
- `audit_events` is a planned V1 table.
- Stable IDs, UTC timestamps and versioned migrations are required.
- The canonical EventType vocabulary remains exactly 32 values; PHX-042 does not invent `audit.*` EventType values.

## Required files

Prefer:

- `phoenix/security/audit.py`
- `phoenix/persistence/audit.py`
- `alembic/versions/0005_audit_events.py`
- `tests/test_audit_trail.py`

Minimal updates may be required to:

- `alembic/env.py`
- historical migration-head tests
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- this ticket

Do not create:

- tool executor
- filesystem tool
- terminal tool
- secrets store
- audit API/UI
- background audit worker
- event-bus bridge

## Reuse existing vocabularies

Reuse exactly:

- `ToolRiskLevel` from PHX-040
- `ToolResultStatus` from PHX-040
- `PermissionStatus` from PHX-041
- `DecisionReason` from PHX-041
- `PermissionRequest` from PHX-041
- `PermissionDecision` from PHX-041

Do not duplicate or redefine these vocabularies.

## Audit event kinds

Implement exactly:

- `permission_decision`
- `tool_outcome`

Prefer:

```python
class AuditEventKind(StrEnum):
    PERMISSION_DECISION = "permission_decision"
    TOOL_OUTCOME = "tool_outcome"
```

No other audit-event kind belongs in PHX-042.

## Audit requirement helper

Provide a pure helper equivalent to:

```python
def audit_required_for_execution(risk: ToolRiskLevel) -> bool:
    ...
```

Semantics:

- P3 -> True
- P4 -> True
- P0/P1/P2/P5 -> False

This helper is descriptive integration policy only. It does not execute tools or permit skipping audit when a future execution path is required by project rules.

Reject foreign/unregistered risk enums.

## Audit record contract

Create frozen, extra-forbidden `AuditRecord` with exactly these fields:

- `audit_id`
- `event_kind`
- `occurred_at`
- `request_id`
- `actor_id`
- `tool_id`
- `action`
- `resource`
- `request_reason_hash`
- `risk_level`
- `permission_requirement`
- `permission_status`
- `permission_reason_code`
- `matched_grant_id`
- `matched_policy_rule_id`
- `user_decision_id`
- `task_id`
- `session_id`
- `correlation_id`
- `tool_result_status`

Exactly 20 fields.

No raw request reason.
No tool input/output payload.
No stdout/stderr.
No model content.
No secret field.
No arbitrary metadata dictionary.
No callback/handler/session/runtime object.

## Audit ID

`audit_id`:

- strict string
- outer-whitespace normalized
- nonempty
- case-preserving
- independent UUID4 default preferred
- caller-supplied valid IDs preserved

Reject explicit `None`, bool, numbers, bytes, objects, empty and whitespace-only values.

## Timestamps

`occurred_at`:

- required
- timezone-aware
- normalize to UTC
- naive timestamps rejected
- no hidden wall-clock default

For both event kinds require:

`occurred_at >= PermissionRequest.requested_at`

The service must never rewrite timestamps.

## Request-reason privacy

Do **not** persist `PermissionRequest.reason` in plaintext.

Persist exactly:

```python
sha256(request.reason.encode("utf-8")).hexdigest()
```

as lowercase 64-character `request_reason_hash`.

No whitespace normalization, case folding or semantic normalization before hashing.

The hash is correlation/privacy-minimization evidence only. It is **not** authentication, signature or secret protection.

## Resource evidence

Persist the exact normalized `PermissionRequest.resource` because the audit trail must preserve WHERE the requested action was scoped.

Treat it as opaque untrusted text.

Do not:

- open it
- resolve it
- treat it as a path/URL
- perform containment or hostname matching
- execute it

Future PHX-043/044 own concrete resource interpretation.

## ToolRegistry correlation

`AuditTrail` must receive a PHX-040 `ToolRegistry` at construction.

For every record operation:

1. exact-revalidate `PermissionRequest`
2. resolve `request.tool_id` via `ToolRegistry.require`
3. derive authoritative risk from `ToolDefinition.risk_level`
4. preserve `ToolDefinition.permission_requirement` as inert evidence
5. exact-revalidate `PermissionDecision`
6. require decision/request/tool correlation
7. require decision risk/permission_requirement equal current registry metadata

Do not infer risk from caller text or from the PermissionDecision alone.

Unknown tool IDs fail explicitly.

## PermissionDecision correlation

Require exact equality:

- decision.request_id == request.request_id
- decision.actor_id == request.actor_id
- decision.tool_id == request.tool_id
- decision.risk_level == registry tool risk
- decision.permission_requirement == registry tool permission_requirement

`PermissionDecision` structural validators remain authoritative for status/reason/evidence-ID consistency.

PHX-042 does not re-run PermissionEngine and does not recreate grants/policy/user-decision evidence.

## Permission-decision audit

Provide a method equivalent to:

```python
record_permission_decision(
    request: PermissionRequest,
    decision: PermissionDecision,
    *,
    occurred_at: datetime,
    audit_id: str | None = None,
    correlation_id: str | None = None,
) -> AuditRecord
```

Semantics:

- event_kind = permission_decision
- tool_result_status = None
- record exact derived request/decision evidence
- append exactly one durable row
- commit explicitly
- return detached exact-revalidated AuditRecord

Any valid correlated PHX-041 decision may be audited:

- allowed
- approval_required
- denied
- blocked

No tool execution occurs.

## Tool-outcome audit

Provide a method equivalent to:

```python
record_tool_outcome(
    request: PermissionRequest,
    decision: PermissionDecision,
    result_status: ToolResultStatus,
    *,
    occurred_at: datetime,
    audit_id: str | None = None,
    correlation_id: str | None = None,
) -> AuditRecord
```

Requirements:

- event_kind = tool_outcome
- exact ToolResultStatus vocabulary
- decision.status must be `allowed`
- P5 is impossible
- `denied` ToolResultStatus is rejected in PHX-042 because denied permission belongs in `permission_decision`, not an executed/attempted allowed-tool outcome
- valid statuses for tool_outcome are therefore:
  - success
  - partial_success
  - timeout
  - cancelled
  - failure
  - unavailable_dependency
- append exactly one durable row
- no tool execution
- no interpretation of result status as verification or final user-facing success

Future PHX-043/044 execution integration owns calling this method after an allowed concrete tool attempt.

## P3/P4 invariant evidence

PHX-042 must prove that a future caller can record both:

1. the correlated permission decision, and
2. the allowed tool outcome

for P3 and P4.

The service itself cannot force future tool implementations to call it because no execution exists in PHX-042.

Completion evidence must state this limitation truthfully.

Do not claim global INV-007 enforcement until concrete privileged execution integrates the audit trail.

## Evidence IDs

Persist exactly from PermissionDecision:

- `matched_grant_id`
- `matched_policy_rule_id`
- `user_decision_id`

Do not invent IDs.
Do not dereference them.
Do not query a grant/policy/user-decision store.

## Task/session correlation

Persist exact request:

- `task_id`
- `session_id`

including `None`.

Do not invent a task/step/session table dependency.
No foreign key is required.

## Correlation ID

`correlation_id` is optional strict nonempty normalized opaque text.

It is supplied by the future execution/orchestration caller.

PHX-042 does not derive it from task/session/request IDs.

## AuditRecord shape rules

`permission_decision` requires:

- `tool_result_status is None`

`tool_outcome` requires:

- `tool_result_status is not None`
- `permission_status == allowed`
- `risk_level != P5`
- `tool_result_status != denied`

Reject contradictory directly constructed AuditRecord instances.

## Exact stored-value revalidation

Every supplied PHX-040/041 contract and every AuditRecord read from persistence must be exact-revalidated from stored values.

Reject:

- subclasses
- model-copy injected extras
- forged enum instances
- malformed timestamps
- contradictory evidence IDs
- contradictory event-kind/status shapes
- malformed reason hashes

Do not trust `isinstance` alone.

## Audit trail service

Implement immutable service equivalent to:

```python
AuditTrail(
    session_factory: sessionmaker[Session],
    registry: ToolRegistry,
)
```

Borrow both dependencies.

Constructor must:

- snapshot/revalidate registry metadata
- perform no database connection
- create no DB file/directory
- run no migration
- start no thread/task
- emit no EventBus event

The service must not dispose the caller's engine/session factory.

## Append-only policy

PHX-042 is append-only.

Public service API must NOT expose:

- update
- replace
- delete
- clear
- mutate

A different or corrected audit fact is a new audit row, never mutation of old history.

## Database-level append-only enforcement

The migration must create SQLite triggers that reject UPDATE and DELETE on `audit_events`.

Use stable explicit trigger names:

- `trg_audit_events_no_update`
- `trg_audit_events_no_delete`

The trigger message must be small and not expose row content.

Downgrade must drop the triggers before dropping the table.

Tests must prove raw SQL/ORM UPDATE and DELETE attempts fail and leave original rows unchanged.

## Duplicate audit ID

If `audit_id` already exists:

- explicit conflict error
- original row unchanged
- no overwrite/upsert/replacement

Different audit IDs may legitimately represent repeated evaluations/outcomes for the same permission request.

Do not add uniqueness on request ID.

## Get

Provide:

```python
get(audit_id) -> AuditRecord | None
```

- strict normalized audit ID
- exact primary-key lookup
- detached revalidated record
- absent -> None
- corrupted stored row -> explicit safe audit data error
- no mutation

## Administrative list query

Create frozen, extra-forbidden `AuditListQuery` with optional exact filters:

- `event_kind`
- `request_id`
- `actor_id`
- `tool_id`
- `risk_level`
- `task_id`
- `session_id`

and:

- `limit`
- `offset`

Recommended:

- default limit = 100
- 1 <= limit <= 500
- offset >= 0

`None` means no filter for that field.

No wildcard, regex, full-text, semantic or prefix filtering.

## List ordering

`list_records(query)` returns immutable tuple ordered exactly:

1. occurred_at ASC
2. audit_id ASC

No database-order reliance.

This is administrative inspection only.

## ORM model

Create one SQLAlchemy 2 model using existing shared `Base`:

`AuditEventRow`

Table name exactly:

`audit_events`

No second Base.

Reuse the existing proven `UTCDateTime` convention from PHX-030.

## Exact audit_events columns

Database columns exactly:

- id
- event_kind
- occurred_at
- request_id
- actor_id
- tool_id
- action
- resource
- request_reason_hash
- risk_level
- permission_requirement
- permission_status
- permission_reason_code
- matched_grant_id
- matched_policy_rule_id
- user_decision_id
- task_id
- session_id
- correlation_id
- tool_result_status

No arbitrary metadata/blob/payload column.

No raw reason/content/stdout/stderr column.

## Database constraints

At minimum enforce with stable named constraints:

- primary key on id
- event_kind in exact two values
- risk_level in P0..P5
- permission_status in exact PHX-041 four values
- permission_reason_code in exact PHX-041 ten values
- request_reason_hash length/hex lowercase contract where SQLite can enforce sanely
- tool_result_status NULL or exact PHX-040 seven values
- permission_decision rows require tool_result_status IS NULL
- tool_outcome rows require tool_result_status IS NOT NULL
- tool_outcome rows require permission_status = 'allowed'
- tool_outcome rows require risk_level <> 'P5'
- tool_outcome rows require tool_result_status <> 'denied'

Domain revalidation remains authoritative for full structural consistency.

## Database indexes

Create exactly these ordinary indexes:

- `ix_audit_events_request_id` on request_id
- `ix_audit_events_tool_id` on tool_id
- `ix_audit_events_occurred_at` on occurred_at
- `ix_audit_events_risk_level` on risk_level

No FTS/vector index.

## Alembic revision

Create exactly:

`alembic/versions/0005_audit_events.py`

Revision:

`0005_audit_events`

Down revision:

`0004_embedding_records`

Do not modify historical migrations 0001–0004.

Upgrade creates only:

- audit_events table
- PHX-042 indexes
- append-only triggers

Downgrade removes only PHX-042 triggers/index/table.

## Historical migration evolution

PHX-042 legitimately advances Alembic head from 0004 to 0005.

Historical tests must continue proving:

- 0001 baseline only
- 0002 memories
- 0003 documents/chunks
- 0004 embedding_records
- 0005/head adds audit_events only

Where an older feature test means "that ticket's revision", explicitly target that historical revision rather than `head`.

Do not weaken earlier schema/constraint/UTC tests.

## Alembic environment registration

Update `alembic/env.py` narrowly to register `AuditEventRow` in shared metadata.

Import registration must remain side-effect free.

No automatic startup migration.

## Persistence transaction semantics

Each append operation uses one short session/transaction:

1. validate/correlate all evidence before session acquisition where practical
2. construct exact detached audit record
3. insert one row
4. commit
5. close session
6. return detached revalidated AuditRecord

On DB failure:

- rollback through normal session semantics
- close session
- raise a small safe audit error
- do not silently retry

## Error privacy

Audit service exceptions must not expose:

- request.reason
- resource
- SQL
- SQL parameters
- private DB paths
- tool description/schema
- credentials/secrets

No automatic logging of AuditRecord contents.

## No EventBus integration

PHX-042 implements durable audit persistence, not the real-time event bridge.

Do not publish or subscribe to EventBus.

Do not add `audit.*` events.

Canonical EventType remains exactly 32.

Future execution integration may emit existing permission/tool events separately.

## No tool execution

Do not:

- execute handlers
- open files
- spawn processes
- make network requests
- control desktop UI

PHX-042 records evidence only.

## No PermissionEngine mutation

Do not:

- modify PermissionEngine policy
- mint grants
- authenticate user decisions
- consume/revoke grants
- re-evaluate decisions inside AuditTrail

PHX-041 remains unchanged.

## P4/P5 safety

PHX-042 must never turn audit evidence into permission authority.

A stored P4 allowed decision proves only that a correlated PermissionDecision was supplied when auditing.

A stored audit row cannot authorize a later request.

No audit row may override P5.

## No concrete tools

PHX-043 filesystem and PHX-044 terminal remain TODO.

No path parsing, containment logic, shell quoting or subprocess handling in PHX-042.

## Secrets boundary

PHX-045 remains TODO.

No secret values, tokens, passwords or API keys belong in audit fixtures or audit-specific columns.

`request_reason_hash` is privacy minimization, not secret storage.

## No API/frontend

Do not add:

- FastAPI audit endpoints
- WebSocket audit stream
- Tauri commands
- React audit UI
- frontend DB access

## Side-effect isolation

Fresh-process/equivalent tests must prove importing/constructing:

- AuditEventKind
- AuditRecord
- AuditListQuery
- AuditTrail
- AuditEventRow

performs no:

- DB connection
- file/directory creation
- migration
- network
- subprocess
- thread/task
- model/provider call
- tool execution
- EventBus activity
- permission mutation

Only explicit temp-DB migration/tests and AuditTrail method calls may perform the documented persistence operations.

## Required focused tests

Create `tests/test_audit_trail.py`.

At minimum cover:

### Contracts

1. exact AuditEventKind values
2. exact AuditRecord 20 fields
3. frozen/extra-forbidden
4. audit IDs strict/default unique
5. aware UTC occurred_at
6. naive time rejected
7. exact request-reason SHA-256
8. source reason absent from record/schema
9. exact PHX-040/041 vocab reuse
10. forged/foreign enum rejection
11. subclass/injected-extra rejection
12. event-kind structural rules

### Correlation

13. exact ToolRegistry tool lookup
14. unknown tool explicit
15. caller cannot override risk
16. decision/request request-ID correlation
17. actor correlation
18. tool correlation
19. registry risk correlation
20. permission_requirement correlation
21. task/session preserved
22. correlation_id preserved/optional

### Permission audit

23. allowed decision append
24. approval_required append
25. denied append
26. blocked P5 append
27. tool_result_status absent
28. detached return

### Tool outcome audit

29. allowed P0 outcome can be recorded
30. allowed P3 outcome can be recorded
31. allowed P4 outcome can be recorded
32. P5 outcome rejected
33. non-allowed permission decision outcome rejected
34. ToolResultStatus.denied rejected
35. other six result statuses accepted
36. result status grants no success/verifier authority

### P3/P4 helper

37. P3 true
38. P4 true
39. P0/P1/P2/P5 false
40. foreign risk rejected

### Persistence

41. exact table name/columns
42. shared Base
43. UTC round-trip
44. exact constraints
45. exact indexes
46. duplicate audit ID conflict preserves original
47. repeated same request with different audit IDs allowed
48. raw SQL update blocked
49. ORM update blocked
50. raw SQL delete blocked
51. ORM delete blocked
52. original row unchanged after blocked mutation
53. get present
54. get absent
55. row-to-domain revalidation
56. corruption fails explicitly

### List

57. query frozen/extra-forbidden
58. exact filters
59. malformed filters rejected
60. bounds for limit/offset
61. deterministic occurred_at/id order
62. immutable tuple result
63. no wildcard/prefix behavior

### Migration

64. revision exactly 0005_audit_events
65. down_revision exactly 0004_embedding_records
66. historical migrations unchanged
67. 0004 lacks audit_events
68. 0005/head contains audit_events
69. exact columns
70. constraints
71. indexes
72. triggers
73. downgrade to 0004 removes table/triggers
74. re-upgrade restores them
75. no schema drift

### Separation/security

76. no EventBus dependency
77. EventType remains 32
78. no tool execution
79. no grant minting
80. no PermissionEngine mutation
81. no filesystem/terminal tool
82. no secrets implementation
83. no API/frontend
84. no model/provider
85. no request.reason persistence/logging
86. safe error messages
87. guarded import/construction side-effect free

Add deterministic cases beyond these where useful.

## Regression requirements

Run PHX-041:

`uv run --locked pytest tests/test_permission_engine.py`

Expected current baseline: 926 passed.

Run PHX-040:

`uv run --locked pytest tests/test_tool_registry.py`

Expected current baseline: 204 passed.

Run all Phase-3 focused suites and persistence.
Run all Phase-2 focused suites.
Run all Phase-1 focused suites.

No production behavior from previous phases may change.

## Alembic lifecycle

Use temporary SQLite only.

Verify:

`0001 -> 0002_memory_schema -> 0003_document_ingestion -> 0004_embedding_records -> 0005_audit_events`

At 0004:

- audit_events absent

At 0005/head:

- audit_events present
- append-only triggers present

Then:

`0005 -> 0004 -> 0005`

Existing memories/documents/document_chunks/embedding_records remain preserved.

Never touch the user's real PHOENIX database.

## Full verification

Run:

- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`

Dependencies expected: NONE.

Also run foundation regression and desktop:

- `npm test`
- `npm run typecheck --workspace @phoenix/desktop`
- `npm run build --workspace @phoenix/desktop`

## Security review

Confirm:

- no credentials/secrets
- no raw reason storage
- no stdout/stderr/tool payload storage
- no raw SQL/private DB path leakage
- append-only DB enforcement
- no P4 audit row creates reusable authority
- no P5 override
- no EventBus side effects
- no execution
- no eval/exec/dynamic import
- no direct frontend DB path

Run repository credential/signature checks.

## Invariant review

Review INV-001 through INV-024.

Especially:

- INV-003 — audit evidence grants no permission.
- INV-004 — no privileged tool bypass; PHX-042 executes nothing.
- INV-005 — P4 approval remains PHX-041 authority.
- INV-006 — P5 remains blocked.
- INV-007 — PHX-042 supplies durable P3/P4 audit primitives for future execution integration; do not falsely claim global enforcement before PHX-043/044 integration.
- INV-011 — no frontend direct persistence path.
- INV-017 — audit text remains untrusted.
- INV-018 — no secret logging/storage path.
- INV-020 — audit schema arrives only through Alembic 0005.
- INV-021 — malformed/corrupt audit evidence fails explicitly.

## ADR policy

At most one ADR.

A useful ADR-047 may record:

- append-only durable `audit_events`
- ToolRegistry + PermissionDecision correlation
- permission_decision/tool_outcome event kinds
- no raw request reason; exact SHA-256 reason hash
- P3/P4 execution-audit helper
- allowed tool-outcome only
- DB-level UPDATE/DELETE blocking triggers
- no EventBus vocabulary expansion
- no tool execution/PermissionEngine mutation
- Alembic 0005
- PHX-043/044 future integration requirement

## Acceptance criteria

1. Exact audit contracts implemented.
2. ToolRegistry/PermissionDecision correlation enforced.
3. P3/P4 audit-required helper exact.
4. Permission-decision append works for all decision states.
5. Tool-outcome append requires allowed non-P5 evidence.
6. No raw reason/tool payload persisted.
7. Audit rows are detached and exact-revalidated.
8. Durable duplicate conflict is explicit.
9. Public API exposes no mutation/delete path.
10. DB UPDATE/DELETE are blocked by triggers.
11. Admin get/list are deterministic/read-only.
12. `audit_events` schema/constraints/indexes exact.
13. Alembic 0005 lifecycle passes.
14. Historical migrations remain unchanged.
15. Canonical EventType remains 32; no EventBus integration.
16. PHX-040/041 regressions pass.
17. Phase-1/2/3 and persistence regressions pass.
18. Full Python/foundation/desktop quality gates pass.
19. Security/side-effect/invariant reviews pass.
20. No PHX-043/044/045 or API/frontend leakage.

## Completion gate

Only after every deterministic gate passes:

- change this ticket status from TODO to DONE
- append concise completion evidence
- update only PHX-042 in `FEATURE_TICKETS.md` from TODO to DONE
- leave PHX-043/044/045 TODO

Exact commit message:

`feat: complete PHX-042 audit trail`

Push only:

`feature/PHX-042-audit-trail`

Do not create a PR, merge, push main, force-push or start PHX-043.
