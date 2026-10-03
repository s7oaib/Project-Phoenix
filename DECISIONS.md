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
