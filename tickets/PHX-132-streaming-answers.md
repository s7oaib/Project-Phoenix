# PHX-132 — Streaming answers

## Status
DONE (implemented on `feature/PHX-132-streaming`; local only, not pushed;
owner asked for no test runs in this round)

## Objective
DIRECT chat answers appear word by word in the desktop Chat, and voice starts
speaking after the first sentence. Planned/COMPLEX missions and tool output
stay as before, so LEVI and verification semantics are untouched.

## Decisions
ADR-230 to ADR-234.

## Changes
- `phoenix/agents/answer_stream.py` (new): `StreamingProvider`, `PartialAnswers`.
- `phoenix/agents/runtime.py`: `partial_answers`; the DIRECT reply's first
  attempt streams and publishes deltas.
- `phoenix/agents/mission_events.py`: the answer delta on `agent.started`.
- `phoenix/api/observability.py`: deltas are never logged.
- `phoenix/api/server.py`: one `PartialAnswers` for the runtime and voice.
- `phoenix/voice/conversation/__init__.py`, `phoenix/voice/engines/pipeline.py`,
  `phoenix/api/voice.py`: the early first sentence.
- `apps/desktop/src/adapters/core.ts`: the live answer in Chat.

## Tests
`tests/test_answer_stream.py`, `tests/desktop_adapters.mjs` (PHX-132 case).
Pins: the changed paths move to `PHX_132_COMMIT` in
`tests/test_prompt_injection_defenses.py` (two-commit pattern).

## Limitations
KAIDO's degraded reply does not stream. If the first DIRECT attempt is
retried, its streamed text shows until the final answer replaces it, and a
sentence spoken early may come from that attempt.
