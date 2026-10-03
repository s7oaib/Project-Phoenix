# PHX-013 — Model Lifecycle Manager

Status: TODO

Phase: 1 — Model Runtime

## Objective

Implement PHOENIX's provider-neutral model lifecycle coordination foundation for bounded local generation concurrency and explicit in-memory model lifecycle state, without introducing model routing, benchmarking, provider-specific process control, or false residency claims.

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
- `docs/25_DEPENDENCIES.md`
- `tickets/PHX-010-model-provider-interface.md`
- `tickets/PHX-011-ollama-provider.md`
- `tickets/PHX-012-model-capability-registry.md`
- this ticket

## Dependencies

- PHX-001 through PHX-012 — DONE

## Requirements

### Boundary

Implement under `phoenix/models/` as a provider-neutral lifecycle coordination layer.

It may depend on neutral PHX model contracts such as `ModelProvider`, `ModelCapabilities`, and the capability registry only where useful. It must not expose Ollama/Colibri internals through its public API.

Do not make agents, FastAPI, frontend, persistence, permissions, tools, voice, or hardware modules depend on provider-specific runtime details.

### BLAZE-6 concurrency policy

The frozen model strategy requires one heavy local generation at a time on BLAZE-6 unless later benchmarks justify more.

Provide an async bounded heavy-generation permit/lease mechanism with default capacity `1`.

Requirements:

- positive configurable capacity
- callers can await acquisition
- capacity never exceeds the configured limit or becomes negative
- waiting is FIFO/fair to the extent naturally provided by the chosen primitive, but do not invent a scheduler
- cancellation while waiting leaks no capacity
- lease exit/release is exception-safe
- cancellation of a caller using an async context manager leaks no capacity when the lease unwinds
- release is single-use/idempotence-safe; double release must fail explicitly or otherwise never over-increment capacity
- no hidden worker task is required

An explicit async context-manager lease is preferred.

### Neutral model identity

Where lifecycle state is keyed, use `(provider_id, model_id)` rather than model ID alone.

Reuse PHX identifier validation where practical.

### Lifecycle state

Represent only PHOENIX-owned coordination state.

A small typed vocabulary such as `unknown`, `preparing`, `ready`, `busy`, `releasing`, `unavailable` is acceptable if justified by implementation.

Important:

- a capability-registry record does NOT prove a model is loaded/resident
- a successful generation does NOT automatically prove persistent residency
- the manager must not claim a model is physically loaded or unloaded without direct provider evidence
- do not invent provider-specific load/unload semantics absent from the neutral provider contract

If state transitions are exposed, use a small explicit legal transition table and reject illegal transitions.

### Lifecycle records

If records are stored, they must be immutable snapshots and memory-only.

Useful neutral fields may include:

- provider_id
- model_id
- lifecycle state
- active heavy-generation lease count if meaningful
- UTC timestamp of last transition

Do not store:

- prompts
- responses
- secrets
- provider raw payloads
- benchmark scores
- routing weights
- agent aliases
- hardware fingerprints

### Provider interaction

PHX-013 must not add provider-specific lifecycle endpoints or process controls.

Do not:

- kill Ollama
- terminate processes
- unload models
- pull models
- call undocumented runtime APIs
- add Colibri logic
- call `generate()` merely to infer lifecycle state

The manager may coordinate callers that already possess a provider/model choice, but it does not choose that provider/model.

### Keepalive

The model strategy notes that keep-alive should reduce unnecessary model swaps. Do not implement provider-specific keepalive behavior in PHX-013 unless it already exists behind a neutral supported contract. It is acceptable to record this as later provider/lifecycle integration work.

### No routing

Do not implement:

- FAST / SMART / CODING / DEEP / FAILSAFE selection
- tier assignment
- preferred models/providers
- fallback chains
- agent-to-model assignment
- benchmark-based selection
- hardware-based selection

PHX-013 coordinates lifecycle only.

### No benchmark harness

Do not measure or rank TTFT, throughput, quality, RAM, VRAM, or disk I/O. PHX-016 owns benchmarking.

### No embeddings / Colibri

Do not implement PHX-014 or PHX-015.

### Events

The canonical event vocabulary already contains `model.loading`, `model.loaded`, `model.unloaded`, and `provider.unavailable`.

Event emission is not required for PHX-013. Do not emit fake provider-residency events. If any event support is introduced, it must be injected through the neutral core event boundary and correspond only to truthful PHOENIX-owned coordination transitions.

### Persistence / API / frontend

- memory-only lifecycle state
- no Alembic changes
- no model-lifecycle tables
- no new FastAPI endpoints
- no frontend model UI
- keep `GET /health` unchanged

### Side effects

Importing or constructing the lifecycle manager must not:

- contact Ollama/Colibri
- open sockets
- launch subprocesses
- inspect hardware
- write files/directories
- access SQLite/Alembic
- emit events
- start background tasks/threads

Awaiting a heavy-generation permit may suspend only on in-memory asyncio synchronization.

## Tests

Use deterministic in-process tests. No live Ollama dependency is required.

At minimum verify:

1. module import is side-effect free
2. manager construction is side-effect free
3. default heavy-generation capacity is one
4. positive custom capacity accepted
5. zero/negative capacity rejected
6. first lease acquires
7. second lease blocks when capacity is exhausted
8. release lets a waiter proceed
9. async context-manager release is exception-safe
10. waiting-task cancellation leaks no capacity
11. holding-task cancellation/context unwind leaks no capacity
12. double release cannot over-increment capacity
13. current/available permit accounting remains bounded
14. custom capacity permits exactly that many simultaneous holders
15. independent provider/model keys remain distinct where records are used
16. invalid provider/model identifiers rejected
17. legal state transitions succeed where transitions exist
18. illegal transitions fail explicitly
19. capability registry presence does not imply loaded/ready state
20. no model/provider routing or ranking fields exist
21. no provider generation/health/capability calls occur during lease operations
22. no live model discovery
23. no filesystem/database/network/subprocess side effects
24. no hidden background tasks/threads
25. PHX-010 tests remain green
26. PHX-011 tests remain green
27. PHX-012 tests remain green
28. Phase 0 foundations remain green
29. desktop regressions remain green

## Dependency policy

Expected new direct dependencies: none.

Use Python 3.11, asyncio, Pydantic v2 and existing PHOENIX contracts.

## Acceptance criteria

- provider-neutral lifecycle manager exists
- BLAZE-6 default heavy-generation capacity is one
- permit/lease accounting is bounded, deterministic and cancellation-safe
- lifecycle records/state never fabricate provider residency
- no routing, benchmarking, embeddings, Colibri, provider process control, persistence, API or frontend work is introduced
- construction/import is side-effect free
- prior provider/foundation contracts stay green
- security/invariants reviewed
- ticket/index marked DONE only after successful verification

## Completion evidence

When complete, append concise evidence covering implementation files, public API, lease/capacity semantics, lifecycle states/transitions if used, cancellation safety, provider neutrality, tests, regressions, security/invariants and known limitations.
