# PHX-060 — Wake-word provider

Status: TODO
Phase: 6 — Voice

## Objective
Implement PHOENIX's first provider-neutral wake-word boundary for the fixed default wake phrase:

> **Rise, Phoenix.**

PHX-060 establishes:
1. a reusable immutable local PCM-audio frame contract,
2. immutable wake-phrase / wake-request / wake-detection contracts,
3. a provider-neutral async `WakeWordProvider` boundary with capability, cancellation and lifecycle semantics,
4. deterministic evidence that multiple future local wake engines can implement the same interface.

PHX-060 does **not**:
- access a microphone,
- record audio,
- perform VAD,
- perform STT,
- perform TTS,
- manage conversation state,
- implement barge-in,
- initialize PHOENIX after a wake,
- publish voice events,
- add a concrete wake engine,
- add or download a wake model,
- add a runtime dependency.

A later integration may implement the provider boundary with an approved local wake engine/model. No dependency or wake-model artifact is currently authorized in the frozen V1 docs, so PHX-060 MUST NOT invent one.

## Frozen authority
- Conceptual owner internal ID: `voice`.
- Display alias: UTA.
- Alias text grants no authority.
- PHOENIX remains supreme orchestrator.
- A wake detection is **interface evidence only**.
- A wake detection never grants ToolRegistry/PermissionEngine authority.
- A wake detection never proves startup health.
- PHX-065 owns the Rise-Phoenix startup health sequence.
- PHX-064 owns active conversation / barge-in behavior.
- PHX-061 owns VAD.
- PHX-062 owns STT.
- PHX-063 owns TTS.
- Voice failure must remain capable of falling back to text in later integration.
- EventType remains exactly 32; this ticket emits no events.

## Architecture decision
The provider boundary consumes **caller-supplied PCM windows**.

PHX-060 deliberately does not capture audio itself.

This separates:
- device/microphone acquisition,
- wake inference,
- VAD,
- STT,
- conversation orchestration.

It also makes the boundary deterministically testable without using a real microphone.

## Dependency decision
Expected new dependencies: **NONE**.

Do NOT add:
- openwakeword
- pvporcupine
- vosk
- whisper/faster-whisper
- torch
- onnxruntime
- tflite-runtime
- sounddevice
- pyaudio
- portaudio
- numpy
- scipy
- librosa
- webrtcvad
- silero-vad
- any microphone/audio SDK

`pyproject.toml` and `uv.lock` must remain unchanged.

## Recommended implementation
Prefer:

- `phoenix/voice/__init__.py`
- `phoenix/voice/audio.py`
- `phoenix/voice/wake.py`
- `tests/test_wake_word_provider.py`

Expected documentation/status updates:
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- this ticket

No production modification to existing Phase 0–5 subsystems is expected.

# Part A — Canonical PCM audio evidence

## AudioEncoding
Freeze exactly:

- `pcm_s16le`

No WAV container.
No compressed audio.
No MP3/AAC/Opus.
No float PCM.

This is raw signed 16-bit little-endian PCM.

## Canonical V1 audio format
Freeze V1 wake input to:

- sample rate: **16000 Hz**
- channels: **1**
- sample width: **2 bytes**
- encoding: `pcm_s16le`

This is a PHX-060 wake-provider contract, not a claim that every future speech subsystem must forever use the same format.

Later tickets may add explicit conversion at a separate boundary if required.

Do not silently resample/downmix in PHX-060.

## PcmAudioFrame
Create frozen, extra-forbidden `PcmAudioFrame` with exactly:

- `stream_id`
- `sequence`
- `captured_at`
- `sample_rate_hz`
- `channels`
- `sample_width_bytes`
- `encoding`
- `pcm_base64`
- `pcm_sha256`

### stream_id
- strict ordinary string
- outer whitespace normalized
- nonempty
- control-free
- case-preserving
- <=256 chars

### sequence
- strict int
- bool rejected
- 0..2_147_483_647

### captured_at
- required timezone-aware datetime
- normalized UTC
- no wall-clock default
- caller evidence only

### format fields
Require exactly:
- sample_rate_hz = 16000
- channels = 1
- sample_width_bytes = 2
- encoding = `pcm_s16le`

No coercion.

### pcm_base64
- strict ordinary string
- `repr=False`
- canonical RFC4648 standard base64 only
- no whitespace/newlines
- decode with validation
- re-encoding exact equality required
- decoded bytes nonempty
- decoded length even
- decoded length <= 32000 bytes

The 32,000-byte cap corresponds to at most one second of V1 mono 16 kHz signed 16-bit PCM.

PHX-060 does not require a frame to be exactly one second.

### pcm_sha256
- exact lowercase SHA-256 of decoded PCM bytes

### convenience
A deliberate method/property such as:

`pcm_bytes_for_provider() -> bytes`

is allowed.

It:
- returns exact decoded bytes,
- performs no I/O,
- grants no authority,
- must not appear in repr/logs automatically.

No mutable buffer retention/caching is required.

## Audio privacy
Audio evidence is potentially sensitive.

PHX-060 MUST NOT:
- log PCM/base64,
- persist PCM,
- store PCM in normal memory/RAG,
- write PCM to disk,
- publish PCM through EventBus,
- send PCM over network.

# Part B — Wake phrase

## WakePhrase
Create frozen, extra-forbidden `WakePhrase` with exactly:

- `phrase_id`
- `text`

Rules:

### phrase_id
- strict normalized nonempty control-free string
- case-preserving
- <=128

### text
- strict meaningful Unicode
- outer whitespace normalized only
- internal punctuation/case preserved
- NUL/control-free
- <=128

Provide immutable constant:

`DEFAULT_WAKE_PHRASE`

with exactly:

- phrase_id = `rise_phoenix`
- text = `Rise, Phoenix`

The punctuation/case is product-display/config evidence only.

A provider may support custom future phrases via its capability contract.

PHX-060 does not train a custom phrase model.

# Part C — Wake provider capability

## WakeWordCapabilities
Create frozen, extra-forbidden `WakeWordCapabilities` with exactly:

- `provider_id`
- `supported_phrases`
- `sample_rate_hz`
- `channels`
- `sample_width_bytes`
- `encoding`
- `supports_cancellation`

Rules:
- provider ID strict normalized <=256
- supported_phrases immutable tuple 1..32
- every nested WakePhrase exact-revalidated
- phrase IDs unique
- exact duplicate phrase text rejected
- V1 audio format fields exactly 16000 / 1 / 2 / pcm_s16le
- supports_cancellation strict bool

Capabilities are technical evidence only.

They:
- do not choose a provider,
- do not grant permission,
- do not prove availability,
- do not start microphone capture.

# Part D — Wake inference request

## WakeWordRequest
Create frozen, extra-forbidden `WakeWordRequest` with exactly:

- `operation_id`
- `phrase`
- `frames`

### operation_id
- strict normalized nonempty control-free string
- case-preserving
- UUID4 default acceptable
- <=256

### phrase
- exact WakePhrase

### frames
- immutable tuple
- 1..32 frames
- every PcmAudioFrame exact-revalidated

Require:
- all frame stream IDs exactly equal,
- sequence numbers strictly contiguous increasing by 1,
- captured_at values nondecreasing,
- no duplicate frame object/evidence sequence,
- total decoded PCM bytes <= 96000 bytes.

The 96,000-byte request limit corresponds to at most 3 seconds of V1 wake audio.

Do not:
- resample,
- reorder,
- concatenate into a file,
- infer missing sequence numbers,
- repair timestamps,
- silently truncate.

## Request privacy
No request text/audio may be logged.

No provider-specific options.

No:
- threshold override,
- model path,
- device ID,
- API key,
- microphone handle,
- callback,
- permission/tool fields.

Provider-specific model/threshold configuration belongs to the future concrete adapter, not this neutral request.

# Part E — Wake detection evidence

## WakeWordDetection
Create frozen, extra-forbidden `WakeWordDetection` with exactly:

- `operation_id`
- `provider_id`
- `phrase_id`
- `stream_id`
- `start_sequence`
- `end_sequence`
- `detected`
- `score`

Rules:
- exact strict IDs
- start/end strict nonnegative ints
- end >= start
- detected strict bool
- score strict finite float 0..1
- bool rejected for score
- score is provider-reported detector evidence only
- score is NOT calibrated probability unless a concrete provider explicitly documents that later

No:
- transcript,
- user identity,
- speaker identity,
- command text,
- permission,
- action,
- startup success,
- health status,
- trust flag.

## Detection correlation
Provide deterministic helper/method equivalent to:

`detection.validate_request(request, provider_id)`

Require exact:
- operation_id
- provider_id
- phrase_id
- stream_id
- start_sequence
- end_sequence

The start/end sequence must equal first/last request frame sequence.

Do not infer detection truth from score independently of provider's detected flag.

A contradictory provider response fails explicitly.

# Part F — WakeWordProvider

## Public API
Define provider-neutral async ABC:

- read-only `provider_id: str`
- read-only `active_operation_ids: tuple[str, ...]`
- `async capabilities() -> WakeWordCapabilities`
- `async detect(request: WakeWordRequest) -> WakeWordDetection`
- `async cancel(operation_id: str) -> CancellationResult`
- `async aclose() -> None`

Reuse neutral existing:
- `CancellationResult`
- `CancellationStatus`

## Provider ID
Provider constructor accepts only one strict normalized provider ID.

Construction:
- performs no I/O,
- starts no thread/task,
- accesses no microphone/device,
- loads no model.

## active_operation_ids
- immutable tuple
- sorted
- observational only
- no task objects
- no authority

The ABC itself does not impose a global application-wide scheduling policy.

Concrete adapters own correct bounded operation state.

## capabilities
Concrete provider must return:
- exact provider ID,
- exact supported phrases,
- exact audio format evidence.

Consumer must exact-revalidate.

PHX-060 tests use fakes only.

## detect
Concrete provider contract:
- consumes only caller-supplied WakeWordRequest
- must not capture audio itself
- returns exact correlated WakeWordDetection or safe explicit error
- must not mutate request/frames
- no implicit retries/fallback provider
- no automatic conversation/startup action

## cancel
Request-scoped only.

Accepted cancellation:
- acknowledgement, not proof inference stopped.

No global shutdown.

## aclose
Explicit provider resource cleanup.

The neutral ABC performs no resource allocation itself.

# Part G — Wake provider errors

## WakeWordProviderErrorCode
Freeze exactly:

- `invalid_input`
- `invalid_response`
- `unsupported_phrase`
- `unsupported_audio`
- `unavailable`
- `timeout`
- `busy`
- `closed`
- `wrong_event_loop`
- `cleanup_failure`

## WakeWordProviderError
Public exception string:
- code/category only.

Never include:
- PCM bytes,
- base64 audio,
- audio hash if not required for safe correlation,
- local model path,
- device name,
- raw backend exception,
- environment,
- credentials,
- transcript.

No `cancelled` code is required because asyncio cancellation and neutral CancellationResult already exist; if an implementation needs an explicit provider cancellation failure later it must be added through an authority update, not invented in PHX-060.

# Part H — Default phrase interoperability

## DEFAULT_WAKE_PHRASE evidence
Focused tests must prove:

`DEFAULT_WAKE_PHRASE.phrase_id == "rise_phoenix"`

and:

`DEFAULT_WAKE_PHRASE.text == "Rise, Phoenix"`

Do not alter product phrase to:
- "Hey Phoenix"
- "Phoenix"
- "Rise Phoenix"
- punctuation/case variants.

This ticket does not implement fuzzy phrase text matching.

The actual future wake engine is responsible for acoustic detection.

# Part I — No wake action yet

A WakeWordDetection with `detected=True` MUST NOT directly:

- initialize PHOENIX,
- emit "Wake command acknowledged...",
- run health checks,
- move voice state,
- publish `voice.listening`,
- start STT,
- activate microphone capture,
- call orchestrator,
- call tools,
- grant permission.

PHX-065 owns startup health flow.

PHX-064 owns conversation state.

# Part J — No VAD / STT / TTS

PHX-060 MUST NOT implement:

## PHX-061
- speech/silence segmentation,
- speech-start/end decisions,
- VAD probabilities,
- VAD model.

## PHX-062
- transcription,
- language detection,
- Whisper/Vosk,
- text decoding.

## PHX-063
- speech synthesis,
- audio playback,
- voice profiles,
- Coqui XTTS.

No imports from future voice modules.

# Part K — No microphone / audio device

PHX-060 production code MUST NOT import/use:
- sounddevice
- pyaudio
- portaudio
- wave device capture
- Windows audio APIs
- WASAPI
- microphone device enumeration
- subprocess audio recorders

No real device I/O in tests.

Device capture belongs to later integration/conversation work.

# Part L — No network

Wake inference boundary is local-first.

PHX-060 neutral code performs no:
- HTTP,
- WebSocket,
- DNS,
- network socket.

No cloud wake provider.

# Part M — No provider dependency

PHX-060 adds no concrete adapter.

Do not add a production fake.

Focused tests may define fake WakeWordProvider subclasses.

This ticket freezes substitutability so a future approved local backend can be added without changing conversation/orchestration contracts.

# Part N — Side-effect isolation

Importing/constructing/validating:
- PcmAudioFrame
- WakePhrase
- WakeWordCapabilities
- WakeWordRequest
- WakeWordDetection
- WakeWordProvider fake subclasses

must not:
- access microphone,
- read environment,
- read/write files,
- access database,
- open network,
- start subprocess,
- start thread/task,
- publish events,
- invoke tools,
- call model provider,
- touch memory/RAG.

# Part O — Authority separation

Wake evidence cannot become:
- PermissionRequest approval,
- UserPermissionDecision,
- PermissionGrant,
- ToolDefinition,
- AuditRecord authority,
- task verification.

Audio data cannot contain executable authority.

Wake score cannot:
- lower risk,
- approve P4,
- override P5,
- disable audit.

No ToolRegistry integration.

No PermissionEngine integration.

No AuditTrail integration.

The provider boundary is interface inference only.

# Part P — Event separation

PHX-060 emits no EventBus event.

Do not use existing:
- voice.listening
- voice.speaking
- voice.interrupted

Those events belong to later runtime/conversation integration.

EventType stays exactly 32.

# Part Q — Persistence / API / frontend

No:
- SQLAlchemy model,
- migration,
- audio history table,
- wake history,
- file cache,
- FastAPI wake endpoint,
- WebSocket audio stream,
- Tauri microphone command,
- React microphone UI,
- Zustand voice state.

Alembic head remains:
`0005_audit_events`.

# Part R — Prompt injection / external data

Raw audio is untrusted input data.

PHX-060 produces no text from audio and does not invoke PromptInjectionGuard.

Do not fabricate transcripts.

A wake detection is not user instruction text.

Later STT text may require its own trust/orchestration treatment.

# Focused tests

Create:

`tests/test_wake_word_provider.py`

Use:
- synthetic byte patterns,
- deterministic base64,
- deterministic fake WakeWordProvider subclasses,
- no real microphone,
- no model runtime,
- no network.

At minimum cover:

## AudioEncoding / frame
1. exact encoding vocabulary
2. PcmAudioFrame exact nine fields
3. frozen
4. extras rejected
5. stream ID normalization
6. blank/control stream ID rejected
7. sequence zero accepted
8. max sequence accepted
9. negative rejected
10. bool sequence rejected
11. captured_at aware required
12. UTC normalization
13. sample rate exactly 16000
14. channels exactly 1
15. sample width exactly 2
16. encoding exactly pcm_s16le
17. canonical base64 accepted
18. malformed base64 rejected
19. base64 whitespace rejected
20. noncanonical base64 rejected
21. decoded empty rejected
22. odd byte length rejected
23. 32000-byte frame accepted
24. >32000 rejected
25. exact SHA-256 required
26. uppercase/wrong hash rejected
27. pcm_base64 repr privacy
28. PCM convenience returns exact bytes
29. JSON roundtrip exact
30. stored-value/subclass/extra attacks rejected

## WakePhrase
31. exact two fields
32. frozen/extras
33. phrase ID normalization/bounds
34. text meaningful
35. text control/NUL rejection
36. text max bound
37. internal punctuation preserved
38. DEFAULT ID exact
39. DEFAULT text exact
40. default immutable

## capabilities
41. exact seven fields
42. frozen/extras
43. provider ID strict
44. 1 supported phrase
45. 32 supported phrases
46. zero rejected
47. 33 rejected
48. duplicate phrase ID rejected
49. duplicate phrase text rejected
50. nested exact revalidation
51. exact 16000/1/2/pcm_s16le
52. strict cancellation bool
53. no authority fields

## WakeWordRequest
54. exact three fields
55. frozen/extras
56. operation ID strict/default
57. exact phrase
58. one frame accepted
59. 32 frames accepted if total budget fits
60. zero frames rejected
61. >32 rejected
62. same stream required
63. contiguous sequence required
64. duplicate sequence rejected
65. reordered sequence rejected
66. nondecreasing timestamps
67. total PCM <=96000
68. >96000 rejected
69. no truncation
70. frame exact revalidation
71. request repr does not dump audio
72. no threshold/model/device fields

## WakeWordDetection
73. exact eight fields
74. frozen/extras
75. provider/operation/phrase/stream IDs strict
76. start/end strict
77. end >= start
78. detected strict bool
79. score zero
80. score one
81. score midrange
82. bool score rejected
83. NaN/inf rejected
84. out-of-range score rejected
85. no transcript field
86. no permission/action/health fields
87. exact request correlation
88. wrong operation rejected
89. wrong provider rejected
90. wrong phrase rejected
91. wrong stream rejected
92. wrong start/end rejected
93. score does not independently override detected

## errors
94. exact error vocabulary
95. safe error string
96. no audio/content in error
97. foreign enum/code rejected where applicable

## WakeWordProvider ABC
98. abstract boundary required members
99. abstract provider cannot instantiate
100. provider ID normalization
101. provider ID invalid rejected
102. provider construction no I/O
103. fake provider A substitutable
104. fake provider B substitutable
105. capabilities exact provider correlation
106. capabilities exact-revalidated
107. detect exact request
108. detect response exact-revalidated
109. detect correlation helper
110. active IDs tuple/sorted in fakes
111. cancellation ACCEPTED shape
112. cancellation NOT_FOUND shape
113. cancellation provider correlation
114. cancellation operation correlation
115. explicit aclose fake
116. no provider selection policy

## default phrase / ownership semantics
117. conceptual owner ID voice documented/tested as constant if exposed
118. UTA alias not required by provider contracts
119. wake phrase exact
120. no fuzzy text matching
121. wake detection grants no permission
122. wake detection does not start PHOENIX

## separation
123. no microphone imports
124. no audio-device I/O
125. no VAD
126. no STT
127. no TTS
128. no conversation state
129. no barge-in
130. no PHX-065 health flow
131. no EventBus
132. EventType exactly 32
133. no ToolRegistry
134. no PermissionEngine
135. no AuditTrail
136. no model providers
137. no PromptInjectionGuard integration
138. no SecretStore
139. no memory/RAG
140. no persistence
141. Alembic head 0005
142. migrations unchanged
143. no API/frontend
144. no network
145. no dependency change
146. guarded import/construction side-effect-free
147. no audio logging
148. credential/signature scan clean

Add further deterministic adversarial cases where useful.

# Targeted verification
Run:

`uv run --locked pytest tests/test_wake_word_provider.py`

All PASS.

No live microphone.
No wake model.
No public network.

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
- secrets: 402
- terminal: 270
- filesystem: 649 passed / 16 skipped
- audit: 302
- permissions: 926
- ToolRegistry: 204

## Phase-3 regressions
Run individually:
- memory schema: 244
- PUDDING: 167
- ingestion: 300
- vector store: 273
- retrieval: 367
- citations: 173
- persistence: 12

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

Pre-PHX-060 baseline:
- 10,983 passed
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
- no concrete wake engine/model
- no dependency promotion
- exact fixed V1 PCM format
- exact audio hashes/base64 admission
- bounded frame/request bytes
- detection evidence carries no text/tool/permission authority
- score does not grant authority
- no ToolRegistry/PermissionEngine/Audit integration
- no VAD/STT/TTS/conversation/startup implementation
- no EventBus/API/frontend/schema
- no model/memory/RAG/secrets integration
- failures privacy-safe

Run credential/signature scan.

# Invariants
Review INV-001 through INV-024.

Especially:
- INV-001 PHOENIX remains supreme
- INV-003 wake/provider output cannot grant permission
- INV-004 no privileged tool execution introduced
- INV-005 P4 unchanged
- INV-006 P5 unchanged
- INV-007 audit unchanged
- INV-008 wake evidence cannot finalize task/startup health
- INV-011 no frontend privileged microphone path
- INV-012 provider-neutral voice contract; no engine leaks into agent definitions
- INV-013 no tool policy inside voice provider
- INV-014 UTA alias removable
- INV-015 text operation remains possible without voice
- INV-016 voice-provider failure must not crash orchestration; integration deferred
- INV-017 audio is untrusted data
- INV-018 no audio/secrets persisted to normal memory
- INV-019 provider contract exposes active state/cancellation
- INV-020 no schema mutation
- INV-021 explicit provider failures
- INV-022 no silent provider fallback
- INV-024 no new dependency

INV-023 heavy local generation is unaffected because PHX-060 adds no model generation.

# ADR
At most one ADR.

ADR-057 should document:
- voice / UTA conceptual ownership
- fixed default phrase `Rise, Phoenix`
- provider-neutral wake boundary
- no concrete engine/model/dependency yet
- microphone capture explicitly outside PHX-060
- canonical 16 kHz mono signed 16-bit little-endian PCM evidence
- base64/hash/bounded frame and request windows
- provider technical capabilities
- exact detection/request correlation
- detector score is provider evidence, not permission/truth
- cancellation acknowledgement semantics
- no wake action/startup sequence yet
- no VAD/STT/TTS/conversation/barge-in
- no EventBus/tool/permission/audit/model/memory/RAG/API/frontend/schema/dependency changes
- future concrete adapter requires separate dependency/model authorization

# Completion
Only after every deterministic gate passes:
- mark this ticket DONE
- mark PHX-060 DONE in FEATURE_TICKETS.md
- leave PHX-061+ TODO
- commit exactly:
  `feat: complete PHX-060 wake word provider`
- push only:
  `feature/PHX-060-wake-word-provider`
- no PR
- no merge
- no main push
- do not start PHX-061

# Known limitations to disclose
At minimum:
- no concrete wake engine
- no trained `Rise, Phoenix` acoustic model
- no microphone capture/device integration
- no VAD/STT/TTS
- no active conversation state
- no wake debounce/refractory behavior
- no barge-in
- no startup health sequence
- fixed wake provider PCM format is 16 kHz mono S16LE
- caller owns audio capture/window construction
- provider score may be uncalibrated
- provider cancellation is cooperative
- no wake event publication yet
- voice failure/text fallback behavior is future integration
- future concrete provider requires explicit approved dependency/model artifact

# Next
PHX-061 — VAD. Do not start it.
