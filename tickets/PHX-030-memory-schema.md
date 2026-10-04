# PHX-030 — Memory Schema

Status: TODO
Phase: 3 — Memory & RAG

## Objective

Establish PHOENIX's first authoritative memory domain/persistence schema without
implementing the PUDDING memory service, document ingestion, vector storage,
retrieval, or citations.

PHX-030 turns the frozen memory record design into validated neutral contracts and
one versioned SQLite `memories` table owned by the existing PHX-005
SQLAlchemy/Alembic boundary.

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
- `/tickets/PHX-014-embeddinggemma-integration.md`
- this ticket

If authoritative sources materially conflict, stop rather than inventing a
resolution.

## Dependencies

Required completed foundations:

- PHX-005 SQLite / SQLAlchemy / Alembic foundation
- PHX-014 embedding-provider boundary, only to preserve embedding separation
- PHX-020 agent registry, only to preserve PUDDING/VEGAPUNK ownership semantics
- Phase 2 complete through PHX-028

No new runtime dependency is expected.

## Ownership boundary

PUDDING (`memory`) owns long-term memory behavior.

PHX-030 does **not** implement PUDDING runtime behavior. It defines data shape only.
Do not instantiate or invoke an agent, model, provider, tool, or orchestrator from
the schema layer.

VEGAPUNK (`knowledge_rag`) owns RAG/knowledge indexing. Vector storage remains an
implementation detail behind later adapters.

## Production scope

Prefer a small split such as:

- `phoenix/memory/schema.py` — neutral immutable domain contracts
- `phoenix/persistence/memory.py` or `phoenix/persistence/models/memory.py` —
  SQLAlchemy mapping
- one Alembic revision after `0001`
- focused tests in `tests/test_memory_schema.py`

A tiny `phoenix/memory/__init__.py` or persistence-model package is acceptable if
needed for clean imports.

Do not build repository/service abstractions in this ticket.

## Exact memory classes

Represent exactly the six classes frozen by `docs/10_MEMORY_SYSTEM.md`:

- `working`
- `episodic`
- `semantic`
- `user`
- `project`
- `agent`

Use a strict typed enum such as `MemoryType`.

Do not add document/RAG chunk types or a `secret` memory class.

## Domain memory record

Create one frozen, extra-forbidden neutral memory record contract equivalent to
`MemoryRecord` with the documented fields:

- `id`
- `type`
- `content`
- `source_type`
- `source_ref`
- `scope`
- `confidence`
- `importance`
- `created_at`
- `updated_at`
- `last_accessed_at`
- `expires_at`
- `embedding_ref`
- `metadata`

Field names may differ internally only where a framework has a reserved name. The
public/domain contract should preserve the documented vocabulary.

## Stable ID contract

`id` must be a strict nonempty string with outer-whitespace normalization.

A UUID4 default is acceptable and preferred.

Reject booleans, numbers, bytes, arbitrary objects, empty strings, and
whitespace-only values.

IDs are stable evidence. The schema does not silently replace a caller-supplied
valid ID.

## Content contract

`content` is strict meaningful text.

- reject empty/whitespace-only content
- preserve meaningful caller formatting
- do not automatically log it
- do not execute it
- treat it as untrusted data

The schema must not perform prompt interpretation, command execution, or automatic
memory admission decisions.

## Source provenance fields

`source_type` is a strict nonempty normalized string identifying the source class.
Do not prematurely freeze a closed source-type enum; later memory/document/tool
sources must remain extensible.

`source_ref` is an optional strict nonempty normalized opaque reference.

Do not dereference `source_ref` in PHX-030.

Do not add foreign keys to future tables that do not yet exist.

## Scope

`scope` is a strict nonempty normalized string used later for retrieval isolation.

Do not prematurely freeze a closed scope enum. PHX-034 owns retrieval/context
assembly semantics.

The schema itself performs no retrieval.

## Confidence and importance

Use explicit normalized numeric scores in the inclusive range `0.0 .. 1.0` for:

- `confidence`
- `importance`

Reject booleans, NaN, infinities, negative values, and values above 1.

The schema records the scores only. It does not calculate, rank, rerank, or retrieve
memory.

## Timestamp contract

All public memory datetimes must be timezone-aware and normalized to UTC.

Required:

- `created_at`
- `updated_at`

Optional:

- `last_accessed_at`
- `expires_at`

Validate at least:

- naive datetime rejected
- `updated_at >= created_at`
- if present, `last_accessed_at >= created_at`
- if present, `expires_at >= created_at`

Wall-clock timestamps are evidence, not monotonic sequence numbers.

The SQLite persistence representation must round-trip UTC-aware values truthfully.
Do not rely on a SQLite mapping that silently returns naive datetimes if tests prove
that behavior. A small explicit SQLAlchemy type/encoding is acceptable if required.

## Embedding reference

`embedding_ref` is optional opaque normalized metadata only.

PHX-030 must NOT:

- store an embedding vector in the memory row
- call EmbeddingGemma/OllamaEmbeddingProvider
- create `embedding_records`
- create a vector index
- add a foreign key to a future embedding table

PHX-033 owns the VectorStore/sqlite adapter.

## Metadata contract

`metadata` is an optional/default-empty JSON-compatible object.

Requirements:

- top level is an object/map with string keys
- values are JSON-compatible data only
- reject arbitrary Python/runtime objects, bytes, callbacks, NaN and infinities
- detach/deep-snapshot caller containers so later caller mutation does not change the
  validated memory record
- no executable behavior is derived from metadata

SQLAlchemy declarative classes already own a `.metadata` attribute. Therefore the
ORM Python attribute may use a safe name such as `metadata_json` while explicitly
mapping to database column name `metadata`. Do not break SQLAlchemy's declarative
metadata API.

## Secret boundary

INV-018 and `docs/09_SECURITY.md` are authoritative: secrets do not belong in normal
conversational memory or embeddings.

PHX-030 must:

- introduce no password/token/API-key/credential/secret fields
- never seed tests or migrations with real secrets
- never log memory content/metadata
- not add a special secret-memory persistence path

PHX-030 is a schema layer and must not pretend it can reliably detect every semantic
secret inside arbitrary content. Admission/secret-rejection policy belongs to the
PHX-031 service/security integration. Document this limitation truthfully rather
than implementing brittle secret heuristics.

## Contradiction boundary

The schema must not silently overwrite contradictory memories.

Do not add uniqueness constraints on `content` that collapse distinct records.
Do not implement merge/upsert/reconciliation behavior.

PHX-031 owns memory-service behavior around creation/update/deletion and conflict
handling.

## SQLAlchemy memories table

Add exactly one new planned domain table in PHX-030:

`memories`

It must use the PHX-005 shared `Base`.

Database columns must represent the documented memory record fields.

A clean ORM mapping may use Python-safe attribute names while preserving database
column names such as `type` and `metadata`.

Minimum database expectations:

- stable string primary key `id`
- constrained memory `type`
- `content` as text, not a large binary
- `source_type` non-null text
- nullable `source_ref`
- `scope` non-null text
- numeric confidence/importance with database constraints keeping them in `[0, 1]`
- UTC-safe timestamp storage for required/optional timestamp fields
- nullable `embedding_ref`
- JSON/object-compatible `metadata`

Do not add speculative columns for service/runtime status, deletion tombstones,
embeddings, documents, chunks, conversations, permissions, or agents unless an
authoritative source explicitly requires them.

## Database indexes

Add only small justified indexes useful for later scoped memory access.

At minimum consider deterministic indexes on:

- `type`
- `scope`
- `created_at`
- `expires_at`

A compact composite index may be used instead where demonstrably cleaner.

Do not build vector indexes or speculative full-text search.

Document the final exact index set in completion evidence.

## Alembic revision

Create one versioned migration after PHX-005 baseline revision `0001`.

Prefer a stable revision identifier such as:

`0002_memory_schema`

with:

`down_revision = "0001"`

The migration must create only the `memories` table and its justified indexes /
constraints.

Upgrade/downgrade behavior must be explicit and reversible:

- upgrade from 0001 -> new head creates memories
- downgrade new head -> 0001 removes memories and its indexes
- re-upgrade works

Do not edit/rewrite `0001_baseline.py`.

INV-020 requires versioned migrations.

## Base metadata / Alembic discovery

Ensure the memory ORM table is registered in shared `Base.metadata` when persistence
models are intentionally imported for schema/migration use.

Do not create database connections or files just to register metadata.

If Alembic needs an explicit model import to expose the table to `target_metadata`,
make that import narrow and side-effect free.

Do not make FastAPI startup automatically run migrations.

## No PUDDING service

PHX-031 owns the memory service.

Do not implement:

- create-memory service/repository methods
- update/delete APIs
- automatic memory extraction from chat
- long-term-memory admission policy
- contradiction resolution
- expiration cleanup jobs
- retrieval ranking
- user inspection UI
- memory.created event emission

The schema/table being writable through raw SQLAlchemy in tests is not a PUDDING
service.

## No document ingestion

PHX-032 owns document ingestion.

Do not create:

- `documents`
- `document_chunks`
- parsers/chunkers
- file upload handling
- content hashing pipeline

## No VectorStore

PHX-033 owns the VectorStore/sqlite adapter.

Do not add:

- sqlite-vec
- FAISS
- Chroma
- vector columns/indexes
- nearest-neighbor queries
- embeddings table

No new vector dependency.

## No retrieval/context assembly

PHX-034 owns retrieval/context assembly.

Do not implement:

- relevance scoring
- recency weighting
- importance ranking
- scope query policy
- reranking
- context budgeting
- model prompt assembly

## No provenance/citation renderer

PHX-035 owns provenance/citations.

PHX-030 records source evidence fields only.

Do not implement citation formatting or claim that `source_ref` is a verified citation.

## Event boundary

The canonical event vocabulary already contains `memory.created`.

Do not emit it in PHX-030 because this ticket does not create a memory service/runtime.

Do not modify the canonical 32-event vocabulary.

## API / frontend boundary

Do not add memory API endpoints, WebSockets, React/Tauri memory UI, or frontend database
access.

`GET /health` must remain exactly unchanged.

## Side-effect isolation

Importing or constructing the domain schema / ORM mapping must not:

- create directories
- create/open a database
- create tables
- run migrations
- open sockets
- contact providers
- run embeddings
- start subprocesses
- create threads
- create asyncio tasks
- emit events
- write files

Only explicit test-controlled database/migration operations may touch temporary
storage.

## Test requirements

Create focused deterministic tests, preferably `tests/test_memory_schema.py`.

At minimum prove:

1. exact six memory classes
2. no secret/document/chunk memory class
3. generated IDs are distinct
4. caller ID preserved/normalized
5. malformed IDs rejected
6. meaningful content required and formatting preserved
7. source_type strict/nonempty
8. source_ref optional and strict when present
9. scope strict/nonempty
10. confidence lower/upper boundaries accepted
11. confidence out-of-range/NaN/inf/bool rejected
12. importance lower/upper boundaries accepted
13. importance out-of-range/NaN/inf/bool rejected
14. aware timestamps normalize to UTC
15. naive timestamps rejected
16. updated-before-created rejected
17. invalid last-access timing rejected
18. invalid expiry timing rejected
19. embedding_ref optional/strict
20. no vector data field exists
21. metadata default empty
22. metadata caller containers detached
23. metadata nested JSON values accepted
24. arbitrary objects/bytes/nonfinite JSON values rejected
25. record is frozen
26. extra fields rejected
27. deterministic serialization/round-trip preserves semantics
28. shared Base contains exactly the intended memories domain table when model imported
29. database table name exactly `memories`
30. exact expected database columns
31. ID primary key
32. confidence/importance DB constraints
33. memory-type DB constraint or equivalent enforcement
34. expected indexes only
35. ORM insert/select round-trip against temporary SQLite
36. ORM timestamp round-trip remains UTC-aware
37. nullable fields round-trip correctly
38. JSON metadata round-trip remains detached/data-only
39. no uniqueness constraint silently collapses contradictory content
40. migration head is the new PHX-030 revision
41. down_revision remains 0001
42. upgrade from 0001 creates memories
43. exact migrated columns/constraints/indexes inspect correctly
44. downgrade to 0001 removes memories
45. re-upgrade succeeds
46. 0001 migration file remains unchanged
47. no documents table
48. no document_chunks table
49. no embedding_records/vector table
50. no memory service/repository runtime
51. no retrieval/reranking/context assembly
52. no citation rendering
53. no embedding-provider calls
54. no provider/model calls
55. no event emission
56. no import-time database/filesystem/network/subprocess/thread/task side effects
57. no credentials/secrets added
58. `/health` unchanged
59. canonical 32 events unchanged
60. Phase-0 persistence regressions green
61. Phase-1 regressions green
62. all Phase-2 regressions green
63. desktop regressions green

Add more strict schema/migration cases where needed.

## Dependencies

Expected dependency changes: **none**.

Use existing Python 3.11, Pydantic v2, SQLAlchemy 2, Alembic, and built-in SQLite.

Do not add a JSON/vector/database helper dependency.

## Verification

At minimum run:

- `uv run --locked pytest tests/test_memory_schema.py`
- existing persistence tests
- all Phase-1 model suites
- all Phase-2 suites
- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`
- a real Alembic upgrade/current/downgrade/re-upgrade lifecycle on a temporary SQLite DB
- `npm test`
- `npm run typecheck --workspace @phoenix/desktop`
- `npm run build --workspace @phoenix/desktop`
- `git diff --check`

## Security / invariant review

Review INV-001 through INV-024, especially:

- INV-001 — schema grants no orchestration authority
- INV-003/004 — memory data grants no permission or tool authority
- INV-011 — frontend does not gain direct DB access
- INV-017 — external/document/model-derived content remains untrusted data
- INV-018 — no secret persistence path in normal memory
- INV-020 — memory schema evolution uses a versioned migration
- INV-021 — migration/schema failures remain explicit
- INV-024 — no optional vector/research dependency becomes required

## ADR policy

At most one ADR is justified.

It may record together:

- exact six memory classes
- neutral memory record field semantics
- normalized confidence/importance range
- UTC-aware timestamp storage convention
- source/scope fields remain extensible strings
- `embedding_ref` stays opaque until PHX-033
- SQLAlchemy reserved-name handling for `metadata`
- one versioned `memories` table in SQLite
- service/retrieval/vector/citation behavior remains deferred

Do not create multiple ADRs.

## Completion gate

Only after all deterministic gates pass:

- mark this ticket DONE
- change only `PHX-030 Memory schema — TODO` to DONE in `FEATURE_TICKETS.md`
- PHX-031 through PHX-035 remain TODO
- commit exactly: `feat: complete PHX-030 memory schema`
- push only `feature/PHX-030-memory-schema`
- do not create a PR, merge, push main, or start PHX-031

## Completion evidence

Report at minimum:

- files created/modified
- dependencies changed or none
- public memory schema API
- exact memory classes
- field contracts
- confidence/importance semantics
- UTC timestamp behavior
- metadata representation and detachment
- embedding-ref boundary
- ORM model/table name and exact columns
- exact indexes/constraints
- Alembic revision/down_revision
- upgrade/downgrade/re-upgrade evidence
- Base metadata registration behavior
- no PUDDING service/RAG/vector/citation leakage
- targeted test result
- persistence regression result
- Phase-1 regression result
- Phase-2 regression result
- full Python result
- foundation result
- desktop result
- side-effect isolation
- security/secret review
- invariant review
- ADR or none
- acceptance gate
- staged diff review
- commit/push/main safety
- known limitations
- next ticket: PHX-031 — PUDDING Memory Service
