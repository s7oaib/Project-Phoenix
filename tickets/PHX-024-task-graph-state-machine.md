# PHX-024 — Task Graph / State Machine

Status: TODO

Phase: 2 — Orchestration

## Purpose

Implement PHOENIX's provider/tool-independent in-memory task graph and canonical task-state machine foundation.

This ticket owns structural task/step graph validation and legal task-state transitions only. It does not execute agents/models/tools, create PHX-025 handoff envelopes, perform LEVI verification, MAHORAGA recovery, KAIDO fail-safe behavior, permission evaluation, persistence, API/UI wiring, or event production.

## Authority

Read and obey repository authority in this order:

1. `AGENTS.md`
2. `RULES.md`
3. `MENTAL_MODEL.md`
4. `INVARIANTS.md`
5. `MASTER_SPEC.md`
6. `PRD.md`
7. accepted `DECISIONS.md`
8. `docs/01_ARCHITECTURE.md`
9. `docs/02_SYSTEM_FLOW.md`
10. `docs/03_AGENT_ROSTER.md`
11. `docs/04_AGENT_ORCHESTRATION.md`
12. `docs/08_PERMISSION_MATRIX.md`
13. `docs/09_SECURITY.md`
14. `docs/14_EVENT_SYSTEM.md`
15. `docs/23_OBSERVABILITY.md`
16. completed PHX-020 through PHX-023 tickets/code
17. this ticket
18. tests/code

If a material conflict exists, stop and report it. Do not invent a resolution.

## Canonical task states

Implement exactly these task states:

- `CREATED`
- `PLANNING`
- `READY`
- `RUNNING`
- `WAITING_PERMISSION`
- `VERIFYING`
- `COMPLETED`
- `FAILED`
- `RECOVERING`
- `DEGRADED`
- `CANCELLED`

Use stable serialized values consistent with existing project style, preferably lowercase strings.

Do not add extra task states.

## Transition policy

The primary lifecycle from `docs/04_AGENT_ORCHESTRATION.md` is:

`CREATED -> PLANNING -> READY -> RUNNING -> WAITING_PERMISSION -> VERIFYING -> COMPLETED`

The state machine must support legitimate bypass/resume/failure paths without implementing the subsystems that cause them.

Required legal transitions:

- `CREATED -> PLANNING`
- `CREATED -> CANCELLED`
- `PLANNING -> READY`
- `PLANNING -> FAILED`
- `PLANNING -> CANCELLED`
- `READY -> RUNNING`
- `READY -> CANCELLED`
- `RUNNING -> WAITING_PERMISSION`
- `RUNNING -> VERIFYING`
- `RUNNING -> FAILED`
- `RUNNING -> CANCELLED`
- `WAITING_PERMISSION -> RUNNING`
- `WAITING_PERMISSION -> FAILED`
- `WAITING_PERMISSION -> CANCELLED`
- `VERIFYING -> COMPLETED`
- `VERIFYING -> FAILED`
- `VERIFYING -> RECOVERING`
- `VERIFYING -> CANCELLED`
- `FAILED -> RECOVERING`
- `FAILED -> DEGRADED`
- `FAILED -> CANCELLED`
- `RECOVERING -> PLANNING`
- `RECOVERING -> READY`
- `RECOVERING -> RUNNING`
- `RECOVERING -> FAILED`
- `RECOVERING -> DEGRADED`
- `RECOVERING -> CANCELLED`

`COMPLETED`, `DEGRADED`, and `CANCELLED` are terminal in PHX-024.

PHX-028 may later extend degraded-mode runtime behavior under its own authority; PHX-024 must not implement KAIDO behavior now.

Any transition not explicitly listed above must fail with a small task-state error and leave state/history unchanged.

Do not allow self-transitions.

## Task graph contract

Implement an immutable validated graph definition under `phoenix/agents/`, preferably `phoenix/agents/task_graph.py`.

A graph represents one task and an ordered tuple of structural step definitions.

Suggested public models:

- `TaskStepDefinition`
- `TaskGraph`
- `TaskState`
- `TaskTransition`
- `TaskStateMachine`
- small explicit errors

Exact naming may vary only if cleaner.

## Task identity

`TaskGraph` must contain a strict nonempty `task_id`.

A UUID4 default is acceptable.

Caller-provided IDs must be preserved after normal outer-whitespace normalization.

Do not derive task identity from user text, aliases, provider IDs, or model IDs.

## Step definition

Each immutable step definition must contain the minimum structural metadata required by the frozen orchestration principles:

- `step_id`
- `owner_agent_id`
- `objective`
- `depends_on`
- `input_refs`
- `expected_output`
- `timeout_seconds`
- `completion_rule`

Requirements:

- all identifiers are strict nonempty normalized strings
- `objective`, `expected_output`, and `completion_rule` contain meaningful non-whitespace text
- `depends_on` is an immutable ordered tuple of step IDs
- `input_refs` is an immutable ordered tuple of nonempty references
- `timeout_seconds` is finite and strictly positive
- booleans are not accepted as numeric timeout values
- extra fields are forbidden
- caller containers are snapshotted into immutable tuples

These fields are descriptive graph metadata only. They do not execute anything.

## Agent ownership

Task graph construction must validate each `owner_agent_id` against PHX-020 `AgentRegistry` authoritative internal IDs.

Aliases must never satisfy ownership validation.

Changing/removing aliases must not alter a graph.

Any registered internal ID may structurally own a step, including future control roles when later tickets explicitly create such steps.

PHX-024 must not itself invoke any agent.

## Graph structural validation

A valid graph must enforce:

- at least one step
- unique normalized step IDs
- every dependency references another step in the same graph
- no self-dependency
- no duplicate dependency inside one step
- graph is acyclic
- declared step ordering is preserved as source order

Cycles must fail explicitly.

Do not silently remove or repair invalid dependencies.

## Deterministic graph queries

Provide small pure read-only helpers sufficient for later orchestration, such as:

- `step(step_id)` or equivalent exact lookup
- `root_step_ids`
- `topological_step_ids`
- `ready_step_ids(completed_step_ids)`

Rules:

- topological order must be deterministic
- when multiple nodes are available, preserve original declared step order
- roots are steps with no dependencies, in declared order
- `ready_step_ids(completed_step_ids)` returns uncompleted steps whose dependencies are all completed, in declared order
- unknown completed-step IDs must fail explicitly rather than being ignored
- duplicate completed-step IDs must fail explicitly if the API accepts a sequence
- graph queries never mutate state

Do not implement an execution scheduler.

## Task state machine

Implement one small in-memory synchronous state machine for one task.

Construction minimum:

- task ID
- initial state `CREATED`

Useful public API:

- `task_id`
- `state`
- `history`
- `can_transition(to_state)`
- `transition(to_state)`
- `is_terminal`

Exact naming may vary if cleaner.

No async API is required.

No locks, worker tasks, queues, timers, or background scheduling.

## Transition record

Every successful transition should append an immutable transition record containing at least:

- `task_id`
- `from_state`
- `to_state`
- UTC-aware `occurred_at`

No arbitrary metadata dictionary.

Do not store prompts, model output, permission grants, secrets, provider raw payloads, or tool output.

State/history inspection must return immutable snapshots.

## State atomicity

`transition()` must validate legality before mutating state/history.

On illegal target/transition/validation failure:

- current state remains unchanged
- history remains unchanged

One process/thread synchronous use is sufficient for V1 PHX-024.

Do not claim cross-thread/distributed safety.

## State-machine separation

The state machine represents state only.

It must not:

- call PhoenixOrchestrator
- call AizenPlanner
- call AgentModelRouter
- invoke agents
- invoke models/providers
- execute tools
- evaluate permissions
- request approvals
- verify with LEVI
- recover with MAHORAGA
- activate KAIDO

Later tickets will cause transitions using this foundation.

## Permission state semantics

`WAITING_PERMISSION` is a task state only.

Entering it does NOT create a permission request or grant.

Returning `WAITING_PERMISSION -> RUNNING` does NOT prove authorization by itself.

PHX-040+ PermissionEngine remains authoritative.

## Verification state semantics

`VERIFYING` is a task state only.

Entering it does NOT call LEVI or prove verification occurred.

Only PHX-026 may implement LEVI runtime verification.

Required verification failure must never transition directly to `COMPLETED`; later runtime must transition according to verification outcome.

## Recovery state semantics

`RECOVERING` is a task state only.

It does not implement retry/replan logic.

PHX-027 owns bounded MAHORAGA recovery.

The structural transitions out of `RECOVERING` only make later recovery integration possible.

## Degraded state semantics

`DEGRADED` is a truthful terminal marker in PHX-024.

It does not execute KAIDO or claim degraded essentials are operational.

PHX-028 owns fail-safe/degraded runtime behavior.

## No PHX-025 handoff envelope

Do not implement the structured handoff contract from `docs/04_AGENT_ORCHESTRATION.md`.

In particular, do not add a runtime handoff object containing:

- source_agent
- target_agent
- permission_scope
- verification_required
- expected_output_schema

The graph's descriptive owner/input/output/timeout/completion fields are structural task metadata, not a handoff message.

PHX-025 owns handoffs.

## No event production

Do not emit `task.created`, `task.state_changed`, `step.started`, or `step.completed` in PHX-024.

The frozen event vocabulary remains unchanged.

This ticket creates the pure graph/state foundation; later orchestration integration may emit truthful events around state-machine operations.

Do not modify the canonical 32 event types.

## No persistence

Do not:

- modify Alembic
- create task tables
- create step tables
- persist transition history
- write graph JSON/YAML/TOML
- create cache/state files

Everything is in memory.

## No provider/model behavior

No provider or model fields belong in task graph definitions in PHX-024.

Do not import concrete Ollama/Colibri adapters.

Do not call ModelProvider.

Do not acquire ModelLifecycleManager leases.

PHX-023 routing decisions remain separate evidence.

## No API / frontend

Do not add task endpoints, WebSockets, React task UI, Tauri commands, or Zustand task state.

`GET /health` remains unchanged.

## Side-effect isolation

Importing/constructing graph/state models and running pure graph/state queries must not:

- open sockets
- contact Ollama/Colibri
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

Add executable isolation evidence.

## Expected dependencies

None.

Use existing Python 3.11, Pydantic v2, standard library, and PHX-020 AgentRegistry.

Do not edit `pyproject.toml` or `uv.lock` unless a genuine defect is discovered.

## Targeted tests

Create `tests/test_task_graph_state_machine.py` or one equally focused file.

At minimum cover:

1. exactly 11 canonical TaskState values
2. no extra states
3. task ID generated by default where applicable
4. caller task ID preserved
5. invalid task IDs rejected
6. TaskStepDefinition immutable
7. graph immutable
8. extra fields rejected
9. at least one graph step required
10. unique normalized step IDs required
11. alias cannot satisfy owner identity
12. valid internal owner accepted
13. renamed/removed alias does not change ownership
14. missing owner internal ID rejected
15. objective validation
16. expected-output validation
17. completion-rule validation
18. finite positive timeout validation
19. bool timeout rejected
20. depends_on snapshotted as tuple
21. input_refs snapshotted as tuple
22. dependency must exist
23. self-dependency rejected
24. duplicate dependency rejected
25. direct cycle rejected
26. multi-node cycle rejected
27. valid DAG accepted
28. declared step order preserved
29. root IDs deterministic
30. topological order deterministic
31. topological tie-break uses declared order
32. exact step lookup works
33. unknown step lookup fails explicitly or returns documented None
34. ready-step computation correct
35. ready-step result excludes completed steps
36. ready-step ordering deterministic
37. unknown completed ID rejected
38. duplicate completed ID rejected if sequence API permits it
39. graph queries do not mutate graph
40. state machine starts CREATED
41. state/history read-only
42. all required legal transitions accepted
43. all unspecified transitions rejected
44. self-transition rejected
45. illegal transition preserves current state
46. illegal transition preserves history
47. successful transition appends exactly one history record
48. transition timestamps UTC-aware
49. transition records immutable
50. terminal COMPLETED rejects further transitions
51. terminal DEGRADED rejects further transitions
52. terminal CANCELLED rejects further transitions
53. `is_terminal` correct
54. `can_transition` does not mutate
55. WAITING_PERMISSION creates no grant/request
56. VERIFYING performs no LEVI runtime call
57. RECOVERING performs no recovery loop
58. DEGRADED performs no KAIDO runtime call
59. no PhoenixOrchestrator invocation
60. no AizenPlanner invocation
61. no AgentModelRouter invocation
62. no model/provider invocation
63. no lifecycle lease
64. no ToolRegistry
65. no PermissionEngine
66. no structured handoff object
67. no EventBus emission
68. no persistence/database work
69. no filesystem writes
70. no network/subprocess/thread/task side effects
71. PHX-020 tests remain green
72. PHX-021 tests remain green
73. PHX-022 tests remain green
74. PHX-023 tests remain green
75. Phase-1 model tests remain green
76. Phase-0 foundation tests remain green
77. exact `/health` unchanged
78. Alembic baseline unchanged
79. canonical 32 event types unchanged
80. desktop regression green

Add more deterministic graph/transition boundary tests as needed.

## Verification

Required:

```text
uv run --locked pytest tests/test_task_graph_state_machine.py
uv run --locked pytest tests/test_agent_registry.py
uv run --locked pytest tests/test_phoenix_orchestrator.py
uv run --locked pytest tests/test_aizen_planner.py
uv run --locked pytest tests/test_agent_model_router.py
uv run --locked pytest tests/test_model_provider.py
uv run --locked pytest tests/test_ollama_provider.py
uv run --locked pytest tests/test_model_registry.py
uv run --locked pytest tests/test_model_lifecycle.py
uv run --locked pytest tests/test_embeddings.py
uv run --locked pytest tests/test_colibri_provider.py
uv run --locked pytest tests/test_model_benchmark.py
uv lock
uv sync --locked
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv build
npm test
npm run typecheck --workspace @phoenix/desktop
npm run build --workspace @phoenix/desktop
```

No dependency or lockfile churn is expected.

## Security / invariant review

Explicitly verify:

- PHOENIX remains sole supreme orchestrator
- AIZEN remains planner only
- route decisions remain separate evidence
- task state never grants permission
- WAITING_PERMISSION does not equal approval
- VERIFYING does not equal verified success
- RECOVERING does not implement MAHORAGA
- DEGRADED does not activate KAIDO
- no privileged tool execution exists
- provider implementation does not leak into graph/state contracts
- aliases remain non-authoritative
- failures are explicit
- no hidden generation/concurrency policy is introduced

## ADR policy

One ADR is acceptable if warranted. A useful single decision may record:

- immutable DAG definitions with authoritative internal owner IDs
- deterministic source-order topological semantics
- exact canonical task-state vocabulary and explicit transition table
- WAITING_PERMISSION/VERIFYING/RECOVERING/DEGRADED are state markers, not subsystem execution
- state history is in-memory evidence only
- no event/persistence/runtime integration yet

Do not create multiple ADRs.

## Completion gate

Only after every required deterministic gate passes:

1. change this ticket `TODO -> DONE`
2. append concise completion evidence
3. change only `PHX-024 Task graph/state machine — TODO` to `DONE` in `FEATURE_TICKETS.md`
4. leave PHX-025 through PHX-028 TODO
5. commit exactly:

`feat: complete PHX-024 task graph state machine`

6. push only `feature/PHX-024-task-graph-state-machine`

Do not create a PR, merge, modify/push main, force-push, or start PHX-025.

## Expected scope

Likely production/test changes:

- `phoenix/agents/task_graph.py`
- `tests/test_task_graph_state_machine.py`
- `DECISIONS.md` only if one ADR is warranted
- this ticket
- `FEATURE_TICKETS.md`

A small `phoenix/agents/__init__.py` export is acceptable if deliberate.

Do not modify PHX-020/021/022/023 production code unless a genuine prior defect is discovered; if so, stop and report it rather than silently broadening scope.
