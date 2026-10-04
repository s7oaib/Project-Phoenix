# PHOENIX Meaningful Decisions Log

Record only meaningful decisions future engineers must preserve.

## ADR-001 — PHOENIX is supreme orchestrator
Status: Accepted.

## ADR-002 — Voice-first, not voice-only
Status: Accepted.

## ADR-003 — Tauri 2 desktop
Status: Accepted.

## ADR-004 — FastAPI is transport, not architecture
Status: Accepted.

## ADR-005 — Custom orchestration core
Status: Accepted. Do not replace with LangChain/CrewAI/AutoGen without an explicit new decision.

## ADR-006 — Ollama default provider
Status: Accepted.

## ADR-007 — Colibri optional deep provider
Status: Accepted; benchmark-gated.

## ADR-008 — SQLite local persistence
Status: Accepted.

## ADR-009 — Deterministic PermissionEngine
Status: Accepted.

## ADR-010 — Neutral internal agent IDs
Status: Accepted.

## ADR-011 — Wake phrase
Status: Accepted: `Rise, Phoenix`.

## ADR-012 — Development hardware profile
Status: Accepted: `BLAZE-6`.

## ADR-013 — One heavy generation by default
Status: Accepted for BLAZE-6.

## ADR-014 — Voice provider abstraction
Status: Accepted.

## ADR-015 — Unauthorized exact voice-actor cloning is not required
Status: Accepted. Distribution path uses original/licensed/consented profiles.

## ADR-016 — Python bootstrap packaging and tooling
Status: Accepted for PHX-001.

Use a root `phoenix` package, Hatchling as the build backend, and uv with a committed
lockfile for a Python 3.11-only development environment. Pytest verifies packaging;
Ruff provides lint/format checks; mypy provides strict type checks. These are pinned
development/build dependencies, with no application runtime dependencies added.

## ADR-017 — npm desktop workspace foundation
Status: Accepted for PHX-001.

Use a private root npm workspace containing `apps/desktop`, with a committed npm
lockfile and a declared npm version. Node's built-in test runner validates workspace
metadata without extra dependencies. PHX-003 introduces the approved Tauri 2 / React /
TypeScript / Vite scaffold and dependencies; PHX-001 reserves its package boundary.

## ADR-018 — Desktop application identity and initial IPC boundary
Status: Accepted for PHX-003.

Use `io.github.s7oaib.phoenix` as the stable Tauri application identifier, scoped
to the Project Phoenix repository owner. The foundation enables no frontend IPC
permissions, plugins, or custom Rust commands. Future backend functionality must
continue through FastAPI and Phoenix's deterministic permission/tool boundaries;
adding a Tauri capability requires an explicit, scoped feature requirement.

Production CSP permits local scripts/styles and denies network connections. The
development CSP allows the exact Vite React refresh preamble by SHA-256 hash,
loopback HMR, and Vite's inline CSS updates. A targeted test checks the preamble
hash against the installed plugin so dependency updates cannot silently break it.

## ADR-019 — Typed configuration namespace and loading boundary
Status: Accepted for PHX-004.

Use `AppSettings` in `phoenix/core/config.py` with Pydantic v2 settings and only
the `PHOENIX_ENVIRONMENT`, `PHOENIX_API_HOST`, `PHOENIX_API_PORT`, and
`PHOENIX_DATA_DIR` fields. Precedence is explicit constructor values, process
environment, UTF-8 `.env` in the working directory, then code defaults. Use
`_env_file=None` to disable dotenv loading for isolated callers/tests.

Unknown constructor/dotenv keys are rejected; the standard process source reads
only declared fields and ignores other environment variables, including unknown
Phoenix-prefixed keys. Tests lock down this distinction. Invalid declared values
raise validation errors without a fallback. Configuration loading reads inputs
without creating directories or initializing runtime subsystems. No import-time
instance, singleton, or cache is provided.

The portable data default is `<home>/.phoenix/data`; `D:/Phoenix/data` is only the
optional BLAZE-6 example override. Directory creation belongs to a later runtime
owner. FastAPI remains independent of loading settings until a concrete transport
consumer requires them; `/health` exposes only its existing three fields.

## ADR-020 — Local persistence ownership and explicit migrations
Status: Accepted for PHX-005.

The V1 structured database is `AppSettings.data_dir / "phoenix.db"`, using
SQLAlchemy 2 and Python's built-in SQLite driver. SQLAlchemy URL construction,
lazy engines, session factories, and the single declarative `Base` belong to
`phoenix/persistence/`; core and FastAPI do not own database initialization.

Directory preparation is a separate explicit operation. Imports, settings,
URL/engine/session construction, and API construction do not connect or create
database files. Callers explicitly prepare the location and connect or migrate;
errors propagate without deleting/recreating data.

Alembic is authoritative for schema evolution. Its environment uses a caller URL
override or the PHX-004 settings-derived URL; it never prepares directories or
starts the API. The schema-empty `0001` baseline introduces only Alembic's version
tracking. Future domain tables require their own ticket and versioned migration.

## ADR-021 — Immutable in-process events and atomic bounded fan-out
Status: Accepted for PHX-006.

The core event foundation uses Pydantic envelopes with the frozen 32 event names,
UTC timestamps, UUID event IDs, caller-supplied/generated correlation strings,
and debug/info/warning/error/critical severity. Payload JSON objects/arrays are
snapshotted into read-only mappings/tuples and serialized back into JSON objects/
arrays. Publication carries information only; it never authorizes execution.

Use a memory-only asyncio bus on one event loop, with one bounded queue per
subscriber (default capacity 256). Under a bus lock, check every active queue
before enqueueing without intervening awaits. A full queue rejects the whole
publication with `EventBusFullError`; no partial delivery or silent drop occurs.
Concurrent publication order is the lock acquisition order shared by subscribers.

Subscription close is explicit and idempotent: remove the subscription, discard
its pending buffer, and wake receivers with `SubscriptionClosedError`. No hidden
workers, retries, producers, or automatic logging exist. Transport and persistence
remain outside the core bus; WebSocket streaming and durable audit are later work.

## ADR-022 — Explicit read-only hardware facts and profile evidence
Status: Accepted for PHX-007.

Hardware scanning is an explicit in-memory operation, independent of transport,
persistence, events, and model runtime. Use psutil for portable CPU counts, total
RAM bytes, and local disk capacity/free-space facts. CPU model evidence comes
from the read-only Windows processor registry or Linux `/proc/cpuinfo`; unsupported
model discovery stays unavailable. Collect no account, network, or serial IDs.

NVIDIA name, per-device VRAM MiB, and driver evidence come from one fixed
`nvidia-smi` CSV query with `shell=False` and a five-second timeout. Missing,
failed, empty, or malformed queries expose explicit issue codes without fabricated
GPU evidence or raw diagnostic logging. Windows AdapterRAM is not used.

BLAZE-6 assessment exposes six match/mismatch/unavailable checks. Any known
contradiction yields `does_not_match`; otherwise missing evidence yields
`insufficient_evidence`; all six matches yield `matches`. RAM tolerance is
22..26 GiB inclusive; VRAM must be exactly 6144 MiB on the selected matching GPU.
Normalize punctuation/case for CPU/GPU comparison, preserving substantive model
tokens. GPU identity uses exact token comparison after removing optional leading
NVIDIA/GeForce branding and compact integer GB/GiB capacity annotations (such as
6GB or 6GiB). Capacity annotations are presentation only; the selected GPU's
queried VRAM is independently checked. Ti/Super/Desktop and other substantive
tokens remain significant. Disks/free space, driver versions, and Ollama versions
are not identity checks. Hardware facts never grant authority or claim runtime
readiness.

## ADR-023 — Provider-neutral async inference contracts
Status: Accepted for PHX-010.

Adapters implement `ModelProvider` under `phoenix/models/`; agents and
orchestration consume this boundary rather than provider SDKs. The ABC owns a
validated stable provider ID and abstract async health, capabilities, generation,
and operation-scoped cancellation methods. Streaming returns an AsyncIterator
directly. Construction and contract imports start no I/O or runtime work.

Frozen Pydantic models carry ordered text messages, generated/caller-supplied
string operation IDs, model/provider identity, generic generation controls,
provider-local UTC health, explicit capability flags, response/chunk content,
optional reported token counts, and finite nonnegative metrics with explicit
units. Unknown counts/metrics remain None; totals are not inferred. Correlation
is an adapter obligation. No arbitrary metadata/options dictionaries are exposed.

Cancellation distinguishes accepted, unsupported, and not_found; acceptance is
an acknowledgement rather than proof execution has stopped. Provider failures
propagate through the small ModelProviderError/ProviderUnavailableError boundary.
Capability metadata and model output convey no authority, permissions, tool
execution, or PHOENIX readiness. Concrete adapters, timeout/stream lifecycle
behavior, discovery, routing, events, persistence, and benchmarks are later work.

## ADR-024 — Bounded direct Ollama REST adapter and scoped cancellation
Status: Accepted for PHX-011.

Use HTTPX AsyncClient directly, promoting the existing httpx>=0.27,<1 dependency
to runtime without an Ollama SDK. OllamaProvider implements PHX-010 unchanged.
Default to http://127.0.0.1:11434; configurable HTTP(S) endpoints reject credentials,
queries, fragments and malformed addresses. Owned clients retain TLS verification,
disable ambient proxy configuration, and never follow redirects. Injected clients
are caller-owned; transports supplied to the owned client close with that client.

Network work is explicit. Finite connect/read/write/pool limits default to
3/120/10/5 seconds; a 300-second total operation deadline additionally bounds I/O.
Health validates local version evidence; show metadata maps capabilities/context
conservatively. Structured-output support stays false under the current neutral
request contract. Chat content, completion, model identity (allowing only default
:latest equivalence), counts and nanosecond timings validate before mapping.
No inferred total tokens or TTFT; no prompts/raw provider errors are logged.

Generation/streaming operations register caller tasks per ID on one event loop.
No hidden workers or scheduler are created. Cancellation affects only the active
operation's I/O task or closes its suspended stream; it acknowledges a request,
not proven cessation of server computation. Streaming yields incrementally,
requires genuine done=true evidence, and closes/unregisters before its one final
chunk. Consumers that stop early must close the iterator/provider. Explicit,
bounded provider cleanup releases owned resources without shutting down Ollama.
Registry, model lifecycle, events, routing, embeddings and benchmarks stay outside
this adapter. Optional live smoke findings are separate from deterministic tests.

## ADR-025 — Neutral memory-only capability snapshots and technical filtering
Status: Accepted for PHX-012.

Key the capability registry by (provider_id, model_id), retaining immutable,
validated PHX-010 ModelCapabilities snapshots with UTC update timestamps.
Identifiers follow the existing neutral contract; no provider-specific aliases
or automatic model discovery/seeding occur. Only explicit refresh calls the
supplied ModelProvider.capabilities(). Validate the returned identity and snapshot
before replacing state; failed or cancelled refreshes preserve prior records.

For one process/event loop, mutations contain no awaits and need no lock or worker.
Concurrent refreshes query independently; the last successfully completed refresh
wins for a shared key. Listings and all technical matches sort by provider/model.
True boolean requirements demand support; false imposes no constraint. Unknown
context cannot satisfy a positive minimum. No ranking, routing, health, lifecycle,
persistence, events, benchmark evidence, or permission authority belongs here.

## ADR-026 — Global cancellation-safe heavy-generation leases
Status: Accepted for PHX-013.

ModelLifecycleManager coordinates one global heavy-generation capacity per shared
instance, defaulting to one for BLAZE-6. Explicit positive integer overrides do
not select a model or infer hardware policy. Use an asyncio BoundedSemaphore and
validated neutral provider/model/operation lease snapshots with UTC acquisition
timestamps. The manager binds to its first acquisition event loop; no workers,
provider calls, external resources, registry mutation or shutdown API are owned.

Check duplicate active operation IDs before waiting and after obtaining a permit.
Commit without another await; any pre-commit failure returns the acquired permit.
Release synchronously and idempotently, validating handle identity before removing
bookkeeping and returning capacity. Async context exit releases on exceptions and
cancellation; nested/released context entry fails. Callers explicitly release raw
leases and own inference, queue deadlines and provider cancellation. Active
snapshots sort by operation ID; available_count is capacity minus committed leases,
including slots in waiter handoff, not a promise of immediate admission. No strict
fairness or priority policy is added beyond the synchronization primitive.

Lease allocation represents PHOENIX coordination only: no physical model residency,
health, keepalive, permissions, routing, benchmarks or model-process control.
State is memory-only and disappears with the process. Share the manager instance
across callers to apply the global resource limit; abandoned leases need explicit
caller cleanup rather than silent reclamation of possibly active generation.

## ADR-027 — Separate neutral embeddings with validated immutable vectors
Status: Accepted for PHX-014.

Keep PHX-010 ModelProvider/ModelCapabilities and PHX-012/013 unchanged. A separate
EmbeddingProvider ABC exposes stable provider_id and explicit async embed().
Frozen request/response models reuse neutral identifiers and forbid extra fields.
Inputs preserve original text/order; vectors are immutable nonempty tuples of
finite numeric floats, with one vector per input and uniform actual dimensions.
No inferred dimensions, renormalization, arbitrary metadata or execution authority.

Use a self-contained OllamaEmbeddingProvider with existing HTTPX/Pydantic only;
do not import PHX-011 private helpers or refactor generation for aesthetic reuse.
Match PHX-011 URL/client security and timeout policy: loopback default, validated
HTTP(S) URL without credentials/query/fragment, TLS verification, no ambient proxy
for owned clients, no redirects, finite connect/read/write/pool 3/120/10/5 seconds,
300-second whole-operation timeout and five-second owned-client cleanup.
Clients or transports may be injected; borrowed clients remain caller-owned and
caller-configured. Callers await/cancel their embed task before provider close.
No background task, construction probe or provider-wide cancellation API.

POST /api/embed sends model, ordered input array and truncate=false. This last
control is necessary because Ollama defaults to silent truncation; exceeding the
context window must fail rather than change caller text. See the official API:
https://docs.ollama.com/api/embed. No generation/options/keepalive/dimension control.
Validate supplied model identity, permitting only ordinary default :latest
equivalence; omitted identity retains the requested model without an evidence claim.
Map reported prompt_eval_count and total_duration nanoseconds/1,000,000 only;
unknown metadata remains None. Safe neutral errors distinguish unavailable
transport from HTTP/protocol/identity/vector failures without exposing inputs,
vectors, response bodies or private URLs.

EmbeddingGemma remains the frozen candidate, not assumed installed. Discovery is
only optional read-only smoke verification. No downloads, automatic heavy-generation
leases, registry support flags, storage, RAG, ingestion, API/UI, events or benchmark
policy belongs to this boundary. Resource policy remains future evidence-based work.

## ADR-028 — Optional Colibri HTTP generation adapter
Status: Accepted for PHX-015.

Colibri remains an optional V1 runtime. ColibriProvider implements the unchanged
PHX-010 ModelProvider using existing HTTPX/Pydantic and explicit local HTTP calls;
PHOENIX does not import, embed, install, configure, launch or stop its engine.
No SDK, dependency, model download, automatic discovery, routing or benchmark
policy is added. The frozen candidate strategy stays unchanged; suitability of
an already served deep model remains PHX-016 evidence, not an adapter claim.

Default API base is http://127.0.0.1:8000/v1. Explicit HTTP(S) bases reject
credentials, queries, fragments and malformed addresses. Owned clients verify
TLS, disable ambient proxies and do not follow redirects. Optional explicit
Bearer tokens use masked storage, without environment lookup or dummy keys.
Borrowed clients remain caller-owned/configured. Finite connect/read/write/pool
defaults are 3/600/10/5 seconds, generation overall 1800, metadata overall 10,
cleanup five; longer generation limits accommodate optional deep inference.

Use GET models, GET models/{encoded entire model ID}, and POST chat/completions.
Official public API/source references:
https://github.com/JustVugg/colibri/blob/main/docs/api.md and
https://github.com/JustVugg/colibri/blob/main/c/openai_server.py.
Valid model discovery establishes only adapter streaming/cancellation support.
Tool use, structured output and vision stay false under the frozen text request
contract. Context remains None because the current public model object provides
no reliable context field. No family-name inference or Ollama :latest aliases.
Supplied model identity must match exactly; missing completion identity retains
the requested model without claiming independent identity evidence.

Map only ordered role/content, model, stream, optional temperature/max_tokens/stop.
Strict single-choice assistant responses and explicit nonnegative token counts
map to neutral models; missing totals and all generation metrics remain unknown.
Colibri performance/engine extensions are internal data, not PHX benchmark facts.
Incremental SSE strictly decodes UTF-8 lines, validates data frames and requires
[DONE] after a valid choice.
Finish/usage frames alone never prove completion. A narrowly recognized non-choice
envelope with a colibri object and optional matching model is ignored; its
internal fields are unconstrained. Unknown/malformed envelopes fail explicitly.
One empty final PHX chunk follows terminal evidence and resource/state cleanup.

Generation and streams track independent caller operations on one event loop.
Duplicate active IDs fail before state mutation; IDs may be reused after cleanup.
Cancellation affects only the active operation's I/O task or suspended response,
not the consumer's unrelated work, other requests or the server process. Acceptance
acknowledges local cancellation, not proven cessation of server computation.
No hidden workers. Consumers must exhaust or explicitly close their iterator,
including in a finally block after consumer exceptions; provider close is bounded
and closes owned clients only. Safe neutral errors never expose raw bodies,
prompts, generated text, keys or private addresses, and no such data is logged.

Registry interoperability uses explicit neutral refresh only. PHX-013 remains
the caller-owned global heavy-generation coordinator; the adapter acquires no
automatic leases and claims no physical residency/global readiness. Embeddings,
events, permissions, persistence, API/UI and orchestration remain separate.
Optional live smoke findings are reported independently of deterministic tests.

## ADR-029 — Explicit Phase-1 local runtime benchmark evidence
Status: Accepted for PHX-016.

Use frozen, extra-forbidden, JSON-serializable neutral benchmark contracts and
the fixed phoenix-phase1-text suite version 1.0.0. Four ordered harmless text
cases exercise instruction following, arithmetic, literal formatting and tiny
code output. Exact/contains scoring is literal; unscored cases remain explicit.
This is a runtime microbenchmark, not the final PHX-091 FYP evaluation dataset.

The caller supplies the provider/model and the shared PHX-013 lifecycle manager.
Bounded health/capabilities preflight records neutral evidence, with free-text
health detail removed. Each warm-up and measured operation uses one shared heavy
lease. Warm-ups stay diagnostic and never enter measured aggregates. Ordinary
failures continue; provider unavailability skips the remaining configured slots.
Success rate includes every measured slot; accuracy includes every scorable
measured slot, including failed/timeout/skipped attempts as failed scores.

Prefer neutral streaming; nonstream TTFT is unknown. perf_counter_ns measures
operation start to first nonempty text and validated neutral final completion.
UTC timestamps are metadata only. Retain explicit neutral token counts without
estimating totals; throughput is output_tokens divided by positive elapsed
seconds between first text and completion, otherwise unknown. Provider timing
and Colibri extensions are not benchmark timing authority. Output stays transient
for scoring: reports contain character count and SHA-256 of exact UTF-8 text,
never full outputs, prompts, provider payloads, private URLs or keys.

Two explicit synchronous resource boundaries use existing psutil and fixed,
read-only nvidia-smi argv with shell=False and a one-second subprocess timeout.
RAM/disk are system-wide; disk resets/missing counters are unknown. Per-GPU
identity and sampled maxima remain separate. Missing/failed/malformed telemetry
is unknown, never fabricated zero. These observations are approximate and may
include unrelated activity; sampled maxima do not prove absolute peaks or model
attribution. Diagnostic probe windows are separate from the lease-and-generation
deadline so optional telemetry cannot decide otherwise valid generation success.
Custom probes must honor the bounded synchronous probe contract.

Caller cancellation propagates after stream/scoped-cancellation cleanup and lease
release. Cleanup failure aborts explicitly rather than inventing a report.
No background sampler/generation worker or automatic provider close is added.
Bounded cleanup relies on cooperative neutral providers; local cancellation
acceptance does not prove remote computation stopped. No routing, winner choice,
discovery/download, strategy freeze, persistence, API/UI, events, embedding or
agent integration. Capability metadata is retained without claiming tool-use,
structured-output, vision, planning or general coding quality was assessed.

## ADR-030 — Immutable logical agent identities and display-only aliases
Status: Accepted for PHX-020.

AgentDefinition contains only internal_id, optional alias, and professional_role.
Use frozen, extra-forbidden Pydantic models and strict trimmed nonempty strings,
matching the existing neutral identifier semantics without importing model/runtime
subsystems. Aliases are display metadata: None is valid, duplicate aliases are
allowed, and removing/renaming aliases cannot change identity, order or role.

AgentRegistry is an immutable in-memory tuple of revalidated definition snapshots.
Reject duplicate internal IDs after whitespace normalization. get/require/contains
use only internal ID, preserving case and never mapping aliases. Valid absent IDs
return None/false or raise UnknownAgentError; invalid inputs fail validation.
list_agents returns the immutable source order. The default factory creates a
fresh registry from the fixed 30-entry roster in docs/03_AGENT_ROSTER.md, from
phoenix through verifier. No mutable global registry or mutation API is added.

Roles are descriptive metadata, never executable prompts or authorization.
PHOENIX alone is supreme; AIZEN plans, LEVI verifies without default repair,
MAHORAGA recovery is bounded and cannot bypass permissions, and KAIDO is degraded
fail-safe only. GOJO/FUJITORA advise; PermissionEngine remains authoritative.
LIGHT scenario analysis stays distinct from AIZEN planning, and LELOUCH active
system intelligence stays distinct from ITACHI silent logs/anomaly monitoring.
BROOK is the media agent; Spotify/YouTube remain tools. PUDDING memory and VEGAPUNK
knowledge/RAG responsibilities do not implement storage. Later tickets enforce
these boundaries; the registry implements no execution/security authority.

One logical identity does not imply one model/client/process. No provider/model
binding, tool/permission objects, memory storage, orchestration, routing, task
graph, handoff, runtime state, events, persistence, API/UI or background work.
Import/construction/build/read operations perform no I/O or runtime initialization.

## ADR-031 — PHOENIX finalization and caller-owned request lifetimes
Status: Accepted for PHX-021.

PhoenixOrchestrator validates the neutral registry's authoritative internal ID
phoenix. Alias and role text never select authority, collaborators or permissions.
Frozen extra-forbidden contracts contain strict nonempty IDs and meaningful text.
IDs trim outer whitespace; user/response text retains its original whitespace.
Requests default to UUID4 IDs, and supplied IDs remain stable after normalization.

The caller explicitly supplies direct or complex. Direct execution awaits one
provider-neutral DirectResponder and accepts only a revalidated DirectResponse
with matching request ID. The collaborator supplies content, never a final result
or arbitrary metadata. PHOENIX alone wraps successful direct content in a completed
PhoenixResult whose orchestrator_id is the literal phoenix. Complex execution
returns planning_required with no completion text and invokes no collaborator.
No classification, AIZEN plan or later Phase-2 behavior is implied.

Track active IDs only in memory, on one event loop, with the executing caller task.
No worker/hidden task is created. Reject simultaneous duplicate IDs before altering
state. Reject nested operations sharing one caller task so request-scoped task
cancellation cannot affect a second tracked operation. Independent caller tasks
may execute distinct IDs concurrently. Sorted immutable ID snapshots expose no
tasks or mutable dictionaries. try/finally removes state after every terminal path;
the ID may then be reused. Cross-loop execution/cancellation fails explicitly.

cancel reports accepted or not_found and cancels only the matching caller task.
Accepted acknowledges a local request, not remote computation termination.
CancelledError propagates; a responder that suppresses cancellation cannot produce
a completed result. Collaborator exceptions and malformed/mismatched responses
remain failures. Cancellation requires cooperative responders; callers own their
deadlines. No provider shutdown, process kill, model unload or persistent history.

Import, construction and inspection perform no I/O, events or runtime work.
No provider/model, tool, permission, persistence, API/UI, voice or memory coupling.
PHX-022 through PHX-028 remain unimplemented; no task-state machine or recovery
loop is added. The registry remains logical metadata, not per-agent runtimes.

## ADR-032 — Subordinate AIZEN decomposition with exact neutral contracts
Status: Accepted for PHX-022.

AizenPlanner validates registry internal ID strategist, never alias AIZEN or role
text. PHOENIX remains the sole supreme orchestrator. The planner is standalone:
PhoenixOrchestrator complex execution still returns planning_required without
calling AIZEN. AizenPlan means only a validated decomposition proposal, not user
success, execution readiness, verified completion or permission to act.

Frozen extra-forbidden PlanningRequest contains request_id and objective; PlanStep
contains step_id and objective. IDs are strict nonempty strings with outer
whitespace normalization. Objectives retain meaningful original text and are not
logged. Independent request IDs default to UUID4. PlanDraft and AizenPlan contain
ordered nonempty tuples of revalidated steps with unique normalized IDs. Caller
lists/dictionaries are snapshotted; ordering is never sorted or inferred from IDs.
Steps remain unassigned: no agents/models, dependencies, state, tool calls,
permission grants, handoff envelopes, verification results or recovery policy.

Explicit plan(request, responder=...) awaits the neutral PlanningResponder.propose
once. Require exact PlanDraft and PlanStep contracts, revalidate even constructed
or copied models, and reject extended models and mismatched correlation. Only
AizenPlanner finalizes subordinate drafts as AizenPlan(planner_id="strategist").
No PhoenixResult import or user-facing completion kind exists in this module.
Validation establishes shape/correlation, not correctness or execution feasibility
of natural-language objectives. Collaborator exceptions remain explicit failures.

Use PHX-021's narrow caller-task pattern locally without extracting shared private
infrastructure or changing the orchestrator. Bind explicit operations to one event
loop. Track independent active IDs before responder awaits; reject duplicate IDs
and nested operations sharing a caller task before changing ownership. Sorted
immutable ID snapshots expose no tasks or mutable dictionaries. finally cleans
state after success, failure or cancellation; cleaned IDs can be reused.

cancel targets only the matching caller task and reports accepted/not_found.
CancelledError propagates. Recorded local cancellation prevents finalization even
if a responder suppresses cancellation or clears the task counter. Acceptance
does not prove remote computation stopped; responders must cooperate and callers
own deadlines. No hidden workers, global shutdown, process kill or model unload.

Import/construction/inspection perform no I/O or runtime work. No provider/model,
tool, permission, persistence, events, memory, API/UI or voice binding. Routing,
task graph/state, handoffs, LEVI, MAHORAGA and KAIDO remain PHX-023 through PHX-028.

## ADR-033 — Deterministic normal-specialist routing from explicit evidence
Status: Accepted for PHX-023.

Use 25 explicit RoutingDomain values and an immutable domain-to-internal-ID map
for the documented normal specialists. Validate the mapped ID in a snapshotted
PHX-020 AgentRegistry. Aliases and professional-role text never select authority;
missing IDs fail with MissingRouteAgentError, without control-role fallback.
phoenix, strategist, verifier, adaptive_recovery and failsafe are excluded.

Frozen extra-forbidden RoutingRequest carries a generated/caller-provided route_id,
domain, nonempty ordered candidate tuple, unique provider-health tuple, PHX-012
CapabilityRequirements and descriptive PermissionRisk P0-P5. Identifiers are
strict nonempty strings with outer whitespace normalization. Candidates contain
only provider_id, model_id and strict resource_eligible bool. Health contains only
provider_id and ProviderHealthStatus. Reject duplicate normalized candidate pairs
and duplicate provider IDs, including contradictory evidence. Snapshot nested
inputs and revalidate copied/constructed data at the routing boundary.

Reuse ModelCapabilityRegistry.matching without duplicating technical rules:
false requirements impose no constraint; unknown context cannot satisfy a positive
minimum. Select the first caller-ordered candidate with resource eligibility,
explicit HEALTHY provider evidence and an exact matching capability record.
DEGRADED, UNAVAILABLE, missing health/records and failed requirements are
ineligible. No candidate yields NoEligibleModelError, never a fabricated route.
The caller-owned capability registry remains current in memory; later explicit
registrations affect later routes. RouteDecision detaches requirements/capabilities
and checks domain/agent and provider/model identity agreement.

Caller ordering is temporary policy input, preserving the model-strategy freeze
boundary. No provider preference, permanent model winner, benchmark score or model
discovery is introduced. Health freshness and resource facts belong to the caller;
the router neither probes nor reserves capacity. PHX-013 remains authoritative
for heavy-generation capacity. Tool-use support and P0-P5 are technical/descriptive
data, never tool execution or permission approval, denial or grants.

Routing is synchronous, read-only and deterministic for the same request/evidence
on one process/thread. Construction/import/routing do no I/O, provider calls,
refresh, inference, events, persistence or background work. No cancellation API,
locks, workers or lifecycle leases are needed. PHOENIX remains supreme; the router
does not invoke the orchestrator, planner or specialists. Task graph, handoffs,
LEVI, MAHORAGA and KAIDO remain PHX-024 through PHX-028.

## ADR-034 — Immutable source-ordered DAGs and canonical task-state evidence
Status: Accepted for PHX-024.

TaskStepDefinition holds only strict normalized step/owner IDs, meaningful original
objective/expected-output/completion text, ordered dependency/input-reference tuples
and a finite positive numeric timeout (never bool). These are descriptive metadata;
input references are opaque and no timeout/completion rule is executed. TaskGraph
contains only task_id (generated UUID4 or caller-preserved) and nonempty ordered
steps. Frozen extra-forbidden contracts snapshot nested definitions by value,
including copied/constructed inputs; duplicate normalized IDs/dependencies,
self-dependencies, missing edges and cycles fail without repair.

Validate every owner through a revalidated PHX-020 AgentRegistry. Default graph
construction uses the canonical roster; an explicit custom registry uses Pydantic
model_validate/model_validate_json context={"agent_registry": registry}. Custom
owner round-trips need that same context. Registry objects/aliases/roles are not
attached to graphs. Any legitimately registered internal ID may structurally own
a step, including control roles, without creating runtime or permission authority.

Declared source order is the deterministic tie-break. Use iterative Kahn traversal
with a min-heap of source positions across all currently available nodes, including
newly ready nodes; do not alphabetize IDs or emit nondeterministic set order.
Roots retain source order. step() performs strict normalized exact lookup, returning
None for a valid absent ID. ready_step_ids() accepts ordered lists/tuples, rejects
invalid/unknown/duplicate completed IDs and returns uncompleted nodes whose direct
dependencies are supplied as completed, in source order. This query trusts caller
completion evidence; it neither validates execution history nor schedules work.

TaskState has exactly eleven lowercase values: created, planning, ready, running,
waiting_permission, verifying, completed, failed, recovering, degraded, cancelled.
An immutable explicit table defines the ticket's 27 legal transitions. completed,
degraded and cancelled are terminal; self-transitions and unlisted edges fail.
TaskStateMachine requires a strict task ID and starts created with empty history.
Read-only properties expose task_id/state/history/is_terminal; can_transition is
pure and transition returns one immutable TaskTransition per successful change.
Validate the target/edge and prepare its record and history before a single
state/history snapshot replacement. Any failure preserves both state and history.
Records hold only task_id/from_state/to_state/UTC-normalized aware occurred_at;
append order, never wall-clock ordering, determines history sequence.

One process/thread, synchronous, in-memory only; no distributed/thread-safe claim,
locks, workers, background tasks, timers, events or persistence. State values are
caller-reported evidence: waiting_permission/resumption grant nothing; verifying
does not invoke LEVI or prove verification; recovering performs no MAHORAGA work;
degraded does not activate KAIDO. A completed marker alone does not establish
user-facing execution/verification success. PHOENIX remains supreme, with the
orchestrator, planner and router unchanged/uninvoked. Model/provider/lifecycle,
tools, PermissionEngine, handoffs, recovery/fail-safe, API and desktop stay separate.

## ADR-035 — Immutable graph-correlated handoff data without runtime authority
Status: Accepted for PHX-025.

HandoffEnvelope contains only the eleven documented evidence fields: task/step IDs,
source/target internal IDs, objective, input_refs, constraints, expected_output_schema,
timeout_seconds, permission_scope and verification_required. Frozen extra-forbidden
contracts require strict nonempty normalized IDs/refs/scopes, meaningful original
objective/schema/constraint text, finite positive numeric timeout (never bool or
numeric strings), and strict verification bool. Ordered lists/tuples are snapshotted;
sets and scalar containers fail. Empty refs/constraints/scopes are valid. Constraints
and scopes reject duplicates after outer-whitespace normalization, without changing
case or constraint formatting. Input refs may repeat and are never dereferenced.

HandoffBuilder snapshots an exact PHX-020 registry at construction, then revalidates
the entire exact PHX-024 graph against that same registry before selecting a step.
All graph owners must remain registered, including owners on unselected steps.
Derive task_id, step_id, target_agent_id, objective, input_refs and timeout_seconds
from the selected step/graph; the normal API exposes no conflicting overrides.
Only source ID and handoff-specific descriptive metadata are supplied by the caller.
Unknown steps/agents fail explicitly, without alias/role fallback or replacement.
Any registered internal ID may structurally participate, including control roles;
this makes no operational authority claim. Normalized source and target must differ,
including during standalone envelope validation. Standalone envelopes validate shape
and distinctness; only builder construction establishes graph/registry correlation.

Revalidate constructed/copied public models by stored field values, including fields
that model_copy can inject but model_dump can omit. Reject extended/subclass graph,
step, registry, definition and envelope payloads at the boundary; retain no mutable
caller containers, runtime objects, callbacks or arbitrary metadata in envelopes.
Registry snapshots are private builder inputs and are never attached to envelopes.

Constraints and expected_output_schema are opaque untrusted descriptions, never
executed, compiled, interpreted as JSON Schema or automatically logged. Requested
permission_scope is neither consent nor approval/grant/denial; P3/P4/P5-style text
creates no authority and future PermissionEngine policy remains authoritative.
verification_required describes a later verification obligation, never LEVI execution
or proof of verification; false likewise claims no successful verification.

Construction/build is synchronous, read-only and memory-only. It changes no graph,
dependency/readiness evidence, task state or history, and enforces no timer or
scheduling policy. No dispatch, agent chat, inference, provider/model selection,
lifecycle lease, tool/permission execution, events, persistence, API or desktop path.
PHOENIX remains supreme; orchestrator, planner, router and graph/state implementations
remain unchanged/uninvoked. LEVI, MAHORAGA and KAIDO runtimes stay PHX-026 through 028.

## ADR-036 — Graph-correlated LEVI evidence with a separate verification gate
Status: Accepted for PHX-026.

LEVI's authoritative internal ID is verifier; aliases/role descriptions are metadata.
LeviVerifier snapshots an exact PHX-020 AgentRegistry at construction and requires
verifier. Explicit verify() revalidates the entire exact PHX-024 TaskGraph and its
steps against that snapshot, then the exact PHX-025 HandoffEnvelope. Require matching
task ID, existing selected step, target/owner, objective, ordered input references
and timeout metadata, and registered source/target IDs. Derive request IDs/objective
and completion rule from the graph, expected_output_schema from the handoff, and
candidate_output from the caller; expose no conflicting request-field overrides.

VerificationRequest, VerificationCheck, VerificationDraft, VerificationResult and
CancellationResult are frozen, extra-forbidden neutral values. IDs are strict,
nonempty, outer-whitespace-normalized strings; meaningful text preserves formatting.
Request construction generates UUID4 by default. Runtime omitted/None verification_id
requests generation; all other supplied IDs validate strictly. A directly constructed
request with verification_id=None is invalid. Candidate/schema/criterion/evidence
text and input references remain untrusted data, never interpreted or dereferenced.

Only explicit verify() awaits VerificationResponder.verify(request), exactly once.
The responder returns only a correlated verification ID and nonempty ordered checks
(check_id, criterion, strict passed bool, evidence_summary). Revalidate draft/checks
by stored field values, including copied/constructed fields omitted by model_dump;
reject extended models, extras, malformed booleans, duplicate normalized check IDs
and correlation errors. Detach caller containers. The runtime derives passed iff
every validated check passes; otherwise failed. Result verifier_id is exactly verifier,
and result validation rejects a verdict contradicting its checks. A pass establishes
only the supplied checks, never independent universal correctness or authentication.

verification_requirement_satisfied() is a pure obligation check. Missing evidence
or failed evidence cannot satisfy required verification. Any supplied result must
match handoff task/step/target, even for an optional obligation. With no obligation,
missing evidence or valid correlated evidence satisfies this requirement only;
a correlated failed result does not create a requirement or establish overall success.
No pass grants permissions or PHOENIX user-facing completion. PermissionEngine remains
authoritative; P3/P4/P5 handoff scope text creates no authority.

One verifier instance belongs to one process/event loop. Distinct IDs may coexist;
active inspection returns a sorted immutable tuple, never task objects. Reject active
duplicates without disturbing the original, permit reuse after finally cleanup and
reject nested operations sharing one caller task. No hidden worker tasks. Cancellation
targets the matching caller task, with only accepted/not_found acknowledgements.
Record local cancellation independently of the task cancellation counter; even a
responder that suppresses CancelledError and clears that counter cannot return a
verification result after local cancellation. Exceptions/cancellation propagate after
cleanup. Cancellation remains cooperative; accepted does not prove external compute
stopped. Callers own deadlines; descriptive graph timeout starts no automatic timer.

No retry, repair, re-plan, MAHORAGA/KAIDO activation, task-state transitions, events,
logging, persistence, API, desktop, tools, permission evaluation, provider/model calls
or lifecycle leases. PHOENIX remains supreme. PHX-020 through PHX-025 implementations
remain unchanged; orchestration/event integration is deferred, recovery belongs to
PHX-027 and degraded/fail-safe behavior to PHX-028.

## ADR-037 — Failure-preserving bounded recovery proposals without execution
Status: Accepted for PHX-027.

MahoragaRecovery requires authoritative internal ID adaptive_recovery in an exact
snapshotted AgentRegistry. Aliases/role metadata cannot supply authority. Revalidate
the entire exact TaskGraph/steps against that registry and the exact HandoffEnvelope;
require task, selected step, target/owner, objective, ordered input references and
timeout correlation plus registered source/target IDs. Derive request task/step/subject
IDs without conflicting overrides. All descriptive failure/rationale/adjustment text
remains untrusted data; references are opaque and never dereferenced.

Frozen extra-forbidden RecoveryRequest has nine fields: recovery/task/step/subject IDs,
trigger, failure_summary, attempts_used, max_attempts and max_replan_steps. Four typed
triggers are execution_failure, verification_failure, timeout and resource_unavailable.
Vocabulary accepts strings/their enum members without byte coercion; IDs are strict,
nonempty and outer-whitespace-normalized. Meaningful text retains formatting. Omitted/
None runtime recovery_id generates UUID4; directly supplying None to the request fails.
Verification failure requires exact revalidated correlated FAILED VerificationResult
evidence, including consistent checks. Reject passed/mismatched/extended/forged values.
Other triggers require no verification evidence and reject an irrelevant supplied result.
Neither input verification evidence nor its required-verification gate is modified.

Attempt evidence is strict integers: used >= 0, max_attempts > 0, max_replan_steps > 0;
bool/coerced numeric values fail. No universal retry default. Used >= max returns
exhausted without responder invocation, active registration or proposal creation.
Otherwise invoke RecoveryResponder.propose(request) once and validate one proposal.
A valid proposal records attempt_number = used + 1 <= max_attempts. The caller owns
carrying counts forward; this boundary has no persistent/global retry ledger or loop.
Duplicate active IDs reject before either eligible or exhausted finalization.

RecoveryDraft contains recovery_id, action, rationale and only six typed optional
fields. None means absent. Action-specific fields are exclusive:
retry_same has none; adjust_parameters requires nonempty ordered meaningful strings;
switch_tool requires one neutral tool ID; switch_model_provider requires both provider
and model IDs; switch_agent requires a different registered internal ID; replan_subgraph
requires nonempty ordered step IDs. Reject irrelevant fields and duplicate normalized
sequence entries, preserving order/meaningful formatting. Replan IDs must all exist in
the input graph, include the failed step and number at most max_replan_steps. This
separate finite size budget does not change attempt accounting. Availability, eligibility
and execution policy remain later orchestration responsibilities; replacements grant
no authority, instantiate nothing and perform no discovery or routing.

Revalidate exact stored model fields, including constructed/copied fields omitted by
model_dump; reject subclasses, injected extras, malformed optional values, action-field
contradictions and recovery-ID mismatch. Snapshot nested proposal containers by value.
RecoveryResult preserves failure trigger/summary, correlated IDs, literal recovery_agent_id
adaptive_recovery, budgets and status proposed/exhausted. Proposed requires its correlated
proposal and exact increment; exhausted requires used >= max, proposal=None and
attempt_number=None. Neither status establishes repaired work, verified success,
permission or PHOENIX completion. Exhaustion activates no KAIDO/degraded behavior.

One process/event loop, with caller-owned eligible operations. Sorted immutable active
ID inspection exposes no tasks. Distinct IDs coexist; duplicates preserve the original;
cleaned IDs may be reused; nested eligible calls sharing a task and cross-loop use fail.
No hidden workers/tasks. cancel targets only the matching caller task and returns
accepted/not_found. Independent local cancellation recording prevents finalization even
when a responder suppresses CancelledError and clears its task counter. finally cleans
every terminal path; responder failures, invalid evidence and cancellation remain explicit.
Cancellation is cooperative; acceptance does not prove external compute stopped. Callers
own deadlines; graph timeout metadata creates no automatic timer.

No retry, parameter application, tool/model/provider/agent switching, AIZEN replanning,
router/LEVI invocation, task-state mutation, permission/scope expansion, events, logging,
persistence, API or desktop integration. PHOENIX remains supreme. PHX-020 through PHX-026
implementations are unchanged; future orchestration owns execution/state/events, and
PHX-028 owns degraded/fail-safe decisions.
