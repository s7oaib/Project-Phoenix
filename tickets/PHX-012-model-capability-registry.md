# PHX-012 — Model Capability Registry

Status: DONE

Phase: 1 — Model Runtime

## Objective

Implement a provider-neutral, in-memory registry for validated model capability snapshots so later lifecycle, routing, orchestration, and UI layers can query what a provider/model combination can technically support without depending on provider internals or inventing routing policy.

## Authoritative context

Read before implementation:

- `AGENTS.md`
- `RULES.md`
- `MENTAL_MODEL.md`
- `INVARIANTS.md`
- `MASTER_SPEC.md`
- `PRD.md`
- `DECISIONS.md`
- `docs/01_ARCHITECTURE.md`
- `docs/05_MODEL_STRATEGY.md`
- `docs/06_HARDWARE_PROFILE.md`
- `docs/09_SECURITY.md`
- `docs/14_EVENT_SYSTEM.md`
- `docs/23_OBSERVABILITY.md`
- `docs/24_TEST_EVALUATION.md`
- `tickets/PHX-010-model-provider-interface.md`
- `tickets/PHX-011-ollama-provider.md`
- this ticket

## Dependencies

- PHX-001 through PHX-011 — DONE

## Requirements

### Boundary

- Keep the registry provider-neutral under `phoenix/models/`.
- Prefer a small module such as `phoenix/models/registry.py`.
- The registry may depend on the PHX-010 `ModelProvider` and `ModelCapabilities` contracts.
- The registry must not depend on Ollama-specific payloads, HTTPX, Colibri, agents, FastAPI, frontend, persistence, tools, permissions, voice, or hardware scanning.
- Do not modify PHX-010 contracts merely for registry convenience unless a genuine contract defect is discovered and reported first.

### Purpose

The registry owns factual capability snapshots for provider/model pairs.

It may answer questions such as:

- is this provider/model known?
- what capability snapshot is currently registered?
- which known models satisfy a caller-supplied technical requirement?
- when was a snapshot last refreshed?

It must not answer policy questions such as:

- which model should PHOENIX use?
- which model is fastest/best/cheapest?
- which model should AIZEN receive?
- which tier should be selected?
- should a model be loaded/unloaded?

Those belong to later routing/lifecycle/benchmark tickets.

### Registry identity

Use the pair:

- `provider_id`
- `model_id`

as the stable registry key.

Do not assume model IDs are globally unique across providers.

Normalize/validate identifiers using the existing PHX-010 identifier semantics rather than inventing provider-specific name parsing.

### Capability record

Provide an immutable validated registry record containing at minimum:

- provider ID
- model ID
- the PHX-010 `ModelCapabilities` snapshot or equivalent frozen fields
- timezone-aware UTC refresh/update timestamp

The record must preserve the exact provider/model identity reported through the neutral contract.

Do not add arbitrary provider metadata dictionaries.

Do not store raw `/api/show` payloads.

### Registry operations

Provide a small explicit API supporting at least:

- register/replace one validated capability snapshot
- get one provider/model record
- remove one provider/model record
- list records deterministically
- clear the in-memory registry explicitly
- refresh one provider/model from a supplied `ModelProvider`

Exact names may differ if the resulting API is cleaner.

No operation should occur automatically at import time.

### Refresh from provider

Refreshing must call only:

`await provider.capabilities(model_id)`

and register the returned neutral `ModelCapabilities` if valid.

The registry must verify that the returned capability record matches:

- the supplied provider's `provider_id`
- the requested `model_id`

A contradictory provider/model identity must fail explicitly and must not mutate existing registry state.

Provider failures must propagate explicitly rather than fabricating a capability record.

### Replacement semantics

Registering a new valid snapshot for an existing provider/model should replace that record atomically.

A failed refresh/validation must leave the previous valid record unchanged.

Do not create duplicate records for the same provider/model key.

### Deterministic listing

Registry listing must be deterministic.

Prefer sorting by:

`(provider_id, model_id)`

rather than relying on incidental dictionary insertion order.

### Capability requirements

Provide a small immutable provider-neutral requirement/query model sufficient for technical filtering.

It may contain requirements such as:

- requires streaming
- requires structured output
- requires tool use
- requires vision
- requires cancellation
- minimum context limit

All fields should default to no requirement.

Do not include:

- model quality score
- benchmark score
- latency
- TTFT
- tokens/sec
- RAM/VRAM preference
- cost
- agent persona
- FAST/SMART/CODING/DEEP/FAILSAFE routing mode
- priority

Those belong to later tickets.

### Matching semantics

Capability matching must be conservative.

For boolean requirements:

- if a requirement is true, the capability must explicitly be true
- if a requirement is false/not requested, it should not exclude models

For minimum context:

- known context limit must be greater than or equal to the requirement
- unknown context (`None`) must NOT satisfy a positive minimum-context requirement

Do not infer missing capabilities from model names.

Do not infer capability support from frozen BLAZE-6 candidate roles.

### Query result ordering

Capability filtering must not become routing.

Return matching records in a deterministic neutral order, preferably registry key order.

Do not rank, score, weight, or select a winner.

A helper returning all matches is acceptable.

A helper returning "best model" is NOT acceptable in PHX-012.

### Candidate model strategy

The frozen model strategy lists BLAZE-6 candidates such as Qwen and EmbeddingGemma, but PHX-012 must not hard-code them as factual installed models or capability truth.

Do not seed the registry automatically with:

- qwen3.5:2b
- qwen3.5:4b
- qwen3.5:9b
- qwen2.5-coder:7b
- embeddinggemma
- gpt-oss:20b
- Colibri models

Provider/model discovery and later configuration may populate the registry explicitly.

### No model discovery surface

Do not implement a general model-list discovery system in this ticket.

Do not add `/api/tags` behavior to the registry.

Do not scan the Ollama model library.

PHX-012 consumes explicit provider/model IDs supplied by callers.

### No health conflation

Provider health and model capability are distinct.

Do not store overall PHOENIX readiness in the registry.

Do not treat a registered capability record as proof that the provider is currently healthy or the model is loaded.

Do not call `provider.health()` during a capability refresh unless a concrete requirement is discovered and approved; capability lookup should remain one explicit capability operation.

### No authority

Capability information is descriptive only.

In particular:

`supports_tool_use = true`

never authorizes tool execution.

The registry must not grant permissions or interact with `PermissionEngine`/`ToolRegistry`.

### Concurrency

Support safe use from one PHOENIX asyncio process/event loop.

Registry operations that mutate shared state must not permit partial or corrupted updates when refreshes occur concurrently.

An `asyncio.Lock` is acceptable for mutation/refresh coordination if required.

Do not add threads, multiprocessing, Redis, database locks, or distributed synchronization.

Do not hold a registry lock across provider network I/O if that unnecessarily blocks unrelated registry reads/refreshes.

A clean pattern is:

1. query the provider outside the mutation lock
2. validate identity/result
3. atomically register under the lock

If concurrent refreshes of the SAME key race, define and test deterministic accepted semantics, such as last successfully committed refresh wins.

Do not over-engineer generation counters unless required.

### Side effects

Importing or constructing the registry must not:

- contact Ollama
- contact Colibri
- scan hardware
- open sockets
- start subprocesses
- create files/directories
- access SQLite
- run migrations
- emit events
- start threads
- create hidden background tasks

Only an explicit refresh call may invoke the supplied provider's `capabilities()` method.

### Memory-only

PHX-012 is in-memory only.

Do not:

- create `model_profiles` tables
- change Alembic
- persist capability records
- add cache files
- add JSON/YAML/TOML catalogs

Persistence can be introduced by the ticket that owns model-profile storage.

### No events yet

Do not emit model/provider events from the registry.

PHX-013/later runtime integration will own lifecycle and availability events.

Do not modify the canonical event list.

### No lifecycle

Do not implement PHX-013 behavior:

- loading/unloading
- keep-alive
- model residency
- one-heavy-generation scheduling
- active generation quotas
- GPU memory management

Registered capability does not mean loaded/resident.

### No embeddings

Do not implement PHX-014 behavior or special embedding APIs.

The current PHX-010 capability contract does not contain an embedding capability flag; do not modify it in PHX-012 solely to anticipate PHX-014.

### No Colibri

Do not implement PHX-015.

The registry must remain capable of accepting a future `colibri` provider ID through the neutral contract without importing Colibri code now.

### No benchmarks/routing

Do not implement PHX-016 benchmark metrics or later PHX-023 routing.

No scores, preferences, tiers, heuristics, automatic choices, or hardware-based selection belong here.

### FastAPI/frontend

Do not add:

- `GET /models`
- registry API endpoints
- model settings endpoints
- frontend model lists
- React/Zustand registry state
- Tauri model commands

`GET /health` remains unchanged.

## Suggested API shape

Names may differ, but keep the surface compact, for example:

- `ModelCapabilityRecord`
- `CapabilityRequirements`
- `ModelCapabilityRegistry`
- `register(capabilities)`
- `get(provider_id, model_id)`
- `remove(provider_id, model_id)`
- `list_records()`
- `matching(requirements)`
- `await refresh(provider, model_id)`

Do not treat this suggested naming as permission to expand the ticket.

## Tests

Use deterministic tests with test-only fake providers and, where useful, an HTTPX `MockTransport` OllamaProvider integration case.

At minimum verify:

1. registry construction is empty and side-effect free
2. provider/model keys are distinct
3. register stores a valid capability snapshot
4. get returns the exact registered logical record
5. registering same key replaces rather than duplicates
6. remove deletes only the requested key
7. removing unknown key is explicit/no-op according to documented semantics
8. clear is explicit and deterministic
9. list order is deterministic by provider/model key
10. refresh invokes provider `capabilities(model_id)`
11. refresh stores valid neutral capabilities
12. provider ID contradiction during refresh fails
13. model ID contradiction during refresh fails
14. failed refresh leaves previous record unchanged
15. provider unavailable/error propagates without registry mutation
16. no arbitrary provider object leaks into records
17. timestamps are timezone-aware UTC
18. refreshed timestamp changes on successful replacement
19. requires-streaming filter works
20. requires-structured-output filter works
21. requires-tool-use filter works
22. requires-vision filter works
23. requires-cancellation filter works
24. combined boolean requirements require all requested capabilities
25. minimum-context filter works
26. unknown context does not satisfy positive minimum context
27. unrequested capability false/true does not exclude a model
28. matching returns all matching records, not one winner
29. matching order is deterministic and neutral
30. matching performs no provider calls
31. no BLAZE-6 model candidates are auto-seeded
32. registry contains no routing score/tier policy
33. registry contains no lifecycle/load state
34. registry contains no health/readiness state
35. concurrent refresh of different keys remains consistent
36. concurrent successful refreshes do not corrupt registry state
37. import/construction performs no network/filesystem/database/process/background work
38. explicit fake-provider refresh performs only the provider capability call
39. PHX-010 tests remain green
40. PHX-011 OllamaProvider tests remain green
41. one mocked OllamaProvider capability refresh can populate the neutral registry without Ollama-specific registry code
42. `/health` remains unchanged
43. persistence/events/hardware regressions remain green
44. desktop regressions remain green

Do not add a new testing framework.

## Dependency policy

Expected new direct dependencies: NONE.

Use existing:

- Python 3.11
- asyncio
- Pydantic v2
- PHX-010 contracts

Do not modify `uv.lock` without a real dependency reason.

## Security review

Confirm:

- capability data grants no authority
- tool-use metadata cannot bypass ToolRegistry/PermissionEngine
- no credentials/API keys/tokens
- no provider raw payload persistence
- no prompt/response storage
- no arbitrary Python objects in public records
- no network access except explicit provider capability refresh
- no direct frontend access
- failures/identity contradictions are explicit

## Invariant review

Review all relevant invariants, especially:

- PHOENIX remains supreme orchestrator
- provider specifics do not leak into agents/core policy
- model capability metadata is descriptive, not authorization
- frontend cannot directly invoke providers
- providers remain replaceable
- failures are explicit
- optional/deep providers do not become mandatory
- no generation/model load starts during registry construction or matching

## Verification

Run at minimum:

- targeted PHX-012 tests
- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`
- PHX-010 and PHX-011 regressions
- Phase 0 regressions
- `npm test`
- desktop typecheck/build
- `git diff --check`

No live Ollama request is required for PHX-012. Deterministic mocked provider integration is sufficient.

## Acceptance criteria

- provider-neutral capability registry exists
- registry key is provider ID + model ID
- snapshots are validated, immutable, timestamped, and memory-only
- explicit provider refresh uses only the PHX-010 capability contract
- contradictory/failing refresh does not corrupt existing state
- deterministic neutral capability filtering works
- unknown context is handled conservatively
- no routing, ranking, lifecycle, benchmark, persistence, health, or authority behavior is introduced
- Ollama integration remains adapter-neutral
- prior foundations remain green
- security/invariants reviewed
- ticket/index update only after successful verification

## Completion evidence

When complete, append concise evidence covering files, public API, key semantics, refresh behavior, query/matching semantics, concurrency, tests, regressions, dependency status, security/invariants, and known limitations.

### Verified completion — 2026-10-04

- Created `phoenix/models/registry.py` and `tests/test_model_registry.py`.
  PHX-010/011 contracts, implementations, tests and completion evidence unchanged.
  No dependency changes; `pyproject.toml` and uv-generated `uv.lock` unchanged.
- API: ModelCapabilityRecord, CapabilityRequirements, ModelCapabilityRegistry;
  register(), get(), remove(), clear(), list_records(), matching(), async refresh().
  Registry starts empty. Key is the exact neutral (provider_id, model_id) pair,
  using PHX-010 strict nonempty, whitespace-normalized identifiers.
- Frozen extra-forbidden records retain detached, revalidated ModelCapabilities,
  consistent provider/model IDs and aware UTC refreshed_at. Each successful
  registration/replacement captures datetime.now(UTC); supplied aware record
  timestamps normalize UTC. Deterministic JSON round-trip verified. No raw objects,
  provider payloads, prompts, scores, health or residency fields are stored.
- Register atomically replaces only the exact key. Get returns record/None;
  remove returns true/false; clear affects memory only. Listing/matching return
  immutable tuples sorted by (provider_id, model_id), independent of insertion.
- Refresh validates identifiers, awaits only provider.capabilities(model_id),
  revalidates the snapshot and checks both identities before committing. Identity
  contradictions raise ModelProviderError. Provider/validation exceptions remain
  explicit; failed/cancelled refreshes preserve old record identity and timestamp.
- One process/event-loop concurrency: no-await synchronous mutation after I/O,
  no lock/background workers. Same-key last successfully completed refresh wins;
  independent keys remain independent. Controlled completion races and late
  failures are tested; reads remain available while refresh waits.
- Requirements are frozen/extra-forbidden: five strict booleans default false,
  positive strict min_context_tokens or None. True requires explicit support;
  false imposes no exclusion. Known context must meet the inclusive minimum;
  unknown context cannot satisfy a positive minimum. Return all matches, no winner.
- Deterministic OllamaProvider + HTTPX MockTransport refresh through ModelProvider
  registers neutral capabilities. Only POST /api/show is invoked; interpretation
  remains in the adapter. No live model discovery, generation or inference run.
- Verification:
  - `uv run --locked pytest tests/test_model_registry.py`: PASS; 83 cases.
  - `uv run --locked pytest tests/test_model_provider.py`: PASS; 61 unchanged cases.
  - `uv run --locked pytest tests/test_ollama_provider.py`: PASS; 108 unchanged cases.
  - `uv lock` / `uv sync --locked`: PASS; 36 packages, no lockfile churn.
  - `uv run --locked pytest`: PASS; 378 tests (API 8, bootstrap 11, config 19,
    events 28, hardware 48, ModelProvider 61, registry 83, Ollama 108, persistence 12).
  - `uv run --locked ruff check .`: PASS.
  - `uv run --locked ruff format --check .`: PASS; 32 files.
  - `uv run --locked mypy`: PASS; 30 source files, strict settings unchanged.
  - `uv build`: PASS; source distribution and wheel in ignored dist/.
  - `npm test`: PASS; four tests; desktop workspace typecheck/build: PASS.
- Executable fresh-process isolation guards sockets, writes/mkdir, SQLite,
  subprocesses, threads and hidden tasks; blocks HTTPX/Ollama, hardware, event/core,
  persistence, API, agents, permission/tool and voice imports. Import, construction,
  local operations, JSON round-trip and explicit fake capability refresh pass
  without artifacts. AST checks keep production imports neutral and minimal.
- Security/invariants INV-001..INV-024 reviewed: preserved or unaffected. Capability
  and tool-use facts grant no authority. No credentials, payload persistence,
  prompts/responses, logging, direct frontend/provider path, events, migrations,
  model discovery/loading or routing. Exact health API, Alembic baseline, 32 events
  and desktop implementation unchanged. ADR-025 records the durable registry policy.
- Scope/whitespace and credential-signature review passed. Only registry code,
  its tests, ADR-025, this ticket and the PHX-012 index status change are included.
- Limitations: memory-only snapshots may become stale; no availability/residency
  inference, auto-refresh or routing. Single-process/event-loop use; refresh timing
  and cancellation belong to caller/provider. UTC wall-clock timestamps are not
  monotonic version counters. Existing Starlette deprecation warning remains.
- All required acceptance checks passed before marking DONE. Commit/push only
  the PHX-012 feature branch; final SHA and remote checks are in the execution
  report. PHX-013..PHX-016 remain TODO; PHX-013 was not started.
