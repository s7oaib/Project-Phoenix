# PHX-013 — Model Lifecycle Manager

Status: DONE

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

### Verified completion — 2026-10-04

- Created `phoenix/models/lifecycle.py` and `tests/test_model_lifecycle.py`.
  PHX-010/011/012 implementations, tests and completion records unchanged.
  No dependency change; `pyproject.toml`/`uv.lock` unchanged after uv verification.
- API: ModelLifecycleManager(heavy_generation_capacity=1), acquire_heavy() with
  keyword provider_id/model_id/operation_id; capacity, active_count, available_count,
  active_leases(), is_active(). GenerationLease exposes read-only snapshot/released,
  synchronous idempotent release() and async context management. One small
  ModelLifecycleError boundary; frozen extra-forbidden LeaseSnapshot with validated
  neutral identities and UTC acquired_at, deterministic JSON serialization.
- Capacity is global per shared manager, not provider/model. Strict positive
  integer overrides accepted; zero/negative/bool/noninteger rejected. No hardware
  discovery or model choice. available_count = capacity - committed active leases,
  including transient waiter handoff slots; it does not promise immediate admission.
  Active snapshot tuples sort by operation_id and never expose internal handles.
- BoundedSemaphore suspends only callers. Duplicate active IDs reject before
  waiting and after permit acquisition; released IDs may be reused. No await
  between permit grant and validated registration. Pre-commit BaseException returns
  capacity and propagates. Release checks actual handle identity and atomically
  unregisters/returns capacity without await; stale/repeated release cannot inflate
  capacity. Released/nested context entry rejects explicitly.
- Deterministic events/checkpoints verify capacity 1/2, different provider/model
  global blocking, duplicate races, queued and woken cancellation, remaining waiter
  progress, immediate pending cancellation, holder repeated cancellation, original
  RuntimeError/ModelProviderError propagation, construction failure/CancelledError,
  context-exit cancellation, wrong-loop use, immutable inspection and ID reuse.
  No arbitrary timed sleeps or hidden task machinery in production.
- Coordination state is completely represented by active lease snapshots/counts;
  no separate state enum or transition API needed. No physical loaded/unloaded,
  residency, readiness or provider availability claim. No registry dependency or
  mutation, provider probes/generation, load/unload/keepalive, routing, benchmark,
  event, persistence, API, frontend, embedding or Colibri implementation.
- Verification commands/results:
  - `uv run --locked pytest tests/test_model_lifecycle.py`: PASS; 54 cases in each
    of three consecutive final-code runs (0.55, 0.55, 0.61 seconds).
  - `uv run --locked pytest tests/test_model_provider.py`: PASS; 61 unchanged cases.
  - `uv run --locked pytest tests/test_ollama_provider.py`: PASS; 108 unchanged cases.
  - `uv run --locked pytest tests/test_model_registry.py`: PASS; 83 unchanged cases.
  - `uv lock` / `uv sync --locked`: PASS; 36 packages, no lockfile churn.
  - `uv run --locked pytest`: PASS; 432 tests (API 8, bootstrap 11, config 19,
    events 28, hardware 48, lifecycle 54, provider 61, registry 83, Ollama 108,
    persistence 12). Existing Starlette deprecation warning only.
  - `uv run --locked ruff check .`: PASS.
  - `uv run --locked ruff format --check .`: PASS; 34 files.
  - `uv run --locked mypy`: PASS; 32 source files, strict configuration unchanged.
  - `uv build`: PASS; source distribution/wheel in ignored dist/.
  - `npm test`: PASS; four tests; desktop workspace typecheck/build: PASS.
- Fresh-interpreter isolation guards sockets, file writes/mkdir, SQLite,
  subprocesses, threads, and task creation; blocks provider adapter/HTTP, registry,
  hardware, events/core, API, persistence, agents, tools, permissions and voice
  imports. Import/construct and explicit permit/context/release/inspection pass.
  Harness alone creates the caller task via a saved public loop method; task
  creation remains blocked for module/operations. No filesystem artifacts appear.
  AST checks restrict production imports to asyncio, datetime, typing, Pydantic
  and neutral provider validation contracts. No live provider operation performed.
- Reviewed security and INV-001..INV-024: preserved or unaffected. PHOENIX remains
  orchestrator; leases/capability/output grant no authority. No credentials,
  prompts/responses/raw payload storage, logging, commands, process control or
  frontend path. Exact health API, Alembic baseline, 32 events and desktop unchanged.
  ADR-026 records the durable resource policy. Scope/whitespace and credential
  signatures reviewed before commit; no generated/private files included.
- Limitations: one event loop/process; all relevant callers must share the manager
  and explicitly use leases. Manually abandoned leases require caller cleanup.
  Queue waits require caller cancellation/deadlines; no scheduler fairness promise.
  Manager owns no background resources and needs no close method. It does not
  infer residency or implement neutral load/unload/keepalive absent from PHX-010.
  Process restart loses memory-only coordination state. Existing warning remains.
- All acceptance checks passed before marking DONE. Commit/push only PHX-013;
  final SHA/remote verification are in the execution report. PHX-014..016 remain
  TODO; PHX-014 was not started.
