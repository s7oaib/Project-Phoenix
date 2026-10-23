# PHX-140 — Smarter chat

## Status
DONE (local only, not pushed; tests written, not run at the owner's request;
ruff and mypy clean on changed files)

## Objective
Follow-ups work (conversation memory), saved memories inform chat replies,
and current/online questions use web research by default.

## Authoritative documents
- `/AGENTS.md`, `/RULES.md`, `/INVARIANTS.md`
- `DECISIONS.md` ADR-264..268

## Requirements
- DIRECT/KAIDO replies see the last ~6 turns (3,000 chars, newest kept).
- Top 3 saved memories (PHX-110 search, similarity floor) for DIRECT/KAIDO.
- "play another one"/"next song"; "summarise that video" uses the last played
  video of the conversation.
- Web research on by default (`PHOENIX_WEB_RESEARCH_ENABLED=0` opts out); a
  deterministic research rule routes current/online questions COMPLEX;
  searches still ask permission (P1, Always allow).

## Model-visible input change (owner approved feature 2026-10-23)
- History: one guarded item via `_guarded` (origin MODEL_OUTPUT, content id
  `conversation`, source `conversation`, field `recent_turns`), lines
  labelled `User: ` / `PHOENIX: `.
- Memories: up to 3 guarded items via `_guarded` (origin EXTERNAL_TEXT,
  content ids `memory1`..`memory3`, source `memory`, field `saved_memory`).
- The existing guard preamble wraps the request; no new sentences, no prompt
  text changed.
- Research rule plus web research default-on with opt-out (routing only).

## Changed protected sources (pinned at the PHX-140 commit)
- `phoenix/agents/runtime.py`, `phoenix/agents/triage.py`,
  `phoenix/api/missions.py`, `phoenix/api/server.py`, `phoenix/core/config.py`.

## Limitations
- Research-rule missions are recorded as triage `decided` (the DB allows
  `rule` only with DIRECT).

## Acceptance
- `tests/test_smarter_chat.py`; `tests/test_config.py` (default on, opt-out).
