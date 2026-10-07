# PHX-072 — Upload / parser routing

Status: TODO
Phase: 7 — Media & Documents
Branch: feature/PHX-072-upload-parser-routing
Starting main/base SHA: f6162fd3f231725ccef8006484d9c67208b37efb
Expected main: f6162fd3f231725ccef8006484d9c67208b37efb

## Objective

Implement PHOENIX V1's local-first upload-to-parser routing boundary for common document
formats while reusing the existing PHX-032 parser-neutral ingestion contract exactly.

PHX-072 owns:
- strict caller-supplied upload-byte admission,
- deterministic format detection/routing,
- concrete local TXT/Markdown/CSV/source-code parsers,
- concrete text-layer PDF parsing,
- concrete DOCX parsing,
- bounded image-format routing to an explicitly injected parser collaborator only,
- dependency promotion required for PDF/DOCX parsing,
- provenance-preserving conversion into existing PHX-032 DocumentSource/ParsedDocument.

PHX-072 does NOT own:
- upload UI,
- FastAPI/multipart endpoints,
- filesystem path opening,
- browser file choosers,
- persistence/chunking/embedding/retrieval changes,
- OCR implementation,
- document generation,
- PHX-073 work.

PHOENIX remains supreme.

ROBIN (`document_analyst`) remains the document-analysis/generation role.
VEGAPUNK (`knowledge_rag`) remains indexing/RAG owner.
This ticket creates no new agent/model/process and grants neither role new authority.

## Existing ingestion boundary is authoritative

PHX-032 already owns:
- `DocumentSource`,
- `DocumentParser`,
- `ParsedSegment`,
- `ParsedDocument`,
- `DocumentIngestionLimits`,
- `DocumentAdmissionPolicy`,
- deterministic chunking/deduplication/hashing,
- document/chunk persistence,
- post-commit document.indexed event behavior,
- active ingestion/cancellation.

PHX-072 MUST consume that boundary.

Do NOT duplicate, replace, fork or refactor:
- `phoenix/rag/ingestion.py`,
- `phoenix/persistence/documents.py`,
- the document schema,
- migrations,
- admission policy,
- chunking/deduplication,
- embedding/vector/retrieval behavior.

The intended integration remains equivalent to:

```python
route = router.resolve(upload)
await ingestion_service.ingest(source=route.source, parser=route.parser)
```

where `route.source` is an existing exact `DocumentSource` and `route.parser`
implements the existing `DocumentParser` protocol.

PHX-072 must include deterministic integration tests proving compatibility with PHX-032
without changing PHX-032 production.

## Preferred production/test scope

Create:

- `phoenix/documents/__init__.py`
- `phoenix/documents/parsers.py`
- `tests/test_upload_parser_routing.py`

Narrow required dependency/docs/status updates are authorized below.

At most one new ADR:
- ADR-065

No schema/EventType/API/frontend change.

## No privileged-path bypass

PHX-072 accepts BYTES already supplied by a trusted application boundary.

It MUST NOT:
- open `filename` as a filesystem path,
- resolve `source_ref` to disk,
- call FilesystemTool,
- call BrowserTool,
- create a file chooser,
- scan directories,
- follow symlinks,
- fetch URLs,
- open network sockets,
- invoke subprocesses,
- write parsed uploads to temporary files.

A future caller that reads a local path must obtain those bytes through the appropriate
existing permission/tool boundary before PHX-072.

User-initiated upload transport/UI wiring belongs to the later frontend/API integration
phase.

Because PHX-072 itself only transforms already-supplied in-memory bytes, it is NOT a new
ToolRegistry capability and must not invent a second PermissionEngine path.

PHX-032 admission policy remains the mandatory enforcement point before persistence.

## Upload input contract

Create a frozen, extra-forbidden exact `UploadDocument` (or equivalent) with:

- `document_id`
- `filename`
- optional `declared_media_type`
- `content_bytes`
- optional `source_timestamp`

Rules:

### document_id
- strict meaningful string,
- preserve caller identity,
- no coercion,
- no model-generated authority.

### filename
- strict string,
- 1..255 Unicode code points,
- valid UTF-8,
- no NUL/C0/C1 control characters,
- no forward slash,
- no backslash,
- no colon/drive/alternate-stream syntax,
- not `.` or `..`,
- no leading/trailing whitespace,
- at least one non-dot filename component and a supported final extension,
- preserve exact accepted display filename,
- lower-case ONLY the extracted extension for routing.

Filename is provenance/display data, never opened as a path.

### declared_media_type
- optional strict bounded ASCII media-type token,
- no parameters,
- no control characters,
- case-insensitive comparison but no arbitrary normalization,
- may be omitted,
- `application/octet-stream` means caller does not know the type and does not override
  extension/signature routing,
- any other supplied value must be compatible with the routed format.

The caller-provided media type never overrides incompatible extension/signature evidence.

### content_bytes
- strict bytes,
- nonempty,
- never logged/repr'd,
- never persisted by PHX-072,
- exact bytes become existing DocumentSource.content_bytes.

### source_timestamp
- optional aware datetime,
- normalize to UTC through existing DocumentSource validation,
- naive timestamps rejected.

No arbitrary upload metadata object in V1. This avoids creating a second metadata
validation/authority surface; code-owned provenance metadata is defined below.

## Canonical routed formats

Closed V1 `UploadFormat`:

- `text`
- `markdown`
- `csv`
- `code`
- `pdf`
- `docx`
- `image`

Extension routing:

### text
- `.txt`

### markdown
- `.md`
- `.markdown`

### CSV
- `.csv`

### PDF
- `.pdf`

### DOCX
- `.docx`

Do NOT accept:
- `.doc`
- `.docm`
- `.dotm`

### image
- `.png`
- `.jpg`
- `.jpeg`
- `.webp`

No built-in OCR in PHX-072.

### source-code/plain-code route
Exact V1 extension allowlist:

- `.py`
- `.pyi`
- `.js`
- `.jsx`
- `.ts`
- `.tsx`
- `.json`
- `.toml`
- `.yaml`
- `.yml`
- `.xml`
- `.html`
- `.htm`
- `.css`
- `.sql`
- `.sh`
- `.ps1`
- `.bat`
- `.cmd`
- `.c`
- `.h`
- `.cpp`
- `.hpp`
- `.java`
- `.kt`
- `.go`
- `.rs`
- `.rb`
- `.php`
- `.cs`

Code is parsed as inert text only.

No execution, rendering, imports, shell parsing, HTML DOM execution or syntax-driven action.

Unknown extensions fail explicitly as unsupported.

Do not infer an unsupported type from content alone.

## Canonical media types

Code-owned canonical DocumentSource.media_type:

- text: `text/plain`
- markdown: `text/markdown`
- csv: `text/csv`
- code: `text/plain`
- pdf: `application/pdf`
- docx:
  `application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- png: `image/png`
- jpeg: `image/jpeg`
- webp: `image/webp`

Compatible declared-media values:

- `application/octet-stream` for any format as unknown/non-overriding evidence,
- canonical value for the selected format,
- markdown may also accept `text/plain`,
- code may accept `text/plain`, `application/json`, `application/xml`,
  `text/xml`, or an exact `text/*` type only when extension still independently
  selects the code route.

A supplied incompatible media type fails explicitly.

No MIME sniffing library.

## Binary signatures

Before concrete binary parsing:

PDF:
- exact prefix begins `%PDF-`.

DOCX:
- valid ZIP package,
- required `[Content_Types].xml`,
- required `word/document.xml`.

PNG:
- exact PNG 8-byte signature.

JPEG:
- starts FF D8 FF.

WebP:
- RIFF container with `WEBP` signature in canonical position.

Signature mismatch is malformed input, not parser fallback.

Text/Markdown/CSV/code:
- route by extension,
- decode rules below,
- never treat binary NUL-bearing data as text.

## DocumentSource mapping

Router resolves accepted upload into the existing PHX-032 `DocumentSource`:

- document_id = upload document_id,
- source_type = `upload`,
- source_ref = `upload:` + document_id,
- media_type = code-owned canonical media type,
- content_bytes = exact upload bytes,
- source_timestamp = upload timestamp,
- metadata = exact code-owned JSON object:
  - `filename`
  - `upload_format`

No filesystem path.
No URL.
No arbitrary caller metadata.
No secret.
No parser object in metadata.

The source_ref remains opaque provenance evidence and is never dereferenced.

## Resolved route

Use a compact immutable runtime route object such as `ResolvedUploadRoute` with:

- exact existing `DocumentSource`,
- selected `UploadFormat`,
- selected existing `DocumentParser` collaborator.

The parser collaborator itself is runtime-only and is never serialized/persisted.

Routing performs no parse, DB session, event, embedding, model call or file/network I/O.

## Explicit parser limits

Require caller-supplied frozen, extra-forbidden `UploadParserLimits`.

No project-wide implicit defaults.

Use strict non-boolean positive integers for:

- `max_source_bytes`
- `max_text_chars`
- `max_segments`
- `max_pdf_pages`
- `max_pdf_content_stream_bytes`
- `max_docx_entries`
- `max_docx_member_uncompressed_bytes`
- `max_docx_total_uncompressed_bytes`
- `max_docx_compression_ratio`
- `max_csv_rows`
- `max_csv_columns`
- `max_csv_cell_chars`

Relations:
- source bytes checked before parser/library invocation,
- DOCX per-member <= total,
- all finite positive,
- booleans rejected.

Total retained parsed text across every built-in parser must not exceed
`max_text_chars`.

Retained segment count must not exceed `max_segments`.

These are parser-admission bounds only. The caller remains responsible for selecting
compatible PHX-032 DocumentIngestionLimits; PHX-072 must not silently rewrite them.

## Parser error contract

Use one safe closed parser/router error type with code vocabulary equivalent to:

- `invalid_input`
- `unsupported_format`
- `media_type_mismatch`
- `source_too_large`
- `decode_failure`
- `malformed_document`
- `encrypted_document`
- `no_extractable_text`
- `limit_exceeded`
- `parser_unavailable`
- `cancelled`
- `wrong_event_loop`

No raw document bytes, extracted text, XML, CSV cells, filenames containing private
content, third-party exception diagnostics or filesystem paths in safe exception text.

PHX-032 may translate parser failures to its existing DocumentParserError during ingest.

Do not change PHX-032 error vocabulary.

## UTF-8 text / Markdown / code parsers

Use stdlib only.

Decode:
- UTF-8,
- UTF-8 with BOM via `utf-8-sig`,
- strict errors.

No encoding guessing.
No chardet.
No locale fallback.
No replacement characters invented for invalid bytes.

Reject NUL-containing decoded input.

Require meaningful non-whitespace text.

Enforce `max_source_bytes`, `max_text_chars`, `max_segments`.

Return exactly one ParsedSegment for TXT/Markdown/code in V1:
- text = exact decoded content after UTF-8 BOM removal only,
- path = accepted filename,
- page = None,
- section = `text`, `markdown`, or `code`,
- metadata = empty object.

Parser IDs:
- text: `phoenix.text.utf8`
- markdown: `phoenix.markdown.utf8`
- code: `phoenix.code.utf8`

Parser version:
- `1`

Markdown is not rendered.
HTML/code is not executed.
JSON/XML/YAML/TOML are not semantically loaded; they remain inert source text.

## CSV parser

Use Python stdlib `csv` only.

Decode strict UTF-8/UTF-8-SIG first.

Use deterministic `csv.reader` with strict parsing.

No dialect sniffing.
No formula evaluation.
No spreadsheet execution.
No locale conversion.
No type inference.

Bound:
- source bytes,
- decoded total chars,
- logical rows,
- columns per row,
- characters per cell,
- retained segments.

Every logical CSV row becomes one ParsedSegment in source order.

Segment text is deterministic JSON-array representation of exact cell strings using
stdlib `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`.

Formula-looking cells such as:
- `=CMD(...)`
- `+1`
- `-1`
- `@SUM(...)`

remain inert strings exactly.

Use:
- path = filename,
- page = None,
- section = `row:<1-based logical row number>`,
- metadata = `{"row_number": <1-based integer>}`.

Parser ID:
`phoenix.csv.stdlib`

Parser version:
`1`

Malformed quoting or bounds failure is explicit.

No CSV output file is written.

## PDF dependency promotion

Authorize direct dependency:

`pypdf>=6.19,<7`

Current verified release on 2026-10-08:
- pypdf 6.19.0
- Python 3.11 supported.

Do NOT install extras such as crypto/image/full unless independently required by the
frozen scope. PHX-072 needs ordinary text-layer reading only.

Use:
- `io.BytesIO`
- `pypdf.PdfReader`

Never pass a filesystem path.

No password support.

Encrypted PDF => explicit `encrypted_document`.

No JavaScript execution.
No attachment extraction.
No external fetch.
No image extraction.
No OCR.

Parser ID:
`phoenix.pdf.pypdf`

Parser version:
use the validated installed pypdf library version; completion evidence must record it.

## PDF parsing contract

Preflight:
- source byte bound before PdfReader,
- `%PDF-` signature,
- valid reader construction,
- reject encrypted documents,
- page count <= `max_pdf_pages`.

Process pages in exact source order.

Before extracting each page:
- inspect the page content stream and enforce the configured
  `max_pdf_content_stream_bytes` using pypdf's available content-stream evidence as
  safely as the library permits,
- if the configured content-stream bound is exceeded, fail before retaining extracted
  page text.

Then call text-layer extraction only.

Each meaningful page produces one ParsedSegment:
- exact extracted text,
- path = filename,
- page = 1-based page number,
- section = None,
- metadata = empty object.

Whitespace-only pages may be omitted while retaining original page numbering.

All pages empty/no meaningful extracted text:
`no_extractable_text`.

Enforce total retained text and segment count.

Between pages, provide an async cancellation checkpoint owned by the caller task.
No hidden task/thread/process.

### Required PDF limitation honesty

pypdf's own documentation states that text extraction may require substantial memory
because page content streams must be parsed/decompressed, and pypdf is not OCR.

Therefore PHX-072 MUST NOT claim:
- a hard process-memory ceiling inside third-party decompression/extraction,
- OCR support,
- recovery of image-only scanned PDFs.

Document the exact limitation in ADR/docs/result evidence.

Input/page/content/text bounds reduce risk but do not make third-party parsing perfectly
preemptible or memory-hard.

## DOCX dependency promotion

Authorize direct dependency:

`python-docx>=1.2,<2`

Current verified release on 2026-10-08:
- python-docx 1.2.0,
- Python >=3.9 / Phoenix Python 3.11 supported.

python-docx depends on:
- `lxml`,
- `typing_extensions>=4.9`.

Current Phoenix lock already contains a compatible typing-extensions version.
A new lxml lock entry is expected if not already present.

Use official `python-docx`, not similarly named forks.

Parser ID:
`phoenix.docx.python-docx`

Parser version:
validated installed python-docx version; completion evidence records it.

## DOCX ZIP preflight

Before python-docx parses the package, preflight with stdlib `zipfile` using BytesIO.

No archive member may be extracted to disk.

Require:
- valid ZIP,
- unique member names,
- no encrypted member flag,
- <= `max_docx_entries`,
- each uncompressed member <= `max_docx_member_uncompressed_bytes`,
- total declared uncompressed bytes <= `max_docx_total_uncompressed_bytes`,
- each nonempty member compression ratio <= `max_docx_compression_ratio`,
- no absolute member name,
- no drive-qualified member,
- no backslash path,
- no `..` path segment,
- required `[Content_Types].xml`,
- required `word/document.xml`.

Read/scan bounded XML and relationship members in memory before python-docx:
- reject `<!DOCTYPE`,
- reject `<!ENTITY`,
- do not resolve external entities.

No macros:
- only `.docx` route is accepted,
- .docm/.dotm unsupported.

No archive extraction.
No embedded-object execution.
No external URL fetching.

## DOCX text extraction

Construct python-docx `Document` from BytesIO only.

Use supported document-order `Document.iter_inner_content()`.

For each top-level Paragraph:
- retain meaningful `.text` as one segment.

For each top-level Table:
- preserve table order,
- preserve row order,
- represent each row as tab-separated exact cell text,
- each meaningful row becomes one segment.

Provenance:
- path = filename,
- page = None,
- section is deterministic:
  - `paragraph:<ordinal>`
  - `table:<table-ordinal>/row:<row-ordinal>`
- metadata = empty object.

Enforce total text and segment limits.

Do not silently parse:
- headers/footers,
- footnotes/endnotes,
- comments,
- text boxes,
- embedded images,
- embedded Office objects,
- nested-table semantics beyond what python-docx exposes as ordinary cell text.

These unsupported surfaces are a documented V1 limitation, not fake success.

No network is ever required for DOCX parsing, including documents containing hyperlinks.

## Image routing

Recognize only:
- PNG,
- JPEG,
- WebP

using extension + signature + compatible media type.

PHX-072 provides NO built-in OCR and adds:
- no Pillow,
- no Tesseract,
- no OCR executable,
- no vision-model coupling.

The router may accept an explicit borrowed image `DocumentParser` collaborator.

If a valid image is uploaded and no image parser is supplied:
- fail explicitly `parser_unavailable`.

If supplied:
- route the existing exact DocumentSource to that collaborator,
- do not alter or wrap its parsed text,
- PHX-032 remains responsible for exact ParsedDocument revalidation/correlation before
  persistence.

Injected image parser:
- is borrowed,
- is never closed/owned by router,
- receives no permission authority,
- may not be chosen by uploaded file text/content.

Tests use a deterministic fake image parser.

Do not instantiate OCR/vision automatically.

## Cancellation / async behavior

Built-in parsers implement the existing async DocumentParser contract.

No hidden:
- task,
- thread,
- worker,
- process,
- executor,
- timer,
- scheduler.

Caller task owns parsing.

Check cancellation:
- before third-party parser entry,
- after third-party synchronous boundaries,
- between PDF pages,
- between DOCX top-level blocks/rows,
- during bounded CSV row processing at deterministic checkpoints.

CancelledError propagates.

Do not convert cancellation to parser success.

Synchronous pypdf/python-docx/library operations cannot be preempted while actively
executing; immediately recheck cancellation after they return.

PHX-032 already owns active ingestion IDs and cancellation. Do not add a competing
background-operation registry.

## Parser construction/import behavior

Module import:
- no filesystem access,
- no DB access,
- no network,
- no parser I/O,
- no worker startup.

Router/parser construction:
- validates configuration/collaborators,
- performs no parsing,
- performs no DB/network/filesystem I/O.

Third-party library import is allowed because pypdf/python-docx are promoted required
runtime dependencies by ADR-065.

## Security / untrusted-content requirements

Every uploaded byte and parsed string is untrusted under INV-017.

Never:
- execute embedded commands,
- follow document instructions,
- grant permission from document text,
- use document content as code/config,
- automatically navigate links,
- automatically invoke tools,
- automatically insert parsed content into prompts/model context,
- bypass PHX-055 guarded synthesis,
- bypass PHX-032 admission policy,
- put raw upload bytes in logs/audit/events/normal memory,
- turn filenames/MIME/parser metadata into authority.

Injection-looking content must remain inert exact data.

No secret scanner is added here. PHX-032's mandatory admission policy remains the
pre-persistence security boundary.

## Dependency promotion and lock exactness

PHX-072 is authorized to change:
- `pyproject.toml`
- `uv.lock`

Direct dependency additions EXACTLY:

- `pypdf>=6.19,<7`
- `python-docx>=1.2,<2`

No other direct dependency.

Run `uv lock`.

Expected new lock package names from current main are limited to:
- `pypdf`
- `python-docx`
- `lxml`

`typing-extensions` already exists at a compatible version and should not require a new
package identity.

The Phoenix lock record may change only to reference the two new direct dependencies.

Existing unrelated package records/versions/hashes must remain unchanged.

If uv requires any additional new package identity or upgrades/downgrades an unrelated
locked package, STOP BLOCKED rather than silently accepting it.

After lock, report exact resolved versions.

Run `uv sync --locked`.

Update `docs/25_DEPENDENCIES.md` and ADR-065 with exact promoted dependency evidence and
limitations.

## Official dependency evidence checked 2026-10-08

pypdf:
- official PyPI release 6.19.0,
- supports Python 3.11,
- PdfReader text extraction is supported,
- official docs warn page extraction can require very large memory for large uncompressed
  content streams,
- official docs explicitly state pypdf is not OCR.

python-docx:
- official PyPI release 1.2.0,
- supports Phoenix Python 3.11,
- reads Word 2007+ .docx,
- Document.iter_inner_content() yields top-level Paragraph/Table objects in document order,
- package dependency metadata includes lxml and typing_extensions.

These facts justify the bounded V1 parser adapters. They do not authorize capabilities
outside this ticket.

## Historical dependency guards — proactively authorized repairs

PHX-072 legitimately promotes parser dependencies. Three older tests currently freeze
Python manifests against `origin/main` or a current branch and would reject any valid
future dependency addition.

Exactly THREE historical test files may be repaired, only as specified here.

No other historical repair is authorized.

### 1. BrowserTool historical dependency proof

File:
`tests/test_browser_tool.py`

Test:
`test_only_authorized_playwright_dependency_promotion`

Problem:
The test currently compares current working-tree manifests against current
`origin/main`, strips only Playwright/pywinauto, and therefore rejects legitimate later
direct dependencies.

Required repair:
Convert only this dependency-history test to fixed PHX-050 historical evidence.

Use:
- PHX-050 pre-promotion main:
  `3c17b8a5291871c236cc3726b2b91097657d4ed4`
- PHX-050 merged post-promotion:
  `762be6471085fa5ccf5e905c3cab0e79a3de7faa`

Prove from those two immutable commits:
- direct Playwright addition is exactly `playwright>=1,<2`,
- new historical package identities are exactly Playwright and its required PHX-050
  transitives `greenlet` and `pyee`,
- historical locked Playwright remains 1.63.0,
- historical existing package entries are otherwise equivalent after removing only the
  authorized Phoenix Playwright dependency metadata.

Then separately prove CURRENT project still contains:
- direct `playwright>=1,<2`,
- locked Playwright 1.63.0,
- required greenlet/pyee dependency relationship.

Do NOT compare unrelated current dependencies to the PHX-050 era.
Do NOT weaken browser production/security assertions.
No BrowserTool production change.

### 2. Screenshot/vision dependency freeze

File:
`tests/test_screenshot_vision_loop.py`

Test:
`test_no_action_persistence_or_dependency_change`

Current broad `git diff origin/main` path list includes:
- `pyproject.toml`
- `uv.lock`

Required repair:
Remove ONLY those two manifest paths from this historical PHX-052 vision-isolation diff.

Preserve every existing protected production/source/schema path in the check.
Preserve:
- no-action AST checks,
- EventType 32,
- Alembic head,
- vision/model/tool isolation.

Add a narrow comment that PHX-072 owns the separately tested parser dependency promotion.

No screenshot/vision production change.

### 3. Prompt-injection source-protection freeze

File:
`tests/test_prompt_injection_defenses.py`

Test:
`test_protected_existing_sources_dependencies_schema_and_event_vocabulary`

Required repair:
Keep exact equality protection for current-main:
- `phoenix/` existing paths,
- `alembic/`,
- `apps/desktop/`.

Remove ONLY:
- `pyproject.toml`
- `uv.lock`

from this historical protected-source list.

Preserve:
- EventType 32,
- Alembic head,
- all prompt-injection functional/security tests,
- no source weakening.

Add a narrow comment that PHX-072's own dependency test owns manifest/lock exactness.

No PHX-055 production change.

### Historical repair verification

For each repaired named exact test:
- run THREE consecutive times.

Then run each complete historical suite THREE consecutive times:
- `tests/test_browser_tool.py` — expected 653 each,
- `tests/test_screenshot_vision_loop.py` — expected 397 each,
- `tests/test_prompt_injection_defenses.py` — expected 604 each.

No skip/xfail/retry/flaky marker.
No test deletion.
No count reduction.

If any other unchanged historical guard fails because of PHX-072's authorized dependency
promotion, reproduce and STOP BLOCKED. Do not repair it without new authority.

## Explicit exclusions

Do NOT implement:
- frontend upload UI,
- FastAPI upload endpoint,
- multipart handling,
- drag/drop,
- filesystem path reads,
- browser file chooser,
- cloud storage,
- URL document fetching,
- DOC legacy parser,
- XLS/XLSX parser,
- PPT/PPTX parser,
- RTF parser,
- email parser,
- archive extraction,
- DOCM/macros,
- OCR,
- Tesseract,
- Pillow,
- PDF image extraction,
- PDF password/decryption,
- document rendering,
- HTML execution,
- Markdown rendering,
- syntax execution,
- antivirus engine,
- secret classifier,
- new model/vision call,
- new EventType,
- new migration/schema,
- changes to ingestion/chunking/embedding/retrieval,
- generation/export,
- PHX-073.

## Required focused tests

Create comprehensive deterministic `tests/test_upload_parser_routing.py`.

No public internet.
No user files.
No Google/Microsoft services.
No shell/browser/frontend.
No OCR executable.
No real filesystem source opening.

Synthetic in-memory bytes only.

Minimum coverage:

### contracts/routing
- exact frozen UploadDocument,
- exact frozen limits,
- bool rejection,
- filename bounds/path attacks/control chars,
- extension case handling,
- unsupported extension,
- media-type compatibility/mismatch,
- octet-stream non-overriding behavior,
- source byte bound before parser/library,
- source_ref never dereferenced,
- exact DocumentSource mapping/provenance,
- route construction no I/O,
- parser collaborator not serialized.

### text/Markdown/code
- UTF-8,
- UTF-8 BOM,
- invalid UTF-8,
- NUL rejection,
- meaningful text requirement,
- exact one segment,
- exact path/section,
- full extension allowlist,
- no execution/rendering/import,
- injection-looking content inert,
- total-char/segment limits.

### CSV
- quoted fields/newlines,
- Unicode,
- empty cells/rows,
- deterministic JSON-array segment text,
- row numbering/provenance,
- row/column/cell bounds,
- malformed quoting,
- formula-looking cells inert,
- no dialect sniff/evaluation,
- cancellation checkpoints.

### PDF
- valid synthetic text PDF,
- multi-page source order,
- empty page omission with original page number,
- all-image/no-text => no_extractable_text,
- signature mismatch,
- malformed PDF,
- encrypted PDF rejection,
- page limit,
- source limit,
- content-stream configured bound,
- extracted total-char/segment bound,
- no path/temp/network,
- parser ID/version,
- cancellation between pages,
- suppressed/late cancellation cannot finalize after return,
- no OCR/image extraction claim.

### DOCX
- valid in-memory generated fixture,
- paragraph order,
- table order/row order,
- paragraph/table provenance,
- hyperlinks remain text without network,
- invalid ZIP,
- missing required members,
- duplicate archive members,
- encrypted flag,
- traversal/absolute/backslash member names,
- entry count,
- per-member size,
- total uncompressed size,
- compression ratio,
- DTD/entity rejection,
- malformed document XML,
- source/text/segment limits,
- .docm/.dotm/.doc unsupported,
- no archive extraction/tempfile,
- parser ID/version,
- cancellation checkpoints,
- no network.

### images
- PNG/JPEG/WebP signatures,
- extension/signature mismatch,
- no injected parser => parser_unavailable,
- injected fake parser selected exactly,
- borrowed parser not closed/owned,
- no automatic OCR/model call,
- PHX-032 revalidation remains downstream.

### PHX-032 integration
- resolved source + parser accepted by existing DocumentIngestionService,
- exact document ID correlation,
- existing admission policy still controls persistence,
- rejected admission remains no persistence/event,
- parser output remains untrusted,
- existing chunking/deduplication behavior untouched.

### architecture/security
- no filesystem open/path resolution,
- no socket/network,
- no BrowserTool/FilesystemTool,
- no subprocess,
- no hidden task/thread/worker,
- no DB/persistence code in new parser module,
- no EventType change,
- migrations exactly five/head 0005_audit_events,
- no ToolRegistry/PermissionEngine implementation,
- no memory/prompt/model promotion,
- ROBIN/VEGAPUNK identities unchanged,
- INV-001 through INV-024.

Add adversarial cases wherever required.

## Focused gate

Run:

`uv run --locked pytest tests/test_upload_parser_routing.py -q`

THREE consecutive times.

All pass.

Report exact count each run.

## Directly impacted regressions

Run individually at minimum:

- `tests/test_document_ingestion.py` — historical/current baseline 300
- `tests/test_retrieval_context.py`
- `tests/test_vector_store_sqlite.py`
- `tests/test_provenance_citations.py`
- `tests/test_agent_registry.py` — 96
- `tests/test_agent_model_router.py` — 213
- `tests/test_tool_registry.py` — 204
- `tests/test_permission_engine.py` — 926
- `tests/test_audit_trail.py` — 302
- `tests/test_secrets_boundary.py` — 402
- `tests/test_spotify_integration.py` — 345
- `tests/test_youtube_integration.py` — 358

Run the three repaired historical suites x3 as specified earlier.

## Protected Phase-6 regressions

Run individually:
- `tests/test_rise_phoenix_startup.py` — 509
- `tests/test_voice_conversation.py` — 950
- `tests/test_tts_provider.py` — 602
- `tests/test_stt_provider.py` — 414
- `tests/test_vad.py` — 403
- `tests/test_wake_word_provider.py` — 537

PHX-071's authorized split-baseline wake repair is already merged; do not modify it.

## Full verification

Current pre-PHX-072 main full-suite baseline after PHX-071:
- 15,101 passed,
- 16 existing Windows filesystem/symlink skips,
- one existing Starlette/httpx warning.

After implementation:

1. `uv lock`
2. exact dependency-delta inspection
3. `uv sync --locked`
4. focused PHX-072 x3
5. historical repaired exact tests x3
6. historical repaired full suites x3
7. all directly impacted regressions
8. protected Phase-6 regressions
9. full Python suite THREE consecutive times
10. `uv run --locked ruff check .`
11. `uv run --locked ruff format --check .`
12. `uv run --locked mypy`
13. wheel and sdist build outside repository
14. verify packaged parser source and dependency metadata
15. foundation bootstrap/API/config/persistence/events/hardware
16. desktop `npm test`
17. desktop typecheck/build
18. credential/signature scan
19. INV-001 through INV-024 individually
20. complete diff review.

The final full-suite pass count will exceed 15,101 because PHX-072 adds tests.

Require the same 16 existing skip identities/reasons and same existing warning only.

No new skip/xfail/retry/flaky marker.

All validation/build artifacts stay outside repository.

## Package/build verification

Wheel and sdist must contain:
- `phoenix/documents/__init__.py`
- `phoenix/documents/parsers.py`

Source bytes must match repository versions exactly.

Package metadata must declare exactly the two new direct dependencies in addition to
existing dependencies.

No parser fixtures, user documents or validation artifacts packaged accidentally.

## Foundation / desktop

Foundation expected:
- 126 passed,
- exact `/health` unchanged.

Desktop:
- `npm test` expected 4 passed,
- typecheck PASS,
- build PASS,
- desktop source unchanged.

No API/frontend source change.

## Security review

Explicitly verify:
- uploaded bytes never logged,
- no source bytes in repr/errors,
- no path opening,
- no temp extraction,
- no network,
- no external entity resolution,
- no archive path traversal/extraction,
- DOCX zip-bomb bounds,
- PDF limits/limitations truthful,
- no OCR dependency,
- no commands executed,
- CSV formulas inert,
- HTML/Markdown/source inert,
- no permission grant from content,
- no prompt/model automatic ingestion,
- PHX-032 admission policy preserved,
- no schema/EventType changes,
- dependencies exactly authorized,
- historical test repairs exactly scoped,
- no credential signatures.

## ADR-065

Only after successful implementation/gates, add exactly one accepted ADR-065 covering:

- PHX-072 upload bytes are caller-supplied; no filesystem path opening,
- router feeds existing PHX-032 DocumentSource/DocumentParser contracts,
- ownership split: ROBIN document analysis, VEGAPUNK indexing/RAG, PHOENIX orchestrates,
- supported V1 routes,
- UTF-8/CSV/code behavior,
- pypdf promotion and exact text-layer/no-OCR limitations,
- python-docx promotion and lxml transitive,
- DOCX in-memory ZIP preflight/bounds,
- image parser is explicit borrowed adapter only,
- no ToolRegistry/PermissionEngine duplication,
- no UI/API/persistence/schema changes,
- cancellation/synchronous-library limitation,
- exact dependency-delta policy,
- three narrow historical dependency-guard repairs.

Do not alter ADR-064 semantics.

## Documentation/status completion

Only after all required gates pass:

- mark PHX-072 DONE in this ticket,
- mark PHX-072 DONE in `FEATURE_TICKETS.md`,
- Phase 7 remains INCOMPLETE because PHX-073 is TODO,
- PHX-073 remains TODO and NOT STARTED,
- update `docs/19_DOCUMENT_SYSTEM.md` narrowly with implemented parsing support and limits,
- update `docs/25_DEPENDENCIES.md` with exact promoted parser dependencies/current lock
  versions and operational limitations,
- add ADR-065.

No PHX-073 implementation.

## Expected final scope

Normal PHX-072 paths:

- `phoenix/documents/__init__.py`
- `phoenix/documents/parsers.py`
- `tests/test_upload_parser_routing.py`
- `pyproject.toml`
- `uv.lock`
- `docs/19_DOCUMENT_SYSTEM.md`
- `docs/25_DEPENDENCIES.md`
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- `tickets/PHX-072-upload-parser-routing.md`

Exactly three authorized historical test paths may also change:

- `tests/test_browser_tool.py`
- `tests/test_screenshot_vision_loop.py`
- `tests/test_prompt_injection_defenses.py`

Expected maximum final scope: 13 paths.

No other path without an explicit blocker clarification.

Must remain unchanged:
- `phoenix/rag/ingestion.py`
- `phoenix/persistence/documents.py`
- all migrations
- EventType definitions
- ToolRegistry production
- PermissionEngine production
- AuditTrail production
- SecretStore production
- BrowserTool production
- screenshot/vision production
- prompt-injection production
- Spotify/YouTube production/tests
- Phase-6 production/tests
- API/frontend.

## Historical diff review

Before completion, inspect each of the three historical test diffs separately.

They may contain only the authorized guard evolution.

Any unrelated reformat/rewrite or assertion weakening => repair before completion.

## Final diff review

Run:
- `git diff --check`
- complete manual diff review,
- dependency lock delta review.

Stage only intended paths.

Then:
- `git diff --cached --check`
- `git diff --cached --stat`
- `git diff --cached`

No:
- real documents,
- temporary ZIP/PDF/DOCX files,
- extracted content,
- caches,
- DB/WAL files,
- logs,
- JUnit/XML receipts,
- build artifacts,
- credentials.

## Completion commit

Only after every required gate passes:

`feat: complete PHX-072 upload parser routing`

Exactly ONE implementation/completion commit after the preparation ticket commit.

No amend of the preparation commit.

## Push

Normal push only:

`feature/PHX-072-upload-parser-routing`

No PR.
No merge.
No main push.
No force push.
No PHX-073.

Expected completed branch:
- 0 behind main,
- exactly 2 commits ahead:
  1. preparation ticket,
  2. completion commit.

## Live acceptance

No live/user upload is required for deterministic completion.

Report:

`LIVE DOCUMENT PARSER ACCEPTANCE: PENDING — deterministic in-memory fixtures passed; user-owned real documents have not been accepted as live production evidence.`

Image OCR acceptance:

`LIVE IMAGE OCR ACCEPTANCE: NOT IMPLEMENTED — PHX-072 only routes supported image signatures to an explicitly injected parser; no built-in OCR runtime is promoted.`

These are nonblocking.

## Known limitations

Completion evidence must preserve at least:
- pypdf synchronous extraction is not hard-preemptible and cannot guarantee a strict
  process-memory ceiling for maliciously compressed content,
- pypdf is text-layer only; scanned/image-only PDFs need an external OCR adapter,
- DOCX V1 covers top-level paragraph/table text and does not claim complete Word semantic
  fidelity,
- no legacy DOC/macros/spreadsheets/slides/RTF/email/archive parsing,
- image OCR absent by default,
- UTF-8 only for plain text/code/CSV,
- parser output remains untrusted and PHX-032 admission still decides persistence,
- no upload UI/API in PHX-072.

## If blocked

If any required gate fails:
- ticket remains TODO,
- FEATURE_TICKETS PHX-072 remains TODO,
- Phase 7 INCOMPLETE,
- no completion commit,
- no incomplete push,
- no PHX-073.

For an unchanged historical failure not covered by the three explicit repairs:
- reproduce individually,
- STOP BLOCKED,
- report exact evidence,
- do not repair without authority.

## Final report

If PASS, report at minimum:

1. PHX-072 STATUS
2. OFFICIAL LOCAL PATH
3. BRANCH
4. STARTING HEAD
5. EXPECTED / OBSERVED MAIN
6. FINAL COMMIT SHA
7. FILES CREATED
8. FILES MODIFIED
9. AUTHORIZED HISTORICAL TEST FILES
10. DIRECT DEPENDENCIES
11. LOCKED NEW PACKAGES / VERSIONS
12. ROUTED FORMATS
13. EXTENSION ALLOWLIST
14. MEDIA-TYPE RULES
15. BINARY SIGNATURE RULES
16. UPLOAD CONTRACT
17. DOCUMENTSOURCE MAPPING
18. PARSER LIMITS
19. TEXT PARSER
20. MARKDOWN PARSER
21. CODE PARSER
22. CSV PARSER
23. PDF PARSER
24. PDF VERSION / LIMITATIONS
25. DOCX PARSER
26. DOCX VERSION / ZIP PREFLIGHT
27. IMAGE ROUTING
28. ERROR VOCABULARY
29. CANCELLATION / SYNC-LIBRARY LIMITS
30. PHX-032 INTEGRATION
31. ROBIN AUTHORITY
32. VEGAPUNK AUTHORITY
33. PHOENIX AUTHORITY
34. TOOLREGISTRY / PERMISSIONENGINE STATUS
35. MEMORY/RAG PERSISTENCE STATUS
36. EVENTTYPE
37. MIGRATIONS
38. BROWSER HISTORICAL REPAIR EXACT TEST x3
39. BROWSER FULL SUITE x3
40. SCREENSHOT HISTORICAL REPAIR EXACT TEST x3
41. SCREENSHOT FULL SUITE x3
42. PROMPT-INJECTION HISTORICAL REPAIR EXACT TEST x3
43. PROMPT-INJECTION FULL SUITE x3
44. FOCUSED PHX-072 RUN 1
45. FOCUSED PHX-072 RUN 2
46. FOCUSED PHX-072 RUN 3
47. DOCUMENT INGESTION REGRESSION
48. RETRIEVAL/VECTOR/CITATION REGRESSIONS
49. TOOL/SECURITY REGRESSIONS
50. SPOTIFY/YOUTUBE REGRESSIONS
51. PHASE-6 REGRESSIONS
52. FULL PYTHON RUN 1
53. FULL PYTHON RUN 2
54. FULL PYTHON RUN 3
55. RUFF
56. FORMAT
57. MYPY
58. BUILD / PACKAGE CONTENTS
59. FOUNDATION
60. DESKTOP
61. SECURITY
62. INV-001 THROUGH INV-024
63. FINAL DIFF
64. ADR-065
65. DOCUMENTATION
66. FEATURE_TICKETS
67. COMMIT / PUSH
68. AHEAD / BEHIND
69. WORKING TREE
70. MAIN SAFETY
71. LIVE DOCUMENT PARSER ACCEPTANCE
72. LIVE IMAGE OCR ACCEPTANCE
73. KNOWN LIMITATIONS
74. NEXT TICKET: PHX-073 — NOT STARTED

No PR.
No merge.
No main push.
No PHX-073.


## Authoritative blocker resolution — foundation dependency guard / formatting / runner — 2026-10-08

PHX-072 correctly stopped after the DOCX/mypy repairs when three later gates exposed:
1. two formatter-only defects in already-authorized historical test files,
2. a current foundation dependency assertion that has not yet been evolved for PHX-072's
   explicitly authorized direct parser dependencies,
3. an external validation-runner console encoding failure after desktop npm tests passed.

This section is authoritative for resume and does not waive any original gate.

### A. Foundation runtime dependency guard — EXACT scope expansion authorized

File:
`tests/test_bootstrap.py`

Only test:
`test_distribution_has_foundation_runtime_dependencies`

Independent current-main inspection confirms this test asserts the CURRENT installed
Phoenix distribution's direct `Requires-Dist` names. It is not an immutable historical
PHX-000 snapshot.

PHX-072 explicitly promotes exactly two new direct runtime requirements:
- `pypdf>=6.19,<7`
- `python-docx>=1.2,<2`

Therefore evolve only this exact expected direct-dependency set to include:
- `pypdf`
- `python-docx`

Preserve every existing expected direct dependency:
- alembic
- fastapi
- httpx
- playwright
- psutil
- pydantic
- pydantic-settings
- pywinauto
- sqlalchemy
- uvicorn

Do NOT add `lxml` to `Requires-Dist`; it is a transitive dependency of python-docx,
not a Phoenix direct dependency.

Do NOT weaken the assertion into subset containment.
It must continue asserting the exact direct dependency-name set of the installed Phoenix
distribution.

Preserve the exact Python requirement assertion:
- >=3.11
- <3.12

No other `tests/test_bootstrap.py` change is authorized.

Required verification:
- run the exact dependency test THREE consecutive times,
- run the full foundation suite and require all 126 tests pass,
- final diff of `tests/test_bootstrap.py` contains only the two added expected names.

This expands PHX-072's maximum authorized final path scope from 13 to 14 paths.

### B. Formatter defects in already-authorized historical files

Files:
- `tests/test_browser_tool.py`
- `tests/test_prompt_injection_defenses.py`

These paths were already authorized for narrow historical dependency-guard evolution.

The full Ruff format check reports only these two files need formatting.

Authorized repair:
- apply Ruff-compatible formatting only to these two files,
- no semantic change,
- no additional assertion removal/weakening,
- no production change,
- inspect each diff after formatting and require changes are limited to the already
  authorized guard edits plus formatter-only layout.

`tests/test_screenshot_vision_loop.py` is not authorized for additional semantic change;
leave it untouched unless Ruff itself reports it and a new clarification is obtained.

After formatting:
- targeted Ruff format check for the two files must pass,
- full `ruff format --check .` must pass before completion.

### C. Desktop validation-runner cp1252 failure — NO repository repair authorized

Observed behavior:
- `npm test` itself completed successfully with 4 tests passed,
- an external validation runner then raised Python `UnicodeEncodeError` under cp1252
  while printing/capturing command output,
- desktop typecheck/build therefore did not run.

This is validation-harness behavior, not Phoenix desktop code.

Do NOT modify repository code/tests/configuration to accommodate the runner encoding.

For final desktop evidence, run the required commands directly in the user's terminal,
outside the failing wrapper:
- `npm test`
- `npm run typecheck --workspace @phoenix/desktop`
- `npm run build --workspace @phoenix/desktop`

If receipts are captured by Python tooling, configure that external process/console for
UTF-8 or redirect output to an external UTF-8 file. Such validation-environment changes
must remain outside the repository.

Required final evidence:
- npm test: 4 passed,
- typecheck: PASS,
- build: PASS,
- desktop source unchanged.

A repeated failure of an external receipt/printing wrapper after the underlying commands
pass is not a Phoenix code failure and does not require repository modification.

### D. Resume rules

Preserve all successful local PHX-072 work, including:
- explicit `os` / `os.sep` mypy repair,
- `ZipInfo.orig_filename` archive-member validation,
- raw backslash ZIP fixture,
- focused 336-test suite work,
- exact dependency delta:
  - pypdf 6.19.0
  - python-docx 1.2.0
  - lxml 6.1.3.

No other historical scope expansion is authorized.

After synchronizing this clarification, resume all outstanding original PHX-072 gates.
Previously completed gate receipts may inform diagnosis, but final completion must satisfy
the ticket's required deterministic verification sequence, including full Python x3,
full mypy, package verification, direct desktop commands, security/invariants and final
diff review.

If another unchanged historical test outside the four now-authorized historical files
fails, reproduce it individually and STOP BLOCKED.

The four authorized historical test paths are now:
- `tests/test_browser_tool.py`
- `tests/test_screenshot_vision_loop.py`
- `tests/test_prompt_injection_defenses.py`
- `tests/test_bootstrap.py`

Expected maximum final scope is now 14 paths.

Because this clarification is an additional preparation commit, successful PHX-072 branch
ancestry relative to main is now expected to be exactly THREE commits ahead:
1. `docs: add PHX-072 upload parser routing ticket`
2. this blocker-resolution commit
3. `feat: complete PHX-072 upload parser routing`

No PR, merge, main push, force push or PHX-073 work.
