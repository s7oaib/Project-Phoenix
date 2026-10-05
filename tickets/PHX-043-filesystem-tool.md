# PHX-043 — Filesystem tool

Status: TODO

Phase: 4 — Tools, Permissions & Security

## Objective

Implement PHOENIX's first concrete filesystem capability as a tightly scoped, local, synchronous tool runtime that is forced through the existing PHX-040 `ToolRegistry`, PHX-041 `PermissionEngine`, and PHX-042 durable audit boundary.

The filesystem tool must support a deliberately small V1 operation set, must never escape a caller-configured trusted workspace root, must never execute shell commands, must never follow symbolic-link/reparse escapes, and must fail closed whenever required P3/P4 audit evidence cannot be persisted before execution.

PHX-043 is the first ticket that performs real privileged side effects. Therefore INV-004 and INV-007 become concrete runtime obligations for this tool, not merely future architecture notes.

This ticket does **not** implement terminal/process execution, broad computer control, secret-store integration, browser/network access, recursive deletion, arbitrary binary manipulation, or API/frontend exposure.

## Frozen authority

The following remain authoritative:

- A tool is a deterministic capability exposed through `ToolRegistry`; a tool is not an agent.
- Privileged tool calls pass through `ToolRegistry` and permission evaluation.
- P1 selected-file observation requires scoped authority.
- P2 low-risk/reversible actions may be allowed by policy.
- P3 file modification requires policy/scoped authority or explicit approval.
- P4 destructive actions always require explicit request-specific user approval.
- P5 is blocked.
- P3/P4 execution creates durable audit evidence.
- LLM/model/agent output cannot grant permission.
- External/file content is untrusted data.
- Security controls include schema validation, scoped paths, path-traversal prevention, permission checks, audit evidence, and least privilege.
- PHX-045 remains the dedicated secrets boundary. PHX-043 must not claim that arbitrary user-home/OS filesystem access is safe before PHX-045.

## Required files

Prefer:

- `phoenix/tools/filesystem.py`
- `tests/test_filesystem_tool.py`

Minimal changes may be made to:

- `phoenix/tools/__init__.py`
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- this ticket

Do not create:

- terminal/process runtime
- secrets store
- browser/network tool
- background filesystem watcher
- recursive indexer
- FastAPI/Tauri/React integration
- new persistence table
- new Alembic revision

## Existing contracts to reuse

Reuse exactly from PHX-040:

- `ToolDefinition`
- `ToolRegistry`
- `ToolRiskLevel`
- `ToolResultStatus`

Reuse exactly from PHX-041:

- `PermissionRequest`
- `PermissionDecision`
- `PermissionGrant`
- `PermissionPolicy`
- `UserPermissionDecision`
- `PermissionEngine`

Reuse exactly from PHX-042:

- `AuditTrail`
- `audit_required_for_execution`

Do not redefine any of these vocabularies.

## Canonical filesystem tool IDs

Implement exactly these seven V1 tool definitions, in this order:

1. `filesystem.read_text`
2. `filesystem.list_directory`
3. `filesystem.stat`
4. `filesystem.create_text`
5. `filesystem.create_directory`
6. `filesystem.write_text`
7. `filesystem.delete_file`

No aliases.
No automatic registration into a global mutable registry.
No hidden extra filesystem capability.

Provide a pure helper equivalent to:

```python
filesystem_tool_definitions() -> tuple[ToolDefinition, ...]
```

The helper performs no I/O and returns canonical immutable metadata.

## Risk mapping

Freeze the exact V1 risk mapping:

- `filesystem.read_text` -> P1
- `filesystem.list_directory` -> P1
- `filesystem.stat` -> P1
- `filesystem.create_text` -> P2
- `filesystem.create_directory` -> P2
- `filesystem.write_text` -> P3
- `filesystem.delete_file` -> P4

Rationale follows the frozen permission matrix:

- selected-file observation -> P1
- new safe output -> P2
- overwrite/edit -> P3
- deletion -> P4

No filesystem operation in PHX-043 is P0 or P5.

## Canonical ToolDefinition metadata

Every canonical filesystem definition must have deterministic metadata.

At minimum:

- exact tool ID
- meaningful description
- JSON-safe input schema
- JSON-safe output schema
- exact frozen risk above
- descriptive permission requirement
- positive bounded timeout metadata
- `cancellation_supported=False`
- descriptive audit behavior
- explicit supported platform/environment metadata

The concrete runtime must **not** interpret `permission_requirement` or `audit_behavior` text as authority. Authorization comes from PHX-041 and mandatory audit behavior comes from the tool risk plus PHX-042 helper.

A canonical definition mismatch is a construction error.

## Registry anti-downgrade rule — critical

`FilesystemTool` must receive a caller-supplied immutable `ToolRegistry`.

Construction must require all seven canonical filesystem IDs to exist and must verify that each registered filesystem definition exactly matches PHX-043's canonical definition, including risk.

Additional unrelated registry tools may exist.

Reject a registry that:

- omits a required filesystem tool
- changes P4 delete to P0/P1/P2/P3
- changes P3 overwrite to a lower risk
- changes any input/output schema
- changes permission/audit metadata
- changes timeout/cancellation/platform/environment declarations
- contains a forged definition

This prevents a caller/model from lowering risk metadata before PermissionEngine evaluation.

Do not mutate the supplied registry.

## Trusted root capability

`FilesystemTool` must be constructed with one explicit trusted workspace root.

The root is configuration supplied by trusted application integration, never by model/web/document text during a call.

Requirements:

- no default current-working-directory root
- no default user-home root
- no default filesystem/drive root
- root path must be an explicit absolute path
- constructor may perform lexical validation only
- constructor must not create the directory
- constructor must not open/read/write filesystem state
- every call is permanently scoped to that root
- a call cannot override/change the root

PHX-043 does not claim arbitrary host-filesystem safety. Wider/user-home/credential-store integration remains blocked until trusted higher-level policy, including PHX-045, explicitly permits it.

## Relative path contract

Each call uses a relative path beneath the configured root.

The relative path is untrusted text and must be validated before permission evaluation without touching the filesystem.

V1 path syntax is intentionally conservative and portable:

- strict nonempty string
- preserve ordinary filename case and spaces
- `/` is the only accepted separator in caller input
- reject NUL
- reject `\\`
- reject absolute paths
- reject drive prefixes
- reject UNC/device paths
- reject `:` to prevent Windows drive/ADS ambiguity
- reject `*`, `?`, `<`, `>`, `|`, `"`
- reject empty segments
- reject `..`
- reject embedded `.` segments
- reject Windows device-name segments case-insensitively (`CON`, `PRN`, `AUX`, `NUL`, `COM1`…`COM9`, `LPT1`…`LPT9`, including extension forms)
- reject segments ending in a dot or space

The single path `.` is allowed only for `list_directory` and `stat` to refer to the configured root itself.

Do not trim meaningful path whitespace into a different filename.

## Lexical intent before filesystem I/O

Before permission evaluation, derive a pure immutable intent from the call without touching filesystem state.

Provide an operation equivalent to:

```python
describe(call) -> FilesystemIntent
```

`FilesystemIntent` must contain at least:

- call ID
- canonical tool ID
- operation
- canonical action string
- canonical resource string
- risk level
- permission requirement

The resource must deterministically identify the exact configured root + validated relative target.

A recommended resource is the lexically normalized absolute target path derived from the trusted absolute root and validated relative segments.

Do not resolve symlinks or check existence during `describe()`.

## Operation vocabulary

Implement exactly:

- `read_text`
- `list_directory`
- `stat`
- `create_text`
- `create_directory`
- `write_text`
- `delete_file`

Prefer `FilesystemOperation(StrEnum)`.

Map operation -> canonical tool ID one-to-one.

## Filesystem call contract

Create frozen, extra-forbidden `FilesystemCall` with exactly these logical fields:

- `call_id`
- `operation`
- `relative_path`
- `content`
- `max_bytes`
- `max_entries`

`call_id`:

- strict nonempty normalized string
- independent UUID4 default acceptable
- caller-provided valid ID preserved

Operation-specific shape:

### read_text

- content = None
- max_entries = None
- max_bytes optional
- default effective max bytes = 1 MiB
- explicit max bytes must be strict integer 1..1 MiB

### list_directory

- content = None
- max_bytes = None
- max_entries optional
- default effective max entries = 200
- explicit max entries strict integer 1..1000

### stat

- content = None
- max_bytes = None
- max_entries = None

### create_text

- content required strict string
- max_bytes = None
- max_entries = None
- UTF-8 encoded content <= 1 MiB

### create_directory

- content = None
- max_bytes = None
- max_entries = None

### write_text

- content required strict string
- max_bytes = None
- max_entries = None
- UTF-8 encoded content <= 1 MiB

### delete_file

- content = None
- max_bytes = None
- max_entries = None

Reject contradictory field combinations.

## Permission request correlation — critical

`execute()` must accept an exact caller-supplied PHX-041 `PermissionRequest` because approval/grant workflows need a stable request ID across retries.

The filesystem runtime must derive its own `FilesystemIntent` and require exact equality:

- `permission_request.tool_id == intent.tool_id`
- `permission_request.action == intent.action`
- `permission_request.resource == intent.resource`

The caller cannot authorize one file and execute another.

The filesystem call itself contains no risk field and no approved flag.

PermissionRequest actor/reason/task/session/request timestamps remain PHX-041 evidence.

## Permission evaluation

Construct/use the existing PHX-041 `PermissionEngine` from the same validated registry snapshot and supplied immutable `PermissionPolicy`.

`execute()` accepts optional exact:

- grants
- user decision

Then calls PermissionEngine exactly once for one execution attempt.

No text, file content, filename, metadata, output, or model message may create approval.

No hidden fallback if PermissionEngine fails.

## Permission result behavior

If `PermissionDecision.status != allowed`:

- perform no target filesystem I/O
- return a structured result with `ToolResultStatus.DENIED`
- preserve the exact PermissionDecision
- do not fabricate a tool outcome

For P3/P4, mandatory permission-decision audit rules below still apply before returning.

For P4 delete:

- grants and policy can never authorize it because PHX-041 forbids that
- only exact request-specific explicit approval may allow execution

## Mandatory P3/P4 audit integration — critical

For any P3/P4 filesystem call, PHX-043 must use PHX-042 durable audit primitives.

Before any target filesystem I/O:

1. derive intent
2. validate/correlate PermissionRequest
3. evaluate PermissionEngine
4. call `AuditTrail.record_permission_decision(...)`
5. only if the required audit append succeeds may an allowed P3/P4 operation touch the filesystem

If the permission-decision audit append fails:

- fail closed
- do not execute the operation
- raise/return an explicit safe filesystem audit failure

For an allowed P3/P4 call that reaches a filesystem attempt:

- execute exactly one operation attempt
- derive structured ToolResultStatus
- append `AuditTrail.record_tool_outcome(...)`
- only after outcome audit succeeds may the runtime return its normal result

If outcome-audit persistence fails after the filesystem effect/attempt:

- never pretend the operation did not happen
- never return a normal success result
- raise an explicit safe post-execution audit error carrying only minimal nonsecret evidence such as call ID and `effect_may_have_occurred=True`
- do not automatically retry the filesystem operation

This rule is essential because filesystem and SQLite audit storage are not one atomic transaction.

## P1/P2 audit scope

PHX-043 does not require durable audit rows for P1/P2 calls.

Do not parse ToolDefinition.audit_behavior strings to invent policy.

Future observability integration may broaden auditing separately.

## No filesystem I/O before permission

After call/path/contract validation, no root/target existence, stat, read, listing, symlink resolution, or mutation may occur until PermissionEngine has returned an allowed decision, except the required P3/P4 permission audit itself which is database-only.

This prevents unauthorized callers from using the tool as a filesystem probing side channel.

## Execution-time root validation

After permission is allowed, resolve the trusted root strictly.

Require:

- root exists
- root is a directory
- resolved target remains within the resolved root

For non-existing create targets, resolve and validate the existing parent.

If the root is unavailable, return a safe structured failure; do not expose raw OS error text.

## Symlink / reparse safety

PHX-043 must not follow caller-reachable symbolic-link/reparse escapes.

At execution time:

- reject any existing target that is a symlink
- reject any existing intermediate component beneath root that is a symlink
- compare resolved existing parent/target paths against resolved root containment
- never read/write/delete through a symlink
- list may report a child entry as `symlink` but must not dereference it

Tests must cover symlink escape attempts where the platform supports symlinks.

Document the remaining V1 limitation that hostile concurrent path replacement/TOCTOU attacks are outside the single-user local threat model; do not claim race-proof openat-style isolation on every platform.

## read_text semantics

`filesystem.read_text`:

- P1
- target must be an existing regular non-symlink file
- no directory reads
- no binary mode
- read at most effective `max_bytes`
- if file size exceeds bound, fail before reading full content
- decode UTF-8 strictly
- preserve content exactly
- invalid UTF-8 -> safe structured failure
- no logging/persistence/memory insertion of content

Returned file text is untrusted data and has no instruction authority.

## list_directory semantics

`filesystem.list_directory`:

- P1
- target must be an existing directory
- nonrecursive
- do not follow child symlinks
- deterministic child ordering by exact name ascending
- return at most effective max entries
- if more entries exist, return the first bounded entries with `partial_success` and `truncated=True`
- each entry exposes only safe metadata: name, kind, and size where available
- no child file content

## stat semantics

`filesystem.stat`:

- P1
- lstat/metadata only after permission
- do not follow symlink target
- return safe structured metadata such as kind, size and UTC modified timestamp
- no content

A symlink target itself must not be dereferenced; the tool may either report kind `symlink` for stat or reject according to one consistent tested policy. Mutation operations must always reject symlinks.

## create_text semantics

`filesystem.create_text`:

- P2
- parent must already exist inside root and be a normal directory
- target must not already exist
- no implicit parent creation
- no symlink target
- exclusive create semantics
- encode UTF-8 exactly
- bounded to 1 MiB
- on conflict return safe `already_exists` failure
- never overwrite

## create_directory semantics

`filesystem.create_directory`:

- P2
- parent must already exist inside root
- one directory only
- no recursive `parents=True`
- target must not already exist
- no symlink target

## write_text semantics

`filesystem.write_text`:

- P3
- target must already exist
- target must be a regular non-symlink file
- UTF-8 content <= 1 MiB
- overwrite exact file only
- no append mode in V1
- prefer same-directory temporary write + `os.replace`/equivalent so a normal successful write does not leave partial content
- cleanup temporary artifact on failure where possible
- preserve no hidden backup copy

Permission audit must succeed before any temp file/write is created.

## delete_file semantics

`filesystem.delete_file`:

- P4
- exact request-specific explicit user approval required via PHX-041
- target must exist
- target must be a regular non-symlink file
- no directory deletion
- no recursive deletion
- one unlink attempt only
- no trash/recycle-bin fallback

Permission audit must succeed before unlink.

## Explicit non-goals

Do not implement:

- recursive delete
- directory delete
- copy
- move
- rename
- append
- chmod/chown/ACL
- symlink creation
- hardlink creation
- globbing
- filesystem search
- watch/monitor
- binary read/write
- archive extraction
- network filesystem discovery
- shell expansion
- environment-variable expansion in paths
- `~` expansion
- terminal commands
- Git commands

## Result error vocabulary

Implement a small closed safe error-code vocabulary, preferably:

- `not_found`
- `already_exists`
- `not_file`
- `not_directory`
- `invalid_utf8`
- `limit_exceeded`
- `path_escape`
- `symlink_forbidden`
- `root_unavailable`
- `io_failure`

Do not expose raw `OSError`, `errno` text, full exception repr, stack trace, or private path beyond the already-authorized canonical resource.

## Filesystem result contract

Create frozen, extra-forbidden `FilesystemToolResult` containing at least:

- call_id
- tool_id
- operation
- resource
- status (`ToolResultStatus`)
- attempted (strict bool)
- effect_applied (strict bool)
- permission_decision
- permission_audit_id
- outcome_audit_id
- output
- error_code

`output` must be a deeply detached immutable JSON-safe object.

Direct contradictory result construction must fail.

Required structural examples:

- permission not allowed -> status denied, attempted false, effect_applied false, output empty/None
- read/list/stat success -> attempted true, effect_applied false
- create/write/delete success -> attempted true, effect_applied true
- normal execution failure -> attempted true, effect_applied false unless an operation-specific effect truly occurred
- P3/P4 normal result -> permission audit ID required
- P3/P4 allowed attempted normal result -> outcome audit ID required
- P1/P2 -> audit IDs absent

Do not claim verification or final PHOENIX task success.

## Safe output shapes

Use deterministic JSON-safe output.

Recommended:

### read_text

- content
- size_bytes

### list_directory

- entries: ordered array of {name, kind, size_bytes}
- truncated

### stat

- kind
- size_bytes
- modified_at UTC ISO string

### create_text / write_text

- bytes_written

### create_directory

- created = true

### delete_file

- deleted = true

No stdout/stderr.
No arbitrary metadata.
No hidden OS object.

## Tool content trust boundary

File names, file content and metadata are untrusted data.

The runtime must never:

- execute file content
- treat file content as a system instruction
- import Python from a file
- execute scripts
- parse copied commands as authority
- auto-forward content to a model
- auto-store content in memory/RAG
- auto-log file content

## Secret boundary separation

PHX-045 remains TODO.

PHX-043 must:

- never log file content
- never write file content into audit rows
- never place file content into normal memory automatically
- never expose a default broad user-home/OS root
- document that trusted application integration must configure a narrow non-secret workspace root until PHX-045 provides dedicated secret-reference/storage controls

Do not add an ad-hoc secret scanner or pretend filename heuristics provide a complete secret boundary.

## Synchronous execution / timeout / cancellation

PHX-043 is synchronous.

Canonical filesystem definitions use `cancellation_supported=False`.

Do not create hidden threads/tasks/processes merely to enforce timeout metadata.

Bounded read/write/list limits are the V1 resource-safety mechanism; the caller/orchestrator owns outer deadlines.

Do not weaken timeout metadata into execution authority.

## No EventBus integration

Do not publish or subscribe to EventBus.

Do not change canonical EventType vocabulary.

EventType remains exactly 32.

PHX-042 durable audit is the only required PHX-043 observability integration.

## No new persistence / migration

PHX-043 adds no database table.

Alembic head remains:

`0005_audit_events`

Do not modify migrations 0001–0005.

Do not alter AuditTrail schema.

## No API / frontend

Do not add FastAPI filesystem routes, WebSocket commands, Tauri commands, React components, Zustand stores, or direct frontend filesystem access.

Frontend must not gain a privileged bypass.

## No agent/model coupling

FilesystemTool is not KAKASHI itself.

Do not import/call:

- PhoenixOrchestrator
- AizenPlanner
- AgentModelRouter
- LEVI
- MAHORAGA
- KAIDO
- ModelProvider
- Ollama
- Colibri
- EmbeddingProvider

No model chooses or lowers filesystem risk.

## Side-effect isolation

Fresh-process/equivalent tests must prove importing and constructing:

- filesystem tool definitions
- FilesystemCall
- FilesystemIntent
- FilesystemToolResult
- FilesystemTool

performs no target filesystem reads/writes, no DB connection, no migration, no EventBus activity, no network, no subprocess, no thread/task creation, and no provider/model call.

Pure `describe()` performs no filesystem or DB I/O.

Only explicit `execute()` after permission may touch the target filesystem; P3/P4 audit operations may touch only the audit DB at the prescribed points.

## Required focused test file

Create:

`tests/test_filesystem_tool.py`

Use temporary directories/files and temporary SQLite migrated through 0005.

No real user files.
No home directory mutation.
No network.
No subprocess.
No arbitrary sleeps.

## Minimum contract tests

Prove at least:

1. exact seven operations
2. exact seven tool IDs
3. exact risk mapping
4. canonical definition order
5. canonical definitions deterministic
6. definitions contain no handlers/callbacks
7. registry missing tool rejected
8. registry risk downgrade rejected
9. registry schema drift rejected
10. extra unrelated tools allowed
11. call frozen/extra-forbidden
12. independent call IDs
13. operation-specific field validation
14. content byte bound
15. read max-byte bound
16. list max-entry bound
17. contradictory call shapes rejected
18. intent frozen
19. result frozen
20. result contradictions rejected

## Minimum path-safety tests

Prove:

21. absolute path rejected
22. `..` rejected
23. embedded dot segment rejected
24. backslash rejected
25. colon rejected
26. NUL rejected
27. wildcard/device characters rejected
28. Windows device name rejected case-insensitively
29. device name with extension rejected
30. trailing dot rejected
31. trailing space rejected
32. `.` allowed only list/stat
33. no `~` expansion
34. no environment-variable expansion
35. canonical resource deterministic
36. call cannot change trusted root
37. target traversal cannot leave root
38. symlink target mutation rejected
39. symlink parent escape rejected where supported
40. list does not dereference child symlink

## Minimum permission tests

Prove:

41. describe performs no filesystem I/O
42. no target I/O before permission allowed
43. permission request tool mismatch rejected
44. action mismatch rejected
45. resource mismatch rejected
46. PermissionEngine called exactly once per execution attempt
47. P1 without scope authority does not read
48. P1 exact grant/approval can read
49. P2 policy can create safe output
50. P3 approval/policy/grant semantics come from PHX-041 unchanged
51. P3 once/persistent grant cannot authorize because PHX-041 forbids it
52. P4 grant cannot authorize delete
53. P4 policy cannot authorize delete
54. P4 wrong-request approval cannot authorize delete
55. exact P4 request-specific approval can delete
56. permission metadata text cannot self-authorize
57. file content cannot self-authorize

## Minimum audit tests

Prove:

58. P3 permission decision audited before any write
59. P4 permission decision audited before any delete
60. P3/P4 denied/approval-required decision audit has no outcome row
61. required permission-audit failure prevents write
62. required permission-audit failure prevents delete
63. successful P3 write records outcome
64. failed P3 attempt records failure outcome
65. successful P4 delete records outcome
66. normal P3/P4 result carries both required audit IDs
67. P1/P2 create no mandatory audit rows
68. P4 audit row does not authorize a later request
69. P5 authority remains unreachable/blocked
70. post-effect outcome-audit failure is explicit and never returns normal success
71. post-effect audit failure does not retry filesystem mutation

## Minimum read/list/stat tests

Prove:

72. exact UTF-8 read preserved
73. empty text file readable
74. oversized file rejected before full content return
75. invalid UTF-8 safe failure
76. directory cannot read as text
77. list sorted by exact name
78. list nonrecursive
79. list max bound partial_success observable
80. stat file
81. stat directory/root
82. stat timestamp UTC
83. read/list/stat results do not mutate filesystem

## Minimum create/write/delete tests

Prove:

84. create_text creates absent file only
85. create_text existing target does not overwrite
86. create_text parent missing fails
87. create_directory creates one level only
88. create_directory existing target fails
89. create_directory parent missing fails
90. write_text requires existing regular file
91. write_text exact content preserved
92. write_text bounded to 1 MiB
93. write_text does not leave temp artifact after normal success
94. failed write preserves original where atomic replacement can guarantee it
95. delete_file removes one regular file
96. delete_file missing target safe failure
97. delete_file rejects directory
98. delete_file rejects symlink
99. no recursive deletion API
100. no append/copy/move/rename/chmod API

## Minimum privacy/separation tests

Prove:

101. file content absent from audit rows
102. file content absent from errors/logging path
103. raw OSError text suppressed
104. no shell/subprocess import/runtime path
105. no terminal integration
106. no secret-store implementation
107. no EventBus integration
108. EventType remains 32
109. no model/provider calls
110. no AgentRegistry/orchestrator calls
111. no API/frontend changes
112. no migration 0006
113. Alembic head remains 0005_audit_events
114. imports/construction side-effect free

Add more deterministic adversarial cases where useful.

## PHX-042 regression

Run:

`uv run --locked pytest tests/test_audit_trail.py`

Existing baseline:

302 passed

Do not modify PHX-042 production behavior.

## PHX-041 regression

Run:

`uv run --locked pytest tests/test_permission_engine.py`

Existing baseline:

926 passed

Do not modify PHX-041 production behavior.

## PHX-040 regression

Run:

`uv run --locked pytest tests/test_tool_registry.py`

Existing baseline:

204 passed

Do not modify PHX-040 production behavior.

## Phase-3 regression

Run individually:

- `tests/test_memory_schema.py` — 244
- `tests/test_pudding_memory_service.py` — 167
- `tests/test_document_ingestion.py` — 300
- `tests/test_vector_store_sqlite.py` — 273
- `tests/test_retrieval_context.py` — 367
- `tests/test_provenance_citations.py` — 173
- `tests/test_persistence.py` — 12

All PASS unchanged.

## Phase-2 regression

Run all nine existing Phase-2 focused suites individually.

Existing aggregate baseline:

2,729 passed

## Phase-1 regression

Run all seven existing Phase-1 focused suites individually.

Existing aggregate baseline:

812 passed

## Full Python quality gate

Run:

```text
uv lock
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv build
```

All must pass.

Pre-PHX-043 full Python baseline:

6,623 passed

Expected dependency changes:

NONE.

`pyproject.toml` and `uv.lock` must remain unchanged.

## Foundation regression

Run/confirm bootstrap, API, configuration, persistence, events/event bus and hardware suites.

Existing aggregate baseline:

126 passed

Confirm exact `/health` unchanged.

Confirm:

- Alembic head = `0005_audit_events`
- EventType count = 32

## Desktop regression

Run:

```text
npm test
npm run typecheck --workspace @phoenix/desktop
npm run build --workspace @phoenix/desktop
```

Existing npm baseline:

4 tests

Desktop files remain unchanged.

## Security review

Confirm:

- no path traversal outside trusted root
- no symlink/reparse escape in supported deterministic cases
- no absolute/drive/UNC/ADS input path
- no shell expansion
- no subprocess
- no eval/exec
- no dynamic import from file content
- no risk downgrade via registry metadata
- no caller-controlled risk
- no P4 grant/policy bypass
- no target I/O before permission
- no P3/P4 execution before permission audit durability
- no raw reason/file content/tool payload in audit
- no raw OS error leakage
- no broad default root
- no automatic model/memory/log forwarding
- no secret fixtures
- no credential/token additions

Run repository credential/signature scan.

## Invariant review

Review INV-001 through INV-024.

Especially:

### INV-003

File/model content cannot grant permission.

### INV-004

Every filesystem action passes through exact ToolRegistry metadata and PHX-041 permission evaluation before target I/O.

### INV-005

Delete remains P4 and requires request-specific explicit confirmation.

### INV-006

No mechanism may bypass P5 or use audit evidence as authority.

### INV-007

For this concrete filesystem runtime, P3/P4 permission audit must persist before target execution and allowed attempts must append outcome evidence. Post-effect audit-storage failure remains explicit rather than falsely hidden.

### INV-011

Frontend gains no direct filesystem path.

### INV-013

Filesystem tool implementation contains no agent routing/policy.

### INV-017

File content and metadata remain untrusted data.

### INV-018

No file content enters logs/memory automatically; broader secrets isolation remains PHX-045.

### INV-019

Operations are deliberately bounded and synchronous; no hidden uncancellable worker is created.

### INV-020

No schema change in PHX-043.

### INV-021

Filesystem and audit failures are explicit; no fabricated success.

### INV-024

No new optional dependency becomes required.

## ADR policy

At most one ADR.

If useful, ADR-048 may record:

- seven canonical filesystem tool definitions and exact risk mapping
- immutable registry anti-downgrade verification
- explicit trusted-root capability with conservative relative-path grammar
- no target I/O before permission
- P3/P4 audit-before-side-effect requirement
- post-effect audit-failure semantics
- bounded UTF-8 text-only read/write
- nonrecursive list/create/delete scope
- symlink/reparse escape rejection
- no terminal/model/EventBus/API/frontend/schema/dependency integration
- PHX-045 secret-boundary deferral

## Completion gate

ONLY after every deterministic gate passes:

Change this ticket:

`TODO -> DONE`

Append concise completion evidence including:

- files
- public API
- seven canonical tool definitions
- exact risk mapping
- registry anti-downgrade evidence
- trusted-root/path grammar
- intent/resource correlation
- permission evaluation
- P4 delete behavior
- P3/P4 audit-before-I/O evidence
- post-effect audit-failure behavior
- read/list/stat semantics
- create/write/delete semantics
- symlink/traversal controls
- result/error contracts
- content privacy/trust boundary
- secret-boundary limitation
- targeted test count
- PHX-040/041/042 regressions
- Phase-1/2/3 results
- full Python result
- foundation/desktop results
- Alembic/EventType/dependency status
- security/invariants
- ADR or none
- known limitations

Then update `FEATURE_TICKETS.md`:

`PHX-043 Filesystem tool — TODO`

to:

`PHX-043 Filesystem tool — DONE`

Leave:

- PHX-044 Terminal tool — TODO
- PHX-045 Secrets boundary — TODO

## Expected implementation scope

Likely net implementation diff:

- `phoenix/tools/filesystem.py`
- `tests/test_filesystem_tool.py`
- optional small `phoenix/tools/__init__.py`
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- this ticket

No expected modifications to:

- `phoenix/tools/registry.py`
- `phoenix/security/permissions.py`
- `phoenix/security/audit.py`
- persistence models/migrations
- agent/model/RAG/memory modules
- EventBus/EventType
- API/frontend

If an existing core implementation must change to satisfy PHX-043, stop and justify the blocker rather than silently broadening scope.

## Commit and push

Only after all gates pass:

```text
git add -A
git diff --cached --check
git diff --cached
git commit -m "feat: complete PHX-043 filesystem tool"
git push origin feature/PHX-043-filesystem-tool
```

Do not amend history.
Do not force-push.
Do not create a PR.
Do not merge.
Do not start PHX-044.

## Acceptance criteria

PHX-043 is DONE only if all are true:

1. Seven canonical filesystem tools and exact risks are implemented.
2. Registry risk/schema downgrade is rejected before runtime use.
3. Explicit absolute trusted root is required; no broad default exists.
4. Relative path grammar blocks traversal/drive/UNC/ADS/device ambiguity.
5. Permission intent/resource correlation is exact.
6. No target filesystem I/O occurs before an allowed PermissionDecision.
7. P4 delete can only execute with exact request-specific approval.
8. P3/P4 permission audit is durable before target execution.
9. P3/P4 allowed attempts append durable outcome evidence.
10. Required pre-execution audit failure fails closed.
11. Post-effect audit failure is explicit and does not fabricate/redo execution.
12. Reads/lists/stats are bounded and untrusted.
13. Creates/overwrites/deletes obey exact nonrecursive semantics.
14. Symlink/reparse escape paths are rejected in deterministic supported cases.
15. No shell/process/model/EventBus/API/frontend/secret-store integration is added.
16. No new migration/dependency is added; head remains 0005 and EventType 32.
17. Focused and full regressions pass.
18. Security/privacy/invariant checks pass.
19. Ticket and feature index are updated only after verification.
20. Exact required commit is pushed only to the PHX-043 feature branch.
