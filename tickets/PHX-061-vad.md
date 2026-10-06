# PHX-061 — VAD

Status: TODO
Phase: 6 — Voice

## Objective
Implement PHOENIX's first local voice-activity-detection boundary and one dependency-free deterministic baseline provider over the PHX-060 caller-supplied PCM evidence contract.

PHX-061 establishes:

1. provider-neutral VAD capability/request/decision/error contracts,
2. a provider-neutral async `VadProvider` interface,
3. one built-in `EnergyVadProvider` using bounded deterministic RMS-energy analysis from the Python standard library only,
4. exact frame-size/correlation/cancellation semantics suitable for later PHX-064 voice conversation and barge-in integration.

PHX-061 does **not**:
- access a microphone,
- record audio,
- perform wake-word detection,
- perform STT,
- perform TTS,
- create transcripts,
- manage active conversation state,
- implement barge-in itself,
- publish voice events,
- initialize PHOENIX,
- run startup health checks,
- persist audio,
- add a runtime dependency.

The built-in energy provider is a local baseline, not a semantic speech model and not a truth claim about human speech. Future higher-quality VAD adapters may implement the same interface after separately approved dependency/model promotion.

## Frozen authorities
- Conceptual owner internal ID: `voice`.
- Display alias: UTA.
- Alias text grants no authority.
- PHOENIX remains supreme.
- PHX-060 owns canonical wake PCM evidence and wake-word contracts.
- PHX-061 owns frame-level voice-activity classification only.
- PHX-062 owns STT.
- PHX-063 owns TTS.
- PHX-064 owns active conversation, debounce/hangover policy and barge-in.
- PHX-065 owns Rise-Phoenix startup health sequence.
- PermissionEngine remains the only permission authority.
- EventType remains exactly 32.

## Architecture decision
VAD consumes exactly one PHX-060 `PcmAudioFrame` per request.

VAD accepts only frame durations that are practical for streaming speech activity detection:

- 10 ms
- 20 ms
- 30 ms

At PHX-060's frozen 16 kHz mono S16LE format this means exact decoded PCM lengths:

- 320 bytes
- 640 bytes
- 960 bytes

PHX-061 performs no resampling, rechunking, buffering or repair.

PHX-064 may later define higher-level debounce, speech-start/end and conversation/barge-in state using these frame-level decisions.

## Dependency decision
Expected new dependencies: **NONE**.

Do NOT add:
- webrtcvad
- silero-vad
- torch
- torchaudio
- onnxruntime
- numpy
- scipy
- librosa
- sounddevice
- pyaudio
- portaudio
- any microphone/audio SDK

`pyproject.toml` and `uv.lock` must remain unchanged.

## Recommended implementation
Prefer:

- `phoenix/voice/vad.py`
- `tests/test_vad.py`

Optional minimal exports in:
- `phoenix/voice/__init__.py`

Expected status/docs:
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- this ticket

No existing Phase 0–5 production module should need modification.

# Part A — VAD vocabularies

## VadActivity
Freeze exactly:

- `silence`
- `speech`

This is frame-level provider evidence only.

It does not:
- start STT,
- move conversation state,
- interrupt TTS,
- authorize any action.

## VadFrameDuration
Freeze exactly:

- `ms_10`
- `ms_20`
- `ms_30`

Provide deterministic duration-to-byte evidence:

- ms_10 -> 320 bytes
- ms_20 -> 640 bytes
- ms_30 -> 960 bytes

No arbitrary duration values.

## VadProviderErrorCode
Freeze exactly:

- `invalid_input`
- `invalid_response`
- `unsupported_audio`
- `unavailable`
- `timeout`
- `busy`
- `closed`
- `wrong_event_loop`
- `cleanup_failure`

Public error strings contain safe code/category only.

Never include:
- PCM bytes,
- base64,
- audio hashes unless explicitly safe/required,
- thresholds,
- raw backend diagnostics,
- device names,
- environment,
- credentials,
- transcript text.

# Part B — VAD capabilities

## VadCapabilities
Create frozen, extra-forbidden `VadCapabilities` with exactly:

- `provider_id`
- `supported_durations`
- `sample_rate_hz`
- `channels`
- `sample_width_bytes`
- `encoding`
- `supports_cancellation`

Rules:
- provider ID strict normalized, control-free, case-preserving, <=256
- supported durations immutable tuple 1..3
- exact `VadFrameDuration` values
- unique
- canonical enum order required
- audio format exactly PHX-060 V1:
  - 16000
  - 1
  - 2
  - pcm_s16le
- supports_cancellation strict bool

Capabilities are technical evidence only.

They do not:
- select a provider,
- prove quality,
- prove availability,
- authorize microphone access,
- grant permission.

Provide exact provider correlation helper equivalent to:

`capabilities.validate_provider(provider_id)`

# Part C — VAD request

## VadRequest
Create frozen, extra-forbidden `VadRequest` with exactly:

- `operation_id`
- `frame`
- `duration`

### operation_id
- strict normalized nonempty control-free string
- case-preserving
- <=256
- UUID4 default acceptable

### frame
- exact PHX-060 `PcmAudioFrame`
- stored-value/subclass/extra attacks rejected

### duration
- exact `VadFrameDuration`

## Exact duration/byte correlation
Require decoded frame PCM length:

- ms_10 -> exactly 320
- ms_20 -> exactly 640
- ms_30 -> exactly 960

Anything else -> `unsupported_audio`.

No:
- padding
- truncation
- rechunking
- resampling
- downmixing
- sequence repair
- timestamp repair

## Request privacy
No:
- device ID
- microphone handle
- threshold override
- model path
- API key
- callback
- tool ID
- permission
- risk
- conversation state

Adapter-specific configuration belongs to provider construction, not request.

# Part D — VAD decision

## VadDecision
Create frozen, extra-forbidden `VadDecision` with exactly:

- `operation_id`
- `provider_id`
- `stream_id`
- `sequence`
- `captured_at`
- `activity`
- `score`
- `duration`

Rules:

### IDs / sequence / timestamp
- exact strict IDs
- sequence strict nonnegative int; bool rejected
- captured_at aware UTC normalized

### activity
exact `VadActivity`.

### score
- strict ordinary float
- bool/int rejected
- finite
- 0..1 inclusive

The score is provider-local normalized activity evidence only.

It is NOT guaranteed:
- calibrated probability,
- speaker identity confidence,
- human speech certainty,
- command validity,
- user intent,
- permission.

Do not derive `activity` from score at the neutral contract layer.

### duration
exact `VadFrameDuration`.

## Decision correlation
Provide equivalent:

`decision.validate_request(request, provider_id)`

Require exact:
- operation_id
- provider_id
- stream_id
- sequence
- captured_at
- duration

Wrong correlation -> explicit `invalid_response`.

No:
- transcript,
- language,
- command,
- speaker/user identity,
- wake phrase,
- permission/action,
- conversation-state transition,
- barge-in flag,
- startup health.

# Part E — VadProvider

## Public API
Define provider-neutral async ABC:

- read-only `provider_id`
- read-only `active_operation_ids`
- `async capabilities() -> VadCapabilities`
- `async classify(request: VadRequest) -> VadDecision`
- `async cancel(operation_id: str) -> CancellationResult`
- `async aclose() -> None`

Reuse existing neutral:
- `CancellationResult`
- `CancellationStatus`

## Provider construction
Base provider constructor accepts only a strict normalized provider ID.

Construction performs no:
- I/O,
- model load,
- microphone access,
- environment lookup,
- thread/task creation,
- filesystem access,
- network.

## active_operation_ids
- sorted immutable tuple
- observational only
- no task handles
- no authority

## classify
Consumes caller-supplied VAD request only.

Concrete providers must:
- exact-revalidate input,
- return exact correlated decision,
- or raise safe explicit provider error.

No:
- implicit retry,
- alternate provider,
- hidden buffering,
- automatic STT/TTS/conversation action.

## cancel
Operation-scoped only.

Accepted:
- acknowledgement, not proof all native work stopped.

No global shutdown.

## aclose
Explicit adapter cleanup.

Base ABC allocates no resources.

# Part F — Deterministic EnergyVadProvider

Implement one production dependency-free baseline:

`EnergyVadProvider`

This is the only concrete production provider in PHX-061.

## Provider identity
Default exact provider ID:

`energy_vad_v1`

Constructor may accept an explicit provider ID only if consistent with existing project provider conventions; the authoritative default must remain `energy_vad_v1`.

Do not silently derive identity from threshold.

## Supported audio
Supports all exact V1 durations:

- 10 ms
- 20 ms
- 30 ms

Uses PHX-060 16 kHz mono S16LE only.

supports_cancellation:

`False`

Classification is bounded and synchronous CPU work wrapped by the async provider API.

No hidden worker/thread.

## Energy algorithm
Decode exact PCM using PHX-060 deliberate byte-access method.

Interpret bytes as little-endian signed 16-bit samples.

Use stdlib only.

Prefer:
- `struct.iter_unpack("<h", pcm)`
or equivalent deterministic stdlib logic.

Calculate:

`rms = sqrt(sum(sample^2) / sample_count)`

For all-zero PCM:
- RMS = 0
- dBFS floor = -96.0

For RMS > 0:

`dbfs = 20 * log10(rms / 32768.0)`

Clamp internal dBFS to [-96.0, 0.0].

## Frozen default threshold
Freeze production default:

`DEFAULT_ENERGY_VAD_THRESHOLD_DBFS = -42.0`

Constructor may accept `threshold_dbfs` with strict rules:

- ordinary float only
- bool/int rejected
- finite
- -90.0 <= value <= -10.0

Threshold is adapter-local configuration only.

It never appears in VadRequest.

## Energy activity rule
For EnergyVadProvider only:

`activity = speech` iff `dbfs >= threshold_dbfs`

Otherwise:

`silence`.

This is a deterministic energy gate, not semantic speech understanding.

## Energy score
For EnergyVadProvider only, derive a deterministic normalized energy score:

`score = clamp((dbfs + 96.0) / 96.0, 0.0, 1.0)`

Rules:
- score 0.0 for -96 dBFS floor
- score approaches 1.0 near 0 dBFS
- this is normalized energy evidence, NOT speech probability
- activity remains threshold-based; do not change neutral contract semantics

## Bounded work
Maximum accepted frame is 960 bytes = 480 samples.

No unbounded loops beyond the exact frame.

No vector/matrix allocation library.

## Energy provider active state
Because classification completes without awaits/worker handoff:
- `active_operation_ids` may remain empty
- duplicate operation IDs do not create retained state
- `cancel()` returns `UNSUPPORTED`
- `aclose()` is explicit/idempotent local close
- classify after close -> `closed`

The provider must still enforce one event-loop ownership if it stores close state and async methods, consistent with project async-provider patterns. Cross-loop reuse after first async use -> `wrong_event_loop`.

No task cancellation is needed for the tiny bounded classify operation.

# Part G — Exact energy-provider lifecycle

## Construction
No loop binding at constructor.

No I/O.

No task.

No resource allocation beyond small immutable configuration.

## First async method
Bind provider instance to current event loop.

Later async call from a different loop:
- `wrong_event_loop`.

## Closed state
After successful `aclose()`:
- capabilities -> `closed`
- classify -> `closed`
- cancel -> `closed`
- repeated aclose is safe/idempotent on same loop

No hidden reopen.

# Part H — No conversation policy

PHX-061 must NOT define:
- speech-start debounce count,
- speech-end hangover,
- conversation timeout,
- wake-to-listen transition,
- barge-in interruption,
- active conversation state,
- sleep state.

Those belong to PHX-064.

Frame-level VAD decisions are input evidence only.

# Part I — No wake-word coupling

PHX-061 may reuse PHX-060 audio contracts only.

Do NOT:
- call WakeWordProvider,
- require WakeWordDetection,
- alter DEFAULT_WAKE_PHRASE,
- trigger wake flow.

Wake-word and VAD are separable components.

# Part J — No STT / TTS

Do not:
- transcribe,
- detect language,
- call Whisper/Vosk/faster-whisper,
- synthesize speech,
- call Coqui,
- play audio.

No text is produced from PCM.

# Part K — No microphone/device

Production code must not import/use:
- sounddevice
- pyaudio
- portaudio
- WASAPI
- Core Audio
- wave device input
- ffmpeg recording
- device enumeration

Tests use synthetic PCM only.

# Part L — No EventBus / tool authority

No EventBus.

No voice event publication.

Do not use:
- voice.listening
- voice.speaking
- voice.interrupted

No ToolRegistry.

No PermissionEngine.

No AuditTrail.

VAD data cannot:
- approve actions,
- lower risk,
- override P5,
- start tools.

# Part M — No model/memory/secrets

No:
- ModelProvider inference
- Ollama
- Colibri
- PromptInjectionGuard
- SecretStore
- Memory/RAG
- embeddings
- vector store

VAD produces no text.

# Part N — No persistence/API/frontend

No:
- SQLAlchemy model
- migration
- audio history
- VAD history
- file cache
- FastAPI endpoint
- WebSocket audio endpoint
- Tauri microphone command
- React voice state

Alembic head remains:

`0005_audit_events`

EventType remains exactly 32.

# Part O — Side-effect isolation

Importing/constructing/validating:
- VAD enums,
- capabilities,
- requests,
- decisions,
- VadProvider fake subclasses,
- EnergyVadProvider

must perform zero:
- microphone/device access,
- file reads/writes,
- DB,
- network,
- subprocess,
- thread,
- async task,
- EventBus,
- model/tool/permission work,
- environment-secret lookup.

Classification itself:
- consumes only exact caller PCM,
- performs bounded CPU math,
- no external side effect.

# Part P — Focused tests

Create:

`tests/test_vad.py`

Use:
- deterministic synthetic S16LE frames,
- zero PCM,
- constant-amplitude PCM,
- alternating amplitude PCM,
- exact 10/20/30 ms byte sizes,
- test-only fake VadProvider implementations where needed,
- built-in EnergyVadProvider.

No microphone/network/model.

At minimum cover:

## enums/errors
1. exact activity vocabulary
2. exact duration vocabulary
3. duration -> byte mapping exact
4. exact provider error vocabulary
5. safe provider error strings
6. foreign/copied enums rejected

## capabilities
7. exact seven fields
8. frozen
9. extras rejected
10. provider ID strict
11. one duration accepted
12. all three accepted
13. empty rejected
14. duplicate duration rejected
15. noncanonical order rejected
16. exact 16000
17. exact mono
18. exact 2-byte
19. exact pcm_s16le
20. strict supports_cancellation bool
21. exact provider correlation
22. wrong provider rejected
23. nested/stored/subclass attacks rejected
24. no authority fields

## request
25. exact three fields
26. frozen/extras
27. operation ID default/strict
28. exact PcmAudioFrame revalidation
29. duration exact
30. 10 ms -> 320 accepted
31. 20 ms -> 640 accepted
32. 30 ms -> 960 accepted
33. 318/322 rejected for 10 ms
34. 638/642 rejected for 20 ms
35. 958/962 rejected for 30 ms
36. PHX-060 longer valid frame rejected as unsupported VAD audio
37. no padding/truncation
38. no resample/rechunk
39. no threshold/model/device fields
40. request repr does not dump PCM/base64

## decision
41. exact eight fields
42. frozen/extras
43. strict operation/provider/stream IDs
44. sequence zero/nonnegative
45. bool sequence rejected
46. captured_at aware required
47. UTC normalization
48. exact activity
49. exact duration
50. score 0
51. score 1
52. score mid
53. bool/int score rejected
54. NaN/inf rejected
55. out-of-range rejected
56. no transcript
57. no language
58. no user/speaker identity
59. no permission/action
60. no conversation/barge-in/startup fields
61. exact request correlation
62. wrong operation rejected
63. wrong provider rejected
64. wrong stream rejected
65. wrong sequence rejected
66. wrong timestamp rejected
67. wrong duration rejected
68. score does not independently override activity

## VadProvider ABC
69. abstract boundary exact members
70. abstract base cannot instantiate
71. provider ID normalized/read-only
72. invalid provider IDs rejected
73. construction no I/O
74. fake provider A substitutable
75. fake provider B substitutable
76. capability consumer exact-revalidates
77. decision consumer exact-revalidates
78. cancel ACCEPTED shape
79. cancel UNSUPPORTED shape
80. cancel NOT_FOUND shape
81. cancellation provider correlation
82. cancellation operation correlation
83. aclose contract
84. no provider-selection policy

## EnergyVadProvider
85. exact default provider ID
86. default threshold -42.0
87. threshold ordinary float only
88. threshold lower bound accepted
89. threshold upper bound accepted
90. threshold below bound rejected
91. threshold above bound rejected
92. bool threshold rejected
93. int threshold rejected
94. NaN/inf threshold rejected
95. construction no loop/I/O
96. capabilities supports all 10/20/30
97. capabilities supports_cancellation false
98. zero PCM -> silence
99. zero PCM score exactly 0.0
100. low constant amplitude -> silence at default threshold
101. high constant amplitude -> speech at default threshold
102. exact threshold boundary uses speech
103. just below threshold uses silence
104. positive max sample handling
105. negative min sample handling
106. alternating signed samples deterministic
107. RMS deterministic repeated calls
108. score deterministic
109. score finite 0..1
110. activity threshold rule exact
111. score not treated as probability claim
112. frame/request not mutated
113. no retained PCM
114. active IDs empty
115. cancel returns UNSUPPORTED
116. aclose idempotent
117. classify after close -> closed
118. capabilities after close -> closed
119. cancel after close -> closed
120. cross-event-loop async use -> wrong_event_loop
121. no hidden thread/task
122. no retry/fallback
123. no logs

## separation
124. PHX-060 audio/wake production unchanged
125. DEFAULT_WAKE_PHRASE unchanged
126. no WakeWordProvider invocation
127. no VAD debounce/hangover conversation policy
128. PHX-062 STT separation
129. PHX-063 TTS separation
130. PHX-064 conversation/barge-in separation
131. PHX-065 startup separation
132. no microphone imports
133. no device I/O
134. no network
135. no EventBus
136. EventType exactly 32
137. no ToolRegistry
138. no PermissionEngine
139. no AuditTrail
140. no model providers
141. no PromptInjectionGuard
142. no SecretStore
143. no memory/RAG
144. no persistence
145. Alembic head 0005
146. migrations unchanged
147. no API/frontend
148. no dependency changes
149. guarded import/construction side-effect free
150. classification side effects limited to bounded CPU math
151. no PCM/base64 logging
152. credential/signature scan clean

Add further deterministic adversarial cases where useful.

# Targeted verification
Run:

`uv run --locked pytest tests/test_vad.py`

THREE consecutive times.

All PASS.

Three runs are required because provider event-loop/close semantics are async even though energy classification itself is bounded CPU work.

## PHX-060 regression
Run:

`uv run --locked pytest tests/test_wake_word_provider.py`

Baseline:
537 passed.

## PHX-055 regression
Run:

`uv run --locked pytest tests/test_prompt_injection_defenses.py`

Baseline:
604 passed.

## PHX-054 regression
Run:

`uv run --locked pytest tests/test_urahara_research_flow.py`

Baseline:
492 passed.

## PHX-053 regression
Run:

`uv run --locked pytest tests/test_search_provider.py`

Baseline:
400 passed.

## PHX-052 regression
Run:

`uv run --locked pytest tests/test_screenshot_vision_loop.py`

Baseline:
397 passed.

## PHX-051 regression
Run:

`uv run --locked pytest tests/test_windows_ui_automation.py`

Baseline:
493 passed.

## PHX-050 regression
Run:

`uv run --locked pytest tests/test_browser_tool.py`

Baseline:
653 passed.

## Phase-4 regressions
Run individually:
- secrets 402
- terminal 270
- filesystem 649 passed / 16 skipped
- audit 302
- permissions 926
- ToolRegistry 204

## Phase-3 regressions
Run individually:
- memory schema 244
- PUDDING 167
- ingestion 300
- vector store 273
- retrieval 367
- citations 173
- persistence 12

## Phase-2
Run all nine established suites individually.
Baseline aggregate:
2,729 passed.

## Phase-1
Run all seven established suites individually.
Baseline aggregate:
812 passed.

# Alembic / events
Verify:
- sole Alembic head `0005_audit_events`
- migrations 0001–0005 unchanged
- EventType exactly 32
- EventBus/EventType production unchanged

# Full quality
Run:

`uv lock`
`uv sync --locked`
`uv run --locked pytest`
`uv run --locked ruff check .`
`uv run --locked ruff format --check .`
`uv run --locked mypy`
`uv build`

Pre-PHX-061 baseline:
- 11,520 passed
- 16 skipped
- one existing warning

Expected dependency changes:
**NONE**

`pyproject.toml` and `uv.lock` unchanged.

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
- no microphone/device access
- no audio persistence/logging
- no network
- no external VAD dependency/model
- exact 10/20/30 ms byte admission
- no resample/rechunk/repair
- decision grants no authority
- score is provider-local evidence only
- EnergyVadProvider threshold is adapter-local config, never request authority
- no wake action
- no STT/TTS/conversation/barge-in/startup
- no EventBus/tool/permission/audit/model/memory/RAG/secrets/API/frontend/schema
- no dependency change
- errors do not expose audio

Run repository credential/signature scan.

# Invariants
Review INV-001 through INV-024.

Especially:
- INV-001 PHOENIX remains supreme
- INV-003 VAD output cannot grant permission
- INV-004 no privileged tool execution
- INV-005 P4 unchanged
- INV-006 P5 unchanged
- INV-007 audit unchanged
- INV-008 VAD cannot finalize task/startup success
- INV-011 no frontend microphone privilege path
- INV-012 VAD provider remains voice-local/provider-neutral
- INV-013 no tool policy in VAD
- INV-014 UTA alias irrelevant
- INV-015 text path unaffected
- INV-016 VAD failure cannot crash orchestration because integration remains caller-owned
- INV-017 audio remains untrusted data
- INV-018 no audio/secrets to normal memory
- INV-019 provider exposes state/cancel/close; classify itself bounded
- INV-020 no schema mutation
- INV-021 explicit errors
- INV-022 no silent fallback
- INV-023 no heavy model generation
- INV-024 no dependency

# ADR
At most one ADR.

ADR-058 should document:
- voice/UTA conceptual ownership
- PHX-060 PCM contract reuse
- exact 10/20/30 ms VAD frame admission
- provider-neutral VadProvider
- deterministic EnergyVadProvider baseline
- default threshold -42 dBFS
- RMS / dBFS algorithm
- score normalized energy, not calibrated probability
- classification is frame-level only
- PHX-064 owns debounce/hangover/conversation/barge-in
- no microphone/wake/STT/TTS/startup/event/tool/permission/audit/model/memory/RAG/API/schema/dependency integration
- future high-quality model VAD may implement same provider interface after explicit dependency authorization

# Completion
Only after every deterministic gate passes:
- mark this ticket DONE
- mark PHX-061 DONE in FEATURE_TICKETS.md
- leave PHX-062+ TODO
- Phase 6 remains INCOMPLETE
- commit exactly:
  `feat: complete PHX-061 VAD`
- push only:
  `feature/PHX-061-vad`
- no PR
- no merge
- no main push
- do not start PHX-062

# Known limitations
Disclose at minimum:
- EnergyVadProvider is an amplitude gate, not semantic speech recognition
- false positives from noise/music and false negatives for quiet speech are expected
- no adaptive noise floor
- no speaker discrimination
- no debounce/hangover
- no speech segment aggregation
- no microphone capture
- no wake coupling
- no STT/TTS
- no conversation/barge-in
- no startup health
- fixed PHX-060 audio format required
- only exact 10/20/30 ms frames accepted
- caller owns chunking/timing
- score is not calibrated probability
- future higher-quality VAD requires separate approved adapter/dependency

# Next
PHX-062 — STT provider. Do not start it.
