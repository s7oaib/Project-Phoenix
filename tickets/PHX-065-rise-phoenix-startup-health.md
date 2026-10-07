# PHX-065 — Rise-Phoenix startup health sequence

Status: DONE
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


## Authoritative blocker resolution 2 — 2026-10-07

A resumed PHX-065 full-suite run exposed a second unchanged historical timing-sensitive test:

`tests/test_ollama_provider.py::test_stream_deadline_between_chunks_does_not_cancel_consumer`

Observed evidence:

- The authorized PHX-016 benchmark stabilization now passes.
- `tests/test_model_benchmark.py` passed 130/130 three consecutive runs.
- PHX-065 focused tests passed 509/509 three consecutive runs.
- All required historical suites passed individually.
- The full suite reached 14,397 passed and failed only this unchanged PHX-011 test.
- The Ollama test passed individually in the PHX-065 working tree and in a separate unchanged-main snapshot.
- The Ollama production and historical test files are unchanged from main.
- The failure occurred before the first streamed chunk arrived inside a 0.01-second real wall-clock operation timeout.
- PHX-011 requires deterministic mocked HTTP tests and does not require real-time scheduler latency to be part of this semantic check.
- The equivalent Colibri test, `test_stream_deadline_between_chunks_does_not_cancel_consumer`, already avoids this scheduler-sensitive pattern by using a long operation timeout and patching the event-loop monotonic clock forward only for the post-first-chunk deadline check.

This evidence identifies a deterministic-test design defect, not a PHX-065 or Ollama production defect. A separate full-suite failure on pristine main is not required before stabilizing this test because the test's semantic intent and deterministic sibling pattern are already explicit in repository authority.

PHX-065 is therefore explicitly authorized to make one additional narrow historical **test-only deterministic-clock stabilization** in `tests/test_ollama_provider.py`.

Required repair constraints:

1. Do not modify `phoenix/models/ollama.py`. Current evidence does not authorize an Ollama production change.
2. Modify only `test_stream_deadline_between_chunks_does_not_cancel_consumer` plus the minimal standard-library test import needed for deterministic clock patching.
3. Preserve the exact semantic assertion:
   - the first stream chunk is received successfully,
   - the total operation deadline can expire while the iterator is suspended between chunks,
   - the next iterator advance raises `ProviderUnavailableError`,
   - the consumer task itself is not left cancelled,
   - stream resources close and active operation IDs clear.
4. Replace the real scheduler-sensitive timeout/sleep mechanism with the same deterministic pattern already used by the Colibri equivalent:
   - import `patch` from `unittest.mock`,
   - construct `OllamaProvider(..., operation_timeout=10, ...)`,
   - receive the first chunk normally,
   - capture `loop = asyncio.get_running_loop()` and `clock = loop.time`,
   - only around the second `anext(iterator)`, patch `loop.time` to return `clock() + 20`,
   - require `ProviderUnavailableError`.
5. Remove the real `await asyncio.sleep(0.02)` from this test.
6. Do not add retries, skips, xfail, load detection, platform checks, or blanket flaky-test handling.
7. Do not weaken any cleanup/cancellation assertion.
8. Do not change `test_total_deadline_cleans_operations` or other Ollama timeout tests; they continue to test real bounded timeout behavior separately.
9. After repair, run the repaired exact test three consecutive times, then run the complete `tests/test_ollama_provider.py` suite three consecutive times and require all 108 cases to pass each run.
10. Then rerun the entire required PHX-065 verification, including the full Python suite three consecutive times. All three full-suite runs must pass; no gate is waived.
11. The final PHX-065 diff may now include exactly two authorized historical test files:
    - `tests/test_model_benchmark.py` — prior timing-constant stabilization only,
    - `tests/test_ollama_provider.py` — this deterministic event-loop-clock stabilization only.
12. Record both historical test repairs truthfully in PHX-065 completion evidence. Do not claim any PHX-016 or PHX-011 production behavior changed.

No additional historical test repair is authorized. If another unrelated historical failure appears, stop and report BLOCKED with exact evidence.

All other PHX-065 authority, scope, completion requirements, exact completion commit message, feature-branch-only push rule, and prohibition on PHX-070 remain unchanged.

## Completion evidence — 2026-10-07

Original drafts were based on 086461f268d2358b1acad317ec5af882537a228d. Final resume
preserved startup drafts and the authorized benchmark edit at a953907350fc19e94b8b4de1cbacb5b4a8eecca1.
Normal fetch --prune and FF-only synchronization brought the existing feature branch
to second authoritative blocker-resolution HEAD bb62864da04ca2279249cbcd2cf3f3d59a4f7fd5;
main 98198ee7d349e57999bc3bdbf2e2588b3fafbc61, 0 behind / 3 ahead, ticket-only diff.
All three preserved files had identical SHA-256 hashes before and after synchronization.
Read complete ticket and current architecture/security/voice/KAIDO/event authority.
No history rewrite, main modification/push, PR, merge or next-ticket work.

Preserved and freshly reviewed phoenix/voice/startup/__init__.py and
tests/test_rise_phoenix_startup.py without rewriting their correct implementation.
Only the two explicitly authorized historical test repairs changed earlier files.
ADR-062 records architecture, evidence,
failure/event/cancellation rules and caller-owned integration limitations.

### Contract and behavior evidence

509 deterministic synthetic cases cover the complete ticket matrix, including exact
three text constants/vocabularies/errors, frozen/extra-forbidden exact snapshots,
stored/subclass/nested/extra/foreign-enum forgeries, strict bounded IDs/languages,
exact Rise-Phoenix request/capability/detection correlation and detected-only truth.
False detection returns None with no startup effects. Constructor invokes no probe,
provider, event or I/O and binds no loop. Wrong-loop, closed and duplicate busy
paths are explicit. One caller task owns startup, without hidden tasks/workers.

All six mandatory caller probes execute sequentially in canonical order on each
accepted startup; no default-success or cached READY path exists. Every subsystem's
degraded/unavailable evidence forces DEGRADED and does not short-circuit remaining
checks. Exceptions safely become unavailable; malformed/wrong-ID/subsystem/status,
forged evidence and changed/failed identity access raise health_failure. Repeated
startup after explicit sleep reruns all checks and observes changed health.

Acknowledgement and final speech reuse PHX-063/064 boundaries, exact explicit voice/
language, one capability/synthesis/play each and completed correlation. Ack failure
skips final speech while checks continue. Final READY speech failure downgrades
result/text/event to degraded, activates KAIDO once and never retries a degraded
phrase. Already-degraded final failure stays degraded with one policy activation.
Healthy flow never activates KAIDO. Degraded selection exactly correlates startup,
failsafe agent, trigger, fixed reason and three capabilities, with no tool routing
or recovery evidence. Exhaustive result consistency enforces six checks, exact
texts/degradation/fail-safe evidence and listening; no raw audio/diagnostics retained.

Real PHX-064 activation occurs once with the exact session, owns the sole listening
event and exposes listening only through its API. Property/activation failures are
safe conversation_failure. Healthy events: started/listening/ready; degraded:
started/listening/degraded. Only safe metadata payloads; no startup voice-speaking
event. Started failure prevents checks/activation. Final publication failure produces
no result and best-effort sleep rollback. Real capacity rejection and rollback
failure tests preserve the correct error and observable manager state.

Cancellation and suppressed cancellation are tested at all 18 awaited points:
wake capability/detect, both TTS capability/synthesis/playback phases, all six probes,
activation, started/listening/final publication. No later startup stage or result
is permitted after cancellation. Nested activation may call its own publisher
before returning; the startup boundary still aborts and emits no false final result.
Close at each boundary prevents returned success and clears bookkeeping without
closing borrowed wake/probes/TTS/output/conversation/EventBus/KAIDO. No guessed or
unrelated provider/model/tool operation is cancelled. Already-entered collaborator
effects/events cannot be retracted; no native hard-stop claim is made.

### Commands and exact results

THREE consecutive uv run --locked pytest tests/test_rise_phoenix_startup.py -q:
509 passed each on the final implementation. No new warning/skip/xfail.
Initial development corrected a cancellation-count fixture expectation and moved
synthetic fixtures into this new test file to avoid a mypy module-name conflict.
No PHX-065 draft change was required on resume; no case/assertion was removed.

Previous BLOCKED reason: three required full-suite attempts each reached 14397
passed, one failure and 16 existing skips, exclusively the unchanged historical
test_slow_optional_probe_does_not_consume_generation_deadline[2]. The benchmark
suite passed independently. ChatGPT's authoritative 2026-10-07 ticket clarification
at a953907350fc19e94b8b4de1cbacb5b4a8eecca1 authorizes exactly two test constants:
time.sleep(0.025) -> time.sleep(1.0), per_trial_timeout_seconds=0.01 -> 0.5, inside
that function only. Delay 1.0 > deadline 0.5 preserves the telemetry-exclusion proof
while providing scheduler margin. All SUCCESS/digest/rate/call-count/cleanup/activity
assertions remain unchanged; the separate blocking-generation timeout test is
unchanged. No benchmark production change, retry, skip, xfail, flaky marker or
environment-conditioned behavior. Exact repaired test: 2 passed. Dedicated
benchmark suite THREE consecutive runs: 130 passed each, no skips/xfails/warnings.

Second BLOCKED reason: after benchmark repair, a full run reached 14397 passed,
one failure and 16 existing skips at the unchanged Ollama test
test_stream_deadline_between_chunks_does_not_cancel_consumer. Its 0.01-second
real-time operation deadline expired before the first chunk. The exact test passed
individually in the working tree and an unchanged-main snapshot; Ollama production
and test blobs matched main. No pristine-main full-suite reproduction was claimed.
ChatGPT's second authoritative ticket clarification at
bb62864da04ca2279249cbcd2cf3f3d59a4f7fd5 authorizes the existing Colibri sibling's
deterministic-clock pattern. Add only unittest.mock.patch import, change only this
test's operation_timeout 0.01 -> 10, receive first chunk normally, replace real
sleep(0.02) with loop=asyncio.get_running_loop(), clock=loop.time, and patch.object
(loop, 'time', lambda: clock()+20) only around the second advance expecting neutral
ProviderUnavailableError. Source closure, empty active IDs, current task existence
and not-cancelling assertions are unchanged. test_total_deadline_cleans_operations
and every other Ollama test remain unchanged. No PHX-011 production change.
Repaired exact Ollama test THREE consecutive runs: 1 passed each. Complete Ollama
suite THREE consecutive runs: 108 passed each, no skips/xfails/warnings. No retry,
skip, xfail, flaky marker, platform/load condition or weakened assertion.

All 40 prior suites were run individually with uv run --locked pytest <path> -q;
all established counts remain unchanged, with JUnit/logs outside the repository.

| Suite | Result |
| --- | --- |
| tests/test_voice_conversation.py | 950 passed |
| tests/test_tts_provider.py | 602 passed |
| tests/test_stt_provider.py | 414 passed |
| tests/test_vad.py | 403 passed |
| tests/test_wake_word_provider.py | 537 passed |
| tests/test_prompt_injection_defenses.py | 604 passed |
| tests/test_urahara_research_flow.py | 492 passed |
| tests/test_search_provider.py | 400 passed |
| tests/test_screenshot_vision_loop.py | 397 passed |
| tests/test_windows_ui_automation.py | 493 passed |
| tests/test_browser_tool.py | 653 passed |
| tests/test_secrets_boundary.py | 402 passed |
| tests/test_terminal_tool.py | 270 passed |
| tests/test_filesystem_tool.py | 649 passed; 16 existing skips |
| tests/test_audit_trail.py | 302 passed |
| tests/test_permission_engine.py | 926 passed |
| tests/test_tool_registry.py | 204 passed |
| tests/test_memory_schema.py | 244 passed |
| tests/test_pudding_memory_service.py | 167 passed |
| tests/test_document_ingestion.py | 300 passed |
| tests/test_vector_store_sqlite.py | 273 passed |
| tests/test_retrieval_context.py | 367 passed |
| tests/test_provenance_citations.py | 173 passed |
| tests/test_persistence.py | 12 passed |
| tests/test_agent_registry.py | 96 passed |
| tests/test_phoenix_orchestrator.py | 101 passed |
| tests/test_aizen_planner.py | 151 passed |
| tests/test_agent_model_router.py | 213 passed |
| tests/test_task_graph_state_machine.py | 348 passed |
| tests/test_structured_agent_handoffs.py | 214 passed |
| tests/test_levi_verification.py | 242 passed |
| tests/test_mahoraga_recovery.py | 790 passed |
| tests/test_kaido_failsafe.py | 574 passed |
| tests/test_model_provider.py | 61 passed |
| tests/test_ollama_provider.py | 108 passed |
| tests/test_model_registry.py | 83 passed |
| tests/test_model_lifecycle.py | 54 passed |
| tests/test_embeddings.py | 182 passed |
| tests/test_colibri_provider.py | 194 passed |
| tests/test_model_benchmark.py | 130 passed |

Aggregates: previous Phase 6 2906; Phase 5 3039; Phase 4 2753 / 16 existing skips;
Phase 3 1524 plus persistence 12; all nine Phase 2 suites 2729; all seven Phase 1
suites 812. All individual commands exited 0.

- uv lock; uv sync --locked: PASS; manifests unchanged.
- uv run --locked pytest: THREE consecutive complete runs, each 14398 passed,
  16 existing skips and one existing Starlette/httpx TestClient deprecation warning.
  Baseline 13889 plus 509 new cases. All three prior BLOCKED full-attempt logs/XML/
  results remain preserved in the original PHX-065-20261007 validation directory;
  The second blocked attempt and main snapshot evidence remain in
  PHX-065-resume-20261007. All three fresh successful runs in the final resume
  have separate full-1/full-2/full-3 XML and logs.
- uv run --locked ruff check .: PASS.
- uv run --locked ruff format --check .: PASS, 124 files formatted.
- uv run --locked mypy: PASS, 118 source files.
- uv build --out-dir <external-validation>/dist: sdist/wheel PASS; exact startup
  package source bytes verified, no startup.py.
- Foundation bootstrap/API/config/persistence/events/hardware: 126 passed,
  existing warning only; exact /health unchanged.
- npm test: 4 passed; desktop workspace typecheck/build: PASS; source unchanged.

All logs/JUnit/review scripts/reports/Python package artifacts stay outside the
project at C:/Users/shoai/.codex/validation/PHX-065-resume2-20261007. Ignored desktop build
output is not delivered. No recordings/models/PCM dumps/transcript dumps/DB/log/
cache/build/credentials or unrelated files enter the intended diff.

### Security, invariants and final scope

Independent normalized Git-blob review proves 141 of 143 prior protected production,
test, migration, desktop and dependency files unchanged, including PHX-060..064.
Only the two authorized historical tests differ: exact benchmark constants and
exact Ollama import/clock stabilization. Normalized byte comparison proves all
other syntax in both files unchanged; all prior production remains unchanged.
Sole Alembic head 0005_audit_events, five migrations unchanged, EventType 32 and
definitions unchanged. Secret-signature scan PASS. Guarded uncached import/
construction and cold interpreter/static import/call review rule out unauthorized
runtime integration and side effects. Real permission/decision/tool contracts
reject startup evidence; no readiness output grants task/verification/permission
authority. Six actual same-run check invocations and voice success are required
for READY; no score/model/alias/string/event can override degradation.

Reviewed INV-001..INV-024: preserved. PHOENIX supreme; P4/P5/audit unchanged;
no tool bypass/LLM permission, AIZEN/LEVI/MAHORAGA execution, automatic memory,
heavy runtime or dependency promotion. KAIDO stays explicit degraded policy only.
No capture/device/backend/network-specific voice runtime, task/model/tool execution,
memory/RAG/secrets/persistence/API/frontend/schema changes. Degradation observable.

All required implementation/regression/quality/security gates passed before DONE.
Complete staged line/whitespace/scope review and final startup/previous-voice staged
and both historical-test rechecks precede the exact completion commit and feature-only push; the execution
report records these results. PHX-065 DONE closes Phase 6 COMPLETE; PHX-070 TODO.

### Known limitations

No microphone/continuous wake or concrete wake/STT/TTS/output backend. Caller supplies
bounded wake PCM and six real probe implementations; their exact evidence is not
independently authenticated. Probes are sequential, with caller-owned outer/native
deadlines and collaborator lifetimes. No fixed PCM cache. Startup speech is not
managed by barge-in. No automatic task/model/tool/recovery execution; KAIDO is policy
evidence only. No API/frontend runtime wiring. Caller must not feed conversation
observations until handle_wake returns. Best-effort rollback can fail; actual state
remains observable and entered/delivered effects cannot be undone. PHX-070 NOT STARTED.

### Individual invariant review

| Invariant | Evidence |
| --- | --- |
| INV-001 | PASS: coordinator owner is phoenix; no independent agent execution. |
| INV-002 | PASS: no AIZEN invocation or permission/finalization delegation. |
| INV-003 | PASS: no model output; exact startup evidence rejects permission/tool contracts. |
| INV-004 | PASS: no privileged tool execution; existing registry and permissions unchanged. |
| INV-005 | PASS: P4 confirmation engine and tests unchanged; no startup approval field. |
| INV-006 | PASS: P5 blocking unchanged; no execution path added. |
| INV-007 | PASS: no P3/P4 action; audit implementation, tests and migration unchanged. |
| INV-008 | PASS: six executed same-run checks plus voice completion required for READY; malformed or failed delivery returns no success. |
| INV-009 | PASS: no MAHORAGA invocation; recovery budget unchanged; no startup retries. |
| INV-010 | PASS: KAIDO called exactly once on actual degraded health/voice evidence and never on healthy startup. |
| INV-011 | PASS: no frontend/API change or direct provider/tool integration. |
| INV-012 | PASS: only neutral wake/TTS/playback/probe interfaces; no concrete backend imports. |
| INV-013 | PASS: no tool implementation changes; probe and voice evidence cannot choose agent policy. |
| INV-014 | PASS: neutral phoenix/voice/failsafe IDs; UTA and KAIDO display names are not runtime authority. |
| INV-015 | PASS: text orchestration unchanged; voice failure degrades startup output without invoking orchestration. |
| INV-016 | PASS: TTS/playback errors yield explicit degraded startup; other boundary errors are safe and typed. |
| INV-017 | PASS: wake/health/provider evidence exactly revalidated; no trusted-content or executable field. |
| INV-018 | PASS: no memory/RAG/secrets imports, writes or private-content event payloads. |
| INV-019 | PASS: active startup observable, caller cancellation propagates, suppression and close block continuation, no hidden worker. |
| INV-020 | PASS: no persistence changes; five versioned migrations unchanged; head 0005_audit_events. |
| INV-021 | PASS: probe exceptions become explicit unavailable; malformed evidence errors; voice errors cannot produce READY. |
| INV-022 | PASS: explicit degraded result, voice failure stage, nonhealthy checks, KAIDO decision and phoenix.degraded event. |
| INV-023 | PASS: no expensive generation; lifecycle/benchmark/Ollama production unchanged. Separate blocking-generation and real Ollama deadline tests unchanged; both authorized historical repairs retain deadline and cleanup assertions. |
| INV-024 | PASS: no new runtime dependency or concrete research/voice engine; manifests unchanged. |
