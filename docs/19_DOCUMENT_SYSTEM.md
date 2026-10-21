# 19 — Document System

Owners:
- **ROBIN** for analysis/generation
- **VEGAPUNK** for indexing/RAG

## Inputs

PDF, DOCX, TXT, Markdown, CSV, images, code.

## Outputs

PDF, DOCX, Markdown, CSV, structured reports.

## Requirements

- Preserve provenance.
- Keep generated artifacts separate from originals.
- File writes follow permission policy.
- Parser/generator failures are explicit.
- Professional output layouts are supported.
- Verify generated file exists and is readable before reporting success.


## Implemented upload routing — PHX-072

`phoenix.documents.parsers` accepts exact caller-supplied bytes through frozen,
extra-forbidden `UploadDocument` and explicit `UploadParserLimits`. Filename is
display/provenance only: 1..255 valid Unicode characters, no controls, separators,
colon, surrounding whitespace or unsupported final extension. Routing lowercases
only the extension; MIME evidence cannot override it. Binary routes require their
ticketed signature/package evidence. Unknown types fail without guessing/fallback.

Use `UploadRouter(limits).resolve(upload)`, then explicitly call the unchanged
PHX-032 ingestion service with `source=route.source, parser=route.parser`. The route
is an immutable runtime object, not serialized authority. Existing DocumentSource
receives the exact ID/bytes, `source_type="upload"`, opaque `upload:<id>` reference,
canonical MIME, aware UTC timestamp and exactly filename/upload_format metadata.
PHX-032 revalidates parsed evidence and requires its admission policy before
chunking/deduplication/persistence; router limits never rewrite ingestion limits.
ROBIN (`document_analyst`) owns analysis/generation, VEGAPUNK (`knowledge_rag`) owns
indexing/RAG, and PHOENIX remains supreme. Parsing does not invoke any of them.

TXT (.txt), Markdown (.md/.markdown) and the ticket's exact 30 code extensions use
strict UTF-8/UTF-8-SIG, remove only the BOM and return one meaningful exact text
segment. NUL/invalid encoding fails. Markdown/HTML/code stays inert: no rendering,
imports, semantic loading, instructions or execution. CSV uses strict stdlib
csv.reader, without sniffing or formula evaluation; every logical row becomes an
exact compact Unicode JSON-array segment with row number provenance, including
empty rows/cells. Its temporary native field bound is restored synchronously.

PDF uses pypdf 6.19.0 on BytesIO, strict reader construction, encrypted-document
rejection and ordered text-layer extraction. Meaningful pages retain their original
1-based page numbers; all-empty/scanned PDFs fail no_extractable_text. Source/page/
content-stream/retained text/segment bounds reduce risk, with public native decoder
bounds and external JBIG2 decoder disabled during synchronous calls. Diagnostics
are redacted before handlers and logging/configuration restored on exit. pypdf is
not OCR; decompression/extraction cannot guarantee a hard process-memory ceiling.

DOCX uses python-docx 1.2.0 on BytesIO after ZIP preflight. Original ZipInfo spelling
is checked before normalized names: backslash, absolute, drive/colon, traversal,
NUL/control or changed spelling fails. Unsafe normalized aliases cannot satisfy
required package members. Preserve unique names, required Content_Types/document
members, no encryption, entry/per-member/total-uncompressed/compression-ratio bounds.
Bounded XML/relationship inspection rejects DTD/entities without external resolution.
Nothing is extracted or written to disk. Document.iter_inner_content preserves
top-level paragraph/table order; meaningful paragraphs and tab-separated table rows
retain deterministic paragraph/table/row sections. Headers/footers, footnotes,
comments, text boxes and embedded objects/images are outside V1; full Word semantic
fidelity and nested-table semantics are not claimed. Legacy DOC/macros are unsupported.

PNG/JPEG/WebP require extension, signature and compatible MIME. Only an explicitly
injected borrowed DocumentParser can handle image bytes; otherwise parser_unavailable.
No automatic OCR/vision runtime, Pillow or Tesseract is promoted. PHX-032 still
revalidates injected parsed evidence and document-ID correlation.

All twelve limits are required strict positive non-boolean integers: source bytes,
retained text/segments, PDF pages/content-stream bytes, DOCX entries/member bytes/
total bytes/compression ratio, CSV rows/columns/cell characters. DOCX member bound
must not exceed total. Safe closed errors contain no source/private diagnostics.
Cancellation stays in the caller task, checked around synchronous library calls
and between pages/blocks/rows; CancelledError propagates. Synchronous calls cannot
be interrupted while executing. Built-in parsers bind to their first event loop;
PHX-032 retains ownership of active ingestion IDs and observable cancellation state.

No filename/source_ref opening, temporary extraction, URL/network, subprocess,
hidden worker, model/prompt/memory promotion, tool/permission duplication or direct
persistence. Future privileged source reads remain existing permission/tool work.
Upload UI/API and disk export remain later integration work; PHX-073 generation is below.
Deterministic synthetic fixtures are not live user-document acceptance evidence.

## Implemented in-memory report generation — PHX-073

`phoenix.documents.generation` consumes strict frozen `ReportDocument` and
`DocumentGenerationRequest`, with ten explicit positive non-boolean limits.
Only paragraph, bullet_list and table blocks are allowed, in ordered sections,
with optional subtitle/author and ordered plain Sources. Text is untrusted;
there are no raw markup/style/metadata/path/callback fields. Exact revalidation
rejects forged copies, subclasses, extras and coercion. Display filenames never
open or save a path. Total characters count every supplied text field once;
section/block/list/table/cell/provenance limits never silently truncate content.

`DocumentGenerator(limits).generate(request)` returns exact revalidated
`GeneratedArtifact`: correlated IDs, format, display filename, canonical MIME,
SHA-256, exact byte size, verified flag and bytes; only PDF has a page count.
Bytes are excluded from repr/ordinary serialization. Success means an artifact
was structurally verified in memory. There is no saved/path/file-exists field.

PDF uses ReportLab 5.0.1 Platypus, Base-14 Helvetica styles, margins, wrapping,
bullets, tables and numbered footers. Rendered text must encode as cp1252 or
fail unsupported_character; DOCX/Markdown retain broader Unicode. Paragraph
text is XML-escaped before code-owned line breaks. The private constant-message
page-limit Exception is caught by type outside native build and maps explicitly
to limit_exceeded; other native failures map safely to generation_failed.
Strict pypdf 6.19.0 verification checks exact signature/bytes, no encryption,
bounded positive page count, page accessibility and extracted title evidence.
PDF/A, accessibility tags, embedded fonts and exact viewer typography are not claimed.

DOCX reuses python-docx 1.2.0 with a minimal code-owned OOXML BytesIO seed,
avoiding the default disk template. Plain text APIs create Title/Subtitle/
Heading 1/List Bullet/Table Grid content and Sources; no images, macros,
hyperlinks, external relationships or arbitrary user XML. Save uses ordinary
BytesIO until native ZIP serialization/cleanup finishes. Then check output size,
canonicalize member order/timestamps in another ordinary buffer, and check final
size before verification/success. The output limit is an artifact-acceptance bound,
not a streaming-memory ceiling. No mid-ZIP throwing writer or warning suppression.
Verification checks ZIP signature, unique unencrypted required members, reopens
exact bytes, checks exact title and traverses paragraphs/tables.

Markdown uses stdlib UTF-8, LF/final newline and code-owned headings, bullets,
pipe tables and Sources. Backslash/punctuation/angles/pipes are escaped; user
newlines become inert literal backslash-n. Escaped HTML-looking text such as
`\<img` and `\<link` remains literal; tests check escape parity, active syntax,
and unchanged code-owned structure. Verification checks strict UTF-8, no NUL,
exact escaped title heading, final newline and exact byte round-trip.

The nine closed failure codes are invalid_input, unsupported_character,
limit_exceeded, generation_failed, verification_failed, cancelled, busy, closed
and wrong_event_loop. Errors retain no private/native diagnostics. CancelledError
propagates. One loop and one active_generation_id belong to the caller task;
same-loop aclose is idempotent. Checkpoints surround native calls and controlled
section/block/item/row/page iterations, verification and success. No hidden work;
synchronous native calls cannot be hard-preempted or promise hard memory bounds.

ROBIN/document_analyst, VEGAPUNK/knowledge_rag and supreme PHOENIX retain authority.
There is no filesystem/temp/network/model/tool/permission/memory/RAG/DB operation,
API/frontend integration or automatic fallback. Provenance is never dereferenced.
Disk persistence remains separate permission-gated integration; only that later
integration can verify a saved file exists/is readable. See ADR-066.

LIVE DOCUMENT GENERATION ACCEPTANCE: PENDING — deterministic in-memory PDF/DOCX/Markdown generation and structural verification passed; no user-owned production document has been exported.

LIVE DISK EXPORT ACCEPTANCE: NOT IMPLEMENTED — PHX-073 does not write artifact bytes to disk; binary persistence remains a separate permission-gated integration.

## Implemented workspace document steps — PHX-113

`phoenix.tools.document_files.DocumentFileTool` brings PHX-072 parsing and
PHX-073 generation to the workspace behind PermissionEngine, reusing
FilesystemTool's path, link/junction and per-call root checks.

- `document.read` (P1): one `.pdf/.docx/.md/.markdown/.csv` file (≤ 4 MiB),
  parsed to text (≤ 20,000 characters, never truncated; PDF pages keep
  `[page N]`). The text is untrusted: in a mission it reaches models only as the
  guarded `tool_output` item, and LEVI's grounded check uses the same item.
- `document.create` (P2): one new Markdown/DOCX (title + plain sections, via the
  generator) or CSV (equal-width rows; formula-like cells prefixed with `'`)
  file, created exclusively in an existing folder. It never overwrites
  (`already_exists`), then reads the file back and compares bytes before
  reporting `verified: true` — the saved-file check PHX-073 deferred.

Missions reach both through the opt-in PHX-113 capability steps with caller
grants only (P2 SESSION only); see
[the PHX-113 contract](../tickets/PHX-113-media-documents.md) and ADR-140–144.

LIVE DISK EXPORT ACCEPTANCE: IMPLEMENTED (Markdown/DOCX/CSV, create-only) — verified on temporary workspaces in tests; live missions await the owner's prompt approval and wiring.
