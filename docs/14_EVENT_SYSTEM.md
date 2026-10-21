# 14 — Event System

## Purpose

Provide observable real-time state for orchestration, UI, logging, and evaluation.

## Core events

- phoenix.started
- phoenix.ready
- phoenix.degraded
- agent.started
- agent.completed
- agent.failed
- task.created
- task.state_changed
- step.started
- step.completed
- tool.requested
- tool.started
- tool.completed
- tool.failed
- permission.requested
- permission.approved
- permission.denied
- model.loading
- model.loaded
- model.unloaded
- provider.unavailable
- verification.started
- verification.passed
- verification.failed
- recovery.started
- recovery.completed
- recovery.exhausted
- memory.created
- document.indexed
- voice.listening
- voice.speaking
- voice.interrupted

## Envelope

event_id, event_type, timestamp, task_id optional, agent_id optional, payload, severity, correlation_id.

## Published events (PHX-103, ADR-087)

The runtime and the mission service publish 15 of the 32 types; none is added.
Every payload holds IDs, closed states and codes, booleans and integer
millisecond timings only, checked against an allowlist
(`phoenix/agents/mission_events.py`). Message text, prompts, model and tool
output, file content, paths and secrets never appear.

- `correlation_id`: the mission (request) ID. `task_id`: the runtime task
  (COMPLEX only). `agent_id`: the internal agent ID.
- `task.created`, then `task.state_changed` (`scope=task`) for every task
  transition, with the failure code on failure.
- `agent.started`, then `agent.completed` (`outcome` `completed` or
  `waiting_permission`) or `agent.failed` (`code`): PHOENIX direct, AIZEN
  planning, PHOENIX assignment and each step's producer.
- `step.started`; `step.completed` only after LEVI passes the step.
- A document step's call: `tool.requested`, `tool.started`, then
  `tool.completed`, `tool.failed` or `permission.requested`.
- LEVI: `verification.started`, then `verification.passed` or
  `verification.failed`.
- MissionService: `task.state_changed` with `scope=mission` for every recorded
  status (queued, running, completed, failed, cancelled, waiting_permission,
  superseded), with closed failure codes (including `interrupted` and
  `permission_denied`) and `rerun_of`.

Publishing never blocks or fails a mission; refusals are counted, not reported
in results. `WS /events` streams the events (`docs/13_API_CONTRACTS.md`).


## Voice events (PHX-114, ADR-108)

The voice service publishes `voice.listening`, `voice.speaking` and
`voice.interrupted`. The payload may hold:

- `state`: off, starting, sleeping, listening, thinking, speaking,
  degraded_voice, text_only or unavailable;
- `session_id`, `turn_id` and `mission_id`: UUIDs;
- `code`: `models_missing`, `dependency_unavailable`,
  `microphone_unavailable` or `voice_failure`.

The correlation ID is the voice session, and `agent_id` is `voice` (UTA).
`voice.speaking` marks playback starting, and `voice.interrupted` a barge-in
(state `listening`). `voice.listening` carries every other voice state change,
for example `thinking` with the request's `mission_id`, or `sleeping`. No
transcript, answer or audio is ever published. The vocabulary stays at 32
types.
