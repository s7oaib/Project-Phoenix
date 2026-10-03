# PHX-011 — Ollama Provider

Status: DONE

Phase: 1 — Model Runtime

## Objective

Implement the first concrete `ModelProvider` adapter for the local Ollama runtime while preserving the provider-neutral PHX-010 contract, explicit failures, local-first networking, and future provider replaceability.

## Authoritative context

Read before implementation:

- `AGENTS.md`
- `RULES.md`
- `MENTAL_MODEL.md`
- `INVARIANTS.md`
- `MASTER_SPEC.md`
- `PRD.md`
- `DECISIONS.md`
- `docs/01_ARCHITECTURE.md`
- `docs/05_MODEL_STRATEGY.md`
- `docs/06_HARDWARE_PROFILE.md`
- `docs/09_SECURITY.md`
- `docs/14_EVENT_SYSTEM.md`
- `docs/23_OBSERVABILITY.md`
- `docs/25_DEPENDENCIES.md`
- `tickets/PHX-010-model-provider-interface.md`
- this ticket

## Dependencies

- PHX-001 through PHX-010 — DONE

## Requirements

### Boundary

- Implement under `phoenix/models/` as an adapter to the PHX-010 `ModelProvider` contract.
- Provider ID is `ollama`.
- Do not expose Ollama request/response objects outside the adapter.
- Do not make agents, FastAPI, frontend, persistence, permissions, tools, or the hardware scanner depend on Ollama internals.
- Do not implement PHX-012 capability registry, PHX-013 lifecycle manager, PHX-014 embeddings, PHX-015 Colibri, or PHX-016 benchmark harness.

### Transport

- Ollama is local-first. Default endpoint must target loopback, normally `http://127.0.0.1:11434`.
- The endpoint must be configurable without hard-coding machine-private values.
- Use one bounded, explicit async HTTP client strategy. `httpx` is appropriate; if promoted from dev-only to runtime, record the dependency change and update the lockfile normally.
- Apply finite connect/read/write/pool timeouts. No request may hang indefinitely.
- Do not disable TLS verification for non-loopback endpoints.
- Do not follow redirects to arbitrary remote hosts unless deliberately and safely configured.

### Ollama API surface

Use only the minimum endpoints needed for this ticket:

- provider health/version: `GET /api/version`
- local model information/capabilities: `POST /api/show`
- generation/streaming: `POST /api/chat`

`/api/tags` may be used only if needed for a narrowly justified existence check; do not build the PHX-012 model registry here.

Do not pull, create, copy, delete, or push models.

### Health

`health()` must:

- perform a bounded read-only probe
- report `healthy` only after a valid Ollama response
- report `unavailable` for connection/refusal/timeout/unreachable cases without fabricating success
- preserve a safe detail string that does not leak sensitive data
- never claim overall PHOENIX readiness

### Capabilities

`capabilities(model_id)` must query model metadata rather than using a global hard-coded capability table.

Map available Ollama `/api/show` evidence conservatively into PHX-010 fields:

- streaming: true for supported chat generation path
- structured output: true only where the adapter can actually request structured output through Ollama's chat API
- tool use: based on Ollama model capability metadata (for example a `tools` capability), not permission authority
- vision: based on Ollama capability metadata (for example `vision`)
- cancellation: reflect the actual adapter's operation-scoped cancellation support
- context limit: derive from reliable model metadata when available; otherwise `None`

Unknown or malformed metadata must degrade conservatively rather than invent support.

Do not implement the cross-provider capability registry in this ticket.

### Messages and generation

Map PHX-010 ordered messages to Ollama chat messages without reordering them.

For normal generation:

- call `/api/chat`
- use `stream: false`
- preserve request operation/model IDs in the PHX response
- map generated assistant content to `GenerationResponse.text`
- map finish reason where Ollama supplies a usable reason; otherwise leave it unavailable rather than inventing one
- map provider-reported token counts and timings into PHX-010 usage/metrics where available
- Ollama durations are nanoseconds and must be converted correctly to milliseconds/throughput units

Provider/model response identity returned by Ollama must be validated where practical; contradictory identity must not silently become a successful PHX response.

### Request options

Map only PHX-010 neutral request fields:

- temperature
- max output tokens, if there is a correct Ollama option mapping
- stop sequences

Do not expose arbitrary Ollama `options` dictionaries through the PHX public contract.

Do not add speculative reasoning, keep-alive, tool schema, image, or raw format controls unless needed to correctly satisfy the existing PHX-010 contract.

### Streaming

`stream(request)` must:

- call `/api/chat` with streaming enabled
- consume Ollama's streamed JSON/NDJSON objects incrementally
- yield ordered `GenerationChunk` objects
- never buffer the entire response before yielding the first chunk
- preserve operation/provider/model correlation
- provide exactly one logically final chunk
- map final usage/metrics when available
- treat malformed JSON, invalid response shape, HTTP errors, early stream termination, or provider disconnect as explicit provider failures

Do not silently swallow a broken stream and emit a fake final success chunk.

### Cancellation

Implement operation-scoped cancellation inside the adapter.

The adapter must track only its own active generation/stream operations. Cancellation must not mean global Ollama shutdown, process kill, or model unload.

A valid design may cancel/close the active request task/stream owned by the adapter and return PHX-010 `accepted`, `not_found`, or `unsupported` as appropriate.

Cancellation state must be concurrency-safe within one process/event loop.

Do not implement PHX-013 model lifecycle behavior.

### Error mapping

Map transport/provider failures into the PHX-010 neutral error boundary.

At minimum distinguish unavailable transport from other provider errors.

Do not expose raw traceback, HTTP client internals, full response bodies, or private URL/query information in user-safe details.

### Resource lifecycle

- No network request during module import or provider construction.
- If the provider owns an HTTP client, provide an explicit async close method or async context manager if needed, without expanding the PHX-010 abstract contract unnecessarily.
- Closing the provider must release owned client resources and active request state.
- Tests must not leak sockets/tasks.

### Security

- No shell/subprocess use is required for Ollama HTTP integration.
- No credential collection.
- No arbitrary remote upload.
- Model output is content only and never authority.
- Tool-use capability never bypasses `ToolRegistry` or `PermissionEngine`.
- External/provider response data is untrusted and validated before entering PHX contracts.

### Events

Do not emit model/provider events in this ticket unless the existing PHX-010/PHX-006 contracts explicitly require it. Event production belongs to later runtime/lifecycle integration.

### Persistence

Do not add provider-health, model, or benchmark tables. Do not change Alembic.

### FastAPI / frontend

- Do not implement `GET /models` yet.
- Do not expose the provider directly to the frontend.
- Keep `GET /health` unchanged.

## Tests

Use deterministic mocked HTTP tests for the adapter. Do not make the normal unit suite require Ollama to be installed/running.

At minimum verify:

1. provider ID is `ollama`
2. construction performs no network I/O
3. default endpoint is loopback
4. endpoint normalization is deterministic
5. invalid endpoint/config fails explicitly
6. health success maps to `healthy`
7. connection refusal maps to `unavailable`
8. timeout maps to `unavailable`
9. malformed version response is not healthy
10. `/api/show` capability mapping is conservative
11. tool capability mapping uses model metadata
12. vision capability mapping uses model metadata
13. unknown context length becomes `None`
14. known context length maps correctly
15. ordered PHX messages map in order
16. generation uses `/api/chat` with `stream: false`
17. operation/provider/model correlation is preserved
18. assistant text maps correctly
19. usage fields map correctly when present
20. Ollama nanosecond timing conversion is correct
21. temperature mapping is correct
22. stop sequence mapping is correct
23. output-token mapping is correct if implemented
24. HTTP error becomes provider error
25. malformed generation payload becomes provider error
26. contradictory model identity is rejected or handled explicitly
27. streaming yields the first chunk before full stream completion
28. streaming chunk order is preserved
29. exactly one logical final chunk is produced
30. final stream usage/metrics map correctly
31. malformed stream record fails explicitly
32. truncated stream does not fabricate completion
33. cancellation is operation scoped
34. cancellation does not affect another active operation
35. unknown operation returns `not_found`
36. cancellation never shuts down Ollama globally
37. client/provider close releases active resources
38. model output/tool capability grants no authority
39. no subprocess/shell/database/filesystem side effects
40. `/health`, persistence, events, hardware, and PHX-010 contract regressions remain green
41. desktop regressions remain green

Use existing pytest tooling. Prefer `httpx.MockTransport` or equivalent deterministic in-process transport rather than another mocking dependency.

## Live smoke verification

If Ollama is available on the official BLAZE-6 machine, perform bounded read-only live checks after deterministic tests pass:

- provider health/version
- capabilities for one already-installed local model if available
- one short non-streaming generation using an already-installed small/local model only if doing so does not require downloading anything
- one short streaming generation using that same already-installed model if feasible

Do not pull/download models in PHX-011.

A missing Ollama service or missing local model is an environment limitation for live smoke, not permission to fake PASS. Deterministic adapter tests still define ticket correctness; report live-smoke availability separately and truthfully.

## Dependency policy

- Prefer promoting existing `httpx` to a direct runtime dependency if the adapter uses it.
- Do not add the Ollama Python SDK unless a concrete architectural benefit outweighs direct REST mapping; provider-neutral control and minimal dependencies are preferred.
- Do not add OpenAI SDK, requests, aiohttp, or overlapping HTTP libraries.

## Non-goals

- no model download/pull
- no model registry/routing
- no model keep-alive/lifecycle manager
- no concurrency scheduler
- no embeddings
- no Colibri
- no benchmark harness
- no agent integration
- no tool execution
- no vision payload transport
- no structured-output schema runtime beyond minimum existing contract support
- no frontend model UI
- no persistence migration

## Verification

Run at minimum:

- targeted Ollama provider tests
- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`
- Phase 0 and PHX-010 regressions
- `npm test`
- desktop typecheck/build
- `git diff --check`
- bounded optional live Ollama smoke if the local runtime/model is already available

## Acceptance criteria

- concrete `OllamaProvider` implements PHX-010 without leaking provider internals
- provider construction/import is network side-effect free
- health/capability/generation/streaming/cancellation behavior is explicit and bounded
- no model downloads or lifecycle work occur
- mocked deterministic tests cover success, failure, malformed, timeout, streaming, and cancellation paths
- provider data is validated before entering neutral PHX models
- previous foundations remain green
- security/invariants reviewed
- ticket/index are marked DONE only after required verification passes

## Completion evidence

When complete, append concise evidence covering files, dependency changes, endpoint/config design, health/capability mapping, generation/streaming/cancellation, timing/token mapping, tests, optional live smoke, regressions, security/invariants, and known limitations.

### Verified completion — 2026-10-04

- Added `phoenix/models/ollama.py` and `tests/test_ollama_provider.py`. Updated
  `pyproject.toml`, `uv.lock`, runtime dependency assertion and ADR-024. PHX-010
  contracts/tests remain unchanged. No agent/API/UI/persistence/events work.
- Promoted `httpx>=0.27,<1` from dev to runtime using `uv add` and `uv remove
  --dev httpx`; locked version remains 0.28.1. Normal uv-generated metadata-only
  lock changes; no other dependency/version changes or SDK added.
- API: OllamaProvider(ModelProvider), provider_id=ollama; constructor accepts
  base_url, finite HTTPX timeout, total operation_timeout, injected client OR
  transport. Default URL: http://127.0.0.1:11434/. Deterministic normalization,
  credential/query/fragment/malformed URL rejection, TLS verification retained,
  owned client trust_env=False, explicit follow_redirects=False on all requests.
  No construction probes. aclose()/async context manager provide cleanup;
  active_operation_ids is read-only evidence. Borrowed clients remain caller-owned.
- Defaults: connect/read/write/pool 3/120/10/5 seconds; total operation 300 seconds;
  request-state/client cleanup each bounded to five seconds. Timeouts are testable
  and reject infinite/zero/negative/non-finite configuration. Stream deadline is
  enforced even for buffered records and cannot cancel unrelated between-chunk
  consumer work. No background worker tasks or cross-loop request use.
- Health GET /api/version requires usable version evidence; transport failures
  yield unavailable; reachable invalid responses yield degraded. Safe detail only.
  POST /api/show maps completion/tools/vision metadata, cancellation=true and
  structured-output=false. Context keys ending .context_length must supply one
  unambiguous strict positive integer value; unavailable/invalid/conflicting
  evidence yields None. No capability registry or inferred model-name facts.
- POST /api/chat maps ordered role/content messages, stream flag, temperature,
  num_predict and stop sequences only. Valid assistant content and completion are
  required. Preserve original PHX operation/model/provider correlation; validate
  returned model when supplied, allowing only omitted/default :latest equivalence.
  Missing finish reason remains None. No tool/image/format/think/keepalive controls.
- Reported prompt_eval_count/eval_count map to input/output tokens; total stays
  None. Valid total_duration nanoseconds / 1,000,000 yields duration_ms;
  eval_count / (eval_duration / 1,000,000,000) yields throughput only with positive
  evaluation duration. No fabricated TTFT. Negative/malformed/non-finite counts
  and timings fail explicitly; no benchmark conclusions.
- Streaming reads HTTPX lines incrementally, ignores empty transport lines,
  validates records and requires real done=true. Exactly one final chunk closes
  resources/unregisters first. Broken/truncated/invalid streams never synthesize
  completion. Early consumers explicitly close the iterator or provider.
- Per-ID registration is atomic without awaits on one event loop; duplicates
  reject. cancel() cancels the caller task during active provider I/O, or closes a
  suspended stream without cancelling consumer work. accepted/not_found preserve
  PHX semantics; no runtime kill/unload/global shutdown. Finally blocks unregister
  normal/failed/timed-out/cancelled operations. aclose cancels pending owned request
  state, closes streams, awaits cleanup, and closes only an owned AsyncClient.
- Neutral errors: transport/timeouts -> ProviderUnavailableError; HTTP/protocol/
  malformed/identity failures -> ModelProviderError. No raw response bodies, URL
  credentials, prompts or HTTPX exceptions enter public response/error details.
- Verification commands:
  - `uv run --locked pytest tests/test_ollama_provider.py`: PASS; 108 cases.
  - `uv run --locked pytest tests/test_model_provider.py`: PASS; 61 unchanged cases.
  - `uv lock` / `uv sync --locked`: PASS; 36 packages.
  - `uv run --locked pytest`: PASS; 295 tests (API 8, bootstrap 11, config 19,
    events 28, hardware 48, ModelProvider 61, Ollama 108, persistence 12).
  - `uv run --locked ruff check .`: PASS.
  - `uv run --locked ruff format --check .`: PASS; 30 files.
  - `uv run --locked mypy`: PASS; 28 source files, strict settings unchanged.
  - `uv build`: PASS; distribution/wheel in ignored dist/.
  - `npm test`: PASS; four tests; desktop typecheck/build: PASS.
  - Diff/staged whitespace and credential-signature checks: PASS.
- Isolation: fresh interpreter guards writes/mkdir/SQLite/sockets/processes/
  threads/tasks and unrelated subsystem imports. Default client construction
  passes guards; explicit health/capability/generate/stream using MockTransport
  plus cleanup passes with no hidden tasks or filesystem artifacts. AST verifies
  no forbidden imports. Mock tests prove incremental first chunk, independent
  operations, duplicate rejection, blocked/paused stream cancellation, timeout,
  early close, owned/borrowed cleanup, HTTP errors and malformed data.
- Optional live smoke: MIXED (reported FAIL for the aggregate live smoke).
  Healthy local Ollama 0.35.1 at 2026-10-03T22:22:49.064776Z. Read-only /api/tags
  selected the smallest installed suitable local model, qwen2.5:1.5b (986,061,892
  bytes); no model pull/install/configuration change. Live capabilities: streaming
  true, structured output false, tools true, vision false, cancellation true,
  context limit 32768. One non-streaming request (16-token cap, safe fixed prompt)
  hit its live-only 45-second read timeout and raised ProviderUnavailableError;
  no successful response/usage was fabricated and no retry was performed.
  One streaming request with finite read/total bounds 120/180 seconds PASSED:
  six typed correlated chunks, ordered text PHOENIX_OK, one final chunk, finish
  reason stop, input/output tokens 38/6, total None, duration 245.9452 ms,
  reported-derived throughput 80.311609 tokens/sec, TTFT None. Active operations
  were empty and owned client closed. Cause of the earlier live timeout is not
  established; deterministic correctness is separate from this environment result.
- Security/invariants: no credentials/dotenv/private paths/raw errors logged,
  shell/subprocess/elevation, tool execution, model download, automatic upload,
  events, migrations, readiness claim or frontend provider access. Model output
  and tool support grant no authority. Reviewed INV-001..INV-024: preserved or
  unaffected. Health API, Alembic baseline and canonical 32 events unchanged.
- Limitations: one event loop per provider; caller must close abandoned streams;
  task cancellation cannot prove remote computation stopped. Borrowed clients/
  custom transports require caller-safe configuration. No structured-format,
  vision payload, registry/lifecycle/embedding/benchmark behavior. Initial live
  nonstream timeout and existing Starlette deprecation warning remain documented.
- All required deterministic acceptance criteria passed before marking DONE.
  Commit/push only the PHX-011 feature branch; final SHA/remote verification are
  in the execution report. PHX-012..PHX-016 remain TODO and were not started.
