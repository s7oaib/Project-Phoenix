# 23 — Observability

## Metrics

- task/step latency
- provider/model
- TTFT
- tokens/sec
- RAM/VRAM/CPU
- disk I/O where available
- tool success/failure
- verification result
- recovery attempts
- permission decisions

## Logs

Structured logs include timestamp, severity, subsystem, correlation ID, task/step IDs, safe message, and error type.

Never log secrets.

## Task trace

A trace should answer:
- what PHOENIX decided
- which agents ran
- which tools ran
- which permissions were requested
- what failed
- whether recovery happened
- what LEVI verified

## Live runtime observability (PHX-115, ADR-145 to ADR-148)

### Structured logs

`python -m phoenix.api` logs one JSON object per line to stderr and to
`<data_dir>/logs/phoenix.jsonl`, size-rotated at 5 MiB with 5 backups, written
through a queue so the event loop never waits on disk. Keys are closed: `ts`
(UTC), `level`, `subsystem` (logger), `message`, the correlation fields
`correlation_id`, `mission_id`, `task_id`, `step_id`, `agent_id`,
`event_type` (identifier-shaped values only; any other `extra` is dropped),
and for an exception `error_type` (class name) and `error_frames`
(`file:line:function`). The exception message, arguments and locals are never
written. Messages pass `redact()`: bearer values, `key=value` secrets (API
keys, tokens, passwords, cookies, authorization), private key blocks and
well-known token prefixes become `[REDACTED]`, and so does every registered
secret (the launcher registers the per-launch API token). Control characters
are removed and messages are capped at 1,000 characters.

Every content-free mission event (PHX-103 allowlist) becomes one
`phoenix.events` record: correlation, mission, task, step and agent IDs, the
event type and the allowlisted payload as `key=value` pairs. Failures log at
warning.

### Metrics (`GET /metrics`)

`MetricsCollector` sits between the publishers (runtime, mission service) and
the `WS /events` stream; it re-checks the allowlist, aggregates and logs each
event, then forwards it unchanged. It never blocks or fails a mission. The
authenticated `GET /metrics` route (`docs/13_API_CONTRACTS.md`) reports:

| Metric | Source |
| --- | --- |
| Task/step latency per stage | event `duration_ms` (agent roles, steps, tools, LEVI, MAHORAGA); mission queue wait and run time from status changes |
| TTFT | mission time to first answer (queued to completed). Generations do not stream, and non-streaming TTFT is unknown (ADR-011, ADR-016), so it is not inferred per generation |
| Provider/model, tokens, tokens/sec | stored `MissionTrace.generations` of missions that ended (read lazily, at most 64 per request); tokens/sec is output tokens over total generation latency (the PHX-091 rule) |
| Provider health | the `provider_health` the last ingested mission saw |
| RAM, CPU | process working set and system RAM via `ctypes` (Windows) or `/proc`; process CPU % from `time.process_time` between snapshots. No new dependency |
| VRAM | Ollama's own placement report, `GET /api/ps` (`size_vram` per loaded model), bounded by the probe timeout; `unavailable` when Ollama is not a configured provider or does not answer |
| Tool success/failure | `tool.completed`/`tool.failed` statuses |
| Permission decisions | `permission.requested`; user approval (mission `superseded`), user denial (`permission_denied`), policy denial (`tool.failed` with `decision` `denied`/`blocked`) |
| Verification result | LEVI `verification.passed`/`failed` and verdicts |
| Recovery attempts | MAHORAGA `recovery.*` actions and outcomes |

Disk I/O is not measured. Aggregates live in memory for one launch.

The desktop proxy admits exactly `GET /metrics` (no query). The adapter maps
the snapshot to the Observability view's metric and log entries (at most 40
backend metrics; live event metrics fill the rest of App's 64) and refreshes it
when the backend becomes ready, the event stream reconnects, a mission settles,
and every 15 s.
