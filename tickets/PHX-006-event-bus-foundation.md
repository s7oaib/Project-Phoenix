# PHX-006 — Event Bus Foundation

## Status
TODO

## Phase
Phase 0 — Foundation

## Objective
Create the framework-independent in-process event foundation PHOENIX will use for observable orchestration state, live UI progress, logging, and evaluation. Define the canonical event envelope and a deterministic asynchronous fan-out bus without persistence, domain producers, or privileged behavior.

## Authoritative documents
- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `/docs/01_ARCHITECTURE.md`
- `/docs/02_SYSTEM_FLOW.md`
- `/docs/13_API_CONTRACTS.md`
- `/docs/14_EVENT_SYSTEM.md`
- `/docs/23_OBSERVABILITY.md`

## Dependencies
- PHX-001 — Repository Bootstrap: DONE
- PHX-002 — FastAPI Backend Foundation: DONE
- PHX-003 — Tauri + React Desktop Foundation: DONE
- PHX-004 — Configuration Foundation: DONE
- PHX-005 — SQLite / SQLAlchemy / Alembic Foundation: DONE

## Requirements
- Keep the canonical event model and bus framework-independent under `phoenix/core/`.
- Implement the event envelope required by `docs/14_EVENT_SYSTEM.md`: `event_id`, `event_type`, UTC `timestamp`, optional `task_id`, optional `agent_id`, `payload`, `severity`, and `correlation_id`.
- Represent the documented core event names without silently renaming them.
- Event identifiers and correlation identifiers must be stable, serializable values; callers must be able to supply a correlation ID so related events can be traced.
- Event timestamps must be timezone-aware UTC.
- Payloads must be constrained to JSON-serializable structured data suitable for future API/WebSocket transport and structured logging.
- Provide an in-process asynchronous publish/subscribe bus with explicit subscription lifetime.
- Preserve publication order per subscriber.
- Fan out each published event to every subscriber that was active when publication occurred.
- Publishing with no subscribers must succeed without side effects.
- Subscriber removal/close must be explicit and safe to repeat.
- If subscriber buffering is bounded, overflow behavior must be explicit and tested; events must never be silently dropped.
- Do not create background worker tasks merely to keep the bus alive.
- Do not create files, directories, database rows, network sockets, or external connections during event-envelope construction, bus construction, subscription, or publication.
- Keep event persistence out of this ticket. PHX-006 is memory-only.
- Keep FastAPI/Tauri/provider/tool/database imports out of the core event module.
- Existing API, persistence, configuration, and desktop behavior must remain green.

## Canonical core event names
Preserve the frozen names from `docs/14_EVENT_SYSTEM.md`:
- `phoenix.started`
- `phoenix.ready`
- `phoenix.degraded`
- `agent.started`
- `agent.completed`
- `agent.failed`
- `task.created`
- `task.state_changed`
- `step.started`
- `step.completed`
- `tool.requested`
- `tool.started`
- `tool.completed`
- `tool.failed`
- `permission.requested`
- `permission.approved`
- `permission.denied`
- `model.loading`
- `model.loaded`
- `model.unloaded`
- `provider.unavailable`
- `verification.started`
- `verification.passed`
- `verification.failed`
- `recovery.started`
- `recovery.completed`
- `recovery.exhausted`
- `memory.created`
- `document.indexed`
- `voice.listening`
- `voice.speaking`
- `voice.interrupted`

## Event severity
Use a small explicit severity vocabulary appropriate for structured observability, e.g. debug/info/warning/error/critical, with a sensible non-error default. Do not encode permission/risk policy into event severity.

## Backpressure / buffering
Choose one simple, deterministic in-process buffering policy and test it. A bounded per-subscriber queue is acceptable if queue capacity is configurable and publication fails explicitly before partial fan-out when a subscriber cannot accept the event. Silent event loss is not acceptable.

## FastAPI / WebSocket boundary
- `WS /events` remains part of the frozen API contract, but PHX-006 is the framework-independent event-bus foundation.
- Do not couple `phoenix/core/` to FastAPI or WebSocket objects.
- A minimal app-level bus injection/storage hook is allowed only if it materially improves future transport integration and does not start background work.
- A network WebSocket streaming implementation is not required for this foundation ticket; do not invent a transport lifecycle before event producers exist.

## Non-goals
- No durable event store or event-replay system.
- No event database table or persistence migration.
- No audit trail implementation; PHX-042 owns audit persistence.
- No structured logging backend or metrics exporter.
- No Redis, Kafka, NATS, RabbitMQ, Celery, or external broker.
- No cross-process/distributed event transport.
- No orchestration, task graph, agents, permissions, tools, providers, memory, RAG, voice implementation, or event producers for those systems.
- No `WS /events` network transport implementation in core.
- No PHX-007 hardware scanning.
- No frontend live-event UI.

## Tests
At minimum cover:
- valid canonical envelope construction
- unique event IDs by default
- supplied correlation ID preservation
- timezone-aware UTC timestamp
- canonical event-name validation
- severity validation/default
- JSON-safe payload validation/serialization
- publish with zero subscribers
- single-subscriber delivery
- multi-subscriber fan-out
- per-subscriber publication order
- explicit unsubscribe/close behavior
- repeated close safety
- no delivery after unsubscribe
- chosen queue/backpressure behavior, including no silent drop/partial fan-out
- event/bus construction and publication have no filesystem/database/network/process side effects
- imports remain framework-independent
- FastAPI `/health`, persistence, configuration, and desktop regression checks remain green

## Verification
At minimum run:
- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`
- `npm test`
- `npm run typecheck --workspace @phoenix/desktop`
- `npm run build --workspace @phoenix/desktop`
- `git diff --check`

Also provide targeted executable evidence that event construction, bus construction, subscription, publication, and unsubscribe create no files, directories, SQLite connections, network sockets, subprocesses, or hidden background tasks.

## Acceptance criteria
- Canonical typed event envelope matches the frozen event-system contract.
- All frozen core event names are represented exactly.
- Timestamps are UTC and identifiers/correlation are traceable.
- Payloads are safe for future JSON transport.
- In-process asynchronous fan-out bus works deterministically.
- Publication order is preserved for each subscriber.
- Event loss/backpressure policy is explicit; no silent drops.
- Subscription lifetime is explicit and leak-resistant at the core API level.
- Core events/bus have no FastAPI/Tauri/database/provider/tool coupling.
- No persistence, broker, WebSocket network transport, or future feature scope is introduced.
- PHX-001 through PHX-005 regressions remain green.
- No secrets/generated runtime artifacts are committed.
- Relevant invariants remain satisfied.
- Ticket and `/FEATURE_TICKETS.md` are updated only after all acceptance criteria pass.

## Completion evidence
Report:
- changed files
- dependencies added, if any
- event-envelope contract
- exact canonical event-type representation
- severity vocabulary
- event-bus API and buffering/backpressure policy
- exact tests and verification commands/results
- side-effect/isolation evidence
- invariant IDs reviewed
- decisions added, if any
- known limitations
