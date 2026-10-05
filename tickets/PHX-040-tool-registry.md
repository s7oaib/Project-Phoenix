# PHX-040 — ToolRegistry

Status: DONE
Phase: 4 — Tools, Permissions & Security
Branch: `feature/PHX-040-tool-registry`

## Objective

Implement PHOENIX's first deterministic, immutable ToolRegistry metadata boundary without implementing PermissionEngine decisions, audit persistence, concrete filesystem/terminal tools, or privileged execution.

This ticket begins Phase 4 by defining what a PHOENIX tool *is* and how tool definitions are registered, validated, inspected, and resolved.

A tool is a deterministic capability. A tool is not an agent, model, permission grant, or free-form callback.

## Architectural position

Frozen authority requires:

`Agent -> ToolRegistry -> validate -> PermissionEngine -> execute -> structured result -> audit/event -> caller`

PHX-040 owns only the ToolRegistry definition/registration/resolution foundation at the left side of that flow.

PHX-041 owns deterministic P0-P5 authorization decisions.

PHX-042 owns the audit trail.

PHX-043 and PHX-044 own the first concrete privileged filesystem/terminal tool implementations.

Therefore PHX-040 MUST NOT execute a tool.

## Required production module

Prefer:

`phoenix/tools/registry.py`

and, if needed:

`phoenix/tools/__init__.py`

Focused tests:

`tests/test_tool_registry.py`

Do not create a persistence table, API endpoint, frontend surface, worker, or plugin loader.

## Required public concepts

Prefer this conceptual surface:

- `ToolRiskLevel`
- `ToolResultStatus`
- `ToolDefinition`
- `ToolRegistry`
- `ToolRegistryError`
- `DuplicateToolError`
- `UnknownToolError`

Minor naming changes are allowed only when equally explicit.

## Tool risk vocabulary

Tool metadata uses exactly the frozen P0-P5 risk vocabulary:

- `P0`
- `P1`
- `P2`
- `P3`
- `P4`
- `P5`

This is descriptive risk metadata only in PHX-040.

It MUST NOT:

- approve a tool call
- deny a tool call
- create a grant
- create consent
- bypass future PermissionEngine
- interpret P3/P4/P5 as executable authority

PHX-041 owns the deterministic authorization semantics.

Do not import the Phase-2 `PermissionRisk` enum merely to reuse text. Tool contracts must not depend on agent/model routing internals.

## Tool result-state vocabulary

Freeze exactly the documented result-state vocabulary for future concrete tool results:

- `success`
- `partial_success`
- `denied`
- `timeout`
- `cancelled`
- `failure`
- `unavailable_dependency`

PHX-040 does not fabricate result instances because no tool execution occurs.

## ToolDefinition contract

Implement one frozen, extra-forbidden, serializable `ToolDefinition` containing exactly these logical fields:

- `tool_id`
- `description`
- `input_schema`
- `output_schema`
- `risk_level`
- `permission_requirement`
- `timeout_seconds`
- `cancellation_supported`
- `audit_behavior`
- `supported_platforms`
- `supported_environments`

No implementation/callback/function/module/class/runtime object field belongs in this definition.

## Tool ID

`tool_id` must be:

- strict string
- nonempty after outer-whitespace normalization
- case-preserving
- exact identity authority

Reject:

- `None`
- bool
- int/float
- bytes
- arbitrary objects
- empty/whitespace-only strings

Tool IDs have no aliases in V1.

No display name may substitute for `tool_id`.

## Description

`description` must be:

- strict string
- meaningful/non-whitespace
- caller formatting preserved

It is descriptive data only.

It must never be interpreted as executable instructions or permission policy.

## Input/output schema evidence

`input_schema` and `output_schema` are JSON-compatible top-level objects.

Requirements:

- top level must be object/map
- string keys only
- nested values limited to JSON-compatible values
- finite numeric values only
- deep detached from caller containers
- exposed stored value cannot be mutated through caller-owned references
- serialization produces detached ordinary JSON-compatible data

Reject:

- bytes
- sets
- custom Python objects
- classes
- callables
- functions
- coroutines
- NaN
- Infinity
- cyclic containers

These schema objects are descriptive contract evidence in PHX-040.

PHX-040 MUST NOT evaluate `$ref`, execute schema content, dynamically import validators, or install a JSON-Schema engine.

Concrete tool request validation/execution integration remains later Phase-4 work.

## Risk level

`risk_level` must use exact `ToolRiskLevel` P0-P5 values.

No free-text risk values.

No automatic authorization behavior follows from the value in this ticket.

## Permission requirement

`permission_requirement` must be:

- strict nonempty normalized string
- descriptive metadata only

Do not freeze the PermissionEngine's future grant/approval state machine here.

PHX-041 will define how tool metadata, grants, user decisions, and P0-P5 policy interact.

A string such as `none`, `scoped`, or `explicit` is only caller-supplied metadata in PHX-040 and confers no authority.

## Timeout

`timeout_seconds` must be:

- ordinary int/float
- bool rejected
- finite
- strictly positive

PHX-040 stores the declared timeout but starts no timer and schedules no cancellation.

Timeout enforcement belongs to permissioned execution integration/concrete tools.

## Cancellation support

`cancellation_supported` is a strict boolean.

It describes capability only.

PHX-040 creates no asyncio task, cancellation token, worker, thread, or process.

## Audit behavior

`audit_behavior` must be:

- strict nonempty normalized string
- descriptive metadata only

PHX-040 performs no audit write.

PHX-042 owns authoritative audit semantics and persistence.

## Platform/environment support

`supported_platforms` and `supported_environments` must each be:

- immutable ordered tuples
- nonempty
- strict nonempty normalized strings
- duplicate-free
- case-preserving

Do not invent a closed OS/environment vocabulary in PHX-040.

Do not probe the live OS/hardware/environment.

Do not reject a definition because the current machine is not listed.

These are declaration metadata only.

## Exact stored-value revalidation

Treat supplied `ToolDefinition` instances as untrusted boundary values.

Registry construction must exact-revalidate stored fields rather than trusting type identity alone.

Reject:

- subclasses
- copied/injected extras
- malformed nested schema evidence
- forged enum values
- caller mutation after construction

Use the strict revalidation patterns established in earlier PHOENIX contracts.

## ToolRegistry contract

Implement an immutable registry over an ordered tuple of exact ToolDefinition values.

Construction:

- snapshots caller containers
- exact-revalidates every definition
- rejects duplicate normalized `tool_id`
- performs no I/O
- creates no background work

A default/empty registry is valid.

Do not pre-register speculative filesystem, terminal, browser, media, or computer-control tools in PHX-040.

Concrete tools arrive in later tickets.

## Duplicate ID semantics

Two definitions with the same normalized `tool_id` are invalid, even if all other metadata is identical.

Examples that conflict after normalization:

- `filesystem.read`
- `  filesystem.read  `

Case remains significant unless a future authority explicitly changes that rule.

Do not silently replace first/last registration.

## Registry lookup API

Provide deterministic operations equivalent to:

- `get(tool_id) -> ToolDefinition | None`
- `require(tool_id) -> ToolDefinition`
- `contains(tool_id) -> bool`
- `list_tools() -> tuple[ToolDefinition, ...]`
- optional immutable `tool_ids` property

Lookup IDs use the same strict normalized identifier contract.

`get()` returns `None` only for a valid but unknown ID.

Malformed IDs fail explicitly rather than being treated as missing.

`require()` raises explicit `UnknownToolError` for a valid unknown ID.

## Ordering

Registry listing order is the caller-declared registration order.

Do not sort automatically.

Do not use hash-map iteration as public order.

The same construction inputs must produce deterministic equivalent registry state.

## Immutability / no runtime mutation

The PHX-040 registry is immutable after construction.

Do not implement mutating:

- `register()`
- `unregister()`
- `replace()`
- hot-reload
- plugin discovery

A caller that wants a different set constructs a new registry.

This prevents hidden runtime capability expansion before permission/audit controls exist.

## No tool implementation storage

PHX-040 registry stores definitions only.

Do NOT store or expose:

- Python callables
- bound methods
- subprocess commands
- module paths for dynamic import
- class instances that execute actions
- shell templates
- browser contexts
- OS handles

The registry cannot be used to bypass later PermissionEngine by extracting a handler.

## No execution method

Do NOT implement:

- `execute()`
- `invoke()`
- `run()`
- `dispatch()`
- `call_tool()`

on ToolRegistry in PHX-040.

No tool code is executed in this ticket.

The later integration must preserve the frozen flow through deterministic PermissionEngine before privileged execution.

## PermissionEngine separation

PHX-041 remains TODO.

PHX-040 MUST NOT implement:

- permission decisions
- approval prompts
- user consent
- scoped grants
- persistent grants
- P3 auto-approval logic
- P4 confirmation logic
- P5 blocking logic
- permission cache

Risk/permission metadata creates no authorization.

## Audit separation

PHX-042 remains TODO.

PHX-040 MUST NOT:

- create audit tables
- write audit files
- emit audit records
- sign audit entries
- hide/modify audit history

`audit_behavior` is descriptive only.

## Concrete tool separation

PHX-043 filesystem tool and PHX-044 terminal tool remain TODO.

Do not implement:

- file read/write/delete
- directory traversal
- process execution
- shell execution
- app launch
- browser automation
- screenshot capture
- clipboard access
- computer input
- media integrations

Do not pre-register IDs for these future tools.

## Secrets separation

PHX-045 remains TODO.

Do not add:

- secret fields
- credential storage
- token storage
- API-key storage
- secret logging

Tool schema/description strings remain untrusted metadata and must not be logged automatically.

## Agent separation

Tools are not agents.

ToolRegistry must not:

- modify AgentRegistry
- infer an agent from a tool ID
- create a specialist process
- call PHOENIX/AIZEN/router/LEVI/MAHORAGA/KAIDO

Tool definitions contain no agent alias.

## Provider/model separation

Do not import or call:

- ModelProvider
- OllamaProvider
- ColibriProvider
- EmbeddingProvider
- ModelLifecycleManager
- AgentModelRouter

Tool metadata is provider-neutral.

## Events

The canonical event vocabulary already contains:

- `tool.requested`
- `tool.started`
- `tool.completed`
- `tool.failed`

PHX-040 emits NONE of them because there is no execution attempt.

Do not change the canonical 32 EventType values.

Future permissioned execution will use existing event types.

## Persistence

PHX-040 is memory-only metadata.

Do not:

- create an Alembic migration
- create a DB table
- write a state file
- auto-save registry definitions

Alembic head remains `0004_embedding_records`.

## API/frontend

Do not add:

- FastAPI tool endpoints
- WebSocket tool commands
- Tauri commands
- React tool UI
- Zustand tool state

Frontend never gains direct privileged capability access.

## Side-effect isolation

Fresh-process/equivalent tests must prove importing and constructing:

- ToolRiskLevel
- ToolResultStatus
- ToolDefinition
- ToolRegistry

and calling registry lookup/list operations does NOT:

- create/open DB
- create files/directories
- open sockets
- start subprocesses
- start threads
- create asyncio tasks
- probe OS/hardware
- dynamically import plugins
- call models/providers
- emit/subscribe events
- execute tools
- evaluate permissions
- write audit state

## Security requirements

- No `eval` / `exec`.
- No dynamic import from schema/metadata strings.
- No shell interpretation.
- No path/URL dereference.
- No callables in registry definitions.
- No hidden default tools.
- No runtime registry mutation.
- P0-P5 metadata never becomes grant authority.
- P4 metadata never means approval exists.
- P5 definitions may be represented as metadata but cannot execute because PHX-040 executes nothing.
- Schema/description metadata remains untrusted data.

## Dependency policy

Expected dependency changes: NONE.

Do not modify `pyproject.toml` or `uv.lock`.

Do not add a JSON-Schema package, plugin framework, or DI container.

## Required focused test suite

Create:

`tests/test_tool_registry.py`

At minimum prove all requirements below.

### Vocabulary

1. ToolRiskLevel contains exactly P0-P5.
2. ToolResultStatus contains exactly success/partial_success/denied/timeout/cancelled/failure/unavailable_dependency.
3. Unknown textual enum values fail.
4. Bool/int enum coercion fails.

### ToolDefinition identifiers/text

5. strict tool ID accepted.
6. outer whitespace tool ID normalized.
7. empty ID rejected.
8. non-string ID rejected.
9. case preserved.
10. meaningful description accepted and formatting preserved.
11. whitespace-only description rejected.

### Schema evidence

12. empty JSON object schema accepted.
13. nested JSON schema evidence accepted.
14. schemas detached from caller mutation.
15. nested returned structures cannot mutate stored definition.
16. string keys required.
17. bytes rejected.
18. set rejected.
19. callable rejected.
20. class/custom object rejected.
21. NaN rejected.
22. infinities rejected.
23. cyclic schema rejected explicitly.
24. serialization returns ordinary detached JSON-compatible data.

### Metadata fields

25. exact P0 through P5 accepted.
26. malformed risk rejected.
27. permission requirement meaningful/strict.
28. audit behavior meaningful/strict.
29. timeout positive int accepted and normalized consistently.
30. positive float accepted.
31. zero/negative rejected.
32. bool rejected.
33. NaN/inf rejected.
34. cancellation strict bool.
35. platforms ordered and immutable.
36. environments ordered and immutable.
37. empty platform/environment sequence rejected.
38. duplicate platform rejected.
39. duplicate environment rejected.
40. malformed entries rejected.

### Frozen/exact contracts

41. ToolDefinition frozen.
42. extra fields rejected.
43. subclasses rejected by registry.
44. model-copy injected extras rejected.
45. caller definition list detached.
46. nested schema caller mutation does not alter registry.

### Registry

47. empty registry valid.
48. source order preserved.
49. exact `get` present.
50. valid missing `get` returns None.
51. malformed lookup fails.
52. `require` present.
53. unknown `require` raises UnknownToolError.
54. contains true/false.
55. immutable tuple from list_tools.
56. optional tool_ids immutable/deterministic if implemented.
57. duplicate exact ID rejected.
58. duplicate normalized ID rejected.
59. duplicate rejection preserves no partially constructed registry.
60. case-distinct IDs remain distinct.
61. repeated construction produces equivalent output.
62. registry frozen/no public mutable map.
63. no runtime register/unregister/replace API.

### Separation / safety

64. no execute/invoke/run/dispatch/call_tool API.
65. no callable/handler field in ToolDefinition.
66. no PermissionEngine implementation/import.
67. no grant/approval object.
68. no audit implementation.
69. no concrete filesystem tool.
70. no concrete terminal tool.
71. no provider/model imports.
72. no AgentRegistry mutation/call.
73. no EventBus publication/subscription.
74. canonical EventType count remains 32.
75. Alembic head remains 0004_embedding_records.
76. no DB/schema changes.
77. no API/frontend changes.
78. no dependencies changed.
79. no import/construction/lookup side effects.
80. no eval/exec/dynamic plugin import.

Add deterministic boundary cases where useful.

## Regression requirements

Run the new focused suite and all existing foundation/Phase-1/Phase-2/Phase-3 regressions.

At minimum run individually:

`tests/test_tool_registry.py`

Phase 3:
- `tests/test_memory_schema.py`
- `tests/test_pudding_memory_service.py`
- `tests/test_document_ingestion.py`
- `tests/test_vector_store_sqlite.py`
- `tests/test_retrieval_context.py`
- `tests/test_provenance_citations.py`
- `tests/test_persistence.py`

Phase 2:
- `tests/test_agent_registry.py`
- `tests/test_phoenix_orchestrator.py`
- `tests/test_aizen_planner.py`
- `tests/test_agent_model_router.py`
- `tests/test_task_graph_state_machine.py`
- `tests/test_structured_agent_handoffs.py`
- `tests/test_levi_verification.py`
- `tests/test_mahoraga_recovery.py`
- `tests/test_kaido_failsafe.py`

Phase 1:
- `tests/test_model_provider.py`
- `tests/test_ollama_provider.py`
- `tests/test_model_registry.py`
- `tests/test_model_lifecycle.py`
- `tests/test_embeddings.py`
- `tests/test_colibri_provider.py`
- `tests/test_model_benchmark.py`

Foundation health/event/persistence/hardware suites must remain green.

Desktop tests/typecheck/build must remain green.

## Full quality gates

Run:

- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`
- `npm test`
- `npm run typecheck --workspace @phoenix/desktop`
- `npm run build --workspace @phoenix/desktop`

No dependency changes expected.

## Invariants

Review INV-001 through INV-024.

Especially:

- INV-001: ToolRegistry has no supreme orchestration authority.
- INV-003: tool/risk metadata cannot grant permission.
- INV-004: PHX-040 creates the mandatory registry boundary but executes no privileged call before PermissionEngine exists.
- INV-011: frontend gains no direct privileged tool path.
- INV-013: future tool implementation must not decide agent policy; PHX-040 definitions contain no agent policy.
- INV-017: schema/description/external-looking strings remain untrusted data.
- INV-018: no secret persistence/logging path is created.
- INV-020: no schema change occurs.
- INV-021: malformed definitions/lookups fail explicitly.
- INV-024: no optional plugin/tool framework becomes a required dependency.

## ADR policy

At most one ADR.

A useful ADR-045 may record:

- immutable metadata-only ToolRegistry foundation
- exact P0-P5 descriptive risk vocabulary
- exact seven future result states
- JSON-safe detached schema evidence
- exact tool IDs/no aliases
- deterministic ordered immutable registry
- no handlers/callables or runtime mutation
- no execution before PermissionEngine integration
- permission/audit strings are metadata only
- PHX-041 owns decisions, PHX-042 audit, PHX-043/044 concrete tools
- no events/persistence/API/frontend/dependencies

Do not create multiple ADRs.

## Acceptance criteria

PHX-040 is complete only if all are true:

1. Exact ToolRiskLevel P0-P5 exists.
2. Exact seven ToolResultStatus values exist.
3. ToolDefinition implements exactly the documented logical metadata fields.
4. ToolDefinition is frozen/extra-forbidden/exact-revalidated.
5. Schema evidence is JSON-safe, deeply detached, and never executed.
6. Tool IDs are strict, normalized, case-preserving, and alias-free.
7. Timeout/cancellation/platform/environment metadata contracts are enforced.
8. ToolRegistry is immutable and deterministic.
9. Duplicate normalized tool IDs fail explicitly.
10. Lookup/require/contains/list semantics are deterministic and strict.
11. No tool implementation/callback is stored or exposed.
12. No tool execution API is implemented.
13. No PermissionEngine/grant/approval logic is implemented.
14. No audit trail is implemented.
15. No concrete filesystem/terminal/other tool is implemented or pre-registered.
16. No events, persistence, schema, API, frontend, provider, or agent runtime integration is added.
17. Imports/construction/lookups are side-effect free.
18. No dependencies change and all security boundaries pass.
19. All focused/regression/full quality gates pass.
20. Ticket/index/ADR/diff/commit/push gates are satisfied exactly.

## Completion evidence

When complete, update this ticket from TODO to DONE and append evidence covering:

- created/modified files
- public API
- exact risk/status vocabularies
- ToolDefinition fields and validation
- schema JSON safety/detachment
- identifier semantics
- metadata semantics
- registry ordering/immutability
- duplicate policy
- lookup API
- no handlers/execution
- PermissionEngine separation
- audit separation
- concrete-tool separation
- events/schema/API/frontend/provider separation
- focused tests
- Phase-1/2/3 regressions
- full Python/foundation/desktop results
- dependency/security/invariant checks
- ADR or none
- staged diff/commit/push/main safety
- known limitations

Then change only:

`PHX-040 ToolRegistry — TODO`

to:

`PHX-040 ToolRegistry — DONE`

in `FEATURE_TICKETS.md`.

PHX-041 through PHX-045 remain TODO.

## Exact commit message

`feat: complete PHX-040 ToolRegistry`

Push only:

`feature/PHX-040-tool-registry`

Do not create a PR, merge, push main, force-push, or start PHX-041.

## Verified PHX-040 completion

Created `phoenix/tools/registry.py` and `tests/test_tool_registry.py`.
Supporting changes: this ticket, only PHX-040 in `FEATURE_TICKETS.md`, and
one accepted ADR-045 in `DECISIONS.md`. Existing tools package initializer,
historical production/tests, manifests, migrations and desktop remain unchanged.

Public API: ToolRiskLevel, ToolResultStatus, ToolDefinition, ToolRegistry,
ToolRegistryError, DuplicateToolError, UnknownToolError; registry get/require/
contains/list_tools and immutable tool_ids inspection. Risk vocabulary is exactly
P0..P5; future result vocabulary is success/partial_success/denied/timeout/
cancelled/failure/unavailable_dependency. No tool result is fabricated.

ToolDefinition has exactly the eleven documented fields and is frozen,
extra-forbidden and stored-value-revalidated. Tool IDs are strict, nonempty,
outer-trimmed, case-preserving and alias-free. Descriptions retain meaningful
original formatting. Permission/audit strings are strict nonempty normalized
descriptive data, including approved/hidden strings, with no runtime authority.
Timeouts are ordinary finite positive int/float values normalized to float;
cancellation is strict bool. Platform/environment declarations are ordered,
nonempty unique tuples of strict normalized case-preserving strings. No runtime
support probing, timers or cancellation work.

Schemas require top-level ordinary JSON objects with string keys, finite numbers
and JSON values. Validate and deeply detach maps into private read-only views
and arrays into tuples; accepted nested tuples serialize as JSON arrays. Fresh
serialization returns ordinary detached dict/list JSON data. Reject bytes, sets,
custom objects/containers, functions/classes/coroutines/generators, nonfinite
numbers and cycles. Shared acyclic data is valid; excessive nesting fails
explicitly. Schema strings/$ref/paths/URLs remain inert and are never evaluated,
dereferenced, imported or used as permission policy.

ToolRegistry is a frozen slotted value over a private tuple. Construction accepts
ordered list/tuple exact definitions, revalidates stored fields/extras/enums/schema
evidence, preserves declaration order and detaches caller containers. Duplicate
normalized IDs raise DuplicateToolError, with no first/last-wins or partial
published state; case-distinct IDs coexist. An empty/default registry has no
tools. Reinitializing a constructed registry is rejected. A new set requires a
new registry. No mutable public registry map, register/unregister/replace/reload/
discovery operation, handler/callback or execution API exists.

get/require/contains use the same strict normalized lookup ID contract. Valid
unknown IDs yield None/UnknownToolError/False; malformed IDs fail validation.
list_tools/tool_ids preserve declaration order as immutable tuples. Identical
inputs produce equivalent metadata. No aliases, default tools or hidden fallback.

Validation commands and results:

- `uv run --locked pytest tests/test_tool_registry.py -q`: **204 passed**,
  final focused run 0.53s. All strict contract, schema, lookup, immutability,
  duplicate and separation cases pass, including guarded uncached import.
- Individually ran all Phase-3 suites: PHX-030 **244**, PHX-031 **167**,
  PHX-032 **300**, PHX-033 **273**, PHX-034 **367**, PHX-035 **173** passed
  (**1524 total**), plus persistence **12 passed**. Historical tests unchanged.
- All seven Phase-1 suites individually: **812 passed**; all nine Phase-2
  suites individually: **2729 passed**. No model/agent/runtime changes.
- Final `uv run --locked pytest -q --junitxml=<temporary report>`:
  **5395 passed**, exit 0, 92.83s; exactly 5191 baseline + 204 new tests.
  Log/XML outside the repository. One existing Starlette/httpx warning.
- `uv run --locked ruff check .`: passed; `uv run --locked ruff format --check .`:
  **81 files** passed; `uv run --locked mypy`: **76 source files** passed.
- `uv lock`, `uv sync --locked`, `uv build`: passed; **36 packages**,
  manifests/lock unchanged; source distribution and wheel built.
- Foundation bootstrap/API/config/persistence/events/hardware command:
  **126 passed**, 3.55s, including unchanged exact `/health`.
- Desktop `npm test`: **4 passed**; desktop workspace typecheck/build passed.

Guarded uncached import/construction/lookups/serialization run in memory with
file/source access, directories, SQLite, sockets, subprocesses, threads, task
creation and OS probing blocked. Registry imports only data/validation libraries;
dependency imports are restricted to already loaded validation modules. Static
checks rule out eval/exec/dynamic plugin imports and runtime subsystem coupling.
No tool execution, PermissionEngine, grants/approval, audit implementation,
filesystem/terminal/concrete tools, secrets handling, agent/provider/model calls,
events, persistence, API/frontend, workers or dependency changes. PHX-041 through
PHX-045 remain TODO; no privileged integration begins in this ticket.

Alembic head stays `0004_embedding_records`; four revisions and env.py unchanged.
EventType remains **32**. Compared **100 historical protected files** with
origin/main: unchanged. Credential/signature scan: **176 files, zero matches**.
Reviewed INV-001 through INV-024 and security/permission implications: preserved.
Risk/permission metadata grants no authority; PHOENIX remains supreme; external
schema/description strings remain untrusted data. Acceptance criteria 1–19 pass.
DONE follows all deterministic gates. Complete staged review and exact normal
feature-branch commit/push evidence are reported in the final lifecycle report.

Limitations: metadata inspection only; no JSON-Schema request evaluation,
permission enforcement, timeout/cancellation execution or audit semantics.
Recursive schema validation rejects excessive nesting explicitly. Lookup is a
simple deterministic linear scan. PHX-041 PermissionEngine P0-P5 is not started.
