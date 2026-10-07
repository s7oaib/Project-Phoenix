# PHX-065 — Rise-Phoenix startup health sequence

Status: TODO
Phase: 6 — Voice

## Objective

Compose the completed Phoenix boundaries into the deterministic Rise-Phoenix startup flow:

Rise, Phoenix
-> exact wake evidence
-> phoenix.started
-> exact startup acknowledgement
-> real same-run health checks
-> READY or DEGRADED
-> KAIDO only when degraded
-> PHX-064 conversation activation
-> exact final startup response
-> phoenix.ready or phoenix.degraded

This ticket closes Phase 6. It does not redesign PHX-060 through PHX-064.

## Frozen authority

- Startup/readiness owner internal ID: phoenix.
- Voice owner internal ID: voice; UTA remains display metadata.
- Wake evidence is interface evidence only.
- PHOENIX remains sole supreme orchestrator.
- KAIDO is subordinate degraded-policy evidence only.
- PermissionEngine and ToolRegistry authority remain unchanged.
- No model, voice, wake, event, or fail-safe output grants permission.
- EventType remains exactly 32.

## Exact user-visible text

Startup acknowledgement:
Wake command acknowledged. Initializing Phoenix network. Checking all agents and core systems.

Healthy response:
All agents online. All core systems operational. What's the mission?

Degraded response:
Phoenix initialized in degraded mode. Some core systems are unavailable. Continuing with available capabilities.

Do not generate alternate wording.

## Production scope

Prefer:
- phoenix/voice/startup/__init__.py
- tests/test_rise_phoenix_startup.py

Use a package directory so the PHX-060 top-level phoenix/voice file-layout contract remains unchanged.

At most one ADR: ADR-062.

Expected dependency changes: NONE.

Do not add:
- microphone/device capture
- concrete wake/STT/TTS/output backend
- continuous wake worker
- hidden task/timer/thread
- model generation
- user-task routing
- AIZEN/LEVI/MAHORAGA execution
- tool execution or permission evaluation
- persistence/memory/RAG writes
- API/frontend/Tauri work
- schema/migration changes
- EventType changes
- fixed PCM phrase cache
- runtime dependencies

## Canonical startup vocabularies

StartupSubsystem exactly:
- persistence
- hardware
- model_provider
- tools
- voice
- agents

StartupCheckStatus exactly:
- healthy
- degraded
- unavailable

StartupOutcome exactly:
- ready
- degraded

StartupVoiceFailureStage exactly:
- tts
- playback

StartupErrorCode exactly:
- invalid_input
- invalid_response
- busy
- closed
- wrong_event_loop
- wake_failure
- health_failure
- tts_failure
- playback_failure
- conversation_failure
- event_failure
- failsafe_failure

Errors expose only the closed code/category. Do not leak collaborator diagnostics or audio.

## StartupCheckResult

Frozen, extra-forbidden fields exactly:
- startup_id
- subsystem
- status

No free-text detail or arbitrary metadata.

Health evidence grants no execution or permission authority.

## StartupHealthProbe

Create async provider-neutral StartupHealthProbe:
- read-only subsystem
- check(startup_id) -> StartupCheckResult

Constructor must receive exactly one probe for each canonical subsystem in this exact order:
1. persistence
2. hardware
3. model_provider
4. tools
5. voice
6. agents

No missing, extra, duplicate or reordered probes.

Construction runs no check.

For one accepted startup:
- run probes sequentially in canonical order,
- call each exactly once unless cancellation/event failure aborts earlier,
- exact-revalidate startup_id/subsystem/status,
- ordinary degraded/unavailable results do not short-circuit later probes,
- probe exception becomes that subsystem unavailable without exposing exception text,
- malformed/wrongly correlated evidence is health_failure/invalid_response, not healthy,
- CancelledError propagates,
- suppressed caller cancellation must not advance.

There is no default probe and no default healthy result. READY must reflect checks actually executed in this startup invocation.

## RisePhoenixStartupRequest

Frozen, extra-forbidden exactly:
- startup_id
- session_id
- wake_request
- tts_voice_profile_id
- tts_language_hint

wake_request must be an exact PHX-060 WakeWordRequest whose phrase is exactly:
- phrase_id: rise_phoenix
- text: Rise, Phoenix

No fuzzy/alternate phrase, health override, permission field, transcript, arbitrary startup text, or provider option.

## Wake integration

Implement handle_wake(request) -> RisePhoenixStartupResult | None.

For each call:
1. exact-revalidate request,
2. require PHX-064 manager sleeping and no other active startup,
3. call WakeWordProvider.capabilities once,
4. exact-revalidate provider correlation and exact default phrase support,
5. call WakeWordProvider.detect once,
6. exact-revalidate detection/request/provider correlation.

No retry or alternate provider.
Use detection.detected only; score never determines wake truth.

If detected is false:
- return None,
- no Phoenix event,
- no health probe,
- no TTS/output,
- no KAIDO,
- no conversation activation.

Wake failure/invalid evidence:
- safe wake_failure or invalid_response,
- no fabricated startup.

## RisePhoenixStartupCoordinator

Constructor collaborators:
- WakeWordProvider
- exact six StartupHealthProbe instances
- TtsProvider
- PHX-064 AudioOutput
- PHX-064 VoiceConversationManager
- EventBus
- KaidoFailSafe

Constructor:
- validates collaborators,
- performs no I/O,
- publishes no event,
- calls no provider/probe,
- starts no task/thread,
- binds no loop.

Public observability:
- owner_id == phoenix
- active_startup_id
- closed

First async use binds loop. Cross-loop async use -> wrong_event_loop.
Only one startup at a time. Concurrent startup -> busy.

## phoenix.started

After exact detected wake and before health checks, publish exactly one existing phoenix.started event:
- agent_id phoenix
- correlation_id startup_id
- payload only startup_id and session_id

No score/audio/provider/permission data.

Event failure:
- event_failure,
- no health,
- no activation,
- no startup result.

## Startup acknowledgement speech

After phoenix.started, attempt the exact acknowledgement text through existing PHX-063 TtsProvider and PHX-064 AudioOutput.

Requirements:
- exact TTS capabilities/provider correlation,
- exact requested voice profile/language,
- synthesize acknowledgement exactly once,
- exact TtsAudio correlation,
- AudioOutput.play exactly once,
- exact playback/output correlation,
- COMPLETED required,
- no alternate provider/retry.

Do not publish voice.speaking/listening for startup speech.

If TTS or playback fails:
- mark startup voice output degraded,
- do not expose diagnostics,
- skip later startup speech attempts,
- continue real health checks,
- final outcome cannot be READY.

## Health derivation

Run all six probes after the acknowledgement attempt.

Provisional READY requires:
- all six exact results healthy,
- acknowledgement speech completed,
- no cancellation/close/error.

Otherwise provisional DEGRADED.

No stale cached health may satisfy a new startup.

## KAIDO integration

PHX-065 is the later startup integration authorized by PHX-028.

For DEGRADED startup call KaidoFailSafe.activate exactly once with:
- trigger critical_subsystem_degradation
- fixed safe startup-degradation reason
- requested capabilities exactly, in order:
  1. basic_conversation
  2. diagnostics
  3. recovery_guidance
- no safe_limited_tool_routing
- no recovery_result

Exact-revalidate the decision.

Healthy READY flow must not call KAIDO.

KAIDO grants no tool/permission/task-success authority.

Invalid/failing fail-safe evidence -> failsafe_failure and no READY result.

## Conversation activation

After health derivation / degraded-policy selection:
- call PHX-064 VoiceConversationManager.activate(session_id) exactly once,
- use exact request session_id,
- no manager internal mutation,
- no duplicated voice.listening event,
- activation failure -> conversation_failure.

Caller must not feed PHX-064 listening observations until handle_wake returns.

## Final response speech

Current READY -> exact healthy text.
Current DEGRADED -> exact degraded text.

If acknowledgement output already failed, skip final speech.

Otherwise use the same validated TTS/output boundaries, no generated wording and no alternate provider.

If READY final TTS/playback fails:
- downgrade final outcome to DEGRADED,
- create KAIDO decision exactly once if not already created,
- response_text becomes exact degraded text,
- do not retry degraded speech.

If DEGRADED final speech fails:
- remain DEGRADED,
- keep exact degraded response_text,
- no retry.

## RisePhoenixStartupResult

Frozen, extra-forbidden exactly:
- startup_id
- session_id
- outcome
- checks
- response_text
- voice_failure_stage
- failsafe_decision
- final_voice_state

READY requires:
- six healthy checks,
- exact healthy response_text,
- no voice failure,
- no fail-safe decision,
- final_voice_state listening.

DEGRADED requires:
- at least one nonhealthy check OR voice failure,
- exact degraded response_text,
- exact KAIDO degraded decision,
- final_voice_state listening.

No wake/audio/provider raw evidence in result/repr.
Result grants no task/permission success.

## Final Phoenix event

After activation and final speech attempt publish exactly one:
- phoenix.ready for READY
- phoenix.degraded for DEGRADED

Event:
- agent_id phoenix
- correlation_id startup_id
- payload only safe structured startup metadata:
  - startup_id
  - session_id
  - outcome
  - unhealthy_subsystems
  - voice_failure_stage

No text/audio/score/provider diagnostics/permission data.
Never publish both final states.

If final event publication fails:
- return no StartupResult,
- best-effort conversation.sleep() to roll back the just-activated session,
- raise event_failure,
- do not fabricate delivery.

Required healthy event order:
phoenix.started -> voice.listening -> phoenix.ready

Required degraded event order:
phoenix.started -> voice.listening -> phoenix.degraded

Undetected wake publishes nothing.

## Cancellation and close

handle_wake runs in caller task. No hidden worker.

CancelledError propagates.
After every awaited collaborator boundary, suppressed caller cancellation/closed state must prevent later stages and success.

Do not cancel guessed/unrelated provider, model, tool, STT, TTS or orchestration operations.

aclose():
- same-loop,
- idempotent,
- marks coordinator closed,
- clears local active-startup bookkeeping,
- does not close borrowed WakeWordProvider, probes, TtsProvider, AudioOutput, VoiceConversationManager, EventBus or KaidoFailSafe,
- late continuation after close cannot finalize startup success.

Post-close handle_wake -> closed.

## Security / separation

Preserve INV-001 through INV-024.

Especially:
- PHOENIX supreme.
- Wake/model/voice/fail-safe output cannot grant permission.
- ToolRegistry/PermissionEngine/P4/P5/audit unchanged.
- KAIDO only on explicit degraded evidence.
- text mode remains available.
- voice failure degrades rather than fabricating READY.
- wake/health evidence is untrusted.
- no automatic memory write.
- caller-owned cancellable startup; no hidden task.
- no schema mutation.
- failures explicit.
- degraded fallback observable.
- no extra expensive generation.
- no new dependency.

No startup audio/wake audio logging, persistence, events or memory/RAG.

## Focused tests

Create tests/test_rise_phoenix_startup.py with deterministic fakes plus real memory-only EventBus.

Cover at minimum:

1. exact constants and all vocabularies
2. safe errors and copied/foreign enum rejection
3. exact/frozen StartupCheckResult
4. exact six probes/order/uniqueness
5. constructor side-effect free
6. probe call count/order/correlation
7. exception -> unavailable, malformed evidence rejected
8. exact request fields/default phrase
9. alternate/fuzzy phrase rejected
10. wake capabilities once and exact provider support
11. wake detect once and exact correlation
12. score ignored
13. undetected -> None with zero side effects
14. wake failure safe/no startup
15. owner phoenix / loop binding / wrong-loop
16. one active startup / duplicate busy
17. exactly one phoenix.started with safe payload
18. started-event failure prevents health/activation
19. exact acknowledgement TTS/output calls/correlation
20. acknowledgement TTS/playback failure degrades and skips later speech
21. all six health probes still run after voice-output failure
22. six healthy -> provisional READY
23. every subsystem degraded/unavailable -> DEGRADED
24. ordinary degraded does not short-circuit checks
25. no cached READY reuse
26. healthy never calls KAIDO
27. degraded KAIDO call exact trigger/capabilities/no recovery
28. KAIDO output grants no authority
29. fail-safe failure explicit
30. conversation activation exact once/session
31. active manager rejected
32. activation failure explicit
33. no duplicate voice.listening
34. exact healthy/degraded final text
35. final ready TTS/playback failure downgrades
36. downgrade creates KAIDO once
37. no retry/alternate provider
38. exact result consistency matrix
39. result privacy
40. ready/degraded final events and payload privacy
41. required event ordering
42. final event failure rolls back best-effort and returns no result
43. cancellation at wake/TTS/output/each probe/activation/final event
44. suppressed cancellation cannot advance
45. no hidden task/thread/timer
46. close idempotence and borrowed collaborator ownership
47. post-close rejection
48. no microphone/device/concrete provider/network backend
49. no PhoenixOrchestrator/AIZEN/LEVI/MAHORAGA execution
50. no ToolRegistry/PermissionEngine/Audit execution
51. no model generation/memory/RAG/secrets/persistence/API/frontend
52. pyproject/uv.lock unchanged
53. EventType exactly 32
54. Alembic head 0005_audit_events and migrations unchanged
55. PHX-060 through PHX-064 production/tests unchanged
56. import/construction side-effect isolation
57. repository secret-signature scan
58. INV-001 through INV-024 review

Add deterministic adversarial cases where useful.

## Required verification

Focused:
uv run --locked pytest tests/test_rise_phoenix_startup.py -q
Run THREE consecutive times.

Previous Phase 6 individually:
- tests/test_voice_conversation.py — 950
- tests/test_tts_provider.py — 602
- tests/test_stt_provider.py — 414
- tests/test_vad.py — 403
- tests/test_wake_word_provider.py — 537

Phase 5 individually:
- prompt injection 604
- URAHARA 492
- search 400
- vision 397
- Windows automation 493
- browser 653

Phase 4 individually:
- secrets 402
- terminal 270
- filesystem 649 / 16 existing skips
- audit 302
- permissions 926
- registry 204

Phase 3 individually:
- memory schema 244
- PUDDING 167
- ingestion 300
- vector store 273
- retrieval 367
- citations 173
- persistence 12

Phase 2:
run all nine established suites individually; baseline aggregate 2729.

Phase 1:
run all seven established suites individually; baseline aggregate 812.

Full quality:
- uv lock
- uv sync --locked
- uv run --locked pytest
- uv run --locked ruff check .
- uv run --locked ruff format --check .
- uv run --locked mypy
- uv build

Pre-PHX-065 full baseline:
- 13889 passed
- 16 existing skips
- one existing Starlette/httpx warning

Foundation:
- established suites 126 passed
- exact /health unchanged

Desktop:
- npm test — 4 passed
- npm run typecheck --workspace @phoenix/desktop
- npm run build --workspace @phoenix/desktop

Verify:
- pyproject.toml unchanged
- uv.lock unchanged
- EventType remains 32
- sole Alembic head 0005_audit_events
- migrations 0001–0005 unchanged
- no generated artifact in diff

## ADR-062

At most one ADR. Document:
- PHOENIX-owned startup triggered by UTA wake evidence
- exact Rise-Phoenix-only integration
- six real same-run startup probes and no default success
- deterministic READY/DEGRADED derivation
- exact three startup texts
- reuse of TTS/AudioOutput without concrete backend
- PHX-064 activation owns voice.listening
- KAIDO only for truthful startup degradation
- existing phoenix.started/ready/degraded events
- no hidden worker or authority expansion
- no dependency/schema/API/frontend changes

## Completion

Only after every deterministic gate passes:
- mark this ticket DONE
- mark PHX-065 DONE in FEATURE_TICKETS.md
- mark Phase 6 COMPLETE
- leave PHX-070 TODO
- commit exactly:
  feat: complete PHX-065 rise phoenix startup health
- push only feature/PHX-065-rise-phoenix-startup-health
- no PR
- no merge
- no main push
- do not start PHX-070

## Known limitations

Disclose at minimum:
- no microphone/continuous wake runtime
- no concrete wake/STT/TTS/output backend
- caller supplies bounded wake PCM windows
- caller supplies six real StartupHealthProbe implementations
- probe evidence is exact but not independently authenticated
- probes run sequentially
- no fixed PCM phrase cache
- startup speech is not barge-in managed
- no automatic model/tool/recovery execution
- KAIDO decision is policy evidence only
- no API/frontend runtime wiring yet
- caller owns collaborator lifetimes and outer deadlines

## Next

PHX-070 — Spotify integration.

Do not start it.


## Authoritative blocker resolution — 2026-10-07

A required PHX-065 full-suite run exposed one pre-existing timing-sensitive Phase-1 test:

`tests/test_model_benchmark.py::test_slow_optional_probe_does_not_consume_generation_deadline[2]`

Observed evidence:

- PHX-065 focused suite passed three consecutive runs with 509 cases.
- Every required historical suite passed individually.
- `tests/test_model_benchmark.py` passed all 130 cases independently.
- Three full-suite attempts reached 14,397 passed and failed only this unchanged case.
- The case currently gives lifecycle acquisition + fake generation only a 0.01-second wall-clock budget while deliberately using a 0.025-second synchronous telemetry sleep. Under whole-suite scheduler/load variance, the 10 ms non-telemetry budget can expire even though the optional probe is correctly excluded.

This is a deterministic-test defect, not authority to waive the full-suite gate.

PHX-065 is therefore explicitly authorized to make one narrow historical **test-only timing stabilization** in `tests/test_model_benchmark.py`.

Required repair constraints:

1. Do not modify `phoenix/models/benchmark.py` unless a new reproducible focused failure proves a production defect. Current evidence does not authorize a production change.
2. Preserve the exact semantic assertion: optional before/after resource-probe time must not consume the generation deadline.
3. Preserve the separate `test_generation_that_blocks_past_deadline_remains_timeout` semantics unchanged.
4. Change only the timing constants inside `test_slow_optional_probe_does_not_consume_generation_deadline` as needed to give ordinary lifecycle acquisition/fake generation a robust scheduler margin while keeping the artificial probe delay strictly greater than the trial timeout. Preferred stabilization:
   - `per_trial_timeout_seconds = 0.5`
   - artificial slow-probe sleep = `1.0` second
5. The probe delay must remain greater than the configured trial timeout so the test would still fail if optional telemetry were incorrectly charged against the generation deadline.
6. Do not add retries, conditional skips, xfail, environment detection, timing tolerance based on current machine load, or a blanket flaky-test exemption.
7. Do not weaken assertions about SUCCESS, output digest, success rate, probe call count, request count, lifecycle cleanup, or provider activity.
8. No other historical test may be edited unless another independently reproducible baseline defect is found and reported as BLOCKED rather than silently repaired.
9. After the timing repair, run `tests/test_model_benchmark.py` three consecutive times and require all 130 cases to pass each run.
10. Then rerun the complete required PHX-065 gate, including the full Python suite. Run the full Python suite three consecutive times; all three must pass with only the existing 16 skips and existing warning profile.
11. The final PHX-065 diff may therefore include exactly one additional historical file:
    `tests/test_model_benchmark.py`
    with only the narrowly authorized timing-constant stabilization.
12. Record this repair truthfully in PHX-065 completion evidence and ADR-062 only if ADR-062 is otherwise required. Do not rewrite PHX-016 semantics or claim a benchmark production change.

All other PHX-065 authority, scope, completion requirements, exact commit message, branch-only push rule, and prohibition on PHX-070 remain unchanged.
