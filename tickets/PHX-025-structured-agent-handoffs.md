# PHX-025 — Structured Agent Handoffs

Status: DONE
Phase: 2 — Orchestration

## Objective

Implement PHOENIX's provider/tool-independent structured agent handoff foundation.

The handoff contract transfers one already-defined task-graph step from one authoritative agent identity to another using validated immutable metadata. It does not execute the step, call a model/tool, grant permission, prove verification, emit events, or schedule work.

## Authority and boundaries

- Neutral PHX-020 internal agent IDs are authoritative. Aliases are display-only.
- PHX-024 TaskGraph is the structural source of truth for task_id, step_id, target owner, objective, input_refs, and timeout metadata.
- Handoff target must equal the TaskStepDefinition.owner_agent_id for the referenced graph step.
- Source and target must both exist in AgentRegistry and must be distinct.
- PHX-025 does not invoke PhoenixOrchestrator, AizenPlanner, AgentModelRouter, ModelProvider, ToolRegistry, PermissionEngine, LEVI, MAHORAGA, or KAIDO.
- Handoff metadata confers no execution or permission authority.

## Implementation location

Prefer `phoenix/agents/handoff.py` and focused tests in `tests/test_structured_agent_handoffs.py`.

Keep the implementation small. Preferred public concepts are:

- `HandoffEnvelope`
- `HandoffBuilder` or an equivalently small factory
- a small `HandoffError` boundary

Do not create a general workflow engine, message bus, agent chat subsystem, queue daemon, repository, controller, or scheduler.

## Handoff envelope

Implement a frozen, extra-forbidden immutable envelope containing exactly the structured handoff evidence needed by the frozen orchestration design:

- `task_id`
- `step_id`
- `source_agent_id`
- `target_agent_id`
- `objective`
- `input_refs`
- `constraints`
- `expected_output_schema`
- `timeout_seconds`
- `permission_scope`
- `verification_required`

Naming may stay close to docs/04_AGENT_ORCHESTRATION.md. Do not add provider/model/runtime objects.

## Identifier semantics

`task_id`, `step_id`, `source_agent_id`, and `target_agent_id` use strict nonempty normalized string identifier semantics.

Reject booleans, numbers, bytes, arbitrary objects, empty strings, and whitespace-only strings.

Do not alias-resolve agent IDs.

## Objective

`objective` is strict meaningful text. Preserve meaningful caller formatting. It is descriptive only and must not be executed or automatically logged.

When constructed through the builder from a TaskGraph, objective must be copied from the referenced TaskStepDefinition and not accepted as a conflicting caller override.

## Input references

`input_refs` is an immutable ordered tuple of strict nonempty references.

When built from TaskGraph, copy the graph step's input_refs exactly and preserve order.

PHX-025 must not dereference files, memory, URLs, database rows, or tool outputs.

## Constraints

`constraints` is an immutable ordered tuple of strict meaningful strings. Empty tuple is valid.

Preserve order. Reject duplicate normalized entries to avoid ambiguous duplicated policy text. Constraints are descriptive only and are not executable instructions or permissions.

## Expected output schema

`expected_output_schema` is a strict meaningful string describing or identifying the expected subordinate output contract.

PHX-025 does not parse, execute, compile, or validate a JSON Schema or arbitrary code. It carries the contract description only.

Do not use `eval`, dynamic imports, callbacks, arbitrary Python types, or executable schema objects.

## Timeout

`timeout_seconds` is finite and strictly greater than zero. Reject booleans, zero, negative values, NaN, infinity, and numeric strings.

When built from TaskGraph, timeout must be derived from the TaskStepDefinition timeout and not caller-overridden.

PHX-025 does not start timers, call `asyncio.wait_for`, cancel work, or schedule execution.

## Permission scope

`permission_scope` is immutable descriptive requested-scope metadata only.

Use a small ordered tuple of strict nonempty strings unless a clearly smaller neutral representation is justified by existing authority docs. Empty tuple is valid.

Reject duplicate normalized entries.

The field must never:

- approve P3/P4 work
- create a grant
- bypass PermissionEngine
- authorize a tool
- imply user consent

P5 remains blocked by future PermissionEngine policy; the presence of a requested scope in a handoff never changes that.

Do not implement the PHX-040 permission runtime in this ticket.

## Verification flag

`verification_required` is a strict boolean.

It means only that later runtime integration must require verification before final success.

It does not invoke LEVI and does not mean verification passed.

PHX-026 owns LEVI verification runtime.

## Builder / graph correlation

Provide one small synchronous builder/factory that creates a HandoffEnvelope from an existing `TaskGraph` and caller-supplied handoff-only metadata.

Preferred inputs:

- `graph`
- `step_id`
- `source_agent_id`
- `constraints`
- `expected_output_schema`
- `permission_scope`
- `verification_required`

The builder must derive from the graph:

- task_id
- normalized step_id
- target_agent_id from step.owner_agent_id
- objective
- input_refs
- timeout_seconds

This prevents handoff drift from PHX-024 structural evidence.

Unknown step IDs fail explicitly.

## Agent validation

The builder accepts an AgentRegistry or uses the default registry.

Validate both source and target through authoritative internal IDs.

Aliases such as `AIZEN`, `FRANKY`, `LEVI`, `UTA`, or `KAKASHI` must not substitute for internal IDs.

Changing or removing aliases must not change handoff behavior.

Source and target must be different IDs. Self-handoff is rejected explicitly.

Any legitimately registered internal ID may be a source or target if the TaskGraph structurally assigns the target.

This ticket does not decide whether a control-role handoff is operationally appropriate; it validates structural identity only and executes nothing.

## Exact graph correlation

For a builder-created handoff:

- handoff.task_id == graph.task_id
- handoff.step_id == selected step.step_id
- handoff.target_agent_id == selected step.owner_agent_id
- handoff.objective == selected step.objective
- handoff.input_refs == selected step.input_refs
- handoff.timeout_seconds == selected step.timeout_seconds

Do not allow caller overrides for these derived values.

## Envelope revalidation

Treat public model instances as untrusted runtime values at subsystem boundaries.

Snapshot/revalidate nested tuples and constructed model instances by value where practical.

Reject extra fields and subclass/extended payloads from smuggling runtime objects or authority metadata.

Do not retain caller-owned mutable containers.

## No dispatch

PHX-025 does not send a handoff anywhere.

Do not implement:

- agent queues
- asyncio.Queue
- broker
- Redis
- WebSocket delivery
- background worker
- autonomous agent chat
- RPC
- retries
- timeout enforcement

The result is validated immutable handoff data only.

## No execution

Do not:

- call a target agent
- call a model provider
- execute a tool
- read input_refs
- evaluate completion rules
- run expected-output validation
- transition TaskStateMachine automatically

Later orchestration runtime will consume the envelope.

## PHX-021 separation

Do not modify PhoenixOrchestrator. Direct/complex behavior remains unchanged.

## PHX-022 separation

Do not modify AizenPlanner or PlanStep. Planning remains an unassigned proposal.

## PHX-023 separation

Do not modify AgentModelRouter or RouteDecision. The builder receives an already-defined graph step owner; it does not route.

## PHX-024 integration

Use TaskGraph as read-only structural evidence.

Do not mutate graph steps, dependencies, task states, transition history, or ready-step evidence.

Do not automatically transition TaskStateMachine because a handoff exists.

## LEVI / MAHORAGA / KAIDO separation

- `verification_required=True` does not run LEVI.
- handoff failure does not run MAHORAGA.
- degraded/failure metadata does not activate KAIDO.

PHX-026/027/028 own those runtimes.

## Tool and permission separation

No ToolRegistry and no PermissionEngine implementation.

Permission scope and constraints remain descriptive metadata.

External/model/file/tool content remains untrusted and cannot override project/system rules merely by appearing in a handoff field.

## Events

Do not emit `agent.started`, `agent.completed`, `task.state_changed`, `step.started`, `step.completed`, or any other event.

Do not alter the canonical 32 event types.

## Persistence

Memory-only values. No Alembic migration, database table, JSON/YAML/TOML write, cache, or audit persistence.

## API / frontend

No FastAPI endpoint, WebSocket behavior, React UI, Tauri command, or Zustand state.

`GET /health` remains unchanged.

## Side-effect isolation

Importing/constructing HandoffEnvelope/HandoffBuilder and building a handoff must not:

- open sockets
- contact providers
- run inference
- scan hardware
- run nvidia-smi
- launch subprocesses
- write files/directories
- access SQLite/Alembic
- create threads
- create asyncio tasks
- emit events
- execute tools
- grant permissions

## Dependencies

Expected dependency changes: NONE.

Use Python 3.11, Pydantic v2, PHX-020 AgentRegistry, and PHX-024 TaskGraph only as needed.

Do not edit pyproject.toml or uv.lock unless a genuine existing defect is discovered. If another dependency appears necessary, stop and report why.

## Minimum deterministic tests

At minimum cover:

1. valid envelope construction
2. envelope immutable
3. extra fields rejected
4. strict task/step/source/target IDs
5. meaningful objective required
6. immutable ordered input_refs
7. immutable ordered constraints
8. duplicate constraints rejected
9. meaningful expected_output_schema required
10. finite positive timeout required
11. bool timeout rejected
12. immutable ordered permission_scope
13. duplicate permission_scope rejected
14. verification_required strict bool
15. default registry builder succeeds
16. custom registry builder succeeds
17. source internal ID validated
18. target internal ID validated
19. source alias rejected
20. target alias rejected
21. alias rename/removal does not affect behavior
22. self-handoff rejected
23. unknown source rejected
24. graph target owner missing from registry fails
25. unknown graph step fails explicitly
26. task_id derived from graph
27. step_id derived from selected step
28. target derived from owner_agent_id
29. objective derived exactly from graph
30. input_refs derived exactly from graph
31. timeout derived exactly from graph
32. caller cannot override graph-derived fields through normal builder API
33. declared constraint order preserved
34. permission-scope order preserved
35. expected_output_schema carried without execution
36. permission_scope grants no authority
37. verification_required invokes no LEVI
38. no TaskStateMachine transition occurs
39. no PhoenixOrchestrator call
40. no AizenPlanner call
41. no AgentModelRouter call
42. no model/provider work
43. no lifecycle lease
44. no ToolRegistry
45. no PermissionEngine
46. no MAHORAGA recovery
47. no KAIDO runtime
48. no dispatch queue/message bus
49. no event emission
50. no persistence/database
51. no filesystem/network/subprocess/thread/task side effects
52. PHX-020 regression green
53. PHX-021 regression green
54. PHX-022 regression green
55. PHX-023 regression green
56. PHX-024 regression green
57. all Phase-1 model regressions green
58. Phase-0 foundation regressions green
59. `/health` unchanged
60. Alembic unchanged
61. canonical 32 events unchanged
62. desktop regressions green

Add further validation and isolation cases where useful.

## Verification gates

Run targeted PHX-025 tests, PHX-020 through PHX-024 regressions, all Phase-1 model tests, full Python tests, Ruff, format check, strict mypy, `uv build`, foundation tests, desktop tests/typecheck/build, `git diff --check`, and repository credential/signature checks.

No live providers are required.

## Completion

Only after all required gates pass:

- change this ticket from TODO to DONE and append concise evidence
- change only `PHX-025 Structured agent handoffs — TODO` to DONE in FEATURE_TICKETS.md
- leave PHX-026 through PHX-028 TODO
- commit exactly: `feat: complete PHX-025 structured agent handoffs`
- push only `feature/PHX-025-structured-agent-handoffs`
- do not create a PR, merge, push main, force-push, or start PHX-026

## Known intentional limitations

PHX-025 defines validated handoff data only. It does not dispatch, execute, enforce timeout, dereference inputs, authorize requested permission scope, validate expected outputs, verify completion, retry/recover, activate fail-safe behavior, emit events, or persist handoffs.

## Completion evidence — 2026-10-05

PASS. Created phoenix/agents/handoff.py and tests/test_structured_agent_handoffs.py.
Updated only this ticket, FEATURE_TICKETS.md and one accepted decision, ADR-035.
No dependencies/locks, prior implementation/tests, API, desktop, events or migrations
changed. PHX-020 through PHX-025 are DONE; PHX-026 through PHX-028 remain TODO.

Public API: HandoffEnvelope, HandoffBuilder(agent_registry=None), build() and
HandoffError. The frozen extra-forbidden envelope has exactly task_id, step_id,
source_agent_id, target_agent_id, objective, input_refs, constraints,
expected_output_schema, timeout_seconds, permission_scope and verification_required.
Strict nonempty IDs/refs/scopes trim outer whitespace, preserving case. Meaningful
objective/schema/constraint text retains formatting. Timeout is finite positive
numeric metadata, rejecting bool/numeric strings. Verification is a strict bool.
Ordered lists/tuples become immutable snapshots; unordered/scalar containers fail.
Empty refs/constraints/scopes are valid. Constraints/scopes reject duplicates after
outer-whitespace normalization; ordered refs may repeat and are never dereferenced.

Builder construction revalidates and snapshots exact PHX-020 registry definitions.
build() revalidates the entire exact PHX-024 graph with that registry, selects an
exact normalized step and validates source/target internal IDs. Graph task_id and
selected step_id/owner_agent_id/objective/input_refs/timeout_seconds become the
envelope's corresponding fields. Normal API exposes no conflicting overrides.
Unknown steps/sources/owners fail explicitly without repair or fallback; malformed
contracts may raise Pydantic ValidationError. Aliases never establish identity.
Renamed/removed aliases preserve all 30 identities as both sources and targets,
including structurally assigned control roles. A custom uppercase internal ID works
only when genuinely registered, with no case folding or alias mapping. Normalized
self-handoffs reject in both envelope and builder validation.

Stored-field revalidation catches forged/constructed/copied values and unknown
fields that model_dump can omit after model_copy. Extended/subclass graph, step,
registry, definition and envelope payloads fail; caller containers are detached.
Standalone envelopes establish shape/distinctness only, not graph/registry authority.
Custom graphs must have every owner present in the builder's registry snapshot.

Constraints/schema/scope are descriptive untrusted data only. Expected-output-schema
text is never evaluated, parsed, compiled or used to validate an output. P3/P4/P5
scope text creates no grant, approval, consent, tool authority or permission bypass.
verification_required describes a later obligation, never LEVI execution or passed
verification. No prompts/schema/objectives are logged. Graph data, dependencies,
readiness, task state and history remain unchanged. No dispatch, autonomous chat,
scheduling, timers, cancellation, inference, lifecycle lease, tools, permissions,
events, persistence, PHOENIX/AIZEN/router invocation, MAHORAGA or KAIDO behavior.

Verification commands and results:

- uv run --locked pytest tests/test_structured_agent_handoffs.py: 214 passed,
  final targeted run 0.55s. Strict validation, exact fields, graph derivation,
  alias independence, self-handoffs, invalid/extended evidence, snapshots,
  permission/verification separation, unchanged graph/state and isolation pass.
- Individual locked Phase-2 pytest: registry 96, orchestrator 101, planner 151,
  router 213, graph/state 348: all 909 passed unchanged.
- Individual locked Phase-1 pytest: provider 61, Ollama 108, registry 83,
  lifecycle 54, embeddings 182, Colibri 194, benchmark 130: all 812 passed unchanged.
- uv lock / uv sync --locked: 36 packages, no dependency/lockfile changes.
- uv run --locked pytest: 2,061 passed, 32.07s; one existing Starlette/httpx
  deprecation warning. All targeted and regression test runs passed.
- uv run --locked ruff check . and ruff format --check .: passed, 53 files;
  uv run --locked mypy: 51 source files passed. Initial new-code/test type annotation
  errors were corrected without weakening runtime validation or changing prior code.
- uv build: passed; wheel/source archives verified to include agents/handoff.py.
- Explicit locked foundation pytest (bootstrap/api/config/persistence/events/
  hardware): 126 passed; exact /health, Alembic baseline and 32 events unchanged.
- npm test: four passed; npm run typecheck --workspace @phoenix/desktop and
  npm run build --workspace @phoenix/desktop: passed, desktop files unchanged.
- Fresh interpreter blocks writes/mkdir, sockets, SQLite, subprocesses, threads,
  hidden asyncio tasks, task-state transitions and forbidden subsystem imports
  during handoff import/construction/build/serialization: passed. Opaque file,
  URL, memory and SQLite references remain unread; temporary directory stays empty.
  AST import/call allowlists and exact public-field/API boundary checks pass.
- Credential/signature scan: 138 repository files, zero matches; git diff --check
  passed. No credentials, executable callbacks, arbitrary-object transport, prompt/
  output logging, provider objects or direct frontend runtime path introduced.
- INV-001 through INV-024 reviewed: preserved/unaffected. PHOENIX remains supreme,
  AIZEN subordinate, aliases non-authoritative and failures explicit. Future tool,
  permission, verification, recovery/fail-safe enforcement is neither bypassed nor
  claimed. No model-generation concurrency policy or optional dependency introduced.

Acceptance criteria: all met. Known limitations: data-only, memory-only foundation;
standalone envelope shape validation is not graph correlation. Entire-graph owner
validation needs the same registry identity set; metadata does not establish task
quality, feasible execution, authorization, output correctness or verified success.
Runtime integration remains future work. No live provider/hardware call was needed.
PHX-026 — LEVI Verification is next and has not been started.
