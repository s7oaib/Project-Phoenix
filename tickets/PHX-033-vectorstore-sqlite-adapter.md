# PHX-033 — VectorStore/sqlite Adapter

Status: TODO
Phase: 3 — Memory & RAG

## Objective

Implement PHOENIX's first provider-neutral `VectorStore` contract plus a built-in SQLite adapter for durable embedding records and bounded exact cosine search, without implementing PHX-034 retrieval/context policy, reranking, citations, parser routing, API/UI integration, or a mandatory sqlite-vec dependency.

PHX-033 is the storage/search primitive between the existing PHX-014 embedding-provider boundary and later PHX-034 retrieval/context assembly. It stores only validated vector/provenance evidence. It does not call an embedding provider itself and does not treat similarity as user-facing relevance or truth.

## Authority

Read and obey, in project authority order:

- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `/PRD.md`
- accepted `/DECISIONS.md`
- `/docs/05_MODEL_STRATEGY.md`
- `/docs/09_SECURITY.md`
- `/docs/10_MEMORY_SYSTEM.md`
- `/docs/11_RAG_SYSTEM.md`
- `/docs/12_DATABASE_SCHEMA.md`
- `/docs/25_DEPENDENCIES.md`
- `/tickets/PHX-005-sqlite-sqlalchemy-alembic-foundation.md`
- `/tickets/PHX-014-embeddinggemma-integration.md`
- `/tickets/PHX-030-memory-schema.md`
- `/tickets/PHX-031-pudding-memory-service.md`
- `/tickets/PHX-032-document-ingestion.md`
- this ticket

If authoritative sources materially conflict, stop instead of inventing a resolution.

## Dependencies

Required completed foundations:

- PHX-005 SQLite / SQLAlchemy / Alembic
- PHX-014 neutral `EmbeddingProvider`, `EmbeddingRequest`, `EmbeddingResponse`
- PHX-030 memory persistence conventions / UTC helper
- PHX-032 documents/chunks and provenance evidence

Expected dependency changes: **none**.

`MASTER_SPEC.md` identifies sqlite-vec as an initial candidate behind a `VectorStore` interface, not as a frozen mandatory runtime dependency. `docs/25_DEPENDENCIES.md` requires an accepted decision before promoting optional/reference dependencies to required runtime. PHX-033 therefore implements the first adapter using the existing SQLite/SQLAlchemy/Python stack only. Keep the abstraction compatible with a later sqlite-vec-backed implementation without making sqlite-vec, FAISS, Chroma, NumPy, SciPy, or another vector package required here.

## Scope

Prefer a compact split such as:

- `phoenix/rag/vector_store.py` — neutral contracts / abstract interface
- `phoenix/rag/sqlite_vector_store.py` — built-in SQLite implementation
- `phoenix/persistence/embeddings.py` — ORM mapping
- `alembic/versions/0004_embedding_records.py`
- `tests/test_vector_store_sqlite.py`

Minimal updates to Alembic registration and historical migration tests are allowed.

Do not modify PHX-014 embedding-provider behavior, PHX-032 ingestion behavior, PUDDING service behavior, or frontend/API code.

## Architectural boundary

PHX-033 owns:

- validated immutable embedding-record contracts
- a provider-neutral `VectorStore` interface
- a SQLite/SQLAlchemy adapter
- atomic batch insertion
- exact get/delete
- bounded low-level cosine similarity search within one explicit embedding space
- durable `embedding_records` schema and Alembic revision 0004
- deterministic result ordering and corruption detection

PHX-033 does **not** own:

- embedding generation/provider invocation
- automatic indexing of every document or memory
- query text -> embedding generation
- document/memory retrieval policy
- scope selection
- hybrid lexical/vector retrieval
- reranking
- context budgeting/assembly
- provenance rendering/citations
- prompt construction
- model generation
- background indexing workers

PHX-034 owns retrieval/context assembly over this primitive.

## Vector record

Create a frozen, extra-forbidden, exact-revalidated domain contract equivalent to `VectorRecord` with exactly these logical fields:

- `id`
- `source_kind`
- `source_id`
- `source_hash`
- `provider_id`
- `model_id`
- `dimensions`
- `vector`
- `created_at`

Rules:

`id`, `source_kind`, `source_id`, `provider_id`, `model_id`:

- strict strings
- outer whitespace trimmed
- nonempty
- case preserved
- no alias/provider-specific normalization beyond the existing neutral identifier convention

`source_kind` remains an extensible neutral string rather than a closed enum. Typical V1 values may be `document_chunk` or `memory`, but PHX-033 must not make those the only future possibilities.

`source_hash`:

- lowercase SHA-256 hex
- exactly 64 characters
- provenance/staleness evidence only
- grants no trust or authority

`dimensions`:

- strict positive integer
- bool rejected

`vector`:

- immutable nonempty tuple
- ordinary int/float inputs may normalize to floats
- bool rejected
- every component finite
- length must equal `dimensions`
- zero-norm vectors are rejected because cosine similarity is undefined

`created_at`:

- timezone-aware
- normalize to UTC
- naive timestamps rejected

No text content, secret value, prompt, arbitrary executable object, provider client, ORM row, or permission evidence belongs in `VectorRecord`.

## Record identity and source binding

PHX-033 does not dereference `source_id` or read memory/document content during normal VectorStore operations. `source_kind`, `source_id`, and `source_hash` are opaque provenance evidence supplied by the caller.

The store must not claim that a source exists, is trusted, or is current merely because a vector record exists. PHX-034 is responsible for resolving stored provenance and rejecting stale/orphaned sources before context assembly.

This preserves separation from PUDDING and document ingestion and avoids hidden filesystem/database cross-service behavior.

## No secrets / INV-018

PHX-033 stores vectors, not raw secret text.

Do not add secret-specific vector fields or log vector contents.

PHX-033 cannot infer whether a vector encodes secret-bearing content. Callers must only provide embeddings generated from content that passed the relevant trusted admission boundary. PHX-032's document admission and PHX-031/PHX-045 memory/secrets boundaries remain authoritative upstream controls.

Do not automatically embed any source in PHX-033.

## Batch write contract

Create an immutable, extra-forbidden contract equivalent to `VectorBatch` or accept a nonempty immutable tuple of exact `VectorRecord` values.

Batch insertion must:

- exact-revalidate every record
- reject empty batch
- reject duplicate record IDs within the batch
- reject duplicate `(source_kind, source_id, provider_id, model_id)` keys within the batch
- require all records sharing `(provider_id, model_id)` in the batch to have one identical dimension count
- detach caller-owned containers
- perform no provider/model call

## Embedding-space consistency

For a given exact `(provider_id, model_id)` already stored, all records must use one consistent `dimensions` value.

On add/add_many:

- if the database already contains that provider/model space at dimension `D`, a new record for the same provider/model with another dimension must fail explicitly
- if existing database rows for one provider/model are internally inconsistent due to corruption/manual writes, fail explicitly rather than choosing a dimension
- validate the whole batch against existing evidence before durable success

Do not compare vectors across different provider/model spaces.

## VectorStore interface

Define a provider-neutral abstract boundary equivalent to:

- `add(record) -> VectorRecord`
- `add_many(records) -> tuple[VectorRecord, ...]`
- `get(record_id) -> VectorRecord | None`
- `delete(record_id) -> bool`
- `search(query) -> tuple[VectorMatch, ...]`

An ABC or Protocol is acceptable if cleanly typed and testable.

The interface must not import Ollama, Colibri, HTTPX, EmbeddingGemma-specific code, FastAPI, React/Tauri, ToolRegistry, or PermissionEngine.

No hidden singleton/global store.

## Add semantics

`add` inserts exactly one record.

Conflicts:

- existing same `id` -> explicit conflict
- existing same `(source_kind, source_id, provider_id, model_id)` -> explicit conflict

Do not:

- upsert
- merge
- replace silently
- delete old embeddings automatically
- mutate source hashes
- call an embedding provider

The original durable record must remain unchanged on conflict.

## Atomic add_many

`add_many` persists the entire validated batch in one transaction.

If any record conflicts, violates model dimensions, fails a constraint, or otherwise cannot persist:

- roll back the whole batch
- leave no partial new records
- preserve all pre-existing records

Input order must be preserved in the returned detached tuple.

Do not retry silently.

## Get / delete

`get(record_id)`:

- strict normalized exact ID
- returns detached exact-revalidated `VectorRecord` or `None`
- never returns ORM state

`delete(record_id)`:

- exact ID only
- commit explicitly
- return `True` if deleted, `False` if absent
- affect no source record
- do not cascade into memories/documents/chunks

PHX-033 defines no update/replace operation. Re-embedding is an explicit caller lifecycle: delete the prior embedding record, then add new evidence. No silent replacement.

## Search query

Create a frozen, extra-forbidden `VectorSearchQuery` with:

- `provider_id`
- `model_id`
- `vector`
- `limit`
- optional `source_kind`
- optional ordered unique `source_ids`

Requirements:

- provider/model are mandatory exact embedding-space selectors
- vector follows the same finite nonzero contract as stored vectors
- `limit` strict positive integer, maximum 100
- source_kind is an exact optional filter
- source_ids are strict normalized IDs, immutable, unique, deterministic in caller order
- if source_ids are supplied, impose a reasonable deterministic maximum such as 2000 to avoid unbounded SQL parameters

No query text belongs here.

No RAG scope inference belongs here.

## Candidate bound

The SQLite adapter must have an explicit positive `max_search_candidates` configuration supplied at construction or a small documented safe default.

Search must first identify/count candidates after exact provider/model/source filters and exact dimension selection.

If candidate count exceeds the configured maximum:

- raise an explicit `VectorSearchLimitError`
- do not silently truncate the candidate set
- do not return an approximate result pretending it is complete

This keeps the built-in pure-Python exact cosine fallback bounded on BLAZE-6.

## Low-level cosine search

The built-in SQLite adapter may load the bounded candidate vectors and compute exact cosine similarity in Python standard library arithmetic.

Requirements:

- compare only exact `(provider_id, model_id)` candidates
- query dimension must match the stored embedding-space dimension when candidates exist
- zero-norm records/query are invalid
- no cross-model comparison
- score is finite
- clamp only tiny floating-point overshoot to the mathematical range `[-1, 1]`
- deterministic ordering: score descending, then embedding-record ID ascending
- return at most `limit`

This is a low-level vector primitive only.

Do not label the score as semantic truth, final relevance, verification, or user-facing confidence.

## Vector match

Create a frozen, extra-forbidden `VectorMatch` containing only the evidence required by PHX-034, preferably:

- `record_id`
- `source_kind`
- `source_id`
- `source_hash`
- `provider_id`
- `model_id`
- `score`

Do not include the full stored vector by default in every match.

Do not include source text or formatted citations.

## SQLite adapter

Implement `SqliteVectorStore` (or equivalent) using the borrowed `sessionmaker[Session]`.

Construction must:

- validate configuration
- perform no connection
- create no database file/directory
- run no migration
- start no worker/thread/task
- call no provider/model

The store never disposes the caller's engine/session factory.

Use short explicit session/transaction scopes.

Database errors must become small safe vector-store errors without raw SQL, vectors, source data, private database paths, or credentials.

## ORM mapping

Use the existing shared `Base`.

Create exactly one new ORM table/model:

`embedding_records`

Suggested ORM name:

`EmbeddingRecordRow`

Importing/registering the mapping performs no I/O.

## embedding_records table

Exact logical columns:

- `id`
- `source_kind`
- `source_id`
- `source_hash`
- `provider_id`
- `model_id`
- `dimensions`
- `vector`
- `created_at`

Use SQLite JSON for `vector` in PHX-033 so the built-in adapter requires no extension/dependency. Domain validation is authoritative for finite numeric values and exact dimension agreement; stored-row reconstruction must revalidate and fail explicitly on corruption.

At minimum enforce named constraints:

- primary key on `id`
- unique `(source_kind, source_id, provider_id, model_id)`
- `dimensions >= 1`
- vector must be a JSON array

Suggested exact indexes:

- `ix_embedding_records_source` on `(source_kind, source_id)`
- `ix_embedding_records_model` on `(provider_id, model_id)`
- `ix_embedding_records_created_at` on `created_at`

Do not add vector/full-text virtual tables or sqlite extensions in PHX-033.

## Alembic

Create one migration:

`alembic/versions/0004_embedding_records.py`

with:

`revision = "0004_embedding_records"`

`down_revision = "0003_document_ingestion"`

Do not modify historical migrations:

- `0001_baseline.py`
- `0002_memory_schema.py`
- `0003_document_ingestion.py`

Upgrade creates only `embedding_records` plus its PHX-033 constraints/indexes.

Downgrade removes only `embedding_records` and returns to 0003 with memories/documents/chunks preserved.

Update Alembic model registration narrowly.

## Historical migration-test evolution

Historical ticket tests must continue proving their own revisions rather than becoming coupled to latest head.

After PHX-033:

- 0001 = baseline only
- 0002 = memories
- 0003 = memories + documents + document_chunks
- 0004/head = previous tables + embedding_records

Where PHX-030 or PHX-032 tests intended to inspect their own schema, target their explicit revision.

Only current-head assertions advance to 0004.

Do not weaken existing schema/constraint/UTC/atomicity tests.

## No sqlite-vec dependency yet

Do not add/load a sqlite-vec extension in PHX-033.

Reasons:

- the master spec calls it an initial candidate, not a mandatory dependency
- the dependency policy forbids silently promoting optional/reference technology
- the `VectorStore` abstraction must permit a future sqlite-vec implementation without changing PHX-034 callers
- deterministic standard-SQLite behavior gives a portable baseline and test oracle

A later accepted decision may add sqlite-vec as an optimized adapter. PHX-033 must not design the interface around sqlite-vec-specific SQL or metadata.

## No embedding provider invocation

PHX-033 may import neutral embedding contracts only if needed for type compatibility, but the store itself must never call:

- `EmbeddingProvider.embed`
- `OllamaEmbeddingProvider`
- `ModelProvider`
- model lifecycle/routing

Callers own embedding generation and must pass validated vector records explicitly.

No hidden batching/provider retry belongs here.

## No automatic indexing

Do not watch documents/memories or subscribe to `document.indexed`/`memory.created`.

Do not create a background indexer.

Do not automatically embed all existing records.

No EventBus dependency is required for PHX-033.

## No PHX-034 retrieval policy

PHX-033 search does **not**:

- resolve source records
- validate source freshness against current document/memory content
- choose project/document scope
- combine memory/document candidates
- rerank
- apply recency/importance weighting
- build model context
- enforce context token budget
- format citations
- call a query embedding provider

Those belong to PHX-034/035.

## No PUDDING mutation

Do not update `memories.embedding_ref` automatically.

Do not call `PuddingMemoryService`.

Do not delete memories when vector records are deleted.

## No document mutation

Do not modify `documents` or `document_chunks` rows from the VectorStore.

Do not add vector/embedding columns to PHX-032 tables.

## No event changes

PHX-033 adds no canonical event type and emits no events.

The frozen event count remains 32.

## No API / frontend

Do not add FastAPI vector endpoints, Tauri commands, React state, or direct frontend database access.

`/health` remains unchanged.

## Side-effect isolation

Fresh-process/equivalent tests must prove importing and constructing the vector contracts, `VectorStore`, `SqliteVectorStore`, and ORM mapping does not:

- connect to SQLite
- create files/directories
- run migrations
- load sqlite extensions
- open network sockets
- invoke embedding/model providers
- start threads/tasks/workers
- publish/subscribe events
- execute tools
- grant permissions

Only explicit migration and store method calls may perform documented DB work.

## Required tests

Create a focused suite such as:

`tests/test_vector_store_sqlite.py`

At minimum cover:

### Contracts

- frozen/extra-forbidden/exact revalidation
- strict IDs
- source hash shape
- aware UTC timestamps
- finite vectors
- bool rejection
- nonempty vectors
- dimension consistency
- zero-norm rejection
- detached caller containers

### Add / add_many

- add one round-trip
- atomic multi-record batch
- return order preserved
- duplicate ID conflict
- duplicate source/model key conflict
- duplicate batch evidence rejected before DB mutation
- model-space dimension mismatch rejected
- race/constraint failure leaves no partial batch
- original rows preserved on failure

### Get / delete

- present/absent get
- detached domain objects
- corruption fails explicitly
- delete true/false
- other rows unaffected

### Search

- mandatory provider/model space
- exact dimensions
- same-model only
- deterministic cosine scores
- deterministic tie order by record ID
- limit
- exact source_kind filter
- exact source_ids filter
- no cross-model comparison
- no source resolution
- candidate-bound explicit failure
- no silent truncation
- malformed/corrupt stored vector explicit failure

### Persistence / migration

- exact table/columns
- named PK/unique/check/indexes
- UTC round-trip
- 0001 -> 0002 -> 0003 -> 0004
- 0004 -> 0003 preserves memories/documents/chunks
- re-upgrade restores embedding_records
- historical migrations byte-for-byte unchanged
- mapping/schema comparison no drift

### Separation

- no sqlite-vec/FAISS/Chroma/NumPy dependency
- no embedding provider call
- no EventBus
- no PUDDING mutation
- no document mutation
- no retrieval/context/citation code
- no API/frontend changes

## Regression gates

Run PHX-014 embeddings tests.

Run PHX-030, PHX-031, and PHX-032 focused regressions.

Run persistence regression.

Run all Phase-1 model-runtime suites.

Run all Phase-2 orchestration suites.

Run full Python suite, Ruff, format check, strict mypy, lock/sync, package build, foundation, and desktop tests.

Expected dependency changes: none.

## Acceptance criteria

PHX-033 is DONE only if all are true:

1. Provider-neutral `VectorStore` boundary exists.
2. Built-in SQLite adapter requires no new vector dependency.
3. `VectorRecord` is immutable, finite, dimension-consistent and nonzero.
4. Embedding space is explicitly keyed by provider + model.
5. Cross-model similarity is impossible through the search contract.
6. Add conflicts never silently overwrite.
7. Batch insert is atomic.
8. `embedding_records` exists under Alembic 0004 only.
9. Historical migrations remain unchanged.
10. Search is exact within the bounded candidate set.
11. Candidate overflow fails explicitly instead of silently approximating.
12. Search results have deterministic ordering.
13. Stored-row corruption is not coerced into success.
14. No provider/model invocation occurs.
15. No source resolution/mutation occurs.
16. No PUDDING/document row mutation occurs.
17. No events/API/frontend integration is added.
18. No PHX-034 retrieval/context policy is implemented.
19. No PHX-035 citation behavior is implemented.
20. All targeted/regression/quality/security gates pass.

## Expected completion evidence

When complete, append concise evidence for:

- files changed
- public VectorStore API
- vector/domain contracts
- embedding-space/dimension rules
- conflict/atomicity behavior
- exact SQLite table/constraints/indexes
- migration lifecycle
- exact cosine behavior
- candidate bound
- source/model filters
- corruption handling
- no sqlite-vec dependency
- no provider invocation
- no retrieval/context/citation leakage
- PHX-014/030/031/032 regressions
- full Python/foundation/desktop results
- dependency/security/invariant review
- known limitations

## Known intended limitation

The first built-in adapter performs bounded exact cosine in Python over validated SQLite rows. It is a correctness/portability baseline, not a large-scale ANN engine. sqlite-vec remains an optimization candidate behind the same `VectorStore` boundary. PHX-034 owns retrieval policy, source resolution, freshness checks, query embedding integration, reranking and context construction.
