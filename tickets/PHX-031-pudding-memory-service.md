# PHX-031 — PUDDING Memory Service

Status: DONE
Phase: 3 — Memory & RAG

## Objective

Implement PHOENIX's first PUDDING-owned memory service over the PHX-030 memory schema.

PHX-031 owns explicit memory admission, durable create/read/administrative-list/replace/delete behavior, exact conflict handling, and truthful `memory.created` observability without implementing document ingestion, vectors, semantic retrieval, context assembly, or citation rendering.

## Authoritative documents

Read and obey in project authority order:

- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `/PRD.md`
- accepted `/DECISIONS.md`
- `/docs/03_AGENT_ROSTER.md`
- `/docs/09_SECURITY.md`
- `/docs/10_MEMORY_SYSTEM.md`
- `/docs/11_RAG_SYSTEM.md`
- `/docs/12_DATABASE_SCHEMA.md`
- `/docs/14_EVENT_SYSTEM.md`
- `/tickets/PHX-005-sqlite-sqlalchemy-alembic-foundation.md`
- `/tickets/PHX-006-event-bus-foundation.md`
- `/tickets/PHX-020-agent-registry.md`
- `/tickets/PHX-030-memory-schema.md`
- this ticket

If authoritative sources materially conflict, stop rather than inventing a resolution.

## Dependencies

Required completed foundations:

- PHX-005 SQLite / SQLAlchemy / Alembic foundation
- PHX-006 EventBus foundation
- PHX-020 AgentRegistry
- PHX-030 Memory schema
- Phase 1 and Phase 2 complete

No new runtime dependency is expected.

## Authoritative owner identity

PUDDING's neutral internal ID is exactly:

`memory`

The service must validate this identity through `AgentRegistry.require("memory")` or an exact equivalent.

`PUDDING` is display-only alias metadata.

Renaming/removing the alias must not change behavior. An alias-only registry lacking internal ID `memory` must fail construction.

The service does not instantiate or execute a logical agent/model process.

## Recommended production scope

Prefer one small service module:

- `phoenix/memory/service.py`

and focused tests:

- `tests/test_pudding_memory_service.py`

Small exports from `phoenix/memory/__init__.py` are acceptable if intentional.

Do not add an API endpoint, frontend page, new persistence table, or background worker.

## Existing PHX-030 contracts are authoritative

Reuse, do not redesign:

- `MemoryType`
- `MemoryRecord`
- `MemoryRow`
- `UTCDateTime`
- `memories` table
- Alembic head `0002_memory_schema`

No PHX-031 migration is expected.

Do not add columns, indexes, constraints, tables, or schema revisions.

## Public service API

A clean public boundary should include equivalents of:

- `MemoryAdmissionVerdict`
- `MemoryAdmissionDecision`
- `MemoryAdmissionPolicy`
- `MemoryListQuery`
- `MemoryEventDelivery`
- `MemoryCreateResult`
- `PuddingMemoryService`
- explicit memory-service errors

Suggested service methods:

- `async create(record: MemoryRecord) -> MemoryCreateResult`
- `get(memory_id: str) -> MemoryRecord | None`
- `list_records(query: MemoryListQuery | None = None) -> tuple[MemoryRecord, ...]`
- `replace(record: MemoryRecord) -> MemoryRecord`
- `delete(memory_id: str) -> bool`

Equivalent names are acceptable if semantics remain exact and obvious.

`create` is asynchronous only because canonical EventBus publication is asynchronous. Read/replace/delete operations may remain synchronous because PHX-005 uses synchronous SQLAlchemy sessions.

No hidden workers/tasks/threads are allowed.

## Constructor dependencies and ownership

The service should receive explicit dependencies rather than creating globals:

- an existing SQLAlchemy `sessionmaker[Session]`
- an existing `EventBus`
- an explicit `MemoryAdmissionPolicy`
- optional/custom `AgentRegistry`, defaulting to the canonical registry if clean

Construction must not:

- open the database
- run migrations
- create directories/files
- subscribe to the EventBus
- publish events
- start workers/tasks/threads

The service does not own/dispose the engine, session factory, EventBus, or registry.

## Exact boundary revalidation

Treat all public model instances crossing into the service as untrusted boundary data.

Revalidate exact stored values rather than trusting a Pydantic instance identity.

Reject:

- subclasses extending a public contract
- `model_copy(update=...)` injected invalid values
- extra fields
- malformed nested metadata
- contradictory IDs

Never persist malformed/fabricated success evidence because an object was already constructed.

## Memory admission policy

PHX-031 must enforce explicit admission before every create or replace.

Do not automatically persist arbitrary chat/model/tool text merely because it reached the service.

Define a small neutral policy protocol such as:

`evaluate(record: MemoryRecord) -> MemoryAdmissionDecision`

The production service must require a policy collaborator. Do **not** ship an allow-all default.

The service itself must not call an LLM/provider to classify memories.

## Admission verdict vocabulary

Use exactly three outcomes or an equivalent closed vocabulary:

- `accepted`
- `rejected_secret`
- `rejected_policy`

`MemoryAdmissionDecision` must be frozen/extra-forbidden and correlate to the exact memory ID.

Only `accepted` may reach durable persistence.

`rejected_secret` must fail with an explicit secret/admission error before any database mutation or event publication.

`rejected_policy` must fail explicitly before any database mutation or event publication.

A malformed decision or mismatched memory ID must fail before persistence.

## Secret boundary — INV-018

Secrets are not stored in normal conversational memory.

PHX-031 must provide the deterministic enforcement point: rejected-secret admission cannot persist.

Do **not** pretend to solve semantic secret detection with brittle keyword/regex heuristics.

The required admission policy is the boundary through which a future trusted secret-classification/security implementation can decide admission.

No built-in allow-all policy.

No `MemoryType.SECRET`.

No credential/token/password/API-key persistence fields.

No memory content/metadata logging.

Completion evidence must state truthfully that correctness still depends on the injected policy classifying secret-bearing content correctly; PHX-045 owns the broader secrets boundary.

## Not every chat turn becomes long-term memory

The service persists only through an explicit `create` call after successful admission.

Do not:

- hook into chat/orchestrator automatically
- extract memories from every message
- call a model to decide what to remember
- auto-create memories during imports/startup

Automatic extraction/consolidation is outside PHX-031.

## Create semantics

`create(record)` must:

1. exact-revalidate the `MemoryRecord`
2. obtain and exact-revalidate admission evidence
3. require correlated `accepted` admission
4. open a short explicit SQLAlchemy session/transaction
5. reject an already-existing exact ID with explicit conflict evidence
6. insert exactly one `MemoryRow`
7. commit successfully
8. only after durable commit, construct and publish one canonical `memory.created` event
9. return an immutable create result

No await should occur before the durable database work solely to create hidden asynchronous behavior.

Do not overwrite an existing row on create.

Do not use database upsert/replace semantics.

## Create result

Return a frozen result containing at least:

- the exact validated `MemoryRecord`
- event-delivery evidence

The returned record must be detached from ORM/session state.

## Canonical `memory.created` event

On each successfully committed new memory, attempt exactly one:

`EventType.MEMORY_CREATED`

Use:

- `agent_id="memory"`
- `correlation_id=record.id`
- INFO severity/default equivalent
- no fabricated task ID

The payload must be privacy-minimal and must contain exactly or equivalently:

- `memory_id`
- `memory_type`

Do not put in the event payload:

- content
- metadata
- source_ref
- embedding_ref
- full source payload
- credentials/secrets

The event is observability evidence only and grants no authority.

Do not modify the canonical 32-event vocabulary.

## Event ordering / durability rule

Never emit `memory.created` before the database commit succeeds.

A failed database write must produce no `memory.created` event.

The durable row is authoritative. Event publication occurs after commit.

## EventBus backpressure semantics

`EventBus.publish()` may reject publication with `EventBusFullError`.

Do not roll back/delete an already committed memory merely because observability backpressure occurs.

Instead return explicit event-delivery evidence, for example a closed enum:

- `published`
- `rejected_full`

The caller must be able to distinguish durable write success from event-delivery failure.

Do not silently swallow the EventBus-full condition.

Do not fabricate that an event was published.

`CancelledError` during/around event publication must propagate; a durable row that already committed remains durable. Do not attempt a fake rollback after commit.

No retry loop is required for event publication.

## Get semantics

`get(memory_id)`:

- validates a strict normalized ID
- performs exact primary-key lookup only
- returns `None` when absent
- returns a newly validated frozen `MemoryRecord` when present
- never returns an ORM row
- does not mutate `last_accessed_at`
- does not auto-delete/hide expired rows
- does not emit events

If stored data cannot reconstruct a valid `MemoryRecord`, fail explicitly with a safe persistence/data error. Do not coerce corruption into success.

## Administrative list semantics

`list_records()` is for deterministic inspection/control, not semantic retrieval.

Use a frozen/extra-forbidden query contract supporting only simple exact administrative filters such as:

- optional exact `MemoryType`
- optional exact `scope`
- optional exact `source_type`
- bounded positive `limit`
- nonnegative `offset`

A good default limit is 100 and a good hard maximum is 500.

Return an immutable tuple of validated `MemoryRecord` objects.

Ordering must be deterministic; prefer:

`created_at ASC, id ASC`

Do not add:

- content substring search
- FTS
- semantic/vector similarity
- relevance score
- recency weight
- importance ranking
- reranking
- context budgeting

Those belong to PHX-034.

Listing must not mutate `last_accessed_at`.

## Expiration semantics

PHX-031 must not silently delete or hide records solely because `expires_at` has passed.

No cleanup worker/job.

Administrative get/list must remain capable of inspecting persisted records including expired records.

Retrieval-time expiry policy belongs to PHX-034 or later explicit integration.

## Replace/update semantics

Provide one explicit identity-scoped replacement/update operation.

A clean design is:

`replace(record: MemoryRecord) -> MemoryRecord`

Rules:

- exact-revalidate the replacement record
- admission is required again
- target ID must already exist
- unknown ID fails explicitly
- `created_at` must exactly equal the stored record's `created_at`
- replacement `updated_at` must not be earlier than the currently stored `updated_at`
- equality is allowed; timestamp is wall-clock evidence, not a unique version counter
- no content-based lookup/upsert
- no automatic merge
- no automatic contradiction resolution
- commit exactly one explicit replacement
- return a newly validated detached `MemoryRecord`
- emit no `memory.created` event for replace

There is no canonical memory-updated event in the frozen 32-event vocabulary; do not invent one in PHX-031.

## Contradictions and conflicts

Contradictions must never be silently overwritten.

Therefore:

- two different IDs may contain identical or contradictory content
- create with duplicate ID fails explicitly
- replace affects only the exact requested ID
- there is no upsert-by-content
- there is no replace-by-scope
- there is no automatic deduplication
- there is no LLM merge/reconciliation

Use explicit conflict/not-found errors where appropriate.

## Delete semantics

`delete(memory_id)`:

- validates strict normalized ID
- deletes only that exact ID
- commits explicitly
- returns `True` if a row was deleted
- returns `False` if no row existed
- does not cascade into future document/vector tables
- emits no fabricated event because no canonical memory-deleted event exists

Deletion is a storage operation, not a permission grant. Future API/PermissionEngine integration controls who may invoke it.

## Row <-> domain conversion

Centralize deterministic conversion between `MemoryRecord` and `MemoryRow`.

Requirements:

- no mutable ORM object leaks through the public service API
- DB rows are revalidated through `MemoryRecord`
- stored UTC-aware semantics remain intact
- metadata is revalidated/deep-detached again on read
- malformed stored rows fail explicitly

Do not bypass PHX-030 validation merely because the database previously accepted a row.

## Transaction and error semantics

Every database operation must use a short explicit session scope.

On persistence failure:

- rollback/close through normal SQLAlchemy session semantics
- raise a concise service error
- do not include content/metadata/private DB paths in error text
- do not silently recreate/delete the database
- do not silently retry
- do not convert failure into success

Pre-existing database schema/migration setup remains caller-owned.

The service does not run Alembic.

## Session/engine ownership

The injected session factory is borrowed.

The service must not:

- dispose the engine
- replace the session factory
- create a second database
- create the data directory
- run migrations

Tests explicitly prepare/migrate temporary SQLite databases before service use.

## No retrieval / RAG leakage

PHX-031 is not PHX-034.

Do not implement:

- semantic retrieval
- vector similarity
- top-k
- relevance scoring
- recency/importance ranking
- reranking
- context assembly
- model prompt assembly

Administrative exact list filters are not retrieval ranking.

## No document ingestion

PHX-032 owns document ingestion.

Do not implement parsers/chunkers/upload flow or `documents`/`document_chunks` schema.

## No VectorStore

PHX-033 owns VectorStore/sqlite adapter.

Do not add sqlite-vec/FAISS/Chroma, embedding tables, vectors, nearest-neighbor queries, or vector indexes.

Do not call PHX-014 embedding providers.

`embedding_ref` remains opaque.

## No citations

PHX-035 owns provenance/citation behavior.

Do not dereference/verify/render `source_ref`.

## No provider/model/tool execution

PHX-031 must not call:

- ModelProvider/Ollama/Colibri
- embedding providers
- ModelLifecycleManager
- benchmark harness
- AgentModelRouter
- AIZEN
- PHOENIX orchestrator
- LEVI
- MAHORAGA
- KAIDO
- ToolRegistry
- PermissionEngine

Admission policy is a neutral memory-policy collaborator, not a model/tool/permission authority.

## User-visible control boundary

The service must expose sufficient exact get/list/delete behavior for future user-visible memory controls.

Do not implement the UI/API itself.

PHX-085 owns the Memory/files workspace UI.

## No frontend/API integration

Do not add FastAPI routes, WebSocket handlers, Tauri commands, React state, or direct frontend database access.

`GET /health` remains exactly unchanged.

## No new persistence schema

Expected Alembic head remains:

`0002_memory_schema`

Do not create revision `0003`.

Do not edit `0001_baseline.py` or `0002_memory_schema.py` except only if a genuine verified PHX-030 defect blocks this ticket; if so stop and report rather than broadening scope silently.

## Side-effect isolation

Importing or constructing service/contracts must not:

- open database connections
- create files/directories
- run migrations
- open network sockets
- call providers/models/embeddings
- launch subprocesses
- start threads
- create asyncio tasks
- publish events
- execute tools

Explicit service methods may perform only their documented database work and, for successful create, one EventBus publication attempt.

## Test requirements

Create focused deterministic tests, preferably `tests/test_pudding_memory_service.py`.

At minimum prove:

1. authoritative owner ID is `memory`
2. default registry construction succeeds
3. missing `memory` ID fails
4. PUDDING alias alone cannot establish authority
5. renamed/removed alias does not change behavior
6. constructor opens no DB and publishes no event
7. exact admission verdict vocabulary
8. admission decision frozen/extra-forbidden
9. decision memory-ID correlation required
10. service requires explicit policy collaborator
11. no allow-all default policy exists
12. create exact-revalidates MemoryRecord
13. malformed/copied/extended record rejected
14. policy called exactly once for eligible create
15. accepted create commits exactly one row
16. rejected-secret create persists nothing
17. rejected-policy create persists nothing
18. malformed/mismatched admission persists nothing
19. admission exception produces no persistence/event
20. duplicate ID create fails explicitly
21. duplicate ID create preserves original row
22. duplicate ID create emits no event
23. duplicate/contradictory content with different IDs may coexist
24. create returns detached frozen record
25. canonical `memory.created` event emitted after successful commit
26. event type exactly memory.created
27. event agent ID exactly memory
28. event correlation ID exactly memory ID
29. event payload contains only memory_id and memory_type
30. event payload contains no content/metadata/source_ref/embedding_ref
31. DB failure emits no event
32. event bus full does not undo committed row
33. event bus full is explicit in create result
34. no event publication retry loop
35. get exact ID returns record
36. get missing returns None
37. get returns no ORM row
38. get does not update last_accessed_at
39. get does not hide expired row
40. malformed stored row fails explicitly on conversion
41. list query frozen/extra-forbidden
42. list default limit bounded
43. invalid limits/offset rejected
44. exact type filter works
45. exact scope filter works
46. exact source_type filter works
47. combined exact filters work
48. deterministic created_at/id ordering
49. list result immutable tuple
50. list does not mutate last_accessed_at
51. list includes expired records
52. no content search/relevance/ranking behavior
53. replace requires existing exact ID
54. replace unknown ID fails
55. replace exact-revalidates MemoryRecord
56. replace requires admission again
57. replace rejects created_at change
58. replace rejects updated_at earlier than stored
59. replace allows equal/later updated_at
60. replace affects only requested ID
61. replace emits no memory.created event
62. replace performs no automatic contradiction merge
63. delete existing returns True and removes exact row
64. delete missing returns False
65. delete validates ID
66. delete does not affect other rows
67. delete emits no fabricated event
68. persistence exceptions are safe/explicit
69. errors contain no memory content/metadata/private DB path
70. service never runs migrations
71. Alembic head remains 0002_memory_schema
72. 0001/0002 migration files unchanged
73. no documents/document_chunks table
74. no embedding/vector table
75. no vector/provider/model call
76. no retrieval/reranking/context assembly
77. no citation rendering
78. no API/frontend integration
79. no ToolRegistry/PermissionEngine authority
80. no hidden tasks/threads/subprocess/network at import/construction
81. canonical 32 event types unchanged
82. `/health` unchanged
83. PHX-030 schema tests remain green
84. persistence tests remain green
85. Phase-1 regressions green
86. Phase-2 regressions green
87. desktop regressions green

Add more exact transaction/privacy/corruption/isolation cases where useful.

## Dependencies

Expected dependency changes: **none**.

Use only existing Python 3.11, Pydantic v2, SQLAlchemy 2, EventBus/core events, AgentRegistry, and PHX-030 memory contracts.

Do not add an async DB driver, repository framework, secret scanner, vector library, or model dependency.

## Verification

At minimum run:

- `uv run --locked pytest tests/test_pudding_memory_service.py`
- `uv run --locked pytest tests/test_memory_schema.py`
- `uv run --locked pytest tests/test_persistence.py`
- all Phase-1 suites
- all Phase-2 suites
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
- `git diff --check`

Also inspect Alembic ScriptDirectory and prove head remains exactly `0002_memory_schema`.

## Security / invariant review

Review INV-001 through INV-024, especially:

- INV-001 — PUDDING is subordinate; PHOENIX remains supreme
- INV-003/004 — memory/admission data grants no permission or tool authority
- INV-011 — frontend has no direct DB/service bypass
- INV-014 — alias independence
- INV-017 — imported/web/document/model content remains untrusted data
- INV-018 — rejected-secret admission never persists to normal memory
- INV-020 — no unversioned schema mutation
- INV-021 — DB/admission/event failures remain explicit
- INV-022 — event backpressure is explicit rather than fabricated success
- INV-024 — no optional vector/research dependency becomes required

## ADR policy

At most one ADR.

A useful ADR-040 may document together:

- PUDDING service internal ID `memory`
- explicit admission-policy boundary with no allow-all default
- accepted/rejected-secret/rejected-policy outcomes
- exact durable create/get/list/replace/delete semantics
- no content-based upsert or contradiction merge
- administrative list is not semantic retrieval
- expired records remain inspectable; no cleanup worker
- `memory.created` only after commit with privacy-minimal payload
- EventBus full does not roll back durable memory and is returned explicitly
- replace emits no invented update event
- PHX-032/033/034/035 remain deferred

Do not add multiple ADRs.

## Completion gate

Only after all deterministic gates pass:

- mark this ticket DONE
- change only `PHX-031 PUDDING memory service` from TODO to DONE in `FEATURE_TICKETS.md`
- leave PHX-032 through PHX-035 TODO
- record exact verification evidence and limitations

## Commit / push

Exact implementation commit message:

`feat: complete PHX-031 PUDDING memory service`

Push only:

`feature/PHX-031-pudding-memory-service`

Do not create a PR, merge, push main, force-push, or start PHX-032.

## Completion report

Report at minimum:

- status/path/branch/start/final SHA
- files created/modified
- dependency status
- service public API
- authoritative `memory` identity / alias independence
- constructor dependency ownership
- admission contract/policy/verdicts
- secret rejection evidence
- create transaction semantics
- duplicate-ID/conflict semantics
- create result / event delivery semantics
- exact memory.created event evidence and privacy
- EventBus-full behavior
- get/list/replace/delete semantics
- expiration behavior
- row/domain revalidation
- contradiction/no-upsert boundary
- document/vector/retrieval/citation separations
- provider/tool/permission separation
- schema/Alembic unchanged evidence
- targeted test result
- PHX-030/persistence regressions
- Phase-1 / Phase-2 regressions
- full Python/lint/type/build result
- foundation/desktop result
- side-effect/security/invariant evidence
- ADR if any
- acceptance criteria
- staged diff review
- commit/push/main safety
- known limitations
- next ticket: PHX-032 — Document Ingestion

## Verified completion — 2026-10-05

- Added phoenix/memory/service.py and tests/test_pudding_memory_service.py;
  changed only this ticket, its index status and one accepted ADR-040.
  Starting prepared feature HEAD: bb1994b8a312e1da44428d4d1962c67b0c001a40.
- Public API: MemoryAdmissionVerdict, MemoryAdmissionDecision,
  MemoryAdmissionPolicy.evaluate(record), MemoryListQuery, MemoryEventDelivery,
  MemoryCreateResult, PuddingMemoryService and explicit safe input/admission/
  secret/policy/conflict/not-found/persistence/data/event errors. Service methods:
  async create(record), get(memory_id), list_records(query=None), replace(record),
  delete(memory_id); read-only agent_id is memory.
- Construction exact-revalidates AgentRegistry and requires internal ID memory.
  Removed/renamed/PUDDING aliases behave identically; alias-only/missing identity
  fails. Borrowed sessionmaker/EventBus and mandatory policy remain caller-owned.
  No constructor DB access, migration, directory, event subscription or worker.
- Create/replace revalidate exact record snapshots and detached nested metadata,
  then evaluate policy once. Exact frozen decision must correlate by memory_id;
  accepted/rejected_secret/rejected_policy is the closed vocabulary. Rejected,
  malformed, extended, forged, mismatched or failed admission never opens a
  session or publishes an event. No production allow-all default or regex/model
  secret detector. Policy mutation cannot alter persistence evidence.
- Create inserts one row under an explicit transaction; duplicate ID and actual
  SQLite primary-key race raise conflict without overwrite. Different IDs may
  have identical/contradictory content. No upsert, merge or reconciliation.
- Session commit and close precede one canonical memory.created attempt. A test
  observes after_commit and close ordering, then verifies the durable row from
  an independent session inside publish. Event INFO, agent_id memory, correlation
  ID equals record ID, task_id None; payload exactly memory_id/memory_type, with
  no content, metadata or private references. Frozen result detaches the record.
- Real EventBus capacity rejection returns rejected_full while preserving the
  committed row; no retry or partial subscriber delivery. Real lock-blocked bus
  cancellation propagates after commit with the row intact. Other publication
  failures raise a safe MemoryEventError stating memory committed. Cancellation
  during admission propagates before session access.
- Get uses a strict normalized exact ID, returns None if absent, otherwise a
  revalidated frozen record. List uses exact type/scope/source_type filters,
  created_at ASC/id ASC, strict limit default 100/max 500 and offset >= 0; result
  is a tuple. Neither changes last_accessed_at or hides expired records.
- Replace admits again, requires an existing exact ID, preserves created_at and
  rejects updated_at earlier than stored; equality/later updates succeed. Only
  the requested row changes. Delete commits only the exact ID, returns True/False
  for present/absent and preserves other rows. Neither emits invented events.
- Central row/domain mapping preserves all fourteen fields, UTC microseconds,
  opaque references and deeply detached metadata. No ORM object escapes. Invalid
  stored fields, JSON and timestamp decoding raise safe errors. Commit failures
  roll back and close without publishing; formatted errors omit SQL parameters,
  private content and database paths. Missing directories are never recreated.
- No chat extraction/hooks, ingestion/parsers/chunkers, document/vector tables,
  embedding calls, retrieval/ranking/FTS/context/citations, providers/models/tools/
  permissions, API/frontend or startup integration. Canonical event count is 32.
- uv run --locked pytest tests/test_pudding_memory_service.py -q:
  167 passed in 26.47s. PHX-030 schema regression: 244 passed in 20.22s.
  Persistence: 12 passed in 2.24s. Timings include concurrent verification load.
- Individual locked Phase-1 suites passed: model_provider 61, ollama_provider
  108, model_registry 83, model_lifecycle 54, embeddings 182, colibri_provider
  194, model_benchmark 130 (812 total). Individual Phase-2 suites passed:
  agent_registry 96, phoenix_orchestrator 101, aizen_planner 151,
  agent_model_router 213, task_graph_state_machine 348, structured_agent_handoffs
  214, levi_verification 242, mahoraga_recovery 790, kaido_failsafe 574
  (2,729 total). Existing production/test files remain unchanged.
- uv lock / uv sync --locked passed, 36 packages; manifests and locks unchanged.
  uv run --locked pytest -q: 4,078 passed in 34.59s; one pre-existing
  Starlette/httpx deprecation warning. Ruff check/format passed (65 files).
  Strict mypy passed (62 source files). uv build produced wheel and sdist.
- Foundation bootstrap/API/config/persistence/events/hardware: 126 passed in
  6.49s. Exact /health remains status=ok/service=phoenix-api/version=0.1.0.
  npm test: 4 passed. Desktop workspace typecheck and build passed.
- Alembic ScriptDirectory has exactly one head, 0002_memory_schema. Temporary
  SQLite has only alembic_version/memories at that revision. Both existing
  migration files and PHX-030 domain/ORM are byte-equivalent to origin/main;
  no table, column, constraint, index, revision or event vocabulary change.
- Fresh-interpreter isolation blocks writes, DB connections/session acquisition,
  network/subprocess, forbidden subsystem imports, subscriptions/publication,
  hidden tasks/threads. Import/constructor/contracts pass with an empty temporary
  directory; only stdlib platform metadata is preinitialized for Windows as in
  PHX-005. AST scope checks pass. Credential-signature scan: 155 repository files,
  zero matches. INV-001 through INV-024 and permission/security implications
  reviewed; evidence remains untrusted and grants no permission or tool authority.
- All deterministic acceptance criteria pass. ADR-040 records the admission,
  storage, administrative inspection and post-commit observability boundaries.
  Full staged diff reviewed: only these five intended files; no unrelated changes,
  runtime integration, dependency/schema churn or hidden execution. Diff check PASS.
- Limitations: trusted admission policy must classify secrets correctly; PHX-045
  owns broader secret handling. Caller owns permissions/schema/dependencies.
  Synchronous SQLite work has no background worker, optimistic version counter
  or semantic conflict resolution. Post-commit publication has no durable outbox,
  replay or retry, so cancellation/full/failure may leave storage without an event.
  No live model/provider calls were needed. PHX-032 through PHX-035 remain TODO;
  next ticket PHX-032 — Document Ingestion was not started.
