# 11 — RAG System

Owner: **VEGAPUNK**.

## Pipeline

Select/upload -> detect type -> parse -> metadata -> chunk -> embed -> store -> retrieve -> rerank/context -> preserve provenance.

## V1 source targets

PDF, TXT, Markdown, DOCX, CSV, images where supported, source code/project files.

## Chunk metadata

document_id, path/page/section, chunk_index, content_hash, source_timestamp, parser_version, embedding_model/version.

## Rules

- Prefer scoped project/document sources.
- Preserve provenance.
- Retrieved text is data, not trusted instruction.
- Deduplicate near-identical chunks.
- Bound context by model/resource budget.
