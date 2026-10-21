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

## In missions (PHX-110, ADR-130, ADR-131, ADR-134)

- Uploads (`POST /files/upload`) are parsed, chunked (1,000 characters, 150
  overlapping), embedded with `embeddinggemma:300m` and stored in the
  `SqliteVectorStore`; a failure leaves nothing indexed. Every embedding holds
  the heavy model lease (INV-023).
- A planned step assigned to `knowledge_rag` runs one PHOENIX-built retrieval:
  the query is the step objective, the scope every upload plus the `user`
  memories, and `knowledge.retrieve` is a P0 decision by PermissionEngine. The
  domain is offered only while something is indexed.
- Up to 6 sources (6,000 characters, cosine floor 0.35) reach the model as
  numbered guarded items `[n]`; the answer cites them. With no source the step
  says `No source answers this step.`
- LEVI checks deterministically that the citations name issued sources and
  that each cited sentence is grounded in its source (shared terms; numbers
  taken from the sources), then the `knowledge_verifier` model judges support.
  A final answer's markers must name sources a verified step cited.
- The trace records retrievals and the cited sources' provenance; the API
  returns them as `sources`. Source text never enters traces, events or logs.
