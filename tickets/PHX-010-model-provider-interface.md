# PHX-010 — ModelProvider Interface

Status: DONE

Phase: 1 — Model Runtime

## Objective

Define the provider-neutral model runtime contract that all PHOENIX inference backends implement. Agents and orchestration must depend on this contract rather than Ollama, Colibri, AirLLM, or provider-specific SDKs.

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
- this ticket

## Dependencies

- Phase 0 / PHX-001 through PHX-007 — DONE

## Requirements

### Boundary

- Implement the contract under `phoenix/models/`.
- Core, agents, routing, and later orchestration consume `ModelProvider`; they must not depend on concrete provider internals.
- Do not implement Ollama, Colibri, AirLLM, provider discovery, lifecycle management, embeddings, benchmarks, or model routing in this ticket.
- No network, subprocess, database, filesystem, hardware scan, background task, event emission, or provider initialization may occur from importing or constructing the contract types.

### Provider contract

The frozen model strategy requires provider capabilities for:

- health
- generation
- streaming
- structured-output metadata
- tool-use metadata
- vision metadata
- context limits
- cancellation
- metrics

Define one small provider-neutral interface/ABC covering these responsibilities without leaking provider-specific request or response types.

A clean contract may include methods conceptually equivalent to:

- provider identity
- `health()`
- model/capability description
- `generate(request)`
- `stream(request)` returning an async iterator
- `cancel(operation_id)`

Exact names may differ if the resulting public API is clearer.

### Provider identity

- Use a stable neutral provider identifier, such as `ollama`, `colibri`, or future adapter IDs.
- The abstract contract must not hard-code one provider as the only implementation.
- Provider display labels are metadata; routing must eventually rely on stable IDs/capabilities.

### Message / generation request

Provide a provider-neutral request model suitable for text generation.

At minimum support:

- generated/caller-supplied operation/request ID
- model identifier
- ordered messages
- message role
- text content
- optional generation controls that are sufficiently generic and validated

Do not overfit the request to Ollama's JSON schema.

Do not add agent persona fields, routing decisions, permission grants, ToolRegistry execution, or conversation persistence.

### Message roles

Support the minimum provider-neutral role vocabulary needed for local chat generation, such as:

- system
- user
- assistant
- tool

If `tool` is included, it is transport/content metadata only. This ticket must not implement tool calling or ToolRegistry behavior.

### Generation response

Provide a typed provider-neutral response containing at least:

- operation/request ID correlation
- provider ID
- model identifier
- generated text/content
- finish/completion reason where available
- token usage where available
- generation metrics where available
- provider/model metadata only if JSON-safe and non-sensitive

Unknown optional metrics must remain unavailable rather than fabricated.

### Streaming

Define a typed streaming chunk contract.

At minimum allow:

- operation/request ID correlation
- provider/model identity
- incremental text
- completion/final marker or finish reason when available
- optional usage/metrics on final/provider-supported chunks

The interface should expose streaming as an asynchronous iterator without starting background workers merely by constructing the provider contract.

PHX-010 does not need a concrete stream implementation beyond deterministic fake/test providers.

### Capabilities

Define typed model/provider capability metadata sufficient for later PHX-012 capability registry work, including at least:

- streaming support
- structured-output support
- tool-use support
- vision support
- context limit in tokens when known
- cancellation support

Do not implement the PHX-012 registry, routing scores, model tiers, benchmark results, or automatic selection.

Capability values describe support; they do not grant permissions or authority.

### Health

Define provider health metadata with explicit states, for example:

- healthy
- degraded
- unavailable

Health must be typed and timestamped where useful.

Do not perform real health probes in PHX-010.

No provider health result may claim that PHOENIX as a whole is ready.

### Cancellation

The abstract provider contract must expose explicit cancellation semantics for a specific operation/request ID.

- Cancellation must not be modeled as a global provider shutdown.
- The interface must allow providers to report whether cancellation was accepted/supported.
- Do not implement process killing or model lifecycle management.

### Metrics

Use provider-neutral optional numeric metrics such as:

- input tokens
- output tokens
- total tokens
- time-to-first-token where available
- total duration where available
- tokens/sec where available

Use explicit units in field names or documentation.

Do not fabricate unsupported metrics.

### Structured output / tool use / vision

PHX-010 defines capability metadata only unless a very small generic request field is clearly required by the frozen architecture.

Do not pre-build:

- ToolRegistry schemas
- provider-specific JSON format flags
- image loaders
- multimodal file handling
- function execution
- permission flows

Those belong to later tickets.

### Error boundary

Define a small provider-neutral exception boundary if useful, but do not create a large hierarchy.

At minimum provider failures must remain explicit and distinguish provider unavailability from successful generation.

Do not swallow exceptions and return fake successful responses.

### Immutability / validation

Prefer immutable validated request/response/capability models.

- reject empty model IDs where appropriate
- reject invalid token/context values
- reject negative metrics
- ensure request IDs/correlation values are stable/serializable
- ensure arbitrary provider objects cannot leak into public metadata

### Security

- No secrets, tokens, credentials, environment dumps, provider URLs, or machine-private paths belong in public response metadata by default.
- Provider metadata must be JSON-safe.
- Model output is untrusted content; defining the contract does not authorize execution.
- Tool-use capability metadata never bypasses `PermissionEngine`/`ToolRegistry`.

## Suggested implementation shape

Keep the surface small, for example:

- `phoenix/models/provider.py`

Possible public types:

- `ModelProvider`
- `ProviderHealth`
- `ProviderHealthStatus`
- `ModelCapabilities`
- `ModelMessage`
- `MessageRole`
- `GenerationRequest`
- `GenerationResponse`
- `GenerationChunk`
- `TokenUsage`
- `GenerationMetrics`
- a small provider error boundary

Exact names may differ if cleaner.

## Tests

Use deterministic fake providers only. Do not contact Ollama or any external runtime.

At minimum verify:

1. provider contract is framework/provider neutral
2. abstract/incomplete provider cannot be instantiated
3. deterministic fake provider can implement the full contract
4. provider ID is exposed neutrally
5. health states validate
6. request ID is generated by default
7. caller-supplied request ID is preserved
8. empty model ID is rejected
9. ordered messages are preserved
10. role vocabulary validates
11. request/control validation rejects invalid values
12. response correlates to request/provider/model
13. token usage accepts unavailable fields and rejects negative counts
14. metrics accept unavailable values and reject negative durations/rates
15. capability metadata validates booleans/context limit
16. unknown context limit can remain unavailable
17. streaming fake provider yields ordered typed chunks
18. stream final/finish metadata is representable
19. cancellation is operation-scoped
20. unsupported/rejected cancellation is explicit
21. provider-unavailable failure is not represented as successful generation
22. JSON serialization of all public models succeeds
23. arbitrary Python objects are rejected from provider metadata
24. import/construction performs no I/O/network/process/task/provider initialization
25. no FastAPI, SQLAlchemy/Alembic, hardware, events, agents, tools, permissions, voice, Ollama, or Colibri imports leak into the contract module
26. Phase 0 regressions remain green
27. `/health` remains unchanged
28. desktop regressions remain green

## Dependencies

Expected new direct dependencies: none.

Use existing Python/Pydantic/typing facilities. Do not add an Ollama client, HTTPX runtime integration, OpenAI SDK, Colibri package, or event framework in PHX-010.

## Non-goals

- no PHX-011 Ollama adapter
- no PHX-012 capability registry
- no PHX-013 model lifecycle manager
- no PHX-014 embeddings
- no PHX-015 Colibri adapter
- no PHX-016 benchmark harness
- no agent/model router
- no model downloads
- no model loading/unloading
- no context-routing policy
- no HTTP calls
- no provider health probe
- no persistence schema changes
- no event producers
- no frontend model UI

## Verification

Run at minimum:

- targeted provider-contract tests
- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`
- `npm test`
- desktop typecheck/build
- `git diff --check`

No live provider/network verification is required because PHX-010 is contract-only.

## Acceptance criteria

- provider-neutral `ModelProvider` contract exists
- health/generation/streaming/capability/cancellation/metrics responsibilities are represented
- public request/response/chunk models are typed and validated
- no concrete provider/runtime behavior is implemented
- no import/construction side effects
- failure semantics are explicit
- no permission/security boundary is weakened
- deterministic fake provider proves substitutability
- no new runtime dependency is required
- Phase 0 functionality remains green
- security/invariants reviewed
- ticket/index are updated only after successful verification

## Completion evidence

When complete, append concise evidence covering files, contract/API, validation and failure semantics, fake-provider tests, regressions, dependency status, security/invariants, known limitations, commit, and push.

### Verified completion — 2026-10-04

- Added `phoenix/models/provider.py` and `tests/test_model_provider.py`. No package
  re-exports, concrete provider, SDK/client, runtime probe, or future empty module.
  Existing stdlib/Pydantic only; no dependency change and `uv.lock` unchanged.
- `ModelProvider(ABC)` exposes validated read-only `provider_id`; abstract async
  `health()`, `capabilities(model_id)`, `generate(request)`, `cancel(operation_id)`;
  abstract `stream(request)` returns AsyncIterator[GenerationChunk] directly.
  Adapters call the base constructor with a neutral non-empty stable ID and are
  responsible for matching provider/model/operation identities in their results.
- Frozen models: ModelMessage/MessageRole, GenerationRequest, ModelCapabilities,
  ProviderHealth/ProviderHealthStatus, TokenUsage, GenerationMetrics,
  GenerationResponse, GenerationChunk, CancellationResult/CancellationStatus.
  Extra fields are forbidden; no arbitrary metadata/options dictionaries.
  Ordered messages and stop sequences are snapshotted to tuples. IDs/non-empty
  stop items, positive integer context/output limits, finite nonnegative controls
  and metrics, and strict capability flags validate. Generated UUID string IDs
  and caller IDs serialize stably. Health timestamps are aware and normalized UTC.
- Response/chunks retain operation/provider/model correlation, text, optional
  finish reason, usage and metrics; chunks add final indication. Token counts are
  nonnegative optional integers with no inferred total. Metrics use ttft_ms,
  duration_ms and tokens_per_second; unknown values remain None. No benchmarking.
- Cancellation is scoped to one operation: accepted/unsupported/not_found;
  accepted acknowledges the request, not completed termination. Two exceptions,
  ModelProviderError and ProviderUnavailableError, expose failures without fake
  success. No process killing, model lifecycle or background task machinery.
- Test-only deterministic providers exercise the full ABC through base-typed
  consumers with separate IDs. Tests verify ordered streaming/final metrics,
  response correlation, request-scoped cancellation leaving another operation
  untouched, explicit unsupported/not-found results and unavailable failure.
- Commands/results:
  - `uv run --locked pytest tests/test_model_provider.py`: PASS; 61 cases.
  - `uv lock` / `uv sync --locked`: PASS; 36 packages, no lockfile churn.
  - `uv run --locked pytest`: PASS; 187 tests. API 8, bootstrap 11, config 19,
    events 28, hardware 48, model provider 61, persistence 12.
  - `uv run --locked ruff check .`: PASS.
  - `uv run --locked ruff format --check .`: PASS; 28 files.
  - `uv run --locked mypy`: PASS; 26 source files, strict configuration unchanged.
  - `uv build`: PASS; source distribution and wheel in ignored dist/.
  - `npm test`: PASS; four tests; desktop typecheck/build: PASS.
  - `git diff --check` and staged whitespace/credential-signature checks: PASS.
- Fresh-interpreter isolation blocks socket creation, subprocess launch, file
  writes (audit hook), mkdir, SQLite connections, threads and asyncio task
  creation. Blocked subsystem imports cover providers/HTTP clients, hardware,
  events/core, API, persistence, agents, tools, permissions and voice. Importing
  contracts and constructing every model plus the actual test fake class passes;
  temporary working directory remains empty. AST checks restrict contract imports
  to standard library and Pydantic. No live provider/network run is needed.
- Phase 0 and desktop are unchanged: exact health contract, Alembic baseline,
  canonical 32 events, configuration, hardware scanner, EventBus and UI preserved.
  Security/privacy: no credentials/URLs/private paths/environment metadata,
  arbitrary object serialization, provider I/O or model-generated execution.
  Tool roles and tool-use support are content/technical metadata only.
- Reviewed INV-001..INV-024: preserved or unaffected, especially orchestrator
  authority, provider replaceability, frontend boundary, permission/tool controls,
  explicit failure and no mandatory optional runtime. ADR-023 records the contract.
- Limitations: adapters must honor correlation, safe health detail and bounded
  operation/stream/cancellation behavior; the ABC implements no provider runtime,
  registry, scheduler, timeout policy, metadata bag or structured-output decoding.
  Existing Starlette HTTPX deprecation warning remains; all verification passes.
- Completion uses a new feature commit `feat: complete PHX-010 ModelProvider
  interface` and pushes only `feature/PHX-010-model-provider-interface`. Final SHA
  and remote verification are reported after push. No main/PR/merge/force push.
  PHX-010 is DONE; PHX-011..PHX-016 remain TODO and are not started.
