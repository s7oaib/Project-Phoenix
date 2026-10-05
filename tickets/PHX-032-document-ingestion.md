# PHX-032 — Document Ingestion

Status: TODO
Phase: 3 — Memory & RAG

## Objective

Implement PHOENIX's first durable, provenance-preserving document-ingestion boundary for VEGAPUNK without implementing vector storage, semantic retrieval, context assembly, citations, upload UI, or parser routing.

PHX-032 accepts an explicit in-memory document source plus an explicitly supplied parser collaborator, validates untrusted parser output, deterministically chunks/deduplicates it, persists one `documents` row plus its `document_chunks` atomically, and emits the already-canonical `document.indexed` event only after durable commit.

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
- `/docs/09_SECURITY.md`
- `/docs/11_RAG_SYSTEM.md`
- `/docs/12_DATABASE_SCHEMA.md`
- `/docs/14_EVENT_SYSTEM.md`
- `/docs/19_DOCUMENT_SYSTEM.md`
- `/docs/25_DEPENDENCIES.md`
- `/tickets/PHX-005-sqlite-sqlalchemy-alembic-foundation.md`
- `/tickets/PHX-006-event-bus-foundation.md`
- `/tickets/PHX-030-memory-schema.md`
- `/tickets/PHX-031-pudding-memory-service.md`
- this ticket

If authoritative sources materially conflict, stop instead of inventing a resolution.

## Dependencies

Required completed foundations:

- PHX-005 SQLite / SQLAlchemy / Alembic foundation
- PHX-006 EventBus
- PHX-020 AgentRegistry
- PHX-030 durable schema conventions / UTC persistence helper
- PHX-031 post-commit event/backpressure precedent

Expected dependency changes: **none**.

Do not add PDF/DOCX/OCR/vector/parser libraries in PHX-032. Concrete upload/parser routing and parser integration remain PHX-072. PHX-032 establishes a parser-neutral ingestion boundary that can consume those future adapters.

## Ownership

VEGAPUNK internal ID `knowledge_rag` owns indexing/RAG behavior.

The display alias `VEGAPUNK` is never an authority key.

PHX-032 must validate `knowledge_rag` through AgentRegistry, remain alias-independent, and create no agent/model process.

ROBIN remains document analysis/generation owner; PHX-032 does not invoke ROBIN.

## Scope

Prefer a compact split such as:

- `phoenix/rag/__init__.py`
- `phoenix/rag/ingestion.py`
- `phoenix/persistence/documents.py`
- `alembic/versions/0003_document_ingestion.py`
- `tests/test_document_ingestion.py`

Minimal updates to Alembic registration and historical migration tests are allowed.

Do not create VectorStore, retrieval, citation, upload, filesystem-tool, or frontend/API modules.

## Source boundary

Create an immutable, extra-forbidden `DocumentSource` or equivalent with at least:

- `document_id`
- `source_type`
- `source_ref`
- `media_type`
- `content_bytes`
- `source_timestamp`
- `metadata`

`document_id`:

- strict nonempty normalized string
- UUID4 default preferred
- caller IDs preserved

`source_type`, `source_ref`, `media_type`:

- strict nonempty normalized strings
- extensible, not closed enums

`source_ref` is opaque provenance data. PHX-032 must not open or resolve it as a filesystem path/URL.

`content_bytes`:

- strict bytes
- nonempty
- supplied explicitly by the caller
- never persisted to SQLite
- never logged

`source_timestamp`:

- optional aware datetime
- normalize to UTC
- naive values rejected

`metadata`:

- top-level JSON object
- string keys
- finite JSON-compatible values only
- deep-detached/read-only semantics consistent with PHX-030
- no executable authority

External/uploaded document bytes and metadata are untrusted data under INV-017.

## Explicit limits

Create an immutable `DocumentIngestionLimits` (or equivalent) supplied explicitly to the ingestion service or call.

It must include finite positive strict integers for:

- `max_source_bytes`
- `max_parsed_chars`
- `max_chunks`
- `chunk_size_chars`

and a strict integer:

- `chunk_overlap_chars >= 0`
- `chunk_overlap_chars < chunk_size_chars`

Reject booleans/coercion.

Do not silently invent project-wide defaults in authoritative docs. A constructor default is acceptable only if the ticket completion evidence records it as an implementation-local safe default and tests prove callers can override it explicitly; explicit caller-owned limits are preferred.

Enforce source-byte size before parser invocation and parsed/chunk bounds before persistence.

## Parser collaborator

Define a minimal parser-neutral async Protocol equivalent to:

```python
class DocumentParser(Protocol):
    async def parse(self, source: DocumentSource) -> ParsedDocument:
        ...
```

PHX-032 production code must not instantiate a parser automatically.

No parser registry/detection/routing belongs here.

No concrete PDF/DOCX/image/OCR dependency is required.

Tests use deterministic fake parsers.

Only an explicit ingestion call may await the supplied parser.

## Parsed segment

Create immutable, extra-forbidden `ParsedSegment` with:

- meaningful `text`
- optional `path`
- optional `page`
- optional `section`
- JSON-safe detached `metadata`

Rules:

- text is strict meaningful untrusted data
- optional path/section are strict meaningful strings when present
- page is a strict positive integer when present; bool rejected
- no commands/instructions are executed
- no source reference is dereferenced

## Parsed document

Create immutable, extra-forbidden `ParsedDocument` with:

- `document_id`
- `parser_id`
- `parser_version`
- nonempty ordered tuple of `ParsedSegment`

Requirements:

- exact document-ID correlation with the source
- parser ID/version are strict nonempty neutral strings
- preserve segment order
- no arbitrary runtime/provider/tool objects
- no embeddings or vector data

Treat parser output as untrusted runtime data.

Exact-revalidate stored field values and reject subclasses/extra-field smuggling.

## Deterministic chunking

PHX-032 owns deterministic character-based chunking over parsed segments so it remains tokenizer/model-provider neutral.

Use source order.

Each segment is chunked into windows bounded by `chunk_size_chars` with explicit `chunk_overlap_chars`.

Requirements:

- no empty/whitespace-only persisted chunk
- preserve original chunk text as stored evidence
- global chunk indices are contiguous `0..N-1` after deduplication
- path/page/section metadata from the originating segment is preserved on each resulting chunk
- total parsed characters cannot exceed `max_parsed_chars`
- final chunk count cannot exceed `max_chunks`

Do not use model tokenizers in PHX-032.

## Near-identical deduplication

Implement a small deterministic V1 approximation to the RAG rule "Deduplicate near-identical chunks".

Deduplicate only **within the same ingested document**.

A permitted canonical comparison form is:

- Unicode NFKC normalization
- whitespace collapse
- case-folding

Keep the first occurrence in source order.

Preserve the original kept chunk content; do not store the canonicalized comparison string as content.

Do not deduplicate across different documents because that would collapse independent provenance.

Do not use embeddings/semantic similarity for deduplication.

## Hashes

Use SHA-256 from the Python standard library.

Document source hash:

- SHA-256 of exact `content_bytes`

Chunk content hash:

- SHA-256 of exact persisted UTF-8 chunk text

Hashes are integrity/provenance evidence, not trust or permission evidence.

## Admission/security policy

Introduce a mandatory neutral `DocumentAdmissionPolicy` protocol.

It evaluates the detached validated document/chunk proposal before any database session is acquired.

Use an exact closed verdict vocabulary:

- `accepted`
- `rejected_secret`
- `rejected_policy`

A frozen `DocumentAdmissionDecision` must correlate to the exact `document_id`.

Only `accepted` may persist.

For rejected/malformed/policy-failure cases:

- no DB mutation
- no event
- no provider/model/tool work

There must be NO built-in allow-all default.

This is the deterministic enforcement point for future security integration. Do not claim PHX-032 can independently detect every semantic secret. Correct secret rejection depends on the injected trusted policy until PHX-045.

Never log source bytes, parsed text, chunk text, metadata, or secret-bearing evidence.

## Document domain record

Create a frozen, extra-forbidden detached `DocumentRecord` with exactly the logical persisted document evidence:

- `id`
- `source_type`
- `source_ref`
- `media_type`
- `source_timestamp`
- `source_hash`
- `parser_id`
- `parser_version`
- `chunk_count`
- `indexed_at`
- `metadata`

Rules:

- strict stable ID
- SHA-256 hashes encoded as lowercase 64-character hex strings
- chunk_count strict positive integer
- indexed_at aware UTC
- source_timestamp optional aware UTC
- metadata JSON-safe/deep-detached

Do not store raw source bytes.

## Document chunk domain record

Create frozen, extra-forbidden `DocumentChunkRecord` with:

- `id`
- `document_id`
- `chunk_index`
- `content`
- `content_hash`
- `path`
- `page`
- `section`
- `source_timestamp`
- `parser_version`
- `metadata`

Rules:

- stable strict IDs
- chunk_index strict integer >= 0; bool rejected
- content strict meaningful untrusted text
- path/page/section optional provenance locators
- content_hash lowercase SHA-256 hex
- source_timestamp optional aware UTC
- parser_version strict nonempty string
- metadata JSON-safe/deep-detached

No embedding fields/vector values are required in PHX-032.

PHX-033 owns embedding/vector records.

## Chunk IDs

Chunk IDs must be deterministic for the validated ingested document/chunk order.

A UUID5 derived from stable document ID + final chunk index + exact content hash is acceptable.

Do not rely on process-random ordering.

## Persistence mapping

Use PHX-005 shared `Base`.

Create exactly two new ORM mappings/tables:

- `documents`
- `document_chunks`

Do not create another DeclarativeBase.

Reuse the PHX-030 UTC persistence convention; do not introduce contradictory timezone semantics.

ORM attributes named `metadata_json` may map to database column `metadata` to avoid SQLAlchemy's reserved declarative `.metadata` name.

Import/construct mapping must perform no I/O.

## `documents` table

Exact logical columns:

- `id`
- `source_type`
- `source_ref`
- `media_type`
- `source_timestamp`
- `source_hash`
- `parser_id`
- `parser_version`
- `chunk_count`
- `indexed_at`
- `metadata`

Constraints at minimum:

- primary key `id`
- `chunk_count >= 1`
- metadata must be a JSON object
- SHA-256 hash shape enforced at domain level; DB shape check is optional if clean

Do NOT make `source_ref` unique.

The same source may be intentionally indexed as a later independent record/version under a different document ID.

Suggested exact indexes:

- `ix_documents_source_type`
- `ix_documents_source_ref`
- `ix_documents_indexed_at`

No vector/full-text index.

## `document_chunks` table

Exact logical columns:

- `id`
- `document_id`
- `chunk_index`
- `content`
- `content_hash`
- `path`
- `page`
- `section`
- `source_timestamp`
- `parser_version`
- `metadata`

Constraints at minimum:

- primary key `id`
- foreign key `document_id -> documents.id`
- unique `(document_id, chunk_index)`
- `chunk_index >= 0`
- `page IS NULL OR page >= 1`
- metadata must be a JSON object

Suggested exact indexes:

- `ix_document_chunks_document_id`
- `ix_document_chunks_content_hash`

Do not add embedding/vector columns.

## Alembic

Create one revision:

`0003_document_ingestion`

with:

`down_revision = "0002_memory_schema"`

Do not modify:

- `0001_baseline.py`
- `0002_memory_schema.py`

Upgrade creates only:

- `documents`
- `document_chunks`
- their exact PHX-032 indexes/constraints

Downgrade drops chunks before documents.

Update Alembic model registration narrowly and without import-time DB I/O.

INV-020 applies.

## Historical migration-test evolution

PHX-030 tests intentionally froze `0002_memory_schema` as the then-current head and proved that revision contained only `memories` beyond the baseline.

PHX-032 is the legitimate next schema ticket.

Minimally evolve historical tests so they continue proving:

- 0001 remains baseline-only
- 0002_memory_schema still creates only `memories`
- PHX-030 files/migration semantics remain unchanged
- current project head becomes `0003_document_ingestion`

Do not weaken PHX-030 coverage merely because head advanced.

Where a PHX-030 lifecycle test intended to test the memory revision specifically, target `0002_memory_schema` explicitly rather than `head`.

## Ingestion service

Implement one obvious `DocumentIngestionService` or equivalent.

Constructor dependencies should be explicit and borrowed:

- `sessionmaker[Session]`
- `EventBus`
- mandatory `DocumentAdmissionPolicy`
- explicit `DocumentIngestionLimits`
- optional `AgentRegistry`

Construction must:

- validate authoritative `knowledge_rag`
- perform no DB connection
- publish no event
- start no worker/thread/task

The service never disposes the borrowed engine/session factory.

## Ingest operation

Preferred public entry point:

```python
await service.ingest(source=..., parser=...) -> DocumentIngestionResult
```

Required sequence:

1. exact-revalidate source
2. validate byte-size limit
3. register active document ingestion
4. invoke supplied parser exactly once
5. detect local cancellation before any persistence
6. exact-revalidate/correlate parsed document
7. validate parsed-character bound
8. deterministic chunking
9. deterministic within-document deduplication
10. validate final chunk-count bound/nonempty result
11. derive hashes/records
12. call admission policy exactly once on detached validated evidence
13. exact-revalidate/correlate admission decision
14. require `accepted`
15. open one short DB transaction
16. reject duplicate durable document ID
17. insert one document row + all chunk rows atomically
18. commit
19. close session
20. attempt exactly one canonical `document.indexed` publication
21. return detached immutable result
22. cleanup active state in `finally`

No partial document persistence.

## Atomicity

Document row and all chunk rows must commit in one transaction.

If any insert/constraint fails:

- rollback all document/chunk writes
- emit no `document.indexed`
- return/raise explicit safe failure

No hidden retry.

No upsert.

Duplicate document ID must preserve the original durable record and emit no event.

## Result

Create frozen, extra-forbidden `DocumentIngestionResult` containing at least:

- detached `DocumentRecord`
- immutable tuple of final chunk IDs
- event-delivery evidence

Do not return ORM rows.

A compact event delivery vocabulary equivalent to:

- `published`
- `rejected_full`

is acceptable and should mirror PHX-031 semantics.

## `document.indexed` event

After durable commit, attempt exactly one canonical:

`EventType.DOCUMENT_INDEXED`

Requirements:

- `agent_id = "knowledge_rag"`
- `correlation_id = document.id`
- `task_id = None`
- INFO/default severity
- privacy-minimal payload exactly equivalent to:

```json
{
  "document_id": "...",
  "chunk_count": 12
}
```

Do not include:

- raw source bytes
- chunk content
- path/source_ref
- metadata
- parser output
- secrets

Database commit must complete before publication.

EventBus full must leave the committed document/chunks durable and return explicit `rejected_full` evidence; no retry loop.

Cancellation during post-commit publication propagates; durable rows remain authoritative.

Other publication failures must remain explicit and must not fake rollback.

## Active state / cancellation

Because parser execution may be long, INV-019 applies.

Track active document IDs in memory per service instance.

Expose immutable deterministic active-ID inspection.

Reject simultaneous duplicate active document IDs without disturbing the original operation.

Provide request-scoped cooperative cancellation with:

- `accepted`
- `not_found`

Use the established caller-task ownership pattern where appropriate.

Do not create hidden worker tasks only for cancellation.

If the parser suppresses cancellation, locally recorded cancellation must prevent later admission/persistence/finalization.

Distinct document ingestions may coexist.

Cleanup active state after every terminal path.

## No automatic file access

PHX-032 MUST NOT open `source_ref`.

No direct filesystem reads.

No browser/network reads.

No path traversal resolution.

No upload endpoint.

Explicit bytes are supplied by the caller.

PHX-072 owns upload/parser routing integration and later authorized file-input plumbing.

## No concrete binary parser dependency

Do not add or instantiate pypdf, python-docx, OCR, Pillow, LibreOffice, Pandoc, or other parser stacks in PHX-032.

Future parser adapters may implement `DocumentParser` behind this boundary.

This keeps PHX-032 dependency-neutral and prevents optional parser choices from becoming architecture requirements.

## No embedding / VectorStore

PHX-033 owns:

- embedding records
- vector storage adapter
- sqlite vector integration
- vector similarity
- embedding generation wiring

PHX-032 must not call PHX-014 embeddings.

Do not add embedding columns/tables/indexes.

## No retrieval / context assembly

PHX-034 owns:

- semantic/scoped retrieval
- recency/importance/relevance ranking
- reranking
- context budgeting
- prompt/context assembly
- retrieval-time expiration/policy

PHX-032 must not expose semantic search APIs.

## No citations

PHX-035 owns provenance/citation formatting and source presentation.

PHX-032 preserves provenance fields only.

Do not render citations or claim provenance fields are verified simply because they are stored.

## No PUDDING coupling

Do not call `PuddingMemoryService`.

Documents/chunks are knowledge/RAG records, not normal conversational memory.

Do not automatically create MemoryRecord rows from chunks.

## No tool / permission / model authority

Do not invoke:

- ToolRegistry
- PermissionEngine
- ModelProvider
- EmbeddingProvider
- AIZEN/router/orchestrator/LEVI/recovery/fail-safe runtimes

Parsed text, metadata, hashes, parser results, and admission decisions grant no execution permission.

## Event vocabulary

The canonical 32-event vocabulary already contains `document.indexed`.

Do not add or rename event types.

## API/frontend boundary

Do not add:

- FastAPI document/upload endpoint
- WebSocket upload flow
- Tauri command
- React file workspace
- direct frontend DB access

`GET /health` remains unchanged.

## Side-effect isolation

Importing/constructing document contracts, persistence mappings, limits, parser protocol, or service must not:

- create directories/files
- open SQLite
- run migrations
- open sockets
- read source_ref
- call providers/models/embeddings
- launch subprocesses
- start threads
- create hidden asyncio tasks
- emit events
- execute tools

Only explicit temp-DB migration/tests and explicit `ingest()` may perform their documented work.

## Minimum targeted test coverage

Create `tests/test_document_ingestion.py` and prove at minimum:

1. authoritative service ID is `knowledge_rag`
2. default registry works
3. missing internal ID fails
4. VEGAPUNK alias alone cannot establish authority
5. alias rename/removal does not change behavior
6. source default IDs are distinct
7. supplied source ID preserved/normalized
8. malformed source IDs rejected
9. strict nonempty source_type/source_ref/media_type
10. source bytes strict/nonempty
11. source bytes never persisted
12. source timestamp aware/UTC-normalized
13. naive source timestamp rejected
14. source metadata JSON-safe/deep-detached
15. explicit limits strict and bounded
16. overlap must be smaller than chunk size
17. oversize source rejected before parser call
18. parser called exactly once
19. exact DocumentSource reaches parser
20. parser ID/version meaningful
21. parser document-ID correlation required
22. parsed segments ordered/nonempty
23. malformed/extended parser outputs rejected
24. parsed text remains untrusted data
25. parsed character bound enforced
26. segment path/page/section validation
27. deterministic chunk windows
28. deterministic overlap behavior
29. no empty persisted chunks
30. global chunk indices contiguous after dedup
31. provenance locators preserved across split chunks
32. max chunk count enforced
33. within-document canonical near-duplicate dedup keeps first
34. dedup does not modify kept content
35. no cross-document dedup policy
36. document source SHA-256 exact
37. chunk SHA-256 exact
38. deterministic chunk IDs
39. admission policy mandatory
40. no allow-all default
41. exact accepted/rejected_secret/rejected_policy vocabulary
42. policy called exactly once on eligible ingestion
43. admission ID correlation enforced
44. rejected_secret opens no DB session/emits no event
45. rejected_policy opens no DB session/emits no event
46. malformed/policy exception opens no DB session/emits no event
47. document record frozen/extra-forbidden
48. chunk record frozen/extra-forbidden
49. exact document ORM/table columns
50. exact chunk ORM/table columns
51. SQLAlchemy metadata collision avoided
52. shared Base only
53. document row + all chunks commit atomically
54. chunk failure rolls back document
55. duplicate document ID preserves original
56. duplicate document ID emits no event
57. same source_ref may exist under different document IDs
58. same content in different documents preserves separate provenance
59. post-commit event only
60. exact document.indexed type
61. event agent ID exactly knowledge_rag
62. correlation ID exact document ID
63. payload only document_id/chunk_count
64. event contains no source/chunk/private metadata
65. EventBus full leaves rows durable
66. EventBus full returns explicit delivery evidence
67. no event retry loop
68. cancellation before persistence leaves no rows
69. active ID visible while parser suspended
70. duplicate active ID rejected
71. cancel active -> accepted
72. cancel unknown -> not_found
73. cancel A does not cancel B
74. suppressed cancellation cannot persist/finalize
75. active cleanup on success/failure/cancellation
76. no source_ref filesystem access
77. no network/subprocess/thread hidden work
78. no PUDDING service call
79. no embedding provider call
80. no VectorStore
81. no retrieval/reranking/context assembly
82. no citation rendering
83. no ToolRegistry/PermissionEngine
84. no provider/model runtime
85. migration revision exactly 0003_document_ingestion
86. down_revision exactly 0002_memory_schema
87. 0001 unchanged
88. 0002 unchanged
89. upgrade 0002 -> 0003 creates only documents/document_chunks
90. exact migrated constraints/indexes
91. downgrade 0003 -> 0002 removes only document tables
92. re-upgrade succeeds
93. PHX-030 historical memory revision semantics remain green
94. PHX-031 memory-service tests remain green
95. Phase-1 suites green
96. Phase-2 suites green
97. foundation suites green
98. `/health` unchanged
99. canonical 32 events unchanged
100. desktop regressions green

Add additional deterministic validation/transaction/cancellation cases where necessary.

## Historical regression requirements

Run at minimum:

- `tests/test_document_ingestion.py`
- `tests/test_memory_schema.py`
- `tests/test_pudding_memory_service.py`
- `tests/test_persistence.py`
- all Phase-1 model-runtime suites
- all Phase-2 suites
- foundation suites
- full Python suite
- Ruff check/format
- strict mypy
- `uv lock`
- `uv sync --locked`
- `uv build`
- real Alembic upgrade/downgrade/re-upgrade on temp SQLite
- desktop test/typecheck/build
- `git diff --check`

## Alembic lifecycle

On a temporary DB, explicitly prove:

`0001 -> 0002_memory_schema -> 0003_document_ingestion -> 0002_memory_schema -> 0003_document_ingestion`

At 0001:

- `alembic_version` only

At 0002:

- `alembic_version`
- `memories`

At 0003/head:

- `alembic_version`
- `memories`
- `documents`
- `document_chunks`

Inspect exact columns, primary/foreign/unique/check constraints, and indexes.

No user data directory.

## Security/invariants

Review INV-001 through INV-024, especially:

- INV-001 — ingestion grants no orchestration authority
- INV-003/004 — parser/admission output grants no permission/tool authority
- INV-011 — no frontend direct DB/filesystem access
- INV-017 — uploaded/document content and metadata remain untrusted
- INV-018 — trusted admission rejection prevents secret-bearing chunks from persistence; no secret-specific storage path
- INV-019 — parser operation exposes active state/cancellation
- INV-020 — document tables use a versioned migration
- INV-021 — parser/DB/event failures remain explicit
- INV-022 — post-commit EventBus backpressure is observable
- INV-024 — parser/vector dependencies do not silently become required

## ADR policy

At most one ADR is justified.

It may record together:

- `knowledge_rag` is authoritative VEGAPUNK identity
- explicit bytes + parser collaborator avoid direct filesystem authority
- parser routing/concrete binary parsers remain PHX-072
- deterministic bounded character chunking
- within-document canonical near-duplicate dedup only
- SHA-256 source/chunk integrity evidence
- mandatory document admission policy/no allow-all default
- atomic documents + chunks persistence
- `document.indexed` only after commit with privacy-minimal payload
- EventBus-full durable-row semantics
- PHX-033/034/035 boundaries
- Alembic 0003 follows 0002

## Completion gate

Only after all required gates pass:

- mark this ticket DONE
- update `FEATURE_TICKETS.md` so only `PHX-032 Document ingestion` changes TODO -> DONE
- leave PHX-033 through PHX-035 TODO
- commit exactly:

`feat: complete PHX-032 document ingestion`

- push only `feature/PHX-032-document-ingestion`

Do not create a PR, merge, push main, force-push, or start PHX-033.
