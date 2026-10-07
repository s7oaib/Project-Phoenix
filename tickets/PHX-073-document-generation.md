# PHX-073 — PDF / DOCX / Markdown generation

Status: TODO
Phase: 7 — Media & Documents
Branch: feature/PHX-073-document-generation
Starting main/base SHA: 6603c9d308361ae9559d72044196df88567bf8a2
Expected main: 6603c9d308361ae9559d72044196df88567bf8a2

## Objective

Implement PHOENIX V1's deterministic, local-first document-generation boundary for:

- PDF,
- DOCX,
- Markdown.

Generation consumes a strict neutral report specification and returns a structurally
verified in-memory artifact.

PHX-073 does NOT write user filesystem paths.

This separation is mandatory because the frozen PHX-043 FilesystemTool deliberately
supports text I/O only and explicitly excludes binary read/write. PDF/DOCX persistence
must therefore not bypass ToolRegistry/PermissionEngine by writing directly from the
generator.

PHOENIX remains the sole supreme orchestrator.

ROBIN (`document_analyst`) remains document analysis/generation owner.
VEGAPUNK (`knowledge_rag`) remains indexing/RAG owner.
No new agent/model/process/permission authority is created.

Completion of PHX-073 completes Phase 7 only if all deterministic gates pass.

## Frozen ownership / persistence boundary

PHX-073 owns:

- strict neutral document/report specification,
- bounded local rendering,
- PDF rendering to BytesIO,
- DOCX rendering to BytesIO,
- Markdown rendering to UTF-8 bytes,
- artifact hashing and metadata,
- in-memory structural verification,
- cancellation/state around local synchronous rendering,
- ReportLab dependency promotion,
- exact regression repairs required by that promotion.

PHX-073 does NOT own:

- filesystem persistence,
- binary filesystem write tool,
- overwrite semantics,
- export/save dialogs,
- downloads,
- browser delivery,
- Tauri/frontend file workspace,
- FastAPI artifact route,
- DB/blob persistence,
- memory/RAG ingestion,
- model generation,
- citation retrieval,
- CSV generation,
- image embedding,
- HTML rendering,
- templates loaded from disk,
- user font loading.

A later trusted integration may persist `GeneratedArtifact.artifact_bytes`.
That persistence must cross an appropriate permission-gated binary-write/export
boundary and verify the resulting file exists/is readable before reporting
"saved to disk" success.

PHX-073 itself may report only:
- artifact generation succeeded,
- artifact was structurally verified in memory.

It must never claim:
- a disk file exists,
- a path was saved,
- a file is readable from disk.

## Existing authorities to preserve

- PHOENIX is sole supreme orchestrator.
- AIZEN may plan only.
- ROBIN owns document analysis/generation as a bounded role.
- VEGAPUNK owns indexing/RAG.
- LEVI verification semantics unchanged.
- MAHORAGA cannot bypass permissions.
- KAIDO remains degraded/failsafe only.
- ToolRegistry/PermissionEngine/AuditTrail production remain unchanged.
- FilesystemTool remains unchanged; no binary capability is added.
- PHX-072 parsers remain unchanged except the one explicitly authorized historical
  dependency-test repair below.
- uploaded/external content remains untrusted.
- EventType remains exactly 32.
- five migrations remain unchanged; sole head `0005_audit_events`.
- no API/frontend/schema changes.

## Preferred implementation

Create:

- `phoenix/documents/generation.py`
- `tests/test_document_generation.py`

Narrow docs/dependency/status changes as frozen below.

Do not modify `phoenix/documents/parsers.py`.

At most one accepted ADR:
- ADR-066.

## Neutral report model

Implement frozen, extra-forbidden, strict Pydantic contracts.

### DocumentFormat

Exactly:
- `pdf`
- `docx`
- `markdown`

### ReportDocument

Exact logical fields:

- `document_id`
- `title`
- `subtitle`
- `author`
- `sections`
- `provenance`

Rules:
- document_id: strict meaningful string, bounded 1..128 chars.
- title: strict meaningful text, bounded by generation limits.
- subtitle: optional strict text.
- author: optional strict text.
- sections: nonempty ordered tuple.
- provenance: ordered tuple, may be empty.

No:
- raw HTML,
- raw Markdown,
- raw XML,
- arbitrary style object,
- arbitrary metadata object,
- filesystem path,
- URL fetch instruction,
- executable callback.

### ReportSection

Exact:
- `heading`
- `blocks`

Heading is strict meaningful text.
Blocks are a nonempty ordered tuple.

### Block vocabulary

Exactly:
- paragraph,
- bullet_list,
- table.

Use a strict discriminated union or equivalent exact closed contract.

#### Paragraph block
- `kind = "paragraph"`
- `text`

#### Bullet-list block
- `kind = "bullet_list"`
- `items`
- nonempty ordered tuple of strict text items.

#### Table block
- `kind = "table"`
- `headers`
- `rows`

Headers:
- nonempty ordered tuple,
- exact column count.

Rows:
- ordered tuple of rows,
- each row exact same width as headers,
- cell values strict text only.

No colspan/rowspan.
No formulas.
No cell markup.
No arbitrary width/style objects.

### ReportProvenance

Exact:
- `label`
- `reference`

Both strict bounded plain text.

Provenance is rendered in a deterministic final "Sources" section.
It grants no authority and is never dereferenced/fetched/opened.

## Generation request

Create exact frozen `DocumentGenerationRequest`:

- `generation_id`
- `format`
- `suggested_filename`
- `document`

generation_id:
- strict meaningful opaque string,
- caller-provided exact value preserved.

suggested_filename:
- display/suggestion only,
- no path opening/writing,
- strict 1..255 chars,
- no C0/C1/NUL,
- no slash/backslash,
- no colon/drive/ADS syntax,
- no leading/trailing whitespace,
- not `.`/`..`,
- required extension must exactly match requested format:
  - .pdf
  - .docx
  - .md

Lowercase extension only for validation.
Preserve accepted filename spelling/case exactly.

## Generation limits

Create frozen, extra-forbidden `DocumentGenerationLimits`.

All are strict positive non-boolean integers:

- `max_total_input_chars`
- `max_sections`
- `max_blocks`
- `max_list_items`
- `max_table_rows`
- `max_table_columns`
- `max_cell_chars`
- `max_provenance_items`
- `max_output_bytes`
- `max_pdf_pages`

No implicit project-wide defaults.

The generator must enforce limits before or during rendering as early as safely possible.

Total input chars count every user-controlled text field exactly once.

Table cell/header content counts toward total input chars.

No silent truncation.

## Generated artifact

Create frozen, extra-forbidden `GeneratedArtifact` with exact logical evidence:

- `generation_id`
- `document_id`
- `format`
- `suggested_filename`
- `media_type`
- `artifact_sha256`
- `byte_size`
- `page_count`
- `verified`
- `artifact_bytes`

Rules:
- artifact_bytes strict bytes, nonempty, `repr=False`, excluded from ordinary serialization.
- artifact_sha256 is lowercase SHA-256 hex of exact artifact_bytes.
- byte_size == len(artifact_bytes).
- verified must be true for returned success.
- page_count:
  - PDF: exact positive verified page count,
  - DOCX/Markdown: None.
- result is exact revalidated before return.

Media types:
- PDF: `application/pdf`
- DOCX: `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- Markdown: `text/markdown; charset=utf-8`

No path field.
No saved flag.
No file_exists field.

## Closed error vocabulary

Exactly:

- `invalid_input`
- `unsupported_character`
- `limit_exceeded`
- `generation_failed`
- `verification_failed`
- `cancelled`
- `busy`
- `closed`
- `wrong_event_loop`

No raw third-party diagnostics, artifact bytes, private document text, stack traces or
filesystem paths in safe exception text/repr.

## Generator runtime

Implement a local `DocumentGenerator`.

Requirements:
- import performs no I/O,
- construction performs no I/O,
- one event loop per runtime,
- one active generation at a time,
- expose `active_generation_id`,
- duplicate/concurrent generation while active fails `busy`,
- explicit idempotent same-loop `aclose()`,
- closed generator rejects new work,
- caller task owns generation,
- no hidden task/thread/process/executor/timer/scheduler,
- CancelledError propagates.

Cancellation checkpoints:
- before rendering,
- between sections,
- between blocks,
- between table rows where generator controls iteration,
- immediately before synchronous library render/save calls,
- immediately after synchronous library calls,
- before verification,
- before returning success.

Synchronous ReportLab/python-docx/pypdf/native compression calls cannot be preempted
mid-call. Recheck cancellation immediately after return.

Close during an active synchronous call cannot undo already-created in-memory bytes,
but the operation must not return normal success after closure/cancellation becomes
observable.

No model generation.
No BLAZE-6 lease.

## Untrusted content handling

Every document text field is untrusted inert content under INV-017.

Never:
- execute content,
- interpret content as permission,
- follow provenance references,
- fetch links,
- import code,
- evaluate formulas,
- treat Markdown/HTML/XML from input as raw structural markup,
- automatically insert generated content into memory/RAG/model context,
- automatically invoke a filesystem/browser/tool action.

Structure comes only from the strict ReportDocument AST.

### PDF markup safety

ReportLab Paragraph interprets an XML-like mini-markup language.

Therefore every user-controlled string passed into ReportLab Paragraph/List/Table
paragraph flowables MUST be escaped first.

No input may inject:
- tags,
- entities,
- links,
- font directives,
- image directives,
- paragraph styling.

Line breaks may be converted to code-owned safe `<br/>` only AFTER user content is
escaped.

### Markdown markup safety

Generated Markdown structure is code-owned.

User-controlled text must be deterministically escaped so it cannot create:
- a new heading,
- a new list item,
- a raw HTML block,
- an injected table column,
- a link/image construct,
- an unintended fenced block.

No raw HTML output.

Document structure must derive only from the neutral AST.

## PDF generation

Authorize direct dependency:

`reportlab>=5.0.1,<6`

Current verified release on 2026-10-08:
- ReportLab 5.0.1,
- Python >=3.9,<4,
- Python 3.11 supported,
- ReportLab Platypus supports document generation to an object with a `write` method
  such as BytesIO.

Use:
- `io.BytesIO`,
- Platypus `SimpleDocTemplate` or a narrowly equivalent local template,
- built-in ReportLab Base-14 fonts only,
- no filesystem font lookup,
- no user font registration,
- no image flowables,
- no SVG,
- no URLs.

Do not use RML.

Do not use eval-like ReportLab features.

### PDF text character boundary

For PHX-073 V1 PDF, all rendered user text must be exactly encodable in Windows-1252
before entering ReportLab Base-14 font rendering.

If any user-controlled rendered text cannot encode as cp1252:
- fail `unsupported_character`,
- do not substitute/replacement-character silently,
- DOCX/Markdown may still support that Unicode text in separate requests.

This is an explicit V1 limitation that avoids hidden font files/search/runtime
dependencies.

### PDF professional layout

Code-owned layout must support:
- title,
- optional subtitle/author,
- section headings,
- paragraphs,
- bullet lists,
- tables,
- final Sources section when provenance exists,
- margins,
- page breaks/splitting,
- page number footer.

Use only code-owned styles.
No caller arbitrary font/size/color/style.

Use deterministic ordinary Base-14 font names such as Helvetica/Helvetica-Bold.

### PDF verification

After generation, before success:
- bytes start with PDF signature,
- byte size <= max_output_bytes,
- open exact bytes with existing pypdf 6.19.0 from BytesIO,
- strict reader,
- not encrypted,
- page count 1..max_pdf_pages,
- every page object can be accessed,
- title text is present in extracted text evidence from the generated PDF,
- no external path/network.

If verification fails:
`verification_failed`.

Do not claim PDF/A, accessibility tagging, embedded fonts, exact cross-viewer typography,
or hard process-memory bounds.

## DOCX generation

Reuse existing:
- `python-docx 1.2.0`.

No new DOCX dependency.

Use:
- `docx.Document()`,
- BytesIO save target.

python-docx official API supports file-like save targets.

No template file loaded from disk.

No user theme/font file.

Code-owned layout:
- title style,
- optional subtitle/author paragraph,
- Heading 1 sections,
- normal paragraphs,
- code-owned List Bullet style,
- Table Grid for tables,
- final Sources section when provenance exists.

No:
- macros,
- embedded files,
- images,
- hyperlinks,
- external relationships added by generator,
- headers/footers,
- comments,
- tracked changes,
- arbitrary XML injection.

User content enters python-docx text APIs only.

### DOCX verification

Before success:
- bytes <= max_output_bytes,
- ZIP signature valid,
- required members include `[Content_Types].xml` and `word/document.xml`,
- no duplicate member names,
- no encrypted flag,
- reopen exact bytes through `docx.Document(BytesIO(...))`,
- first title paragraph matches exact title,
- structural paragraph/table traversal succeeds,
- no filesystem path.

If verification fails:
`verification_failed`.

Generated DOCX is ordinary OOXML, not macro-enabled.

## Markdown generation

No new dependency.

Generate exact UTF-8 bytes with:
- `\n` line endings,
- final newline,
- code-owned:
  - `# ` title,
  - optional subtitle/author lines,
  - `## ` section headings,
  - paragraphs,
  - `- ` bullets,
  - pipe tables,
  - final `## Sources` when provenance exists.

Escape user-controlled text before inserting Markdown syntax.

Table cells:
- escape backslash first,
- escape pipe,
- replace embedded newline with code-owned `<br>` text only if angle brackets are
  themselves safely escaped or use a non-HTML literal representation such as `\\n`.
Prefer literal `\\n` to avoid raw HTML.

No raw HTML.

### Markdown verification

Before success:
- bytes <= max_output_bytes,
- strict UTF-8 decode,
- no NUL,
- output starts with the exact code-owned title heading containing escaped title,
- final newline present,
- decoded byte round-trip exact.

## Provenance rendering

When provenance exists, append a final code-owned Sources section in every format.

Each provenance item is rendered in source order as plain text:
- label,
- separator,
- reference.

No hyperlink creation.
No reference dereference.
No authority/confidence inference.
No arbitrary metadata serialization.

## Dependency promotion

PHX-073 authorizes exactly one new direct runtime dependency:

`reportlab>=5.0.1,<6`

No extras.

Current ReportLab 5.0.1 metadata requires ordinary runtime dependencies including:
- Pillow >=9,
- charset-normalizer.

Current main does not contain reportlab, pillow or charset-normalizer.

Expected new lock package identities are exactly:
- `reportlab`
- `pillow`
- `charset-normalizer`

Expected current resolver versions on 2026-10-08:
- reportlab 5.0.1,
- pillow 12.3.0,
- charset-normalizer 3.5.2.

If `uv lock` selects a different version satisfying ReportLab metadata because of
platform/resolver constraints, do not guess. Record the exact resolver result and verify
it against current package metadata. If any FOURTH new package identity appears or any
unrelated existing package version/hash changes, STOP BLOCKED.

Phoenix project metadata may change only to add reportlab as one direct requirement.

Do NOT add Pillow or charset-normalizer as direct Phoenix requirements.

Update `docs/25_DEPENDENCIES.md`.

## Current external evidence checked 2026-10-08

ReportLab:
- PyPI latest: 5.0.1, released 2026-08-20,
- Python >=3.9,<4,
- Python 3.11 classifier present,
- ReportLab Platypus BaseDocTemplate accepts a writable object such as BytesIO.

python-docx:
- 1.2.0,
- supports file-like save targets.

Pillow:
- latest current release 12.3.0 (2026-07-01).

charset-normalizer:
- latest current release 3.5.2 (2026-09-30).

These facts justify the frozen local generation dependency boundary only.

## Historical dependency guard repairs — proactively authorized

PHX-073's legitimate ReportLab promotion would otherwise fail two existing current/
historical dependency tests.

Exactly TWO existing test files may evolve.

No other historical repair is authorized.

### 1. Current foundation direct-dependency assertion

File:
`tests/test_bootstrap.py`

Test:
`test_distribution_has_foundation_runtime_dependencies`

Add exactly:
- `reportlab`

to the exact current Phoenix direct Requires-Dist name set.

Preserve:
- all existing names,
- exact equality,
- Python >=3.11,<3.12 assertion.

Do NOT add:
- pillow,
- charset-normalizer.

They are transitive.

No other bootstrap change.

Run exact test THREE consecutive times.
Run complete foundation and require 126 passed.

### 2. Freeze PHX-072 dependency promotion historically

File:
`tests/test_upload_parser_routing.py`

Current test:
`test_exact_authorized_dependency_delta_and_unchanged_existing_records`

Problem:
It compares the PHX-072 pre-main
`f6162fd3f231725ccef8006484d9c67208b37efb`
against the CURRENT working-tree manifests, so any later valid dependency promotion
would make a PHX-072 historical proof fail.

Required repair:
Make the PHX-072 promotion proof immutable.

Use:
- PHX-072 pre-promotion main:
  `f6162fd3f231725ccef8006484d9c67208b37efb`
- PHX-072 merged post-promotion:
  `6603c9d308361ae9559d72044196df88567bf8a2`

Prove from those two commits:
- direct additions exactly:
  - pypdf>=6.19,<7
  - python-docx>=1.2,<2
- new package identities exactly:
  - pypdf
  - python-docx
  - lxml
- exact historical versions:
  - pypdf 6.19.0
  - python-docx 1.2.0
  - lxml 6.1.3
- all unrelated historical package records equal after accounting only for those direct
  Phoenix references.

Then separately prove CURRENT project still contains:
- direct pypdf>=6.19,<7,
- direct python-docx>=1.2,<2,
- locked pypdf 6.19.0,
- locked python-docx 1.2.0,
- locked lxml 6.1.3.

Do NOT compare unrelated CURRENT dependencies to the PHX-072 era.

No parser production change.

Run this exact test THREE consecutive times.
Run the complete `tests/test_upload_parser_routing.py` suite THREE consecutive times and
require the current baseline 336 each unless PHX-073 legitimately adds no parser tests,
in which case count must remain exactly 336.

## Explicit exclusions

Do NOT implement:
- filesystem save/export,
- binary FilesystemTool changes,
- filesystem path validation,
- file overwrite/delete,
- FastAPI/Tauri/React integration,
- download endpoint,
- user-selected template path,
- user font,
- image embedding,
- charts,
- SVG,
- HTML-to-PDF,
- Markdown-to-HTML,
- raw markup pass-through,
- macros,
- PDF encryption/password,
- PDF signatures,
- PDF/A,
- DOCX headers/footers,
- tracked changes/comments,
- CSV generation,
- model/LLM generation,
- RAG retrieval,
- citation retrieval,
- DB/artifact persistence,
- EventType changes,
- migrations,
- PHX-080 work.

## Required focused tests

Create:
`tests/test_document_generation.py`

Synthetic in-memory content only.

No real filesystem output.
No internet.
No user fonts.
No external templates.

Minimum coverage:

### exact contracts
- exact format enum,
- strict frozen ReportDocument/Section/block/provenance/request/limits/artifact,
- extras/subclasses/model_copy forgery rejected,
- bool/int coercion rejection,
- filename path/control/extension mismatch attacks,
- private artifact bytes absent from repr/model dump.

### limits
- total chars,
- section count,
- block count,
- bullet item count,
- table row/column/cell limits,
- provenance count,
- output bytes,
- PDF page count,
- no silent truncation.

### untrusted content
- HTML-looking text,
- ReportLab markup-looking text,
- Markdown headings/list/table injection,
- formula-looking table cells,
- URLs in provenance,
- code-looking content.

Prove all remain inert text and cannot create extra structure beyond the neutral AST.

### PDF
- signature,
- in-memory BytesIO only,
- built-in fonts only,
- title/subtitle/author,
- heading/paragraph/bullet/table/sources,
- multipage/page footer,
- user text escaped before Paragraph,
- unsupported Unicode outside cp1252 fails explicitly,
- no replacement-character success,
- output-byte limit,
- page limit,
- pypdf structural verification,
- title text extracted,
- no encryption,
- no filesystem/network/font lookup under guarded execution,
- ReportLab failure maps safe,
- verification failure maps safe.

### DOCX
- in-memory save,
- title/subtitle/author,
- headings/paragraphs/bullets/tables/sources,
- Unicode preserved,
- required OOXML members,
- exact title after reopen,
- no macros/hyperlinks/images/external relationships,
- output limit,
- safe generation/verification failures,
- no filesystem/network/template lookup.

### Markdown
- exact UTF-8,
- LF only,
- final newline,
- deterministic structure,
- escaping of headings/lists/emphasis/links/images/raw HTML/table pipe/backslash/fence
  attacks,
- Unicode preserved,
- sources rendered plain,
- round-trip verification,
- output limit.

### runtime
- import/construction no I/O,
- one loop,
- active_generation_id,
- busy,
- close/idempotence,
- wrong loop,
- cancellation before render,
- cancellation between blocks,
- cancellation after synchronous library call,
- suppressed cancellation cannot return success,
- close while generation active cannot return late success,
- no hidden task/thread/process/executor.

### architecture/security
- no FilesystemTool/BrowserTool/ToolRegistry/PermissionEngine/AuditTrail invocation,
- no open/Path write/tempfile,
- no DB/memory/RAG/model/provider,
- ROBIN/VEGAPUNK identities unchanged,
- no EventType/migration,
- exact dependency delta,
- INV-001 through INV-024.

## Focused gate

Run:

`uv run --locked pytest tests/test_document_generation.py -q`

THREE consecutive times.

All pass.

## Required regressions

Run individually at minimum:

- `tests/test_upload_parser_routing.py` — 336, THREE consecutive times because of the
  authorized historical dependency-test repair,
- `tests/test_document_ingestion.py` — 300,
- `tests/test_retrieval_context.py` — 367,
- `tests/test_vector_store_sqlite.py` — 273,
- `tests/test_provenance_citations.py` — 173,
- `tests/test_filesystem_tool.py` — current exact suite count, including existing
  platform skips; no source/test modification,
- `tests/test_agent_registry.py` — 96,
- `tests/test_agent_model_router.py` — 213,
- `tests/test_tool_registry.py` — 204,
- `tests/test_permission_engine.py` — 926,
- `tests/test_audit_trail.py` — 302,
- `tests/test_secrets_boundary.py` — 402,
- `tests/test_spotify_integration.py` — 345,
- `tests/test_youtube_integration.py` — 358.

Foundation:
- exact bootstrap dependency test x3,
- complete foundation 126.

Protected Phase 6:
- startup 509,
- conversation 950,
- TTS 602,
- STT 414,
- VAD 403,
- wake 537.

## Full verification

Current pre-PHX-073 main full-suite baseline after PHX-072:
- 15,437 passed,
- 16 existing Windows filesystem/symlink skips,
- one existing Starlette/httpx warning.

Run:
1. `uv lock`
2. exact dependency delta inspection
3. `uv sync --locked`
4. focused PHX-073 x3
5. exact bootstrap guard x3
6. exact PHX-072 dependency-history guard x3
7. upload parser full suite x3
8. all required regressions
9. complete Python suite THREE consecutive times
10. `uv run --locked ruff check .`
11. `uv run --locked ruff format --check .`
12. `uv run --locked mypy`
13. wheel/sdist build outside repository
14. verify packaged generation source/dependency metadata
15. foundation
16. desktop npm test/typecheck/build
17. credential/signature scan
18. INV-001 through INV-024 individually
19. complete final diff review.

Final pass count will exceed 15,437 because PHX-073 adds tests.

Require same 16 existing skip identities/reasons and same one existing warning only.

No new skip/xfail/retry/flaky marker.

## Package/build verification

Wheel and sdist must include:
- `phoenix/documents/generation.py`.

Packaged bytes must match repository source exactly.

Distribution metadata must directly add only:
- reportlab.

Existing pypdf/python-docx direct requirements remain.

Pillow/charset-normalizer must be transitive only.

No generated PDF/DOCX/Markdown fixtures or validation artifacts packaged.

## Desktop/foundation

Foundation:
- 126 passed,
- exact /health unchanged.

Desktop:
- npm test: 4 passed,
- typecheck PASS,
- build PASS,
- no desktop source change.

## Security review

Explicitly verify:
- no path open/write,
- no tempfile artifact persistence,
- no network,
- no template/font lookup from user/system paths,
- no raw ReportLab markup injection,
- no raw Markdown structure injection,
- artifact bytes private,
- no automatic memory/RAG/model promotion,
- no permission grants,
- no tool invocation,
- no EventType/schema changes,
- dependency delta exact,
- historical repairs exact,
- safe third-party diagnostics,
- no credentials.

## ADR-066

Only after every gate passes, add exactly one accepted ADR-066 covering:

- PHX-073 is in-memory generation/verification only,
- disk export remains a separate permission-gated integration,
- neutral report AST,
- ROBIN/VEGAPUNK/PHOENIX ownership,
- PDF ReportLab dependency and Base-14/cp1252 limitation,
- pypdf verification,
- DOCX python-docx in-memory generation,
- Markdown deterministic escaping,
- artifact bytes privacy/hash evidence,
- synchronous-library cancellation limitation,
- exact dependency promotion/transitives,
- two narrow dependency guard repairs,
- no filesystem/API/frontend/schema/model/RAG changes.

Do not alter ADR-065 semantics.

## Documentation/status completion

Only after every required gate passes:

- ticket Status -> DONE,
- PHX-073 -> DONE in `FEATURE_TICKETS.md`,
- Phase 7 -> COMPLETE,
- update `docs/19_DOCUMENT_SYSTEM.md` narrowly,
- update `docs/25_DEPENDENCIES.md`,
- add ADR-066.

Do not start PHX-080.

Document explicitly:
- "generated artifact verified in memory" is not "saved file verified on disk",
- future export must permission-gate binary persistence and verify saved file before claiming
  disk success.

## Expected final scope

Normal PHX-073:
- `phoenix/documents/generation.py`
- `tests/test_document_generation.py`
- `pyproject.toml`
- `uv.lock`
- `docs/19_DOCUMENT_SYSTEM.md`
- `docs/25_DEPENDENCIES.md`
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- `tickets/PHX-073-document-generation.md`

Authorized existing tests:
- `tests/test_bootstrap.py`
- `tests/test_upload_parser_routing.py`

Expected maximum: 11 paths.

No other path without explicit blocker clarification.

Must remain unchanged:
- `phoenix/documents/parsers.py`
- PHX-032 ingestion/persistence,
- FilesystemTool production/tests,
- ToolRegistry,
- PermissionEngine,
- AuditTrail,
- SecretStore,
- agents,
- models/providers,
- memory/RAG,
- Spotify/YouTube,
- voice,
- API/frontend,
- migrations/EventType.

## Historical diff review

Before completion inspect separately:

- `git diff -- tests/test_bootstrap.py`
- `git diff -- tests/test_upload_parser_routing.py`

Bootstrap may only add reportlab to exact current direct names.

Upload parser may only convert the named dependency proof to immutable PHX-072 pre/post
history plus current retention checks.

No unrelated formatting/semantic weakening.

## Final diff review

Run:
- `git diff --check`,
- complete manual diff,
- lock delta inspection.

Stage only authorized paths.

Then:
- `git diff --cached --check`
- `git diff --cached --stat`
- `git diff --cached`

No generated artifacts, PDFs, DOCX, Markdown samples, logs, receipts, caches, DBs, build
artifacts or credentials in repository.

## Completion commit

Only after every required gate passes:

`feat: complete PHX-073 document generation`

Exactly one implementation/completion commit after preparation.

No amend.

## Push

Push only:
`feature/PHX-073-document-generation`

Normal push.

No PR.
No merge.
No main push.
No force push.
No PHX-080.

Expected completed branch:
- 0 behind main,
- exactly 2 ahead:
  1. preparation ticket,
  2. completion commit.

## Live acceptance

No user-owned real artifact output is required for deterministic completion.

Report:

`LIVE DOCUMENT GENERATION ACCEPTANCE: PENDING — deterministic in-memory PDF/DOCX/Markdown generation and structural verification passed; no user-owned production document has been exported.`

Report:

`LIVE DISK EXPORT ACCEPTANCE: NOT IMPLEMENTED — PHX-073 does not write artifact bytes to disk; binary persistence remains a separate permission-gated integration.`

These are nonblocking.

## Known limitations

Preserve at least:
- generated artifacts are in-memory only,
- PDF Base-14 V1 explicitly rejects text outside cp1252,
- ReportLab/python-docx/pypdf synchronous calls are not hard-preemptible,
- no image/font/template embedding,
- no PDF/A/accessibility/signing/encryption,
- no full advanced Word layout,
- Markdown is code-owned escaped structure, not arbitrary raw Markdown passthrough,
- no CSV generation in PHX-073,
- no persistence/API/frontend integration.

## If blocked

If any required gate fails:
- Status remains TODO,
- PHX-073 remains TODO,
- Phase 7 remains INCOMPLETE,
- no completion commit,
- no incomplete push,
- no PHX-080.

If an unchanged historical failure outside the two authorized repairs appears:
- reproduce individually,
- STOP BLOCKED,
- do not repair without authority.

## Final report

If PASS, report at minimum:

1. PHX-073 STATUS
2. OFFICIAL LOCAL PATH
3. BRANCH
4. STARTING HEAD
5. EXPECTED / OBSERVED MAIN
6. FINAL COMMIT SHA
7. FILES CREATED
8. FILES MODIFIED
9. AUTHORIZED HISTORICAL FILES
10. DIRECT DEPENDENCY
11. NEW TRANSITIVE PACKAGES / VERSIONS
12. DOCUMENT FORMATS
13. REPORT AST
14. REQUEST CONTRACT
15. GENERATION LIMITS
16. GENERATED ARTIFACT CONTRACT
17. ERROR VOCABULARY
18. PDF GENERATOR
19. REPORTLAB VERSION
20. PDF CHARACTER LIMITATION
21. PDF MARKUP ESCAPING
22. PDF STRUCTURAL VERIFICATION
23. DOCX GENERATOR
24. DOCX STRUCTURAL VERIFICATION
25. MARKDOWN GENERATOR
26. MARKDOWN ESCAPING
27. PROVENANCE RENDERING
28. OUTPUT BYTE LIMITS
29. CANCELLATION / ACTIVE STATE
30. ROBIN AUTHORITY
31. VEGAPUNK AUTHORITY
32. PHOENIX AUTHORITY
33. FILESYSTEM/PERMISSION BOUNDARY
34. MEMORY/RAG STATUS
35. EVENTTYPE
36. MIGRATIONS
37. BOOTSTRAP HISTORICAL/CURRENT TEST x3
38. FOUNDATION
39. PHX-072 DEPENDENCY HISTORY TEST x3
40. UPLOAD PARSER FULL SUITE x3
41. FOCUSED PHX-073 RUN 1
42. FOCUSED PHX-073 RUN 2
43. FOCUSED PHX-073 RUN 3
44. DOCUMENT/RAG REGRESSIONS
45. FILESYSTEM REGRESSION
46. TOOL/SECURITY REGRESSIONS
47. SPOTIFY/YOUTUBE REGRESSIONS
48. PHASE-6 REGRESSIONS
49. FULL PYTHON RUN 1
50. FULL PYTHON RUN 2
51. FULL PYTHON RUN 3
52. RUFF
53. FORMAT
54. MYPY
55. BUILD / PACKAGE
56. DESKTOP
57. SECURITY
58. INV-001 THROUGH INV-024
59. FINAL DIFF
60. ADR-066
61. DOCUMENTATION
62. FEATURE_TICKETS / PHASE 7
63. COMMIT / PUSH
64. AHEAD / BEHIND
65. WORKING TREE
66. MAIN SAFETY
67. LIVE DOCUMENT GENERATION ACCEPTANCE
68. LIVE DISK EXPORT ACCEPTANCE
69. KNOWN LIMITATIONS
70. NEXT TICKET: PHX-080 — NOT STARTED

No PR.
No merge.
No main push.
No PHX-080.


## Authoritative blocker resolution — Pydantic discriminator / ReportLab typing — 2026-10-08

PHX-073 correctly stopped during initial validation before any completion commit/push.

Observed blockers:
1. Pydantic rejects a before-field validator applied directly to the discriminated-union
   discriminator field `kind`; module import fails.
2. Strict mypy reports missing ReportLab typing information plus ordinary PHX-073-local
   typing errors.
3. Ruff identified local formatting/line-length issues; formatting was run but the final
   lint gate has not yet been reverified.

This section authorizes only the following narrow repairs.

### A. Discriminator validation repair

The frozen block vocabulary remains exactly:
- paragraph,
- bullet_list,
- table.

Each block model must keep an exact `Literal` discriminator:
- `Literal["paragraph"]`
- `Literal["bullet_list"]`
- `Literal["table"]`

Do NOT attach a `field_validator(..., mode="before")` to the discriminator field used by
Pydantic's discriminated union.

Remove that invalid discriminator field validator.

Use Pydantic's discriminated-union Literal contract as the canonical discriminator
validation.

If additional anti-coercion/forgery validation is required, perform it at the model-level
or existing exact-contract wrapper, not as a before-validator on the discriminator field.

Preserve:
- frozen models,
- extra="forbid",
- strict validation,
- exact three block variants,
- model-copy/subclass/extras revalidation requirements.

No weakening to an open `str` discriminator is authorized.

### B. ReportLab typing repair — no additional dependency

Do NOT add `types-reportlab` or any other typing package.

Current external verification on 2026-10-08:
- ReportLab runtime is 5.0.1,
- the current `types-reportlab` package targets ReportLab 5.0.0,
- adding it would create a fourth new lock identity and violate PHX-073's frozen
  dependency delta.

Instead, add a narrowly scoped mypy override in `pyproject.toml` for external,
untyped ReportLab modules only:

```toml
[[tool.mypy.overrides]]
module = ["reportlab", "reportlab.*"]
ignore_missing_imports = true
```

Equivalent TOML ordering is acceptable.

The override must NOT:
- disable mypy globally,
- disable strict mode,
- ignore Phoenix-local errors,
- add `ignore_errors = true`,
- silence python-docx/pypdf/Phoenix modules,
- add per-line `# type: ignore` merely to avoid fixing local typing.

All PHX-073-local typing errors in `phoenix/documents/generation.py` and focused tests
must be fixed normally with precise annotations/narrowing/protocols/casts where justified.

The ReportLab override is type-checker configuration only; it is not a runtime dependency.

### C. Dependency delta remains frozen

Direct runtime addition remains exactly:
- `reportlab>=5.0.1,<6`

New runtime lock identities remain exactly:
- reportlab 5.0.1,
- pillow 12.3.0,
- charset-normalizer 3.5.2.

No fourth package identity.
No `types-reportlab`.
No unrelated package mutation.

The Phoenix direct runtime dependency set adds only ReportLab.

### D. Ruff repair

Run Ruff formatting only over PHX-073-local changed files as needed, then verify:

- targeted `ruff check`,
- targeted `ruff format --check`,
- final full-project `ruff check .`,
- final full-project `ruff format --check .`.

Formatter changes must not alter semantics or expand scope.

### E. Resume rules

Preserve the current uncommitted:
- `phoenix/documents/generation.py`,
- `pyproject.toml`,
- `uv.lock`.

After synchronizing this clarification, first verify:
1. module import succeeds,
2. targeted Pydantic contract/discriminator smoke tests pass,
3. targeted mypy for generation/tests passes,
4. targeted Ruff lint/format passes.

Only then continue the full original PHX-073 test matrix.

No additional historical repair is authorized.

Because this clarification adds a second preparation commit, successful PHX-073 history
relative to main is now expected to be exactly THREE commits ahead:
1. `docs: add PHX-073 document generation ticket`
2. this blocker-resolution commit
3. `feat: complete PHX-073 document generation`

Expected maximum final path scope remains 11 paths because the clarification modifies only
the already-authorized PHX-073 ticket and `pyproject.toml` was already authorized.

No PR, merge, main push, force push or PHX-080 work.
