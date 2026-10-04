# PHX-015 — Colibri Provider Adapter

Status: DONE

## Objective

Implement an optional, provider-neutral `ColibriProvider` that satisfies the frozen PHX-010 `ModelProvider` contract through Colibri's local OpenAI-compatible HTTP service (`coli serve`) without making Colibri a required PHOENIX runtime dependency.

## Authority

Read and obey, in order:

1. `AGENTS.md`
2. `RULES.md`
3. `MENTAL_MODEL.md`
4. `INVARIANTS.md`
5. `MASTER_SPEC.md`
6. `PRD.md`
7. accepted `DECISIONS.md`
8. `docs/01_ARCHITECTURE.md`
9. `docs/05_MODEL_STRATEGY.md`
10. `docs/06_HARDWARE_PROFILE.md`
11. `docs/09_SECURITY.md`
12. `docs/14_EVENT_SYSTEM.md`
13. `docs/23_OBSERVABILITY.md`
14. `docs/25_DEPENDENCIES.md`
15. PHX-010 through PHX-014 tickets and current code
16. this ticket

If these materially conflict, stop and resolve before implementation.

## Required implementation

### Provider boundary

- Add a concrete provider under `phoenix/models/`, preferably `phoenix/models/colibri.py`.
- Subclass the existing PHX-010 `ModelProvider` unchanged.
- Provider ID must be exactly `colibri`.
- Keep the adapter provider-neutral at its public boundary; do not expose raw Colibri/OpenAI HTTP objects.
- Do not import or depend on Ollama provider internals.

### Optional runtime classification

- Colibri remains an **optional V1 runtime**, not a required platform dependency.
- Do not add the Colibri repository/package as a Python runtime dependency.
- Do not clone, build, install, start, stop, configure, or manage the Colibri engine.
- Do not download/prepare any deep model.
- Reuse the existing HTTPX runtime dependency for HTTP transport.

### Endpoint policy

- Default API base URL: `http://127.0.0.1:8000/v1`.
- Constructor must allow an explicit configurable API base URL.
- Validate HTTP(S) URLs; reject embedded credentials and malformed query/fragment forms.
- Do not silently follow redirects.
- Owned clients must not trust ambient proxy variables for the local-first default.
- TLS verification must remain enabled for HTTPS.
- Optional API-key support is allowed for a locally configured Colibri server; never log or expose the key.
- Construction must perform zero network I/O.

### Health

Implement provider-local `health()` using only a bounded read-only API request, preferably `GET /v1/models` through the configured API base.

- Valid reachable model-list response => `healthy`.
- Transport/connect/read timeout/unreachable => `unavailable`.
- Reachable malformed protocol response => `degraded`.
- Health must not claim global PHOENIX readiness.
- Do not include raw response bodies or secrets in details.

### Capabilities

Implement `capabilities(model_id)` through the public Colibri OpenAI-compatible model endpoint, preferably `GET /v1/models/{model}`.

Return conservative PHX-010 `ModelCapabilities` only from evidence the adapter can actually satisfy:

- streaming: true for a successfully discovered served chat model through this adapter;
- cancellation: true if operation-scoped cancellation is implemented by this adapter;
- structured output: false unless the current neutral PHX request contract can actually request and verify it;
- tool use: false unless the current PHX request contract can actually supply tool definitions and the adapter can satisfy it end-to-end;
- vision: false because PHX-010 messages are text-only;
- context limit: only use a reliable positive model/API field if present and unambiguous, otherwise `None`.

Do not infer context/capabilities from a model family name, project candidate name, benchmark target, or Colibri engine marketing.

### Generation

Implement `generate(request)` through `POST /v1/chat/completions`.

Map the existing neutral PHX request only:

- `model`
- ordered `messages`
- `stream: false`
- `temperature` when provided
- `max_tokens` when provided
- `stop` when provided and supported by the HTTP API

Do not add provider-specific thinking, KV-slot, expert-cache, tool, image, audio, top-p, seed, reasoning, or engine-control fields to the PHX contract.

Validate successful responses strictly:

- preserve PHX operation ID;
- provider ID = `colibri`;
- preserve/validate requested model identity;
- extract assistant text from one valid completion choice;
- map finish reason only when supplied;
- map provider-reported token usage conservatively;
- map provider-reported total token count only when explicitly supplied;
- do not fabricate PHX benchmark metrics from provider-specific extensions.

HTTP/protocol/model-identity failures must never become successful generations.

### Streaming

Implement `stream(request)` using incremental HTTP SSE from `POST /v1/chat/completions` with `stream: true`.

Requirements:

- yield chunks incrementally; do not buffer the whole response;
- preserve PHX operation/provider/model correlation on every chunk;
- parse only valid `data:` SSE frames needed for OpenAI-compatible completion chunks;
- tolerate documented non-choice Colibri extension frames without exposing them as PHX output;
- treat malformed nonempty frames as explicit provider errors;
- require real stream completion evidence (`[DONE]` or equivalent validated terminal evidence);
- emit exactly one logical PHX final chunk;
- never report final success if the connection terminates early;
- clean up active-operation state on success, failure, timeout, cancellation, or consumer exit.

### Cancellation

Implement operation-scoped cancellation consistent with PHX-010 and PHX-011 semantics.

- Track active operations per operation ID.
- `cancel(operation_id)` must affect only that operation.
- Active operation => `accepted` when cancellation is requested.
- Unknown operation => `not_found`.
- Do not kill the Colibri process or all requests.
- Do not implement model unload or server shutdown.
- Duplicate simultaneous operation IDs must fail explicitly rather than overwrite state.

### HTTP client lifecycle

- Use `httpx.AsyncClient` directly; do not add OpenAI SDK.
- Every HTTP operation must have finite connect/read/write/pool bounds and a finite overall operation timeout.
- Allow deterministic test injection with a client or transport.
- Owned clients close explicitly through `aclose()`; borrowed clients remain caller-owned.
- Repeated cleanup should be safe where practical.
- No hidden worker/background task.

### Error boundary and privacy

Use the existing PHX-010 provider-neutral errors unless one small Colibri-specific subclass of `ModelProviderError` is genuinely useful internally.

- network/timeout/unreachable generation => `ProviderUnavailableError`;
- HTTP/protocol/malformed/provider contradiction => `ModelProviderError`;
- never expose full prompts, generated text, raw provider bodies, API keys, or private URLs in safe errors;
- do not automatically log prompts/responses.

## Frozen candidate policy

The frozen project strategy names an optional Colibri deep candidate, but PHX-015 must not hard-code any candidate as installed, supported, healthy, or selected.

- Do not auto-register Qwen or any current upstream Colibri model.
- Do not change the frozen model strategy to follow upstream model-family changes.
- Adapter accepts explicit served model IDs.
- Exact deep model suitability remains benchmark-gated in PHX-016 and later routing work.

## Strict non-goals

Do not implement:

- `coli serve` process management;
- Colibri installation/build/download/model conversion;
- model load/unload/keepalive/process termination;
- provider routing/fallback tiers;
- model ranking or benchmark decisions;
- PHX-016 benchmark harness;
- embeddings;
- agent/orchestrator integration;
- EventBus production;
- persistence/Alembic changes;
- FastAPI model endpoints;
- frontend/Tauri model controls;
- direct provider access from the frontend.

## Tests

Add focused deterministic tests, preferably `tests/test_colibri_provider.py`, using HTTPX mock transport/in-process streaming doubles. Live Colibri must not be required.

At minimum prove:

1. provider ID is `colibri`;
2. construction performs no network request;
3. default API base is loopback `/v1`;
4. URL normalization and validation;
5. credentials in URL rejected;
6. redirects not silently followed;
7. optional auth header applied only when configured and never leaked;
8. finite timeout validation;
9. health valid model list => healthy;
10. health connection failure/timeout => unavailable;
11. malformed health payload => degraded;
12. capabilities requests exact model endpoint;
13. model identity contradiction fails;
14. conservative capability flags are correct;
15. unknown context remains None;
16. reliable positive context metadata maps if implemented;
17. ordered PHX messages map unchanged;
18. nonstream request uses `/chat/completions` and `stream=false`;
19. temperature/max-token/stop mapping;
20. successful text maps to `GenerationResponse`;
21. operation/provider/model IDs preserved;
22. contradictory response model rejected;
23. provider-reported usage maps correctly;
24. total tokens are not invented;
25. no provider-specific performance extension becomes fabricated PHX metrics;
26. HTTP error => explicit provider error;
27. connection/timeout => unavailable error;
28. malformed JSON/body/choice => explicit error;
29. safe errors do not leak prompt/body/key;
30. stream request uses `stream=true`;
31. first stream chunk is available before complete stream consumption;
32. ordered streamed text preserved;
33. Colibri non-choice extension frame is safely ignored/validated;
34. malformed nonempty SSE frame fails explicitly;
35. early EOF without terminal evidence fails;
36. exactly one final logical PHX chunk;
37. final finish reason/usage map when supplied;
38. active generation cancellation => accepted;
39. active stream cancellation => accepted;
40. unknown cancellation => not_found;
41. cancellation A does not cancel B;
42. duplicate active operation ID fails explicitly;
43. completed/failed/cancelled operations are removed from active state;
44. owned HTTP client cleanup;
45. borrowed client remains caller-owned;
46. repeated cleanup safe where intended;
47. no shell/subprocess/process control;
48. no filesystem/database/hardware/event side effects;
49. no hidden tasks at import/construction;
50. no Colibri package/SDK runtime dependency;
51. PHX-010 provider tests remain green;
52. PHX-011 Ollama tests remain green;
53. PHX-012 registry tests remain green;
54. PHX-013 lifecycle tests remain green;
55. PHX-014 embedding tests remain green;
56. Phase 0 foundations remain green;
57. `/health` unchanged;
58. Alembic unchanged;
59. canonical 32 events unchanged;
60. desktop regressions remain green.

## Optional live smoke

Only after deterministic tests pass:

- check the configured/default local Colibri endpoint with a bounded read-only model-list request;
- do not start `coli serve`;
- do not install/clone/build Colibri;
- do not download/prepare a model;
- if a model is already served, a single tiny nonstream and streaming smoke may be run with strict time bounds;
- if no server/model is already available, report live smoke `UNAVAILABLE` without failing deterministic ticket correctness.

Never alter implementation to force a live environment pass.

## Verification gate

Required before DONE:

- targeted PHX-015 tests pass;
- PHX-010 through PHX-014 targeted tests pass unchanged;
- full Python test suite passes;
- Ruff check/format, strict mypy, `uv build` pass;
- desktop tests/typecheck/build pass;
- no dependency/lock churn unless genuinely required;
- side-effect/security/privacy/invariant review passes;
- diff contains no later-ticket scope.

## Completion

Only after all deterministic gates pass:

- change this ticket status from TODO to DONE and append concise evidence;
- change only PHX-015 in `FEATURE_TICKETS.md` from TODO to DONE;
- keep PHX-016 TODO.

Commit message:

`feat: complete PHX-015 Colibri provider adapter`

Do not create a pull request, merge, push main, force-push, or start PHX-016.

## Verified completion — 2026-10-04

- Created phoenix/models/colibri.py and tests/test_colibri_provider.py. Updated
  this ticket, only PHX-015 in FEATURE_TICKETS.md, and one accepted ADR-028.
  PHX-010 through PHX-014 implementation/tests, dependencies/locks, desktop,
  foundations, frozen model strategy and PHX-016 remain unchanged.
- ColibriProvider subclasses ModelProvider unchanged with provider_id=colibri;
  public health(), capabilities(model_id), generate(request), stream(request),
  cancel(operation_id), aclose(), async context management and read-only tuple
  active_operation_ids. No engine import/installation/process management or SDK.
- Default http://127.0.0.1:8000/v1; normalized explicit HTTP(S) base rejects
  credentials/query/fragment/malformed forms. Owned HTTPX verifies TLS, disables
  ambient proxies and redirects; calls enforce no redirects even on borrowed
  clients. Optional explicit Bearer key is masked, never read from environment,
  never replaced by a dummy key, and never included in public errors/logging.
- Finite positive configurable connect/read/write/pool defaults 3/600/10/5
  seconds, whole generation/stream 1800, metadata 10, cleanup five. No network
  during import/construction; one event loop, no hidden background worker.
  Owned client/transport closes explicitly; borrowed client remains caller-owned.
- GET models validates the OpenAI list and model records; an empty list is
  healthy. Transport/timeouts map unavailable; reachable HTTP/malformed protocol
  maps degraded, without claiming global PHOENIX readiness or loaded models.
- GET models/{URL-encoded entire model ID} requires exact returned identity,
  including no whitespace normalization/default :latest equivalence. Streaming
  and local operation cancellation are true; structured output/tool use/vision
  false. Context None: current public model metadata has no reliable context
  field; arbitrary extension fields/model names are not capability evidence.
- POST chat/completions maps model and ordered unmodified role/content messages,
  stream=false/true and supplied temperature/max_tokens/stop only. Single valid
  index-zero assistant choices validate before neutral response creation.
  Missing completion identity retains the request model; supplied contradiction,
  malformed content/choices/usage, HTTP/protocol failure cannot become success.
- Strict explicit prompt_tokens/completion_tokens/total_tokens map to neutral
  usage, with latest supplied stream values retained and no inferred total.
  GenerationMetrics stays None. Provider performance/engine extensions never
  become text, PHX metrics or benchmark evidence.
- Incremental SSE strictly decodes UTF-8, handles fragmented characters/multiline
  JSON, LF/CRLF/CR separators and comments; malformed bytes fail explicitly.
  A non-choice colibri-object envelope with optional exact model
  is ignored without inspecting internal telemetry; unknown/malformed envelopes
  fail. [DONE] after a valid choice is required; finish/usage/extension frames or
  premature EOF are not success. Exactly one empty final chunk retains supplied
  finish/usage only after response close and operation unregister.
- Executable first-chunk evidence: two source pieces consumed while later
  content/terminal pieces remain unread. Cancellation tracks generation and
  streaming per ID, affects only the matching I/O task/suspended response, and
  returns accepted/not_found. Duplicate IDs fail without overwriting originals;
  concurrent IDs remain independent, and stale cancelled iterators cannot remove
  a newly reused ID. Success/failure/timeouts/cancellation/explicit iterator close
  remove active state. Consumers own iterator close after early exit/exceptions.
- Safe ModelProviderError / ProviderUnavailableError distinguish HTTP/protocol
  from transport/timeouts, suppress raw exception display and expose no prompt,
  response, key or private URL. No provider-data logging or permission authority.
- Verification:
  - uv lock / uv sync --locked: PASS, 36 packages; no dependency/lock changes.
  - uv run --locked pytest tests/test_colibri_provider.py: PASS, 194 cases.
    Three consecutive final targeted runs: 194/194/194, no timing failures.
  - Individual locked PHX-010/011/012/013/014 regressions: PASS,
    61/108/83/54/182 cases; combined prior-model run also PASS, 488 cases.
  - uv run --locked pytest: PASS, 808 cases; one existing Starlette HTTPX
    deprecation warning. Phase 0 foundation subset: 126 passing cases.
  - uv run --locked ruff check . / ruff format --check .: PASS, 39 files.
  - uv run --locked mypy: PASS, 37 source files under unchanged strict config.
  - uv build: PASS, source distribution and wheel in ignored dist/.
  - npm test: PASS, four cases; desktop workspace typecheck/build: PASS.
- Fresh-interpreter evidence blocks sockets/network, filesystem writes/mkdir,
  SQLite, subprocesses, threads/tasks and imports of engine/SDK/core/API/hardware/
  persistence/agents/tools/permissions/voice/other model subsystems. Import,
  default/mock construction, explicit mocked health/capabilities/generation and
  cleanup pass with an empty temporary directory. AST checks constrain imports.
- Neutral registry.refresh() interoperates with the discovered capability;
  explicit caller-owned PHX-013 lease around generation works unchanged. No
  registry policy, lifecycle scheduling, embeddings, routing or benchmarks added.
- Optional bounded local health probe: UNAVAILABLE at 127.0.0.1:8000/v1.
  Served model/capabilities unavailable; live nonstream/stream SKIPPED. No runtime
  installation/start/stop/configuration/download/model preparation attempted.
- Security and INV-001 through INV-024 reviewed: preserved or unaffected.
  PHOENIX/PermissionEngine/ToolRegistry authority unchanged; data grants no
  permissions. No frontend provider access, events, persistence, migrations,
  API/UI, secret storage, uploads to an unconfigured endpoint or engine execution.
  /health, Alembic baseline and canonical 32 event types remain unchanged/green.
- All deterministic acceptance criteria met. Known limitations: no live model
  evidence, context/metrics unknown, unsupported envelopes fail explicitly,
  cancellation cannot prove remote computation stopped, stream consumers must
  close abandoned iterators and callers own borrowed-client configuration.
- Final whitespace/credential-signature and entire staged-diff review precede
  the new feat: complete PHX-015 Colibri provider adapter commit and normal push
  only to feature/PHX-015-colibri-provider-adapter. Final SHA/remote/clean-tree
  evidence is reported after push. No main push, PR, merge, force or PHX-016 work.
