# PHX-034 — Retrieval/context assembly

Status: TODO
Phase: 3 — Memory & RAG

## Objective

Implement PHOENIX's first provider-neutral retrieval and context-assembly service over the completed PHX-030/031 memory foundation, PHX-032 document/chunk ingestion, PHX-033 VectorStore, and PHX-014 embedding-provider boundary.

PHX-034 owns query embedding integration, explicit source scoping, low-level vector candidate retrieval, current-source resolution, stale/orphan rejection, deterministic memory reranking using relevance/recency/importance, authoritative-project-document precedence supplied explicitly by the caller, and bounded structured context assembly.

PHX-034 does **not** own citation rendering, user-facing provenance formatting, model generation, prompt construction, tool execution, automatic indexing, memory/document mutation, or provider/model routing. PHX-035 owns citations/provenance presentation.

## Authority

Read and obey, in project authority order:

- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `/PRD.md`
- accepted `/DECISIONS.md`
- `/docs/03_AGENT_ROSTER.md`
- `/docs/05_MODEL_STRATEGY.md`
- `/docs/09_SECURITY.md`
- `/docs/10_MEMORY_SYSTEM.md`
- `/docs/11_RAG_SYSTEM.md`
- `/docs/12_DATABASE_SCHEMA.md`
- `/docs/25_DEPENDENCIES.md`
- `/tickets/PHX-014-embeddinggemma-integration.md`
- `/tickets/PHX-030-memory-schema.md`
- `/tickets/PHX-031-pudding-memory-service.md`
- `/tickets/PHX-032-document-ingestion.md`
- `/tickets/PHX-033-vectorstore-sqlite-adapter.md`
- this ticket

If authoritative sources materially conflict, stop instead of inventing a resolution.

## Dependencies

Required completed foundations:

- PHX-014 neutral `EmbeddingProvider`, `EmbeddingRequest`, `EmbeddingResponse`
- PHX-030 immutable memory schema and SQLite persistence conventions
- PHX-031 durable PUDDING memory service semantics
- PHX-032 durable documents/chunks and provenance evidence
- PHX-033 provider-neutral `VectorStore` and bounded SQLite adapter

Expected dependency changes: **none**.

PHX-034 must use the existing Python/SQLAlchemy/provider/vector abstractions only. Do not add tokenizers, reranking packages, NumPy/SciPy, vector dependencies, or model libraries.

## Ownership and boundaries

VEGAPUNK's authoritative internal ID is `knowledge_rag` and owns RAG retrieval/context coordination.

PUDDING's authoritative internal ID is `memory` and remains owner of memory admission/persistence semantics. PHX-034 may perform bounded read-only source resolution against existing memory rows, but it must not create, replace, delete, or mutate memories and must not bypass PHX-031 admission rules.

Aliases remain display metadata only.

PHOENIX remains supreme. A retrieval result is context evidence, not a final answer or success verdict.

## Recommended implementation layout

Prefer a compact implementation such as:

- `phoenix/rag/retrieval.py` — public contracts and retrieval/context service
- `tests/test_retrieval_context.py` — focused deterministic tests

A small `phoenix/rag/__init__.py` export update is acceptable.

Do not add migrations or new persistence tables.

## Retrieval source kinds

PHX-034 supports exactly these V1 retrieval source kinds:

- `memory`
- `document_chunk`

This does **not** close PHX-033's extensible `VectorRecord.source_kind` contract. It only defines which stored vector evidence PHX-034 understands in V1.

Unknown vector source kinds must not be interpreted as memory or document evidence.

## Retrieval request

Create a frozen, extra-forbidden contract equivalent to `RetrievalRequest` containing at least:

- `retrieval_id`
- `query_text`
- `provider_id`
- `model_id`
- `as_of`
- `memory_scopes`
- optional `memory_types`
- `document_ids`
- `authoritative_document_ids`
- `candidate_limit_per_kind`
- `min_similarity`
- `ranking_policy`
- `context_budget`

### Retrieval ID

`retrieval_id`:

- strict nonempty string
- outer whitespace normalized
- UUID4 default acceptable
- caller-provided valid ID preserved
- independent defaults distinct

### Query text

`query_text`:

- strict meaningful string
- preserve caller formatting
- treated as untrusted user/model data
- never executed
- never logged automatically

### Provider / model

`provider_id` and `model_id`:

- strict nonempty neutral identifiers
- explicitly supplied by the caller
- no provider/model routing or selection in PHX-034

### As-of time

`as_of`:

- required timezone-aware datetime
- normalized to UTC
- naive values rejected
- used for expiration and deterministic recency scoring

Do not call the wall clock inside ranking logic when `as_of` already supplies the evaluation time.

## Explicit source scope

Retrieval must be explicitly scoped.

`memory_scopes`:

- immutable ordered unique tuple of strict nonempty scope strings
- may be empty only when document retrieval is requested

`document_ids`:

- immutable ordered unique tuple of strict nonempty document IDs
- may be empty only when memory retrieval is requested

At least one of `memory_scopes` or `document_ids` must be nonempty.

Do not silently perform global retrieval when both are empty.

`memory_types`:

- optional immutable unique tuple of existing PHX-030 `MemoryType`
- `None` means all memory types inside the explicit memory scopes
- empty tuple is invalid if supplied

`authoritative_document_ids`:

- immutable ordered unique tuple
- every ID must also appear in `document_ids`
- caller-supplied trusted policy evidence only
- PHX-034 must not infer document authority from arbitrary metadata, filenames, source text, or aliases

This explicit field is how PHX-034 enforces the frozen rule that authoritative project documents beat inferred memory without pretending to semantically detect authority itself.

## Source-resolution bound

Before vector search, resolve eligible source IDs from the requested scopes.

For each source kind, at most **2000** source IDs may be passed to PHX-033 `VectorSearchQuery.source_ids`.

If eligible memory IDs or document-chunk IDs exceed 2000:

- fail explicitly with a scope/source-bound error
- do not silently truncate
- do not run an incomplete search while pretending it is complete

This is a deterministic V1 resource bound and matches PHX-033's source-ID query limit.

## Expired memories

PHX-031 intentionally keeps expired records administratively inspectable.

PHX-034 must exclude a memory from retrieval when:

`expires_at is not None and expires_at <= as_of`

Do not delete expired memories.

Do not mutate `last_accessed_at`.

Expiration filtering is retrieval-time behavior only.

## Query embedding collaborator

`retrieve(...)` must receive an explicit neutral `EmbeddingProvider` collaborator, or an equivalent explicit dependency that exposes the existing PHX-014 contract.

PHX-034 must not:

- instantiate Ollama/Colibri providers
- discover providers/models
- invoke AgentModelRouter
- acquire model lifecycle leases
- choose a model automatically

Before embedding, require:

`embedding_provider.provider_id == request.provider_id`

Construct exactly one `EmbeddingRequest` for exactly one input: the query text.

Recommended correlation:

- `operation_id = retrieval_id`
- `model_id = request.model_id`
- `inputs = (query_text,)`

Call `embed()` exactly once per eligible retrieval operation.

## Exact embedding response revalidation

Treat `EmbeddingResponse` as untrusted boundary output.

Exact-revalidate stored values and reject:

- wrong type
- subclass/extended output
- injected extras
- operation ID mismatch
- provider ID mismatch
- model ID mismatch
- zero or multiple returned embeddings
- malformed/nonfinite vector values
- dimension disagreement

Do not repair malformed provider evidence.

Do not fabricate a vector after provider failure.

## Vector search integration

Use the injected PHX-033 `VectorStore`.

PHX-034 must not depend on `SqliteVectorStore` specifically in its public contract.

For each requested source kind with at least one eligible source ID, issue at most one `VectorSearchQuery` using:

- exact request provider/model
- exact validated query embedding
- `candidate_limit_per_kind`
- source kind = `memory` or `document_chunk`
- explicit eligible source IDs

`candidate_limit_per_kind`:

- strict positive int
- maximum 100

Do not perform cross-model comparison.

Do not call search for a source kind with zero eligible source IDs.

Treat returned `VectorMatch` evidence as untrusted and exact-revalidate each match.

Reject collaborator output that:

- uses another provider/model
- uses another source kind
- references a source ID outside the requested eligible set
- duplicates vector record IDs
- contains malformed score/hash/identity evidence

## Similarity threshold

`min_similarity`:

- ordinary finite number
- inclusive range `[-1, 1]`
- bool rejected

Matches below this threshold are excluded before final source ranking/context assembly.

Do not interpret the threshold or similarity score as truth, verification, or confidence.

## Current source resolution

PHX-034 must resolve the **current** source record before allowing a vector match into assembled context.

### Memory source

For `source_kind == "memory"`:

- `source_id` is the exact memory ID
- load the current `MemoryRow` read-only
- reconstruct/revalidate the existing PHX-030 `MemoryRecord`
- require it still belongs to the requested scope/type set
- require it is not expired at `as_of`
- compute current source hash as lowercase SHA-256 of exact UTF-8 `MemoryRecord.content`
- require current hash == `VectorMatch.source_hash`

If the row no longer exists, it is orphaned vector evidence.

If the current hash differs, it is stale vector evidence.

Neither stale nor orphaned memory evidence may enter context.

### Document chunk source

For `source_kind == "document_chunk"`:

- `source_id` is the exact `DocumentChunkRow.id`
- load current chunk and parent document read-only
- require parent document ID is in requested `document_ids`
- reconstruct/revalidate the PHX-032 chunk/document evidence needed by this boundary
- require current `DocumentChunkRow.content_hash == VectorMatch.source_hash`

If chunk or parent document no longer exists, treat it as orphaned.

If content hash differs, treat it as stale.

Neither stale nor orphaned chunk evidence may enter context.

Do not mutate source rows.

## Stale/orphan observability

Stale/orphan matches should be safely excluded rather than silently treated as valid context.

The final result must expose aggregate immutable diagnostics at minimum for:

- vector matches received
- below-similarity exclusions
- stale-source exclusions
- orphaned-source exclusions
- eligible ranked items
- budget exclusions
- final context item count

Do not expose raw SQL, private paths, secret content, or full vectors in diagnostics.

PHX-035 may later present richer provenance/citation evidence.

## Memory ranking policy

Create a frozen, extra-forbidden deterministic policy equivalent to `MemoryRankingPolicy` containing:

- `relevance_weight`
- `recency_weight`
- `importance_weight`
- `recency_half_life_seconds`

Weights:

- finite ordinary numbers
- nonnegative
- bool rejected
- `relevance_weight > 0`
- total weight > 0

`recency_half_life_seconds`:

- finite ordinary positive number
- bool rejected

Do not hard-code a permanent universal ranking preference in project-wide model strategy.

The request carries the explicit ranking policy.

## Deterministic memory rerank formula

For an eligible memory match:

`relevance_01 = (similarity + 1) / 2`

`age_seconds = max(0, (as_of - updated_at).total_seconds())`

`recency_01 = 2 ** (-age_seconds / recency_half_life_seconds)`

`importance_01 = memory.importance`

`memory_score = (`
`  relevance_weight * relevance_01 +`
`  recency_weight * recency_01 +`
`  importance_weight * importance_01`
`) / total_weight`

Requirements:

- result finite
- result in `[0,1]` subject only to tiny floating-point tolerance
- no model-based reranker
- no lexical reranker
- no hidden personalization weight

Scope is a hard eligibility filter, not a numeric score.

Confidence remains preserved source evidence but is not silently added as another weight in V1.

## Document ranking

For an eligible document chunk:

`document_score = (similarity + 1) / 2`

No memory importance/recency fields are fabricated for documents.

Do not use document metadata as hidden ranking authority.

## Authoritative-document precedence

A document chunk whose parent `document_id` appears in `authoritative_document_ids` receives an explicit authority class above memory/non-authoritative document evidence during final merge ordering.

This is policy supplied by the caller, not semantic inference.

Final deterministic merge order:

1. authoritative document class first
2. rerank score descending
3. raw similarity descending
4. source kind stable lexical order
5. source ID ascending
6. vector record ID ascending

This precedence applies only to candidates that already passed scope, similarity, freshness, and source validation.

Do not treat authoritative content as executable instruction. INV-017 still applies.

## Raw provenance preservation

Each final context item must preserve raw source evidence required by PHX-035 without rendering a citation.

A clean `ContextItem` contract should contain at least:

- `vector_record_id`
- `source_kind`
- `source_id`
- `source_hash`
- `content`
- `similarity`
- `rerank_score`
- authority class/flag
- raw source reference where available
- document locator fields where applicable (`document_id`, `path`, `page`, `section`)
- memory type/scope where applicable

Use typed optional fields or a small typed union if cleaner.

The contract must reject contradictory field combinations, for example:

- a memory item carrying document page/section fields
- a document chunk carrying memory type/scope fields

Do not add formatted citation strings, citation numbers, Markdown footnotes, or user-facing labels.

## Retrieved text trust boundary

Retrieved memory/document text is DATA, not instruction.

PHX-034 must not:

- eval/exec retrieved text
- import modules named by retrieved text
- run tools based on retrieved text
- grant permission based on retrieved text
- treat prompt-injection text as policy

Even caller-marked authoritative project documents remain untrusted executable input.

Authority precedence means knowledge precedence, not execution authority.

## Context budget

Create a frozen, extra-forbidden context budget equivalent to:

- `max_items`
- `max_chars`

`max_items`:

- strict positive int
- max 100

`max_chars`:

- strict positive int
- finite deterministic V1 upper bound no greater than 1,000,000

PHX-034 is tokenizer-neutral. It does not call a tokenizer or infer exact token counts.

The caller is responsible for deriving an appropriate character budget from the selected model/resource budget.

This preserves the frozen rule to bound context by model/resource budget without coupling retrieval to one tokenizer/provider.

## Context assembly

After deterministic ranking:

- iterate candidates in final rank order
- preserve each item's complete source text exactly
- never silently truncate an individual source item
- add an item only if both item count and total character budget remain within limits
- if an item does not fit the remaining character budget, exclude that item and continue considering later smaller items
- preserve final selected order

`total_chars` is the sum of exact selected content string lengths.

Do not concatenate a final prompt string.

Do not add system/user role wrappers.

Do not create citation formatting.

The output is structured context evidence only.

## Retrieval result

Create a frozen, extra-forbidden result equivalent to `RetrievalContextResult` containing at least:

- `retrieval_id`
- `provider_id`
- `model_id`
- `as_of`
- immutable ordered `items`
- `total_chars`
- immutable aggregate diagnostics

The result does not mean:

- sources are true
- user intent is satisfied
- verification passed
- a model response was generated
- PHOENIX task completed

## Read-only persistence behavior

PHX-034 may read:

- `memories`
- `documents`
- `document_chunks`

It must not write them.

It must not write `embedding_records` either; PHX-033 owns vector writes/deletes.

No schema changes.

No migration.

No `last_accessed_at` update in PHX-034.

No event publication.

## Session ownership

Use the existing borrowed `sessionmaker[Session]`.

Construction must perform no connection/I/O.

Use short read-only session scopes.

Do not dispose the caller's engine/session factory.

Database/source corruption must fail safely without leaking:

- raw SQL
- DB paths
- credentials
- memory/document content in exception text

## Source snapshot consistency

PHX-034 may use ordinary SQLite read snapshots and explicit revalidation, but it does not provide a cross-service distributed transaction spanning the query embedding provider, VectorStore's own session, and source tables.

The final current-source hash check is authoritative for the source snapshot actually resolved.

Document this V1 limitation truthfully rather than claiming impossible global snapshot atomicity.

## Active retrieval tracking

Because query embedding may be long-running, track active retrieval IDs in memory.

One service instance / process / event loop.

Requirements:

- active before awaiting embedding provider
- distinct IDs may coexist
- duplicate active ID rejected explicitly
- immutable deterministic sorted active-ID inspection
- IDs reusable after cleanup
- no Task objects publicly exposed
- no hidden worker task

## Cooperative cancellation

Provide request-scoped cancellation equivalent to:

- active -> `accepted`
- unknown -> `not_found`

Use the proven caller-task ownership pattern where appropriate.

Cancel A must not cancel B.

Do not kill providers/processes globally.

Do not close shared DB engines.

Do not create hidden tasks solely for cancellation.

If the embedding provider catches/suppresses `CancelledError`, independently recorded local cancellation must prevent vector search, source resolution, or successful result finalization afterward.

`CancelledError` must not become a successful empty context.

Cleanup active state in `finally` on every path.

## No query caching

Do not add retrieval/result caching in PHX-034.

No persistent query history.

No background prefetch.

## No source mutation

Do not call:

- `PuddingMemoryService.create/replace/delete`
- document ingestion mutation
- `VectorStore.add/add_many/delete`

Only low-level `VectorStore.search` is used.

## No automatic indexing

Do not subscribe to events.

Do not automatically embed stale/orphaned sources.

Do not repair missing embeddings.

A stale match is excluded and reported; re-index lifecycle belongs to later explicit orchestration.

## No model generation

PHX-034 must not call generation providers or PHOENIX/AIZEN/router agents.

Only the explicit embedding collaborator may perform inference.

Do not send assembled context to a generation model.

## No citations

PHX-035 owns provenance/citation presentation.

PHX-034 may preserve raw provenance fields only.

Do not add:

- citation strings
- footnotes
- source numbering
- Markdown links
- citation renderer
- final answer attribution

## No API/frontend

Do not add:

- FastAPI retrieval endpoints
- WebSocket retrieval commands
- Tauri commands
- React context/retrieval UI
- Zustand retrieval state
- frontend database/vector access

`/health` remains unchanged.

## No event changes

PHX-034 emits no event and adds no EventType.

Canonical event count remains 32.

## Side-effect isolation

Fresh-process/equivalent tests must prove importing and constructing retrieval contracts/service does not:

- connect to SQLite
- create files/directories
- run migrations
- call embeddings
- call VectorStore search
- open network sockets
- invoke generation models
- start threads/tasks/workers
- publish/subscribe events
- execute tools
- grant permissions

Only explicit `retrieve()` may await the supplied embedding provider and perform documented read/search operations.

## Required focused tests

Create:

`tests/test_retrieval_context.py`

At minimum cover the following.

### Contracts

- frozen / extra-forbidden / exact revalidation
- strict retrieval/provider/model/source IDs
- unique memory scopes/document IDs/authority IDs
- at least one source scope required
- authoritative IDs must be requested document IDs
- aware UTC `as_of`
- candidate limit 1..100
- similarity threshold finite and in range
- ranking weights finite/nonnegative with positive relevance weight
- positive recency half-life
- context item/budget/result invariants

### Embedding integration

- provider ID must match request
- exactly one provider call
- exactly one query input
- operation/retrieval correlation
- model/provider response correlation
- malformed/extended response rejected
- zero/multiple embeddings rejected
- provider failure explicit
- no provider routing/lifecycle behavior

### Scope resolution

- exact memory scope filtering
- optional memory type filtering
- expired memory excluded
- no last_access mutation
- exact document ID -> chunk resolution
- 2000-ID bound explicit
- empty selected source kind skips vector search
- no global retrieval when both scope families empty

### Vector collaborator

- at most one search per requested source kind
- correct provider/model/vector/source_kind/source_ids
- candidate limit preserved
- malformed VectorMatch rejected
- match provider/model/source-kind mismatch rejected
- match source ID outside eligible set rejected
- duplicate vector match IDs rejected
- vector-store failure explicit

### Freshness/source resolution

- current memory hash accepted
- stale memory hash excluded
- orphan memory excluded
- current document chunk hash accepted
- stale chunk excluded
- orphan chunk/parent excluded
- source rows remain unchanged
- vector rows remain unchanged

### Memory ranking

- relevance normalization exact
- recency at age zero == 1
- recency half-life behavior
- importance contribution
- explicit weight changes alter deterministic order
- scope is hard filter not hidden score
- confidence is not silently included
- rank finite/in range

### Merge/authority

- caller-declared authoritative doc outranks eligible memory
- unmarked document does not get hidden authority
- authority metadata is not inferred from document metadata/text/path
- deterministic score/similarity/source/ID tie breaks
- authoritative content still treated as untrusted data

### Context assembly

- max_items enforced
- max_chars enforced
- no individual content truncation
- oversized item skipped and later smaller item considered
- selected content formatting preserved
- total_chars exact
- immutable item tuple
- raw provenance preserved
- no citation strings/rendering
- no final prompt construction

### Diagnostics

- match count
- below-similarity count
- stale count
- orphan count
- eligible ranked count
- budget exclusion count
- final item count
- no sensitive/raw-vector diagnostic leakage

### Cancellation

- active ID visible while embedding is suspended
- deterministic sorted active IDs
- duplicate active ID rejected
- cancel active -> accepted
- cancel unknown -> not_found
- cancel A leaves B active
- caller cancellation propagates
- suppressed provider cancellation cannot proceed to vector search or final result
- cleanup on success/failure/cancel
- ID reusable after cleanup

### Separation

- no memory/document/vector writes
- no events
- no generation model
- no ToolRegistry/PermissionEngine
- no citation renderer
- no API/frontend
- no schema/migration changes

## Regression gates

Run focused regressions for:

- PHX-014 embeddings
- PHX-030 memory schema
- PHX-031 PUDDING memory service
- PHX-032 document ingestion
- PHX-033 VectorStore/sqlite adapter
- persistence

Run all Phase-1 model-runtime suites.

Run all Phase-2 orchestration suites.

Run full Python, Ruff, format, strict mypy, lock/sync, package build, foundation and desktop suites.

Expected dependency changes: none.

Alembic head remains `0004_embedding_records`.

Historical migrations remain unchanged.

## Acceptance criteria

PHX-034 is DONE only if all are true:

1. Retrieval/context contracts are immutable and exact-revalidated.
2. Retrieval is explicitly scoped; no accidental global search.
3. Query embedding uses exactly one explicit neutral provider/model and exact response correlation.
4. PHX-033 is consumed through the provider-neutral `VectorStore` interface.
5. Only `memory` and `document_chunk` source kinds are interpreted in V1.
6. Scope/source-ID bounds fail explicitly rather than silently truncating.
7. Expired memories are excluded without deletion or mutation.
8. Current source evidence is resolved read-only.
9. Stale and orphaned vectors cannot enter context.
10. Memory reranking deterministically considers relevance, recency and importance.
11. Caller-declared authoritative documents outrank eligible memory without inferred authority.
12. Retrieved text remains untrusted executable input.
13. Context is bounded by explicit item/character budgets.
14. Individual source content is never silently truncated.
15. Structured raw provenance is preserved without PHX-035 citation rendering.
16. Active-state/cancellation behavior is deterministic and request-scoped.
17. No memory/document/vector mutation occurs.
18. No model generation, provider routing, events, schema, API or frontend integration is added.
19. No PHX-035 citation behavior is implemented.
20. All targeted/regression/quality/security/invariant gates pass.

## Expected completion evidence

When complete, append concise evidence for:

- files changed
- public API
- knowledge_rag identity/alias independence if service validates registry
- request/scope/budget/ranking contracts
- embedding provider correlation
- VectorStore search calls
- source-ID bounds
- expiration handling
- current-source freshness checks
- stale/orphan handling
- memory rerank formula
- authority precedence
- context budget/assembly
- raw provenance / no citations
- cancellation/active tracking
- no writes/events/generation/API/frontend
- targeted test count
- PHX-014/030/031/032/033/persistence regressions
- Phase-1 / Phase-2 results
- full Python/foundation/desktop results
- dependency/schema/security/invariant status
- known limitations

## Known intended limitations

V1 retrieval uses the caller-selected embedding provider/model, PHX-033's bounded vector primitive, explicit source scopes and deterministic formula-based reranking. It does not provide lexical/hybrid search, learned rerankers, distributed snapshots, exact tokenizer accounting, automatic re-indexing, source truth verification, or citation rendering. Those capabilities require later explicit tickets/decisions.
