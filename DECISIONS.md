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

## ADR-050 — Explicit runtime secrets and Windows OS storage boundary

Date: 2026-10-06

Status: Accepted

PHX-045 introduces only `phoenix/security/secrets.py`, with no tool, model,
transport or ordinary-data integration. `SecretReference` is frozen,
extra-forbidden, exact-revalidated and contains only `secret_id`. IDs are strict
ordinary strings, trimmed outside, case-preserving, 1..128 ASCII characters,
with grammar `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`. Control characters reject before
trimming. Separators, wildcard/target syntax and arbitrary namespace overrides
are forbidden. References serialize as nonsecret metadata; trusted callers must
not use a username, token or secret value as an ID. An ID is neither permission,
existence, ownership, trust nor authentication evidence and is never automatically
promoted into memory.

`SecretMaterial` is a small runtime-only slotted class rather than a Pydantic
model/dataclass. It accepts one explicit nonempty valid Unicode string, including
meaningful whitespace/NUL, and keeps a mutable UTF-8 bytearray. Its fixed repr/str
contain redaction markers rather than value-derived text. Ordinary JSON, pickle,
copy/deepcopy, __getstate__, dict/asdict/bytes/vars paths reject; it has no
model_dump or dict method and cannot be subclassed. Equality/hash use object identity. Only the
deliberately named `reveal_text_for_trusted_use()` returns exact text while open.
Reveal confers no permission, performs no logging/persistence, and creates an
immutable interpreter copy. Idempotent close exposes closed state and overwrites
the original mutable buffer in place; context exit closes on success or failure.
Closed material cannot be revealed, stored or used for redaction.

Python cannot guarantee erasure of input strings, revealed/decoded strings,
interpreter/allocator copies, exception-frame references or OS-managed copies.
Buffer wiping is best effort, not cryptographic proof. This boundary prevents
ordinary accidental serialization; it is not protection against trusted-process
introspection, hostile native pointers, private-attribute tampering or an OS
administrator. Trusted callers own authorization, explicit lifetime and consumer
admission. No automatic environment/file/database/model lookup constructs material.

`SecretStore` is the provider-neutral synchronous put/resolve/delete protocol,
without list, export, value search, callback execution or authorization mutation.
`WindowsCredentialStore` uses standard-library ctypes and GENERIC credentials,
fixed target `PHOENIX/<normalized secret_id>`, UTF-8 blobs without a terminator,
and a 2,560-byte OS limit. CRED_PERSIST_LOCAL_MACHINE retains the current user's
credential across logons on this machine without requesting roaming. Windows
target lookup is case-insensitive: reference spelling is preserved, but IDs that
differ only in case share a Windows credential. The namespace is not an access
control mechanism against other applications under the same Windows user.

Import/construction does no credential I/O. Explicit operations lazily load
advapi32.dll using system-directory search and declared Win32 ABI signatures.
Non-Windows operations and unavailable DLL/logon-session state fail unavailable.
Each operation exact-revalidates the reference; put requires open exact material
and writes only its supplied bytes. Resolve reads one fixed target, validates
type/target/bounded nonempty blob/strict UTF-8, and returns independent closable
material. Missing resolve raises not_found; absent delete returns False, present
delete True. Operations do not retry. All public adapter errors expose only the
six closed codes not_found/unavailable/invalid_reference/invalid_material/
store_failure/decode_failure, suppressing private exception chains. Put/read
temporary mutable buffers are wiped in finally blocks. Bounded OS-owned read
blobs are wiped before CredFree, including decode failure; every nonnull returned
allocation is freed. Malformed size evidence is freed without unsafe oversized
access. Read cleanup failure closes a produced result before raising failure.

There is no production in-memory store, plaintext file/JSON/SQLite/registry/env
fallback, custom encryption or new dependency. Tests always deny the real WinDLL
loader and use synthetic values plus deterministic fake OS calls. ABI, target,
bytes, frees, buffer wiping, errors and single-attempt behavior are tested without
reading, writing or deleting real credentials.

`redact_known_secrets(text, materials)` is pure exact-substring redaction for
explicit open exact materials in an ordered list/tuple. Longer values claim
overlapping occurrences first across the original text; equal lengths follow
caller order and repeated occurrences are covered. Output uses `[REDACTED]`,
without rescanning inserted markers or retaining values in a regex cache.
It discovers no unknown/semantic secrets and replaces neither isolation nor
admission policy, PermissionEngine or PHX-055 prompt-injection defenses.

P5 stored-credential reveal stays blocked even with synthetic approval, denial,
grant/policy-looking evidence and safe/approved text. No production reveal tool,
endpoint, command, model resolver or frontend display exists. PermissionEngine,
audit, filesystem and terminal semantics remain unchanged; no secret injection,
credential file scanning, memory/RAG/embedding/citation/model forwarding or
EventBus integration is introduced. API/desktop, /health, events, persistence,
Alembic and dependency manifests remain unchanged. EventType stays 32 and the
head stays 0005_audit_events. Future integrations require their own authorized
permission/audit design; PHX-045 exposes no user-facing secrets manager.

## ADR-051 — Isolated direct browser tools and required Playwright runtime

Date: 2026-10-06

Status: Accepted for PHX-050.

Playwright is promoted to required Phase-5 Python runtime dependency
`playwright>=1,<2`, locked to 1.63.0 with greenlet 3.5.6 and pyee 13.0.1. No existing
third-party package changes. Chromium binaries remain separately installed operational
prerequisites; import/construction/tests/package hooks/application startup never
download them. Explicit authorized session_open maps missing/failed startup to
safe unavailable-dependency evidence. Deterministic tests always use fake runtime,
DOM and protocol objects; a live smoke test is optional and was not run here.

The internal owner stays computer_control, without AgentRegistry integration.
BrowserTool offers exactly seven canonical tools: session_open P2, navigate P2,
read_page P1, inspect_element P1, click P4, fill P4, session_close P2. Complete
ToolDefinition snapshots prevent risk/schema/permission/audit/timeout/platform
drift. Click and fill remain generic P4 because DOM semantics can cause sensitive
external effects, including autosave. Page labels/model/caller claims cannot
lower risk. Existing PermissionEngine evaluates one exactly correlated request;
only request-specific approval permits P4. No grant/policy or page evidence can
authorize it. Trusted integration still owns authentic user evidence and replay
control; the SHA-256 fill digest binds data without authenticating a caller.

Frozen, extra-forbidden, strict, exact-revalidated BrowserCall has the eight frozen
fields and operation-specific shapes. Pure describe returns canonical metadata
and compact JSON resource evidence without session access/I/O. Resource binds
operation/session and URL/selector/read bound; fill includes only the exact UTF-8
digest. P4 permission audit must durably commit before session/browser/page access,
including denied attempts. Allowed P4 outcomes durably commit once before normal
return. Pre-audit failure prevents all target I/O; post-attempt audit failure raises
explicit possible-effect evidence without retry, rollback claim or normal success.
Browser effects and audit DB are separate transactions.

Only allowed session_open starts synchronous headless Chromium with one fresh
nonpersistent context and blank page, accept_downloads=False, service_workers=block,
permissions=[] and 30,000-ms action/navigation waits. V1 owns one active session
and only its context/browser/driver. No user browser profile, executable/args,
cookies/storage/HTTP/proxy credentials, environment or permission input is accepted.
Duplicates never replace state; explicit close and failed startup/crash cleanup
attempt each owned resource once. Playwright does not expose a hard close timeout;
cleanup is cooperative best effort, without hidden workers/retries or a claim of
a process-wide deadline. Cancellation metadata remains False.

URL data is strict HTTP/HTTPS <=4,096 characters, outer-trimmed, without userinfo,
controls, localhosts or nonpublic literals; ambiguous Chromium numeric address
spellings are rejected. No DNS probes or pre-permission fetch occurs. Fixed
context request routing applies this lexical policy. Playwright 1.63 follows
HTTP redirects without another routing callback, so an internal owned-page CDP
Fetch response-stage guard validates Location before continuation. It checks all
five HTTP redirect statuses, rejecting private/malformed/ambiguous destinations
without reading response bodies, logging destinations or exposing caller commands.
Protocol failure never silently resumes a paused request. Child-frame and worker
requests are rejected because their separate Chromium targets would escape the
owned page response guard; no iframe/worker browsing capability is offered in V1.
The fixed protocol guard is internal security enforcement, not an eighth tool.
Public-host DNS rebinding is still not claimed fully prevented.

Read/inspect/click/fill bind exact expected_url after permission/pre-audit. This
check is not atomic with a hostile page's concurrent navigation or DOM changes.
CSS-only input <=2,048 characters uses a fixed css= locator; no selector chains,
XPath/text engines, first/nth fallback or evaluate. Exactly one match is required.
Read returns bounded body inner text; inspect returns bounded element text and
only id/class/role/aria-label/name/type/href/title, omitting tag name rather than
evaluating JavaScript. Prefix truncation is deterministic and reported explicitly;
underlying inner text is obtained before slicing. Detached results are immutable,
JSON-safe and structurally correlated; attributes use immutable snapshots.

Click/fill invoke one ordinary bounded operation without options, modifiers,
Enter/submit/click chaining or retry. Once invocation begins, effect evidence is
conservatively true even after error. Fill accepts explicit ordinary text bounded
to 65,536 UTF-8 bytes and rejects SecretMaterial and file/password/current-password/
new-password inputs before filling. No raw fill text is echoed in result/title,
resource/audit/log/exception. No SecretStore resolution or secret classification
is added; trusted callers own ordinary-text admission. Popups are closed best
effort, never adopted; downloads fail where detected without saving or exposing
paths. No upload/file chooser exists. Successful action is execution evidence,
not proof of external business outcome.

Page URL/title/DOM text/attributes remain inert untrusted evidence, with closed
safe errors and suppressed raw diagnostics. No model/agent authority, tool chain,
memory/RAG/embedding/audit-content persistence, terminal/filesystem, Windows UI,
screenshot/vision, search/URAHARA, PHX-055 classifier, EventBus, API/frontend or
schema integration is introduced. P5 remains blocked. Alembic stays
0005_audit_events; all five migrations and 32 EventTypes remain unchanged.
Historical bootstrap/vector/retrieval dependency test assertions are reconciled
with the exact authorized promotion, without changing any existing production
security or data semantics. All deterministic gates pass; PHX-051 remains TODO.

## ADR-052 — Explicit Windows UIA boundary with conservative native mutation
Status: Accepted. Date: 2026-10-06. Ticket: PHX-051.

Promote pywinauto >=0.6.9,<0.7 with sys_platform == "win32" only, locked to
0.6.9 plus pywin32 312, comtypes 1.4.17 and six 1.17.0. No existing third-party
package changes. Direct API/DOM remains preferred over native accessibility.
The conceptual owner is computer_control; KAKASHI is a display alias only.
PHOENIX remains supreme, and existing ToolRegistry/PermissionEngine/AuditTrail
own declaration, permission and durable audit authority.

Exactly seven ordered tools: windows.list_windows P1, windows.inspect_window P1,
windows.focus_window P2, windows.inspect_control P1, windows.invoke_control P4,
windows.set_text P4, windows.close_window P4. Complete immutable declarations
are snapshotted and compared at construction, including schemas, risk, permission,
timeout, cancellation, audit, platforms and environments. Unrelated tools may
coexist. Strict frozen extra-forbidden contracts are exactly revalidated.
Calls carry no caller approval/risk/backend/callback surface. Positive HWNDs and
expected process ID/class bind focus and control/mutation resources. Selectors
use exact case-sensitive automation_id/control_type/name, never fuzzy matching,
regex, wildcard, XPath, index or first-match fallback. Zero/multiple matches fail.
At most 500 discovered descendants are admitted before any action; overflow fails.

Describe is pure. Resources are canonical JSON with effective bounds, identity
and selectors; set_text correlates SHA-256 of exact UTF-8 ordinary text, not raw
text. The digest is data-minimization evidence, not authentication. SecretMaterial
is structurally rejected. Permission requests match tool/action/resource exactly,
and PermissionEngine evaluates once. Nonallowed execution touches no UIA.
P4 decision audit commits before even backend import/initialization or HWND lookup;
denials retain decision audit only. Every allowed P4 attempt commits one outcome
before normal return, including unavailable/failed attempts. Pre-audit failure
closes the boundary. Post-attempt audit failure raises safe uncertainty evidence,
with no retry, rollback claim or normal success. Grants/policies cannot satisfy P4.
Trusted integration owns consent authenticity, replay control and request admission.

The adapter lazily loads pywinauto and fixes Desktop to backend=uia with magic
lookup disabled. Direct ElementFromHandle avoids WindowSpecification polling.
Fixed raw FindAll scopes enumerate root children or the selected window's
descendants. Raw properties and array access preserve COM failures instead of
using pywinauto helpers that log or silently return empty/default evidence.
Windows BOOL marshalling is explicitly handled; unknown password state omits
value and prevents set_text. Values are read only from ordinary non-password
ValuePattern controls. Disabled/hidden controls cannot be mutated. Native
SetFocus, InvokePattern.Invoke, writable ValuePattern.SetValue and WindowPattern.Close
are called directly once. Methods/patterns are acquired before possible-effect
evidence. High-level pywinauto focus/invoke/close helpers are avoided because they
can add keyboard fallback, logging or worker/retry behavior. Once an action begins,
possible-effect evidence remains true even on error. Success proves an attempt,
not the user's external business outcome.

Top-level windows are HWND-sorted, prefix-limited to 100 by default (maximum 200),
with titles <=4096 characters. Inspection defaults to 4096 (maximum 8192) name/
title/value characters. Prefix truncation produces explicit partial_success.
Evidence is detached, immutable and JSON-safe, with structural operation/resource/
permission/audit correlation. Mutation results never echo titles, control content
or raw input. Closed safe error codes replace raw COM/library diagnostics.
Window/control evidence is inert untrusted data, never authorization, instructions,
agent routing or automatic verification. Audit persists correlation/status rather
than UI content or raw set_text. No UI state persistence is introduced.

Native COM calls are synchronous/cooperative and cannot be safely hard-preempted;
metadata is 10 seconds with cancellation_supported=False, without sleeps, polling,
workers, tasks or retries. FindAll can materialize its native array before the
500-descendant admission check. HWND/PID/class and selector checks are not atomic
against hostile concurrent replacement or tree changes. UI effects and SQLite
audit commits are separate transactions. Windows/UIA-capable patterns are required;
non-Windows explicit execution returns safe unavailable-dependency evidence.

All deterministic tests use a private fake, including fake COM/native marshalling,
and temporary audit databases; no real desktop enumeration or mutation occurs.
No process launch/termination, Win32 backend/messages, physical input, clipboard,
screenshots/OCR/vision, browser DOM, terminal/filesystem chain, credential resolver,
memory/RAG/model/provider, EventBus, API/frontend or schema integration is added.
PHX-040–050 production remains unchanged, P5 remains blocked, all five migrations
stay intact, Alembic remains 0005_audit_events and EventType remains 32. Historical
bootstrap/browser manifest assertions evolve only for this exact promotion, with
a new full-project/lock comparison rejecting unrelated changes. PHX-052+ stays TODO.

## ADR-053 — Scoped in-memory screenshot evidence and separate advisory vision
Status: Accepted. Date: 2026-10-06. Ticket: PHX-052.

Add exactly screen.capture_window at P1 observe, owned conceptually by
computer_control (KAKASHI display alias). ToolRegistry declarations and the
existing deterministic PermissionEngine remain authoritative. Complete canonical
tool metadata is compared against a detached registry snapshot, with exact input
revalidation. ScreenshotCall contains only call_id, positive integer HWND,
expected PID and normalized case-preserving class name <=256 characters. Describe
is pure; canonical JSON binds capture_window/HWND/PID/class. Execute matches exact
request tool/action/resource and evaluates once before any native access. Nonallowed
execution returns denied without attempting capture. No new P3/P4 audit behavior
is introduced for this P1 capability. Consent authenticity/replay and later actions
remain responsibilities of trusted orchestration.

Capture uses lazy Windows ctypes user32/gdi32, GetWindowDC for the exact nonzero
HWND, a compatible off-screen memory DC and 32-bit top-down DIB, and PrintWindow
with fixed whole-window flags. No desktop DC, screen BitBlt fallback, clipboard,
pywinauto screenshot helper, image library or file output exists. The window must
exist, match expected PID/class, be visible/non-iconic, and have bounded geometry.
Pre/post identity and geometry rechecks discard changed evidence. These checks
narrow races without an atomic observation guarantee. Native PrintWindow is
synchronous and not safely hard-preemptible; protected/GPU/special windows may
fail or provide incomplete/blank rendering despite native success. No resize,
downsample or crop is offered. DC/bitmap/selection resources receive one cleanup
attempt in order on every path; cleanup failure is explicit, without retries.
Native and mutable pixel buffers are wiped best effort, without claiming complete
erasure of immutable Python/interpreter copies. GDI BI_RGB's reserved alpha channel
is made opaque; the pure encoder preserves supplied BGRA alpha exactly.

GetWindowRect is DPI-virtualized; native inspection/rendering therefore temporarily
uses per-monitor DPI awareness on the calling thread and restores the prior context
once afterward, without changing process-wide policy. Missing/failed native DPI
support fails safely (Windows 10 1607+ is required). Restoration failures discard
and wipe mutable pixels rather than return a frame. Coordinate/dimension evidence
is physical pixels; DPI support and cleanup are verified with fake WinAPI only.

Dimensions are 1..4096 each, <=8,294,400 pixels, with exact BGRA byte length and
orientation evidence. Deterministic stdlib PNG uses BGRA-to-RGBA conversion, filter
0 for every row, zlib level 9, exact chunk lengths/CRCs and only IHDR/IDAT/IEND.
No metadata/timestamp/text chunks exist. PNG is <=8 MiB without silent truncation.
ScreenshotFrame is frozen, extra-forbidden, exactly revalidated, UTC-normalized
and sensitive: identity/coordinates/dimensions plus exact SHA-256 and canonical
base64. Base64 is omitted from repr/str. Frame validation admits the fixed
metadata-free 8-bit RGBA non-interlaced PNG representation and checks bounded
decompression/raster/CRC/hash/dimension integrity. Frame possession is observation
evidence, never authority. Results are detached JSON-safe contracts correlated to
permission/resource; success has one frame, denial/failure none. Closed safe codes
replace native diagnostic text and pointers.

PHX-010 ModelProvider/GenerationRequest/ModelMessage remain text-only and unchanged.
VisionInterpreter is a separate async neutral ABC with interpret/cancel/aclose and
read-only provider ID. Frozen request/response/region contracts carry no action,
tool, permission, risk or verified/task-success fields. Query <=4096, summary
<=8192, <=64 regions, strict integer geometry and ordinary finite float confidence
0..1 are validated against the exact correlated frame. Pixels, query, summary,
labels, coordinates and confidence remain untrusted advisory data.

OllamaVisionInterpreter uses existing httpx, only lexical localhost/localhost.
or stdlib loopback literals, HTTP(S) and root base URL with fixed api/show/api/chat
paths. No DNS probe is required; userinfo, ambiguous/non-loopback hosts, query,
fragment and caller path routing are rejected. trust_env=False is required for
owned and injected clients; redirects are disabled per request, with client auth
disabled for these local calls. Injected transports/clients are trusted infrastructure;
clients remain caller-owned, while transports of owned clients close with them.
They must preserve this local transport intent; no security
claim is made against a malicious replacement transport or server. Each operation
checks exact vision capability via api/show before uploading one base64 frame.
Chat is one user message with fixed visual-data-only/no-action/no-permission
instructions plus caller query, stream=False, JSON format and fixed temperature 0,
seed 0, num_predict 2048. No tool schema/function calls are sent.

HTTP failure, protocol error, duplicate/nonfinite JSON, invalid assistant/done/model
evidence, oversized bodies/content, extra inner fields and malformed/out-of-bounds
regions fail explicitly. Adapter-generated operation/provider/model/frame IDs are
correlated; model text cannot choose those IDs. HTTP bodies are capped at 1 MiB,
content at 65,536 characters. Finite connect/read/write/pool deadlines and a whole
operation deadline (default 300 seconds) bound cooperative inference. One instance
has one active caller operation, no queue/worker. Independent cancellation flags
prevent success after local cancellation even if lower code suppresses it; deadline
checks prevent late success after suppressed timeout. Owned client cleanup and
active-task waiting are bounded; cancellation does not claim server shutdown.

VisionLoopService processes only an explicit immutable 1..3-frame sequence with
same HWND/PID/class, unique IDs and nondecreasing UTC timestamps. Iteration budget
is 1..3 and cannot exceed frame count. Collision-resistant operation IDs derive
from canonical loop ID/iteration; each interpreter response is exactly revalidated
and correlated. Stop immediately on sufficient; otherwise consume exactly the
budget. Normal result contains only interpretations/IDs/count/sufficient/exhausted
with advisory_only=True, never task verification. One active loop per instance;
scoped cancellation sets a separate flag and targets the current interpreter
operation/caller task, never continuing to another frame after cancellation.
No hidden recapture or model-to-tool/action path exists. Model inference remains
cooperative and may be incorrect/adversarial; sufficient is not business success.

Frames and model outputs are not logged or persisted to files, databases, audit,
memory/RAG/embeddings or caches. No SecretStore, OCR, physical input, browser/native
UI action, terminal/filesystem chain, search/research, EventBus, API/frontend,
schema/migration or dependency change is added. PHX-040–051 production and all text
providers/registry/router remain unchanged. Five migrations, Alembic head
0005_audit_events and 32 EventTypes are preserved. Deterministic tests use synthetic
pixels, fake WinAPI resource boundaries, in-process HTTP and fake interpreters;
no real desktop/model smoke is required or performed.

Repaired stale PHX-051 test_exact_windows_dependency_promotion moving-origin/main
comparison after PHX-051 merge; test-only repair, no PHX-051 production change.
The fixed historical PRE/POST promotion is tested separately from current-retention
checks, without preventing legitimate future dependency evolution. PHX-053+ TODO.

## ADR-054 — Pure provider-neutral search evidence without execution
Status: Accepted. Date: 2026-10-06. Ticket: PHX-053.

SearchProvider is a separate async provider category under phoenix/web, exposing
read-only normalized provider_id, abstract sorted immutable active_request_ids,
search/cancel/aclose, with existing PHX-010 CancellationResult/Status. The ABC
starts no I/O or worker and adds no concrete provider. Future adapters own bounded
operations, state cleanup and scoped cancellation; accepted cancellation is an
acknowledgement, not proof remote work stopped. PHOENIX remains supreme. Future
research owner is web_research; URAHARA is display-only, absent from contracts.
Actual web search is P1; ToolRegistry/PermissionEngine execution integration is
deferred to PHX-054. This provider abstraction creates no search tool or permission.

SearchRequest has exactly request_id/query_text/max_results/as_of; SearchResult
has exactly result_id/rank/url/title/snippet/source_name/published_at/date_text/
source_ref; SearchResponse has exactly request_id/provider_id/as_of/results.
Frozen extra-forbidden models revalidate exact stored values and nested results,
rejecting subclasses, copied extras, colliding extras and malformed evidence.
Ordinary Unicode strings reject NUL; normalized identifiers are control-free,
case-preserving, nonempty and <=256 characters. Query preserves meaningful
formatting <=4096; title/snippet preserve original text, including empty values,
with <=1024/8192 character bounds. These three content fields are omitted from
repr/str. Explicit aware dates normalize UTC, including aware ISO representations
for JSON roundtrips; numeric/naive timestamps fail. No clock default exists.
Strict integer max_results/rank are 1..20; bool/coerced numbers fail.

URLs are outer-trimmed HTTP(S) metadata <=4096 characters, without userinfo,
controls, backslashes or malformed authority. Localhost/suffixes and nonpublic
literals reject; IDNA and ambiguous numeric/bracket syntax are checked lexically.
The original admitted URL spelling is retained. No DNS, fetch or dereference
occurs; admission grants no navigation approval or DNS-rebinding protection.
Later retrieval requires its own independent security and permission checks.

Optional source_name preserves meaningful provider text <=512; published_at is
provider-reported UTC evidence; date_text preserves raw text <=256. source_ref
is an opaque control-free string <=1024, preserved even when empty. Neither
names, dates, opaque references nor rank prove quality, truth, authenticity or
freshness. Responses snapshot list/tuple inputs to immutable 0..20 result tuples,
require ranks exactly 1..N in order, and reject duplicate normalized IDs/exact
admitted URL strings. No semantic URL/source deduplication occurs. validate_request
revalidates both sides and checks exact request/provider/as_of and result count;
it establishes no semantic query correctness or task success.

Ten closed safe error categories include input/response failure, unavailable,
timeout, rate_limited, unauthorized, busy, closed, wrong_event_loop and cleanup_failure.
Contract validation failures suppress private diagnostic chains and expose only
invalid_input/invalid_response, without query, URL, snippet or extra field names.
Web prompt/tool/system/approval/shell-looking text remains inert untrusted data.
No browser fallback, page retrieval, ranking/cross-check/synthesis, citations,
SecretStore/env keys, model/vision, memory/RAG, audit/persistence, EventBus,
API/frontend, schema/migration or dependency integration is added. Existing
production and tests remain unchanged; Alembic stays 0005_audit_events and the
EventType vocabulary stays 32. Tests use synthetic data and test-only providers.

## ADR-055 — Bounded advisory research through existing permission boundaries
Status: Accepted. Date: 2026-10-06. Ticket: PHX-054.

WebSearchTool declares exactly web.search at P1 and snapshots every canonical
ToolDefinition field. Existing PermissionEngine evaluates one exact correlated
tool/action/resource request before any injected SearchProvider search. Resource
is compact canonical JSON binding search/provider/SHA-256 of exact UTF-8 query/
max_results/UTC as_of, without query plaintext. The hash is correlation data,
never authority. Nonallowed requests perform no search; valid allowed requests
derive one SearchRequest and exact-revalidate/correlate one SearchResponse.
No P1 audit rows, concrete provider, query rewrite, retry or alternate fallback.
Provider lifetime and cooperative deadlines remain caller-owned.

UraharaResearchService requires neutral registry ID web_research, independently
of URAHARA/any alias. PHOENIX remains supreme. An injected neutral ResearchPageReader
returns existing BrowserToolResult evidence; service accepts only exact allowed
P1 READ_PAGE success/valid partial-success, no effect/error, with correlated call,
expected public URL and read bound. Final URL independently satisfies public
PHX-053 lexical admission. No BrowserTool execution, session broker or Playwright
is added. Individual denied/unavailable/failed/invalid evidence uses observable
search_snippet fallback; unexpected reader exceptions are fatal. Search denial/
failure raises a closed safe ResearchError rather than an ordinary report.

Select first eligible provider-ordered candidates within explicit 1..8 source
budget; no quality score or semantic dedup. Optional age filtering uses only
provider-reported published_at; exact boundary includes, missing dates remain
unknown, future dates count anomalies and remain unknown-age candidates.
Never parse date_text. Prefix-only per-source/total character budgets retain
all selected provenance, including empty excerpts after total exhaustion.
Source/page operation IDs derive from unambiguous canonical research/result
identity hashes; exact UTF-8 excerpt hashes and truncation evidence are retained.

ResearchSynthesizer is a caller-supplied async ABC, with no concrete model adapter.
Exact correlated drafts reference only selected source IDs. Final support counts
distinct declared IDs; corroborated means at least two, without asserting truth,
source independence, fact checking or semantic entailment. Summary, claim text,
uncertainty and all source metadata stay untrusted inert data. Reports always
have advisory_only=True and owner_id=web_research, never task success/verification.
Zero selected sources use one fixed service summary, no claims and no synthesis.

One active caller research per service/event loop; second request is busy, with
no queue/hidden worker/global serialization. Scoped cancel sets an independent
local flag, targets only current search/page/synthesis ID, validates neutral
acknowledgement and cancels the still-owned caller task. Suppressed cancellation
cannot advance/finalize. Finally clears active state. Delayed acknowledgements
cannot cancel unrelated caller work after cleanup. Accepted is an acknowledgement,
not proof remote work stopped; collaborators must cooperate and callers own
deadlines/resources. No automatic collaborator close or global shutdown.

No text-to-permission/action path, download, concrete browser/model coupling,
credential lookup, memory/RAG/embedding/citation/history persistence, EventBus,
API/frontend/schema or dependency change. Prior production and tests are intact;
five migrations, head 0005_audit_events and 32 EventTypes remain unchanged.
PHX-055 generalized prompt-injection defenses remain separate future work.

## ADR-056 — Always-untrusted JSON prompt boundary and guarded research adapter
Status: Accepted. Date: 2026-10-07. Ticket: PHX-055.

PromptInjectionGuard exact-revalidates AgentRegistry/AgentDefinition and requires
neutral perimeter_security. GOJO is removable display metadata; PHOENIX remains
supreme and PermissionEngine alone authorizes actions. The guard is a frozen,
slotted stateless synchronous service, with no retained registry, cache or mutable
policy. Every external origin always stays untrusted, including no-signal content.

UntrustedContentItem preserves exact ordinary Unicode evidence and its UTF-8
SHA-256; IDs/field names are strict normalized bounded control-free strings.
Frozen extra-forbidden contracts reject stored-value/subclass/colliding-extra
forgeries, secret objects without coercion, NUL and malformed Unicode. Seven
origin categories are provenance only. Eight finite heuristic signals emit once
in canonical enum order. Temporary NFKC/casefold/Cc-Cf removal/whitespace scanning
never changes evidence. Explicit bounded substring tables perform no regex,
decoding, semantic classification or model call. Severity none/suspicious/high
is structural advisory signaling only, never trust, intent, permission or safety.

GuardedPrompt binds exact ordered items/assessments to perimeter_security and
phoenix-prompt-boundary-v1. Boundaries permit 0..64 items, <=50000 chars each,
<=200000 total evidence chars, and meaningful user task <=4096. Overflow rejects,
never truncates. A fixed code-owned PHOENIX preamble precedes one canonical JSON
payload containing user task, original evidence/provenance and assessments.
Sorted keys, compact separators and ASCII escaping are deterministic; additional
JSON Unicode escapes encode <, >, &, and backticks without deleting content.
Attacker newlines/delimiters cannot introduce structural header/footer lines;
json.loads recovers exact originals. Text cannot interpolate into policy. Rendered
text is sensitive ordinary str and must not be logged; separation is not proof
that a future model will obey the policy.

GuardedResearchSynthesizer implements unchanged PHX-054 ResearchSynthesizer.
Exact-revalidate request/nested sources before converting source-order fields:
requested_url, final_url, title, optional source_name/date_text/source_ref, content.
For page evidence, final_url/content use web_page; other metadata and snippets
use web_search. Content IDs hash canonical [source_id,field_name]; hashes bind
exact text. Query becomes separate user_task. Only GuardedResearchRequest reaches
the neutral GuardedResearchResponder ABC: research/responder IDs, UTC as_of,
1..8 ordered unique allowed source IDs and exact correlated GuardedPrompt.
No raw ResearchSourceEvidence/request or model-specific options reach responder.
Future responders must use render_text() for model input. One response is exactly
revalidated, including claims and research/synthesizer/known-source correlation;
original draft text/order remain untrusted. No retries, fallback or trusted flag.

One active caller synthesis per instance/event loop, second busy, no queue/worker.
Scoped cancel marks an independent flag before one exact responder cancellation,
revalidates neutral acknowledgement and cancels the still-owned caller task.
Repeated pending cancellation does not re-delegate. Suppressed cancellation cannot
return a draft; delayed acknowledgement cannot cancel unrelated subsequent caller
work. Active state clears in finally. Accepted acknowledges local cancellation,
not proven remote stop. V1 adapter OWNS responder: aclose requests active scoped
cancellation then attempts responder close exactly once, even after cancel failure.
Closed state begins before awaits; future synthesize/cancel raise closed. Concurrent
close callers wait for the first close; safe cleanup failure is retained, no retry.
Resources/deadlines/global model scheduling remain integration responsibilities.

No concrete responder/model, privileged tool invocation, permission/audit mutation,
secret resolution, URL fetch, logging, memory/RAG/citation/persistence, EventBus,
API/frontend, schema/migration or dependency change. PHX-054 research.py and all
previous production/tests remain unchanged. Five migrations, 0005_audit_events and
32 EventTypes remain intact. Finite heuristics have false positives/misses; encoded,
multilingual, visual, semantic and unknown jailbreak attacks are not fully covered.
No-signal never means safe; evidence is not silently sanitized. Upstream secret
admission and downstream ToolRegistry/PermissionEngine/audit remain necessary.
Zero-source synthesis rejects invalid_input because URAHARA already skips it;
metadata can overflow the guard's total budget even when source content alone fits.

## ADR-057 — Canonical PCM evidence and a provider-neutral wake boundary
Status: Accepted. Date: 2026-10-07. Ticket: PHX-060.

The conceptual owner is voice; UTA is removable display metadata, absent from
runtime authority. PHOENIX remains supreme. The immutable default phrase is
exactly phrase_id=rise_phoenix, text=Rise, Phoenix. No fuzzy text matching,
acoustic training, concrete wake engine, model artifact or dependency is added.
Future concrete providers require separately approved dependencies/model artifacts.

WakeWordProvider is a separate async ABC over caller-supplied audio, exposing a
strict read-only provider ID, abstract sorted immutable active-operation IDs and
capabilities/detect/cancel/aclose. Its constructor validates only identity and
allocates no runtime resource. Adapters own bounded operation state, deadlines,
cooperative cancellation and explicit cleanup; no global scheduling, queue,
selection, retry or fallback policy is imposed. Reuse unchanged PHX-010
CancellationResult/Status. ACCEPTED acknowledges one operation's cancellation,
without proving native/remote inference stopped or shutting down unrelated work.

PcmAudioFrame freezes nine fields, strict normalized control-free stream identity,
ordinary integer sequence 0..2147483647, explicit aware UTC capture evidence and
exact 16000 Hz / mono / two-byte / pcm_s16le format. Canonical RFC4648 standard
base64 must decode to nonempty even-length PCM <=32000 bytes and re-encode exactly;
the lowercase SHA-256 must match decoded bytes. Encoded input is bounded before
decode. Audio is omitted from repr/str. Deliberate pcm_bytes_for_provider() returns
revalidated exact bytes without caching, playback, conversion or I/O. Explicit
serialization/byte access is sensitive and caller-owned, never automatic logging
or persistence. This wake format does not freeze every future speech subsystem.

Frozen extra-forbidden phrase/capability/request/detection contracts reject stored
forgeries, subclass extensions, missing fields and injected/colliding extras.
Nested caller containers become detached exact snapshots. Capabilities contain
1..32 phrases, unique IDs/exact texts, fixed audio format and strict cancellation
support. Consumers exact-revalidate and correlate provider identity; technical
support proves neither availability nor authority and selects no provider.

Requests carry only operation_id/phrase/frames. Windows contain 1..32 frames from
one stream, contiguous +1 sequences, nondecreasing UTC timestamps and <=96000
decoded bytes. Overflow/gaps/reordering fail without truncation or repair.
Detections carry only operation/provider/phrase/stream/range IDs, strict detected
bool and ordinary finite float score 0..1. validate_request() revalidates both
sides and requires all six correlation values, including the full first/last
frame range. Score is provider evidence, not guaranteed calibrated probability,
speaker identity, permission, health or task confidence. It never overrides the
provider's detected flag. Closed safe errors suppress private input/backend
diagnostics, including audio and malicious extra-field names.

Positive detection starts nothing. No microphone/device access, VAD/STT/TTS,
conversation/debounce/barge-in/startup health, EventBus publication, tool,
permission, audit, model inference, prompt guard, memory/RAG, secrets, network,
audio persistence/logging, API/frontend or schema integration is introduced.
Existing production/tests/manifests, five migrations, head 0005_audit_events and
32 EventTypes remain unchanged. Text remains usable independently; voice failure
handling/text fallback belongs to later integration. Deterministic tests use
synthetic PCM and two independent test-only provider implementations, with
guarded uncached import/construction/validation/positive-detection/cancel/close.

## ADR-058 — Exact frame-level VAD and bounded stdlib energy evidence
Status: Accepted. Date: 2026-10-07. Ticket: PHX-061.

Conceptual owner voice; UTA remains display metadata and PHOENIX supreme.
VadProvider is a separate neutral async capabilities/classify/cancel/aclose ABC
with read-only identity and sorted immutable observational operation IDs. Reuse
unchanged PHX-060 AudioEncoding/PcmAudioFrame and PHX-010 cancellation contracts.
No wake-provider dependency or default phrase modification is introduced.

Implement the public phoenix.voice.vad module as vad/__init__.py. The preferred
flat vad.py conflicts with PHX-060's live top-level file-layout assertion. A
tentative historical-test repair then conflicts with PHX-054's protected-test
integrity gate. Using one package module preserves the same public import path
and every prior source/test Git blob, without weakening either regression.
The tentative test edit was fully restored; no prior test is changed.

Frozen extra-forbidden capabilities/request/decision contracts exact-revalidate
stored values, reject subclasses/injected/colliding extras and detach PCM frames.
Durations are exactly ms_10/ms_20/ms_30, admitting exactly 320/640/960 decoded
bytes at 16000 Hz mono two-byte pcm_s16le. Valid PHX-060 frames with another size
fail unsupported_audio, without conversion/rechunking/padding/truncation/repair.
Capabilities require unique canonical-order durations and exact provider identity.
Decisions bind operation/provider/stream/sequence/UTC capture time/duration exactly.
Activity is silence/speech; ordinary finite float score 0..1 is provider-local
evidence and never determines activity at the neutral layer. Neither grants
permission, human-speech certainty, identity, intent, task success or startup health.
Closed safe errors expose no PCM/base64/hash/threshold/backend diagnostics.

EnergyVadProvider defaults to energy_vad_v1 and adapter-local -42.0 dBFS threshold.
Explicit thresholds require ordinary finite floats in [-90.0,-10.0], never request
options. Decode through pcm_bytes_for_provider(), interpret signed little-endian
16-bit samples, calculate sqrt(sum(sample^2)/sample_count), use -96.0 dBFS for
zero RMS or 20*log10(rms/32768.0), and clamp to [-96.0,0.0]. Speech iff dBFS >=
threshold, including equality. Score is clamp((dBFS+96.0)/96.0,0.0,1.0), normalized
energy rather than calibrated speech probability. At most 480 samples are analyzed
without awaits/worker handoff. Active IDs stay (); cancellation is UNSUPPORTED.

Construction binds no loop and starts no work. First async call binds the current
loop; all later cross-loop calls fail wrong_event_loop, including after closure.
Same-loop aclose is idempotent and marks closed; capabilities/classify/cancel then
fail closed. Reinitialization cannot replace configuration/lifecycle or reopen.
Only configuration/loop/closed state is retained, never PCM/request/result history.

This baseline is an amplitude gate: noise/music false positives and quiet-speech
false negatives are expected. No adaptive noise floor, speaker discrimination,
debounce/hangover or segment aggregation exists. Caller owns exact chunking/timing;
PHX-064 owns conversation/barge-in policy. No microphone/device, wake, STT/TTS,
startup, EventBus, tool/permission/audit, model/prompt/secrets/memory/RAG, audio
logging/persistence, API/frontend/schema or dependency integration is introduced.
Five migrations, head 0005_audit_events and 32 EventTypes remain unchanged.
Future better VAD adapters require separate explicit dependency/model authorization.

## ADR-059 — Bounded provider-neutral STT and inert transcript evidence
Status: Accepted. Date: 2026-10-07. Ticket: PHX-062.

Conceptual owner voice; UTA remains display metadata and PHOENIX supreme.
SttProvider defines capabilities/transcribe/cancel/aclose with read-only identity
and immutable sorted observational operation IDs. Reuse unchanged PHX-060 PCM
and neutral cancellation evidence. No provider routing, retry or fallback policy
is embedded; adapters own bounded operation state/deadlines/cooperative cleanup.
ACCEPTED cancellation acknowledges the request, not proven native cessation.

Use the ticket-authorized stt/__init__.py layout for public phoenix.voice.stt,
preserving PHX-060's live top-level file assertion and all prior tests/sources.
Frozen extra-forbidden exact capability/request/transcript contracts detach and
revalidate stored values, rejecting subclasses and forged extras/collisions.
Capabilities admit only 16000 Hz mono two-byte pcm_s16le and strict support flags.
Requests admit 1..256 exact PCM frames, one stream, contiguous sequences and
nondecreasing UTC capture times, capped at 960000 decoded bytes / 30 seconds.
No repair, resampling, conversion, rechunking, padding or truncation occurs.

PCM byte access deliberately concatenates only bounded decoded bytes in supplied
order, without a container, cache, logging, persistence or I/O. Duration is exact
decoded bytes / 32000.0. Caller owns frames and window selection; PHX-064 owns
utterance/conversation policy. Language hints/results are opaque optional tokens,
trimmed only outside, preserving case/Unicode, without locale/taxonomy/selection
inference. Hint does not fabricate or constrain reported language/confidence.

Transcript correlation binds operation/provider/stream and first/last sequences.
Text preserves exact Unicode/whitespace, including empty evidence, capped at
20000 characters and excluded from repr. Optional language confidence is an
ordinary finite float 0..1 requiring a language, and stays provider-local evidence.
Text/language/confidence are neither authentication, speaker/user identity,
permission/approval, intent, task/startup success nor an executable instruction.
Closed safe errors expose only canonical codes, never private audio/text/backend
diagnostics. No automatic prompt guard or transcript-to-memory integration exists.

No concrete Whisper/faster-whisper/Vosk/cloud engine, STT model or new dependency
is introduced. Future local STT requires separately approved runtime/model
artifacts. No microphone/device, wake/VAD invocation, TTS, conversation/barge-in,
startup, EventBus, tool/permission/audit/model, memory/RAG/secrets, persistence,
API/frontend or schema integration is added. Existing source/tests/manifests,
five migrations, head 0005_audit_events and 32 EventTypes remain unchanged.
Two independent test-only adapters plus guarded fresh/uncached module execution
provide deterministic substitution, correlation, cancellation and inertness evidence.

## ADR-060 — Provider-neutral TTS and bounded canonical synthesis evidence
Status: Accepted. Date: 2026-10-07. Ticket: PHX-063.

Conceptual owner voice; UTA is removable display metadata, with PHOENIX supreme.
The public phoenix.voice.tts package defines a neutral async TtsProvider ABC with
read-only provider identity, sorted immutable observational active IDs and
capabilities/synthesize/cancel/aclose. Reuse existing CancellationResult/status
semantics; ACCEPTED acknowledges cancellation rather than proving native inference
stopped. Consumers exact-revalidate returned evidence and correlation. Adapters
own bounded state/deadlines/cleanup; the base owns no runtime resources or routing,
retry, fallback, playback or conversation policy.

Frozen extra-forbidden profile/capability/request/audio snapshots reject forged
stored values, subclasses and injected/colliding extras. Nested profile/language
tuples are detached, ordered and unique. Capabilities admit 1..64 profiles, exactly
one original primary, with optional original/licensed/consented profiles. Declared
provenance is evidence, not independent proof of legal rights, consent or identity.
No bundled profile, celebrity/character identity, cloning sample or embedding exists.

Requests preserve exact meaningful UTF-8 Unicode text/whitespace up to 4096
characters, reject NUL and exclude text from repr. Voice is explicit; optional
language tokens are opaque, case-preserving and at most 32 characters. Missing
voice fails unsupported_voice; a present unsupported hint fails unsupported_language.
No implicit primary fallback, language substitution or backend request option exists.

Results contain canonical standard base64 for nonempty even raw mono S16LE PCM
at a provider-declared strict integer 8000..48000 Hz, with exact lowercase SHA-256.
Bound encoded input before decode; decoded PCM is at most 8388608 bytes and at
most 120 seconds. Byte access and duration exact-revalidate without I/O or cache.
Bind operation/provider/voice/rate/channels/width/encoding to request/capabilities.
Hashes and duration do not prove semantic synthesis correctness. PCM is excluded
from repr; deliberate serialization/byte access remains sensitive caller data.
Safe errors expose only a closed category, never private text/PCM/backend details.

Coqui XTTS remains an optional candidate. No concrete engine, model, voice sample,
new dependency, audio device or network synthesis is introduced. Future concrete
local TTS requires separate dependency/model/voice-profile authorization. PHX-060,
061 and 062 production behavior remains unchanged; only their pure neutral helpers
and cancellation evidence are reused. PHX-064 owns playback/conversation/barge-in;
PHX-065 owns startup. No fixed-phrase cache, automatic memory, EventBus, privileged
tool, permission, audit, model/RAG/secrets, persistence, API/frontend or schema
integration exists. EventType remains 32 and sole migration head 0005_audit_events.

Testing compatibility: remove only stale historical repository-diff guards in
tests/test_vad.py, tests/test_stt_provider.py, tests/test_search_provider.py and
tests/test_urahara_research_flow.py. Their ticket-era broad diffs/dynamic origin/main
hash equality prevented later legitimate tracked additions or test maintenance.
Scope validation now belongs to active-ticket/final-diff review. No functional,
security, provider, credential or semantic assertion was weakened; no skips/xfails
were added. All other syntax in those files is unchanged. Their 403/414/400/492
case counts are unchanged and all pass before staging and with TTS actually staged.

## ADR-061 — Caller-driven voice conversation and scoped playback interruption
Status: Accepted. Date: 2026-10-07. Ticket: PHX-064.

Conceptual owner voice; UTA remains removable display metadata. PHOENIX retains
supreme authority. Six voice-only states are sleeping/listening/thinking/speaking/
degraded_voice/text_only, without PHOENIX ready/degraded health inference. Caller
activation starts a session; PHX-065 owns wake/startup health and startup speech.
No WakeWordProvider invocation or direct PhoenixOrchestrator coupling is added.

Use conversation/__init__.py for the public phoenix.voice.conversation import,
following VAD/STT/TTS packages. The preferred flat conversation.py would violate
the unchanged PHX-060 top-level file-layout regression. No package export or prior
source/test repair is needed. Reuse existing pure PCM/identifier/stored-evidence,
VAD/STT/TTS and neutral cancellation contracts without changing their semantics.

Exact frozen extra-forbidden observations correlate caller-supplied frames/VAD
decisions via reconstructed VadRequest. Activity drives segmentation and barge-in;
score does not derive activity or identity. A synchronous segmenter defaults to
2 consecutive speech frames, 10 end-hangover silence frames and a total 256-frame
buffer cap. Start frames are included, leading silence discarded, short internal
silence retained if speech resumes, terminal silence excluded. The cap includes
pending/trailing buffers; accepting any extra buffered observation beyond it resets
and fails segment_too_long, without truncation. Stream/gap/time/invalid-evidence
failures reset. Segments have 1..256 exact contiguous same-stream frames with
nondecreasing timestamps and <=960000 decoded PCM bytes.

VoiceResponder and AudioOutput are caller-owned neutral async ABCs with no concrete
backend. Response request/draft text is exact meaningful UTF-8, private in repr,
bounded to 20000/4096 characters and correlated to session/turn. Playback request
contains exact TtsAudio plus IDs; result contains playback/output/status only.
Canonical statuses completed/interrupted and exact cancellation acknowledgement
correlation do not independently prove native playback stopped. All contract
admission/integrity checks map to closed safe errors without private diagnostics.

Manager construction binds no loop, invokes no collaborator/clock/event and starts
no task. First async use binds one loop; cross-loop use fails wrong_event_loop.
One active session/turn executes in the caller task: STT capabilities/transcribe,
responder, TTS capabilities/synthesize, output play; each once with exact evidence
and correlation, no routing/retry/alternate provider. STT failure yields text_only
without text; TTS/playback failure preserves transcript/response in degraded_voice.
Degraded input turns use STT/responder only and voice_output_unavailable fallback.
Responder failure restores listening and raises responder_failure. Explicit
activation is required to recover voice output; arbitrary transcript commands are
not parsed. Injected monotonic clock drives a 30.0-second follow-up deadline;
expire_if_idle is caller-driven, listening-only, with no hidden timer/task.

Barge-in defaults to 2 consecutive speech observations; silence resets. Interrupt
only exact active playback once, validating canonical ACCEPTED provider/playback
acknowledgement before owning interruption. Barge-in owns interrupted/listening,
refreshing deadline and publishing one trailing listening event. Explicit sleep
owns interrupted/sleeping, clears session/deadline/segmenter/counters and emits no
trailing listening. A later completed playback result cannot override either
accepted ownership. NOT_FOUND/UNSUPPORTED/malformed/wrong acknowledgement or safe
output failure raises cancellation_failure, without fake interruption/sleep.
Sleep during thinking is rejected; unrelated STT/TTS/tools/models/tasks are never
cancelled. A local asyncio lock serializes acknowledgement/events with completion,
preventing delayed-ack races or duplicate interrupts; it creates no worker.

Publish only existing voice.listening/voice.speaking/voice.interrupted, agent_id
voice, correlation=session, payload session/turn IDs only. No text/PCM/hash/permission
payload. Normal order listening/speaking/listening; barge-in adds interrupted;
explicit speaking sleep has no trailing listening. Publish before committing state
where practical. Event failure is explicit event_failure, never fabricated delivery.
Accepted interrupt ownership may precede failed publication; the turn then aborts
to sleeping with cleared bookkeeping and an explicit error. Cancelled interrupt
publication likewise prevents observable-success finalization.

Every awaited collaborator boundary checks caller cancellation/closed/event-stop
state. Caller CancelledError propagates; suppressed cancellation cannot advance
or finalize success. Cancellation aborts local state to sleeping, clears session
and active bookkeeping, and does not cancel guessed collaborator operations.
Same-loop close is idempotent, interrupts only active playback, clears local state
and closes no borrowed STT/TTS/responder/output/EventBus. Late turn continuation
after close cannot resurrect state. Post-close mutations fail closed.

No microphone, speaker/backend, devices, acoustic echo cancellation, concrete
STT/TTS/model, network voice path, hidden worker, permissions/tools/audit/model,
memory/RAG/secrets, content logging/persistence/cache, API/frontend/schema or new
dependency is introduced. Five migrations, sole head 0005_audit_events, 32 EventTypes
and all previous production/tests/manifests remain unchanged. Future integration
owns capture, real runtime, native deadlines and cancellation effectiveness.

## ADR-062 — Evidence-derived Rise-Phoenix startup and degraded policy
Status: Accepted. Date: 2026-10-07. Ticket: PHX-065.

PHOENIX owns startup/readiness (phoenix); voice/UTA supplies subordinate interface
evidence. Use phoenix.voice.startup as startup/__init__.py, preserving the PHX-060
top-level file-layout contract. No previous voice/agent/core semantics change.
No concrete wake/STT/TTS/output backend or runtime wiring is added.

RisePhoenixStartupRequest is an exact frozen extra-forbidden five-field snapshot:
startup/session IDs, bounded PHX-060 WakeWordRequest, explicit TTS profile/language.
Only canonical rise_phoenix / Rise, Phoenix phrase evidence is accepted. Wake
capabilities/detection run once, exact-revalidated and correlated. detected alone
drives startup; score has no authority. False detection returns None without events,
probes, TTS/output, KAIDO or conversation activation. No retry/provider fallback.

StartupHealthProbe is an abstract caller-owned async check with read-only canonical
subsystem. Constructor requires six probes in exact persistence/hardware/model_provider/
tools/voice/agents order, detached from supplied tuple/list. No default probe/result
or cached health exists. Each accepted startup invokes all six sequentially once,
including after ordinary degraded/unavailable evidence or voice failure. Exact
three-field StartupCheckResult binds startup/subsystem/status without details or
authority. Probe exceptions become unavailable without diagnostics; malformed or
mis-correlated evidence raises health_failure. Identity is checked before execution
and after returned evidence. Caller-supplied checks are structural evidence, not
independent authentication or proof of their implementation's thoroughness.

Freeze exact acknowledgement, healthy and degraded texts from the ticket:
- Wake command acknowledged. Initializing Phoenix network. Checking all agents and core systems.
- All agents online. All core systems operational. What's the mission?
- Phoenix initialized in degraded mode. Some core systems are unavailable. Continuing with available capabilities.

After phoenix.started, attempt acknowledgement through existing TtsProvider/AudioOutput
only. Each speech phase fetches exact capabilities, validates explicit profile/
language/provider, synthesizes once, correlates bounded TtsAudio and plays once with
exact completed result. Phase-specific bounded operation/playback IDs derive from
startup ID without retaining audio. No generated text, alternate voice or retry.
Acknowledgement TTS/playback failure forces degraded and skips all later speech,
while all actual health checks continue. Startup speech publishes no voice events.

READY requires six same-run healthy checks and no startup voice failure, cancellation,
close or error. Any unhealthy check or voice failure forces DEGRADED. Only degraded
startup invokes pure synchronous KaidoFailSafe.activate, once, with correlated
failsafe_id=startup_id, critical_subsystem_degradation, fixed safe reason and exact
basic_conversation/diagnostics/recovery_guidance order. No safe_limited_tool_routing
or recovery_result. Exact decision validation rejects identity/mode/trigger/reason/
capability/recovery/extras/enum forgeries. KAIDO selects policy only, grants no
permission and runs no tool/model/recovery. Invalid decision raises failsafe_failure.

Activate borrowed PHX-064 manager exactly once after derivation/policy. It must
begin sleeping and end correlated listening; only PHX-064 emits voice.listening.
Caller must not feed listening observations until startup returns. Provisional
ready attempts healthy final speech; failure downgrades the final result/text/event,
selects KAIDO if absent and never retries the degraded phrase. Already-degraded
speech failure remains degraded without a second policy activation or retry.
Eight-field RisePhoenixStartupResult enforces six canonical correlated checks,
exact text, voice failure/fail-safe consistency and final listening state. It carries
no raw wake/audio/backend evidence and grants no task/verification/permission success.

Publish only existing phoenix.started/ready/degraded, agent phoenix and correlation
startup_id. Started payload is startup/session IDs only; final adds outcome,
unhealthy subsystem names and voice-failure stage only. No private content, score,
PCM/hash, provider diagnostics, fail-safe reason or permission data. Normal order
started -> PHX-064 listening -> one ready/degraded. Started failure prevents checks
and activation. Final event failure returns no result, best-effort sleeps only the
matching activated session and retains event_failure even if rollback fails. Actual
borrowed manager state remains observable; delivered events cannot be retracted.

Coordinator construction calls no provider/probe/clock/event, performs no I/O,
starts no worker/task/timer and binds no loop. First async use binds one loop;
cross-loop use fails wrong_event_loop. One active startup runs in the caller task.
Every awaited collaborator boundary checks cancellation/close before advancement.
CancelledError propagates; suppression cannot advance later stages or return a
successful result. Active bookkeeping always clears. Same-loop close is idempotent
and closes no borrowed collaborator. Aborts after activation best-effort sleep the
matching session; rollback failure preserves original safe category/cancellation.
Caller/injected collaborators own outer/native deadlines and cancellation effects;
an already-entered publication/physical operation cannot be retroactively undone.

No microphone, continuous wake, devices, concrete backends, phrase PCM cache,
network-specific voice runtime, user-task/orchestrator/AIZEN/LEVI/MAHORAGA execution,
ToolRegistry/PermissionEngine/audit/model generation, memory/RAG/secrets/persistence,
API/frontend/schema/EventType or dependency changes. PHOENIX remains supreme,
P4/P5/audit authority unchanged; degraded fallback is explicit subordinate evidence.
Five migrations, sole head 0005_audit_events and 32 EventTypes remain unchanged.
This completes Phase 6's authorized contracts/integration, not deployment wiring.

The amended ticket also authorizes one historical deterministic-test stabilization:
only test_slow_optional_probe_does_not_consume_generation_deadline changes probe
sleep 0.025 -> 1.0 seconds and generation timeout 0.01 -> 0.5 seconds. Probe delay
still exceeds the generation deadline; all assertions and the separate blocking-
generation timeout test stay intact. PHX-016 benchmark production is unchanged.
The second clarification authorizes only the Ollama between-chunk deadline test
and a unittest.mock.patch import: operation timeout 10 seconds, first chunk normally,
then patch loop.time to clock()+20 only for the second advance. Neutral timeout,
resource cleanup and uncancelled-consumer assertions remain intact. The separate
real-time deadline tests and all PHX-011 Ollama production remain unchanged.

## ADR-063 — Permission-gated Spotify Web API and manual PKCE boundary
Status: Accepted. Date: 2026-10-07. Ticket: PHX-070.

Spotify is a tool integration in phoenix.tools.spotify, under existing media/BROOK
reasoning and PHOENIX orchestration. No agent/router/policy/runtime change. Register
nine exact tools: search/playback_state/devices at P1; play/pause/next/previous/
transfer/volume at P2. Revalidate exact frozen input, canonical intent and matching
PermissionRequest before existing PermissionEngine evaluation. Only ALLOWED can
resolve secrets or send HTTP. Denied/approval-required results report no attempt.
No caller risk downgrade, model-derived approval, alias or custom endpoint.

Bind resource to operation, SHA-256 of the query, types, limit, offset, media URI,
device, volume and transfer-play flag using canonical JSON. Query hashing avoids
literal query retention in permission evidence; it does not protect low-entropy
queries from guessing. Revalidate nested result/decision snapshots and correlate
call/request/actor/tool/risk/resource and successful response shape. Closed safe
errors never include raw HTTP bodies, headers, tokens or collaborator diagnostics.
P3/P4 audit rules remain untouched; no new mandatory durable audit for these P1/P2
actions. Spotify strings are inert third-party data, never permission or instruction.

Trusted account linking uses caller-owned manual Authorization Code with PKCE.
Generate bounded cryptographic state/verifier and S256 challenge; verifier and code
use runtime-only SecretMaterial. Construct consent URL without opening a browser
or hosting a callback. Caller supplies explicit client ID and registered redirect.
Only bounded HTTPS or HTTP loopback literal 127.0.0.1/[::1] redirects, no localhost,
aliases, unsafe HTTP, userinfo, query, fragment, wildcard or URI rewriting. Exact
callback state/redirect and single-use pending setup are required. Scope is only
user-read-playback-state plus user-modify-playback-state. No client secret, cookie,
environment credential discovery, implicit/client-credentials login or OAuth UI.

Fixed SecretStore references spotify.access_token / spotify.refresh_token contain
all persisted token material; no normal settings/database/memory persistence.
Temporary materials are closed even on failure. Validate the entire token response
before writes; store refresh then access. Existing multi-reference writes are not
atomic: a store failure may leave an earlier rotation written and requires explicit
reauthorization/recovery. No rollback can safely invent transactional guarantees.
On Web API 401 only, refresh once and replay the exact request once. Replace refresh
only when returned, preserving it otherwise. No other HTTP/shape/device failure
retries, hidden token timer or expiry guarantee. Current Spotify guidance requires
manual renewal when dashboard refresh tokens expire after six months.

Only api.spotify.com/v1 and accounts.spotify.com OAuth endpoints. Owned lazy HTTPX
client explicitly verifies TLS, disables trust_env/redirects, and has connect/pool
5s and read/write 10s deadlines inside a 30s total operation deadline. Check elapsed
time after synchronous store boundaries; native SecretStore work is not preemptible.
Bound successful decoded payload to 256 KiB, nesting to 32 and nodes to 10,000;
validate only selected minimal metadata. No raw failure body is parsed/retained.
Search supports track/artist/album/playlist, four unique types, current official
limit 1–10 per type (default 5), offset 0–1000, query <=512. Results are bounded to
40 items/64 devices, names <=512 and artist lists <=32. Null search entries are
unavailable, nullable/missing device IDs remain unknown, restrictions preserved.
State 204 means no active playback. Episode/local/unsupported playback types do
not add playable input capability. Play accepts one track URI or album/artist/
playlist context; optional explicit device, transfer exactly one device, strict
integer volume 0–100. Control 204 means API command acceptance, not audible proof.
Premium/Connect/account restrictions are explicit failures, never simulated success.

Runtime has one observable caller-owned operation and first-use event-loop binding;
no hidden task/worker/scheduler/cache. Cancellation propagates, active IDs clear,
and suppressed cancellation/close cannot advance or report success. Explicit close
is idempotent, wipes pending verifier and closes only owned HTTP client; injected
client and SecretStore remain borrowed. Trusted injected transports belong to the
owned test client; HTTPX closes them with it. Already-entered store/media operations
cannot be undone. Python/HTTPX immutable secret copies cannot be guaranteed wiped.

No audio streaming/cache/history, playlist/library mutation, memory/RAG/training,
events, schema/migration, FastAPI/frontend, model generation or new dependency.
AgentRegistry, ToolRegistry, PermissionEngine, AuditTrail, SecretStore, five
migrations/sole head 0005_audit_events and 32 EventTypes remain unchanged. Current
official API references and complete limitations are in the frozen ticket. Live
Spotify consent/Premium acceptance remains optional user-owned manual work.


## ADR-064 — Permission-gated YouTube discovery, neutral player and revision-bound queue
Status: Accepted. Date: 2026-10-08. Ticket: PHX-071.

YouTube remains a tool under neutral media/BROOK reasoning and PHOENIX supreme
orchestration. Exactly ten canonical declarations: search/player_state/queue_state
P1; cue/play/pause/seek/enqueue/remove/clear_queue P2. Existing ToolRegistry and
PermissionEngine remain unchanged. Validate exact call, canonical intent and matching
PermissionRequest before any secret/network/player/queue effect. Non-ALLOWED yields
attempted=false and zero effects. Caller/model/provider/player text cannot approve,
lower risk or override endpoints. P4/P5 and P3/P4 durable audit remain unchanged.

Public discovery uses one GET https://www.googleapis.com/youtube/v3/search with
part=snippet, query, ordered types and maxResults only. Fixed borrowed SecretStore
reference youtube.api_key supplies X-Goog-Api-Key; never a URL parameter. Build the
request directly to avoid borrowed-client default parameters/cookies/auth headers;
auth and redirects are disabled for the send. Close temporary SecretMaterial.
Reject reflected keys in normalized evidence and a query containing the resolved key.
No logging configuration mutation or post-request URL redaction. Python/HTTPX immutable
header copies cannot be guaranteed wiped. Trusted injected clients/transports own their
configuration; an owned client's injected transport follows that HTTPX client's lifetime.

Search video/channel/playlist only, meaningful query <=512, 1..3 unique ordered types,
strict max_results 1..10/default 5. One request, no pagination/retry/prefetch. Successful
body <=256 KiB, JSON depth <=32/nodes <=10000, duplicate/nonfinite JSON rejected;
strings <=4096 during decoding, selected names <=512, timestamps bounded/aware UTC,
video IDs 1..64 and channel/playlist IDs 1..128 ordinary opaque ASCII tokens. Return at
most max_results total minimal metadata; URLs are derived by code. Titles/channel names
remain inert untrusted data, never automatic instructions or memory/RAG/model content.
Only bounded <=8 KiB structured 403 reason evidence distinguishes quotaExceeded;
raw failure bodies/messages are neither returned nor retained. 429 has no sleep/retry;
5xx/network are unavailable; malformed success is invalid_response.

Strict video refs admit 1..64 opaque IDs and only the four ticketed exact HTTPS forms.
No userinfo, ports, fragments, extra/duplicate/encoded query keys, aliases/lookalikes or
redirects. Normalize immediately to ID and canonical watch URL; syntax proves neither
existence nor embedding availability. Borrowed YouTubePlayerAdapter is an async ABC for
observe/cue/play/pause/seek only. Exact snapshots correlate operation/player identity,
requested video and seconds. Stable configured player identity also binds permission
resources. Neutral unstarted/ended/playing/paused/buffering/cued/unavailable states carry
no permission authority. Start/seek integers and reported finite position are bounded
0..604800; booleans rejected. Commands report correlated acceptance only, never physical
playback, rendering, autoplay permission or user viewing. No readback/policy inference.

Queue is an immutable ordered in-memory tuple, <=50 entries, duplicates allowed; each
entry holds only canonical video ID/watch URL. No title/blob/persistence, search or
auto-play. Revision begins 0 and increments once on a successful content-changing enqueue,
remove or nonempty clear; reads, denials, failures and empty clear do not increment.
Remove requires strict index and expected_queue_revision; both bind the permission
resource. Recheck revision/index after ALLOWED immediately before synchronous mutation;
stale/out-of-range fails existing invalid_input with unchanged state and no player/HTTP.
Snapshots are detached immutable evidence, without cross-process or permission authority.
Close clears the queue and closes the runtime permanently; revision need not be retained.

Resources bind operation, query digest, ordered types/limit, canonical video, start/seek,
index/revision and configured player identity, never literal query/key. Hashes correlate
data, without hiding low-entropy queries from guessing or authenticating consent.
Exact frozen extra-forbidden call/intent/result/nested snapshots reject subclasses,
extras, copied malformed/stale/mis-correlated evidence and impossible outcomes. Closed
20 safe error categories replace provider/adapter diagnostics. A canonical watch URL is
fallback evidence only; future PHOENIX browser.navigate needs its separate existing P2
permission. YouTube imports/invokes no BrowserTool and forwards no approval.

Import/construction performs no I/O. One event loop and observable caller-owned active
operation; no hidden task/thread/worker/scheduler/player watcher. Owned lazy HTTPX client
verifies TLS, trust_env=False, follow_redirects=False; connect/pool 5s, read/write 10s,
inside total 30s. Native synchronous store work is non-preemptible and checked after
return. Cancellation propagates; suppressed cancellation/close cannot advance or return
success. Finally clears the active ID; in-flight close retains it until the caller exits.
Same-loop idempotent close closes only owned HTTPX, never borrowed player/store/client.
Already-entered network/player effects cannot be undone; collaborator cooperation and
real adapter lifetime/physical cancellation remain integration responsibilities.

No iframe/React/Tauri/API/WS/Playwright player, OAuth/private-account data, transcripts,
downloads/stream extraction, playlist mutation, embeddings/RAG/memory/training/cache,
EventType, schema/migration or dependency addition. EventType 32; five migrations/sole
head 0005_audit_events; Spotify/BROOK/core/security/voice production unchanged. Concrete
embedded-player/frontend implementation remains deferred. Live search needs a user-owned
Google project/restricted key; live player acceptance needs the later concrete adapter.

The two authoritative clarifications authorize only a test-only PHX-060 compatibility
repair: preserve BASELINE e58d304df3a2097ae03e0b31941c9cf487a584f9 for voice/__init__.py;
add PHX060_COMPLETION 4cc0feb59ae91bc77b72c7a3a0f1a7885f397ee3 for audio.py/wake.py,
which did not exist before PHX-060. Replace only the named test's broad successor diff
with those exact historical comparisons, preserving EventType/Alembic and all other
test syntax. This freezes original PHX-060 blobs without rejecting legitimate new tools.
No skip/xfail/retry/flaky/bypass; 537 cases remain. No other historical repair authorized.


## ADR-065 — Bounded upload-byte routing into the existing ingestion boundary
Status: Accepted. Date: 2026-10-08. Ticket: PHX-072.

PHOENIX remains supreme; ROBIN/document_analyst owns analysis/generation and
VEGAPUNK/knowledge_rag owns indexing/RAG. These adapters create no new role/model
process or permission authority. Exact frozen UploadDocument accepts trusted
caller-supplied bytes and bounded display filename, optional compatible ASCII MIME
and aware timestamp. No arbitrary metadata. Immutable runtime routes feed exact
existing PHX-032 DocumentSource/DocumentParser contracts with opaque upload:<id>,
canonical MIME, exact bytes/identity, UTC timestamp and code-owned filename/format.
PHX-032 remains solely responsible for revalidation/correlation, mandatory admission,
chunking/hash/dedup, persistence and active ingestion cancellation. No duplicated
ToolRegistry/PermissionEngine, privileged path read, source dereference or DB access.

V1 routes TXT, Markdown, CSV, exact ticketed source-code extensions, text-layer PDF,
DOCX and PNG/JPEG/WebP to an explicit borrowed image parser only. Text/code/Markdown
use strict UTF-8-SIG (BOM removal only), meaningful exact text and no NUL. Code,
Markdown, HTML and formula-looking CSV remain inert. Strict stdlib CSV preserves
logical row order, exact cells as compact Unicode JSON arrays, empty rows/cells and
row provenance; native field bounds are restored synchronously. No sniffing, render,
eval, imports, model/prompt automatic ingestion or instruction-derived permission.

All twelve explicit strict positive integer parser limits are required: source bytes,
retained text/segments, PDF pages/content-stream bytes, DOCX entries/member/total
uncompressed bytes/ratio, CSV rows/columns/cell characters. Booleans rejected;
member <= total. Input admitted before concrete parsing; no silent limit rewriting.
Extension/MIME/signature must agree; no unsupported-content guessing or fallback.
Closed twelve-code errors remove private/provider diagnostics; bytes are excluded
from input/route repr and serialization. Parsed text remains untrusted under INV-017.

Promote exactly pypdf>=6.19,<7 (6.19.0) and python-docx>=1.2,<2 (1.2.0).
Only lxml 6.1.3 is newly transitive; typing-extensions was already compatible.
All 42 unrelated lock records/versions/hashes remain unchanged, with Phoenix metadata
limited to two direct references. lxml is not a Phoenix direct dependency. No extras,
Pillow/Tesseract/OCR/runtime download or optional research promotion. Official
extraction/Document API references are recorded in docs/25_DEPENDENCIES.md.

PDF reads BytesIO with strict PdfReader, rejects encryption, retains exact meaningful
page text in source order with original page numbers. Enforce source/page/content/
text/segment bounds. Public native decompression bounds and disabled external JBIG2
decoder reduce exposure during synchronous calls. A scoped current-thread LogRecord
factory redacts pypdf diagnostics before handlers; original factory/native config
restored in finally, without changing logging levels/handlers or unrelated records.
No password, OCR, JS, attachment/image extraction or fetch. Native decompression and
extraction can materialize substantial memory: no hard process-memory ceiling or
preemption guarantee, and scanned PDFs with no meaningful text fail explicitly.

DOCX reads BytesIO only after stdlib ZIP metadata and bounded XML/relationship
preflight. Validate ZipInfo.orig_filename before normalized filename/required lookup;
reject raw backslash, absolute, drive/colon, traversal, NUL/control or changed spelling.
Unsafe normalized aliases cannot satisfy canonical package members or bypass unique
names. Retain required Content_Types/document members, no encrypted flags, entry/
member/total/ratio bounds. XML DTD/entities fail without external resolution. No
archive extraction/write. Supported iter_inner_content yields top-level paragraphs
and tables in source order; meaningful paragraphs and exact tab-separated rows retain
deterministic sections. Headers/footers/notes/comments/text boxes/embedded objects and
full Word/nested-table fidelity remain outside V1. No DOC/macros/archive/office extras.

Raw backslash tests patch equal-length filename fields in both local and central ZIP
headers only, proving original spelling before Phoenix and preserving offsets,
lengths and payload bytes. Canonical required-member acceptance, unsafe normalized
required-member/alias rejection and NUL/control cases are separately covered. The
fixture uses explicit stdlib os.sep, preserving cross-platform assertions without
zipfile.os access, ignores, xfails or skips.

Images have no built-in OCR: exact extension/signature/MIME routes only to a configured
borrowed existing DocumentParser; missing adapter explicitly parser_unavailable.
Router does not close/own it; PHX-032 revalidates its evidence before persistence.
Cancellation belongs to caller task with checkpoints around synchronous boundaries
and between pages/blocks/rows; CancelledError propagates and cannot finalize success.
Built-ins bind first loop; no hidden task/thread/process/scheduler or competing active
registry. Already executing synchronous library calls cannot be interrupted.

Four narrow dependency-guard changes are explicitly authorized: BrowserTool proves
immutable PHX-050 pre/post promotion commits separately from current Playwright 1.63.0;
screenshot and injection guards retain every protected source/schema/Event assertion
while PHX-072 owns exact manifests; bootstrap exact current direct-name set adds only
pypdf/python-docx with Requires-Python unchanged and lxml transitive only. Ruff changes
layout only in the two specified historical files. No other historical syntax changes.
Desktop runner cp1252 failure is external: direct npm commands validate unchanged
desktop, and no repository workaround is introduced.

No UI/API, schema/migration, EventType, tool/security, voice/media, memory/RAG production
or generation/export change. EventType remains 32; five migrations, sole head
0005_audit_events. PHX-073 stays TODO, Phase 7 incomplete. Deterministic in-memory
fixtures do not claim live production acceptance of user-owned documents or OCR.


## ADR-066 — Strict reports produce verified in-memory artifacts before export
Status: Accepted. Date: 2026-10-08. Ticket: PHX-073.

PHOENIX remains supreme; ROBIN/document_analyst owns document analysis/generation,
VEGAPUNK/knowledge_rag owns indexing/RAG. DocumentGenerator is a caller-owned local
renderer, with no tool/permission/model/agent, memory/RAG or persistence authority.
The unchanged PHX-043 FilesystemTool remains text-only. Future binary persistence
must cross a separate permission-gated integration and verify disk readability.
PHX-073 returns bytes and verified metadata only, never a saved-path assertion.

Frozen, strict, extra-forbidden contracts define exactly PDF/DOCX/Markdown and a
neutral ordered paragraph/bullet_list/table AST, sections and plain provenance.
Revalidate exact models, including copied/constructed/nested instances; reject
subclasses/extras/coercion and unsafe display filenames. All ten caller-supplied
positive non-boolean limits are required. Count every user-controlled text field
once, admit before rendering, enforce cumulative counts, never truncate. Artifact
bytes remain excluded from repr/ordinary serialization; exact hash/size/MIME,
correlated IDs, verified flag and format-specific page count are checked again.

Promote only reportlab>=5.0.1,<6, locked 5.0.1; Pillow 12.3.0 and charset-normalizer
3.5.2 are its only new transitive identities. Existing lock records remain exact;
no extras, types-reportlab, OCR or research runtime promotion. Reuse python-docx
1.2.0 and pypdf 6.19.0. Strict mypy remains enabled: only external untyped reportlab
and reportlab.* receive ignore_missing_imports; Phoenix-local typing is checked.

PDF uses ReportLab Platypus and built-in Helvetica/Helvetica-Bold, with code-owned
styles, margins, wrapping, bullet/table layout and numbered footers. All rendered
user text must encode exactly as cp1252; unsupported Unicode fails explicitly.
Escape every Paragraph input before inserting safe code-owned line breaks. Never
load images, fonts, templates, URLs or executable markup. A private ordinary
Exception sentinel with constant safe text crosses the native page callback;
catch its type outside build and map only it to limit_exceeded. Public domain
errors never enter native callbacks; classification never parses
native messages. Other native rendering failures remain safe generation_failed.

DOCX starts from a minimal code-owned empty OOXML package in BytesIO, avoiding
python-docx's default disk template. User content enters plain text APIs only;
styles, bullet numbering, grid tables and Sources are code-owned. Native save
uses ordinary BytesIO through completed ZIP serialization/cleanup. Reject excess
bytes after save and before structural verification/artifact success. Canonical
ZIP member order/timestamps make exact bytes deterministic, using an ordinary
buffer and checking its final size too. No mid-ZIP throwing writer/stream close,
warning suppression or zipfile monkeypatch. This is an artifact-acceptance bound,
not a hard streaming-memory ceiling.

Markdown uses stdlib UTF-8 with LF/final newline, deterministic punctuation and
backslash escaping, and literal backslash-n for user line breaks. Structure comes
only from the AST. Escaped HTML-looking literals remain inert: tests count preceding
backslash parity to distinguish escaped <img/<link from active openers, retaining
all injection cases without changing correct renderer behavior. Sources retain
caller order and plain references; no fetch, link, authority inference or ingestion.

Verification reopens exact bytes in memory: strict unencrypted bounded PDF pages
and extracted title evidence; unique unencrypted DOCX ZIP members, exact title and
paragraph/table traversal; Markdown strict UTF-8/title/newline/round-trip evidence.
Safe nine-code failures never return a normal artifact. Native PDF diagnostics are
redacted before handlers and the prior log-record factory restored. No disk/temp,
network, subprocess, hidden worker, API/frontend, EventType or migration changes.

One loop, one active ID and the caller task own generation. Busy/closed/wrong-loop
fail explicitly; same-loop aclose is idempotent. Check cancellation/closure around
native calls and between controlled iterations, through verification and immediately
before success; clear active state in finally. Synchronous native rendering/save/
extraction/compression cannot be hard-preempted or promise process-memory ceilings.
No format/font/model/OCR fallback, task scheduler or BLAZE-6 generation lease.

The two authorized historical repairs preserve exact PHX-072 pre/post dependency
history independently of later promotions, and add ReportLab alone to bootstrap's
exact current dependency names. No other protected source or tests are rewritten.
Deterministic focused coverage passes 284 x3; complete Python passes 15,721 x3 with
the same 16 pre-existing skips and one pre-existing warning. All required subsystem,
quality, package, desktop and 24 individual invariant reviews pass. Phase 7 is
complete; PHX-080 remains TODO/not started. No user-owned live export is claimed.

## ADR-067 — Local app-shell navigation preserves frontend privilege boundaries

Status: Accepted
Date: 2026-10-08
Ticket: PHX-080

Use the existing React/TypeScript/Vite/CSS stack in App.tsx and styles.css for
exactly fourteen stable views: onboarding, home, agents, tasks, memory, files,
computer, research, media, permissions, models, voice, settings and observability.
Local React state selects home initially; an unknown input resolves to home without
coercion, dynamic execution, router/history or browser-storage persistence.
PHOENIX is the single identity and remains the supreme orchestrator. Group labels
are presentation only and instantiate no agent or authority.

Native named buttons, aria-current, semantic landmarks/headings, a skip-to-main
link and visible focus support keyboard use. An effect moves focus to main only
after requested navigation commits; initial focus is undisturbed. Cinder/amber
CSS, system fonts, contrast checks, reachable sidebar scrolling, narrow stacking
and reduced-motion treatment adapt to the existing 800x600 native window.

The shell distinguishes local selection from future backend functionality. Home
previews future areas; thirteen other panels identify their purpose and explicitly
remain unconnected. No real or simulated chat, voice, health, task, permission,
model or memory success is offered. No HTTP/WebSocket, backend/provider/database,
tool, native IPC/plugin or permission execution is introduced. CSP, capability
grants, Rust, all manifests and lockfiles remain unchanged; no new dependency.
PHX-081 through PHX-087 own later feature composition and transport/action binding.

Evolve only the obsolete PHX-003 static-screen test into React SSR/pure/AST shell
contracts; retain its other three workspace/CSP tests unchanged. The authorized
PHX-055 guard excludes only the two owned shell files. PHX-052 and PHX-051 retain
every positive protected path, including apps, and append those same two exact Git
negative pathspecs. All prohibited-operation/import, EventType 32, migration head
and historical dependency assertions remain active. Normalize the four mixed LF
endings in the PHX-055 guard to its existing CRLF style; normalized bytes and Python
AST prove no unintended transformation. An external Git fixture proves other
desktop/protected paths and similar filenames still trigger the guards.

Exact historical tests pass 1 x3 each; prompt-injection passes 604 x3; screenshot
and Windows UI suites pass 397 and 493. Frontend passes 14 x3, strict TypeScript,
Vite and static Tauri/Cargo gates. Full Python passes 15,721 x3 with identical
16 existing skip identities/reasons and one existing Starlette/httpx warning.
Required regressions, lint, formatting, mypy, package parity and all 24 individual
invariant reviews pass. Manual WebView2 mouse/keyboard acceptance remains pending;
backend-connected frontend acceptance is not implemented. Phase 8 remains incomplete.

## ADR-068 — Honest chat / voice presentation with a borrowed send boundary

Status: Accepted
Date: 2026-10-08
Ticket: PHX-081

Replace only Home's preview using the existing App.tsx/styles.css and dependencies.
Keep PHX-080's fourteen route IDs/labels, brand/groups, default/fallback, native
navigation, skip link, committed-navigation focus and thirteen honest placeholders.
PHOENIX remains supreme; display roles/text never authenticate intent or permissions.
GET /health is the only implemented API; POST /chat and WS /events remain future.

Default App has no ChatPort, shows backend unavailable and disables Send. A native
controlled textarea accepts local React-memory drafts independently of voice. Raw
input is bounded at 4000 UTF-16 code units; empty/whitespace/oversize sends reject,
and admitted text is trimmed. App owns draft/flight state across navigation. No
browser storage, logs, DB, network, timers, polling or background workers are added.

The optional caller-owned ChatPort exposes only connected/unavailable/degraded,
readonly id/role/content snapshots and sendText(text): Promise<void>. Runtime data
checks reject malformed fields/methods without executing accessors; supplied callback
code remains trusted caller code, not a sandbox. There is no concrete transport.
Only connected admission calls the callback, once per in-flight request. A native
Promise resolving undefined is explicit acknowledgement; synchronous values,
arbitrary thenables, other resolved values and exceptions cannot clear a draft.
Only the unchanged submitted draft clears after acknowledgement; no local user or
assistant transcript entry is manufactured. Errors reveal only safe retry guidance.

Cancel waiting cancels the UI wait only, preserves the draft even after late ack and
states that delivery is unknown. Actual transport cancellation is unavailable. The
one-flight guard remains until settlement to prevent duplicate delivery. A forever
pending adapter remains pending; no speculative timeout or cancellation API is added.

Transcript rendering consumes only supplied snapshots: first 64 entries at most,
exact id/role/content fields, unique nonblank opaque IDs up to 128 code units,
user/assistant roles only and nonblank content up to 4000 code units. Reject malformed,
extra-field, duplicate or oversized entries, show an omission notice and never
partially clip security-critical text. Accepted text is JSX escaped with whitespace
and long-string wrapping; no HTML/Markdown/code/link/image interpretation or privileges.

Voice is explicitly unavailable, with an explained disabled microphone and text-first
fallback. No capture, wake/STT/TTS, playback or native audio bridge is introduced.
The top-level Voice placeholder remains PHX-086's responsibility. Native accessible
controls/statuses, bounded transcript scrolling, cinder/amber contrast, responsive
stacking at the existing 800x600 window and reduced motion remain in force.

Keep the three PHX-003 test bodies unchanged; evolve only obsolete Home assertions
and exact local helper calls, retaining provider/network/native/media/storage bans.
All three historical guards are byte-identical to main. Frontend passes 28 x3;
TypeScript/Vite/Tauri/Cargo gates pass (Cargo contains zero Rust tests). Historical
exact guards pass x3 each; full suites pass 604/397/493. All 18 required regressions
pass. Full Python passes 15,721 x3 with identical 16 existing skip identities/reasons
and the one existing Starlette/httpx warning. Ruff, formatting, mypy, wheel/sdist
source/dependency parity, health/security and all 24 invariant reviews pass.

No API/Python/Rust/Tauri/config/manifest/lock/dependency changes. Fake adapters prove
only deterministic UI contracts; live chat and live voice acceptance are NOT
IMPLEMENTED. Manual desktop GUI/WebView2 end-to-end acceptance remains PENDING.
Phase 8 remains INCOMPLETE; PHX-082 through PHX-087 remain TODO/not started.

## ADR-069 — Canonical agent directory with passive borrowed status evidence

Status: Accepted
Date: 2026-10-08
Ticket: PHX-082

Replace only the agents placeholder in existing App.tsx/styles.css with a directory
of 30 frozen logical identities: one PHOENIX supreme orchestrator and 29 specialists.
Preserve exact internal IDs, display aliases, professional roles and source order
from Python DEFAULT_AGENT_DEFINITIONS and docs/03_AGENT_ROSTER.md; deterministic
Node tests compare every triple against both sources. This display reference never
becomes a runtime registry, executes agents or implies initialization/online state.
AIZEN plans, LEVI verifies, MAHORAGA recovery is bounded and KAIDO degraded-only.

App-owned local search/selection survive navigation without changing chat's borrowed
send boundary or frozen helpers. Search accepts strings up to 80 UTF-16 code units,
matches alias/ID/role case-insensitively and preserves source order. Clear restores
30. Default ID is phoenix; only exact canonical internal IDs select details. Unknown,
alias or object input keeps the last valid selection. Off-list selection is explicitly
labelled with Clear search guidance. Native named buttons expose aria-pressed and
aria-controls; accessible match count/details, responsive scrolling/wrapping and
unchanged shell focus/skip/reduced-motion behavior require no new framework.

Optional readonly AgentPort has only connected/unavailable/degraded status and
readonly records: internalId, state, optional currentTask/provider. No operations,
transport or default instance. Connected validated snapshots are labelled supplied
presentation evidence; absence says Live agent state not connected and Status not
reported. Unavailable/degraded suppress activity. Admit the whole snapshot only
after validating <=30 unique known IDs, closed idle/working/waiting_permission/degraded
states, own data fields only, task <=240/provider <=120 UTF-16 units. Any malformed,
duplicate, extra, accessor, sparse/extended or oversized input rejects all status
with generic notice. Copy primitives, never clip task/provider text or expose errors.
JSX escapes all untrusted text; aliases/status cannot authenticate or grant permission.

Data descriptors avoid getters/coercion/ordinary Proxy get traps; reflective failures
are caught. Browser JS does not sandbox hostile same-realm Proxy reflective traps
or distinguish a forwarding proxy from data. The caller is trusted infrastructure,
and admission is presentation validation rather than authenticity or code isolation.
The port is never mutated and no arbitrary supplied method is invoked.

Preserve all fourteen shell routes and PHX-081's entire chat helper/UI code block,
draft limits, pending/ack/error/local cancellation, plaintext history and disabled
voice/text fallback. Twelve other views stay honest placeholders. No GET /agents,
backend/network/provider/native call, agent execution/dispatch/reconfiguration,
permission action, persistence, timer, polling or dependency is added. Manifests,
locks, Tauri restrictive CSP/empty capabilities, Rust and all Python remain unchanged.

Node 45 tests pass x3; TypeScript/Vite/Tauri/Cargo pass (Cargo has zero Rust tests).
Historical exact PHX-055/052/051 guards pass x3 with unchanged files; full suites
604/397/493 and all 18 required individual regressions pass. Full Python passes
15,721 x3 with the same 16 skip identities/reasons and one Starlette/httpx warning.
Ruff, format, mypy, all 77 Python package source/dependency parity, unchanged health,
credential scan and individual INV-001..024 review pass. All validation receipts
are external. Live agent status/control are NOT IMPLEMENTED; manual WebView2
end-to-end acceptance is PENDING. Phase 8 remains INCOMPLETE; PHX-083..087 TODO.

## ADR-070 — Read-only source-priority task graphs from bounded borrowed evidence

Status: Accepted
Date: 2026-10-08
Ticket: PHX-083

PHX-083 adds a read-only Task Graph workspace to the existing React/TypeScript/CSS
shell. Default App supplies no TaskGraphPort and shows No task graph connected with
no invented task rows/progress. Only an explicitly connected, validated borrowed
snapshot provides task ID, optional task-level state, steps and metadata. Unavailable
or degraded ports suppress all graph/state; malformed input shows a generic notice.

TaskGraphPort is passive readonly data: status connected/unavailable/degraded and an
optional graph containing taskId, optional taskState and ordered steps. Each step has
only stepId, ownerAgentId, objective, dependsOn, inputRefs, expectedOutput,
timeoutSeconds and completionRule. Canonical agent IDs alone establish owner identity;
PHX-082 aliases are separate display metadata. There are no callbacks or action methods.

Exactly eleven serialized task states match Python PHX-024: created, planning, ready,
running, waiting_permission, verifying, completed, failed, recovering, degraded and
cancelled. No per-step runtime status or automatic transition exists. The qualifier
Caller-supplied task state; not live transport or verified completion stays visible.
Completed is not execution/LEVI evidence; waiting_permission is not an approval request.

Whole-graph admission requires own data fields, nonblank exact task/step IDs <=128
UTF-16 units, 1..40 steps, exact known owner IDs, meaningful objective <=600,
expectedOutput/completionRule <=400, finite numeric timeout >0 and <=86400, <=12
dependencies per step and <=120 total edges, <=8 input refs each meaningful <=128.
Reject wrong types, unsupported states, extras/symbols/accessors, sparse or extended
arrays, duplicate step IDs/dependencies, unknown/self dependencies and cycles as a
whole. No text truncation, dependency repair or partial private-data display.
Input refs may repeat, matching PHX-024. Caller inputs are never mutated; only detached
frozen primitive/array snapshots render after acyclicity proof.

Pure Kahn traversal repeatedly picks the earliest original source position among all
eligible nodes, including newly eligible earlier nodes. A bounded forty-node scan
matches Python's heap semantics without a graph dependency. Roots retain source order;
edge count derives only from admitted dependencies. Ordered native cards show inert
Depends on text, root/downstream designation and structural dependency layers.
Structure never establishes runtime readiness, completion, permission or verification.

App-owned local selection starts at the first topological step, accepts only current
graph step IDs, survives navigation and resets when task identity changes, the selected
step disappears or the port disconnects. Guarded React render adjustment compares
identity fields only; there is no background effect, timer or task state mutation.
Details show canonical owner/alias, objective, expected output, completion rule,
timeout definition with no running timer, dependencies and inert input references.

Native named selection buttons use aria-pressed/aria-controls; labelled sections and
polite selection feedback complement unchanged navigation focus/skip behavior. Wrapped
plain text and bounded step scrolling stack below 1000px for the unchanged 800x600
window. Existing contrast, narrow navigation and reduced-motion treatment remain.
HTML/Markdown/command-looking IDs, objectives, rules and refs are JSX-escaped text.
Descriptor checks avoid getters/coercion/ordinary Proxy get traps, catch reflective
failures and reveal no exceptions. Reflective traps may run: same-realm JS is not a
sandbox, and snapshot shape does not authenticate activity or hard-limit browser memory.

PHX-080's fourteen destinations/default/fallback/branding/groups/focus remain intact.
PHX-081's chat code and PHX-082's entire roster/search/status/UI block stay byte-equal
to main, apart from task-view composition. Eleven remaining views stay placeholders.
No GET/POST /tasks, WS/events, execution, cancel/retry, approval, verification action,
provider/tool/native call, persistence or new dependency. Python/backend/Rust/Tauri
configs/CSP/empty permissions/manifests/locks remain unchanged. PHOENIX stays supreme.

- npm ci --ignore-scripts PASS; all manifests/locks unchanged, zero audit vulnerabilities.
- npm test THREE final consecutive runs: 65 passed each, zero failures/skips.
- TypeScript, Vite build, Tauri info, Cargo check/test and Tauri release build PASS;
  Cargo defines zero Rust tests. No GUI or dev server was launched.
- Exact PHX-055/052/051 guards 1 passed x3 each; historical files unchanged.
  Whole prompt/screenshot/Windows suites 604/397/493 passed.
- All 18 required individual regressions PASS: API 8, bootstrap 11, registry 96,
  router 213, ToolRegistry 204, permissions 926, audit 302, secrets 402,
  Spotify 345, YouTube 358, documents 284, upload 336, startup 509,
  conversation 950, TTS 602, STT 414, VAD 403, wake 537.
- Additional PHX-024 test_task_graph_state_machine.py: 348 passed unchanged.
- Full uv run --locked pytest THREE consecutive runs: 15,721 passed each,
  same 16 existing skip identities/reasons and one Starlette/httpx warning as main.
  No new skip/xfail/retry/flaky workaround/filter or warning suppression.
- uv sync --locked PASS: 49 packages; Ruff check PASS; format PASS: 133 files;
  strict mypy PASS: 127 source files.
- External wheel/sdist PASS: all 77 Python sources byte-equal to worktree and
  Git-normalized main; direct dependency metadata and Requires-Python unchanged.
  Exact GET /health regression and API source parity PASS; no private artifacts.
- Credential signature/source/native boundary scan and individual INV-001..024 PASS.
- Full unstaged/staged diff review, git diff --check and cached --check PASS.

LIVE TASK GRAPH ACCEPTANCE: NOT IMPLEMENTED — no live GET /tasks or /tasks/{id}
backend transport or WS /events is connected.
LIVE TASK EXECUTION/CANCELLATION ACCEPTANCE: NOT IMPLEMENTED — no execution,
cancellation, transition, recovery, verification or permission-action controls.
MANUAL NATIVE TASK GRAPH ACCEPTANCE: PENDING — no verified end-to-end WebView2
mouse/keyboard interaction; SSR/hook/CSS/native build evidence is not GUI acceptance.

Phase 8 INCOMPLETE; PHX-084..087 TODO/not started.

## ADR-071 — Permission evidence presentation and request-specific human intent

Date: 2026-10-09
Status: Accepted

Decision: PHX-084 owns only the existing Permissions workspace. A caller-owned
optional readonly PermissionPort supplies status, requests and an optional
submitDecision(requestId, approved|denied): Promise<void> intent callback.
Default App remains disconnected. Whole-snapshot admission checks exact PHX-041
request/evaluation fields, bounded primitives, own descriptors, unique IDs,
request/actor/tool correlation, real UTC timestamps and all ten structural evidence
combinations before status-based suppression. These are output-shape checks;
PermissionEngine alone remains the P0-P5 authorization authority.

Only explicit native clicks on valid connected approval_required P1-P4 evidence
can deliver intent; P0/P5 and all other statuses have no decision controls. P5 is
unconditionally blocked. One global pending guard survives navigation/source loss.
Only native Promise resolving undefined acknowledges delivery, without changing
supplied evaluation or proving authorization/audit/execution. That ID stays blocked
until valid evidence changes away from approval_required or the request disappears.
Failure is generic and permits deliberate retry after settlement; no automatic
retry, timeout, polling or cancellation. No decision ID/time, grants, policies,
audit records, tool execution, API/network/native/storage capability or dependency.

Rationale: only GET /health currently exists. Labelled caller evidence and explicit
human intent satisfy the frontend boundary without inventing live transport or
trusted authentication. JSX escaping, detached frozen primitives, strict admission
and a bounded source-order search/selection keep external text inert. Reflective
Proxy traps may run; this UI is not a JavaScript sandbox or authentication boundary.

Consequences: trusted adapter/backend must authenticate/correlate human intent,
create authoritative evidence and enforce unchanged PermissionEngine, ToolRegistry
and AuditTrail. All prior workspaces, CSP/capabilities, native/backend code and locks
are preserved. Live permission transport, trusted decision authentication, grants,
audit and execution remain NOT IMPLEMENTED; native WebView2 acceptance is PENDING.
Phase 8 remains INCOMPLETE; PHX-085 through PHX-087 stay TODO and PHX-085 is not started.

## ADR-072 — Read-only Memory / Files presentation evidence

Status: Accepted
Date: 2026-10-09

PHX-085 uses optional caller-owned readonly MemoryPort/FilePort in the existing
React shell, with the user-confirmed exact DTOs and caps documented in
docs/21_FRONTEND.md. They are display adapters, not replacements for PHX-030/031
memory, PHX-032–035 RAG, PHX-043 filesystem or PHX-072/073 document contracts.
Whole-snapshot own-descriptor admission, bounded primitives and UTC chronology,
detached frozen records, inert JSX and generic failures precede display even for
hidden unavailable/degraded sources. Reflection validation is not a Proxy sandbox,
authentication, memory admission or proof of source truth. Missing evidence is
not inferred. Local search/selection preserves source order and navigation state.

PUDDING/ROBIN/VEGAPUNK ownership and PermissionEngine authority are unchanged.
No action callback, live transport, IPC, filesystem, memory mutation, persistence,
upload/download/save/export/delete, parsing/indexing or background work is added.
Generated artifacts may exist only in memory; metadata/hash evidence never proves
disk persistence or file existence. Future trusted adapters must enforce existing
secret policy and authoritative evidence. No dependency or native security change.
Deterministic gates passed before acceptance; manual native WebView2 acceptance
remains PENDING. Phase 8 is INCOMPLETE and PHX-086/087 remain TODO.

## ADR-073 — Read-only model/voice evidence and non-editor Settings

Date: 2026-10-10
Status: Accepted

PHX-086 uses the user-specified exact optional readonly ModelPort/VoicePort DTOs
and caps in the existing React shell; no callbacks or backend-domain replacements.
Own-data whole-snapshot admission, real bounded UTC, strict booleans/safe integers,
composite model identity, detached frozen primitives, generic failures and inert
JSX precede display, including hidden degraded/unavailable evidence. Reflection
checks are not a Proxy sandbox, authentication or proof of factual source truth.
Missing evidence is not inferred. Source-order bounded local search and separate
native Inspect evidence controls change only independent local inspection state.
Cards remain passive; inspection never chooses a runtime model or activates voice.

Provider health/capability/residency/startup readiness remain distinct; voice
evidence is channel-local. PermissionEngine alone authorizes. Settings is an
informational overview, without a port, schema, editor, values, controls or saving.
No provider/model/audio/device operation, API/network/IPC, filesystem/clipboard,
settings persistence, background work or dependency is added. Future trusted
adapters own authentication/privacy and authoritative evidence. PHX-081–085,
native/CSP/capabilities, backend, manifests/locks and historical guards remain
unchanged. Deterministic gates passed; manual native WebView2 remains PENDING.
Phase 8 INCOMPLETE; PHX-087 TODO and NOT STARTED.

## ADR-074 — Bounded read-only diagnostic evidence without telemetry collection

Status: Accepted
Date: 2026-10-12
Ticket: PHX-087

Use the explicitly user-confirmed ObservabilityPort display DTO: status plus
metrics/logs/traces, each capped at 64 unique IDs. Preserve all exact metric,
severity and trace-kind vocabularies, required/optional field allowlists,
UTF-16/finite-number/real-UTC bounds and primitive values from the ticket.
These presentation DTOs replace no backend event, permission, task or model contract.

Validate the complete source through own data descriptors before display,
including hidden unavailable/degraded data. Reject malformed snapshots whole,
with generic failure wording; detach/freeze accepted primitive snapshots. Missing
source, supplied empty evidence, unavailable/degraded and populated evidence stay
distinct. Source loss hides details and resets selected records. Independent
source-order local searches (80 UTF-16 units), counts, Clear search and named
native inspection controls change only local React state. Cards stay passive;
supplied text is escaped and inert. Existing UI state and architecture remain
preserved except necessary composition/harness plumbing.

No instrumentation, collection, computation/aggregation, live transport, API/IPC,
runtime/provider hooks, logging, persistence, filesystem/export/clipboard or new
dependency. PHOENIX alone orchestrates; PermissionEngine alone authorizes.
Supplied records prove neither execution, permission outcomes, LEVI completion
nor recovery success. Shape validation guarantees neither secret-free strings
nor authenticated/current/complete/factual evidence. Proxy traps may execute;
trusted future producers must enforce redaction and authentication.

All mandatory deterministic gates passed before acceptance. External evidence
retains failed/incomplete validation attempts and the explicitly approved
containment/baseline repairs; no test/security weakening occurred. Manual native
WebView2 acceptance remains PENDING and live integration remains absent.
PHX-087 is DONE; Phase 8 stays INCOMPLETE. Its phase-status decision is BLOCKED
pending explicit phase-level native/backend acceptance policy, as required by
the user's PHX-087 execution section 9. No phase-level completion is inferred.

## ADR-075 — Native host wraps WebView2 keyboard tab-out back into web content

Status: Accepted
Date: 2026-10-14
Ticket: PHX-088

Phase 8 native acceptance (B5-trap step B12) found that Tab past the last, or
Shift+Tab before the first, focusable element left keyboard focus for one
keypress on the unnamed, unoutlined WebView2 host element. Cause: WebView2
raises ICoreWebView2Controller::MoveFocusRequested at those edges and no layer
of the pinned stack (tauri 2.12.1, tauri-runtime-wry, wry 0.57.0, tao) handles
it, so the WebView2 default applies. The PHOENIX window has no other tab stops.

The Windows desktop host subscribes to MoveFocusRequested on the main window and,
for NEXT/PREVIOUS only, calls controller.MoveFocus with the same reason and marks
the event handled, so focus wraps directly to the first/last focusable element.
Programmatic requests keep the default. Registration is best effort: failure
leaves the previous default behaviour, never a crash. React focus management,
DOM order, CSP, capabilities/permissions, IPC and frontend code are unchanged.

webview2-com 0.39 becomes a direct Windows-only dependency. It was already
locked transitively through tauri/wry at 0.39.1, so no new crate enters the
build; it is required to name the WebView2 event handler types Tauri exposes.

Verified by unit tests for the wrap decision, cargo check/test, a locked offline
release build, and native keyboard replay of the B5 path against an external
build: the unfixed control reproduced the host-element stop, the fixed build
wrapped in both directions. The previously accepted Phase 8 executable predates
this change and does not contain it; Phase 8 stays INCOMPLETE and PHX-090 is not
started.

Amendment (2026-10-14): the PHX-088 delivery did not run `npm test`, whose PHX-080
byte-identity guard pins Cargo.toml, Cargo.lock and src/main.rs; it failed on main.
With owner approval those three files are now pinned to the PHX-088 commit
30c8d57 instead of 4a70867. Every other guarded file keeps its PHX-080 baseline,
and any further change to the three files still fails the guard.

## ADR-076 — Repository text is LF on every platform

Status: Accepted
Date: 2026-10-14
Ticket: PHX-008

All committed text is LF, but the repository had no .gitattributes, so a
Windows checkout with core.autocrlf=true writes CRLF. Byte-for-byte guards in
tests/workspace.test.mjs compare that file's own text with LF git blobs and
fail whenever git rewrites it with CRLF (observed on main 0e0bd57: npm test
179/181 until the file was restored to its LF blob).

Add .gitattributes with `* text=auto eol=lf`, marking the two icons binary.
Text files are checked out as LF everywhere, overriding core.autocrlf; binary
detection plus explicit binary rules keep icons byte-exact. `git add
--renormalize .` changed no committed content. No tooling, dependency, guard or
product change; contributors' git configuration is not modified.

## ADR-077 — Phase 8 native acceptance and completion

Status: Accepted
Date: 2026-10-14
Ticket: Phase 8 (phase-level decision deferred by ADR-074)

Phase 8 is COMPLETE. The accepted native executable is
7F32F7656184BA4C90E41817351EF2B61743FD38287B1DF8A246E0D33060C2B7, built with
the canonical procedure from main 81b3ae9, whose 17 build inputs are unchanged
through this decision. It supersedes the earlier accepted build 639D8BE5…, which
lacked the PHX-088 focus fix. Evidence lives outside the repository under
validation/PHX-phase8-B5-rebuild-20261014-192904 (report, checklist r5).

Final 68-criterion matrix: 67 PASS / 0 FAIL / 0 NOT VERIFIED / 1
CONDITIONAL_NOT_REACHABLE. Owner decisions recorded with the evidence:
- Diagnostic-build evidence (in-process CDP observer; identical web content)
  counts for C4 reduced motion and E3 runtime errors. C4 covers the actual
  WebView condition plus the reduce rule under emulation, not a real OS-level
  reduced-motion setting.
- The external validation fixture composition is the acceptance scope for
  malformed-snapshot exception leakage (D4) and the D2–D4 presentation rows,
  because production supplies no ObservabilityPort.
- A4 unknown-route fallback stays CONDITIONAL_NOT_REACHABLE and is ruled
  satisfied: no supported native path to an unknown view exists.

Independent read-only verification confirmed the result with caveats, all of
which were resolved before completion. Required gates pass on main ee2a399.
PHX-090 is authorized to start. Live backend integration remains out of scope.

## ADR-078 — Automated test matrix and CI

Status: Accepted
Date: 2026-10-14
Ticket: PHX-090

Required gates were a hand-run command list kept in external evidence. There
was no CI; the only PR check was CodeRabbit, which let the PHX-088 guard
regression reach main.

`scripts/test_matrix.py` (Python standard library only) runs the gates declared
in `tests/matrix.toml` in order. It records the command, exit code, duration
and test counts for each gate, and writes schema-versioned JSON plus a Markdown
summary to the gitignored `.test-matrix/`. The manifest maps every test file to
the docs/24 categories. Owner decisions (2026-10-14):

- **External manifest.** Existing test files are not edited, because preservation
  guards pin earlier test blocks.
- **Explicit gaps.** Uncovered categories are declared `[[gap]]` rows with
  reasons; gap-filling tests are follow-up tickets.
- **Manual rows.** Native WebView2 acceptance and live model or device runs are
  `[[manual]]` rows with evidence and never count as an automated PASS.
- **CI.** A GitHub Actions workflow runs the matrix on `windows-latest` for PRs to
  main and pushes to main.

The runner fails on any gate failure, missing tool, timeout, unmapped or missing
test file, undeclared gap or failed category, or if a gate modifies the working
tree. It requires a clean tree unless `--allow-dirty` is passed, and records
that.

`tests/test_test_matrix.py` validates the real manifest inside the normal pytest
run, so a new test file must be mapped. `pyproject.toml` is pinned by the
PHX-080 guard and is not edited; the runner is type-checked by its own strict
mypy gate instead.

New external dependencies are CI-only: GitHub Actions and the actions
`actions/checkout@v4`, `actions/setup-python@v5`, `astral-sh/setup-uv@v6` (uv
0.12.10), `actions/setup-node@v4` and `actions/upload-artifact@v4`. There are no
new runtime, test or package dependencies. `tauri:build` is not part of the
matrix; the native release build stays a manual, evidence-backed step.

## ADR-079 — FYP benchmark suite: runnable baseline, explicit NOT RUNNABLE rows

Status: Accepted
Date: 2026-10-15
Ticket: PHX-091

The docs/24 FYP comparison has three configurations: a single local model,
PHOENIX multi-agent orchestration, and PHOENIX with adaptive routing and the deep
tier. Only the first can run today. PhoenixOrchestrator, AizenPlanner and
LeviVerifier accept injected responders only, and Colibri is not installed.

Owner decisions:

- Build a fixed, versioned task set and run the single-model baseline now.
  `benchmarks/fyp/tasks-v1.toml` has 16 harmless cases in 7 capabilities and uses
  deterministic exact/contains scoring only, with no LLM judge.
- Report configurations (2) and (3), and the docs/24 metrics they would provide,
  as NOT_RUNNABLE with reasons until model-backed responders exist. Nothing is
  estimated.
- `scripts/fyp_benchmark.py` reuses the unchanged PHX-016 BenchmarkRunner,
  ModelLifecycleManager and OllamaProvider (ADR-029 privacy: output
  hashes/lengths only).
- The baseline uses the MASTER_SPEC tiers qwen3.5:2b/4b/9b and qwen2.5-coder:7b.
  With owner approval the Ollama store was copied to `D:\Phoenix\models`, user
  OLLAMA_MODELS points there, and the models were pulled (16.64 GB).

Finding: Qwen3.5 reasons ("thinks") by default. Ollama returns that reasoning
in a separate field, and OllamaProvider sends no `think` control. With the
harness's 64–128-token budgets every answer was empty: 0 characters and 0%
accuracy on the 2B model. A direct API check shows `think:false` answering in
about 0.3 s, `/no_think` having no effect, and a 2048-token budget completing
both the reasoning and the answer. The suite therefore runs through the real
provider with a 2048-token budget and a 180 s per-trial timeout, so it measures
current PHOENIX behaviour with thinking on.

Adding thinking control to OllamaProvider is a separate product ticket. After
it lands, the baseline is re-run with thinking off as an additional row; the
v1 task set does not change.

Amendment to ADR-079 (2026-10-15, PHX-091 follow-up): after PHX-017
(ADR-080), `scripts/fyp_benchmark.py --think {default,on,off}` selects the
Ollama thinking control, and the choice is recorded in the results. The
thinking-off row was run on 2026-10-15 with the v1 task set and the same budget
and timeout. Its evidence is in the PHX-091 ticket. The thinking-on baseline
is still the default and is still owed.

Correction (2026-10-15): that thinking-off row ran CPU-only and is invalid as
BLAZE-6 evidence.
- **Cause:** the RTX 3050 was disconnected from the bus (Device Manager Code 45),
  and Ollama reported `library=cpu`, `total_vram="0 B"`. `nvidia-smi`'s
  "insufficient permissions" error was a symptom, not the cause.
- **Guard:** Ollama falls back to CPU silently. `scripts/fyp_benchmark.py` now
  reads each tier's placement from Ollama `/api/ps` (`size_vram` vs `size`)
  before and after the run and records it in each row and the summary.
- **Policy:** it refuses CPU-only placement unless `--allow-cpu` is passed, and
  marks a row `invalid_cpu_fallback` if the GPU is lost mid-run.
- **Product code:** `phoenix/` is unchanged.
- **Next step:** both thinking modes must be re-run with the GPU enabled.

Correction (2026-10-15): "the models were pulled" to `D:\Phoenix\models` above
is inaccurate.
- **Where they went:** the Ollama desktop app ignores `OLLAMA_MODELS` and uses
  its own model-location setting, so the pulls went to the C: store. The missing
  files were later copied to D: and verified.
- **Context setting:** the app also passes its own context-length setting
  (262,144), which overrides `OLLAMA_CONTEXT_LENGTH`. At that size, Ollama
  0.40.4 (auto-updated from 0.35.1) left only 9 of 25 qwen3.5:2b layers on the
  GPU.
- **How the GPU baselines run:** with the desktop app closed, on a headless
  `ollama serve` using `OLLAMA_CONTEXT_LENGTH=16384` (MASTER_SPEC "normal
  context") and `OLLAMA_MODELS=D:\Phoenix\models`, on CUDA.
- **Placement:** 2b and 4b fully on the GPU; 9b 53% and coder-7b 73% (6 GB
  VRAM limit).
- **Recorded per run:** placement and the Ollama version.

Amendment (2026-10-15, closure): the GPU baselines ran in both thinking modes.
Their evidence is in the PHX-091 ticket.
- **Overlap rerun:** the thinking-on T1 and T2 rows came from a clean rerun,
  because the first run overlapped an unrelated CPU-heavy test-matrix run. The
  rerun reproduced accuracy and failures exactly.
- **Metric limitation, accepted rather than fixed:** with thinking on, the
  PHX-016 harness's `tokens_per_second` counts hidden thinking tokens but
  divides only by the visible-text interval, so it is inflated and not
  comparable. TTFT means time to the first visible answer token, which includes
  reasoning time.
- **Reporting:** FYP reports must use output tokens ÷ total latency for
  thinking-on throughput.
- **Harness:** it stays frozen. Changing the metric belongs to a harness ticket
  if the owner wants it.
- **Closure:** PHX-091 closes on configuration (1). Configurations (2) and (3)
  move to the PHX-091 continuation after PHX-029 and PHX-046. The owner chose,
  on 2026-10-15, to install Colibri for configuration (3).

Amendment (2026-10-18, PHX-091 continuation): configuration (2) is runnable.
- **Runner:** `scripts/fyp_benchmark.py --config phoenix-multi-agent`
  dispatches to `scripts/fyp_multi_agent.py`. It runs each v1 case through
  `PhoenixRuntime.run`, COMPLEX by default, with a default and a `think=False`
  `OllamaProvider` and the runtime tier table. It uses its own mission loop,
  because BenchmarkRunner would deadlock on the single lifecycle lease.
- **Path:** `--path triage|complex|direct`. After PHX-105 merged, `triage`
  became the default: it passes no path, so PHOENIX triages each request, and
  each trial records the decided path and triage outcome. `--path complex`
  stays as the full-pipeline stress test.
- **Scoring:** unchanged; the same deterministic rules on the final answer.
  Missions without an answer count as not passed.
- **Trials:** 0 warm-up and 1 measured run by default, because a mission takes
  about 1–2 minutes. Both are recorded in the results.
- **Reported:** outcome counts, mission latency, generations, tokens, LEVI
  verdicts, failure codes, per-category accuracy and the prompt set version.
- **NOT_RUNNABLE:** verification catch rate, until fault-injected cases exist.
  Routing accuracy belongs to PHX-105. Configuration (3) also stays
  NOT_RUNNABLE.
- **Comparison:** `--compare` sets configuration (1) tiers beside
  configuration (2). It states that (2) mixes models and thinking modes by role
  and that its latency is whole-mission time.
- **Privacy:** unchanged (ADR-029): hashes and lengths only.
- **Product code:** `phoenix/` is unchanged.
- **Live evidence (2026-10-18):** BLAZE-6, RTX 3050 on CUDA, Ollama 0.40.4
  desktop app, 16K context, AC power; 16 missions per run. Details are in the
  PHX-091 ticket.
  - **Forced COMPLEX** (prompts v3, `validation/PHX-091-config2-20261018-030132`):
    43% accuracy, 38% success, 122 s median latency. Failures were mostly
    structured output (4 schema, 3 truncated, 1 not_json) plus 2
    `verification_failed`. All 6 completed answers were correct.
  - **Triage** (prompts v4, `validation/PHX-091-config2-triage-20261018-033220`):
    11 DIRECT and 5 COMPLEX; 64% accuracy, 69% success, 1.1 s median latency.
  - **Configuration (1) best, for comparison:** 4B at 93% / 0.3 s (thinking
    off) and 100% / 7.2 s (thinking on).
- **Interpretation:**
  - On these short tasks with 6 GB of VRAM, multi-agent is slower and less
    accurate than the best single small model.
  - Its value is that no wrong answer is reported as verified, plus
    permissioned tool use.
  - The next levers are constrained JSON decoding and MAHORAGA retries
    (PHX-109).

## ADR-080 — Explicit Ollama thinking control at provider construction

Status: Accepted
Date: 2026-10-15
Ticket: PHX-017

This amends ADR-024, which kept `think` out of the Ollama adapter. The
MASTER_SPEC Qwen3.5 tiers think by default. Ollama returns that reasoning in
`message.thinking`, so with bounded output budgets every answer came back empty
(PHX-091, ADR-079).

Owner decisions (2026-10-15):

- **Construction option.** `OllamaProvider(think=None | False | True)`. The
  PHX-010 `GenerationRequest` contract is unchanged; Colibri, the PHX-016 runner
  and other callers are untouched. A caller needing both modes holds two
  providers. A neutral per-request field waits for a ticket that needs it.
- **Default `None`.** No `think` field is sent, so existing behaviour and
  payloads are unchanged. `False`/`True` are sent as top-level `think`. Only a
  strict bool or `None` is accepted.
- **Reasoning text is never surfaced.** `message.thinking` stays discarded in
  generate and stream; it never reaches responses, chunks, errors, logs, memory
  or benchmark reports. No reasoning token count is inferred.
- **Non-thinking models.** `think=False` is accepted by Ollama 0.35.1 and sent
  as-is. `think=True` fails as `ModelProviderError` from Ollama's HTTP 400, with
  no retry or fallback. Capability metadata is unchanged.
- **Empty content stays honest.** A completed record with empty content returns
  `text=''` with Ollama's finish reason (for example `length`), not an error.

No new dependencies. Only `phoenix/models/ollama.py` changes in production code.

Amendment (2026-10-15, owner decision "Re-pin to the PHX-017 commit"): two
historical guards compared `phoenix/models/ollama.py` with `origin/main` and
would reject this change. In
`tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary`
and `tests/test_screenshot_vision_loop.py::test_no_action_persistence_or_dependency_change`,
that one file is now pinned to the PHX-017 implementation commit
`b7d96518d58ee7466d441cbe3b17c4eaad0866cf`, so any further change still fails.
Every other protected file stays pinned to `origin/main`. This follows the PR #61
re-pin (commit 52ec305). It requires the PR to land with a merge commit, so
that SHA stays reachable from main.

## ADR-081 — Model-backed orchestration runtime over frozen contracts

Status: Accepted
Date: 2026-10-15
Ticket: PHX-029

Before this decision, PhoenixOrchestrator, AizenPlanner and LeviVerifier accepted
injected responders only. Nothing in `phoenix/agents` called `ModelProvider`, so the
complex path stopped at `PLANNING_REQUIRED`. The owner answered all 13 PHX-029
questions with the recommended option:

- **Scope.** Three tickets.
  - PHX-029: text-only responders and runtime.
  - PHX-046: P1 read-only filesystem steps and the `end_to_end` test that clears
    the PHX-090 gap.
  - PHX-091: a configuration (2) runner.
- **Location.** New modules only: `phoenix/agents/prompts.py`, `responders.py`
  and `runtime.py`.
  - Every existing `phoenix/` file stays byte-identical, so the protected-source
    guards are untouched.
  - `PhoenixRuntime` is PHOENIX's runtime (`orchestrator_id="phoenix"`), not a
    second orchestrator (INV-001). It uses PhoenixOrchestrator for DIRECT and for
    the COMPLEX hand-over.
- **Path.** The caller supplies DIRECT or COMPLEX. Triage is deferred.
- **Routing.**
  - A PHOENIX assignment generation picks each step's domain from an allowlist
    (v1: intelligence, scenario_analysis, build_engineering). AIZEN keeps no
    routing authority.
  - Specialists go through AgentModelRouter.
  - Control roles (phoenix, strategist, verifier), which the router deliberately
    excludes, use `ControlRoleModelSelector`. It applies the same rules: HEALTHY
    provider, capability match, first eligible in caller order.
  - The tier table is provisional data (docs/05 freeze gate):
    - phoenix → T1 qwen3.5:4b;
    - strategist and verifier → T2 qwen3.5:9b;
    - build_engineer → T2-code qwen2.5-coder:7b;
    - others → T1;
    - T0 qwen3.5:2b is always the last fallback.
  - No resource monitor exists yet, so registered candidates count as resource
    eligible.
- **Structured output.** Parsed from text. The response must be exactly one JSON
  object, with at most one code fence, and strict `extra="forbid"` wire models
  validate it. The following are rejected with closed codes:
  - truncated output (`finish_reason="length"`) or empty output;
  - oversized output;
  - output that is not valid UTF-8 (`invalid_text`, for example a lone
    surrogate).

  The raise happens outside any handler, so no exception carries the raw text.
  Responders, not models, assign every ID. No provider change.
- **Thinking.** `think:false` applies to the structured roles: PHOENIX direct and
  assignment, AIZEN planning and LEVI verification.
  - Following ADR-080, which allows two providers, `PhoenixRuntime` takes optional
    `structured_providers` (for example `OllamaProvider(think=False)`) that share
    IDs with the default providers.
  - Specialist and final-answer steps keep the provider default.
  - Health is probed on the default instance.
- **Prompts.** Versioned constants (`phoenix-prompts-v1`). The coding agent
  drafted them; the owner approved the exact `phoenix-prompts-v1` text on
  2026-10-15. Any change after release bumps the version. The review fixes
  below changed the prompts before release or approval, so v1 was kept rather
  than bumped.
- **Plan objectives are guarded (owner decision "Guard them", 2026-10-15).**
  AIZEN objectives are model output. They reach the assignment, specialist and
  verifier prompts only through `PromptInjectionGuard` as `MODEL_OUTPUT` items.
  Fixed runtime text tells the model to complete, or verify against, the planned
  objective shown in the data block. Only runtime constants and the user's own
  request appear as raw prompt text.
- **Verification.**
  - Every step, including a final `user_interaction` answer step, is verified with
    `verification_required=True`. Self-handoff is forbidden, so the final answer
    cannot be a PHOENIX step.
  - LEVI's responder emits deterministic checks first, so the all-checks verdict
    cannot be overridden (INV-008). When any of them fails, the model is not
    consulted. The checks are:
    - the output is within the size limit;
    - for intermediate steps only, the output is plain text, not JSON.
  - The final answer is exempt from the plain-text check, because it must follow
    whatever format the request asks for, including JSON (the PHX-091 structured
    cases).
  - LEVI prefers a model other than the producer's.
- **DIRECT path.** It has no LEVI verification and no task graph. It is bounded
  only by the mission deadline. Simple answers are PHOENIX direct output (as
  PHX-021 already allowed), not verified results.
- **Task state.** The canonical table has no VERIFYING → RUNNING edge, and states
  are task-level. Intermediate step verification therefore runs while RUNNING, and
  VERIFYING is the final-answer verification phase:
  CREATED → PLANNING → READY → RUNNING → VERIFYING → COMPLETED.
- **Failures.** Explicit, with no hidden retries. Provider, responder-output,
  prompt-boundary, routing, verification and timeout failures each end as FAILED
  with a closed code; unknown exceptions propagate. `cancel()` yields CANCELLED,
  and outside cancellation propagates. Settling a failure or cancellation onto
  the task state machine never skips an illegal edge silently: it raises
  `TaskStateTransitionError` as a defect. MAHORAGA and KAIDO stay deferred. The codes
  are shaped to become their trigger evidence later.
- **Events.** None; the 32-type vocabulary is unchanged. `MissionTrace` records:
  - state history;
  - provider health;
  - per-generation role, model, tokens, duration, and output hash and length;
  - LEVI verdicts.

  It never records prompts or outputs.
- **Leases.** Every generation holds the global heavy lease (INV-023), and
  cancellation is forwarded to the provider. The PHX-016 BenchmarkRunner cannot
  wrap a mission, because it would deadlock on the single lease, so PHX-091
  configuration (2) needs its own loop.

No new dependencies. Tests are offline: a scripted provider, plus a real
OllamaProvider over `httpx.MockTransport` for the `think` wire check.

## ADR-082 — Permissioned read-only filesystem steps in the PHOENIX mission runtime

Status: Accepted (prompt text `phoenix-prompts-v2` approved by the owner 2026-10-15)
Date: 2026-10-15
Ticket: PHX-046

This extends ADR-081. It supersedes ADR-081's "new modules only" and "no tools"
limits for `phoenix/agents/prompts.py` and `phoenix/agents/runtime.py` only.
Owner decisions (2026-10-15):

- **Structure.** `prompts.py` and `runtime.py` change; the tool logic lives in
  the new `phoenix/agents/tool_steps.py`; `responders.py` stays byte-identical.
  Every other existing `phoenix/` file is unchanged.
- **Re-pin.** `tests/test_prompt_injection_defenses.py` freezes every
  `phoenix/` path in `origin/main`. Following the PHX-017 precedent (ADR-080),
  `phoenix/agents/prompts.py` and `phoenix/agents/runtime.py` leave the
  `origin/main` baseline and are pinned to the PHX-046 implementation commit
  `95c5afad908933ea92ab61c6c2e5b86fdd04e0e0` (`PHX_046_COMMIT` /
  `PHX_046_PATHS`), so any further change still fails. A pin
  cannot name its own commit, so the work lands as two commits: the
  implementation, then the pin. The PR must merge with a merge commit so the
  pinned SHA stays reachable from main.
- **Prompts.** `phoenix-prompts-v2`, scoped to tool steps:
  - the planner may include steps that read one workspace file, list one
    directory or inspect one file's metadata, and still excludes writes,
    deletes, other tools and approvals;
  - `DOMAIN_DESCRIPTIONS["documents"]` is added;
  - new roles `document_specialist` (proposes exactly one
    `{"tool_request": {"operation": ..., "relative_path": ...}}`) and
    `document_answer` (answers the step from the guarded `tool_output` item);
  - document-step expected output and completion rule;
  - the final-answer prompt may use workspace evidence reported in verified step
    outputs and still forbids claiming tool runs, changes or approvals itself.

  The verifier, direct, assignment and text specialist prompts are unchanged.
  Every system prompt keeps a stable `PHOENIX ROLE:` first line. The planner
  prompt is shared, so a runtime without a filesystem tool may still see a
  file-reading plan step; it can only be assigned to a text domain there, and
  the text specialist prompt forbids claiming file access.
- **Allowlist.** `filesystem.read_text`, `filesystem.list_directory` and
  `filesystem.stat` only (all P1). `documents` (`document_analyst`, provisional
  T1 then T0 per ADR-081) joins the assignment allowlist only when
  `PhoenixRuntime` is constructed with a `FilesystemTool`, whose explicit root
  is the workspace. Document steps route with `PermissionRisk.P1` and carry the
  three tool IDs as the handoff `permission_scope`. FilesystemTool requires all
  seven filesystem definitions, so the runtime itself enforces the three-op
  allowlist and also rejects any `describe()` result whose risk is not P1.
- **One tool call per step.** The document specialist proposes once; PHOENIX
  builds the `FilesystemCall` (reads bounded to `max_output_chars` bytes,
  listings to 50 entries) and the `PermissionRequest` (actor
  `document_analyst`, the fixed `DOCUMENT_TOOL_REASON`, a fresh request ID, the
  task ID and the caller's session ID). A second request, extra fields or a
  request in the answer turn is never executed. `FilesystemTool.execute` runs
  via `asyncio.to_thread`.
- **Grants.** `PhoenixRuntime.run(request, path, *, grants=(), session_id=None)`.
  Only caller grants are used, never model text (INV-003). Because ONCE grants
  need the runtime's request ID and TASK grants its task ID, only SESSION
  (matching the caller's `session_id`) and PERSISTENT P1 grants for
  `document_analyst` with the exact `DOCUMENT_TOOL_REASON` are accepted; any
  other grant is a `PhoenixRuntimeError` before the mission starts.
- **Waiting is non-terminal.** `APPROVAL_REQUIRED` moves the task
  `RUNNING → WAITING_PERMISSION` and ends the mission as the new
  `MissionKind.WAITING_PERMISSION`, carrying `pending_permission` (the
  unattempted `PermissionRequest`, including the absolute resource path). The
  task stays in WAITING_PERMISSION; there is no approval loop, and a rerun with a
  grant is a new mission.
- **Failures.** New `MissionFailureCode` values: `tool_rejected` (an unlisted,
  mutating or unknown operation, an invalid or escaping path, or a non-P1
  intent; nothing is executed) and `tool_failed` (a FilesystemTool FAILURE, or
  output over the configured limit). An invalid `tool_request` shape is the
  existing `responder_output` code. No retries.
- **Untrusted output.** Tool output reaches models only through
  `PromptInjectionGuard` as a `TOOL_OUTPUT` item (INV-017); plan objectives and
  earlier outputs stay `MODEL_OUTPUT` items.
- **LEVI.** For document steps a wrapper around `ModelVerificationResponder`
  emits `deterministic.tool_evidence` first: the result's call ID and the
  decision's request ID correlate with what PHOENIX issued, the decision is
  ALLOWED by a matching caller grant, the tool is on the allowlist and P1, and
  the result is `attempted=True`, `effect_applied=False`, status SUCCESS. If it
  fails, the model verifier is not consulted and the mission fails
  `verification_failed` (INV-008). A truncated listing (PARTIAL_SUCCESS) is
  never answered: the runtime ends the mission `tool_failed` before the answer
  turn, and this check would reject it anyway.
- **Duplicate grants.** Caller grants with a repeated ID or effective scope are
  rejected before the mission starts, mirroring PermissionEngine's own rule.
- **Audit.** P1 has no mandatory audit (INV-007 covers P3/P4), so the runtime
  writes no rows; the end-to-end test asserts zero rows on a migrated database.
- **Trace.** `MissionTrace.tool_calls` holds `ToolCallRecord` entries (step,
  call and request IDs, tool, operation, decision status, tool status,
  attempted, effect, output hash and length). Traces never hold file content.
- **Neutrality.** Only `runtime.py` and `tool_steps.py` may import
  `phoenix.tools.filesystem` and `phoenix.tools.registry`; every other ban
  (Ollama, Colibri, httpx, logging, persistence, API) still holds.
- **Live smoke.** `scripts/phoenix_mission_smoke.py` runs one harmless mission
  against local Ollama on the owner's go-ahead. It records only hashes, lengths,
  status and timings, is strictly type-checked by the `mypy-scripts` gate, and
  is not a CI gate.

Limitations: the model verifier judges the document answer without seeing the
file; correlation and authority are checked deterministically, faithfulness to
the file is not. A cancelled tool step stops waiting, but the bounded read-only
thread may still finish in the background. No new dependencies.

## ADR-083 — Grounded LEVI verification for document steps and final answers

Status: Accepted (`phoenix-prompts-v3`, including the round-5 sentence, approved by the owner 2026-10-16; live acceptance 3/5 accepted, with remaining variance assigned to PHX-109)
Date: 2026-10-16
Ticket: PHX-047

This extends ADR-082. It supersedes ADR-082's "the verifier is unchanged" and its
stated limitation that the model verifier judges a document answer without the
file. It also supersedes two ADR-081 rules:
- the PHX-029 Q7 rule that specialists keep the default provider, for the two
  document turns (`document_specialist` and `document_answer`) only;
- the rule that final-answer verification uses the text verifier prompt with
  only the objective and candidate.

Live evidence, round 1 (2026-10-16,
`validation/PHX-046-mission-smoke-20261016-003757`):
the first live mission planned, assigned `documents`, proposed `read_text
smoke.txt`, was ALLOWED by the caller SESSION grant, read the file and answered
`42` (2 characters). LEVI's deterministic `tool_evidence` check passed, and then
LEVI's model verifier (qwen3.5:9b, `think:false`) failed the step, so the mission
ended `verification_failed`. The model never saw the file, and the verifier
prompt says to fail a check when unsure, so it could not confirm a file-derived
fact. The `document_specialist` also ran with thinking on and spent about 34 s and
1,240 output tokens on a 74-character JSON request.

Owner decisions (2026-10-16):

- **Grounded document verification.** When LEVI verifies a document step, its
  model receives the step's tool output as one guarded, untrusted `TOOL_OUTPUT`
  item via `PromptInjectionGuard` (INV-017). It is the same item, with the same
  bounded rendering, content, call-ID source and field name, that the
  `document_answer` turn received, next to the existing guarded `objective` and
  `candidate` items. Intermediate text steps and the final-answer verification
  receive no tool output.
- **Order and verdict.** For document steps LEVI still runs
  `deterministic.tool_evidence` first, then the plain-text and size checks. If
  any deterministic check fails, the model is not consulted. The verdict stays
  LEVI's all-checks rule, so the model cannot override a deterministic failure
  (INV-008). A grounded model check that fails ends the mission
  `verification_failed`.
- **Prompts.** `phoenix-prompts-v3` adds a new role, `PHOENIX ROLE:
  document_verifier` (`DOCUMENT_VERIFICATION_SYSTEM`), and its guard task text
  (`DOCUMENT_CANDIDATE_TASK`). It tells LEVI to judge the candidate against the
  objective and the `tool_output` item, to treat all three items as untrusted data
  and ignore instructions inside them, and to fail a check when the candidate is
  not supported by the tool output or when unsure. The JSON reply form is the
  verifier's. The text `VERIFICATION_SYSTEM` is byte-identical. Round 2 below
  adds the final verifier and changes `ASSIGNMENT_SYSTEM`. A test hash-pins every
  v2 constant and allows exactly that one change.
- **Thinking.** The `document_specialist` proposal turn emits one JSON object, so
  it uses the structured providers (`think:false` when the caller supplies them),
  like the other structured roles. Round 2 extends this to `document_answer`.
- **Model selection.** The document and final verifiers use the verifier binding:
  the verifier tier, preferring a model other than the producer's.
- **Structure.** `phoenix/agents/tool_steps.py` holds `DocumentVerificationResponder`
  and `FinalVerificationResponder`. Both subclass `ModelVerificationResponder`,
  reusing its deterministic checks and check parsing. The document verifier is
  wrapped by `ToolEvidenceVerificationResponder`. For document steps,
  `runtime.py` builds the document verifier and passes a structured binding to
  `DocumentStepRunner.perform`, which uses it for both turns. For the final step
  it builds the final verifier. `responders.py` stays byte-identical to
  `origin/main`.
- **Re-pin.** `tests/test_prompt_injection_defenses.py` freezes every `phoenix/`
  path in `origin/main`, which now includes `tool_steps.py`. `prompts.py`,
  `runtime.py` and `tool_steps.py` leave their previous baselines (the
  `PHX_046_COMMIT` pin for the first two, `origin/main` for the third) and are
  pinned to the latest PHX-047 implementation commit (`PHX_047_COMMIT` /
  `PHX_047_PATHS`), so each path keeps exactly one baseline and any further
  change still fails. Round 1 pinned
  `f600f122b2715260384db47b63bd7c07172919b1`; round 2 re-pinned to
  `73a4f007fab4cf65d4e504e5503fbbec588bdf80`; round 3 re-pinned to
  `e47834a3e82d8f10dfde8479c89cea20aa5d369a`; round 4 re-pinned to
  `327f4e4abc746b5d0b6d4d9faf8066b3360689a6`; round 5 re-pins to
  `47aa471712c6c5ea3452cf5a45c3ed390470e185`. As in ADR-082, each
  implementation commit is followed by its pin commit, and the PR must merge
  with a merge commit so the pinned SHA stays reachable from main.
- **Privacy.** Tool output still never enters `MissionTrace`, errors or logs:
  traces keep IDs, statuses, hashes and lengths only. Model check summaries,
  which may quote the file or step outputs, are not traced.

Live evidence, round 2 (2026-10-16, 10 live missions on `ea81364`): two missions
completed with the correct `42`, and the grounded `document_verifier` passed all 4
times it was reached. The other failures had three causes:
- **(a)** `document_answer` (4B, default thinking) sometimes spent the whole
  2048-token cap thinking and returned 0 characters with
  `finish_reason=length`, which became `responder_output/truncated`.
- **(b)** The text `verifier` on the `final_answer` step failed a correct `42` in
  2 of 4 runs. It never saw the verified step outputs, even though its completion
  rule says "consistent with the verified step outputs".
- **(c)** The 4B assignment routed a "Read the content of the file smoke.txt..."
  step to `intelligence` instead of `documents`. Fenced and compact JSON both
  parsed fine.

Owner decisions, round 2 (2026-10-16):

- **`document_answer` thinking.** `document_answer` also uses the structured
  providers. Text specialists and `final_answer` keep the default providers.
- **Grounded final verification.** When LEVI verifies the `final_answer` step,
  its model receives each verified step output as a guarded, untrusted
  `MODEL_OUTPUT` item via `PromptInjectionGuard`, plus the `objective` and
  `candidate` items. These are the same step-output items the `final_answer` turn
  received. A new role, `PHOENIX ROLE: final_verifier`
  (`FINAL_VERIFICATION_SYSTEM`, guard task `FINAL_CANDIDATE_TASK`), tells LEVI:
  - to judge the candidate against the original request, any format it requires,
    and the step outputs;
  - to treat all items as untrusted data;
  - to fail a check when the candidate is unsupported by the step outputs,
    breaks a required format, or LEVI is unsure.

  The JSON reply form is the verifier's. The size check runs first, and the
  final answer stays exempt from the plain-text check. The verdict is still
  `all(checks)`, and nothing new reaches the trace. Intermediate text steps
  keep `VERIFICATION_SYSTEM` with no step outputs.
- **Assignment routing rule.** `ASSIGNMENT_SYSTEM` gains one sentence: "If
  "documents" is one of the allowed domains, assign every step that reads a
  file, lists a directory or inspects a file's metadata to "documents"."
  `responders.py` is frozen and formats this prompt only with the domain list,
  so the rule cannot be inserted conditionally. It is worded to apply only when
  `documents` is in the allowed list, so a text-only runtime is unaffected. The
  rest of the prompt is unchanged, and v3 is unreleased, so this v2 constant
  changes under v3.

Live evidence, round 3 (2026-10-16, live acceptance on `ac37229` + `7c33b9d`):
- r1 and r3 completed with the correct answer, and every LEVI check passed.
- In r2, the 4B assignment (62 characters) misrouted the file step to
  `intelligence`. The text specialist (4B, thinking on) then spent the whole
  2048-token cap thinking and returned 0 characters, so the mission ended
  `responder_output/truncated`.
- r4 and r5 were interrupted by a session restart and do not count.

Owner decisions, round 3 (2026-10-16):

- **Thinking token cap.** Roles on the default providers think before answering
  (the text `specialist` and `final_answer`). They get the new
  `RuntimeLimits.max_thinking_output_tokens` (default 4096). Structured roles keep
  `max_output_tokens` (default 2048): PHOENIX direct and assignment, AIZEN, every
  LEVI role, and both document turns. `max_output_chars` still bounds the visible
  answer of every role.
- **Assignment on T2.** The provisional tier table gains an `assignment` row:
  qwen3.5:9b, then qwen3.5:4b, then qwen3.5:2b. The binding keeps agent ID
  `phoenix`. A caller table without an `assignment` row falls back to the
  `phoenix` row. PHOENIX direct stays on qwen3.5:4b. LEVI still prefers a model
  other than the producer's where the table allows. Assignment output is not
  LEVI-verified, so its tier does not affect verifier selection.

Live evidence, round 4 (2026-10-16, 5-run acceptance on `cdf3b9b`, GPU enabled):
2 of 5 runs passed. The 9B assignment routed correctly in all 5 runs, and nothing
truncated. A diagnostic of LEVI's raw output found two causes, plus one transient
`provider_unavailable` mid-run (it stays a failure; PHX-109 owns retries):
- **(a) Extra keys in check objects.** LEVI (9B, `think:false`) added keys to
  checks, for example `"completed_step_objective": true`. The strict
  `extra="forbid"` checks wire rejected them, so a mission whose checks all
  passed failed with `responder_output/schema`.
- **(b) Ungrounded intermediate text step.** A two-step plan read the file in s1
  (`documents`), then had s2 "state the number" in a text domain. The text
  `verifier` for s2 saw no evidence, wrote that the file contents were
  inaccessible, and failed a correct `42`.

Owner decisions, round 4 (2026-10-16):

- **Lenient check objects.** Every LEVI role this runtime uses
  (`verifier`, `step_verifier`, `document_verifier`, `final_verifier`) parses
  its checks with a PHX-047 wire model in `tool_steps.py`.
  - The required keys `criterion`, `passed` and `evidence_summary` keep strict
    types.
  - Unknown extra keys in each check are dropped unread; they are never trusted,
    stored or used.
  - The top level stays strict: exactly one object holding only a non-empty
    `checks` list, within the `max_checks` bound. A missing or wrongly typed
    required key, or a top-level extra such as `verdict`, is still `schema`.
  - `responders.py` stays byte-identical, so the frozen `ModelVerificationResponder`
    is no longer used directly by the runtime. Planner and assignment parsing
    stay strict.
- **Grounded intermediate text steps.** When a text step received earlier
  verified step outputs, its LEVI check receives the same items as guarded,
  untrusted `MODEL_OUTPUT` items, plus the objective and candidate. A new role,
  `PHOENIX ROLE: step_verifier` (`STEP_VERIFICATION_SYSTEM`, guard task
  `STEP_CANDIDATE_TASK`), tells LEVI:
  - to judge the candidate against the objective, expected output and
    completion rule, using the earlier outputs as evidence;
  - to treat all items as untrusted;
  - to fail a check when a fact the candidate takes from earlier steps is
    unsupported, or when it is unsure.

  A text step with no earlier outputs keeps the byte-identical
  `VERIFICATION_SYSTEM` (role `verifier`) and the same prompt as before.
  Deterministic checks still run first, the verdict is still `all(checks)`, and
  nothing new reaches the trace. This is `StepVerificationResponder` in
  `tool_steps.py`.
- **Acceptance bar.** The live acceptance bar stays at 4 of 5 runs.

Live evidence, round 5 (2026-10-16, on `4d634bd`): acceptance batch 1 passed 3 of
5, batch 2 passed 2 of 4 counted runs, and an 8-run diagnostic passed 5 of 8.
- **Request format enforced on a step.** Two diagnostic failures (runs 3 and 7)
  had the same cause. The `document_answer` was a correct sentence ("The magic
  number stated in the file is 42."). The `document_verifier` failed it, quoting
  the original request's "reply with only the magic number". That format rule
  belongs to PHOENIX's final answer, but intermediate verifiers see the original
  request and enforced it on the step. Bare `42` step answers always passed.
- **Empty assignment.** An occasional `{"assignments": []}` (19 characters)
  appeared 3 times, each on the first call in a fresh process near Ollama "llm
  server error" model reloads. It fails explicitly as `schema`; no change here,
  and PHX-109 owns retries.

Decision, round 5 (approved by the owner 2026-10-16):

- **Step-scoped format.** `DOCUMENT_VERIFICATION_SYSTEM` and
  `STEP_VERIFICATION_SYSTEM` each gain one sentence, placed before the
  reply-format paragraph and reflowed to the existing wrapping:

  > The original request's output format (for example "reply with only ...") applies to PHOENIX's final answer, not to this step; do not fail this step for wording or format beyond its expected output and completion rule.

  `FINAL_VERIFICATION_SYSTEM`, the text `VERIFICATION_SYSTEM` and every other
  constant are unchanged, so the final verifier still enforces the user's
  format. Tests pin the sentence in both prompts, its absence from the final and
  text verifiers, and the hashes of the unchanged v3 additions.

Limitations:
- LEVI's model now judges file-derived facts and step-derived final answers, so a
  weak verifier can still wrongly fail or pass an answer. Correlation and
  authority stay deterministic.
- The verifier prompts grow by the tool output (at most `max_output_chars`) or by
  the step outputs, which are already bounded for the final-answer turn.
- The routing rule is advisory to the model: a misrouted file step still runs as
  a text step, which cannot read files. T2 assignment and the larger thinking
  cap make that failure less likely and recoverable, not impossible.
- A larger thinking cap lengthens worst-case step time; step and mission
  deadlines still bound it.

No new dependencies.

## ADR-084 — Backend composition root and loopback entrypoint

Status: Accepted (PHX-100; the owner confirmed recommendations R1–R9 on 2026-10-16)
Date: 2026-10-16
Ticket: PHX-100

ADR-083 is reserved by the unmerged PHX-047 branch.

- **New modules only.** `phoenix/api/server.py`, `phoenix/api/launcher.py` and
  `phoenix/api/__main__.py` compose the existing services, with no edit to any
  existing `phoenix/`, `alembic/` or `apps/` file. `create_app()` stays
  subsystem-free. The two `git diff origin/main` guards
  (`tests/test_screenshot_vision_loop.py`, `tests/test_windows_ui_automation.py`)
  exclude exactly these three new paths, each by name.
- **Composition root.** `build_server_app(settings, *, config, provider_factory)`
  returns `create_app()` plus a lifespan and `GET /ready`. The lifespan:
  - migrates SQLite to the Alembic head (R1) under an OS-level exclusive lock
    file in the data directory (`phoenix.migrate.lock`; `msvcrt.locking` on
    Windows, `fcntl.flock` elsewhere). SQLite DDL is not transactional, so
    concurrent upgrades could otherwise interleave and leave a half-applied
    schema. The wait is bounded (`migration_lock_timeout_seconds`, default
    30 s) and a timeout fails startup explicitly with `MigrationLockTimeout`
    (INV-021);
  - builds the engine and session factory, the default agent registry, the tier
    table, `ModelCapabilityRegistry` and `ModelLifecycleManager` (capacity 1,
    INV-023);
  - builds the default `OllamaProvider` and a `think=False` structured twin
    (ADR-080);
  - builds `EventBus`, a `ToolRegistry` of the filesystem definitions (R7),
    `PermissionPolicy()`, `AuditTrail` and `PhoenixRuntime`.

  Each is built once and held in the frozen `BackendServices` container on
  `app.state.phoenix`. Tier models are registered once at startup, for healthy
  providers only, and missing models are skipped (R6). No FilesystemTool
  workspace is configured (PHX-108). On exit, the lifespan cancels active
  missions, closes the providers and disposes of the engine. A failed startup
  closes whatever it had opened. The lock serializes migrations only: a second
  backend instance on the same data directory is still possible, and PHX-106
  owns single-instance enforcement.
- **Surface.** `/docs`, `/redoc`, `/openapi.json` and the OAuth2 redirect are
  removed from the server app (`create_app()` itself is frozen and unchanged).
- **Readiness.** `GET /ready` returns `ready`, `degraded` or `unavailable`,
  with:
  - per-provider IDs and statuses;
  - the registered (provider, model) pairs;
  - `database.at_head`.

  Provider probes and the database check are bounded (default 2 s). At most
  one database check is in flight: a timed-out `to_thread` worker keeps
  running, so later calls share it instead of filling the executor. Any
  provider error, mismatch or timeout reports that provider as `unavailable`,
  and the backend as `degraded` (HTTP 200), not a crash (INV-021, INV-022).
  `unavailable` (HTTP 503) means the services are not built or the database is
  not at head. Besides statuses and IDs, only the PHOENIX version is
  returned; provider URLs, provider versions, health details and paths are
  never echoed.
- **Entrypoint.** `python -m phoenix.api [--host] [--port] [--data-dir]`:
  - Only `127.0.0.1` or `::1` is accepted; anything else exits with code 2
    before binding.
  - `--port` defaults to 0, an OS-assigned port (R2). 8000 is Colibri's port,
    and `AppSettings.api_host` and `api_port` are not read.
  - The launcher pre-binds the socket, exclusively on Windows (R3), and runs
    uvicorn programmatically with lifespan `on`, proxy headers off, the access
    log off and logs on stderr (R9).
  - Once startup has finished, it writes exactly one stdout line:
    `{"event":"phoenix.backend.ready","host":...,"port":N,"pid":P,"version":...}`.
  - SIGINT, SIGTERM and CTRL_BREAK shut down through uvicorn. Active missions
    are cancelled before connections drain (R8; INV-019), and the drain is
    bounded to 10 s. The drain runs even if cancellation raises.
- **Config.** `ServerConfig` (new) holds the Ollama base URL, the probe
  timeouts and the migration-lock timeout, because `AppSettings` is frozen
  (R4).

Open: migrations are not packaged in the wheel (Q1); model registration after
startup (Q2); real-uvicorn WebSocket support needs a new dependency (Q3);
parent-death detection (Q4). No new dependencies.

## ADR-085 — Local API security: loopback, per-launch token, Host and Origin checks

Status: Accepted (owner decision 2026-10-16; the owner confirmed details R1–R10
on 2026-10-16)
Date: 2026-10-16
Ticket: PHX-101

**Owner decision.** The backend binds loopback only. The Tauri shell hands it a
fresh secret on every launch, which every HTTP request and WebSocket must carry.
Host and Origin are checked, and there is no CORS for other origins.

- **Token handoff (R1, R2).** The token is one stdin line, read within 10 s and
  at most 1024 characters. It is URL-safe base64 without padding, 43–512
  characters, and decodes to at least 32 bytes. It is never passed in argv or
  the environment. The host is validated before the token is read. A missing
  or malformed token exits with code 2 before binding, and the message never
  contains the token. There is deliberately **no** `--dev-insecure` flag;
  developers pipe in a generated token.
- **Enforcement point.** `phoenix/api/security.py` (new) adds pure ASGI
  middleware covering HTTP and WebSocket scopes, before routing. Every route is
  covered, including `/health`, `/ready` and unknown paths.
  `build_server_app(settings, *, security, ...)` now *requires* a
  `LocalApiSecurity` (R9). `create_app()` is unchanged.
- **Checks, in order.**
  1. **Host (R4).** Exactly one `Host`, equal to the bound literal
     `127.0.0.1:<port>` or `[::1]:<port>`. Anything else is 403
     `forbidden_host`, which defeats DNS rebinding.
  2. **Origin (R5).** Absent, or exactly one of `http://tauri.localhost`,
     `https://tauri.localhost` or `tauri://localhost` (verified in tauri
     2.12.1). Anything else, including `null`, is 403 `forbidden_origin`.
     This is defence in depth only, not authentication: every Tauri app on
     Windows sends `http://tauri.localhost`, and a missing Origin is accepted.
     The token is the real gate.
  3. **Token (R3, R7).** HTTP needs exactly one
     `Authorization: Bearer <token>` (the scheme is case-insensitive); a
     failure is 401 `unauthorized` with `WWW-Authenticate: Bearer`. A WebSocket
     must offer `phoenix.v1` and exactly one `phoenix.auth.<token>`
     subprotocol. A failure closes it with 1008 *before accept*.
     `accept_websocket()` re-checks (failing closed when no security is
     installed) and echoes only `phoenix.v1`. The comparison is always
     `hmac.compare_digest` on bytes.
- **No CORS (R6).** No CORS middleware is installed, for any origin. The
  webview therefore cannot `fetch` the backend directly. The recommendation for
  PHX-106 and PHX-107 is that the Rust shell keeps the token and proxies HTTP
  and WebSocket traffic, so the token never enters webview JavaScript. The
  alternative, an exact-origin CORS allowlist for the Tauri origins only, needs
  an owner decision.
- **Errors (R8).** `{"error": {"code", "message", "request_id"}}` for 401, 403,
  404, 405, 422, other HTTP errors and 500. Messages are fixed text; input,
  header values and exception details are never echoed. `request_id` is a
  server-generated `uuid4().hex`, also sent as `X-Request-ID`; a client-supplied
  ID is ignored.
- **Secrets (R10).** The token is held as bytes, the `repr` is redacted, and the
  token is never logged. The access log and proxy-header trust stay off
  (ADR-084).
- **Guards.** The two `git diff origin/main` guards also exclude
  `phoenix/api/security.py` by name.

Open: the dev-mode Vite origin (Q1); the webview transport, proxy or CORS (Q2);
no in-process token rotation (Q3). No new dependencies.

## ADR-086 — Persisted missions, FIFO runner and user-decided grants

Status: Accepted (owner decisions 2026-10-16 and 2026-10-17; the owner confirmed
recommendations R1–R15 in `tickets/PHX-102-mission-persistence-runner.md` on 2026-10-17)
Date: 2026-10-17
Ticket: PHX-102

Owner decisions:

- **Concurrency.** One active mission at a time, with a FIFO queue. Queued
  missions show `queued` and can be cancelled (INV-019, INV-023).
- **Resume after approval is a linked re-run.** Approving a waiting permission
  stores the grant, supersedes the waiting mission (`superseded`) and queues a
  new mission for the same request with `rerun_of` set. There is no mid-mission
  checkpoint (ADR-082).
- **Grant duration is the user's choice.** "This session" is a SESSION grant
  scoped to the backend's per-launch session ID; "always" is PERSISTENT until
  revoked. Grants are stored per exact actor, tool, action, resource and reason,
  carry `user_decision=approved`, are created only by a user approval and never
  from model text (INV-003), and can be revoked.
- **Restart.** On startup, missions left `running` or `queued` are marked
  `failed/interrupted`. Nothing re-runs automatically; `run_again` is the
  explicit path (an API in PHX-104).
- **Retention.** Conversations, messages, missions and traces are kept 30 days,
  then deleted: on startup and hourly while running. Delete-conversation and
  clear-all exist. Grants are not purged by age.
- **Content.** The user's message and PHOENIX's final completed answer are
  stored as chat history. Traces stay hashes, lengths, IDs and statuses only
  (the runtime's `MissionTrace`). The pending PermissionRequest, including its
  resource path, is stored so the UI can show it. Raw model output, tool output
  and file content are never stored.

Design:

- **Modules.** `phoenix/persistence/missions.py` (rows and `MissionStore`) and
  `phoenix/api/missions.py` (`MissionService`), both new. The service is
  backend application logic, not transport: it imports no FastAPI. It cannot
  live in `phoenix/agents`, which ADR-082 keeps free of persistence imports.
- **Schema.** Migration `0006_missions`: `conversations`, `messages`,
  `missions` (status, path, failure code, responder error, `rerun_of`,
  timestamps, user and answer message links), `mission_traces` (one JSON
  `MissionTrace` per mission), `permission_requests` and `permission_grants`
  (SESSION or PERSISTENT, P1–P3, `revoked_at`, provenance
  `approved_request_id`). Structural CHECK constraints mirror `MissionResult`'s
  consistency rules.
- **Store.** Short synchronous transactions called through `asyncio.to_thread`,
  serialized in-process by a lock. Every status change is compare-and-set, so a
  stale writer (a late settle after a delete, a claim racing a cancel) changes
  nothing.
- **Runner.** One task consumes an in-memory FIFO. Each mission runs in its own
  task (`PhoenixRuntime.run` needs a caller task) with the stored, unrevoked
  grants that pass `document_grants` (persistent, plus this launch's session
  grants) and the launch session ID. A runtime defect is recorded
  `failed/internal_error` and the queue continues (INV-021).
- **Wiring.** `BackendServices.missions`; the lifespan starts the service after
  building it. `cancel_active_missions`, which the launcher already calls
  before draining and the lifespan calls on exit, now stops the service first:
  queued and running missions become `failed/interrupted`, consistent with the
  restart rule.
- **Frozen files.** `phoenix/api/server.py` (wiring) and `alembic/env.py` (row
  registration, as PHX-042 did) change. Following ADR-082/083, both leave the
  `origin/main` baseline and are pinned to the PHX-102 implementation commit
  (`PHX_102_COMMIT` / `PHX_102_PATHS`, `9ccc5d4522c513832f85da49c44a236284aab7b7`), landed as an implementation commit then
  a pin commit; the PR must merge with a merge commit. The two `git diff
  origin/main` guards exclude the new modules, the migration and `env.py` by
  name. Head expectations move to `0006_missions` and revision counts to 6;
  table-list expectations gain the six tables; the PHX-050 `env.py` byte guard
  allows exactly the one new registration block.
- **Review fixes (2026-10-17).** Stored grant rows that are not valid grants
  are skipped and counted (`skipped_grants`), never raised. Any failure after
  a claim records `failed/internal_error` and the queue continues; a failed
  claim records the dequeued mission `failed/internal_error` too (R15).
  `run_again` is refused once a re-run of that mission exists. Once `stop()`
  begins nothing new is enqueued, and it waits (bounded) for writes in flight
  before its final interrupt sweep. `list_grants` returns this launch's usable
  grants; with `include_inactive` it adds earlier launches' SESSION grants as
  `expired` and revoked grants as `revoked`.
- **Revoke timing.** Grants are read once when a mission starts, so revoking
  does not affect a mission that is already running; it applies from the next
  mission.
- **Follow-up: SQLite foreign keys.** Production does not set
  `PRAGMA foreign_keys=ON`. The store deletes in foreign-key order and the
  tests run with foreign keys enforced, so purge and delete leave no orphans.
  Enabling the pragma belongs to a separate ticket: `database.py` is frozen and
  the change also affects `document_chunks`.

No routes (PHX-104) and no events (PHX-103). No new dependencies.

## ADR-087 — Content-free runtime events and the `WS /events` stream

Status: Accepted (owner decisions 2026-10-17; the owner confirmed recommendations
R1–R16 in `tickets/PHX-103-runtime-events-ws.md` on 2026-10-17)
Date: 2026-10-17
Ticket: PHX-103

This supersedes ADR-081's "Events. None" for the runtime. The 32-type vocabulary
is unchanged.

Owner decisions:

- **The runtime publishes.** `PhoenixRuntime(events=...)` takes an optional sink
  (default none, so behaviour without one is unchanged). Events are live, use
  only existing types, and are correlated by the mission (request) ID and the
  task ID. Publishing never blocks or fails a mission (INV-021, INV-022).
  MissionService publishes coarse mission events.
- **Slow clients are disconnected.** `WS /events` is authenticated by the
  PHX-101 helper. Each connection has a bounded buffer; a full buffer closes
  that client with 1013, and the client reloads state over REST, the source of
  truth. There is no replay in v1.
- **Payloads hold IDs, states, codes and timings only**, enforced by an
  allowlist schema.

Design:

- **Modules.** `phoenix/agents/mission_events.py` (new) holds `EventSink`, the
  payload allowlist, `mission_event`, `check_event`, `EventPublisher` and the
  per-mission `MissionEvents`. It imports no API, persistence, tool or
  provider module. `phoenix/api/event_stream.py` (new) holds `EventStream`, the
  sink and fan-out. `runtime.py`, `tool_steps.py`, `missions.py` and `server.py`
  change; `responders.py` is byte-identical.
- **Mapping.** End events also carry `duration_ms`.

  | Event | When | Payload (besides IDs) |
  | --- | --- | --- |
  | `task.created` | COMPLEX task created | `path` |
  | `task.state_changed` (`scope=task`) | every task transition | `from_state`, `to_state`, `failure_code`/`responder_error` on failure |
  | `task.state_changed` (`scope=mission`) | every MissionService status change | `mission_status`, `failure_code`, `responder_error`, `rerun_of` |
  | `agent.started` / `agent.completed` / `agent.failed` | PHOENIX direct, AIZEN planner, PHOENIX assignment, each step's producer | `role`, `step_id`, `outcome` (`completed` or `waiting_permission`), `code` |
  | `step.started` / `step.completed` | each task step; completed only after LEVI passes it | `step_id`, `domain`, `final` |
  | `tool.requested` / `tool.started` / `tool.completed` / `tool.failed` | a document step's one call | `tool_id`, `call_id`, `operation`, `permission_request_id`, `tool_status`, `decision`, `code` |
  | `permission.requested` | the call needs the user's approval | `tool_id`, `call_id`, `permission_request_id`, `risk_level` |
  | `verification.started` / `verification.passed` / `verification.failed` | LEVI per step | `step_id`, `verification_id`, `verdict`, `code` |

  A DIRECT mission publishes only `phoenix`'s agent events (it has no task),
  plus the mission events. Unused: `phoenix.*`, `permission.approved/denied`,
  `model.*`, `provider.unavailable`, `recovery.*`, `memory.created`,
  `document.indexed` and `voice.*`.
- **Allowlist.** Every published type has a complete key list, and every key a
  kind: a canonical UUID, a step ID (`s1`-`s99` or `final`), a closed value
  set (including the tool IDs and the registry's agent IDs), integer
  milliseconds or a boolean. Correlation and task IDs must be UUIDs and agent
  IDs registry agents. No text, path (not even `notes.txt`), prompt, output or
  secret can pass. The bridge
  re-checks every event before sending (`rejected_events`).
- **Never blocking, never failing.** `EventPublisher` swallows anything a sink
  or the allowlist raises (only cancellation propagates) and counts it in
  `PhoenixRuntime.dropped_events` / `MissionService.dropped_events`. The
  production sink, `EventStream.publish`, never raises: a bus that refuses
  because a subscriber is full is counted in `EventStream.dropped_events`
  and closes every client with 1013 (see Bridge).
  Nothing reaches `MissionResult` or `MissionTrace`.
- **Bridge.** `EventBus` publication is all-or-none, so no client may own a
  queue the publisher can fill. The bridge holds one subscription (bus
  capacity 4096) while any client is connected; a shared pump drains it
  promptly, yielding per event, into per-client buffers
  (`ServerConfig.event_client_buffer`, default 256). A full client buffer
  closes that client alone with 1013. A bus refusal (the bridge's own
  subscription overflowed) closes every client with 1013, so no client misses
  events silently. Long publish loops (the interrupt sweep) yield. Closes wait
  at most 5 s.
- **Connection cap.** `ServerConfig.event_max_connections` (default 16). Beyond
  it the upgrade is refused with HTTP 503 (`too_many_connections`,
  `Retry-After: 1`) via the WebSocket Denial Response extension, distinct from
  authentication's 403; each connection runs three tasks.
- **Frames.** JSON envelopes (eight fields), server to client only; client
  frames are ignored. Without running services the socket is accepted and
  closed 1013.
- **Shutdown.** The lifespan exit closes remaining sockets with 1001 and
  refuses new ones; under the launcher uvicorn closes them with 1012 first.
  `launcher.py` is unchanged.
- **Frozen files.** `runtime.py` and `tool_steps.py` (PHX-047 pin), `server.py`
  (PHX-102 pin) and `missions.py` (`origin/main`) are pinned to the PHX-103
  implementation commit (`PHX_103_COMMIT` / `PHX_103_PATHS`, flake-fix round `023be4ea7cd71ff8ff5c74b0ff8992cf7bb0c134`, which also pins `event_stream.py` and `mission_events.py`; earlier rounds `829670b`, `10c1723`), landed as an
  implementation commit and then a pin commit; the PR must merge with a merge
  commit. `prompts.py` stays on PHX-047 and `alembic/env.py` on PHX-102. The
  two `git diff origin/main` guards exclude `phoenix/api/event_stream.py`.

Limitations: a phase cut short by the mission or planning deadline reports
`cancelled`; the task's terminal event carries `timeout`. No replay. Deleting
conversations publishes no terminal mission-scope event for the deleted
missions; under the launcher, shutdown arrives as uvicorn's 1012.

## ADR-088 — `websockets` as uvicorn's WebSocket backend

Status: Accepted (owner decision 2026-10-17)
Date: 2026-10-17
Ticket: PHX-103

Real-uvicorn WebSockets needed a new dependency (ADR-084 Q3). The owner chose
`websockets`.

- **Requirement.** `websockets>=17.2,<18`, locked **17.2** (released
  2026-10-03, BSD-3-Clause), following the bounded style of `uvicorn>=0.30,<1`.
  It has no transitive packages, so the lock gains exactly one record and the
  `phoenix` record's two references; every other record is unchanged. It is a
  direct requirement, not the `uvicorn[standard]` extra.
- **Backend.** uvicorn 0.54's `ws="auto"` selects
  `WebSocketsSansIOProtocol` when `websockets` is importable. No uvicorn option
  changes.
- **Guards.** Following the PHX-088 precedent, the workspace byte pins for
  `pyproject.toml` and `uv.lock` move to the PHX-103 implementation commit;
  the other files in that map stay on `4a70867`. The PHX-073 exactness gate
  (`tests/test_document_generation.py`) becomes fixed history plus retention
  checks, and the new PHX-103 gate (`tests/test_events_ws.py`) owns current
  manifest and lock exactness against `4a70867`.

## ADR-089 — REST API v1 and the settable document workspace

Status: Accepted (owner decisions 2026-10-17; the owner confirmed recommendations
R1–R24 in `tickets/PHX-104-rest-api-v1.md` on 2026-10-17)
Date: 2026-10-17
Ticket: PHX-104

Owner decisions:

- **Asynchronous chat.** `POST /chat` queues a mission through
  `MissionService.submit` and returns 202 with the mission and conversation IDs
  at once. Progress arrives over `WS /events`; the answer comes from
  `GET /missions/{id}`. No request is held open for a mission (INV-019).
- **Scope.** Chat, missions (list, get, cancel, run again), permissions (list
  pending, approve with `session` or `always`, deny, list grants, revoke),
  agents, models, settings (GET; PATCH for the workspace only) and
  conversations (list, messages, delete, clear all). `/files/upload` and
  `/memory/search` are deferred to PHX-110 and do not exist.
- **Only the workspace folder is writable.** It is validated as an existing
  local directory, stored in the database (migration 0007), and used by
  missions that start afterwards, never by one already running. Model tiers,
  ports and retention are read-only. Changing it does not re-validate or carry
  over grants for paths outside the new root.

Design:

- **Modules.** `phoenix/api/routes/` (new: `common`, `schemas`, `missions`,
  `permissions`, `system`), `phoenix/api/workspace.py` (new: validation and
  `WorkspaceService`) and `phoenix/persistence/settings.py` (new:
  `app_settings` row and `SettingsStore`). The routes are thin; MissionService
  and WorkspaceService own behaviour. `build_server_app` imports the routes
  when it runs, because they import `server.py`.
- **Contract.** Pydantic schemas with `extra="forbid"` for every body and
  response; canonical lowercase UUIDs in paths (else 422); limit/offset
  pagination (1–200, default 50; offset at most 1,000,000) returning
  `{"items", "limit", "offset", "total"}`. Errors use the PHX-101 envelope: 404
  `not_found`, 409 `invalid_state`, 422 `invalid_request` or `workspace_*`, 503
  `service_unavailable`. The full table is in `docs/13_API_CONTRACTS.md`.
- **Content.** Responses carry the user's message text and PHOENIX's final
  answer, IDs, statuses, codes, timestamps, and a pending permission's tool,
  action, absolute resource path and risk level. Never raw model output, tool
  output or file content; mission detail exposes only the trace's task ID and
  task states.
- **Approvals (INV-003, INV-005).** Approve takes only a duration (`session` →
  SESSION for this launch, `always` → PERSISTENT until revoked); deny takes no
  fields. The grant's tool, action, resource and actor come from the stored
  request; a body naming them is refused.
- **Workspace validation.** Text rules first (absolute, no `..`, not UNC or a
  device path, no control characters, at most 4,096 characters), so no network
  path is touched; then existing directory; no symlink, junction or reparse
  point, and a real path equal to the given one; not a drive root; not inside
  the OS or program directories; not overlapping the PHOENIX data directory.
  FilesystemTool still checks every read.
- **Storage.** Migration `0007_app_settings`: `app_settings(key, value,
  updated_at)` with `key IN ('workspace_root')`, the table docs/12 planned.
- **Reconfiguration.** FilesystemTool is immutable and the runtime takes it at
  construction, so a new root builds a new tool and runtime (`runtime.py` is
  unchanged). `MissionService.use_runtime` makes missions that start afterwards
  use it; each mission keeps the runtime it started on, which also receives its
  cancel and shutdown. At startup the stored root is validated again before the
  first mission; an invalid one is reported `unavailable` and documents stay
  off.
- **Grants across a change.** Untouched. They match exact absolute resources,
  and every resource a document step can request is inside the current root,
  so a grant for a path outside it never applies.
- **Frozen files.** `phoenix/api/server.py` and `phoenix/api/missions.py` (from
  the PHX-103 pin) and `alembic/env.py` (from the PHX-102 pin) are pinned to
  the PHX-104 implementation commit (`PHX_104_COMMIT` / `PHX_104_PATHS`,
  review round `7af605e26afcaf0e7000ee44d312e741a9107b2b`; first round `f9a075c`), landed as an implementation commit then a pin commit; the PR must merge with
  a merge commit after PHX-103. Head expectations move to `0007_app_settings`.
  The two `git diff origin/main` guards exclude the new modules and migration
  by name; the PHX-050 `env.py` byte guard strips the one new registration
  block from the file and from main.

- **Review fixes (2026-10-17).** The stored root is validated again before
  every mission that would use it (a preflight given with the runtime); if it
  fails, that mission fails `tool_rejected`, the workspace becomes
  `unavailable` and later missions run without documents (D1). Approval is
  refused with 409 `workspace_changed` for a resource outside the active root
  (D2). The data-directory check uses real paths (D3). System folders also come
  from the Windows API and fixed defaults (D4); `$Recycle.Bin`, `System Volume
  Information` and the user's AppData tree (`workspace_app_data`) are refused,
  and `C:\Users\Public` is allowed but recorded for the owner. The root is
  stored in its canonical real-path spelling (D5). Request bodies are capped at
  64 KiB (413 `payload_too_large`, new `phoenix/api/body_limit.py`); a failed
  chat submit leaves no empty conversation; `set_root` maps FilesystemTool
  construction failures to 422 and needs a running service.
- **Follow-up: FilesystemTool root hardening.** FilesystemTool should `lstat`
  its root on every call, refuse a symlink, junction or other reparse point and
  require `realpath(root) == root`, because `_target` follows a junction that
  replaced the root after validation. PHX-104's preflight narrows the window to
  a swap during a mission; `filesystem.py` is frozen, so this is its own ticket.

Limitations: pages are cut in memory from the store's lists;
`/grants?include_inactive` is not bounded by retention; mission detail loads the
conversation's messages on each poll; new error codes carry the envelope's
generic message. No new dependencies and no new event types.

## ADR-090 — Request triage: a routing hint with an observable COMPLEX fallback

Status: Accepted (owner decisions 2026-10-18; the owner approved the `TRIAGE_SYSTEM`
text, the routing-set labels and recommendations R1–R14 in
`tickets/PHX-105-request-triage.md` on 2026-10-18; live routing accuracy 85%)
Date: 2026-10-18
Ticket: PHX-105

This replaces ADR-081's "Path. The caller supplies DIRECT or COMPLEX. Triage is
deferred."

Owner decisions:

- **Triage model.** `qwen3.5:4b` with thinking off returns exactly
  `{"path": "direct" | "complex"}` for every request without an explicit path.
  A new `triage` row in the tier table (T1 4B, then T0 2B) runs on the
  structured providers. The input is the user's request text only. The
  decision is a routing hint: it grants nothing (INV-003), and PHOENIX runs
  the chosen path with all its checks (INV-001).
- **Fallback: COMPLEX.** Any triage failure except cancellation (not JSON,
  schema, truncated, oversized, invalid text, timeout, provider unavailable or
  error, no eligible model, an internal error) takes the COMPLEX path, AIZEN
  planning plus LEVI verification. It is observable (INV-022): `MissionTrace`
  records `triage_outcome` (`decided`, `fallback`, `client`) and
  `triage_reason`, and PHOENIX's triage agent events carry them.
- **Labelled routing set.** `benchmarks/routing/routing-v1.toml`, 60 harmless
  labelled requests, versioned and checksummed like PHX-091.
- **Client override kept.** `POST /chat` `path` is optional. Omitted, triage
  decides; given, it is used as is. Missions record who decided.

Design:

- **Modules.** New `phoenix/agents/triage.py`: `ModelTriageResponder` (the
  frozen responders' `_generate` and `parse_json_object`, a strict
  `extra="forbid"` wire model), `TriageOutcome`, `TriageFallbackReason` and
  `fallback_reason`. `responders.py` is byte-identical.
- **Prompt.** `phoenix-prompts-v4` adds `TRIAGE_SYSTEM` (`PHOENIX ROLE:
  triage`); every v3 constant is unchanged (hash-pinned). Unsure means
  `complex`, matching the fallback.
- **No guard on the request.** The request is the user message, as for the
  DIRECT and planning roles (ADR-081). `PromptInjectionGuard` has origins only
  for untrusted third-party content; labelling the user's own request as one,
  and letting its blocking signals turn ordinary wording into a fallback, would
  misuse it. The prompt says the request is data, and the decision cannot
  grant anything.
- **Runtime.** `PhoenixRuntime.run(request, path=None)` triages inside the
  mission deadline, as one `phoenix` agent phase (role `triage`) bounded by
  the new `RuntimeLimits.triage_timeout_seconds` (60 s), which covers the health
  probe, model selection and the generation. Health is still probed once per
  mission: the triage probe serves the chosen path. `decided_by` lets a re-run
  record a reused decision; it changes nothing else.
- **Trace.** `MissionTrace.path` may be `None` only when no decision was made
  (cancelled, or the mission deadline, during triage). `triage_outcome` is
  `None` only then and in traces stored before PHX-105; a reason appears only
  with `fallback`, and a fallback always has the `complex` path.
- **Events.** No new event types. The allowlist gains the role `triage`,
  `path` and `triage_outcome` on `agent.completed` and `agent.failed`, and
  `triage_reason` on `agent.failed`, all closed enums. A decision publishes
  `agent.completed`; a fallback `agent.failed` (warning) with the mapped
  `code`, then the COMPLEX path's own events. Cancellation publishes
  `agent.failed` with `code="cancelled"` and no decision.
- **Persistence.** Migration `0008_mission_triage` makes `missions.path`
  nullable and adds `triage_outcome`, with checks: the path and outcome are
  set together, the outcome is one of the three values, and `fallback` means
  `complex`. Existing rows become `client`. `settle` fills a NULL path from the
  run's decision; a decided path never changes. Re-runs (`run_again`, and the
  re-run an approval queues) copy the path and outcome, so they never re-triage
  unless the original never got a decision.
- **REST.** `ChatRequest.path` is optional (`null` = omitted); mission summaries
  and detail add `triage_outcome`, and `path` is `null` until triage decides.
- **Routing benchmark.** `scripts/routing_benchmark.py` (added to the
  `mypy-scripts` gate) runs `ModelTriageResponder` alone with the runtime's
  limits on Ollama with thinking off. It reports accuracy, a confusion
  matrix, per-category accuracy, latency and fallbacks to the gitignored
  `.routing-bench/`. `--dry-run` validates the set. `fyp_benchmark.py`'s
  `routing_accuracy` reason points to it.
- **Frozen files.** `prompts.py` (from the PHX-047 pin), `runtime.py` and
  `mission_events.py` (from the PHX-103 pin), `api/missions.py` (from the
  PHX-104 pin) and `persistence/missions.py`, `api/routes/missions.py` and
  `api/routes/schemas.py` (from `origin/main`) move to the PHX-105
  implementation commit (`PHX_105_COMMIT` / `PHX_105_PATHS`, `e34142ca0062dc14c05a5a1d18720b64446cdaa7`), landed as an
  implementation commit then a pin commit. Head expectations move to
  `0008_mission_triage`; the two `git diff origin/main` guards exclude the
  new migration by name.

Limitations: triage adds one generation (and its latency) to every request
without a path. A mission interrupted by a crash before it settles has no
recorded decision, so its re-run triages again. Triage accuracy is not yet
measured live (the routing benchmark is the instrument). No new dependencies
and no new event types.

## ADR-091 — The desktop shell owns the backend and proxies the webview's traffic

Status: Accepted (the owner confirmed recommendations R1–R12 in
`tickets/PHX-106-tauri-backend-sidecar.md`, and the `ureq` and `tungstenite`
dependencies, on 2026-10-18)
Date: 2026-10-18
Ticket: PHX-106

Context: PHX-101 R6 (confirmed by the owner) recommends that the Rust shell hold
the per-launch token and proxy HTTP and WebSocket traffic, so XSS in the webview
cannot steal it and the backend needs no CORS. The launcher reads the token on
stdin and announces the port in one JSON line on stdout (ADR-084, ADR-085).

Decision:

- **Lifecycle.** At setup the shell locks `<data>/phoenix.desktop.lock`
  (exclusive; a second app reports `already_running`), generates 32 random bytes
  (URL-safe base64), starts `python -m phoenix.api --host 127.0.0.1 --port 0
  --data-dir <dir>` as an argument array without a shell or console window,
  writes the token as one stdin line, and waits at most 90 s for the ready line.
  On Windows the child starts suspended inside a kill-on-close job object, so the
  venv launcher's interpreter is in the job and dies with the app even if the app
  crashes. A watcher reports `degraded` / `backend_exited`; app exit kills the
  tree. The webview sees only `starting`, `ready` or `degraded` with a closed code.
- **Demo/dev launch mode.** The interpreter is the repository's `.venv`
  (`PHOENIX_BACKEND_ROOT` / `PHOENIX_BACKEND_PYTHON` override, the build
  checkout by default); data is `PHOENIX_DATA_DIR` or `~/.phoenix/data`. PHX-092
  replaces this with a bundled backend.
- **Two commands.** `api_request(method, path, body)` forwards only 18 exact
  (method, route) pairs of the REST v1 contract, with canonical UUID segments,
  listed query keys and JSON object bodies up to 64 KiB; destructive routes that
  no adapter uses are absent. Rust adds `Authorization: Bearer`, sends no Origin,
  disables the environment proxy and redirects, and returns only the status and
  JSON body, or a closed error code. `backend_status()` returns the state.
- **Events.** One Rust `tungstenite` client authenticates to `WS /events` with
  the PHX-101 subprotocols, re-checks each frame as a bounded envelope, and
  emits `phoenix:event` and `phoenix:events-status` to the main window;
  it reconnects after any close.
- **ACL and CSP.** `build.rs` declares the two commands as an app manifest; the
  only capability grants `allow-api-request`, `allow-backend-status`,
  `core:event:allow-listen` and `core:event:allow-unlisten`. No plugin. The
  production CSP's `connect-src` changes from `'none'` to exactly
  `ipc: http://ipc.localhost`, Tauri 2's IPC channel; the webview still cannot
  reach the backend, Ollama, Colibri or any network origin (INV-011).

Consequences: the token exists only in Rust memory and the child's stdin. New
crates `ureq` 3.4 and `tungstenite` 0.28 (no TLS); `base64`, `getrandom`,
`serde`, `serde_json` and `windows-sys` were already locked. The changed native
files move their guard pins to the implementation commit (ADR-075 precedent).
App exit does not shut the backend down gracefully; missions in flight are
recorded `interrupted` at the next start (PHX-102).

## ADR-092 — Live port adapters behind an unchanged App

Status: Accepted (the owner confirmed recommendations R1–R11 in
`tickets/PHX-107-frontend-port-adapters.md` on 2026-10-18)
Date: 2026-10-18
Ticket: PHX-107 (with a minimal piece of PHX-108)

Decision:

- **Boundary.** `src/adapters/tauri.ts` is the only webview code that touches
  IPC (`invoke`, `listen`, `isTauri` from `@tauri-apps/api` 2.12.1). `core.ts`
  holds a store that maps REST responses and content-free events to the
  existing ports; `ConnectedApp.tsx` renders App with them through
  `useSyncExternalStore`. App.tsx, its readers and its guards are unchanged;
  every snapshot is admitted by them as they are.
- **Source of truth.** REST. Events schedule coalesced refetches; backend
  readiness and every stream reconnection or close reload everything; a 3 s
  poll covers a tracked mission while it is queued or running.
- **Ports.** Chat (POST /chat; the latest 64 transcript entries, long answers in
  ordered parts), Permissions (pending requests; `approval_required` evidence
  only for P1–P4; approve with `session`, deny with `{}`; the store follows the
  linked re-run), Task Graph and Agent Network (live step and agent state from
  events; objectives and dependencies are not published, so the objective text
  states the live status and no edges are drawn), Models (`GET /models`) and
  Observability (recent events as traces, timing and result metrics). Memory,
  Files and Voice stay unconnected.
- **Minimal PHX-108.** A connection bar above App shows the backend state, sets
  the document workspace with `PATCH /settings` (validation codes explained),
  defaults the mission path to PHX-105 triage ("Automatic", no `path`) with
  direct and planned steps as overrides, and offers cancel and run again for
  the tracked mission.

Consequences: the frontend has no direct backend, provider or tool access
(INV-011); decisions are only user intents that PermissionEngine evaluates
(INV-003, INV-005). App's captions still describe ports as caller-supplied and
transport as not implemented; updating that copy is left to PHX-108.

## ADR-093 — Bounded MAHORAGA recovery in missions

Status: Proposed (the recommended defaults are applied under the owner's
overnight instruction, "do which is recommended", 2026-10-18; R1–R16 in
`tickets/PHX-109-mahoraga-missions.md` await the owner's review)
Date: 2026-10-18
Ticket: PHX-109

ADR-091 and ADR-092 are reserved by the desktop branch
(`feature/PHX-106-107-desktop-connected`).

This replaces ADR-081's "Failures. Explicit, with no hidden retries ...
MAHORAGA and KAIDO stay deferred" for MAHORAGA. KAIDO stays deferred.

Context: in FYP configuration 2 (2026-10-18), 15 of 32 missions failed. Most
failures were structural (`schema`, `truncated`, `not_json`) or transient
(`provider_unavailable`), and every completed mission was correct.

Decision:

- **What is retried.** A mission generation is retried once when it fails with
  `responder_output` (`not_json`, `schema` or `truncated`), `provider_unavailable`
  or `provider_error`. Nothing else is retried: LEVI's `verification_failed`
  stands (INV-008), and timeouts, cancellation, permission decisions, tool
  outcomes, prompt-boundary refusals and routing failures stay explicit
  (INV-021). Document steps, which would repeat a permissioned tool call, are
  not retried; their LEVI generation is.
- **MAHORAGA decides.** Each decision goes through the existing
  `MahoragaRecovery.recover()`, with a deterministic `PolicyResponder`
  (PHOENIX's fixed policy, no model) and a one-step evidence graph describing
  the failed generation. MAHORAGA's own budget check bounds the mission: at
  most three actions (`RuntimeLimits.max_recovery_actions`, 0 to 3; 0
  disables recovery), plus one retry per generation (INV-009). Exhaustion, or
  a retry failing again with a retryable failure, keeps the original failure
  code.
- **How it retries.**
  - A provider failure waits `recovery_backoff_seconds` (2 s, at most 30 s)
    and retries only if that provider's health re-check is `HEALTHY`;
    otherwise the mission fails with the original code.
  - A truncated thinking role (specialist or final answer) retries on the
    structured think:false provider when one is configured
    (`adjust_parameters`).
  - Everything else retries the same generation (`retry_same`).
  - Retries stay in the current task state under the same deadlines, and
    still pass LEVI.
- **Failed plan.** A planner `schema` failure that persists after its retry
  falls back to PHOENIX's DIRECT path (`switch_agent`). The COMPLEX task ends
  `FAILED`, the trace keeps `path="complex"`, and the answer is unverified
  DIRECT output (ADR-081), so it never counts as COMPLEX success.
- **Observability (INV-022).** `MissionTrace.recoveries` records each decision
  with closed values only: role, agent, step, class, responder error, action
  and outcome. The existing `recovery.started`, `recovery.completed` and
  `recovery.exhausted` types publish it (agent `adaptive_recovery`), and the
  PHX-103 allowlist gains them with closed-enum fields. The vocabulary stays
  at 32.
- **Frozen files.** `responders.py`, `recovery.py` and `failsafe.py` are
  byte-identical. `runtime.py` and `mission_events.py` move from the PHX-105
  pin to the PHX-109 implementation commit (`PHX_109_COMMIT` /
  `PHX_109_PATHS`). This lands as an implementation commit followed by a pin
  commit.

Limitations:

- KAIDO is not wired, so a provider that stays down fails the mission.
- Re-generating after a LEVI rejection is deferred to the owner.
- The direct fallback covers every plan `schema` failure, not only an empty
  plan, because the runtime sees only the closed code.
- The live effect on the FYP success rate is not yet measured.

No new dependencies, event types, migrations or API changes.
