# PHX-023 — Agent/Model Router

Status: DONE
Phase: 2 — Orchestration

## Objective

Implement PHOENIX's first deterministic, provider-neutral routing boundary for
assigning one bounded specialist agent and one eligible model candidate to a unit
of work.

The router must consume explicit routing evidence and return one validated route.
It must not classify arbitrary user text, execute agents/models/tools, grant
permissions, or become a scheduler.

## Authority

- PHOENIX remains the sole supreme orchestrator.
- AIZEN remains the subordinate planner for complex work.
- PHX-023 may choose a bounded specialist + model candidate only.
- A route is a proposal/decision record, not execution success or permission.
- Internal agent IDs are authoritative; aliases are presentation only.
- PermissionEngine remains authoritative for action approval.
- PHX-013 remains authoritative for heavy-generation capacity.

## Inputs from frozen architecture

Routing may use only explicit evidence corresponding to the frozen routing inputs:

- domain
- modality / technical capability requirements
- tool-use need
- provider health
- resource eligibility/pressure evidence
- permission-risk metadata

Do not infer these from free-form user text in PHX-023.

## Agent routing

Create a small typed `RoutingDomain` (or equivalent) mapped deterministically to
normal routable V1 specialist internal IDs.

The normal specialist map may include roles such as:

- intelligence -> intelligence
- automation -> automation
- mission_coordination -> mission_coordinator
- project_management -> project_manager
- build_engineering -> build_engineer
- documents -> document_analyst
- system_intelligence -> system_intelligence
- diagnostics -> diagnostics
- security -> perimeter_security
- health -> health_biometrics
- compliance -> ethics_compliance
- media -> media
- infrastructure -> infrastructure
- knowledge_rag -> knowledge_rag
- optimization -> background_optimizer
- scenario_analysis -> scenario_analysis
- heavy_compute -> heavy_compute
- threat_recon -> threat_recon
- priority_execution -> priority_executor
- user_interaction -> user_interaction
- silent_monitoring -> silent_monitor
- memory -> memory
- web_research -> web_research
- voice -> voice
- computer_control -> computer_control

The exact enum spelling may vary if cleaner, but the mapping must use neutral
internal IDs and documented roles only.

Do NOT route normal work to these control identities in PHX-023:

- phoenix — supreme orchestration is PHX-021
- strategist — planning is PHX-022
- verifier — verification is PHX-026
- adaptive_recovery — recovery is PHX-027
- failsafe — degraded fail-safe is PHX-028

No alias may be used as a routing key.

## Model candidate contract

Create an immutable provider-neutral model candidate contract containing at least:

- provider_id
- model_id
- resource_eligible: strict bool

Candidate tuple order is explicit caller policy/evidence. PHX-023 may select the
first candidate that satisfies all eligibility checks, but it must not invent a
benchmark score, quality score, model tier winner, or provider preference.

Reject duplicate provider/model candidate pairs after identifier normalization.

## Provider health evidence

Create a small immutable route-health evidence model, or accept an equally strict
neutral representation, containing:

- provider_id
- ProviderHealthStatus

Requirements:

- provider health is supplied to the router; the router performs no network probes
- health evidence must have unique provider IDs
- normal routing accepts only HEALTHY providers
- DEGRADED is not silently treated as healthy
- UNAVAILABLE is never eligible
- missing health evidence for a candidate provider makes that candidate ineligible
- do not auto-activate KAIDO when only degraded/unavailable providers remain

PHX-028 owns degraded fail-safe behavior.

## Capability requirements

Reuse PHX-012 `CapabilityRequirements` and `ModelCapabilityRegistry`.

The routing request must be able to express neutral technical requirements such as:

- streaming
- structured output
- tool use
- vision
- cancellation
- minimum context tokens

A model candidate is technically eligible only when the registry contains the
exact `(provider_id, model_id)` record and its capabilities satisfy the effective
requirements.

Do not call `ModelProvider.capabilities()` from the router.
Do not refresh the registry from the router.
Do not discover models.

## Tool-use need

A route may require `supports_tool_use=true` as technical model metadata.

This does NOT:

- execute a tool
- grant permission
- bypass ToolRegistry
- bypass PermissionEngine

Tool execution remains later Phase-4 work.

## Modality

If a small modality enum is useful, keep it neutral and limited.

For example:

- text
- vision

A vision route must require `supports_vision=true`.

Do not implement image transport, STT, TTS, screenshots, or multimodal message
payloads in this ticket.

Voice is an interface/agent domain, not proof that the selected text model has a
voice modality.

## Permission risk

The routing request may carry permission-risk metadata matching P0–P5 or an
existing neutral representation.

PHX-023 must not approve, deny, or create grants.

Risk metadata may be preserved in the route result for later enforcement, but it
must never become authorization evidence.

Do not implement PermissionEngine.

## Resource evidence

Resource eligibility is explicit caller evidence on each model candidate.

If `resource_eligible=false`, the candidate is ineligible.

Do not:

- scan hardware
- measure VRAM/RAM
- read benchmark reports
- create resource scores
- create a second lifecycle semaphore

PHX-007/013/016 remain separate authorities/evidence sources.

## Routing request

Create an immutable, extra-forbidden request with a generated/caller-provided
route/request ID and at minimum:

- routing domain
- ordered model candidates
- provider-health evidence
- capability requirements
- permission-risk metadata if implemented

If modality/tool need are represented separately, derive an effective
`CapabilityRequirements` deterministically and without mutating caller objects.

Require at least one model candidate.

## Routing result

Create an immutable, extra-forbidden route result containing at least:

- request/route ID
- agent_id
- provider_id
- model_id
- domain
- effective technical capability requirements or enough neutral evidence to audit
  the decision
- permission-risk metadata if carried by the request

The result must not contain:

- anime alias as authority
- provider client objects
- ModelProvider instances
- tool objects
- permission grants
- benchmark score
- task state
- handoff envelope
- generated model text

## Deterministic model selection

For candidate order C1..Cn, evaluate in caller-provided order.

A candidate is eligible only if ALL are true:

1. candidate `resource_eligible` is true
2. matching provider health evidence exists
3. provider health is HEALTHY
4. exact capability-registry record exists
5. capability record satisfies all effective requirements

Select the first eligible candidate.

Do not reorder candidates by provider/model name.
Do not rank by benchmark metrics.
Do not prefer Ollama or Colibri in router code.
Do not hard-code qwen model IDs.

If no candidate is eligible, fail explicitly with a small router error such as
`NoEligibleModelError`.

## Agent validation

After domain -> internal-ID mapping, validate that the selected agent exists in the
supplied/default PHX-020 AgentRegistry.

If the required agent is missing, fail explicitly.

Do not fall back to another role.
Do not use aliases or professional-role text to recover a missing ID.

## Router construction

A clean design may accept:

- AgentRegistry
- ModelCapabilityRegistry

Construction must perform no I/O and no provider calls.

Do not copy mutable registry internals into public route objects.

## Concurrency

PHX-023 routing should be synchronous/pure if possible because all required evidence
is already in memory.

Do not create:

- background tasks
- workers
- queues
- locks
- semaphores

No cancellation API is required for a purely synchronous route decision.

## Exact routing freeze boundary

`docs/05_MODEL_STRATEGY.md` states exact model routing is not permanently frozen
until benchmark evidence is sufficient.

Therefore PHX-023 must NOT hard-code permanent provider/model winners.

The caller-supplied candidate ordering is the temporary policy input; PHX-023 only
applies factual eligibility filters.

Later policy can change candidate ordering without changing the router contract.

## No provider calls

Do not call:

- `health()`
- `capabilities()`
- `generate()`
- `stream()`
- `cancel()`

on any ModelProvider.

Do not import concrete Ollama/Colibri adapters.

## No execution

Do not:

- invoke PhoenixOrchestrator
- invoke AizenPlanner
- invoke specialist agents
- create tasks/task steps
- execute tools
- acquire lifecycle leases
- start inference

PHX-023 returns a route only.

## No PHX-024+ leakage

Do not implement:

- task graph/state machine
- structured agent handoffs
- LEVI verification runtime
- MAHORAGA recovery
- KAIDO fail-safe

No task lifecycle state belongs in the router result.

## No persistence / API / events

Do not:

- modify Alembic
- add route tables
- persist routes
- write JSON/YAML/TOML route state
- add FastAPI endpoints
- add frontend/Tauri work
- emit EventBus events
- modify the canonical 32 event types

## Dependencies

Expected dependency changes: NONE.

Use only existing Python/Pydantic and PHOENIX contracts.

Do not edit `pyproject.toml` or `uv.lock` without a genuine discovered defect.

## Suggested implementation

Prefer:

- `phoenix/agents/router.py`
- `tests/test_agent_model_router.py`

Keep production code compact.

## Required tests

At minimum prove:

1. router construction with default registries succeeds
2. construction performs no I/O
3. routing-domain values are deterministic
4. each normal domain maps to the expected neutral internal ID
5. aliases are never routing keys
6. changing/removing aliases does not change routing
7. phoenix is not a normal specialist target
8. strategist is not a normal specialist target
9. verifier is not a PHX-023 normal target
10. adaptive_recovery is not a PHX-023 normal target
11. failsafe is not a PHX-023 normal target
12. missing mapped agent fails explicitly
13. route/request ID generated by default
14. supplied route/request ID preserved
15. invalid IDs rejected
16. request immutable
17. extra request fields rejected
18. at least one model candidate required
19. candidate tuple ordering preserved
20. duplicate candidate pairs rejected
21. model candidate immutable
22. resource_eligible strict bool
23. health evidence immutable
24. duplicate provider-health evidence rejected
25. HEALTHY provider eligible
26. DEGRADED provider ineligible in normal router
27. UNAVAILABLE provider ineligible
28. missing health evidence makes candidate ineligible
29. missing capability record makes candidate ineligible
30. resource_eligible=false makes candidate ineligible
31. capability requirements are applied
32. streaming requirement filters unsupported model
33. structured-output requirement filters unsupported model
34. tool-use requirement filters unsupported model
35. vision requirement filters unsupported model
36. cancellation requirement filters unsupported model
37. minimum context requirement filters unknown/too-small context
38. exact minimum-context boundary passes
39. first eligible candidate in caller order is selected
40. an ineligible earlier candidate does not block later eligible candidate
41. candidate order, not alphabetical model/provider order, determines winner
42. router does not hard-code Ollama preference
43. router does not hard-code Colibri preference
44. router contains no qwen/EmbeddingGemma model IDs
45. no benchmark score/ranking exists
46. no model discovery exists
47. no provider health probe occurs
48. no provider capability refresh occurs
49. no generation/stream/cancel occurs
50. no lifecycle lease acquired
51. route result uses neutral agent internal ID
52. route result preserves selected provider/model IDs
53. route result is immutable
54. route result contains no alias authority
55. permission-risk metadata grants no authority
56. tool capability grants no execution authority
57. no ToolRegistry implementation
58. no PermissionEngine implementation
59. no task graph/state machine
60. no handoff implementation
61. no LEVI runtime
62. no MAHORAGA runtime
63. no KAIDO auto-fallback
64. no persistence/database access
65. no filesystem writes
66. no network
67. no subprocess/thread/background-task creation
68. no EventBus emission
69. PHX-020 tests remain green
70. PHX-021 tests remain green
71. PHX-022 tests remain green
72. all Phase-1 model tests remain green
73. Phase-0 foundation tests remain green
74. `/health` unchanged
75. Alembic baseline unchanged
76. canonical 32 events unchanged
77. desktop regressions green

Add boundary tests where implementation requires them.

## Verification

Run targeted tests first, then:

- PHX-020 / PHX-021 / PHX-022 regressions
- all Phase-1 model-runtime regressions
- full Python tests
- Ruff lint/format
- strict mypy
- package build
- foundation regressions
- desktop npm tests/typecheck/build
- credential/signature scan
- invariant review

## Completion gate

Only after every deterministic gate passes:

- mark this ticket DONE
- change only `PHX-023 Agent/model router — TODO` to DONE in
  `FEATURE_TICKETS.md`
- record one ADR only if a durable routing-policy decision needs documentation
- commit with exactly:
  `feat: complete PHX-023 agent model router`
- push only `feature/PHX-023-agent-model-router`

Do not start PHX-024.

## Completion evidence — 2026-10-05

PASS. Implemented phoenix/agents/router.py with tests/test_agent_model_router.py.
Updated only this ticket, FEATURE_TICKETS.md and one durable decision, ADR-033.
No dependencies, existing implementations/tests, API, desktop or migrations changed.
PHX-020 through PHX-023 are DONE; PHX-024 through PHX-028 remain TODO.

Public API: RoutingDomain, DOMAIN_AGENT_IDS, PermissionRisk, ModelRouteCandidate,
ProviderRouteHealth, RoutingRequest, RouteDecision, AgentModelRouter.route(),
AgentModelRoutingError, MissingRouteAgentError and NoEligibleModelError.

All 25 documented normal domains map to neutral specialist internal IDs. The map
is immutable. Registry validation uses IDs only, without alias/role fallback.
phoenix, strategist, verifier, adaptive_recovery and failsafe are excluded.
Removing/changing aliases preserves every normal domain's route.

Frozen extra-forbidden contracts normalize strict nonempty identifiers, snapshot
ordered input containers and nested evidence, and revalidate forged/copied models.
Requests default to UUID4 route IDs and require nonempty candidates, explicit
capability requirements and descriptive P0-P5 risk. Empty health evidence is
permitted and makes all candidates ineligible. Duplicate normalized candidate
pairs and provider-health IDs fail even when their facts agree.

Selection uses PHX-012 ModelCapabilityRegistry.matching and caller order. Every
winner must have resource_eligible=true, explicit HEALTHY provider evidence and
an exact provider/model record satisfying all technical requirements. DEGRADED,
UNAVAILABLE, missing evidence and unsupported requirements do not trigger fallback.
False requirements impose no constraint; unknown context fails positive minima;
the exact minimum passes. Missing mapped agents/no eligible models fail explicitly.
Detached RouteDecision evidence preserves correlation, domain, selected IDs,
requirements and risk; identity contradictions fail validation.

No provider/model winner is frozen. No health probe, capability refresh, discovery,
generation, stream, cancel, benchmark ranking, hardware scan or lifecycle lease.
No tool execution, grants, permission evaluation, orchestrator/planner invocation,
task graph, handoff, LEVI, MAHORAGA, KAIDO, persistence, events, API or desktop work.
P4/P5 routes describe proposed identities only; they do not authorize P4/P5 actions.

Verification commands and results:

- uv run --locked pytest tests/test_agent_model_router.py: 213 passed, 0.58s.
- Individual locked pytest PHX-020 registry: 96 passed; PHX-021 orchestrator:
  101 passed; PHX-022 planner: 151 passed. Existing code/tests unchanged.
- Individual locked Phase-1 pytest suites: provider 61, Ollama 108, registry 83,
  lifecycle 54, embeddings 182, Colibri 194, benchmark 130: all 812 passed.
- uv lock and uv sync --locked: 36 packages; manifests/lock unchanged.
- uv run --locked pytest: 1,499 passed, 24.80s; one existing Starlette/httpx
  deprecation warning. No failed test run occurred during regression verification.
- uv run --locked ruff check .: passed; ruff format --check .: 49 files formatted;
  uv run --locked mypy: 47 source files passed under unchanged strict config.
- uv build: passed; wheel/source archives verified to contain agents/router.py.
- Explicit locked foundation pytest (bootstrap/api/config/persistence/events/
  hardware): 126 passed. /health, Alembic baseline and canonical 32 events unchanged.
- npm test: four passed; npm run typecheck --workspace @phoenix/desktop and
  npm run build --workspace @phoenix/desktop: passed.
- Fresh-interpreter import/construction/routing audit blocks filesystem writes,
  mkdir, sockets, SQLite, subprocesses, threads, tasks and forbidden subsystem
  imports: passed. AST import/call allowlist, synchronous execution and exact
  public-field checks passed. Repeated routing produces equal decisions.
- Credential/signature scan: 132 repository files, zero matches; git diff --check
  passed. No secret/prompt logging, raw health detail, provider URL/client fields
  or executable callbacks added; no frontend runtime path or permission bypass.
- INV-001 through INV-024 reviewed: authority, aliases, explicit failures and
  generation capacity separation preserved. Deferred permission/verification/
  recovery/fail-safe enforcement is not claimed. No optional runtime dependency.

Limitations: callers own candidate order, observation freshness and resource facts;
eligibility does not reserve capacity or prove task quality. Technical capability
matching does not assess benchmark quality. One process/thread, memory-only
evidence. No text classification, model inference or runtime integration. All
capability/health fixtures are local data; no live provider smoke was needed.
PHX-024 — Task Graph / State Machine is next and has not been started.
