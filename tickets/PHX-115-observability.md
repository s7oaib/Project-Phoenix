# PHX-115 — Observability

Status: DONE (recommended options applied under the owner's instruction for PHX-115, 2026-10-21; the owner reviews R1–R10 in the PR, and merging confirms them)
Phase: 10 — Product Integration (milestone M3, capabilities)
Branch: `feature/PHX-115-observability` (local commits only; no push, no PR)
Base: `origin/main` `5722431` (PR #85)

## Objective

Make a running PHOENIX observable: structured, redacted JSON logs with correlation and mission/task/step IDs in rotating files, live runtime metrics behind an authenticated endpoint, and the existing Observability view showing that live data through the desktop proxy and the ObservabilityPort adapter.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md` (security 6), `/INVARIANTS.md` (INV-004, INV-011, INV-018, INV-021, INV-022), `/MASTER_SPEC.md`
- `docs/23_OBSERVABILITY.md`, `docs/13_API_CONTRACTS.md`, `docs/14_EVENT_SYSTEM.md`, `docs/21_FRONTEND.md`
- ADR-011, ADR-016 (no inferred TTFT), ADR-085 (local API security), ADR-087 (content-free events), ADR-091/092 (proxy, adapters, unchanged App)

## Owner instruction

"Recommended options; ADR numbers 145–149 only." Each R item below is applied under that instruction, pending the owner's review.

## Recommendations (R1–R10)

- **R1 — JSON log format (ADR-145).** One JSON object per line, closed keys: `ts`, `level`, `subsystem`, `message`, and the identifier-shaped correlation fields `correlation_id`, `mission_id`, `task_id`, `step_id`, `agent_id`, `event_type`. Other `extra` keys are dropped.
- **R2 — Exceptions without content.** `error_type` and `error_frames` (`file:line:function`) only; never the exception message, arguments or locals.
- **R3 — Redaction.** Pattern redaction (bearer, `key=value` secrets, private keys, known token prefixes) plus registered exact secrets (the per-launch API token); control characters removed; 1,000-character cap.
- **R4 — Rotating files.** `<data_dir>/logs/phoenix.jsonl`, 5 MiB x 5 backups, written through a queue; stderr gets the same JSON; the root logger is restored on exit.
- **R5 — Collector placement (ADR-146).** A `MetricsCollector` wraps the event stream as the publishers' sink; it re-checks the allowlist, aggregates, logs and forwards unchanged. No new event types, payload fields, migrations or `runtime.py`/`missions.py` changes.
- **R6 — TTFT basis.** No per-generation TTFT is inferred from non-streaming generations (ADR-011/016 stand). The live TTFT is mission time to first answer (queued to completed), labelled `ttft_basis: "mission_first_answer"`.
- **R7 — Resources without a dependency.** RAM and CPU via the standard library (`ctypes` on Windows, `/proc` elsewhere); VRAM from Ollama's `GET /api/ps` placement, bounded, only when Ollama is a configured provider, else `unavailable`. No `psutil`, so no dependency ADR.
- **R8 — Trace evidence lazily.** Tokens, durations, per-model usage and provider health come from stored traces of missions that ended, read when `/metrics` is requested (at most 64 per request, each mission once).
- **R9 — Proxy (ADR-147).** Exactly `GET /metrics`, no query, added to the Rust allowlist.
- **R10 — Adapter (ADR-148).** `core.ts` maps the snapshot into App's closed metric names (at most 40 entries; event metrics fill the rest of 64) and the recent event logs into the logs collection; refreshes on ready, reconnect, mission settle and every 15 s. App.tsx unchanged.

## Requirements and how they are met

| Requirement | Implementation |
| --- | --- |
| Structured JSON logs with correlation ID and mission/task ID | `phoenix/api/structured_logging.py`; every mission event logged by the collector with correlation/mission/task/step/agent IDs |
| No raw model/tool content or secrets | allowlisted events only; closed extras; no exception text; `redact()`; tests with sentinels |
| Rotating log files in the data dir | `configure_logging` from `launcher.main` |
| TTFT, latency per stage, tokens, RAM/VRAM, tool success rate, permission decisions, LEVI verdicts, recoveries | `phoenix/api/observability.py` |
| Authenticated metrics endpoint | `GET /metrics` (`phoenix/api/routes/observability.py`), PHX-101 middleware |
| Rust proxy allowlist + tests | `apps/desktop/src-tauri/src/proxy.rs` |
| ObservabilityPort adapter shows live data | `apps/desktop/src/adapters/core.ts` |

## Non-goals

- Streaming generation or token-level TTFT; disk I/O metrics; persisting metrics across launches; Prometheus/OpenTelemetry export; App.tsx copy changes (PHX-108).

## Files

New: `phoenix/api/structured_logging.py`, `phoenix/api/observability.py`, `phoenix/api/routes/observability.py`, `tests/test_observability.py`, this ticket.

Changed: `phoenix/api/server.py`, `phoenix/api/launcher.py`, `phoenix/api/routes/__init__.py`, `apps/desktop/src-tauri/src/proxy.rs`, `apps/desktop/src/adapters/core.ts`, `tests/desktop_adapters.mjs`, `tests/test_rest_api.py` (route table gains `/metrics`), `tests/test_prompt_injection_defenses.py` (PHX-115 pin), `tests/test_screenshot_vision_loop.py` and `tests/test_windows_ui_automation.py` (the three new modules excluded from the `origin/main` diff guards, as earlier tickets did), `tests/matrix.toml`, `docs/13_API_CONTRACTS.md`, `docs/21_FRONTEND.md`, `docs/23_OBSERVABILITY.md`, `DECISIONS.md` (ADR-145–148), `FEATURE_TICKETS.md`.

Hash pin: `server.py` (from the PHX-104 pin), `launcher.py`, `routes/__init__.py`, `proxy.rs` and `core.ts` are pinned at the PHX-115 implementation commit (`PHX_115_COMMIT`), landed as an implementation commit then a pin commit; the PR must merge with a merge commit.

## Verification

- `uv run pytest -q tests/test_observability.py` (redaction, log format, rotation, collector aggregation and forwarding, trace ingestion, CPU/RAM sampling, faked `/api/ps`, `GET /metrics` on the composition root with a scripted model, launcher logging).
- `npm test` (adapter mapping, bounds, refresh triggers, admission by the unchanged App readers).
- `cargo test` (proxy allowlist).
- Full matrix: `uv run python scripts/test_matrix.py`.

No Ollama, GPU or network is used by any test; `/api/ps` is faked with `httpx.MockTransport`.

## Acceptance criteria

- [x] JSON logs with closed keys, correlation and mission/task/step IDs, rotating files in the data dir.
- [x] Redaction tests: credential shapes, the registered token, extras, exception text.
- [x] Metrics: per-stage latency, TTFT (mission first answer), tokens and tokens/sec, RAM, VRAM, CPU, tool success rate, permission decisions, LEVI verdicts, recoveries.
- [x] `GET /metrics` requires the token and bound Host; response holds no prompt, answer or token.
- [x] Proxy admits exactly `GET /metrics`; tests updated.
- [x] Observability view receives live metrics and logs through the existing port.
- [x] Full matrix passes.

## Completion evidence

See the final report on the branch (commits, commands, results). Limitations: TTFT is mission-level, not token-level, until generation streams; VRAM is Ollama's report of its own models only; RAM/CPU are the backend process and the system, not Ollama's process; metrics reset at each launch; App's caption still says "Live telemetry is not connected" because App.tsx stays unchanged (ADR-092; copy update with PHX-108).
