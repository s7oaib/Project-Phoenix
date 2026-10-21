# PHX-119 — KAIDO fail-safe in missions

Status: DONE (recommended defaults applied under the coordinator's PHX-119 brief; the owner reviews R1–R14 and approves the KAIDO prompt text in the PR, and merging confirms them)
Phase: 10 — Product Integration (milestone M3, capabilities)
Branch: `feature/PHX-119-kaido-failsafe` (local commits only; no push, no PR)
Base: `origin/main` `5722431` (PR #85)

## Objective

Give PHOENIX an observable degraded mode. When the model provider is unhealthy, memory runs low, or MAHORAGA's mission budget is exhausted, KAIDO answers with one short T0 reply. The reply is labelled degraded and is never verified. KAIDO releases with hysteresis when health returns. It never bypasses PermissionEngine or LEVI's rules.

## Authoritative documents

- `/AGENTS.md` (KAIDO is degraded/fail-safe mode only), `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md` (INV-008, INV-009, INV-010, INV-021, INV-022), `/MASTER_SPEC.md` (KAIDO context 8K)
- `docs/05_MODEL_STRATEGY.md` (T0 Qwen3.5 2B for KAIDO), `docs/22_RECOVERY_FAILSAFE.md`, `docs/13_API_CONTRACTS.md`
- PHX-028 (`KaidoFailSafe`, ADR-038), PHX-109 R7 (KAIDO deferred, ADR-093), PHX-100/103/104/105

## Recommendations (R1–R14)

Each item is applied under the coordinator's brief, pending the owner's review.

- **R1 — Placement.** A new module, `phoenix/agents/kaido.py`, holds `KaidoMonitor`, `KaidoPolicy`, `KaidoStatus`, `KaidoRecord`, `ResourceSample`, the T0 reply and the prompt text. `failsafe.py`, `recovery.py`, `responders.py` and `prompts.py` are byte-identical. Every engagement is a PHX-028 `KaidoFailSafe.activate()` decision.
- **R2 — One process-wide monitor.** The backend owns one `KaidoMonitor`, shared by `/ready` and every runtime. Its input is the provider statuses a caller just probed: `/ready`'s bounded `probe_provider`, and each mission's once-per-mission probe. This is the same signal PHX-108 uses for re-registration. Without a monitor (`PhoenixRuntime(kaido=None)`), nothing changes.
- **R3 — Triggers.**
  - `primary_model_provider_unavailable`: no provider is `HEALTHY` in that probe.
  - `resource_pressure`: memory is short (R11).
  - `recovery_exhausted`: MAHORAGA's mission budget is spent (R8).

  `deep_provider_unavailable` and `critical_subsystem_degradation` have no mission signal yet.
- **R4 — Hysteresis.** KAIDO engages on the first bad observation. It releases only after `release_after` (default 2) consecutive clear observations. Memory counts as clear only above the release threshold. A bad observation resets the count, and the trigger stays the engaging one.
- **R5 — The KAIDO reply.** A mission is exactly one generation:
  - T0 (`qwen3.5:2b`, new tier-table row `failsafe`) on the structured think:false provider;
  - at most 1,024 output tokens;
  - an 8K-token budget: `len(KAIDO_SYSTEM) + len(text) <= (8192 − 1024) × 3` characters. Every 16,000-character API request fits; a longer runtime request is `declined`.
  - No triage, plan, tools, PermissionEngine call, MAHORAGA retry or LEVI check.
- **R6 — Degraded provider.** T0 may run on a provider that reports `DEGRADED` (Ollama returns it for an invalid health response). T0 never runs on an `UNAVAILABLE` provider. With no usable provider, the mission fails `provider_unavailable`. With T0 not registered, it fails `no_eligible_model`. Either way the trace records `outcome=unavailable`.
- **R7 — Labelled and never verified.**
  - The answer text starts with `KAIDO_ANSWER_LABEL`, a blank line, then the reply.
  - `MissionTrace.kaido.verified` is literal `false`.
  - The mission status is `completed` (as a DIRECT answer is, ADR-081). The UI reads `kaido` to show degraded mode.
  - LEVI cannot be bypassed because no KAIDO answer claims verification.
- **R8 — KAIDO after exhaustion.**
  - **When.** Only when `MahoragaRecovery` itself returns `exhausted` for the mission. That `RecoveryResult` is the PHX-028 evidence.
  - **Never.** A zero budget, a retry that failed again, a LEVI rejection and an exhausted LEVI generation never reach KAIDO (INV-008).
  - **Task state.** The COMPLEX task ends `FAILED` with the original codes, then moves `FAILED → DEGRADED` if KAIDO answers.
  - **KAIDO fails too.** The original failure code stands, and the task stays `FAILED`.
- **R9 — Capabilities.** `basic_conversation`, `diagnostics` and `recovery_guidance`. Never `safe_limited_tool_routing`: KAIDO in missions has no tools, so it cannot reach ToolRegistry or PermissionEngine at all. `KaidoRecord` rejects tool routing.
- **R10 — Observability.**
  - **Events.** No new event types (32). The allowlist gains:
    - `phoenix.degraded`: `failsafe_id`, `kaido_scope`, `trigger`; severity warning;
    - `phoenix.ready`: `failsafe_id`, `kaido_scope`, for the process release;
    - the agent role `kaido`.
  - **Trace.** `MissionTrace.kaido` holds closed values and IDs only. Old traces load; no migration is needed.
  - **`GET /ready`.** Gains `kaido` (`KaidoStatus`). An engaged KAIDO makes `status` `degraded`, still HTTP 200.
  - **`GET /missions/{id}`.** Gains `kaido` (`KaidoOut`).
- **R11 — Memory evidence.** The backend samples available RAM with psutil (already a dependency; no subprocess).
  - **Engage:** below 2,048 MiB RAM, or below 512 MiB free VRAM when a caller's probe reports it.
  - **Clear:** at or above 3,072 MiB RAM and 1,024 MiB VRAM.
  - **Configuration:** `ServerConfig.kaido_policy`; `kaido_ram_probe=False` turns the sample off.
  - **A failing probe** is no evidence.
  - **VRAM:** not sampled by the backend. `nvidia-smi` per probe is costly, and while Ollama holds a model free VRAM is not pressure.
- **R12 — Bounded.** At most one KAIDO reply per mission, never retried (INV-009). Cancellation and the mission deadline still win (INV-019).
- **R13 — Pins.**
  - `runtime.py` and `mission_events.py` move from the PHX-109 pin to the PHX-119 implementation commit.
  - `server.py` moves from the PHX-104 pin, and `routes/missions.py` and `routes/schemas.py` move from the PHX-105 pin, to the same commit (`PHX_119_COMMIT` / `PHX_119_PATHS`), in a separate pin commit.
  - `kaido.py` is new, so it is protected once it is on `origin/main`.
  - The branch merges with a merge commit.
- **R14 — Existing tests.** Two exact-shape assertions change:
  - `test_ready_when_provider_and_database_are_healthy` gains the released `kaido` object;
  - `test_vocabulary_stays_32_and_mapping_uses_existing_types` lists `phoenix.degraded` and `phoenix.ready`.

## Owner approval needed

New prompt text (in `phoenix/agents/kaido.py`, `KAIDO_PROMPT_VERSION = "kaido-prompts-v1"`; `PROMPT_SET_VERSION` stays `phoenix-prompts-v4`):

`KAIDO_SYSTEM`:

```text
PHOENIX ROLE: kaido
You are KAIDO, PHOENIX's fail-safe assistant. PHOENIX is running in degraded
mode on a small local model, with no tools and no verification. Answer the
user's message briefly and plainly in one reply. If the request needs files,
tools, long reasoning or current facts, say that PHOENIX is in degraded mode
and suggest trying again when normal mode returns. Never claim to have run
tools, read files, verified anything or been granted any permission.
```

`KAIDO_ANSWER_LABEL` (first line of every KAIDO answer):

```text
[KAIDO degraded mode - unverified answer from the fail-safe model]
```

The fixed `FailSafeRequest.reason` strings (data, never shown as an answer):

- "No model provider reported healthy"
- "Free memory fell below the KAIDO threshold"
- "MAHORAGA's mission recovery budget is spent"

## Non-goals

These are out of scope:

- desktop UI changes (the API exposes the state; the UI work is a follow-up);
- tools in KAIDO mode;
- LEVI-verifying KAIDO answers;
- new event types, migrations or dependencies;
- setting Ollama's `num_ctx` per request (`ollama.py` is frozen);
- triggers for the deep provider or subsystem degradation;
- live model runs (GPU rule).

## Tests

New `tests/test_kaido_missions.py` (unit, integration, recovery, api_contract, secret_leakage). It uses scripted providers, health and memory, with no GPU or Ollama. It covers:

- **Monitor.**
  - Engaging on a provider that is down, degraded or absent.
  - Hysteresis: two clear observations release KAIDO, and a bad one resets the count.
  - The memory release band, VRAM pressure, and precedence.
  - A failing probe, and no probe at all.
  - Policy, status and record contracts.
- **Process KAIDO.**
  - On the triage, DIRECT and COMPLEX paths: one structured T0 reply, labelled, with the 1,024-token cap, no task, LEVI, tools or recovery, and exact events.
  - A degraded provider answers; an unavailable provider fails explicitly without a generation.
  - An unregistered T0 fails `no_eligible_model`; a request beyond 8K is declined.
  - A failed KAIDO reply is not retried.
  - The mission after release runs normally.
  - Without a monitor, nothing changes.
  - Cancellation wins.
- **Exhaustion KAIDO.**
  - Responder and provider exhaustion end `FAILED → DEGRADED` with a KAIDO answer, the codes on the `failed` event and the recovery ID correlated.
  - A zero budget, a LEVI rejection and an exhausted LEVI generation get no KAIDO answer.
  - When KAIDO fails, the original code is kept.
  - Without a monitor, exhaustion fails as before.
- **Contracts.**
  - Trace validators, and traces stored before PHX-119 load.
  - The allowlist accepts closed values and refuses text.
- **Backend.**
  - `/ready` engages and releases with hysteresis.
  - `/chat` under memory pressure returns a labelled KAIDO answer, and `GET /missions/{id}` exposes `kaido`.
- **Secrets.** A sentinel never reaches a trace or an event.

## Live smoke steps (main session, not run here: GPU rule)

1. Start Ollama with `qwen3.5:2b` and `qwen3.5:4b` pulled. Launch the backend, then check that `GET /ready` shows `kaido.engaged=false`.
2. **Memory pressure.** Restart with `ServerConfig(kaido_policy=KaidoPolicy(engage_free_ram_mib=999999, release_free_ram_mib=999999))`, or use a script that holds RAM until less than 2 GiB is free.
   - `GET /ready` shows `degraded`, `kaido.trigger=resource_pressure`.
   - `POST /chat {"text":"What is 2+2?"}` completes. `answer_text` starts with the label, `GET /missions/{id}` → `kaido.outcome=answered`, and `/events` shows `phoenix.degraded` then `agent.*` with agent `failsafe`.
3. **Release.** Free the memory (or restart with the defaults) and call `GET /ready` twice. The second call shows `kaido.engaged=false`, and `/events` shows `phoenix.ready`.
4. **Provider down.** Stop Ollama.
   - `GET /ready` shows `kaido.trigger=primary_model_provider_unavailable`.
   - A chat fails `provider_unavailable` with `kaido.outcome=unavailable`.
   - Restart Ollama and call `GET /ready` twice; KAIDO releases.
5. **Exhaustion.** Optional; needs a flaky model to exhaust three actions. Run FYP configuration 2. Any `recovery.exhausted` mission (not LEVI's) should complete with a labelled KAIDO answer and `task_states` ending `failed, degraded`.

## Open issues (for the owner)

- **R7 status.** A KAIDO answer's mission status is `completed` (labelled, `kaido.verified=false`), following the DIRECT and direct-fallback precedent. The owner may prefer a distinct mission status. That needs a migration and UI work.
- **R8.** After exhaustion, KAIDO answers instead of failing. The owner may prefer an explicit failure, with KAIDO used for process-wide engagement only.
- **R11.** The memory thresholds are not benchmarked.
- **Hysteresis.** It counts observations, not time. The desktop should poll `/ready` while degraded, so release does not wait for missions.

## Acceptance

- [x] Engage/disengage with hysteresis, the T0 reply, exhaustion KAIDO and observability implemented
- [x] Targeted and impacted tests, ruff, ruff format and strict mypy pass
- [x] Full matrix (see the evidence below)
- [ ] Owner reviews R1–R14 and approves the prompt text
- [ ] Live smoke steps (main session)

## Completion evidence

- Implementation commit `5a49797`; pin commit `da294a6` (`PHX_119_COMMIT`).
- `uv run pytest tests/test_kaido_missions.py`: 44 passed.
- Impacted suites (API, server, security, events, WS, mission events and service, MAHORAGA, runtime, triage, e2e, REST, FYP runners, prompt-injection pins): pass.
- `uv run python scripts/test_matrix.py` on `da294a6` (clean tree): PASS. Every gate passed: uv-sync, ruff check and format, mypy, mypy-scripts, pytest (16,419 tests, 0 failures, 0 errors, 30 skipped), npm-test, typecheck, frontend-build, tauri-info, cargo-check, cargo-test, uv-build. No test timed out under load.
- INV-008/009/010/021/022/023 reviewed: KAIDO is degraded only, never verified, bounded to one reply, explicit on failure, observable, and holds one lease.
- No new dependencies, event types or migrations. No tool or permission path is used in KAIDO mode.
