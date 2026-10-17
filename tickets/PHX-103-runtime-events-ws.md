# PHX-103 — Runtime events and `WS /events`

Status: IN_PROGRESS (implemented; awaiting owner confirmation of R1–R16 and the merge)
Phase: 10 — Product Integration (milestone M1, backend)
Branch: `feature/PHX-103-runtime-events-ws` (local commits only; no push, no PR)
Base: main `4c649d7` (includes PHX-100, PHX-101 and PHX-102)

## Objective

Give the desktop UI live, observable mission progress (INV-019, INV-022) without moving the source of truth. The runtime and the mission service publish content-free events with the frozen 32-type vocabulary. An authenticated `WS /events` streams them to the shell with a bounded buffer per client. REST (PHX-104) stays the source of truth for answers and state.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/INVARIANTS.md` (INV-001, INV-011, INV-019, INV-021, INV-022, INV-023), `/MASTER_SPEC.md`
- `docs/13_API_CONTRACTS.md`, `docs/14_EVENT_SYSTEM.md`, `docs/09_SECURITY.md` ("Local API"), `docs/25_DEPENDENCIES.md`
- ADR-081 to ADR-086; PHX-047, PHX-100, PHX-101 and PHX-102

## Owner decisions (2026-10-17)

1. **Dependency.** Add `websockets` as uvicorn's WebSocket backend. Use a bounded version in the repo's style, update `uv.lock` with uv, and record it in an ADR and `docs/25`. Move the `pyproject.toml` / `uv.lock` byte-pin baselines and the dependency-gate expectations following the PHX-088 precedent, for exactly this dependency.
2. **The runtime publishes events.** An optional event sink on `PhoenixRuntime` (an `EventBus` or a narrow protocol), defaulting to none. Live task, step, agent, tool, permission and verification events, using only the existing 32 types, correlated with the task ID and the mission/request ID. Publishing never blocks or fails a mission; drops are counted at the bus or bridge, not in mission results. `runtime.py`, `tool_steps.py`, `server.py` and `missions.py` change with the two-commit pin pattern; `responders.py` stays byte-identical. MissionService publishes coarse mission events (queued, superseded, interrupted) as existing types.
3. **Slow clients are disconnected.** An authenticated `WS /events` on the PHX-101 helper. Each connection has a bounded buffer; when it fills, the client is closed with 1013 and reloads over REST. Publishing never blocks. No replay in v1.
4. **Payloads hold IDs, states, codes and timings only.** No message text, model output, tool output, file content, prompts, paths or the token. An allowlist schema enforces this, and tests prove that planted content never appears.

## Findings (main `4c649d7`)

- `EventBus.publish` is all-or-none: if any subscriber's queue is full it raises `EventBusFullError` for everyone. A slow client therefore must never own a bus queue that the publisher can fill; the bridge drains each subscription at once into a separate per-client buffer.
- `accept_websocket` already re-checks Host, Origin and the token, closes 1008 before accept and echoes only `phoenix.v1`; the middleware closes failures before routing.
- Under the launcher, uvicorn's own shutdown closes WebSocket connections (1012 with the websockets sans-I/O backend) before the lifespan exits.
- With `websockets` installed, uvicorn's `ws="auto"` picks `WebSocketsSansIOProtocol`; its `send` waits while the transport is paused, so a stalled client blocks only its own sender task.
- `tests/test_document_generation.py` (PHX-073) owned current manifest/lock exactness (the brief calls it the PHX-072 gate; PHX-073 had already turned PHX-072's gate into fixed history).
- `tests/test_retrieval_context.py` failed on main `4c649d7` itself: its `alembic/env.py` byte guard strips the PHX-102 registration from the current file only, but main now contains it too.

## Recommendations (R1–R16, for owner confirmation)

- **R1 — Placement.** The allowlist and publisher live in the new `phoenix/agents/mission_events.py`, shared by the runtime and the service; `phoenix/agents` stays free of API, persistence, tool and provider imports. The bridge is the new `phoenix/api/event_stream.py`; the route is in `server.py`.
- **R2 — Sink and drops.** `EventSink` is a protocol with `async publish(event)`, which `EventBus` satisfies; it must refuse rather than wait. `EventPublisher` swallows anything a sink or the allowlist raises (only cancellation propagates) and counts it: `PhoenixRuntime.dropped_events` and `MissionService.dropped_events`. Production wires the `EventStream`, which never raises and counts bus-full refusals in `EventStream.dropped_events`. Nothing reaches `MissionResult` or `MissionTrace`.
- **R3 — Event mapping.** As in ADR-087 and `docs/14`. 15 of the 32 types are used; none is added.
- **R4 — Correlation.** `correlation_id` is the mission (request) ID. `task_id` is the runtime's task ID (COMPLEX only; DIRECT has no task). `agent_id` is the internal agent ID (`phoenix`, `strategist`, specialists, `document_analyst` for tool events, `verifier` for LEVI).
- **R5 — Allowlist.** Each published type has a complete key list; each key has a kind: an ID (`[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`), a closed value set, integer milliseconds or a boolean. Envelope IDs must be ID-shaped too. Failures are `warning` severity, the rest `info`. A caller request ID that is not ID-shaped makes its events drop (counted).
- **R6 — Mission events.** Beyond queued, superseded and interrupted, MissionService publishes every status change it records: running, each settled outcome, cancelled, and failed with `permission_denied` or `internal_error`. All are `task.state_changed` with `scope="mission"`; a queued re-run carries `rerun_of`. Deletion, clearing and purges publish nothing.
- **R7 — Agent outcomes.** A step that stops for permission ends its agent with `agent.completed` and `outcome="waiting_permission"` (the agent did its part; it is not a failure). Cancellation, or a mission or planning deadline cutting a phase short, is `code="cancelled"`; the task's terminal transition carries the mission's own code (for example `timeout`). A step or verification deadline is `timeout`. An unknown defect is `internal_error` in the event and still propagates.
- **R8 — Tool order.** `tool.requested` once PHOENIX has built the call, `tool.started` as it enters ToolRegistry (the permission decision and the read happen in one bounded call), then exactly one of `tool.completed`, `tool.failed` or `permission.requested`. A proposal rejected before a call exists publishes only `tool.failed` (`tool_rejected`). No path, operation argument or output is published; the operation is a closed value.
- **R9 — Steps and verification.** `step.completed` only after LEVI's verification passes. A failed step shows through `agent.failed` or `verification.failed` and the task's `failed` transition. LEVI publishes `verification.started`, then `verification.passed` or `verification.failed` with the verdict and verification ID, or a code if verification itself raised.
- **R10 — Wire protocol.** `WS /events`; authentication through `accept_websocket` (1008 before accept). Frames are server-to-client JSON envelopes with exactly eight fields; client frames are ignored; there is no replay. If the services are not running, the socket is accepted and closed with 1013.
- **R11 — Buffers.** One bus subscription per connection, drained into a per-client buffer of `ServerConfig.event_client_buffer` events (default 256, 1–4096). A full buffer closes that client with 1013 and releases its subscription at once; other clients are unaffected. A close waits at most 5 s (a stalled transport is then dropped by the server).
- **R12 — Shutdown.** The lifespan exit closes remaining sockets with 1001 and refuses new ones. Under the launcher, uvicorn closes them with 1012 first; either way the client reconnects and reloads over REST. `launcher.py` is unchanged.
- **R13 — Defence in depth.** The bridge re-checks every event against the allowlist before sending and counts refusals in `rejected_events`.
- **R14 — No connection limit in v1.** Each connection costs one subscription and three small tasks; access is already loopback plus the per-launch token.
- **R15 — Dependency.** `websockets>=17.2,<18`, locked **17.2** (BSD-3-Clause, released 2026-10-03), with no transitive packages. It is a direct requirement, not the `uvicorn[standard]` extra. uvicorn's `auto` WebSocket selection picks its sans-I/O implementation.
- **R16 — Guards.** The PHX-073 gate becomes fixed history (main → `4a70867`) plus retention checks; a new PHX-103 gate in `tests/test_events_ws.py` owns current exactness against `4a70867` (byte-identical at the base). The workspace byte pins for `pyproject.toml` and `uv.lock` move to the PHX-103 implementation commit; the other files stay on `4a70867`. `tests/test_bootstrap.py`'s runtime-requirement list gains `websockets`. The `test_retrieval_context.py` `env.py` guard also strips the PHX-102 block from main's copy, fixing the failure found on main.

## Requirements

1. `phoenix/agents/mission_events.py` (new): `EventSink`, the payload allowlist, `mission_event`, `check_event`, `EventPublisher`, `MissionEvents`.
2. `phoenix/agents/runtime.py`: optional `events`, `dropped_events`, task/step/agent/verification events. `phoenix/agents/tool_steps.py`: tool and permission events.
3. `phoenix/api/missions.py`: optional `events`, mission status events, `dropped_events`.
4. `phoenix/api/event_stream.py` (new): `EventStream`. `phoenix/api/server.py`: the stream in `BackendServices`, the runtime and service wired to it, `WS /events`, closing on exit, `ServerConfig.event_client_buffer`.
5. `websockets` in `pyproject.toml` and `uv.lock`.
6. Records: ADR-087, ADR-088, this ticket, `FEATURE_TICKETS.md`, `docs/13`, `docs/14`, `docs/25`.

## Non-goals

New event types; replay or resumable streams; REST routes or answer text (PHX-104); the shell transport (PHX-106/107); model, recovery or voice events; a connection limit.

## Frozen-file handling

- `runtime.py` and `tool_steps.py` leave the PHX-047 pin, `server.py` leaves the PHX-102 pin and `missions.py` leaves `origin/main`. All four are pinned to the PHX-103 implementation commit (`PHX_103_COMMIT` / `PHX_103_PATHS`) in a separate pin commit; `prompts.py` stays on PHX-047 and `alembic/env.py` on PHX-102, so each path has exactly one baseline. The PR must merge with a merge commit.
- `responders.py` is byte-identical.
- The two `git diff origin/main` guards exclude `phoenix/api/event_stream.py` by name.
- `tests/test_api_server.py` now expects the `/events` route.

## Tests

- `tests/test_mission_events.py` (unit, integration, secret_leakage): the exact COMPLEX document-mission sequence and correlation; DIRECT events; `permission.requested` on WAITING_PERMISSION; verification passed and failed; tool rejected and failed; responder-output failure; cancellation; sentinels in the request, file name, file content and every model output absent from every event; a raising sink and a full bus never fail a mission; the allowlist refusing content, unknown fields and non-ID values; the vocabulary still 32; mirrored closed values; module neutrality; MissionService queued, running, settled, superseded with `rerun_of`, denied, cancelled, interrupted and restart-recovery events; the service surviving a raising sink.
- `tests/test_events_ws.py` (unit, integration, api_contract, permission_bypass, secret_leakage): bridge fan-out with a slow client closed 1013 while another keeps receiving, a never-blocking publish, off-allowlist events never sent, close with 1001; TestClient: 1008 before accept without a token, with a wrong token, a foreign or `null` Origin or a wrong Host, events received with a valid token and the token not echoed, subscriptions released on disconnect, 1001 on close, 1013 without services; a real uvicorn server through the launcher with the websockets client: 403 before upgrade without a token or with a foreign Origin, two clients receiving a mission's events, cleanup on disconnect, sockets closed on shutdown; and the exact dependency delta.

## Acceptance

- [x] Dependency, events, bridge and route implemented
- [x] Targeted tests, ruff, format and strict mypy pass
- [ ] Owner confirms R1–R16
- [ ] Full matrix on the final committed tree (see report)
