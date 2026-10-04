# PHX-020 — Agent Registry

Status: DONE

Phase: 2 — Orchestration

## Objective

Implement the provider-neutral in-memory registry for PHOENIX's canonical logical agent identities. The registry must make the frozen internal IDs authoritative, keep anime aliases display-only, expose immutable deterministic metadata, and provide the stable roster foundation required by later orchestration tickets without implementing orchestration itself.

PHX-020 is identity/metadata infrastructure only. It does not plan, route, invoke models, call tools, grant permissions, execute handoffs, persist agent state, or create agent-to-agent conversations.

## Authoritative context

Read at minimum:

- `AGENTS.md`
- `RULES.md`
- `MENTAL_MODEL.md`
- `INVARIANTS.md`
- `MASTER_SPEC.md`
- `PRD.md`
- `DECISIONS.md`
- `docs/01_ARCHITECTURE.md`
- `docs/02_SYSTEM_FLOW.md`
- `docs/03_AGENT_ROSTER.md`
- `docs/04_AGENT_ORCHESTRATION.md`
- `docs/05_MODEL_STRATEGY.md`
- `docs/08_PERMISSION_MATRIX.md`
- `docs/09_SECURITY.md`
- `docs/10_MEMORY_SYSTEM.md`
- `docs/14_EVENT_SYSTEM.md`
- `docs/23_OBSERVABILITY.md`
- completed Phase-1 model-runtime tickets
- this ticket

Authority conflicts must stop implementation rather than being silently resolved.

## Canonical roster

The built-in registry must represent exactly these 30 logical identities from `docs/03_AGENT_ROSTER.md`:

| Internal ID | Display alias | Professional role |
|---|---|---|
| `phoenix` | PHOENIX | Supreme orchestrator |
| `strategist` | AIZEN | Chief strategist/master planner |
| `intelligence` | ARMIN | Intelligence/reporting |
| `automation` | LAW | Automation/workflows |
| `mission_coordinator` | ERWIN | Mission/task coordination |
| `project_manager` | NAMI | Project management |
| `build_engineer` | FRANKY | Build/engineering |
| `document_analyst` | ROBIN | Documents/knowledge extraction |
| `system_intelligence` | LELOUCH | Active system intelligence/monitoring |
| `diagnostics` | CHOPPER | Diagnostics |
| `perimeter_security` | GOJO | Security/perimeter |
| `health_biometrics` | MIGHT GUY | Health/biometrics |
| `ethics_compliance` | FUJITORA | Legal/ethics/compliance |
| `media` | BROOK | Spotify/YouTube/media |
| `failsafe` | KAIDO | Emergency degraded fail-safe |
| `infrastructure` | WHITEBEARD | Infrastructure/stability |
| `knowledge_rag` | VEGAPUNK | Knowledge indexing/RAG |
| `background_optimizer` | SHIKAMARU | Background optimization |
| `scenario_analysis` | LIGHT | Scenario/trade-off analysis |
| `heavy_compute` | GARP | Heavy compute/batch work |
| `threat_recon` | SASUKE | Threat reconnaissance |
| `priority_executor` | THORFINN | High-priority execution |
| `user_interaction` | NARUTO | User interaction support |
| `silent_monitor` | ITACHI | Logs/anomaly monitoring |
| `adaptive_recovery` | MAHORAGA | Adaptive recovery/self-healing |
| `memory` | PUDDING | Long-term memory |
| `web_research` | URAHARA | Real-time web research |
| `voice` | UTA | Voice/wake interface |
| `computer_control` | KAKASHI | Computer control |
| `verifier` | LEVI | Verification/QA |

No alias is an authoritative identifier.

## Required agent definition contract

Create a small immutable validated `AgentDefinition` (exact class name may vary) under `phoenix/agents/`.

At minimum it must represent:

- authoritative `internal_id`
- optional/display-only `alias`
- nonempty `professional_role`
- immutable capability/responsibility tags useful for later routing, if introduced
- immutable activation tags/rules only if they are descriptive metadata rather than executable routing policy
- immutable forbidden-responsibility notes where useful for preserving documented boundaries

Do not create provider-specific model IDs, provider URLs, API keys, tool implementation objects, PermissionEngine grants, database handles, or executable callbacks inside agent definitions.

If capability/activation/constraint metadata is added, keep it small, identifier/text based, deterministic, and provider-neutral. Do not invent a large ontology merely to fill fields.

## Alias rule

`internal_id` is the only authoritative registry key.

Changing or removing an alias must not change:

- identity
- lookup by internal ID
- ordering
- routing metadata
- permissions
- model/provider selection
- behavior

The registry must not accept an anime alias as an implicit substitute for an internal ID in authoritative lookup APIs.

Alias lookup may be omitted entirely. If any alias lookup helper is provided, it must be explicitly display/discovery-only and must never drive execution or authority.

## Registry API

Implement a compact in-memory registry, conceptually:

- `AgentRegistry(...)`
- `get(internal_id) -> AgentDefinition | None`
- `require(internal_id) -> AgentDefinition` or equivalent explicit missing-ID failure
- `contains(internal_id) -> bool`
- `list_agents() -> tuple[AgentDefinition, ...]`

A built-in constructor/helper such as `build_default_agent_registry()` may load the frozen canonical roster.

Exact names may vary if cleaner, but behavior must remain deterministic and minimal.

## Validation

Require:

- unique internal IDs
- valid nonempty internal IDs
- deterministic whitespace normalization consistent with existing neutral identifier semantics where practical
- nonempty role text
- no duplicate canonical roster entry
- immutable definitions
- immutable list snapshots
- forbidden extra fields on public Pydantic models
- deterministic JSON serialization where models are serialized

Aliases should be validated as display strings but remain semantically non-authoritative.

## Ordering

`list_agents()` must return deterministic canonical ordering.

For the built-in roster, preserve the frozen roster order from `docs/03_AGENT_ROSTER.md`, beginning with `phoenix` and ending with `verifier`.

Do not depend on incidental dictionary insertion order unless that order is explicitly frozen into an immutable source tuple and tested.

## Boundary metadata

The registry must preserve the documented distinctions needed by later orchestration without pretending to enforce security itself. At minimum tests/documentation must preserve these facts:

- PHOENIX is the sole supreme orchestrator.
- AIZEN plans; PHOENIX decides/finalizes.
- LEVI verifies; it does not repair by default.
- MAHORAGA performs bounded adaptation/recovery and cannot bypass permissions.
- KAIDO is degraded/fail-safe only.
- GOJO/FUJITORA may advise but are not the PermissionEngine.
- BROOK is an agent; Spotify/YouTube are tools.
- PUDDING owns memory responsibility; storage implementation is separate.
- VEGAPUNK owns RAG/knowledge-indexing responsibility.
- LIGHT performs scenario/trade-off analysis rather than execution planning.
- LELOUCH active system intelligence is distinct from ITACHI silent log/anomaly monitoring.

These facts may be represented as neutral descriptive metadata or verified against the frozen role definitions. Do not create a fake authorization system here.

## Model separation

Do not bind agents to concrete providers/models in PHX-020.

Do not put values such as:

- Ollama
- Colibri
- `qwen3.5:9b`
- `qwen2.5-coder:7b`
- deep-model IDs

inside canonical agent definitions.

`INV-012` requires provider-specific implementation not to leak into agent definitions.

Any future model preference/routing interpretation belongs to PHX-023. The registry may only hold small provider-neutral descriptive hints if clearly required by authority; avoid them otherwise.

## Tools and permissions separation

PHX-020 must not implement ToolRegistry or PermissionEngine behavior.

Do not grant permissions from registry metadata.

Do not store live permission grants.

Do not execute tools.

Do not treat GOJO/FUJITORA/agent metadata as security authority.

Tool and permission systems arrive in later phases.

## Memory separation

Do not implement PUDDING memory in this ticket.

If a neutral memory namespace descriptor is included in agent metadata, it must be a string identifier only and must not create/read/write memory.

No SQLite schema or persistence changes.

## Orchestration separation

Do not implement:

- PHOENIX orchestration loop
- AIZEN planning
- agent/model router
- task graph
- handoff execution
- agent-to-agent messaging
- LEVI verification runtime
- MAHORAGA recovery runtime
- KAIDO degraded runtime

PHX-021 through PHX-028 own those behaviors.

## No one-process-per-agent architecture

The registry represents logical personas/capability containers.

Creating the 30 roster entries must not:

- instantiate 30 LLM clients
- load 30 models
- create 30 processes
- start 30 tasks
- open network connections

The roster is metadata only.

## No side effects

Importing the module, constructing definitions, building the default registry, and reading/listing entries must not:

- contact Ollama/Colibri
- run model inference
- scan hardware
- write files/directories
- access SQLite/Alembic
- launch subprocesses
- open sockets
- start threads
- create asyncio background tasks
- emit events

## Dependencies

Expected new dependencies: none.

Use existing Python/Pydantic/typing facilities.

Do not modify `pyproject.toml` or `uv.lock` without a genuine requirement.

## Suggested implementation scope

Likely files:

- `phoenix/agents/registry.py`
- `tests/test_agent_registry.py`
- this ticket
- `FEATURE_TICKETS.md`
- `DECISIONS.md` only if one durable registry decision merits an ADR

Do not add one Python module per character in PHX-020.

## Minimum tests

Add focused deterministic tests proving at least:

1. canonical default registry contains exactly 30 identities
2. first canonical identity is `phoenix`
3. last canonical identity is `verifier`
4. every frozen internal ID exists exactly once
5. every frozen display alias/role matches `docs/03_AGENT_ROSTER.md`
6. internal IDs are unique
7. authoritative lookup uses internal ID
8. alias is not accepted as internal-ID lookup
9. changing/removing alias in an otherwise equivalent custom definition does not change authoritative identity
10. unknown `get()` returns `None`
11. `require()` or equivalent missing lookup fails explicitly
12. `contains()` works
13. list ordering is deterministic
14. list result is immutable
15. definitions are immutable
16. extra public fields rejected
17. invalid/empty IDs rejected
18. empty role rejected
19. duplicate IDs rejected during custom registry construction
20. caller mutation cannot alter stored metadata
21. PHOENIX remains identified as sole supreme orchestrator by frozen metadata/role
22. strategist/AIZEN remains planner rather than supreme authority
23. verifier/LEVI remains verification role
24. adaptive_recovery/MAHORAGA remains recovery role
25. failsafe/KAIDO remains fail-safe role
26. LIGHT and AIZEN roles remain distinct
27. LELOUCH and ITACHI roles remain distinct
28. GOJO/FUJITORA are not represented as PermissionEngine authority
29. no concrete provider/model IDs exist in canonical definitions
30. no provider imports in agent registry
31. no tool execution/permission implementation
32. no persistence/database access
33. no event emission
34. no subprocess/thread/background-task creation
35. import/build/list causes no network or filesystem side effects
36. no per-agent model/runtime instances are created
37. PHX-010 through PHX-016 model-runtime tests remain green
38. Phase-0 foundation tests remain green
39. exact `/health` contract remains unchanged
40. Alembic baseline remains unchanged
41. canonical 32 event types remain unchanged
42. desktop regressions remain green

Add additional validation tests if implementation requires them.

## Verification

Required before DONE:

- targeted agent-registry tests
- full Python test suite
- Ruff lint
- Ruff format check
- strict mypy
- package build
- all Phase-0 and Phase-1 regressions
- desktop tests/typecheck/build
- side-effect isolation proof
- credential/signature scan
- `git diff --check`

No live model/runtime call is required.

## Completion criteria

PHX-020 is DONE only when:

- canonical 30-identity roster exists exactly and deterministically
- internal IDs are authoritative
- aliases are display-only/removable without behavioral effect
- registry contracts are immutable/validated
- no concrete provider/model bindings leak into definitions
- no permissions/tools/orchestration/runtime behavior is implemented
- construction/read operations are side-effect free
- deterministic tests and full regressions pass
- no new dependency is required
- ticket/index completion evidence is updated truthfully

## Commit message

`feat: complete PHX-020 agent registry`

## Next ticket

PHX-021 — PHOENIX Orchestrator. Do not start it from this ticket.

## Verified completion — 2026-10-04

- Created phoenix/agents/registry.py and tests/test_agent_registry.py. Updated
  only this ticket, PHX-020 in FEATURE_TICKETS.md, and one accepted ADR-030.
  Existing agent package initializer, model implementations/tests, dependencies/
  locks, foundations, desktop and PHX-021 onward remain unchanged.
- Public contracts: AgentDefinition(internal_id, alias=None, professional_role),
  AgentRegistry(agents=...), get(), require(), contains(), list_agents(),
  UnknownAgentError, DEFAULT_AGENT_DEFINITIONS, build_default_agent_registry().
  Frozen extra-forbidden Pydantic models use strict trimmed nonempty strings.
  No capability/routing/activation/permission/runtime fields were speculated.
- The default registry contains exactly 30 logical identities, one PHOENIX and
  29 specialists. An immutable source tuple preserves the exact documented IDs,
  aliases, professional roles and order, beginning phoenix and ending verifier.
  Tests compare every triple against docs/03_AGENT_ROSTER.md and freeze all IDs.
- Authoritative lookup uses internal_id only, trims outer whitespace, preserves
  case and has no alias fallback. get() returns None for valid missing IDs;
  require() raises UnknownAgentError; contains() returns false. Invalid inputs
  raise validation errors. Custom registries preserve explicitly supplied order.
- Aliases may be removed or renamed without changing IDs, role metadata, ordering
  or lookup. Tests remove/rename every canonical alias and check equivalence.
  Alias collisions cannot shadow internal IDs; duplicate aliases are harmless
  display metadata. Built-in aliases retain their documented spelling.
- Duplicate IDs reject after normalization. Caller lists/dictionaries are
  snapshotted into tuples and revalidated frozen definitions; later caller
  mutation cannot change stored records. Forged invalid nested models reject.
  Registry/definition assignment and extra fields reject; JSON round-trips and
  independently built default registries serialize deterministically.
- Frozen roles preserve PHOENIX as sole supreme orchestrator, AIZEN planning,
  LEVI verification without default repair, bounded MAHORAGA recovery without
  permission bypass, and KAIDO degraded fail-safe only. GOJO/FUJITORA are advisory,
  not PermissionEngine authority. LIGHT scenario analysis and LELOUCH active
  intelligence remain distinct from AIZEN planning and ITACHI silent monitoring.
  BROOK is an agent while Spotify/YouTube are tools; PUDDING memory and VEGAPUNK
  knowledge/RAG remain descriptive responsibilities with separate implementations.
- Only typing and Pydantic are imported by registry code. No concrete providers,
  model IDs/URLs, clients, lifecycle leases, tool objects, permission grants,
  callbacks or per-agent processes/tasks are created or stored. No orchestration,
  routing, task graph, handoffs, memory/storage, event production or API/UI added.
  Role text and aliases grant no execution/permission authority.
- Fresh-interpreter executable isolation blocks model/core/API/persistence/tool/
  permission/memory/voice/recovery/hardware imports, sockets/network, subprocesses,
  filesystem writes/mkdir, SQLite, threads and hidden asyncio tasks. Import,
  custom definition/registry construction, default build, lookup/listing and JSON
  serialization pass with an empty temporary directory. AST tests also restrict
  production imports and rule out asynchronous runtime code/global registries.
- Verification:
  - uv run --locked pytest tests/test_agent_registry.py: PASS, 96 cases.
  - Individual locked PHX-010/011/012/013/014/015/016 regressions: PASS,
    61/108/83/54/182/194/130 cases, 812 total, prior tests unchanged.
  - uv lock / uv sync --locked: PASS, 36 packages, no dependency/lock changes.
  - uv run --locked pytest: PASS, 1,034 cases; one existing Starlette HTTPX
    deprecation warning. Explicit six-file Phase-0 subset: 126 passing cases.
  - uv run --locked ruff check . / ruff format --check .: PASS, 43 files.
  - uv run --locked mypy: PASS, 41 source files under unchanged strict config.
  - uv build: PASS, source distribution and wheel in ignored dist/.
  - npm test: PASS, four cases; desktop workspace typecheck/build: PASS.
- Security and INV-001 through INV-024 reviewed: preserved or unaffected.
  Neutral identities and removable aliases satisfy INV-012/014; no model work
  affects global heavy-generation limits. PermissionEngine/ToolRegistry authority
  remains intact. No credentials, provider URLs, prompt/output storage, executable
  callbacks, runtime calls or direct frontend runtime access added. /health remains
  exact; Alembic baseline and canonical 32 event types remain unchanged/green.
- All deterministic acceptance criteria met. Known limitations: metadata-only,
  no runtime enforcement of role boundaries, no scheduling/routing, and no claim
  that registered identities are online or initialized. Future orchestration owns
  runtime behavior; future routing owns model/provider selection. No live runtime
  call is required or performed. Phase 2 has begun; only PHX-020 is DONE.
- Entire staged-diff, whitespace, credential-signature and exact-scope review
  precede feat: complete PHX-020 agent registry and a normal push only to
  feature/PHX-020-agent-registry. Final SHA, clean-tree and remote equality evidence
  is reported after push. No main push, force, PR, merge or PHX-021 implementation.
