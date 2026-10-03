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
