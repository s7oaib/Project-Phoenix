# PHX-017 — Ollama thinking control

## Status
DONE

## Phase
1 — Model Runtime (follow-up found in Phase 9 / PHX-091)

## Objective

Let PHOENIX explicitly turn model "thinking" (separate reasoning output) on or
off when it calls Ollama, so thinking-by-default models such as the MASTER_SPEC
Qwen3.5 tiers return their answer within bounded output budgets.

## Problem evidence

Measured 2026-10-15 on BLAZE-6, Ollama 0.35.1 (PHX-091, ADR-079):

- `qwen3.5:2b/4b/9b` report the Ollama capability `thinking` and think by
  default. `/api/chat` returns the reasoning in `message.thinking` and the answer
  in `message.content`.
- `OllamaProvider` sends no `think` field (ADR-024 deliberately excluded it). With
  64–128-token `num_predict` budgets the whole budget goes to reasoning: content
  `''`, `done_reason` `length`, 0% accuracy for the 2B tier.
- Direct API on `qwen3.5:2b`: default → empty after 128 tokens (5.3 s);
  `think:false` → `PHOENIX_READY` in 0.3 s; `/no_think` prompt suffix has no
  effect; `num_predict` 2048 → correct after ~200 reasoning tokens (8.3 s).
- Direct API on non-thinking `qwen2.5:1.5b` (capabilities `completion`,
  `tools`): `think:false` → HTTP 200, normal answer; field absent → HTTP 200;
  `think:true` → HTTP 400 `"qwen2.5:1.5b" does not support thinking`.

## Authoritative documents
- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `docs/05_MODEL_STRATEGY.md`
- `docs/25_DEPENDENCIES.md`
- `tickets/PHX-010-model-provider-interface.md`
- `tickets/PHX-011-ollama-provider.md`
- `DECISIONS.md` ADR-023, ADR-024, ADR-029, ADR-079

## Dependencies
- PHX-010, PHX-011 — DONE
- PHX-091 — in progress; consumes this ticket for its thinking-off baseline row

## Design (owner-confirmed 2026-10-15)

1. **Where the control lives — provider construction option.**
   `OllamaProvider(..., think: bool | None = None)`. The PHX-010
   `GenerationRequest` contract is unchanged, so Colibri, the PHX-016 benchmark
   runner, document generation and all fake providers are untouched. A caller
   that needs both modes holds two provider instances. A neutral per-request
   field is deferred until a router/agent ticket needs per-call control.
2. **Default — `None`, meaning send no `think` field.** Today's wire payload and
   behaviour stay byte-for-byte identical unless a caller opts in. `False` sends
   `"think": false`; `True` sends `"think": true`. Strict `bool`/`None` only;
   other values are rejected at construction with `ValueError`.
3. **Thinking text is never surfaced.** The adapter keeps discarding
   `message.thinking` (generate and stream). It never enters
   `GenerationResponse`/`GenerationChunk`, logs, errors, memory or benchmark
   reports. No reasoning token count is inferred.
4. **Non-thinking models.** `think=False` is sent as-is (Ollama accepts it).
   `think=True` on a model without thinking fails explicitly as
   `ModelProviderError` from Ollama's HTTP 400; no silent retry without `think`,
   no fallback. `capabilities()` and `ModelCapabilities` are unchanged.
5. **Empty content.** A completed record with empty content stays an honest
   response: `text=''` with Ollama's `finish_reason` (for example `length`). It
   is not converted to success text or to an error. Tests pin this.

## Owner decisions

Confirmed 2026-10-15: ticket id PHX-017 and every recommendation above
(construction option, default `None`, reasoning text never surfaced, explicit
failure for `think=True` on non-thinking models, honest empty content, and the
narrow one-path guard repair, later refined to a re-pin). Recorded as ADR-080.

Note: this session's three short live probes at about 01:25–01:27 IST ran
during the PHX-091 baseline run (`PHX-091-fyp-baseline-20261015-011750`) and
may have inflated latency in trials from that window.

## Requirements

- Change only `phoenix/models/ollama.py`: constructor option, validation, and
  `_payload` adds top-level `think` when the option is not `None` (both
  `generate` and `stream`).
- No change to `phoenix/models/provider.py`, `colibri.py`, `benchmark.py`,
  registry, lifecycle, embeddings or vision adapters.
- No new dependency.
- ADR-024 is amended by ADR-080 (ADR-079 is reserved by PHX-091).

### Historical guards — narrow authorized repair

Two existing guards freeze `phoenix/models/ollama.py` against `origin/main` and
would reject this ticket on its branch and in PR CI:

- `tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary`
- `tests/test_screenshot_vision_loop.py` (explicit `git diff origin/main` path list)

Owner decision: re-pin, in a separate commit. Both guards stop comparing
`phoenix/models/ollama.py` with `origin/main` and instead require it to equal
the blob from the PHX-017 implementation commit (full SHA). Every other
protected path keeps exact `origin/main` equality. Precedent: PR #61 (commit
52ec305) re-pinned the PHX-088 files in `tests/workspace.test.mjs`.

## Tests (deterministic, `httpx.MockTransport`)

1. default provider sends no `think` key (generate and stream); existing exact
   payload tests unchanged
2. `think=False` sends top-level `"think": false` (generate and stream), outside
   `options`
3. `think=True` sends top-level `"think": true` (generate and stream)
4. invalid `think` values (`1`, `"false"`, `0`) raise `ValueError` at construction
5. non-thinking model: `think=True` + mocked HTTP 400 → `ModelProviderError`,
   no retry (exactly one request)
6. non-thinking model: `think=False` + normal record → normal response
7. record with `message.thinking` text: thinking text absent from response text,
   `model_dump`, chunk text and error messages (generate and stream)
8. completed record with empty content and `done_reason: length` → `text==''`,
   `finish_reason=='length'`, no error
9. stream of thinking-only chunks then content chunks: thinking never emitted,
   order and single final chunk preserved
10. construction with `think` performs no I/O (existing isolation test style)
11. guards still protect every other path

New tests go in the existing `tests/test_ollama_provider.py`, so
`tests/matrix.toml` needs no new mapping.

## Live check (after deterministic tests, no PHX-091 run active)

One bounded non-streaming and one streaming request to the installed
`qwen3.5:2b` with `think=False`, temperature 0, `num_predict` 64: expect the
fixed answer, `finish_reason` `stop`. One request to `qwen2.5:1.5b` with
`think=False` to confirm non-thinking acceptance. No pulls. Results are
reported separately from deterministic correctness.

## Non-goals
- no change to the PHX-010 neutral contract
- no thinking control for Colibri or other providers
- no routing/agent policy deciding when thinking is on
- no exposure of reasoning text, reasoning token counts or "thinking"
  capability metadata
- no PHX-091 re-run (owned by PHX-091 after this lands)
- no model pulls or lifecycle changes

## Expected files/components
- `phoenix/models/ollama.py`
- `tests/test_ollama_provider.py`
- `tests/test_prompt_injection_defenses.py` (one-path re-pin only)
- `tests/test_screenshot_vision_loop.py` (one-path re-pin only)
- `DECISIONS.md` (new ADR)
- `FEATURE_TICKETS.md`
- this ticket

## Verification
- `uv run --locked pytest tests/test_ollama_provider.py`
- the two guard tests above
- `python scripts/test_matrix.py` (all PHX-090 gates)
- `git diff --check`
- bounded live check above

## Acceptance criteria
- `OllamaProvider` can disable thinking; default behaviour unchanged
- tests cover `think` true/false/absent payloads, non-thinking models, hidden
  thinking text and empty-content handling
- live `qwen3.5:2b` with `think=False` returns the answer
- all matrix gates pass
- invariants and security reviewed; ADR recorded; ticket and index updated

## Completion evidence
Report changed files, commands/tests, results, decisions, assumptions, and known limitations.

### Verified completion — 2026-10-15

- Commits: `b7d9651` (implementation, ticket, ADR-080, index), `26dfdc7`
  (merge of main 64d3cda; ADR-079/ADR-080 ordering conflict resolved),
  `ad7ed2d` (guard re-pin), then this evidence commit (docs only).
- Changed: `phoenix/models/ollama.py` (`think` construction option, strict
  bool/None validation, top-level `think` only when not `None`),
  `tests/test_ollama_provider.py` (+20 cases, 128 total), the two guard tests
  (one-path re-pin to `b7d96518d58ee7466d441cbe3b17c4eaad0866cf`),
  `DECISIONS.md` (ADR-080 with re-pin amendment), `FEATURE_TICKETS.md`, this
  ticket. No new test file, so `tests/matrix.toml` is unchanged. No dependency,
  contract, schema, event or frontend change.
- `python scripts/test_matrix.py` at `ad7ed2d`, clean tree: PASS, 13/13 gates
  (uv-sync, ruff check/format, mypy, mypy-scripts, pytest 15769 passed / 0
  failed / 16 skipped, npm test 181/181, typecheck, frontend build, tauri:info,
  cargo check, cargo test, uv build).
- Guards: both pass. A probe edit to `ollama.py` fails both, so the re-pin keeps
  protection; every other protected path still compares with `origin/main`.
- Live check (Ollama 0.35.1, AC power, nothing else running on the GPU,
  2026-10-15T06:31Z, temperature 0, `num_predict` 64, fixed harmless prompt):
  `qwen3.5:2b` `think=False` generate → `PHOENIX_READY`, stop, 6 output tokens;
  stream → `PHOENIX_READY`, stop, one final chunk; default (`None`) → `''`,
  length, 64 output tokens (unchanged behaviour); `qwen2.5:1.5b` `think=False` →
  `PHOENIX_READY`, stop; `think=True` → `ModelProviderError` (HTTP 400). Ollama
  had stopped after the overnight shutdown; it was restarted through its normal
  tray app. No pulls.
- Invariants: INV-012 kept (no provider detail in the neutral contract or agent
  definitions); INV-021 kept (unsupported thinking and empty content stay
  explicit); INV-023 kept (live checks ran one at a time, with no other GPU
  work). Reasoning text never leaves the adapter. Model output still grants no
  authority.
- Limitations: thinking control is per provider instance, not per request; it
  applies to Ollama only. gpt-oss style thinking levels are not supported. The
  early live probes at about 01:25 IST overlapped the stopped PHX-091 run (see
  its NOTE.md).
