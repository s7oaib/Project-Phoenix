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

## ADR-038 — Explicit least-scope degraded policy without execution authority
Status: Accepted for PHX-028.

KaidoFailSafe requires authoritative internal ID failsafe in an exact snapshotted
AgentRegistry. KAIDO is removable display metadata; aliases/role text confer no
authority. activate(request) is synchronous and pure, with no hidden work or state
transition. PHOENIX remains the sole supreme orchestrator and completion authority.

Five explicit caller triggers are primary_model_provider_unavailable,
resource_pressure, deep_provider_unavailable, critical_subsystem_degradation and
recovery_exhausted. Never infer conditions from untrusted reason text or perform
health/resource probes. Every decision has literal failsafe identity and degraded
mode; normal/healthy/high-quality activation is excluded.

Four essential descriptive categories are basic_conversation, diagnostics,
recovery_guidance and safe_limited_tool_routing. Require a nonempty unique ordered
capability tuple and return precisely that requested tuple, preserving order.
No hidden capability expansion. Safe limited tool routing neither selects/executes
tools nor grants permission. Future execution still requires ToolRegistry and
deterministic PermissionEngine; P4 confirmation and P5 blocking remain unchanged.

Frozen extra-forbidden request/decision contracts have five/seven fields. Strict
nonempty IDs normalize outer whitespace; request IDs default independently to
UUID4. Meaningful reason formatting remains intact and is never logged/executed.
Revalidate exact stored model fields, including copied/constructed injected fields,
reject subclasses and snapshot caller containers and nested recovery evidence.
recovery_exhausted alone requires an exact PHX-027 RecoveryResult revalidated by its
own contract: exhausted, used >= max, positive finite integer budgets, no proposal
and no attempt number. Preserve its recovery ID; other triggers reject recovery
evidence and carry no recovery reference. Proposed/contradictory evidence fails.
Exhaustion remains failure, never repaired work, verified success or completion.

Explicit immutable mode/trigger/reason/capabilities/recovery reference satisfy
INV-022 without event production. No provider/model work, tool/permission calls,
registered agent execution, LEVI/MAHORAGA/AIZEN/router/orchestrator invocation,
task-state mutation, persistence, API, frontend, voice or startup integration.
Those integrations remain later work; PHX-020 through PHX-027 remain unchanged.

## ADR-039 — Neutral memory evidence and the first versioned domain table
Status: Accepted for PHX-030.

MemoryType has exactly working, episodic, semantic, user, project and agent.
MemoryRecord is a frozen, extra-forbidden fourteen-field contract: id, type,
content, source_type, source_ref, scope, confidence, importance, created_at,
updated_at, last_accessed_at, expires_at, embedding_ref and metadata. Revalidate
exact stored model fields, including copied extras; reject extended records.
IDs are strict nonempty outer-whitespace-normalized strings, with independent
UUID4 defaults. Content retains meaningful original formatting and remains data.
source_type and scope are required extensible normalized strings; optional source
and embedding references are opaque normalized strings, without foreign keys,
dereferencing, vectors or verified-citation claims.

Confidence/importance are explicit ordinary finite int/float evidence normalized
to floats in inclusive [0,1], rejecting booleans/coerced strings. Creation and
update timestamps are required caller values, with no clock defaults. All logical
datetimes must be aware and normalize to UTC; aware ISO representations support
serialization. Updated, accessed and expiry timestamps cannot precede creation.
Wall-clock evidence is not a monotonic version counter. UTCDateTime binds aware
datetimes as naive UTC in SQLite's DateTime representation and restores UTC
awareness on reads, preserving microseconds. Naive persistence binds fail.

Metadata defaults independently to an empty JSON object with string keys and
finite JSON values. Recursively detach and freeze maps/arrays into read-only maps
and tuples; serialize back to ordinary JSON objects/arrays. Reject arbitrary
runtime objects, bytes, sets, callbacks, nonfinite numbers and cycles. Metadata
and content confer no executable, permission or trust authority.

MemoryRow intentionally registers memories in PHX-005's shared Base; registration
and construction perform no I/O. Python memory_type/metadata_json map to database
type/metadata, preserving DeclarativeBase.metadata. Exactly fourteen columns,
named id primary key, checks for six types, inclusive score bounds and valid JSON
object metadata, plus four ordinary indexes on type/scope/created_at/expires_at.
No content uniqueness, silent overwrite or reconciliation. ORM mapping is storage,
not admission validation; future callers validate domain records explicitly.

Alembic 0002_memory_schema follows unmodified schema-empty 0001 and creates only
memories and its constraints/indexes. A narrow model import registers migration
target metadata without connecting or running migrations on app startup. Real
temporary lifecycle tests preserve 0001's empty domain schema and verify upgrade,
downgrade and re-upgrade, inspection and mapping agreement. PHX-005 tests identify
the new head while explicitly targeting 0001 for baseline-only assertions.

PUDDING service/admission/semantic-secret rejection/conflict policy, document
ingestion, VectorStore, embedding generation, retrieval/context and citation
rendering remain PHX-031 through PHX-035. No secret-specific fields/type/storage,
logging, events, API, frontend or agent/provider/tool/permission execution.
Schema validation cannot reliably identify every secret inside arbitrary text.

## ADR-040 — Admitted identity-scoped memory and post-commit observability
Status: Accepted for PHX-031.

PuddingMemoryService requires the neutral AgentRegistry internal ID memory.
Aliases and professional roles are display metadata, never identity or authority.
Borrow an explicit synchronous sessionmaker, EventBus and mandatory admission
policy. Construction/import opens no session, connection, file or subscription,
performs no migration and starts no task/thread. Callers own schema setup,
dependency lifetimes and authorization; the service never disposes their engine.

Every create/replace exact-revalidates a detached PHX-030 MemoryRecord before
calling the injected synchronous MemoryAdmissionPolicy.evaluate exactly once.
Require an exact frozen extra-forbidden MemoryAdmissionDecision with matching
memory_id and exactly accepted/rejected_secret/rejected_policy. Only accepted
reaches session acquisition. Policy failures, malformed/corrupt decisions and
rejections fail explicitly without content/metadata disclosure. No allow-all
default, semantic detector, model call or secret-specific storage is supplied.
INV-018 enforcement depends on the trusted policy classifying secrets correctly;
the broader secrets implementation remains PHX-045. Admission grants no permission.

Create inserts one exact new ID, rejecting duplicates including primary-key races,
without overwriting, upsert, content deduplication or contradiction reconciliation.
Get uses the exact ID and returns a detached revalidated record or None. Frozen
MemoryListQuery supports only exact type/scope/source_type, strict limit 1..500
(default 100) and offset >= 0, ordered created_at ASC then id ASC. Return immutable
tuples; administrative inspection is not semantic retrieval. Get/list preserve
last_accessed_at and expose expired rows without timers, deletion or hidden filters.

Replace requires an existing exact ID and admission again. Keep created_at equal
to stored creation and updated_at >= the stored update; equality is valid because
wall-clock timestamps are not unique versions. Replace only that row, with no
content-based matching or merge. Delete commits removal of only the exact ID and
returns True/False for present/absent. Neither operation invents an update/delete
event. All database operations use short explicit session scopes; failures roll
back/close through SQLAlchemy and expose safe errors without SQL or private paths.
Malformed ORM JSON/timestamps and invalid domain reconstruction fail explicitly.

Create commits and closes its session before constructing and awaiting exactly
one canonical memory.created event: INFO, agent_id memory, correlation_id record
ID, no task ID, and payload only memory_id/memory_type. Frozen MemoryCreateResult
contains a detached record and published/rejected_full evidence. EventBus full
preserves the durable row, reports rejected_full and never retries. Cancellation
propagates and preserves already committed memory. Other publication failures
raise safe MemoryEventError explicitly describing the committed row; no fake
rollback, durable outbox or automatic replay is provided. Event evidence grants
no execution authority. EventBus event-loop ownership remains caller-controlled.

Reuse the unchanged PHX-030 domain/ORM and Alembic head 0002_memory_schema. No
schema/dependency changes, automatic chat extraction, API/frontend integration,
provider/model/lifecycle work, ToolRegistry bypass or permission implementation.
Document ingestion, VectorStore, retrieval/context and citations remain PHX-032
through PHX-035; PHOENIX remains supreme and PUDDING remains subordinate.

## ADR-041 — Admitted, bounded document ingestion with atomic provenance
Status: Accepted for PHX-032.

DocumentIngestionService requires AgentRegistry's internal ID knowledge_rag.
VEGAPUNK aliases remain display metadata and confer no authority. Borrow an
explicit sessionmaker, EventBus, mandatory synchronous admission policy and
explicit limits. Import/construction performs no I/O, migration, subscription or
worker startup. Caller owns schema setup, dependencies and permissions.

DocumentSource holds caller-supplied nonempty bytes and an opaque source_ref;
ingestion never opens or resolves that reference. Strict source identities,
aware UTC timestamps and detached finite JSON metadata remain untrusted data.
The explicit parser collaborator runs once after the source-byte gate. Exact
revalidation rejects extended, forged, mismatched or malformed parsed evidence.
Concrete binary parsers and routing remain PHX-072; no parser dependency/default.

Bound total parsed Python characters before deriving source-ordered windows.
Explicit chunk_size and chunk_overlap advance by size minus overlap, stopping
when a window reaches the segment's end. Skip whitespace-only windows. Within
one document, compare NFKC/whitespace-collapsed/casefolded window text, retaining
the first original content, locators and metadata. Bound the retained chunk count
and assign contiguous indices from zero. No cross-document deduplication. SHA-256
hashes exact source bytes and each retained chunk's UTF-8 bytes. UUID5 in
NAMESPACE_URL names a canonical JSON array of document ID, final index and exact
content hash, giving stable IDs without ambiguous delimiter concatenation.

Required DocumentAdmissionPolicy.evaluate(document, chunks) receives detached
frozen snapshots once before session acquisition. Require an exact correlated
DocumentAdmissionDecision and accepted/rejected_secret/rejected_policy verdict.
Reject secret/policy evidence, malformed decisions and policy failures before
persistence. No allow-all default or semantic secret detector. INV-018 depends on
the trusted policy correctly classifying secrets; broader handling is PHX-045.
Admission grants no permission and policy mutation cannot change stored evidence.

Alembic 0003_document_ingestion follows immutable 0002_memory_schema and adds only
documents/document_chunks, named constraints and five indexes. Reuse shared Base
and UTCDateTime without PUDDING service/domain calls. metadata_json maps SQL
metadata. Source references/hashes are not unique. A short transaction checks the
document ID, flushes the parent inside that same transaction, inserts all chunks,
commits and closes before publication. Any pre-commit database failure rolls back
all rows. Duplicate durable IDs, including SQLite primary-key races, fail without
overwrite. Connection-level SQLite foreign-key enforcement remains caller-owned;
tests enable it and independently prove the foreign key and full atomicity.

Attempt exactly one canonical document.indexed event after commit: INFO,
agent_id knowledge_rag, correlation_id document ID, no task ID, and payload only
document_id/chunk_count. Full queues return rejected_full with rows intact; no
retry or partial delivery. Post-commit cancellation propagates with durable rows.
Other publication failures explicitly report committed rows through safe errors.
No durable outbox, replay or fabricated rollback is supplied.

One service belongs to one process/event loop. Caller tasks own parsing; no hidden
task/thread. Sorted read-only active IDs expose progress, duplicate active IDs
fail, and cancel targets only the specified caller operation. An independent
cancellation flag prevents a parser swallowing CancelledError or clearing the
task counter from persisting/finalizing. Finally removes each active registration
on every exit; callers own deadlines and parser cooperation.

Historical memory tests explicitly retain their 0002 schema expectations; only
current-head assertions advance to 0003, including PHX-031. New full-head mapping
and temporary upgrade/downgrade/re-upgrade tests prove memory preservation and
document constraints. No provider/model/tool/permission/API/frontend execution,
PUDDING call, vectors, retrieval/context or citations. PHX-033 through PHX-035
remain separate work; PHOENIX remains supreme and document evidence subordinate.

## ADR-042 — Portable vector evidence and bounded exact SQLite cosine
Status: Accepted for PHX-033.

VectorStore is a synchronous provider-neutral ABC with add/add_many/get/delete/
search. SqliteVectorStore borrows the caller's sessionmaker and requires a strict
positive max_search_candidates. Import/construction opens no connection, file,
subscription or worker. Caller owns schema setup, permissions and engine lifetime.
No sqlite-vec, FAISS, Chroma, NumPy or SciPy dependency/extension is introduced;
sqlite-vec remains a possible optimized adapter behind the same boundary.

Frozen extra-forbidden VectorRecord has exactly nine fields: id, source_kind,
source_id, source_hash, provider_id, model_id, dimensions, vector and created_at.
Identifiers normalize only outer whitespace and preserve case; source_kind is
extensible. SHA-256-shaped source hashes and source IDs are opaque evidence,
never source existence, freshness or authority. Finite ordinary numbers become
immutable floats; reject booleans, malformed/empty/zero vectors and dimension
disagreement. Creation is explicit aware UTC evidence, without a clock default.
Exact snapshots reject subclass extensions, copied extras and forged values.
No source text/metadata/secret field or semantic secret detector. Callers must
generate vectors only after the applicable trusted admission/security boundary.

VectorBatch snapshots ordered exact records, requiring a nonempty batch, unique
IDs and source_kind/source_id/provider_id/model_id keys, plus one dimension for
each exact provider/model pair. Add is insert-only; duplicate durable identity
fails without overwrite. One explicit BEGIN IMMEDIATE transaction reserves the
SQLite writer before conflict/dimension reads, inserts all records and commits.
This serializes cooperating writes' dimension checks. Internally inconsistent
stored dimensions fail; manual writes can still corrupt evidence. Constraint/
commit failures roll back the whole batch, preserving earlier rows. No retries.
Get returns a detached revalidated record or None. Delete uses only the exact ID
and returns actual deletion evidence, without reconstructing malformed vectors.
Re-embedding requires explicit delete followed by add; no replace/update API.

VectorSearchQuery requires provider/model/vector/strict limit 1..100 and optional
exact source_kind/ordered unique source_ids, capped at 2000 IDs. None means no
source-ID filter; an empty supplied tuple selects no sources. Validate the entire
selected provider/model dimension space before source filtering; disagreeing
stored dimensions fail even outside a selected source subset. Require query
dimensions to agree when filtered candidates exist; absent candidates return an
empty tuple. No inferred scope, source lookup, freshness check or query text.

Use an explicit SQLite read transaction so dimension/count/vector reads share one
snapshot, including under concurrent WAL writes. Count exact filtered candidates
before loading vectors or calculating similarity. Overflow raises
VectorSearchLimitError, without approximation/truncation; independently bound
fetch by that validated count. Revalidate every candidate before returning any
result; never silently skip corruption. Use standard-library scaled temporary
unit arithmetic, hypot and fsum for cosine, handling finite extreme/subnormal
values without changing stored vectors. Reject nonfinite/grossly out-of-range
scores; clamp only overshoot within 1e-12. Sort score DESC then record ID ASC and
apply limit after scoring the complete bounded set. VectorMatch has seven fields:
record_id, source_kind, source_id, source_hash, provider_id, model_id and score.
Similarity is low-level evidence, not truth, verification or final relevance.

EmbeddingRecordRow shares Base and the existing UTCDateTime convention. Revision
0004_embedding_records follows unchanged 0003 and adds only embedding_records:
nine columns, JSON vector arrays, named PK/source-model uniqueness/dimension/
array checks and three exact ordinary indexes. No source foreign keys, virtual
tables or extensions. Domain reconstruction owns finite/nonzero/shape checks.
Safe errors suppress raw SQL, vectors, private paths and exception details; no
logging, database repair or borrowed-engine disposal. Historical tests keep their
0001/0002/0003 schema semantics; only current-head assertions advance to 0004.

No embedding/model/provider invocation, automatic indexing, EventBus, source
resolution/mutation, PUDDING call, memory embedding_ref update, ingestion change,
API/frontend, permissions/tools, reranking/context or citations. PHOENIX remains
supreme. PHX-034 owns retrieval/freshness/query embedding/context policy and
PHX-035 citations. This bounded exact adapter is a portable correctness baseline,
not a large-scale ANN engine; callers own synchronous execution and resource scope.

## ADR-043 — Scoped read-only retrieval and bounded structured context evidence
Status: Accepted for PHX-034.

RetrievalContextService requires AgentRegistry's internal ID knowledge_rag.
Aliases and role text confer no authority. PHOENIX remains supreme; PUDDING
retains admission/persistence ownership. Borrow an explicit sessionmaker and
provider-neutral VectorStore. Construction/import performs no I/O, provider
calls, event work or worker startup; callers own authorization, schema,
dependency lifetimes, deadlines and upstream secret admission.

Frozen extra-forbidden contracts exact-revalidate stored values and nested
evidence, rejecting subclasses, copied extras and forged values. Requests carry
explicit provider/model, meaningful original query text, aware UTC as_of,
ordered unique memory scopes/types and document IDs, caller-declared authority,
candidate limit 1..100, similarity threshold, ranking policy and context budget.
At least one source family must be scoped. Authority IDs must be requested
documents; metadata, paths, filenames and text cannot self-promote a source.

Resolve exact eligible IDs read-only, ordered by ID, fetching at most 2001 per
family to enforce the 2000-ID bound without truncation. Exclude memories whose
expiry is <= as_of, without deleting or touching last_accessed_at. Close that
source session before awaiting exactly one supplied EmbeddingProvider.embed
for only query_text, correlated by retrieval_id/provider/model. Require exact
EmbeddingResponse, one ordered finite nonzero vector and truthful dimensions.
No provider discovery/routing, generation, fallback, retry or lifecycle lease.

Search at most once per nonempty eligible memory/document_chunk family through
VectorStore.search with exact provider/model/vector/IDs and the private request
candidate bound. Exact-revalidate all returned matches, even below threshold,
rejecting correlation/scope/duplicate-ID violations and excess candidates. No
adapter-specific dependency, vector writes or interpretation of future kinds.

Before context inclusion, resolve current source rows in a new short explicit
SQLite read snapshot and reconstruct existing MemoryRecord/DocumentRecord/
DocumentChunkRecord contracts. Reject corruption and noncanonical source
bindings safely. Recheck memory scope/type/expiry and document membership.
Memory freshness hashes exact UTF-8 content; document freshness compares the
current persisted chunk content_hash. Missing source/parent is orphaned; changed
hash is stale. Exclude both without repair or mutation. Aggregate diagnostics
also count source eligibility changes, with exact accounting across similarity,
stale/orphan/ineligible, ranked, budget-excluded and selected counts.

Use exactly the ticket's weighted relevance/recency/importance memory mean,
as_of-based nonnegative age and base-two half-life decay. Temporary weight
scaling avoids overflow while preserving the same formula for finite weights.
Confidence remains raw evidence, without a hidden weight. Documents use only
(similarity+1)/2. Merge authoritative documents first, then rerank descending,
similarity descending, source kind, source ID and vector ID ascending.
Authority is knowledge precedence only; all retrieved text remains untrusted
executable input and grants no instruction, tool or permission authority.

Caller-owned tokenizer-neutral budgets require max_items 1..100 and max_chars
1..1000000. Select complete original texts in rank order; skip any nonfitting
item and consider later smaller items. Structured items retain source identity,
hash, reference, document locators/parser/timestamp/index and memory type/scope/
confidence. No prompt concatenation, role wrappers, citation formatting or
truth/verification/task-completion claim. PHX-035 owns citation presentation.

One service belongs to one process/event loop. Caller tasks own active IDs;
reject duplicates and nested ownership, expose sorted immutable ID inspection,
and clean state in finally on every path. cancel acknowledges accepted/not_found
and targets only that caller task. An independent cancellation flag prevents
search, later source resolution and finalization after a provider suppresses
CancelledError and clears task counters. Cancellation is cooperative, not proof
remote inference stopped; synchronous bounded reads/search need caller resource
scope. No hidden tasks, cache, persistent query history or background re-indexing.

No source/vector/schema mutation, migration, events, generation, tool/permission
runtime, API or frontend integration. Source snapshots do not span the embedding
provider, VectorStore transaction and all source phases; freshness is evidence
for the final resolved source snapshot, not distributed atomicity or source truth.

## ADR-044 — Pure provenance binding and deterministic plain-text citations
Status: Accepted for PHX-035.

ProvenanceCitationService consumes only the exact immutable PHX-034
RetrievalContextResult. Require AgentRegistry internal ID knowledge_rag;
removing/renaming VEGAPUNK does not change behavior or grant runtime authority.
Construction retains no collaborators or mutable operation state. PHOENIX
remains supreme; callers own authorization and upstream secret admission.

Exact-revalidate stored result, nested ContextItem and diagnostics values,
rejecting subclasses, copied extras and contradictions before presentation.
Preserve retrieval_id/provider_id/model_id/UTC as_of and every selected item's
order. Create exactly one detached frozen CitationEntry per item, without
deduplication, fabrication, enrichment or retrieved-content duplication.
Empty results produce empty citation and render tuples.

Citation ordinals are strict integers 1..100. Within each bundle they must be
contiguous 1..N, with exact citation-{ordinal} IDs and [{ordinal}] markers.
No whitespace/padding equivalents or source-derived numbering. Bundle entries
share retrieval_id and have unique vector_record_id values. Entry, bundle and
render record contracts forbid extras and revalidate nested stored evidence.
Separate optional coverage records are unnecessary; bundle validation proves
structural order/correlation, and build enforces one-to-one input coverage.

Preserve source kind/ID/vector ID/hash/reference and original selected document
or memory provenance. Memory requires type/scope/finite confidence and is never
authoritative, with no document locators. Documents require document ID/reference/
parser/chunk index, preserve optional path/page/section/UTC timestamp, and carry
no memory-only fields. Source hashes are exact lowercase 64-character evidence,
never recomputed, displayed by default or claimed to authenticate a source.
Document authority remains only the PHX-034 knowledge-precedence boolean,
never truth, verification, instruction authority, safety or permission.

CitationRenderer returns ordered frozen citation_id/ordinal/marker/label records.
Memory label: Memory · type=<type> · scope=<scope> · source=<reference-or-ID>.
Document label: Document · source=<reference> · document=<ID>, followed only by
present page, section, path and chunk locators in that order. Collapse whitespace
with " ".join(value.split()) for display; retain original structured provenance.
Labels preserve ordinary Unicode and treat URL/Markdown/HTML/shell/prompt-looking
text only as inert untrusted plain text. Source bracket text cannot change the
separate structural marker. Output grows linearly with selected provenance;
no arbitrary metadata serialization, confidence/authority decoration or hash label.

Build/render are synchronous stateless in-memory transformations. No source
re-resolution, freshness check, file/URL/path dereference, database/session,
retrieval/vector/embedding/model call, event, tool/permission work, persistence,
schema/migration, API/frontend, cache or worker. No answer insertion, generation,
claim extraction or entailment. Citations preserve the retrieval snapshot;
they do not establish source truth, newer freshness or answer correctness.
Downstream consumers must keep labels as untrusted plain text and independently
authorize any future source interaction. No new dependency is required.

## ADR-045 — Immutable tool declarations without execution or authorization
Status: Accepted for PHX-040.

ToolRegistry stores only exact detached ToolDefinition metadata in caller
declaration order. Tools remain distinct from agents and models; PHOENIX is
supreme. The frozen future flow remains ToolRegistry -> validation ->
PermissionEngine -> execution -> result -> audit/event. This ticket implements
only metadata construction and resolution, with an empty default registry.
No concrete or speculative tool IDs are registered automatically.

ToolRiskLevel is exactly P0..P5 descriptive metadata. ToolResultStatus is exactly
success/partial_success/denied/timeout/cancelled/failure/unavailable_dependency;
no tool result is fabricated. Risk, permission_requirement and audit_behavior
strings do not grant, approve, deny, suppress audits or create consent. PHX-041
owns authorization; PHX-042 audit; PHX-043/044 concrete filesystem/terminal tools.
P5 metadata is representable here because no definition can execute.

ToolDefinition has exactly eleven frozen extra-forbidden fields: tool_id,
description, input_schema, output_schema, risk_level, permission_requirement,
timeout_seconds, cancellation_supported, audit_behavior, supported_platforms,
supported_environments. IDs and declaration strings are strict, outer-trimmed,
nonempty and case-preserving; descriptions retain meaningful original formatting.
Timeouts are ordinary finite positive int/float values normalized to float,
without timers. Cancellation is strict bool metadata. Support sequences are
ordered nonempty unique tuples of strict normalized strings, with no closed
vocabulary or OS/environment probing.

Schema evidence requires ordinary string-keyed JSON objects, finite numbers and
JSON scalar/array/object values. Validate recursively without arbitrary object
conversion, rejecting cycles, custom containers, callables and non-JSON values.
Accept nested lists/tuples as JSON arrays; copy maps into private read-only
MappingProxyType views and arrays into tuples. Serialization returns fresh
ordinary dict/list JSON data. Repeated acyclic shared containers remain valid;
excessive nesting fails explicitly at the interpreter's safe recursion boundary.
This is data validation, not JSON-Schema evaluation: no $ref dereference,
expression execution, validator/plugin import, file opening or URL resolution.

Registry construction accepts ordered list/tuple exact ToolDefinition instances,
snapshots and revalidates their stored values, rejecting subclasses, copied extras,
forged enums and malformed nested evidence. Duplicate normalized IDs raise
DuplicateToolError; case-distinct IDs remain distinct. Publish the private tuple
only after every definition validates. The registry is a frozen slotted value;
reinitializing an existing registry is rejected to prevent capability replacement.
A different tool set requires constructing a new registry.

get/require/contains share the strict normalized ID validator. Valid unknown IDs
return None/UnknownToolError/False respectively; malformed lookup IDs fail
validation. list_tools returns the declaration-order tuple; tool_ids is immutable
inspection. No alias fallback, mutable public map, registration, replacement,
hot-reload, discovery or execution API. No handlers, grants, provider clients,
agent runtime, events, persistence, schema/migration, secrets, audit writes,
API/frontend integration or new dependencies. Import/construction/inspection
perform no I/O or worker creation. All schema/description text remains untrusted
inert metadata for later permissioned concrete-tool integration.

## ADR-046 — Deterministic permission evidence over immutable tool risk
Status: Accepted for PHX-041.

PermissionEngine snapshots exact PHX-040 ToolRegistry metadata and an ordered,
immutable PermissionPolicy. Resolve each exact request through ToolRegistry.require;
only that ToolDefinition's ToolRiskLevel determines risk. Preserve descriptive
permission_requirement without interpretation. PHOENIX remains supreme and owns
future orchestration; models, agents and untrusted text cannot grant authority.

Requests, user decisions, grants, policy rules/policies and output decisions are
frozen and extra-forbidden. Revalidate stored values, including copied extras and
nested rules; reject subclasses, foreign or unregistered enums, malformed times/
shapes and duplicate authority. Accept only declared enum member identities or
exact textual vocabulary values. Identity/action/resource strings are strict,
outer-trimmed and case-preserving. Meaningful reason text retains its original
formatting and matches exactly. Resources are opaque: no path, URL, wildcard or
semantic scope expansion.
Aware explicit timestamps normalize to UTC; no wall-clock reads or implicit expiry.

Validate request, resolve registry risk, then validate all supplied evidence and
user correlation before applying precedence. P5 always yields blocked/p5_blocked.
For P0-P4 correlated explicit denial overrides all ordinary allow paths. P0 then
yields allowed/p0_internal. P4 requires explicit approved evidence for that exact
request ID; grants and policies never satisfy P4. Correlated explicit approval can
satisfy P1-P3. Without it, P1 requires an exact matching P1 grant; P2/P3 select an
exact same-risk policy before a qualifying same-risk grant, otherwise require
approval. Decision status/reason/risk and the single applicable evidence ID must
agree structurally; decisions establish neither execution nor success.

Grants require literal approved and only P1/P2/P3 risks. once binds one request,
task binds one task, session binds one session, persistent carries no context IDs;
contradictory context fields reject. P1/P2 accept all four durations. P3 accepts
only task/session grants; request-specific explicit approval remains available.
WHO actor, WHAT tool/action, WHERE resource, WHY reason and risk match exactly.
issued_at must not exceed request time; expiry, when present, must exceed both
issuance and request time. Duplicate grant IDs/effective scope-risk-duration-context
combinations reject even when times differ. Ordered valid nonmatching grants are
ignored; the first qualifying grant supplies evidence. Policy allows only P2/P3,
rejects duplicate IDs/effective exact scope-risk rules and defaults to empty.

UserPermissionDecision is trusted caller-supplied approved/denied evidence with
exact request correlation and decided_at >= requested_at. It is never inferred
from text or converted into a reusable grant. Future trusted UI/settings integration
owns evidence authenticity and distinct request-ID admission; this pure boundary
does not authenticate origins or implement consumption/replay storage.

No tool execution, handler, grant minting/refresh, model/provider/agent call,
filesystem/terminal implementation, secrets handling, logging, EventBus activity,
audit writes, persistence, migrations, API/frontend or dependency is added.
PHX-042 owns audit integration; INV-007 remains mandatory for future P3/P4
execution, while PHX-041 executes no action. PHX-043/044/045 remain separate.

## ADR-047 — Append-only audit evidence without execution authority
Status: Accepted for PHX-042.

AuditTrail borrows a caller-owned session factory and snapshots the exact PHX-040
ToolRegistry. Construction performs no I/O. Each explicit append revalidates the
exact stored PermissionRequest and PermissionDecision, resolves registry metadata,
and requires matching request ID, actor, tool, risk and permission requirement.
Audit evidence never evaluates permissions, authenticates approvals, mints grants,
executes tools, or establishes verification or user-facing success.

Frozen AuditRecord evidence has exactly 20 fields. Permission-decision records
accept all PHX-041 decision states and carry no result. Tool-outcome records require
an allowed non-P5 decision and one of the six non-denied PHX-040 result statuses.
Denied attempts remain permission-decision evidence. Evidence IDs and optional
task/session/caller correlation are preserved as opaque, case-sensitive text.
Resources remain untrusted opaque data. Persist only the lowercase SHA-256 digest
of the exact UTF-8 request reason, never its plaintext or arbitrary tool payloads.
Explicit aware occurrence times normalize to UTC and cannot precede request time;
omitted audit identities use independent UUID4 values.

Alembic 0005 adds only audit_events to the shared Base, with one primary key,
11 named checks and four ordinary indexes. Stable SQLite BEFORE UPDATE and
BEFORE DELETE triggers reject row mutation with a content-free error. Duplicate
IDs fail explicitly without replacement; repeated request IDs are legitimate.
Each append uses a short committing transaction; failures roll back and close,
raise safe errors with suppressed private exception chains, and never retry.
get/list return detached, revalidated evidence. Filters use exact equality; bounded
lists order by occurred_at then ID and return immutable tuples.

The only new vocabulary is permission_decision/tool_outcome; EventType remains 32
and there is no EventBus bridge. Historical revisions 0001–0004 remain immutable;
head assertions evolve to 0005 while preserving earlier schema and source rows.
Downgrade removes only the audit triggers, indexes and table. No startup migration,
API/frontend, concrete tool, secrets boundary or dependency is introduced.

audit_required_for_execution is true only for canonical P3/P4 risk members. INV-007
still requires the future PHX-043/044 execution pipelines to invoke durable audit
operations and handle their failures. This evidence primitive does not claim global
execution enforcement or resistance to an administrator who controls the DB schema.
P4 request-specific approval and unconditional P5 blocking remain PHX-041 authority.

## ADR-048 — Permission-gated, audited workspace filesystem capability
Status: Accepted for PHX-043.

FilesystemTool exposes exactly seven synchronous operations. Canonical definitions
are read_text/list_directory/stat P1, create_text/create_directory P2, write_text P3
and delete_file P4. Construction snapshots ToolRegistry and rejects every mismatch
against canonical schemas, risks and descriptive metadata. Definitions contain no
handlers; PermissionEngine remains the sole P0–P5 authority and receives the same
validated registry snapshot and explicit immutable application policy.

The application supplies one explicit absolute trusted root; construction and
describe are lexical and perform no filesystem/database I/O. Calls cannot replace
that root. Conservative `/`-separated relative paths reject traversal, embedded
dots, empty segments, backslashes, absolute/drive/UNC/device syntax, ADS colons,
wildcards, reserved Windows device names (including extension forms) and trailing
dot/space ambiguity. Ordinary case and filename spaces are preserved; no shell,
environment or home expansion occurs. Only list/stat admit `.` for root access.
Intent resources identify the normalized absolute root plus exact relative target;
request tool, action and resource must match exactly before one permission
evaluation. Call IDs remain independent of caller-supplied stable request IDs.

Every non-allowed decision returns denied evidence without target I/O. For P3/P4,
AuditTrail must commit the permission decision before even resolving/statting the
root or creating a temporary file. Allowed attempts then append durable outcomes
before returning a normal result. Required pre-audit failure raises a safe error
and fails closed. Post-attempt audit failure raises a distinct safe error with the
call ID and effect_may_have_occurred=True, without success or automatic retry.
Filesystem and SQLite storage are not one atomic transaction. P1/P2 have no
mandatory audit rows. Audit history never authorizes later operations.

After authorization, strictly resolve the trusted root, require a directory, check
each existing caller-reachable component with lstat, reject symlinks and every
Windows reparse attribute (including junctions), and verify resolved containment.
Creates require an existing contained parent. Stat consistently rejects link
targets; listings may report immediate links as symlink with no target size.
V1 assumes a single-user local workspace, without hostile concurrent path
replacement: these checks do not claim cross-platform race-proof openat isolation.

Reads and text payloads are bounded to 1 MiB of strict UTF-8. Reads preserve bytes'
text meaning without newline conversion. Listings are nonrecursive, sorted by
exact name, return at most 200 entries by default (maximum 1000), and expose
truncation as partial_success. Sorting retains only bound+1 names in memory while
scanning the directory; outer deadlines belong to the caller. Exclusive creates
never overwrite or create parents. Writes require an existing regular file and
use a same-directory temporary file, flush/fsync and os.replace, with best-effort
cleanup and no backup. Delete is one regular-file unlink after exact P4 user
approval. Actual partial creation on an I/O failure is reported as effect_applied;
failures never fabricate success. Atomic replacement does not promise preservation
of inode identity, ACLs, modes or crash-atomic audit coupling.

Frozen calls/intents/results reject extras, stored-value forgeries and contradictory
evidence. Result outputs are detached immutable JSON data with exact operation
shapes; failures expose only closed error codes. File content and metadata remain
untrusted, with no execution, instruction authority, logging, audit payload,
automatic memory/RAG insertion or model forwarding. Trusted integration owns
approval-origin authenticity and request admission; this runtime does not mint
permission evidence or provide consumption/replay storage.

PHX-045 remains the dedicated secret boundary. Until then trusted integration must
configure a narrow non-secret workspace: no arbitrary home/OS-root safety claim or
ad-hoc secret detector is provided. There is no terminal/process, model/agent,
EventBus, API/frontend, watcher, recursive operation, migration or dependency
integration. Cancellation metadata is false; no hidden worker is created. Alembic
head remains 0005_audit_events and the canonical EventType vocabulary remains 32.

Necessary regression support: PHX-042 is already merged into the supplied main
baseline. Retrieval's protected-source test retains its exact single audit
registration assertion and now compares the entire Alembic environment to main,
without removing that registration from only one side. No source protection is
relaxed and no existing production behavior changes.

## ADR-049 — Trusted, bounded, permission-gated terminal profiles
Status: Accepted for PHX-044.

TerminalTool exposes synchronous describe/execute over immutable application-owned
command profiles. Its three metadata-only canonical definitions are terminal.inspect
P2, terminal.project P3 and terminal.sensitive P4. Construction snapshots and checks
every ToolRegistry field against the canonical definitions, then constructs the
existing PermissionEngine with that registry and explicit immutable policy. Profiles
select a canonical tool but cannot override risk. The default catalog is empty;
profiles preserve source order and reject duplicate normalized IDs. Adding profiles
requires a new catalog/runtime, with no discovery, registration or hot reload.

Each profile declares an absolute executable, fixed argv, either no caller arguments
or an exact string allowlist, a fixed relative working directory, supported platforms,
ordinary inherited environment names, stdin policy and bounded timeout/output limits.
Relative executables, batch files and named shell interpreters are rejected. Calls
contain only opaque call ID, profile ID, ordered strict arguments and optional stdin.
There is no shell string, parsing/expansion, caller executable/risk/cwd/environment,
handler, timeout override or implicit shell invocation. Trusted configuration owns
read-only versus modifying classification and must avoid interactive interpreters,
background jobs and argument combinations with unintended command semantics.

The root is explicit, absolute and lexical at construction. Describe performs no
target I/O and derives action execute plus a canonical JSON resource identifying
profile and lexical absolute cwd, with a SHA-256 digest binding exact profile
configuration, arguments and stdin. This binds approvals to the invocation without
persisting raw arguments/stdin in audit resources; it is data minimization, not
encryption or an authentication mechanism. Call IDs are independent of stable
request IDs, default to UUID4, preserve ordinary opaque case and correlate audit
records. Request tool/action/resource must match before exactly one permission
evaluation. Every non-allowed result is denied with no attempt, effect or target I/O.
PHX-041 remains authoritative: P3 task/session scope, exact request-specific P4 user
approval, no P4 policy/grant authorization and unconditional P5 blocking.

For P3/P4, the permission decision must commit through AuditTrail before cwd
resolution, environment reads, temporary stdin creation or process launch. An
independent database connection observes that committed row in tests. Allowed
attempts append one outcome before returning normal evidence, including launch,
cwd, pipe, nonzero-exit, timeout and output-limit failures. Pre-audit failure raises
a safe error and launches nothing. Post-attempt audit failure raises a distinct safe
error with call ID and effect_may_have_occurred=True, without a normal result or
retry. Child effects and audit transactions are not atomic; audit evidence never
authorizes a later request. P2 has no mandatory audit rows.

After authorization, strict root resolution and each fixed cwd component's lstat
reject missing/non-directory targets, symlinks and all Windows reparse attributes,
including real junction escapes, and verify resolved containment. Minimal inherited
environment defaults to SystemRoot; only SystemRoot, WINDIR, LANG, LC_ALL and
LC_CTYPE may be explicitly selected. PATH, home/profile, proxy, Python injection
and credential names cannot be selected. No full environment is copied or logged.
Trusted integration must configure narrow non-secret roots, executables and inputs;
PHX-045 owns broader secret handling and remains TODO.

One direct child uses argv, shell=False, no PTY, binary pipes and no hidden worker.
Synchronous fair reads use Windows PeekNamedPipe or POSIX nonblocking os.read;
there is no communicate/read-all capture or reader thread. Stdin is disabled by
default and limited to 64 KiB UTF-8; a temporary seekable file, created only after
authorization/audit and closed after the attempt, prevents blocking on unconsumed
input. Caller/fixed argument sequences each allow at most 64 strict strings, each
at most 4096 UTF-8 bytes; the final executable/fixed/caller argv is at most 16 KiB.
Timeout defaults to 30 seconds and is finite, positive and at most 300 seconds.
Stdout and stderr each default to 64 KiB, with strict bounds 1 byte through 1 MiB.
Capture retains bounded byte prefixes and observes at most one extra byte per
stream before an immediate direct-child kill on excess; timeout also kills. Wait
and cleanup are bounded best effort. Native OS process creation is not preemptible,
and there is no process-tree manager. Cancellation metadata truthfully stays false.

Raw prefixes decode as UTF-8 with replacement, preserving newlines. Replacement
characters can expand encoded result text up to three times the raw-byte ceiling.
Both truncation flags mark incomplete capture on timeout/limit, even if only one
stream exceeded its budget. Zero exit maps to success evidence; nonzero exit to
failure/process_failure, timeout to timeout/timeout, excess output to failure/
output_limit, missing executable to unavailable_dependency/executable_unavailable,
and cwd/other I/O failures to failure with closed codes. Modifying profiles preserve
possible-effect evidence once launch succeeds, including nonzero/timeout/limit or
reader failure; pre-launch failures report no effect. Effect_applied is conservative
possible mutation evidence, not proof of a specific change. P2 reports no mutation.

Frozen exact-revalidated calls, profiles, intents and results reject extras,
stored-value forgeries and contradictory evidence. Results contain no process
handles, environment dumps or raw exceptions. Stdout/stderr remain inert untrusted
evidence, with no approval/instruction authority, command chaining, logging, audit
payload, automatic memory/RAG insertion or model forwarding. Exit codes do not
establish verified task completion or bypass PHOENIX/LEVI. Trusted integration owns
approval authenticity, request admission and replay control.

V1 follows PHX-043's single-user local workspace model: hostile concurrent path
replacement and hostile executables are outside this boundary. Cwd containment is
not an OS sandbox, process-tree restriction, network firewall or crash-atomic audit
guarantee. OS failure may prevent best-effort cleanup; trusted profiles must not
leave descendants. Real Windows process/junction behavior is tested; POSIX/macOS
branches are declared but not executed on this Windows host. No existing runtime,
filesystem tool, agent/model, secret store, EventBus, API/frontend, schema, migration
or dependency changes. Alembic remains 0005_audit_events; EventType remains 32.
