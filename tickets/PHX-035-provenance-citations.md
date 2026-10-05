# PHX-035 — Provenance/citations

Status: DONE
Phase: 3 — Memory & RAG
Branch: `feature/PHX-035-provenance-citations`

## Objective

Implement PHOENIX's first deterministic provenance/citation presentation boundary over the immutable PHX-034 `RetrievalContextResult` without re-running retrieval, dereferencing sources, generating an answer, mutating storage, or claiming source truth.

PHX-035 closes Phase 3 by turning each selected PHX-034 context item into one structured, deterministic citation entry and one safe plain-text citation presentation record while preserving exact retrieval provenance.

Authoritative RAG owner internal ID remains:

`knowledge_rag`

Display alias `VEGAPUNK` is never authority.

## Architectural position

Input:

`RetrievalContextResult`

Output:

structured citation/provenance evidence only.

PHX-035 does not perform retrieval, freshness checking, embedding, generation, claim extraction, claim-to-source entailment, answer rewriting, hyperlink resolution, source opening, or permission evaluation.

The immutable PHX-034 retrieval result is the source snapshot for this ticket. PHX-035 must not re-read databases to establish a newer/different provenance view.

## Required production module

Prefer:

`phoenix/rag/citations.py`

Focused tests:

`tests/test_provenance_citations.py`

A minimal export update to `phoenix/rag/__init__.py` is optional.

## Required public concepts

Prefer the following exact conceptual surface, with minor naming changes allowed only when equally explicit:

- `CitationEntry`
- `CitationBundle`
- `CitationRenderRecord`
- `CitationRenderer`
- `ProvenanceCitationService`
- typed citation/provenance errors

The service entry point should be synchronous and pure, for example:

`build(result: RetrievalContextResult) -> CitationBundle`

A renderer may expose:

`render(bundle: CitationBundle) -> tuple[CitationRenderRecord, ...]`

No async API is required.

## Authoritative identity

If the service accepts an `AgentRegistry`, construction must revalidate the registry and require exact internal ID:

`knowledge_rag`

Do not use alias `VEGAPUNK` or professional-role text as authority.

Alias removal or rename must not change behavior.

A custom registry containing alias `VEGAPUNK` but lacking internal ID `knowledge_rag` must fail.

No agent/model process is launched.

## Exact input boundary

`ProvenanceCitationService.build()` accepts only an exact PHX-034 `RetrievalContextResult`.

Treat that object and every nested `ContextItem` as untrusted boundary evidence.

Revalidate stored fields rather than trusting an existing instance.

Reject:

- wrong type
- subclasses
- copied/injected extras
- malformed nested items
- contradictory result totals
- invalid ranking order
- duplicate context vector identities
- malformed source hashes
- malformed provenance shapes

Do not silently repair malformed retrieval evidence.

## Retrieval correlation

Every citation bundle must preserve:

- `retrieval_id`
- `provider_id`
- `model_id`
- `as_of`

exactly from the validated retrieval result.

PHX-035 must not create a second retrieval ID or pretend citation construction is a new retrieval.

## One-to-one citation coverage

Every selected `ContextItem` in the retrieval result receives exactly one citation entry.

Citation order is exactly the retrieval result item order.

No selected context item may be silently omitted.

No citation may be fabricated for an item that was not in the retrieval result.

Empty retrieval result items produce an empty citation tuple and remain valid.

## Citation numbering

Use deterministic contiguous 1-based citation ordinals:

1, 2, 3, ... N

in exact retrieval-context order.

A visible marker must be derived only from the ordinal, for example:

`[1]`
`[2]`

Do not accept caller-supplied marker text.

Do not number from database IDs, filenames, aliases, or source metadata.

## Citation identity

Each citation entry must contain a deterministic citation identifier.

Recommended V1 form:

`citation_id = f"citation-{ordinal}"`

within one bundle.

The citation ID is presentation identity only. It grants no source authority and is not persisted.

Citation IDs must be unique and contiguous with entry order.

## CitationEntry contract

Create a frozen, extra-forbidden exact contract containing at least:

- `citation_id`
- `ordinal`
- `marker`
- `retrieval_id`
- `vector_record_id`
- `source_kind`
- `source_id`
- `source_hash`
- `authoritative`
- `source_ref`
- document locator fields when document evidence
- memory provenance fields when memory evidence

Recommended document locator fields:

- `document_id`
- `path`
- `page`
- `section`
- `source_timestamp`
- `parser_version`
- `chunk_index`

Recommended memory provenance fields:

- `memory_type`
- `memory_scope`
- `memory_confidence`

Do not include:

- raw vector
- query embedding
- full retrieved content
- SQL
- DB path
- prompt text
- generated answer text
- tool/permission state

## Source-kind vocabulary

PHX-035 understands exactly the PHX-034 V1 citation kinds:

- `memory`
- `document_chunk`

Do not invent a citation source kind for web research yet.

URAHARA web provenance belongs to later web-research integration and may reuse the architecture later.

## Exact provenance copying

Citation entries must copy provenance only from the exact validated `ContextItem`.

Do not independently infer or enrich provenance from:

- filenames
- metadata strings
- source contents
- aliases
- path patterns
- URL patterns

Do not transform source IDs/hashes into different identities.

## Memory citation provenance

For a memory citation preserve:

- vector record ID
- source kind `memory`
- memory source ID
- source hash
- source_ref if present
- memory type
- memory scope
- memory confidence

Memory citation must have:

- `authoritative == False`
- no document-specific locator evidence

Do not claim the memory fact is verified merely because it has a citation.

## Document citation provenance

For a document citation preserve:

- vector record ID
- source kind `document_chunk`
- chunk source ID
- source hash
- source_ref
- document ID
- path if present
- page if present
- section if present
- source timestamp if present
- parser version
- chunk index
- authoritative flag

Do not fabricate page/section/path when absent.

## Authoritative flag semantics

`authoritative=True` means only that PHX-034 caller policy ranked that requested document as authoritative project evidence.

It does NOT mean:

- cryptographically verified
- universally true
- permission-granting
- safe instruction
- signed source
- externally authenticated

Citation presentation must not rename this to `verified` or `trusted`.

## No source re-resolution

PHX-035 must not query:

- `memories`
- `documents`
- `document_chunks`
- `embedding_records`

Do not open a SQLAlchemy session.

Do not call PHX-031, PHX-032, or PHX-033 services.

PHX-034 already established its final source snapshot and freshness evidence.

A later source change does not mutate an already-built citation bundle.

## No source dereference

Do not open/fetch/resolve:

- `source_ref`
- `path`
- URLs embedded in source_ref
- filesystem paths
- document files
- network resources

Source references are opaque evidence.

## Provenance is evidence, not truth

Citation construction must never claim that a source is:

- true
- verified
- current beyond PHX-034's retrieval snapshot
- safe
- authorized
- instruction-bearing

A citation means only:

"This selected retrieval context item carried this provenance evidence."

## Retrieved content separation

Citation entries should not duplicate full retrieved content.

The PHX-034 result remains the context-content object.

PHX-035 citation records bind markers to provenance.

This separation reduces accidental content leakage and keeps provenance independently inspectable.

## Plain-text renderer

Implement a deterministic plain-text citation renderer.

Renderer output is presentation metadata only, never executable markup.

`CitationRenderRecord` should be frozen and extra-forbidden and contain at least:

- `citation_id`
- `ordinal`
- `marker`
- `label`

The renderer may also expose a safe `locator` string separately if cleaner.

## Renderer safety

Untrusted provenance strings may contain newlines, tabs, brackets, Markdown-like text, shell-looking commands, URLs, or prompt-injection text.

The renderer must treat them only as display data.

For any provenance string inserted into a plain-text label:

- collapse/control line-breaking whitespace into a single space
- preserve ordinary Unicode text
- do not execute or parse Markdown/HTML
- do not auto-link URLs
- do not interpret bracket-looking text as citation markers

Only the service-generated `marker` is a citation marker.

A recommended helper is deterministic whitespace collapsing using `" ".join(value.split())`.

Do not use HTML sanitization libraries or add dependencies.

## Canonical memory label

Use one deterministic V1 memory label shape.

Recommended:

`Memory · type=<memory_type> · scope=<memory_scope> · source=<source_ref-or-source_id>`

where untrusted inserted values are display-sanitized.

If `source_ref` is absent, use `source_id`.

Do not include memory content.

Do not include confidence in the human label unless the authoritative ticket implementation documents a compelling reason; confidence remains structured provenance.

## Canonical document label

Use one deterministic V1 document label shape.

Recommended base:

`Document · source=<source_ref> · document=<document_id>`

Append only locators actually present, in this exact V1 order:

1. `page=<page>`
2. `section=<section>`
3. `path=<path>`
4. `chunk=<chunk_index>`

`parser_version` and source timestamp remain structured provenance and need not clutter the display label.

Do not fabricate missing locator text.

## No Markdown/HTML links

PHX-035 must not emit:

- `[label](url)`
- `<a href=...>`
- `file://` links
- clickable filesystem paths
- auto-open actions

Future UI may decide how to display structured provenance safely.

## No inline answer citation insertion

Do not implement:

`insert_citations(answer_text)`

or equivalent.

PHX-035 has no claim-to-source alignment evidence.

It must not guess which sentence should receive which marker.

Downstream answer generation may be instructed to reference citation markers later, but that integration is outside this ticket.

## No claim entailment

Do not implement:

- claim extraction
- NLI/entailment
- factuality checking
- citation correctness scoring
- citation completeness scoring against generated prose

LEVI remains verification owner; PHX-035 does not invoke LEVI.

## CitationBundle contract

Create a frozen, extra-forbidden bundle containing exactly the needed immutable correlation evidence, preferably:

- `retrieval_id`
- `provider_id`
- `model_id`
- `as_of`
- `citations`

Optionally include one small exact coverage summary if useful.

The citations tuple must preserve exact ordinal order.

Bundle validation must enforce:

- unique citation IDs
- contiguous ordinals starting at 1
- marker exactly agrees with ordinal
- every entry retrieval_id matches bundle retrieval_id
- unique vector record IDs
- no contradictory memory/document provenance

## Coverage evidence

If a coverage contract is implemented, keep it purely structural.

Recommended fields:

- `context_item_count`
- `citation_count`

Require equality.

Do not call this:

- factual coverage
- truth coverage
- answer coverage

because no generated answer exists in this ticket.

## Renderer/bundle correlation

Renderer output must preserve:

- citation ID
- ordinal
- marker

exactly.

One render record per citation.

Renderer order exactly equals citation order.

No extra rendered reference.

No missing rendered reference.

## Determinism

For one exact `RetrievalContextResult`, repeated build/render calls must produce exactly equal citation structures and labels.

No randomness.

No wall-clock calls.

No UUID generation inside citation build.

No DB ordering.

## Empty result semantics

A valid retrieval result containing zero context items must build:

- zero citations
- zero render records

without fabricating a "no source" citation.

## Exact revalidation of CitationEntry / Bundle

Public citation contracts must use the strict patterns established elsewhere:

- frozen
- extra-forbidden
- revalidate instances
- reject subclasses
- reject injected extras
- reject contradictory copied fields

Forged marker/citation ID/ordinal combinations must fail.

## Marker contract

Marker must be exactly:

`f"[{ordinal}]"`

No caller-controlled marker string.

Reject:

- `[01]`
- `[ 1 ]`
- `[1].`
- `1`
- arbitrary source-provided marker values

## Citation ID contract

Citation ID must be exactly:

`f"citation-{ordinal}"`

for V1.

Reject mismatches.

No global persistence/uniqueness is claimed beyond one bundle.

## Display label length/resource safety

Do not create an unbounded algorithmic expansion.

Renderer output length should be linear in input provenance length.

Do not recursively serialize arbitrary metadata.

Do not include metadata dictionaries in labels.

No JSON pretty-print of arbitrary source metadata.

## Metadata separation

PHX-035 does not expose full PHX-030 memory metadata or PHX-032 document metadata because PHX-034 `ContextItem` intentionally carries only selected provenance.

Do not re-open source rows to enrich citation metadata.

## Source hash handling

Preserve exact validated source hash in structured citation evidence.

Do not show the 64-character hash in the default human label.

Do not claim SHA-256 provenance means source authenticity.

## Authority presentation

Preserve `authoritative` structurally.

Default plain-text label should not prepend words such as:

- VERIFIED
- TRUSTED
- TRUE

If the renderer indicates authority, the wording must be literal and narrow, e.g. `authoritative-project-source=true`.

Simpler V1 behavior is to keep authority only in structured citation evidence.

## No persistence

Do not add tables.

Do not write citation records to SQLite.

Do not create files/caches.

Do not add Alembic revision 0005.

Alembic head remains:

`0004_embedding_records`

## No EventBus

Do not publish or subscribe to events.

Do not change canonical 32 EventType values.

## No provider/model calls

Do not call:

- EmbeddingProvider
- ModelProvider
- Ollama
- Colibri
- AgentModelRouter
- ModelLifecycleManager

PHX-035 is deterministic transformation only.

## No retrieval call

Do not invoke `RetrievalContextService.retrieve()`.

The already-completed retrieval result is explicit input.

Do not re-search VectorStore.

## No generation

Do not generate/synthesize/rewrite an answer.

Do not concatenate context into a model prompt.

Do not call PHOENIX/AIZEN/LEVI/MAHORAGA/KAIDO.

## No tool/permission authority

Do not call ToolRegistry or PermissionEngine.

Citation evidence grants no permission.

A malicious source label cannot trigger a tool.

## No API/frontend integration

Do not add:

- FastAPI citation endpoint
- WebSocket message
- Tauri command
- React citation component
- Zustand citation state

Those integrations belong later.

## No schema mutation

Do not modify ORM models or migrations.

Do not modify `alembic/env.py`.

Do not update source rows with citation IDs.

## Side-effect isolation

Importing or constructing citation contracts/service/renderer must perform no:

- DB connection
- filesystem access
- source-ref dereference
- network access
- provider call
- model call
- vector search
- event publish/subscribe
- process/thread/task creation
- tool execution
- permission evaluation

Calling `build()` and `render()` must remain pure in-memory transformations.

## Expected dependency changes

NONE.

Do not change `pyproject.toml` or `uv.lock`.

If a new dependency appears necessary, stop and return BLOCKED rather than adding it.

## Focused tests

Create:

`tests/test_provenance_citations.py`

Tests must be deterministic and in-memory.

Use real PHX-034 contract objects where useful.

No SQLite fixture is required for citation construction itself.

No live provider/model/network.

## Minimum focused contract tests

At minimum prove:

1. service authoritative agent ID is `knowledge_rag` if identity surface exists
2. default registry accepted
3. missing `knowledge_rag` rejected
4. alias-only VEGAPUNK rejected
5. removed alias works
6. renamed alias works
7. exact RetrievalContextResult accepted
8. result subclass rejected
9. result injected extras rejected
10. malformed nested ContextItem rejected
11. empty result accepted
12. build output frozen
13. bundle extra fields rejected
14. citation entry frozen
15. citation entry extra fields rejected
16. render record frozen
17. render record extra fields rejected

## Minimum one-to-one / numbering tests

18. zero items -> zero citations
19. one item -> one citation
20. N items -> N citations
21. retrieval order preserved
22. ordinals contiguous 1..N
23. citation IDs exactly citation-1..citation-N
24. markers exactly [1]..[N]
25. duplicate citation IDs rejected
26. duplicate ordinals rejected
27. noncontiguous ordinals rejected
28. forged marker rejected
29. forged citation ID rejected
30. entry retrieval ID mismatch rejected
31. duplicate vector record ID rejected

## Minimum memory provenance tests

32. memory source kind preserved
33. memory source ID preserved
34. memory source hash preserved
35. vector record ID preserved
36. source_ref preserved when present
37. source_ref None preserved
38. memory type preserved
39. memory scope preserved
40. memory confidence preserved
41. authoritative memory rejected
42. document locators on memory rejected
43. no content field in citation entry

## Minimum document provenance tests

44. document source kind preserved
45. chunk source ID preserved
46. source hash preserved
47. vector record ID preserved
48. document source_ref preserved
49. document ID preserved
50. page preserved
51. absent page preserved as None
52. section preserved
53. absent section preserved as None
54. path preserved
55. absent path preserved as None
56. source timestamp preserved UTC
57. parser version preserved
58. chunk index preserved
59. authoritative flag preserved true/false
60. memory-only provenance on document rejected

## Minimum rendering tests

61. one render record per citation
62. render order preserved
63. marker preserved exactly
64. citation ID preserved exactly
65. memory canonical label deterministic
66. memory source_ref fallback to source_id
67. document canonical base label deterministic
68. page locator appended only when present
69. section appended only when present
70. path appended only when present
71. chunk locator present
72. locator ordering deterministic
73. raw content never appears in label
74. source hash not displayed by default
75. metadata cannot appear because it is unavailable
76. repeated rendering byte-for-byte equal

## Minimum display-safety tests

77. newline in source_ref cannot create a second rendered line
78. tab in source_ref collapses to safe spacing
79. carriage return collapses
80. multiline section rendered as one display line
81. multiline path rendered as one display line
82. shell-looking source text is inert display data
83. prompt-injection-looking source_ref is inert display data
84. URL-looking source_ref is not dereferenced
85. Markdown-looking source_ref is not parsed into a link
86. HTML-looking source_ref is not executed/interpreted
87. source-provided `[99]` text never changes generated marker
88. renderer creates no file/network activity

## Minimum trust/separation tests

89. citation does not expose `verified=true`
90. citation does not rename authority to trust
91. memory citation not authoritative
92. authoritative document still treated as untrusted display data
93. no source truth claim in contracts
94. no freshness re-check
95. no SQL/session dependency
96. no VectorStore.search
97. no EmbeddingProvider
98. no model generation
99. no EventBus
100. no ToolRegistry
101. no PermissionEngine
102. no retrieval invocation
103. no PHX-031 service invocation
104. no PHX-032 ingestion invocation
105. no source mutation
106. no vector mutation
107. no schema/Alembic change
108. no API/frontend integration

## PHX-034 regression

Run:

`uv run --locked pytest tests/test_retrieval_context.py`

Existing baseline before PHX-035:

367 passed.

Do not modify PHX-034 production behavior.

## Phase-3 regressions

Run individually:

`uv run --locked pytest tests/test_memory_schema.py`
`uv run --locked pytest tests/test_pudding_memory_service.py`
`uv run --locked pytest tests/test_document_ingestion.py`
`uv run --locked pytest tests/test_vector_store_sqlite.py`
`uv run --locked pytest tests/test_retrieval_context.py`
`uv run --locked pytest tests/test_persistence.py`

Existing baselines before PHX-035:

- PHX-030: 244
- PHX-031: 167
- PHX-032: 300
- PHX-033: 273
- PHX-034: 367
- persistence: 12

No migration-head evolution is expected.

## PHX-014 embedding regression

Run:

`uv run --locked pytest tests/test_embeddings.py`

Embedding behavior must remain unchanged.

## Phase-1 regressions

Run all seven established Phase-1 model suites individually.

All must pass unchanged.

## Phase-2 regressions

Run all nine established Phase-2 orchestration suites individually.

All must pass unchanged.

## Full Python verification

Run:

`uv lock`
`uv sync --locked`
`uv run --locked pytest`
`uv run --locked ruff check .`
`uv run --locked ruff format --check .`
`uv run --locked mypy`
`uv build`

Pre-PHX-035 Python baseline:

5,018 passed.

Dependency changes expected:

NONE.

## Foundation regression

Existing bootstrap/API/configuration/persistence/events/hardware suites must pass.

Confirm exact `/health` response unchanged.

Confirm:

- Alembic head remains `0004_embedding_records`
- canonical EventType count remains 32

## Desktop regression

Run:

`npm test`
`npm run typecheck --workspace @phoenix/desktop`
`npm run build --workspace @phoenix/desktop`

All must pass.

Desktop files remain unchanged.

## Security review

Confirm:

- no credentials/API keys/tokens
- no full retrieved content copied into citation records
- no citation/source logging
- no source dereference
- no clickable-link generation
- no HTML/Markdown execution
- no raw SQL/DB path
- no source authenticity claim
- no authority-to-permission conversion
- no provider/model/tool execution
- no direct frontend data path

Run repository credential/signature checks.

## Invariant review

Review INV-001 through INV-024.

Especially:

INV-001 — citation evidence remains subordinate to PHOENIX.

INV-003 — citation/provenance cannot grant permission.

INV-004 — no privileged tool bypass.

INV-011 — frontend gains no direct source/database/provider path.

INV-014 — VEGAPUNK alias is removable.

INV-017 — source labels and retrieved provenance remain untrusted data.

INV-018 — no secret-specific citation persistence and no content duplication.

INV-020 — no schema mutation.

INV-021 — malformed provenance fails explicitly.

INV-024 — no optional dependency becomes required.

## ADR policy

At most one ADR.

A useful ADR-044 may record:

- PHX-035 transforms only immutable PHX-034 retrieval evidence
- one citation per selected context item
- deterministic 1-based markers and citation IDs
- no source re-resolution or dereference
- provenance evidence does not prove truth/authenticity
- safe one-line plain-text labels
- structured authority retained without trust claims
- no retrieved-content duplication in citation records
- no inline answer citation insertion/claim entailment
- no persistence/events/providers/tools/API/frontend/schema changes

Do not create multiple ADRs.

## Acceptance criteria

PHX-035 is PASS only when all of the following are true:

1. exact PHX-034 result revalidation exists
2. every selected item receives exactly one citation
3. no extra citation is fabricated
4. numbering/markers are deterministic and contiguous
5. memory provenance is preserved exactly
6. document provenance is preserved exactly
7. authoritative flag is preserved narrowly
8. citation records do not duplicate full retrieved content
9. plain-text labels are deterministic and one-line safe
10. source refs/paths are never dereferenced
11. Markdown/HTML links are not generated
12. no source truth/verification claim exists
13. no inline answer citation insertion exists
14. no claim entailment/generation exists
15. no DB/vector/provider/event/tool/permission work exists
16. no schema/migration/API/frontend changes exist
17. focused tests pass
18. all regressions/quality gates pass
19. ticket and feature index are marked DONE only after verification
20. only intended feature-branch commit is pushed

## Completion evidence

When all gates pass, update this ticket from TODO to DONE and append concise evidence covering:

- files
- public API
- knowledge_rag identity/alias independence
- exact input revalidation
- citation entry/bundle contracts
- one-to-one coverage
- numbering/marker rules
- memory provenance
- document provenance
- authority semantics
- renderer and display sanitization
- no content duplication
- no source resolution/dereference
- no truth claim
- no inline answer insertion/entailment
- side-effect isolation
- targeted test count
- PHX-034 regression
- Phase-3 regressions
- Phase-1/2 regressions
- full Python result
- foundation result
- desktop result
- schema/event/dependency status
- security/invariants
- known limitations

Then update `FEATURE_TICKETS.md`:

`PHX-035 Provenance/citations — TODO`

to:

`PHX-035 Provenance/citations — DONE`

PHASE 3 is then complete.

Do not change PHX-040 or any later ticket status.

## Exact commit message

After all gates pass:

`feat: complete PHX-035 provenance citations`

Push only:

`feature/PHX-035-provenance-citations`

Do not create a PR, merge, push main, force-push, or start PHX-040.

## Verified PHX-035 completion

Created `phoenix/rag/citations.py` and `tests/test_provenance_citations.py`.
Supporting changes: this ticket, only PHX-035 in `FEATURE_TICKETS.md`, and
one accepted ADR-044 in `DECISIONS.md`. Historical runtime/tests, schema,
migrations, desktop and dependency manifests are unchanged.

Public API: `ProvenanceCitationService.build` and read-only `agent_id`,
`CitationRenderer.render`, frozen extra-forbidden `CitationEntry`,
`CitationBundle`, `CitationRenderRecord`, and explicit safe `CitationError` /
`CitationInputError`. Registry owner is `knowledge_rag`; default, removed and
renamed aliases work, while alias-only/missing/forged registry evidence fails.

Build exact-revalidates stored PHX-034 result, every nested exact ContextItem
and diagnostics, rejecting subclasses, copied extras, malformed provenance,
totals/order/correlation contradictions and duplicate vector identity.
Preserve retrieval/provider/model/UTC as_of. Exactly one detached citation per
selected item, in original order, even when several vectors share a source.
Empty results produce empty tuples. Strict ordinals 1..100 are contiguous;
IDs are exactly citation-{ordinal}, markers exactly [{ordinal}]. Bundle entries
must share retrieval_id and have unique vector_record_id. No separate optional
coverage object; validation and one-to-one tests provide structural evidence.

The 20-field entry copies only selected provenance: source kind/ID/vector ID/
lowercase 64-character hash/reference and document or memory fields. No content,
query, vector, score, arbitrary metadata, prompt, answer or permission/truth state.
Memory preserves type/scope/finite confidence/reference-or-None and stays
nonauthoritative, with no document fields. Documents preserve document ID,
reference, parser/chunk index, optional path/page/section/UTC timestamp, and
original authoritative true/false, with no memory fields. No provenance inference,
hash recomputation, newer source resolution, authenticity or verification claim.

Bundle has five fields: retrieval_id/provider_id/model_id/as_of/citations.
Render records have four: citation_id/ordinal/marker/label. Memory label is
Memory · type=<type> · scope=<scope> · source=<reference-or-ID>. Document label
is Document · source=<reference> · document=<ID>, appending present page,
section, path and chunk in that order. Display whitespace collapses to one line;
structured provenance retains its original value. Unicode and URL/Markdown/HTML/
shell/prompt-looking values remain inert untrusted text. Source [99] text cannot
change generated markers. No link wrappers, hash/confidence/authority decoration,
content duplication, answer insertion, generation or claim entailment.

Validation commands and results:

- `uv run --locked pytest tests/test_provenance_citations.py -q`: **173 passed**,
  final focused run 1.38s. Includes boundaries, numbering, provenance, malicious
  display text, no-content contracts and fresh-process isolation.
- Individual `uv run --locked pytest tests/test_<suite>.py -q`:
  PHX-034 **367**, PHX-030 **244**, PHX-031 **167**, PHX-032 **300**,
  PHX-033 **273**, persistence **12**, PHX-014 embeddings **182** passed.
- All seven Phase-1 suites individually: **812 passed**; all nine Phase-2
  suites individually: **2729 passed**. Existing tests unchanged.
- Final `uv run --locked pytest -q --junitxml=<temporary report>`:
  **5191 passed**, exit 0, 89.84s. Log/XML outside the repository. One existing
  Starlette/httpx deprecation warning; dependencies unchanged.
- `uv run --locked ruff check .`: passed; `uv run --locked ruff format --check .`:
  **79 files** passed; `uv run --locked mypy`: **74 source files** passed.
- `uv lock`, `uv sync --locked`, `uv build`: passed, **36 packages**,
  unchanged manifests/lockfile; wheel and source distribution built.
- Foundation bootstrap/API/config/persistence/events/hardware command:
  **126 passed**, including exact unchanged `/health`, in 4.63s.
- Desktop `npm test`: **4 passed**; `npm run typecheck --workspace
  @phoenix/desktop` and `npm run build --workspace @phoenix/desktop`: passed.

Fresh-process import/construction/build/render guards reject database connections,
sessions, source reads/dereference, network, retrieval/vector/provider work,
source writes, EventBus, process/thread/task creation and file writes. All outputs
are deterministic and detached. Static import/call review confirms no generation,
tool/permission, API/frontend, persistence, cache or worker integration.
Alembic head remains `0004_embedding_records`; four revisions and env.py are
unchanged. EventType remains **32**. Compared **98 historical protected files**
against origin/main: unchanged. Repository credential-signature scan:
**173 files, zero matches**. Reviewed INV-001 through INV-024 and permission /
security implications: preserved. PHOENIX retains supremacy; provenance grants
no permissions, and labels remain untrusted data.

Implementation/verification acceptance criteria 1–18 pass. DONE status follows
the verified gates. Complete staged review, exact commit and feature-branch-only
push are recorded in the final lifecycle report. Phase 3 is complete in the
feature branch; PHX-040 remains TODO and has not been started.

Limitations: citation identity is local to one bundle. Evidence preserves the
retrieval snapshot without checking later source changes or truth. Consumers
must keep labels as untrusted plain text and authorize future source interaction
independently. No inline answer citations, claim verification, generated answer,
new source kinds or API/frontend integration are supplied by this ticket.
