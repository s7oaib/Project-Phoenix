# PHX-016 — Local Model Benchmark Harness

Status: **DONE**

Phase: **1 — Model Runtime**

## Objective

Implement a provider-neutral, explicit local generation benchmark harness for PHOENIX. The harness must produce reproducible, JSON-serializable evidence for model/runtime comparison without selecting a winning model, changing routing, downloading models, persisting benchmark rows, or making any provider mandatory.

This ticket closes the Phase 1 model-runtime foundation. The final FYP benchmark dataset and system-level comparison remain PHX-091 work.

## Authoritative context

Read and preserve:

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
- `docs/23_OBSERVABILITY.md`
- `docs/24_TEST_EVALUATION.md`
- `docs/25_DEPENDENCIES.md`
- PHX-010 through PHX-015 tickets and implementation

## Scope

### Provider-neutral benchmark boundary

Add a small benchmark module under `phoenix/models/`, preferably `phoenix/models/benchmark.py`.

The benchmark layer may depend on neutral `ModelProvider`, `ModelCapabilities`, `GenerationRequest`, `GenerationResponse`/`GenerationChunk`, and `ModelLifecycleManager` contracts. It must not import Ollama or Colibri private implementation helpers.

The harness must not itself route, rank, select or load models.

### Fixed, versioned local runtime suite

Provide one small immutable built-in benchmark suite for Phase 1 runtime verification.

The suite must have a stable suite ID and explicit version. It should contain only non-sensitive, deterministic text-generation cases suitable for local provider smoke/performance comparison, such as exact instruction following, arithmetic, simple formatting and tiny code output.

This Phase 1 suite is intentionally small. It is not the final ~100-task FYP evaluation dataset and must not be described as such.

Case ordering must be deterministic.

### Benchmark case contract

Use immutable validated models. A case should identify at least:

- `case_id`
- category/purpose
- ordered PHX messages or equivalent neutral input
- deterministic scoring rule where applicable
- optional request limits needed for a bounded run

Keep scoring deliberately small. Exact text and/or required substring scoring is sufficient. Open-ended cases may be explicitly unscored.

Do not embed user/private data in the built-in suite.

### Run configuration

Provide an immutable benchmark run configuration with explicit positive validation for values such as:

- warm-up runs
- measured repetitions
- per-trial overall timeout
- temperature, normally `0` for deterministic local evidence
- output-token bound

Defaults must be practical for local development and configurable for tests/live smoke.

Do not add provider-specific options or model-routing tiers.

### Provider preflight

A benchmark run targets an explicitly supplied provider and model ID.

Before measured generation, perform bounded neutral preflight through:

- `provider.health()`
- `provider.capabilities(model_id)`

Record the observed neutral capability snapshot.

Do not call model discovery APIs from the benchmark core.

If the provider is unavailable or incompatible, report explicit benchmark unavailability/failure rather than fabricated metrics.

### Lifecycle integration

Every measured/warm-up heavy generation must respect the existing PHX-013 lifecycle boundary.

Require or explicitly accept a shared `ModelLifecycleManager` and acquire a heavy-generation lease around each provider generation/streaming operation.

The harness must not implement its own heavy-generation semaphore.

Do not modify PHX-013.

### Streaming preference

If neutral capabilities report streaming support, prefer `provider.stream()` so the harness can measure TTFT directly.

If streaming is not supported, fall back to `provider.generate()` and report TTFT as unavailable rather than inventing it.

The harness must require real stream terminal evidence through the provider contract; it must not reinterpret provider wire protocols.

### Wall-clock performance metrics

Use monotonic high-resolution timing such as `time.perf_counter_ns()` for elapsed measurements.

Record at least:

- total generation latency in milliseconds
- TTFT in milliseconds when measurable
- output token count when explicitly reported by the provider
- input token count when explicitly reported
- total token count only when explicitly reported
- tokens/second only when defensibly calculable from explicit output token count and measured decode interval

TTFT means elapsed time to the first non-empty generated text chunk.

Do not use character count as token count.

Do not fabricate token totals or provider metrics.

### Throughput semantics

When streaming and output token count is known, throughput may be calculated as output tokens divided by elapsed decode time after first text through terminal completion.

If the denominator is zero/non-positive or output token count is unavailable, throughput is `None`.

Do not use provider-internal Colibri extension telemetry as benchmark truth.

### Deterministic scoring

For scorable cases, compute a small explicit pass/fail score from the transient generated text.

Store score evidence without retaining full generated text by default. A result may retain safe facts such as:

- output character count
- SHA-256 digest of generated text
- passed/failed/unscored

Do not automatically persist or log full prompts/responses.

### Trial result contract

Each measured trial must include enough immutable evidence to identify:

- suite ID/version
- case ID
- provider ID
- model ID
- repetition index
- UTC start/end timestamps
- operation ID
- success/failure/timeout/skipped state
- scoring outcome
- wall-clock metrics
- explicit token usage if available
- resource observations
- concise safe error category/detail if failed

Warm-up runs must be distinguishable and excluded from aggregate measured statistics.

### Report and aggregation

Return one immutable JSON-serializable benchmark report containing:

- suite identity/version
- target provider/model
- neutral capability snapshot
- run configuration
- measured trials
- aggregate summary

Aggregate measured successful trials only for numeric latency/TTFT/throughput summaries.

Use deterministic simple statistics such as count, min, median and max. Do not add a statistics dependency.

Include success rate and score accuracy for scorable cases.

Do not automatically choose or recommend a winner.

### Resource observation

Use existing `psutil` and safe local system APIs to collect approximate benchmark resource evidence without adding dependencies.

At minimum record:

- system RAM used bytes at benchmark samples
- system disk read/write byte counters so per-trial deltas can be calculated
- NVIDIA VRAM used MiB when `nvidia-smi` is available

Resource sampling is observational only and must never fail an otherwise valid generation solely because optional telemetry is unavailable.

Use clear names such as `max_observed` rather than claiming an absolute hardware peak if sampling is discrete.

System-wide RAM/disk counters are approximate and may include background activity; document this limitation.

### NVIDIA telemetry safety

If NVIDIA VRAM is sampled, use a fixed read-only `nvidia-smi` command, `shell=False`, finite timeout, captured output and strict parsing.

Support zero, one or multiple GPUs. Do not sum unrelated GPUs into a fabricated per-model number; retain per-GPU observations where practical.

Missing/failed/malformed `nvidia-smi` telemetry must become unavailable telemetry, not benchmark failure.

### Sampling policy

Keep sampling explicit and bounded.

A simple V1 policy may sample resources:

- immediately before generation
- at meaningful streaming checkpoints/chunks
- immediately after terminal completion

No permanent sampler daemon is required.

If a temporary sampler task is used, it must exist only during an explicit trial, be cancellation-safe, and leave no task behind.

### Disk I/O semantics

For Colibri and other providers, record system-wide disk read/write counter deltas when available.

Do not claim those bytes are exclusively attributable to Colibri. Label them as system-wide observed deltas.

This supplies the evidence channel needed for later Colibri/NVMe evaluation without pretending to have provider-process attribution.

### Failure and timeout semantics

A timed-out or failed trial must remain a failed/timeout trial.

Do not turn failure into success because some metrics were collected.

On timeout/cancellation, ensure lifecycle leases are released and provider operations are not left active. Use provider cancellation where appropriate and supported, without treating `CancellationStatus.ACCEPTED` as proof remote compute stopped.

Subsequent trials must still be able to run.

### No raw provider internals

The benchmark layer consumes neutral provider contracts only.

Do not parse:

- Ollama HTTP JSON
- Colibri SSE
- Colibri expert telemetry
- provider SDK objects

Those remain adapter responsibilities.

### No automatic model discovery/download

The core harness receives explicit provider/model targets.

Do not:

- pull Ollama models
- install EmbeddingGemma
- clone/start Colibri
- download deep weights
- auto-enumerate candidate models

Optional live verification may perform bounded read-only discovery outside the benchmark core to choose an already-installed model.

### No routing or freeze decision

PHX-016 gathers evidence. It must not implement model routing, tier selection, agent-to-model mapping, fallback order, or automatic routing freeze decisions.

Do not edit the frozen model strategy based on one live run.

### Capability coverage honesty

The frozen strategy ultimately needs evidence for quality, tool calling, structured output, coding, planning, vision and performance.

The current PHX-010 request contract is text-only and has no neutral tool definitions or image payloads. PHX-016 must not fake those evaluations.

The report may record neutral capability metadata and explicitly mark dimensions as unsupported/not-assessed. Future tickets and PHX-091 may expand benchmark tasks when authoritative contracts exist.

### No embedding benchmark in this ticket

Do not benchmark `EmbeddingProvider`/EmbeddingGemma in PHX-016. This ticket benchmarks text-generation providers and routing evidence. RAG/embedding quality belongs to later memory/RAG evaluation.

### No persistence

Do not add or modify Alembic migrations or create `model_benchmarks` tables in PHX-016.

Benchmark reports are in-memory JSON-serializable evidence. Callers may explicitly serialize them outside this ticket, but the harness performs no implicit file write/database insert.

### No API/frontend/event integration

Do not add benchmark FastAPI endpoints, frontend pages, Tauri commands or EventBus producers.

`GET /health` remains unchanged.

## Tests

Add focused tests, preferably `tests/test_model_benchmark.py`.

At minimum verify:

1. built-in suite ID/version are stable
2. suite/case ordering deterministic
3. suite/case/config models immutable and extra-forbidden
4. invalid warm-up/repetition/timeout/token limits reject
5. provider/model identifiers validate
6. health preflight is performed explicitly
7. unavailable health produces explicit non-success result/report
8. capabilities captured through neutral contract
9. no provider-specific imports in benchmark core
10. lifecycle lease acquired per generation
11. lifecycle lease released after success
12. lifecycle lease released after provider failure
13. lifecycle lease released after timeout/cancellation
14. default global lifecycle capacity remains respected
15. streaming path preferred when supported
16. non-stream fallback works when streaming unsupported
17. TTFT measured from first non-empty text chunk
18. empty stream chunks do not become TTFT
19. total latency uses monotonic timing
20. input/output/total usage maps only from explicit provider usage
21. token totals are never invented
22. tokens/sec formula is correct
23. missing output token count -> throughput None
24. non-positive decode interval -> throughput None
25. exact scoring works
26. contains scoring works if implemented
27. unscored case remains explicitly unscored
28. generated text is not retained in report by default
29. output digest/length deterministic
30. warm-up trials excluded from measured aggregates
31. success/failure/timeout state aggregation correct
32. min/median/max calculations deterministic
33. success rate correct
34. score accuracy excludes unscored cases
35. resource probe samples before/after explicit generation
36. RAM values numeric/nonnegative when available
37. disk counter deltas nonnegative when counters monotonic
38. unavailable disk counters handled
39. fixed nvidia-smi argv used
40. shell execution never used
41. malformed/missing nvidia-smi does not fail trial
42. multiple GPU telemetry remains distinguishable
43. VRAM observed-max semantics correct
44. system-wide disk metrics clearly labeled approximate
45. resource telemetry failure cannot fabricate zero-as-known
46. timeout leaves no active provider operation
47. timeout leaves no lifecycle lease
48. one failed trial does not corrupt later trial
49. report JSON round-trip is deterministic
50. report contains no raw provider payload
51. report contains no API keys/credentials
52. benchmark core performs no model discovery/download
53. no routing/tier/preference selection exists
54. no benchmark persistence/Alembic change
55. no EventBus production
56. no API/frontend integration
57. PHX-010 provider tests remain green
58. PHX-011 Ollama tests remain green
59. PHX-012 registry tests remain green
60. PHX-013 lifecycle tests remain green
61. PHX-014 embedding tests remain green
62. PHX-015 Colibri tests remain green
63. Phase 0 foundation tests remain green
64. `/health` unchanged
65. canonical 32 events unchanged
66. desktop regression green

Use deterministic fake providers and fake clocks/resource probes where necessary. Do not make normal tests depend on live Ollama, Colibri, NVIDIA utilities or machine load.

## Optional live smoke

After all deterministic tests pass, perform one bounded local benchmark smoke using an already-installed small Ollama chat model if available.

Do not download a model.

Use the built-in suite or a tiny subset with warm-up disabled and one measured repetition to keep the smoke bounded.

Report actual:

- model ID
- provider health
- capabilities
- case count
- measured success count
- score pass count
- TTFT if available
- total latency
- explicit token usage
- calculated throughput when available
- RAM observation
- VRAM observation when available
- disk I/O delta

Do not print full generated text.

If no suitable model is installed or Ollama is unavailable, report live smoke as unavailable without failing deterministic ticket acceptance.

If Colibri is already running, an additional tiny Colibri smoke is optional; do not start/install it.

## Dependencies

Expected new dependencies: **none**.

Reuse Python 3.11, asyncio, Pydantic v2, psutil and existing model contracts.

## Acceptance criteria

- provider-neutral benchmark harness exists
- small fixed/versioned Phase 1 suite exists
- explicit warm-up/measured run semantics
- lifecycle boundary enforced
- streaming TTFT/latency measured truthfully
- explicit token usage and defensible throughput only
- deterministic scoring and safe output digest evidence
- RAM/VRAM/disk observations are truthful and clearly scoped
- benchmark/report models immutable and JSON-serializable
- failures/timeouts remain explicit and cleanup is proven
- no routing/selection/freeze decision
- no downloads/discovery in core
- no persistence/API/frontend/events
- all deterministic tests and full regressions pass
- ticket/index become DONE only after verification

## Expected implementation scope

Likely:

- `phoenix/models/benchmark.py`
- `tests/test_model_benchmark.py`
- `DECISIONS.md` only if a durable benchmark-policy ADR is justified
- this ticket
- `FEATURE_TICKETS.md`

Avoid unrelated changes.

## Commit

If and only if all gates pass:

`feat: complete PHX-016 local model benchmark harness`

Push only the PHX-016 feature branch. Do not create/merge a PR and do not start Phase 2.

## Verified completion — 2026-10-04

- Created phoenix/models/benchmark.py and tests/test_model_benchmark.py. Updated
  this ticket, only PHX-016 in FEATURE_TICKETS.md, and one accepted ADR-029.
  PHX-010 through PHX-015 implementations/tests, dependency manifests/locks,
  frozen model strategy, foundations, desktop and PHX-020 remain unchanged.
- Public entry point: BenchmarkRunner(shared_lifecycle, resource_probe=...,
  clock_ns=...).run(provider, model_id, suite=PHASE1_SUITE, config=...). Frozen,
  extra-forbidden models cover cases/suite/scoring/config, resource readings,
  trial results, summaries and reports; reports JSON-round-trip. ResourceProbe
  supports deterministic injection; SystemResourceProbe performs explicit local
  observations. summarize() exposes measured-only numeric/scoring aggregates.
- Suite phoenix-phase1-text / 1.0.0: ordered instruction, arithmetic, format,
  code cases; literal expected PHOENIX_OK, 42, {"ok":true}, print(2 + 3).
  Exact/contains/unscored rules do not normalize text. This is a tiny runtime
  microbenchmark; final FYP quality evaluation remains PHX-091.
- Defaults: one warm-up and three measured repetitions per case, per-trial
  lease/generation deadline 60 seconds, preflight 10, cleanup five, temperature
  zero and output limit 64. Integer fields reject booleans, invalid counts and
  limits; finite positive timeouts and finite nonnegative temperature validate.
- Explicit neutral health/capabilities preflight validates identities, strips
  free-text health detail, records capabilities and reports unavailable/degraded/
  invalid/timeout outcomes without fabricated trials or PHOENIX readiness claims.
  No registry mutation, model-name capability inference or target discovery.
- Every warm-up/measured operation acquires the caller's shared PHX-013 heavy
  lease. Shared capacity one is enforced between runners and unrelated operations;
  waiting deadlines/cancellation perform no generation or probes. There is no
  extra semaphore, capacity manager, background generation task or sampler.
- Streaming is preferred when supported; otherwise generate() returns TTFT None.
  perf_counter_ns measures TTFT from immediately before provider execution to
  first nonempty text, ignoring empty/usage/final-empty frames. Latency ends at
  validated neutral is_final evidence, exactly once; malformed/missing/duplicate/
  post-final frames fail. Adapters retain ownership of transport completion.
  UTC start/end fields never calculate durations. Provider timing is not reused.
- Retain only explicit input/output/total TokenUsage, including partial supplied
  stream counts; missing totals are never inferred. Throughput is output_tokens /
  ((completion_ns - first_text_ns) / 1e9) only with known TTFT, known output count
  and positive interval. Missing, invalid or overflowing rate evidence is None.
- Generated text remains transient for scoring and is absent from reports.
  Retain exact UTF-8 SHA-256 and Python character count; invalid Unicode fails
  explicitly. No prompt, API key, raw response, private URL or error body is
  retained/logged. Neutral safe failure enums avoid provider error text.
- Trial states are success/failed/timeout/skipped; warm-up and measured kinds
  remain distinct. Successful measured values supply count/min/median/max;
  absent evidence stays None. Success rate is successful/all measured slots.
  Accuracy is passed/all scorable measured slots, including failed/timeout/
  skipped scored attempts as failures; unscored slots are excluded. No scorable
  trials means accuracy None. Warm-ups never enter any aggregate denominator.
- Two explicit boundary resource snapshots report max_observed RAM-used bytes,
  system disk read/write deltas, and separate GPU index/name/total/max observed
  used MiB. Disk resets/missing counters yield None. RAM/disk may include unrelated
  system activity; GPU maxima are sampled observations, not absolute peaks or
  exclusive provider/model attribution. Zero GPUs is distinguishable from unknown.
- NVIDIA queries use fixed argv, shell=False, captured output and one-second
  timeout, with strict numeric CSV and multiple-GPU parsing. Missing/nonzero/
  timed-out/malformed queries and optional probe exceptions yield unknown data,
  without failing valid generation. Diagnostic probe windows are separate from
  the lease/generation deadline; tests cover slow probes before and after work.
- Timeout/caller cancellation unwinds generation, closes the iterator, attempts
  bounded operation-specific cancellation when supported and releases the lease.
  Subsequent operations remain usable. Cleanup failure raises safe BenchmarkError
  and aborts the run; it cannot fabricate completion. Provider unavailability
  skips remaining configured slots; ordinary trial failures continue. Caller
  owns providers, and cancellation cannot prove server computation stopped.
- Verification:
  - uv lock / uv sync --locked: PASS, 36 packages, no lock/dependency churn.
  - uv run --locked pytest tests/test_model_benchmark.py: PASS, 130 cases.
    Three consecutive final runs: 130/130/130 in 1.13/1.17/1.18 seconds.
  - Individual locked PHX-010/011/012/013/014/015 regressions: PASS,
    61/108/83/54/182/194 cases, without editing any prior model tests.
  - uv run --locked pytest: PASS, 938 cases; one existing Starlette HTTPX
    deprecation warning. Explicit six-file Phase-0 subset: 126 passing cases.
  - uv run --locked ruff check . / ruff format --check .: PASS, 41 files.
  - uv run --locked mypy: PASS, 39 source files under unchanged strict config.
  - uv build: PASS, source distribution and wheel in ignored dist/.
  - npm test: PASS, four cases; desktop workspace typecheck/build: PASS.
- Fresh-interpreter executable isolation blocks network, subprocesses, filesystem
  writes/mkdir, SQLite, threads/tasks and adapter/core/API/persistence/agent/tool/
  permission/voice imports. Import/construction passes with an empty temporary
  directory. AST checks enforce neutral dependencies and prohibited integrations.
- Optional bounded local probes: Ollama UNAVAILABLE, Colibri UNAVAILABLE.
  No model selected, generation attempted or live metrics collected. Discovery,
  model download, runtime start/install/stop/configuration were not performed.
  Live unavailability is separate from deterministic ticket acceptance.
- Security and INV-001 through INV-024 reviewed: preserved or unaffected.
  PHOENIX/PermissionEngine/ToolRegistry authority remains intact; benchmark scores
  grant no authority. /health, Alembic baseline and canonical 32 events remain
  unchanged/green. No routing/winner/strategy change, persistence, API/UI, event,
  embedding, provider process management or Phase-2 integration was added.
- All deterministic acceptance criteria met. Known limitations: no live runtime
  evidence; boundary sampling is approximate; synchronous probes need bounded
  implementations and add diagnostic time outside the operation deadline;
  provider cleanup must cooperate; local cancellation is not remote termination;
  tool/structured/vision/planning/general-coding quality remains not assessed.
- Entire staged-diff, whitespace and credential-signature review precede the
  new feat: complete PHX-016 local model benchmark harness commit and normal push
  only to feature/PHX-016-local-model-benchmark-harness. Final SHA, remote equality
  and clean-tree evidence are reported after push. No main push, force, PR, merge
  or PHX-020 work. PHX-010 through PHX-016 are DONE; Phase 1 is COMPLETE.
