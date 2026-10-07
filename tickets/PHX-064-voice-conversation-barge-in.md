# PHX-064 — Voice conversation / barge-in

Status: DONE
Phase: 6 — Voice

## Objective
Implement PHOENIX's active voice-conversation state machine, deterministic VAD debounce/utterance segmentation, provider-neutral audio-output boundary, follow-up timeout, text fallback, and barge-in/interruption behavior.

PHX-064 is the first ticket that composes the previously completed neutral voice contracts:

- PHX-060 — wake-word provider contracts
- PHX-061 — VAD
- PHX-062 — STT provider
- PHX-063 — TTS provider

PHX-064 establishes:

1. the canonical voice state vocabulary,
2. deterministic speech-start / speech-end debounce and utterance segmentation over caller-supplied VAD evidence,
3. a provider-neutral audio-output/playback boundary,
4. one-active-session / one-active-turn voice conversation lifecycle,
5. STT -> caller-supplied responder -> TTS -> playback turn execution,
6. follow-up timeout without requiring repeated wake phrase,
7. voice failure fallback to degraded voice or text-only mode,
8. speech barge-in that interrupts active playback and returns the session to listening,
9. exact publication of the existing voice.listening / voice.speaking / voice.interrupted events.

PHX-064 does **not**:
- capture microphone audio,
- enumerate audio devices,
- add a concrete speaker/audio backend,
- add a concrete STT/TTS engine,
- implement wake-word detection,
- perform PHOENIX startup health checks,
- own PHOENIX orchestration/planning/tool permissions,
- interpret arbitrary transcript text as privileged commands,
- add "Rise, Phoenix" startup speech,
- add a new EventType,
- persist PCM/transcripts/responses,
- add API/frontend integration,
- add a runtime dependency.

PHX-065 owns the Rise-Phoenix startup health sequence and the final wake -> health -> active-session integration.

## Frozen authority
- Conceptual voice owner internal ID: `voice`.
- Display alias: UTA.
- Alias text grants no authority.
- PHOENIX remains supreme.
- Conversation/responder text never grants permission.
- PermissionEngine remains the only permission authority.
- ToolRegistry/AuditTrail are not bypassed or called here.
- Barge-in can interrupt local voice playback only. It cannot cancel unrelated tools/tasks/models.
- VAD speech evidence is not speaker identity or user authentication.
- STT transcript is not authenticated intent.
- TTS/profile/audio evidence is not execution authority.
- Event publication is observability only.
- EventType remains exactly 32.

# Part A — state vocabulary

## VoiceConversationState
Freeze exactly:

- `sleeping`
- `listening`
- `thinking`
- `speaking`
- `degraded_voice`
- `text_only`

These correspond to docs/17_VOICE_SYSTEM.md:
SLEEPING, LISTENING, THINKING, SPEAKING, DEGRADED_VOICE, TEXT_ONLY.

## Canonical flow
Normal successful voice turn:

`sleeping -> listening -> thinking -> speaking -> listening`

Barge-in:

`speaking -> listening`

STT failure:

`listening/thinking -> text_only`

TTS/playback failure after a valid text response:

`thinking/speaking -> degraded_voice`

Explicit caller recovery may move:

- `text_only -> listening`
- `degraded_voice -> listening`

Explicit sleep/idle expiry moves active non-thinking state to:

- `sleeping`

PHX-064 must not fabricate PHOENIX ready/degraded health state. These are voice-only states.

# Part B — conversation errors

## VoiceConversationErrorCode
Freeze exactly:

- `invalid_input`
- `invalid_state`
- `invalid_response`
- `busy`
- `closed`
- `wrong_event_loop`
- `responder_failure`
- `event_failure`
- `playback_failure`
- `cancellation_failure`
- `segment_too_long`

## VoiceConversationError
Safe public code/category only.

Never include:
- PCM/base64,
- transcript text,
- response text,
- TTS text,
- model/backend diagnostics,
- audio-device names,
- credentials,
- environment data,
- stack traces from collaborators.

Provider-specific errors may be mapped to closed voice-state/fallback evidence, but raw exception strings must not leak.

# Part C — deterministic frame observation

## VoiceFrameObservation
Create frozen, extra-forbidden contract exactly:

- `frame`
- `decision`

Where:
- frame is exact PHX-060 `PcmAudioFrame`,
- decision is exact PHX-061 `VadDecision`.

Validation must reconstruct the exact corresponding `VadRequest` from:
- decision.operation_id,
- frame,
- decision.duration,

then call exact decision/request correlation against decision.provider_id.

Require:
- frame stream ID == decision stream ID,
- frame sequence == decision sequence,
- frame captured_at == decision captured_at,
- decision duration byte count exactly matches frame PCM.

Do not infer speech truth from score.
Use decision.activity only.

# Part D — segmenter configuration

## VoiceSegmenterConfig
Create frozen, extra-forbidden exactly:

- `speech_start_frames`
- `speech_end_frames`
- `max_segment_frames`

Freeze defaults:

- speech_start_frames = 2
- speech_end_frames = 10
- max_segment_frames = 256

Rules:
- strict integers
- bool rejected
- speech_start_frames: 1..8
- speech_end_frames: 1..50
- max_segment_frames: 1..256

No milliseconds are inferred beyond the individual VAD frame evidence.

No adaptive noise floor.
No ML semantics.
No speaker identity.

# Part E — VoiceSegment

## VoiceSegment
Create frozen, extra-forbidden exactly:

- `stream_id`
- `start_sequence`
- `end_sequence`
- `frames`

Rules:
- frames immutable tuple 1..256 exact PcmAudioFrame snapshots,
- all same stream,
- contiguous sequence +1,
- timestamps nondecreasing,
- first/last sequence must equal start/end,
- total decoded PCM <= 960000 bytes,
- no truncation/repair/reordering.

VoiceSegment is utterance audio evidence only.

# Part F — VoiceSegmenter

Implement a synchronous bounded `VoiceSegmenter`.

Public surface:
- read-only `config`
- read-only `active`
- read-only `buffered_frame_count`
- `feed(observation: VoiceFrameObservation) -> VoiceSegment | None`
- `reset() -> None`

No I/O.
No provider calls.
No threads/tasks.

## Start debounce
When idle:
- silence clears any pending speech-start buffer,
- consecutive speech frames are buffered,
- segment begins only when speech_start_frames consecutive speech observations are seen,
- all start-debounce speech frames are included in the segment.

Do not retain arbitrary leading silence.

## End hangover
When active:
- speech frames extend segment,
- silence frames enter a trailing-silence buffer,
- if speech resumes before speech_end_frames, buffered silence is included in the segment in exact order,
- if silence reaches speech_end_frames, finalize the segment,
- terminal end-hangover silence is EXCLUDED from the finalized segment.

No hidden truncation.

## Segment maximum
If accepting additional active/pending frames would exceed max_segment_frames:
- reset segmenter to idle,
- raise `segment_too_long`.

Do not silently truncate or synthesize a segment.

## Stream continuity
While start/active buffering:
- stream change -> invalid_input and reset.

# Part G — responder boundary

PHX-064 must not couple directly to PhoenixOrchestrator routing/planning.

Define neutral caller-owned responder contracts.

## VoiceResponseRequest
Frozen, extra-forbidden exactly:
- `session_id`
- `turn_id`
- `transcript_text`

transcript_text:
- exact Unicode
- meaningful/nonblank
- <=20000
- repr=False
- preserve formatting
- NUL rejected.

## VoiceResponseDraft
Frozen, extra-forbidden exactly:
- `session_id`
- `turn_id`
- `response_text`

response_text:
- exact meaningful Unicode
- <=4096
- repr=False
- preserve formatting
- NUL rejected.

## VoiceResponder
Provider-neutral/caller-owned async ABC or Protocol:

- read-only `responder_id`
- `async respond(request: VoiceResponseRequest) -> VoiceResponseDraft`

Construction performs no I/O.

PHX-064 validates exact response correlation.

The responder:
- may later wrap PhoenixOrchestrator,
- cannot grant permission through response text,
- does not receive PCM,
- does not receive permission objects,
- is not automatically retried/fallback-routed.

No production concrete responder.

# Part H — audio output boundary

## AudioPlaybackStatus
Freeze exactly:

- `completed`
- `interrupted`

## AudioOutputErrorCode
Freeze exactly:

- `invalid_input`
- `invalid_response`
- `unavailable`
- `busy`
- `closed`
- `wrong_event_loop`
- `cleanup_failure`

Public errors safe category only.

## AudioPlaybackRequest
Frozen, extra-forbidden exactly:

- `playback_id`
- `session_id`
- `turn_id`
- `audio`

audio:
- exact PHX-063 TtsAudio snapshot.

No:
- device ID,
- volume,
- output path,
- callback,
- permission,
- tool,
- API key.

## AudioPlaybackResult
Frozen, extra-forbidden exactly:

- `playback_id`
- `output_id`
- `status`

Exact correlation helper equivalent to:
`result.validate_request(request, output_id)`.

## AudioOutput
Async ABC:

- read-only `output_id`
- read-only `active_playback_ids`
- `async play(request: AudioPlaybackRequest) -> AudioPlaybackResult`
- `async interrupt(playback_id: str) -> CancellationResult`
- `async aclose() -> None`

Reuse neutral:
- CancellationResult
- CancellationStatus

No concrete audio output backend in production.

Tests use fakes only.

# Part I — conversation configuration

## VoiceConversationConfig
Create frozen, extra-forbidden exactly:

- `follow_up_timeout_seconds`
- `barge_in_speech_frames`

Defaults:

- follow_up_timeout_seconds = 30.0
- barge_in_speech_frames = 2

Rules:
- follow_up_timeout_seconds ordinary float only, finite, 1.0..600.0
- bool/int rejected
- barge_in_speech_frames strict int, bool rejected, 1..8

Timeout uses an injected monotonic clock.
No wall-clock timer.
No background task.

# Part J — turn request/result

## VoiceTurnRequest
Frozen, extra-forbidden exactly:

- `session_id`
- `turn_id`
- `segment`
- `stt_language_hint`
- `tts_voice_profile_id`
- `tts_language_hint`

Rules:
- exact IDs
- exact VoiceSegment
- language tokens follow PHX-062/063 neutral shape
- TTS profile ID strict
- no tool/permission/path/model/device fields.

## VoiceTurnOutcome
Freeze exactly:

- `spoken`
- `interrupted`
- `text_fallback`

## VoiceFailureStage
Freeze exactly:

- `stt`
- `tts`
- `playback`
- `voice_output_unavailable`

## VoiceTurnResult
Frozen, extra-forbidden exactly:

- `session_id`
- `turn_id`
- `outcome`
- `transcript_text`
- `response_text`
- `failure_stage`
- `final_state`

Rules:
- text fields optional and repr=False,
- spoken requires transcript+response, no failure_stage, final_state listening,
- interrupted requires transcript+response, no failure_stage, and final_state:
  - listening for accepted barge-in,
  - sleeping for accepted explicit sleep during active playback,
- text_fallback:
  - STT failure: transcript/response both None, failure_stage stt, final_state text_only,
  - TTS/playback failure: transcript+response present, failure_stage tts/playback, final_state degraded_voice,
  - degraded_voice input-only turn: transcript+response present, failure_stage voice_output_unavailable, final_state degraded_voice.

No raw provider error is retained.

# Part K — VoiceConversationManager

Implement async `VoiceConversationManager`.

Constructor collaborators:
- SttProvider
- TtsProvider
- VoiceResponder
- AudioOutput
- EventBus
- optional VoiceSegmenterConfig
- optional VoiceConversationConfig
- injected monotonic clock callable

Constructor performs no I/O and starts no task.

## Public properties
At minimum:
- owner_id == `voice`
- state
- session_id
- active_turn_id
- active_playback_id
- follow_up_deadline
- closed

Observability only.
No task handles.

## Loop ownership
No loop binding at construction.

First async manager method binds the current event loop.

Any later async use from another event loop -> wrong_event_loop.

# Part L — session activation

## activate(session_id)
Caller-authorized only.

Allowed from:
- sleeping
- text_only
- degraded_voice

Requirements:
- no active turn,
- exact session ID,
- reset segmenter and barge-in counters,
- set state listening,
- create/refresh follow-up deadline = clock + timeout,
- publish exactly one `voice.listening`.

Activation does NOT:
- call WakeWordProvider,
- prove "Rise, Phoenix",
- run PHOENIX health checks,
- speak startup text.

PHX-065 will own wake/startup integration.

Calling activate while an active listening/thinking/speaking session already exists -> busy/invalid_state, no silent session replacement.

# Part M — follow-up timeout

No hidden timer task.

## expire_if_idle()
Caller-driven deterministic check.

If:
- state == listening
- no active turn
- clock >= deadline

then:
- state -> sleeping
- session cleared
- segmenter reset
- deadline cleared
- return True.

Otherwise return False.

No timeout while thinking/speaking/degraded/text_only.

After each:
- successful spoken turn,
- barge-in interruption,
- degraded text-fallback turn,

refresh deadline for the active session.

This allows follow-ups without repeated wake phrase until timeout.

# Part N — listening segmentation

## observe_listening(observation)
Allowed only in state:
- listening
- degraded_voice

In degraded_voice, voice input/STT remains usable, but no new TTS/playback attempt is made until explicit activate/recovery.

Rules:
- check idle timeout first,
- exact-revalidate observation,
- feed manager-owned segmenter,
- return VoiceSegment | None,
- no STT call inside observe_listening.

No wake phrase required for follow-up observations inside active session.

# Part O — process_turn

## process_turn(request)
One active turn at a time.

Require:
- manager not closed,
- request session matches active session,
- state listening or degraded_voice,
- no active turn,
- request.segment exact,
- segmenter not carrying another active utterance,
- timeout not expired.

Save current caller task as active-turn ownership.
Nested/duplicate turn ID -> busy.

### Step 1 — thinking
Set state thinking.

No EventType exists for thinking; emit no fake event.

### Step 2 — STT
Build exact SttRequest:

- operation_id = turn_id
- frames = request.segment.frames
- language_hint = request.stt_language_hint

Call:
- stt_provider.capabilities()
- exact revalidate/correlate capabilities
- stt_provider.transcribe()
- exact revalidate SttTranscript
- validate transcript against request/provider.

No retry/fallback provider.

If ANY SttProviderError or invalid STT evidence:
- clear active turn safely,
- state -> text_only,
- clear follow-up deadline,
- return VoiceTurnResult(
  outcome=text_fallback,
  failure_stage=stt,
  transcript_text=None,
  response_text=None,
  final_state=text_only
  ).

Do not leak error details.

### Step 3 — responder
Transcript text is exact user-input evidence.

Create VoiceResponseRequest with:
- session_id
- turn_id
- transcript_text

Call responder exactly once.

Require exact VoiceResponseDraft and exact IDs.

Responder exception/invalid evidence:
- restore state listening,
- refresh deadline,
- raise responder_failure safe error.

Do NOT map arbitrary responder failure to voice degradation.
Do NOT synthesize fake success.

### Step 4A — degraded_voice input mode
If the turn started while manager was degraded_voice:
- do not call TTS,
- do not call AudioOutput,
- remain degraded_voice,
- refresh deadline,
- return text_fallback with transcript+response,
- use the canonical VoiceFailureStage value `voice_output_unavailable`.

Allow text_fallback in degraded_voice with:
- transcript+response present,
- failure_stage=voice_output_unavailable,
- final_state=degraded_voice.

This stage is manager-state evidence only; no new provider truth.

### Step 4B — TTS
For a normal listening-started turn:
- get TtsCapabilities,
- exact validate provider correlation,
- construct TtsRequest:
  - operation_id = turn_id
  - text = response_text
  - voice_profile_id = request.tts_voice_profile_id
  - language_hint = request.tts_language_hint
- validate request against capabilities,
- call synthesize exactly once,
- exact-revalidate TtsAudio,
- validate audio against request/capabilities.

Any TtsProviderError/invalid evidence:
- state -> degraded_voice,
- refresh deadline,
- return text_fallback with transcript+response, failure_stage=tts.

No automatic alternate voice/provider.

### Step 5 — speaking
Create AudioPlaybackRequest:
- playback_id = turn_id
- session_id
- turn_id
- exact TtsAudio

Set active_playback_id.

Set state speaking.

Publish exactly one `voice.speaking`.

Event payload must contain only safe IDs/state metadata.
No transcript, response text, PCM/base64/hash.

### Step 6 — playback
Call AudioOutput.play exactly once.

Exact-revalidate playback result and correlation.

Normal completed result:
- state -> listening
- refresh deadline
- publish exactly one voice.listening
- return outcome=spoken.

Interrupted result:
- may occur only if barge-in/sleep requested for this active playback,
- state -> listening unless explicit sleep requested,
- refresh deadline when listening,
- return outcome=interrupted.

AudioOutput error/invalid result:
- state -> degraded_voice,
- clear active playback,
- refresh deadline,
- return text_fallback with transcript+response, failure_stage=playback.

# Part P — barge-in

## observe_barge_in(observation)
Allowed only while:
- state == speaking,
- active playback exists,
- active turn exists.

Exact-revalidate VoiceFrameObservation.

Use only decision.activity.

Do not use score as threshold.

Rules:
- silence resets consecutive barge-in speech counter,
- consecutive speech increments counter,
- trigger only at config.barge_in_speech_frames,
- after triggering once for a playback, additional observations do not trigger another interrupt.

At trigger:
1. call AudioOutput.interrupt(active_playback_id) exactly once,
2. exact-revalidate CancellationResult,
3. provider_id must equal audio_output.output_id,
4. operation_id must equal active_playback_id.

Accepted:
- set barge-in requested flag,
- publish exactly one `voice.interrupted`,
- return True.

NOT_FOUND / UNSUPPORTED:
- raise cancellation_failure,
- do not claim interruption,
- do not publish voice.interrupted.

The existing playback coroutine is responsible for returning an exact interrupted AudioPlaybackResult after accepted interruption.

If collaborator suppresses interruption and returns completed:
- manager MUST NOT report spoken success if barge-in was accepted.
- accepted barge-in owns final turn outcome interrupted.
- this is the equivalent of suppressed-cancellation safety.

Barge-in affects only voice playback.

Do NOT call:
- PhoenixOrchestrator.cancel
- model provider cancel
- tool cancel
- STT cancel
- TTS cancel after synthesis is complete.

# Part Q — explicit sleep

## sleep()
Caller-authorized operation.

PHX-064 does not interpret transcript text itself as the sleep command.

Allowed:
- listening
- degraded_voice
- text_only
- speaking

Listening/degraded/text-only:
- state -> sleeping,
- clear session/deadline,
- reset segmenter/barge-in state.

Speaking:
- call AudioOutput.interrupt once,
- require exact ACCEPTED acknowledgement,
- set explicit sleep_requested,
- publish voice.interrupted once,
- playback completion path must end in sleeping,
- no voice.listening after explicit sleep.

Thinking:
- return busy/invalid_state.
- PHX-064 does not cancel arbitrary responder/orchestration work.

No hidden parsing of "Phoenix, sleep".
A future deterministic interaction layer may invoke sleep explicitly after interpreting user intent.

# Part R — text fallback and recovery

## STT failure
-> text_only.

No voice input accepted until explicit activate/recovery.

## TTS/playback failure
-> degraded_voice.

Transcript+response remain available as text.

In degraded_voice:
- observe_listening and STT/responder are allowed,
- TTS/playback is skipped,
- returned response is text-only,
- state remains degraded_voice.

## activate()
Explicit caller recovery may re-enable listening and TTS attempt.

No automatic retry/provider switching.

# Part S — events

PHX-064 may use ONLY existing:

- voice.listening
- voice.speaking
- voice.interrupted

No new EventType.

Every event:
- agent_id = `voice`,
- correlation_id = active session ID or deterministic session/turn correlation,
- payload safe IDs/state only,
- no transcript,
- no response text,
- no PCM/base64/hash,
- no permission data.

Event publication grants no authority.

EventBus publication failure:
- map to event_failure,
- never fabricate successful event delivery.

Do not publish:
- phoenix.ready
- phoenix.degraded
- task/tool/permission/model events.

PHX-065 owns startup-health events/flow.

# Part T — event failure semantics

A voice state transition must never be silently reported as event-observable when EventBus.publish failed.

Prefer deterministic helper:
- construct event,
- publish,
- then expose/commit the externally visible transition where practical.

If exact rollback is unsafe, fail explicitly and document actual state in the safe error path.

Tests must cover:
- EventBus full/failure,
- no duplicate events,
- exact order of listening/speaking/interrupted/listening on normal/barge-in paths.

# Part U — cancellation / suppressed-cancellation safety

No hidden manager worker task.

process_turn runs in caller task.

Manager tracks caller task only for observation/safety; do not expose task handles publicly.

If process_turn caller task is cancelled:
- propagate asyncio.CancelledError,
- clear active turn/playback bookkeeping in finally,
- do not fabricate VoiceTurnResult.

If STT/TTS/responder/output collaborator suppresses caller cancellation:
- after every awaited collaborator boundary check task.cancelling() / internal stop flags,
- do not advance to later stages or finalize success.

Do not cancel unrelated provider operations by guessed IDs.

# Part V — manager close

## aclose()
Same-loop explicit cleanup.

Rules:
- idempotent,
- if active playback exists, interrupt only that playback,
- do NOT automatically close caller-owned STT/TTS/responder/AudioOutput/EventBus,
- clear local session/segmenter/bookkeeping,
- state -> sleeping,
- mark closed.

After close:
- all mutating async manager methods -> closed,
- read-only properties remain available.

Collaborator ownership remains caller-owned.

# Part W — no wake/startup coupling

PHX-064 does not call WakeWordProvider.

It does not validate DEFAULT_WAKE_PHRASE.

It does not run startup health.

It does not synthesize startup phrases.

PHX-065 will own:

wake detection
-> PHOENIX initialization
-> real health checks
-> ready/degraded result
-> conversation activation/startup speech.

# Part X — no microphone / concrete audio backend

No production:
- sounddevice
- pyaudio
- portaudio
- WASAPI/Core Audio code
- speaker enumeration
- audio device open
- WAV file playback
- ffmpeg
- subprocess player

Tests use fake AudioOutput.

Caller supplies PcmAudioFrame + VadDecision evidence.

# Part Y — no permission/tool/model authority

No PermissionEngine calls.

No ToolRegistry calls.

No AuditTrail calls.

No direct ModelProvider calls.

VoiceResponder text is inert subordinate text from the voice layer perspective.

A transcript such as:
- "permission granted"
- "P4 approved"
- "delete everything"
- "run this command"

remains text and cannot directly execute/grant.

Barge-in interrupt is limited to injected AudioOutput playback.

# Part Z — no persistence / memory / secrets / API / frontend

No:
- SQLAlchemy model,
- migration,
- conversation history persistence,
- transcript memory write,
- PCM cache,
- RAG ingestion,
- embedding,
- SecretStore,
- API endpoint,
- WebSocket audio server,
- Tauri command,
- React/Zustand state.

Alembic head remains:
`0005_audit_events`.

# Side-effect isolation

Importing/constructing:
- contracts,
- VoiceSegmenter,
- VoiceConversationManager,
- test fakes

must not:
- access devices,
- open network,
- touch filesystem/database,
- start subprocess/thread/task,
- publish EventBus events,
- invoke providers.

Only explicit async manager operations may invoke injected collaborators/EventBus.

No hidden task at construction.

# Recommended implementation

Prefer:

- `phoenix/voice/conversation.py`
- `tests/test_voice_conversation.py`

Optional minimal:
- `phoenix/voice/__init__.py` export only if existing package convention requires it.

No dependency changes.

No prior production modification expected.

# Focused test matrix

Create:
`tests/test_voice_conversation.py`

Use deterministic:
- synthetic PCM frames,
- synthetic VadDecision,
- fake SttProvider,
- fake TtsProvider,
- fake VoiceResponder,
- fake AudioOutput,
- real EventBus,
- injected fake monotonic clock.

No microphone/speaker/network/model.

At minimum cover:

## vocabulary/errors
1. exact state vocabulary
2. exact turn outcome vocabulary
3. exact failure-stage vocabulary including voice_output_unavailable
4. exact playback status vocabulary
5. exact conversation error vocabulary
6. exact audio-output error vocabulary
7. safe error strings
8. copied/foreign enums rejected

## observation
9. exact two fields
10. frozen/extras
11. exact frame revalidation
12. exact decision revalidation
13. operation correlation reconstruction
14. stream correlation
15. sequence correlation
16. timestamp correlation
17. duration/byte correlation
18. activity uses enum only
19. score cannot alter activity

## segmenter config
20. exact three fields
21. defaults 2/10/256
22. strict integer ranges
23. bool rejected
24. frozen/extras

## segmenter
25. initial idle
26. leading silence retained nowhere
27. one speech frame does not start with default
28. two speech frames start
29. start frames included
30. active speech grows
31. short silence buffered
32. resumed speech flushes short silence in order
33. ten terminal silences finalize
34. terminal hangover silence excluded
35. returned segment exact stream
36. exact contiguous sequences
37. nondecreasing timestamps
38. stream change resets/fails
39. invalid observation resets/fails safely
40. max 256 accepted
41. overflow raises segment_too_long
42. overflow resets
43. reset explicit
44. bounded buffers
45. no provider calls
46. no I/O/tasks

## segment contract
47. exact four fields
48. frozen/extras
49. empty rejected
50. >256 rejected
51. stream mismatch rejected
52. sequence gap/reorder rejected
53. timestamp regression rejected
54. start/end mismatch rejected
55. <=960000 bytes
56. >960000 rejected

## responder contracts
57. request exact three fields
58. draft exact three fields
59. frozen/extras
60. strict IDs
61. transcript privacy/exact formatting
62. response privacy/exact formatting
63. blank/NUL/bounds rejected
64. responder ID strict/read-only
65. abstract/protocol substitutability
66. constructor no I/O
67. no PCM in request
68. response correlation exact

## playback contracts
69. exact playback status vocabulary
70. playback request exact four fields
71. exact TtsAudio revalidation
72. no device/volume/output-path fields
73. playback result exact three fields
74. exact result correlation
75. AudioOutput ABC exact methods
76. output ID strict/read-only
77. active IDs sorted/immutable fake
78. interrupt cancellation correlation
79. ACCEPTED acknowledgement semantics
80. UNSUPPORTED/NOT_FOUND not interruption
81. aclose fake
82. no concrete production output

## config
83. exact two fields
84. default timeout 30.0
85. default barge-in speech frames 2
86. timeout strict float 1..600
87. int/bool timeout rejected
88. barge-in count 1..8 strict

## manager construction/state
89. owner_id voice
90. initial state sleeping
91. no session/turn/playback
92. construction no loop binding/I/O/tasks/events
93. same-loop binding
94. wrong-loop rejected
95. closed state

## activation
96. sleeping -> listening
97. exact session ID
98. listening event exactly once
99. safe event payload
100. no wake provider
101. no startup health
102. duplicate activation busy
103. text_only recovery activation
104. degraded recovery activation
105. deadline created
106. segmenter reset

## timeout
107. before deadline no expiry
108. equality expires
109. after deadline expires
110. listening only
111. sleeping clears session/deadline
112. no background timer/task
113. follow-up completion refreshes deadline
114. barge-in refreshes deadline
115. degraded text turn refreshes deadline

## turn validation
116. exact turn request fields
117. session mismatch rejected
118. one active turn
119. duplicate/nested turn busy
120. listening accepted
121. degraded accepted
122. sleeping rejected
123. text_only rejected
124. speaking rejected
125. timeout checked before start

## STT stage
126. state -> thinking
127. no fake thinking event
128. exact SttRequest derivation
129. operation ID equals turn ID
130. frames exact
131. language hint exact
132. capabilities called once
133. transcribe called once
134. exact capability correlation
135. exact transcript correlation
136. no retry/fallback
137. STT provider failure -> text_only
138. invalid transcript -> text_only
139. STT fallback result has no text
140. no raw STT error leak

## responder stage
141. responder called once
142. receives exact transcript only
143. exact IDs
144. invalid responder draft rejected
145. wrong IDs rejected
146. exception -> responder_failure
147. state restored listening
148. deadline refreshed
149. no fake response
150. approval-looking text inert

## TTS stage
151. skipped entirely in degraded_voice
152. normal listening turn calls capabilities once
153. exact TtsRequest text
154. exact requested voice
155. exact language hint
156. capability validation
157. synthesize once
158. exact TtsAudio correlation
159. no retry/fallback
160. TTS failure -> degraded_voice
161. invalid audio -> degraded_voice
162. fallback result preserves transcript/response
163. no provider diagnostics leak

## degraded voice
164. degraded accepts new voice segment/turn
165. STT still used
166. responder still used
167. TTS not called
168. output not called
169. text_fallback voice_output_unavailable
170. state remains degraded_voice
171. explicit activate recovers listening

## speaking/playback
172. playback request exact IDs/audio
173. active playback ID set
174. state speaking
175. voice.speaking exactly once
176. payload no text/PCM/hash
177. play called once
178. exact completed result
179. completed -> listening
180. voice.listening after completion
181. outcome spoken
182. cleanup active turn/playback

## barge-in
183. only speaking allowed
184. one speech observation below threshold no trigger
185. silence resets count
186. consecutive threshold triggers
187. score ignored
188. interrupt called once
189. exact cancellation provider correlation
190. exact playback ID correlation
191. ACCEPTED publishes voice.interrupted once
192. safe interrupted event payload
193. duplicate observations no second interrupt
194. output UNSUPPORTED -> cancellation_failure
195. output NOT_FOUND -> cancellation_failure
196. wrong cancellation evidence -> cancellation_failure
197. no fake interrupted event on failure
198. accepted barge-in + playback interrupted -> outcome interrupted
199. accepted barge-in + collaborator returns completed -> still outcome interrupted
200. final state listening
201. listening event follows interrupted completion
202. no PhoenixOrchestrator/model/tool cancellation

## playback failure
203. output exception -> degraded_voice
204. invalid result -> degraded_voice
205. text_fallback preserves transcript/response
206. failure_stage playback
207. active playback cleared
208. no fake completed/listening event

## sleep
209. listening -> sleeping
210. degraded -> sleeping
211. text_only -> sleeping
212. segmenter/deadline/session cleared
213. speaking sleep interrupts exact playback once
214. ACCEPTED voice.interrupted exactly once
215. speaking sleep finishes sleeping
216. no listening event after sleep
217. thinking sleep rejected
218. no transcript parsing in production

## events
219. exact three voice EventTypes only
220. EventType remains 32
221. agent_id voice
222. deterministic correlation/session IDs
223. no transcript/response/PCM in payload
224. normal order listening, speaking, listening
225. barge-in order listening, speaking, interrupted, listening
226. EventBus full -> event_failure
227. no permission authority from events

## cancellation safety
228. caller cancellation propagates
229. active bookkeeping cleaned
230. suppressed cancellation cannot advance STT->responder
231. suppressed cancellation cannot advance responder->TTS
232. suppressed cancellation cannot advance TTS->playback
233. suppressed cancellation cannot finalize spoken success
234. no hidden manager task

## close
235. close idle idempotent
236. close clears session
237. close speaking interrupts only active playback
238. close does not close STT
239. close does not close TTS
240. close does not close responder
241. close does not close AudioOutput
242. close does not close EventBus
243. post-close activate fails closed
244. post-close turn fails closed
245. post-close observe fails closed
246. read-only state remains observable

## separation/security
247. no microphone capture
248. no speaker backend
249. no sounddevice/pyaudio/portaudio
250. no network
251. no concrete STT/TTS backend
252. no WakeWordProvider invocation
253. no PHX-065 startup
254. no PhoenixOrchestrator direct call
255. no ToolRegistry
256. no PermissionEngine
257. no AuditTrail
258. no ModelProvider
259. no PromptInjectionGuard automatic call
260. no memory/RAG
261. no secrets
262. no persistence
263. Alembic head 0005
264. migrations unchanged
265. no API/frontend
266. no dependency change
267. EventType definitions unchanged
268. prior voice production unchanged
269. guarded import/construction side-effect free
270. credential/signature scan clean

Add further deterministic adversarial cases where useful.

# Targeted verification

Run:

`uv run --locked pytest tests/test_voice_conversation.py`

THREE consecutive times.

All PASS.

# Previous Phase-6 regressions

Run individually:

- tests/test_tts_provider.py — baseline 602
- tests/test_stt_provider.py — baseline 414
- tests/test_vad.py — baseline 403
- tests/test_wake_word_provider.py — baseline 537

All pass.

# Phase-5 regressions

Run individually:

- tests/test_prompt_injection_defenses.py — 604
- tests/test_urahara_research_flow.py — 492
- tests/test_search_provider.py — 400
- tests/test_screenshot_vision_loop.py — 397
- tests/test_windows_ui_automation.py — 493
- tests/test_browser_tool.py — 653

# Phase-4 regressions

- secrets 402
- terminal 270
- filesystem 649 passed / 16 existing skips
- audit 302
- permissions 926
- ToolRegistry 204

# Phase-3 regressions

- memory schema 244
- PUDDING 167
- ingestion 300
- vector store 273
- retrieval 367
- citations 173
- persistence 12

# Phase-2

Run all nine established suites individually.
Baseline aggregate:
2,729 passed.

# Phase-1

Run all seven established suites individually.
Baseline aggregate:
812 passed.

# Alembic / events

Verify:
- sole Alembic head `0005_audit_events`
- migrations 0001–0005 unchanged
- EventType remains exactly 32
- no EventType definition edits

PHX-064 is allowed to IMPORT and PUBLISH the existing voice events.

# Full quality

Run:

`uv lock`
`uv sync --locked`
`uv run --locked pytest`
`uv run --locked ruff check .`
`uv run --locked ruff format --check .`
`uv run --locked mypy`
`uv build`

Pre-PHX-064 baseline:
- 12,939 passed
- 16 skipped
- one existing warning

Expected dependency changes:
NONE.

# Foundation

Run established foundation suites.
Baseline:
126 passed.

Exact `/health` unchanged.

# Desktop

Run:
- `npm test`
- `npm run typecheck --workspace @phoenix/desktop`
- `npm run build --workspace @phoenix/desktop`

Baseline:
4 passed.

Desktop production unchanged.

# Security review

Confirm:
- only caller-supplied PCM/VAD evidence accepted,
- no microphone/device capture,
- no real speaker backend,
- no network voice path,
- no audio/transcript/response persistence/logging,
- no auto provider fallback,
- no hidden timers/tasks/workers,
- no transcript-based permission/tool execution,
- barge-in limited to active injected playback,
- exact event payload privacy,
- exact provider/result correlation,
- explicit text fallback,
- explicit caller recovery,
- no startup/health fabrication,
- no ToolRegistry/PermissionEngine/Audit/model/memory/RAG/secrets/API/frontend/schema/dependency changes.

Run credential/signature scan.

# Invariants

Review INV-001 through INV-024.

Especially:
- INV-001 PHOENIX supreme
- INV-003 voice/transcript/output cannot grant permission
- INV-004 no privileged tool bypass
- INV-005 P4 unchanged
- INV-006 P5 unchanged
- INV-007 audit unchanged
- INV-008 conversation cannot finalize task verification
- INV-011 no frontend direct provider/device path
- INV-012 provider implementations remain outside agent definitions
- INV-013 no tool policy in voice layer
- INV-014 UTA alias removable
- INV-015 text remains independently usable
- INV-016 voice failure falls back to degraded/text state without crashing orchestration
- INV-017 transcript/audio are untrusted evidence
- INV-018 no automatic normal-memory persistence
- INV-019 active operations explicit/cancellable; no hidden long task
- INV-020 no schema mutation
- INV-021 failures explicit
- INV-022 no silent provider fallback
- INV-023 no additional expensive model generation path introduced
- INV-024 no new dependency

# ADR

At most one ADR.

ADR-061 should document:
- voice/UTA conceptual ownership,
- exact six voice states,
- caller-driven activation; PHX-065 owns wake/startup,
- injected monotonic follow-up timeout,
- deterministic 2-frame start debounce / 10-frame end hangover / 256-frame max segment,
- terminal silence exclusion,
- provider-neutral responder/output boundaries,
- STT -> responder -> TTS -> playback pipeline,
- degraded_voice vs text_only semantics,
- barge-in requires consecutive VAD speech evidence and interrupts only active playback,
- existing three voice EventTypes and safe payloads,
- no concrete capture/playback engine,
- no hidden timer/task,
- no transcript authority,
- caller-owned collaborators,
- no dependency/schema/API/frontend changes.

# Completion

Only after all deterministic gates pass:
- mark PHX-064 ticket DONE,
- mark PHX-064 DONE in FEATURE_TICKETS.md,
- leave PHX-065 TODO,
- Phase 6 remains INCOMPLETE,
- commit exactly:
  `feat: complete PHX-064 voice conversation barge-in`
- push only:
  `feature/PHX-064-voice-conversation-barge-in`
- no PR,
- no merge,
- no main push,
- do not start PHX-065.

# Known limitations

Disclose at minimum:
- no microphone capture/backend,
- no concrete speaker/output backend,
- no real STT/TTS engine,
- caller must supply VAD observations,
- VAD may false-trigger from noise/TTS echo,
- no acoustic echo cancellation,
- no streaming STT/TTS,
- one active session and one active turn,
- timeout is caller-driven via expire_if_idle; no background timer,
- sleep intent parsing is external,
- barge-in interrupts playback only,
- no cancellation of unrelated model/tool/orchestration work,
- degraded_voice uses voice input + text output,
- text_only requires typed/UI fallback,
- explicit activation required to recover voice,
- PHX-065 still required for wake/startup health integration.

# Next

PHX-065 — Rise-Phoenix startup health sequence.

Do not start it.


# Authoritative clarification — interrupted turn result and explicit sleep

This section resolves the PHX-064 blocker discovered before implementation and
**supersedes any earlier conflicting wording in this ticket**.

## VoiceFailureStage final vocabulary

Freeze exactly:

- `stt`
- `tts`
- `playback`
- `voice_output_unavailable`

The earlier Part J three-value list is incomplete. The
`voice_output_unavailable` value defined in Part O is canonical and required.

## VoiceTurnResult interrupted-state semantics

For `outcome=interrupted`:

- `transcript_text` is present,
- `response_text` is present,
- `failure_stage=None`,
- `final_state` is allowed to be exactly one of:
  - `listening`
  - `sleeping`

These two final states have distinct manager meanings:

### Barge-in-owned interruption

When interruption was accepted because of
`observe_barge_in(...)` for the active playback:

- `outcome=interrupted`
- `failure_stage=None`
- `final_state=listening`
- manager state ends `listening`
- follow-up deadline is refreshed
- exactly one `voice.listening` follows the accepted interruption/completion path

This preserves focused-test requirements 198–201.

### Explicit-sleep-owned interruption

When `sleep()` is called while speaking and
`AudioOutput.interrupt(active_playback_id)` returns exact correlated
`CancellationStatus.ACCEPTED`:

- explicit sleep owns the terminal voice state,
- `outcome=interrupted`
- `failure_stage=None`
- `final_state=sleeping`
- manager state ends `sleeping`
- session is cleared
- follow-up deadline is cleared
- segmenter/barge-in local state is reset
- no `voice.listening` event is published after explicit sleep

This preserves focused-test requirements 213–216.

An interrupted result may not use `thinking`, `speaking`,
`degraded_voice`, or `text_only` as final_state.

## Suppressed-interruption safety for explicit sleep

After an exact ACCEPTED sleep interruption, the sleep request owns the terminal
voice outcome just as an accepted barge-in owns its interruption outcome.

Therefore, if the injected AudioOutput collaborator suppresses the interruption
and later returns `completed` for that same active playback:

- do NOT report `spoken`,
- do NOT return to `listening`,
- do NOT refresh the follow-up deadline,
- do NOT publish `voice.listening`,
- finalize the voice turn as `interrupted` with `final_state=sleeping`.

This is deterministic suppression safety, not a claim that native playback
actually stopped.

## Failed explicit-sleep interruption

If the sleep-time `AudioOutput.interrupt(...)` result is:

- `NOT_FOUND`,
- `UNSUPPORTED`,
- wrongly correlated,
- malformed,
- or raises a safe AudioOutput/cancellation failure,

then:

- raise/map `cancellation_failure` as defined by PHX-064,
- do not set `sleep_requested`,
- do not publish `voice.interrupted`,
- do not claim an interrupted/sleeping turn result solely from that failed
  acknowledgement.

No fake interruption may be reported.

## Event ordering clarification

For an accepted explicit sleep while speaking, the relevant observable order is:

`voice.speaking -> voice.interrupted`

There is **no** trailing `voice.listening` event.

For barge-in, the existing required order remains:

`voice.speaking -> voice.interrupted -> voice.listening`

Activation's earlier `voice.listening` event remains unchanged.

## Result-contract implementation requirement

The frozen `VoiceTurnResult` validator may admit
`outcome=interrupted` with final_state `listening` or `sleeping`.

The `VoiceConversationManager` is responsible for enforcing the cause-specific
mapping:

- accepted barge-in -> `listening`
- accepted explicit sleep -> `sleeping`

No new field is added to `VoiceTurnResult`.

## Additional deterministic focused cases

In addition to the existing matrix, explicitly test:

1. `VoiceFailureStage` exact four-value vocabulary including
   `voice_output_unavailable`.
2. interrupted result accepts final_state `listening`.
3. interrupted result accepts final_state `sleeping`.
4. interrupted result rejects every other final_state.
5. barge-in cannot produce interrupted/sleeping.
6. explicit sleep while speaking cannot produce interrupted/listening.
7. accepted explicit sleep + collaborator returns `completed` still resolves
   interrupted/sleeping.
8. accepted explicit sleep clears session/deadline and emits no trailing
   listening event.
9. failed/unsupported/not-found sleep interruption does not claim sleeping
   interruption or emit `voice.interrupted`.
10. exact cancellation provider/playback correlation is required before
    sleep ownership is established.

This clarification is authoritative for PHX-064 and requires no separate ADR
beyond the existing ADR-061 completion record.

# Completion evidence — 2026-10-07

Original clean local HEAD 17d185841b8a2114d5652fe4e04226d0bdcff1f8 was updated only
by normal fetch --prune and ff-only pull to authoritative preparation
e88c8bd0177df1458932b6d6ac03604d3da3c41f. Main remained
e60a9d8b0471940a516210437e9588c45e1ad055; 0 behind / 3 ahead, ticket-only initial diff.
Read the complete clarified ticket; the old interrupted-result blocker is resolved.
No preparation history was rewritten. No next-ticket work/PR/merge/main push.

Implemented phoenix/voice/conversation/__init__.py and tests/test_voice_conversation.py.
Package layout preserves the public import and unchanged PHX-060 file-layout test;
no prior production/test or export change is needed. ADR-061 records all architecture,
failure/event/cancellation ownership and the layout choice.

## Contract, lifecycle and matrix evidence

950 synthetic deterministic cases cover the 270 numbered ticket boundaries and
post-blocker clarification through grouped behavioral/adversarial tests: exact
vocabularies/fields/frozen JSON snapshots, extras/subclass/stored forgery rejection,
strict IDs/ints/floats/languages/text, private repr/safe errors, exact PCM/VAD and
segment evidence, 2/10/256 segmentation, terminal exclusion, total buffer overflow,
same-stream/sequence/time continuity, responder/output/cancellation correlation,
one-turn lifecycle, follow-up expiry, stage call counts/failure/text fallback,
degraded input-only recovery, events, explicit sleep, cancellation and cleanup.
Exhaustive result-consistency combinations verify all outcome/failure/state/text
combinations; interrupted may only be listening or sleeping with both texts.

Normal pipeline calls STT cap/transcribe -> responder -> TTS cap/synthesize -> play
once each. Follow-up needs no repeated wake phrase. STT failure returns text_only
with no text/deadline. TTS/playback failure returns degraded_voice preserving text;
subsequent degraded turns skip TTS/output and use voice_output_unavailable.
Responder failure restores listening/deadline and raises responder_failure.
No automatic provider retry/switch/voice recovery exists.

Barge-in uses activity only, default two consecutive speech frames, silence reset,
one exact active-playback interrupt and canonical correlated acknowledgement.
Accepted barge-in returns interrupted/listening with refreshed deadline and trailing
listening event. Accepted speaking sleep returns interrupted/sleeping, clears
session/deadline/local buffers and publishes no trailing listening. Both retain
ownership if fake playback later returns completed. Failed acknowledgements cannot
claim interruption or sleep. Delayed acknowledgement racing completed playback and
concurrent duplicate observations cannot produce spoken success/duplicate interrupts.
No unrelated provider/model/tool/orchestration cancellation occurs.

Real EventBus tests verify normal/barge-in/sleep order, agent voice, deterministic
session correlation and IDs-only payloads. Full queue/failure/cancelled publication
raises explicit error and prevents false observable completion; state/bookkeeping
are safely cleared. Caller-task cancellation, including suppression at all six
collaborator awaits, cannot advance/finalize a turn. Close interrupts only playback,
is same-loop/idempotent, clears state, closes no borrowed collaborator and blocks
late continuation. Constructor/explicit operations create no hidden manager tasks.

## Commands and exact results

THREE consecutive uv run --locked pytest tests/test_voice_conversation.py -q:
950 passed each after the final buffer/event-cancellation review changes.
Earlier development runs found safe-wrapper ordering and two fixture coroutine
issues; these were fixed. No prior tests were changed or bypassed.

All 39 previous focused suites ran individually with uv run --locked pytest <path>
-q and JUnit logs outside the repository. Their historical case counts are unchanged.

| Suite | Result |
| --- | --- |
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

Aggregates: previous Phase 6 1956; Phase 5 3039; Phase 4 2753 / 16 existing skips;
Phase 3 1524 plus persistence 12; all nine Phase 2 suites 2729; all seven Phase 1
suites 812. Every suite passed individually.

- uv lock; uv sync --locked: PASS, manifests unchanged.
- uv run --locked pytest: 13889 passed, 16 existing skips, one existing
  Starlette/httpx TestClient deprecation warning. Baseline 12939 plus 950 new cases.
- uv run --locked ruff check .: PASS.
- uv run --locked ruff format --check .: PASS, 122 files formatted.
- uv run --locked mypy: PASS, 116 source files.
- uv build --out-dir <external-validation>/dist: wheel/sdist PASS; both contain
  exact conversation/__init__.py source bytes, no flat conversation.py.
- Foundation bootstrap/API/config/persistence/events/hardware: 126 passed,
  existing warning only. Exact /health unchanged.
- npm test: 4 passed; desktop workspace typecheck/build PASS, desktop source unchanged.

Logs/JUnit/review scripts/reports/Python build artifacts remain outside the project:
C:/Users/shoai/.codex/validation/PHX-064-20261007. Ignored desktop build output is
not part of the delivered diff. No recordings, models, PCM/text dumps, DB/log/cache,
credential or unrelated artifacts are staged.

## Security, invariants, scope and acceptance

Independent normalized Git-blob review proves all 141 prior protected production,
tests, migrations, desktop and dependency paths unchanged. EventType definitions
unchanged with 32 members; sole head 0005_audit_events and five unchanged migrations.
Credential/signature scan PASS. Guarded uncached construction and cold import deny
unrelated integrations; static import/call checks rule out capture/device/network/
subprocess/background/provider startup. PCM/transcript/response stays caller data,
private in repr, never logged/persisted/events. Real authority contracts reject
voice evidence as PermissionGrant/UserPermissionDecision/ToolDefinition.

Reviewed INV-001..INV-024: all preserved. PHOENIX supreme; AIZEN/LEVI/MAHORAGA/KAIDO,
deterministic permissions, P4 explicit confirmation, P5 blocking and P3/P4 audit
unchanged. No tool bypass, LLM permission, voice authority, task verification claim,
automatic memory, hidden provider fallback, heavy runtime or dependency promotion.
Only existing three voice events publish; observability grants no authority.

All deterministic implementation/regression/quality/security gates passed before
DONE. Complete staged line review and final voice/TTS/STT/VAD/wake recheck are required
before the exact completion commit and feature-only push; execution final report
records those results. PHX-065 remains TODO and Phase 6 INCOMPLETE.

## Known limitations

No microphone/capture or concrete speaker/output/STT/TTS runtime. Caller supplies
PCM/VAD evidence and owns collaborators/deadlines/native cleanup. Noise/TTS echo
can trigger VAD/barge-in; no acoustic echo cancellation or identity authentication.
No streaming STT/TTS; one active session/turn; timeout requires expire_if_idle calls;
sleep-intent parsing is external. Segment cap includes pending terminal silence;
overflow resets rather than truncating. Playback interruption acknowledgement does
not prove native cessation and affects no unrelated work. Degraded voice preserves
voice input/text output; text_only requires typed/UI fallback and explicit activation
recovers voice. No wake/startup health integration; PHX-065 is NOT STARTED.
