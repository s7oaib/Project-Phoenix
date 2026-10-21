# 10 — Memory System

Owner: **PUDDING**.

## Memory classes

- Working — current session/task state
- Episodic — events/experiences
- Semantic — durable facts
- User — preferences
- Project — decisions/context
- Agent — bounded specialist learnings

## Suggested record fields

id, type, content, source_type, source_ref, scope, confidence, importance, created_at, updated_at, last_accessed_at, expires_at, embedding_ref, metadata.

## Rules

- Not every chat turn becomes long-term memory.
- Secrets do not enter normal memory.
- Retrieval considers relevance, scope, recency, and importance.
- Contradictions are not silently overwritten.
- User can inspect and delete persisted memory.
- Authoritative project documents beat inferred memory.

## Write policy (PHX-110, ADR-132)

- Only the user writes memory, through `POST /memory`; missions and models
  never do, so no chat turn becomes memory automatically.
- A memory is one note of at most 2,000 characters, typed `user` (default),
  `project` or `semantic`, in the single `user` scope (`source_type`
  `user_note`, confidence 1.0, importance 0.5, no expiry); at most 2,000.
- Credential-shaped text is refused (`secret_rejected`) before it is embedded
  or stored; the same rule is PUDDING's admission policy (INV-018).
- Every memory is listed (`GET /memory`), searchable (`GET /memory/search`)
  and deletable with its vector (`DELETE /memory/{id}`).
- Missions read memories only through knowledge steps (docs/11), as untrusted
  guarded data, and cite them like documents.
