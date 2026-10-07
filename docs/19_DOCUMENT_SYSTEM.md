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
Upload UI/API and generation/export remain later integration/PHX-073 work.
Deterministic synthetic fixtures are not live user-document acceptance evidence.
