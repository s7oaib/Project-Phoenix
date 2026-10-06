# PHX-062 — STT provider

Status: DONE
Phase: 6 — Voice

## Objective
Implement PHOENIX's provider-neutral speech-to-text boundary over caller-supplied PHX-060 PCM evidence.

PHX-062 establishes:

1. strict immutable STT capability/request/transcript contracts,
2. bounded in-memory audio assembly from PHX-060 `PcmAudioFrame`,
3. a provider-neutral async `SttProvider` interface with lifecycle/cancellation semantics,
4. deterministic evidence that future local STT engines can implement the same interface without changing PHX-064 conversation contracts.

PHX-062 does **not**:
- access a microphone,
- record audio,
- implement wake-word detection,
- implement VAD,
- implement TTS,
- implement conversation state or barge-in,
- initialize PHOENIX,
- publish voice events,
- add a concrete Whisper/Vosk/faster-whisper engine,
- add or download an STT model,
- add a runtime dependency.

The frozen project requires local-first STT where practical, but no STT engine/model dependency is currently authorized in `docs/25_DEPENDENCIES.md`. PHX-062 therefore freezes the provider boundary first. A future concrete adapter must receive separate dependency/model authorization rather than being silently promoted here.

## Frozen authority
- Conceptual owner internal ID: `voice`.
- Display alias: UTA.
- Alias text grants no authority.
- PHOENIX remains supreme orchestrator.
- PHX-060 owns canonical 16 kHz mono S16LE PCM evidence.
- PHX-061 owns frame-level VAD only.
- PHX-062 owns transcription provider contracts only.
- PHX-063 owns TTS.
- PHX-064 owns active conversation, segment collection, follow-up behavior and barge-in.
- PHX-065 owns Rise-Phoenix startup health flow.
- PermissionEngine remains authoritative for privileged actions.
- STT output is transcription evidence, not permission/authentication evidence.
- EventType remains exactly 32.

## Dependency decision
Expected dependency changes: **NONE**.

Do NOT add:
- faster-whisper
- openai-whisper
- whisper
- Vosk
- SpeechRecognition
- CTranslate2
- torch
- torchaudio
- numpy
- scipy
- librosa
- PyAV
- sounddevice
- pyaudio
- portaudio
- cloud speech SDKs

`pyproject.toml` and `uv.lock` remain unchanged.

## Recommended implementation
Prefer:

- `phoenix/voice/stt.py`
- `tests/test_stt_provider.py`

If the existing live package-layout protections make a flat `stt.py` unsafe, use:

- `phoenix/voice/stt/__init__.py`

while preserving the public import path `phoenix.voice.stt`.

Expected docs/status:
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- this ticket

No prior production module should need modification.

# Part A — STT error vocabulary

## SttProviderErrorCode
Freeze exactly:

- `invalid_input`
- `invalid_response`
- `unsupported_audio`
- `unsupported_language`
- `unavailable`
- `timeout`
- `busy`
- `closed`
- `wrong_event_loop`
- `cleanup_failure`

## SttProviderError
Public exception text contains safe code/category only.

Never include:
- PCM bytes,
- base64 audio,
- raw transcript text,
- language text supplied by provider,
- local model path,
- device name,
- raw backend exception,
- environment,
- credentials.

# Part B — STT capabilities

## SttCapabilities
Create frozen, extra-forbidden `SttCapabilities` with exactly:

- `provider_id`
- `sample_rate_hz`
- `channels`
- `sample_width_bytes`
- `encoding`
- `supports_language_hint`
- `supports_language_detection`
- `supports_cancellation`

Rules:
- provider ID strict normalized nonempty control-free string
- case-preserving
- <=256
- exact V1 audio format:
  - sample_rate_hz = 16000
  - channels = 1
  - sample_width_bytes = 2
  - encoding = `pcm_s16le`
- all support fields strict bool

Capabilities are technical evidence only.

They do not:
- select a provider,
- prove accuracy,
- prove model availability,
- prove language support,
- grant permission.

Provide exact provider-correlation helper equivalent to:

`capabilities.validate_provider(provider_id)`

# Part C — language token

## Language-token semantics
STT needs optional language metadata without freezing one backend's language taxonomy.

Use a small pure helper/validator for optional language tokens:

- ordinary string only
- outer whitespace normalized
- nonempty when present
- control/NUL-free
- case-preserving
- <=32 characters
- no semantic normalization
- no locale lookup
- no language-name translation

Examples such as `en`, `en-US`, `hi`, `kn` may be admitted, but the contract does not claim a particular provider supports them.

No provider may invent language support from the token shape alone.

# Part D — STT request

## SttRequest
Create frozen, extra-forbidden `SttRequest` with exactly:

- `operation_id`
- `frames`
- `language_hint`

### operation_id
- strict normalized nonempty control-free string
- case-preserving
- <=256
- UUID4 default acceptable

### frames
- immutable tuple
- 1..256 exact PHX-060 `PcmAudioFrame` snapshots
- every nested frame exact-revalidated

Require:
- all stream IDs exactly equal,
- sequences strictly contiguous +1,
- timestamps nondecreasing,
- no duplicate/gap/reorder,
- total decoded PCM bytes <= 960000 bytes.

The 960,000-byte bound equals at most 30 seconds of PHX-060 16 kHz mono signed-16-bit PCM.

Do not:
- pad,
- truncate,
- resample,
- rechunk,
- downmix,
- synthesize missing frames,
- repair timestamps.

### language_hint
- optional language token
- `None` means caller does not constrain language
- no default locale/environment inference

## Request privacy
No:
- model ID,
- model path,
- beam size,
- temperature,
- hotwords,
- initial prompt,
- translation flag,
- task flag,
- device ID,
- microphone handle,
- API key,
- callback,
- tool ID,
- permission,
- risk,
- conversation state.

Provider-specific configuration belongs to the future concrete adapter constructor.

## In-memory byte access
Provide deliberate pure helper/method equivalent to:

`pcm_bytes_for_provider() -> bytes`

Rules:
- exact-revalidate the stored request,
- concatenate decoded frame bytes in frame order,
- return exact raw S16LE bytes,
- no caching,
- no file creation,
- no WAV container,
- no logging,
- no persistence,
- no network.

Provide pure duration evidence equivalent to:

`audio_duration_seconds`

calculated exactly from decoded byte count / 32000.0.

This is caller/request evidence only.

# Part E — STT transcript

## SttTranscript
Create frozen, extra-forbidden `SttTranscript` with exactly:

- `operation_id`
- `provider_id`
- `stream_id`
- `start_sequence`
- `end_sequence`
- `text`
- `language`
- `language_confidence`

### IDs / sequence
- exact strict IDs
- start/end strict nonnegative ints
- bool rejected
- end >= start

### text
- strict ordinary Unicode
- preserve exact provider text
- NUL rejected
- <=20000 characters
- empty string allowed for valid no-speech/no-transcript evidence
- `repr=False`
- no strip/case/punctuation normalization

### language
- optional language token
- exact provider-reported metadata
- no truth/authentication claim

### language_confidence
- optional ordinary float
- bool/int rejected
- finite
- 0..1 inclusive
- if present, language must also be present
- if language is absent, confidence must be None

Confidence is provider-local evidence only.

It is NOT guaranteed:
- calibrated probability,
- speaker identity confidence,
- permission,
- transcription correctness.

## Transcript correlation
Provide equivalent:

`transcript.validate_request(request, provider_id)`

Require exact:
- operation_id,
- provider_id,
- stream_id,
- start_sequence,
- end_sequence.

start/end equal first/last request frame sequence.

If request.language_hint is non-None:
- transcript language may be None if provider does not perform language reporting,
- if transcript language is present, do NOT require exact equality unless the provider contract explicitly promises that later,
- language hint is a request hint, not a guaranteed result.

Wrong identity/range correlation -> `invalid_response`.

No:
- speaker/user identity,
- authentication,
- permission,
- action,
- wake detection,
- VAD result,
- conversation-state transition,
- task success,
- startup health.

# Part F — SttProvider

## Public API
Define provider-neutral async ABC:

- read-only `provider_id: str`
- read-only `active_operation_ids: tuple[str, ...]`
- `async capabilities() -> SttCapabilities`
- `async transcribe(request: SttRequest) -> SttTranscript`
- `async cancel(operation_id: str) -> CancellationResult`
- `async aclose() -> None`

Reuse existing neutral:
- `CancellationResult`
- `CancellationStatus`

## Provider constructor
Base constructor accepts only strict normalized provider ID.

Construction performs no:
- model import/load,
- microphone access,
- filesystem model discovery,
- environment lookup,
- network,
- DB,
- thread/task.

## active_operation_ids
- immutable sorted tuple
- observational only
- no task handles
- no authority

The ABC imposes no global app-wide provider-selection policy.

## capabilities
Consumer must exact-revalidate returned evidence and provider correlation.

No capability may be inferred from provider name.

## transcribe
Concrete adapter contract:
- consumes only caller-supplied SttRequest,
- exact-revalidates request,
- returns exact correlated SttTranscript,
- no implicit retry,
- no alternate provider,
- no microphone capture,
- no automatic VAD,
- no automatic TTS,
- no conversation-state mutation,
- no tool execution.

## cancel
Operation-scoped only.

ACCEPTED means cancellation request acknowledged, not proof native inference stopped.

No global shutdown.

## aclose
Explicit adapter resource cleanup.

Base ABC allocates no runtime resources.

# Part G — consumer helpers

Provide small pure exact-consumer helpers only if they improve testability, for example:

- exact capability revalidation/provider correlation,
- exact transcript revalidation/request correlation,
- exact cancellation-result revalidation/provider+operation correlation.

Do not create provider routing/fallback logic.

# Part H — no concrete STT engine

PHX-062 adds NO production:

- FasterWhisperSttProvider,
- WhisperSttProvider,
- VoskSttProvider,
- cloud STT provider,
- fake/mock provider.

Tests may define deterministic fake SttProvider subclasses.

Reason:
- no STT runtime/model dependency is currently frozen in authoritative dependency docs,
- dependency/model promotion requires an explicit later decision.

Do not dynamically import optional STT packages in production in this ticket.

# Part I — no microphone/device

No production import/use of:
- sounddevice,
- pyaudio,
- portaudio,
- WASAPI/Core Audio microphone capture,
- device enumeration,
- ffmpeg recording,
- wave device input.

PHX-062 consumes caller-supplied PCM only.

# Part J — PHX-060 / PHX-061 separation

PHX-062 may reuse `PcmAudioFrame` only.

Do NOT:
- call WakeWordProvider,
- modify DEFAULT_WAKE_PHRASE,
- invoke EnergyVadProvider,
- require VadDecision,
- perform speech/silence segmentation.

PHX-064 owns deciding which collected frames constitute an utterance.

# Part K — no TTS / conversation

Do not:
- synthesize speech,
- play audio,
- manage listening/thinking/speaking states,
- implement follow-up timeout,
- implement sleep command,
- interrupt TTS,
- implement barge-in.

PHX-063/064 remain separate.

# Part L — transcription trust boundary

STT output is transcription evidence.

It is not:
- authenticated user identity,
- a PermissionGrant,
- a UserPermissionDecision,
- a ToolDefinition,
- proof the user intended an action,
- proof a privileged confirmation occurred.

A later PHX-064/orchestration layer may treat transcript text as user conversational input, but all privileged actions must still cross normal permission policy.

Do not call PromptInjectionGuard automatically in PHX-062.

Do not automatically execute copied commands from transcript text.

# Part M — no EventBus / tools / audit

No EventBus.

Do not publish:
- voice.listening
- voice.speaking
- voice.interrupted.

No new EventType.

No:
- ToolRegistry,
- PermissionEngine,
- AuditTrail.

EventType remains exactly 32.

# Part N — no model-provider coupling

STT is not a PHX language-model `ModelProvider`.

Do not import/call:
- OllamaProvider,
- ColibriProvider,
- ModelLifecycleManager,
- AgentModelRouter.

No LLM generation.

INV-023 is unaffected because no concrete STT inference runtime is added.

# Part O — no memory/RAG/secrets

Do not:
- persist transcript,
- write PUDDING memory,
- ingest transcript as document,
- embed/vectorize,
- call retrieval/citations,
- resolve SecretStore,
- reveal SecretMaterial,
- inspect environment API keys.

Transcript persistence decisions belong to later caller flows under normal memory rules.

# Part P — no persistence/API/frontend

No:
- SQLAlchemy transcript model,
- migration,
- transcript history,
- audio history,
- file cache,
- FastAPI STT endpoint,
- WebSocket PCM endpoint,
- Tauri microphone command,
- React voice UI,
- Zustand conversation state.

Alembic head remains:
`0005_audit_events`.

# Part Q — side-effect isolation

Importing/constructing/validating:
- STT contracts,
- SttProvider fake subclasses,
- pure audio assembly helpers

must perform zero:
- microphone/device I/O,
- filesystem,
- DB,
- network,
- subprocess,
- thread/task,
- EventBus,
- model/tool/permission work,
- environment-secret lookup.

Calling `SttRequest.pcm_bytes_for_provider()` performs only:
- exact revalidation,
- base64 decode,
- bounded byte concatenation.

No external side effect.

# Focused tests

Create:

`tests/test_stt_provider.py`

Use:
- deterministic synthetic PHX-060 frames,
- exact base64/hash evidence,
- fake SttProvider A/B,
- no real model/microphone/network.

At minimum cover:

## errors/language
1. exact error vocabulary
2. safe error strings
3. foreign/copied error enum rejected
4. language token None accepted
5. simple language token accepted
6. outer whitespace normalized
7. case preserved
8. blank rejected
9. control/NUL rejected
10. >32 rejected
11. object/bytes rejected
12. no locale/environment lookup

## capabilities
13. exact eight fields
14. frozen
15. extras rejected
16. strict provider ID
17. exact 16000
18. exact mono
19. exact two-byte
20. exact pcm_s16le
21. strict language-hint bool
22. strict language-detection bool
23. strict cancellation bool
24. exact provider correlation
25. wrong provider rejected
26. stored-value/subclass/extra attacks rejected
27. no model/authority fields

## request
28. exact three fields
29. frozen/extras
30. operation ID default/strict
31. one frame accepted
32. 256 frames structurally admitted when total bytes fit
33. zero frames rejected
34. >256 rejected
35. exact nested PcmAudioFrame revalidation
36. same stream required
37. contiguous sequence required
38. duplicate sequence rejected
39. gap rejected
40. reorder rejected
41. nondecreasing timestamps
42. decreasing timestamps rejected
43. <=960000 total bytes accepted
44. >960000 rejected
45. no truncation/repair
46. language hint None
47. language hint exact
48. no model/path/beam/temp/hotword/prompt/task/device/permission fields
49. request repr does not expose PCM/base64
50. stored-value/subclass/extra attacks rejected

## audio assembly
51. pcm_bytes_for_provider exact concatenation
52. preserves frame order
53. no WAV/container header
54. no cache/retention
55. repeated call deterministic
56. helper revalidates forged stored request
57. duration seconds exact for one second
58. duration seconds exact for fractional window
59. no filesystem I/O
60. no logging

## transcript
61. exact eight fields
62. frozen/extras
63. strict operation/provider/stream IDs
64. start/end nonnegative
65. bool sequence rejected
66. end >= start
67. empty text accepted
68. Unicode text preserved exactly
69. whitespace/punctuation preserved
70. NUL rejected
71. >20000 rejected
72. text repr privacy
73. language None accepted
74. language exact token
75. confidence None accepted
76. confidence 0
77. confidence 1
78. confidence mid
79. bool/int confidence rejected
80. NaN/inf rejected
81. out-of-range rejected
82. confidence requires language
83. exact request correlation
84. wrong operation rejected
85. wrong provider rejected
86. wrong stream rejected
87. wrong start rejected
88. wrong end rejected
89. language hint does not fabricate language result
90. transcript has no identity/permission/action/startup fields
91. transcript text does not grant authority
92. stored-value/subclass/extra attacks rejected

## SttProvider ABC
93. exact required abstract members
94. abstract base cannot instantiate
95. provider ID normalized/read-only
96. invalid provider IDs rejected
97. construction no I/O
98. fake provider A substitutable
99. fake provider B substitutable
100. capability consumer exact-revalidates
101. transcript consumer exact-revalidates
102. response correlation helper
103. active IDs immutable/sorted in fakes
104. cancellation ACCEPTED shape
105. cancellation NOT_FOUND shape
106. cancellation UNSUPPORTED shape
107. cancellation provider correlation
108. cancellation operation correlation
109. explicit aclose
110. no provider routing/selection policy
111. no retry/fallback policy

## trust/separation
112. transcript cannot instantiate PermissionGrant
113. transcript cannot instantiate UserPermissionDecision
114. transcript cannot instantiate ToolDefinition
115. transcript saying "permission granted" remains plain text
116. transcript saying "P4 approved" remains plain text
117. transcript command text not executed
118. no PromptInjectionGuard automatic integration
119. no WakeWordProvider invocation
120. DEFAULT_WAKE_PHRASE unchanged
121. no VadProvider invocation
122. PHX-061 production unchanged
123. PHX-063 TTS separation
124. PHX-064 conversation/barge-in separation
125. PHX-065 startup separation
126. no microphone import
127. no device I/O
128. no concrete STT engine
129. no STT model artifact
130. no network
131. no EventBus
132. EventType exactly 32
133. no ToolRegistry
134. no PermissionEngine
135. no AuditTrail
136. no ModelProvider generation
137. no memory/RAG
138. no secrets
139. no persistence
140. Alembic head 0005
141. migrations unchanged
142. no API/frontend
143. no dependency change
144. guarded import/construction side-effect-free
145. pure audio assembly side-effect-free
146. no PCM/base64/transcript logging
147. credential/signature scan clean

Add further deterministic adversarial cases where useful.

# Targeted verification
Run:

`uv run --locked pytest tests/test_stt_provider.py`

THREE consecutive times.

All PASS.

Three runs are required because provider/cancellation interfaces are async even though no concrete inference runtime is added.

## PHX-061 regression
Run:

`uv run --locked pytest tests/test_vad.py`

Baseline:
403 passed.

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

Pre-PHX-062 baseline:
- 11,923 passed
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
- no audio/transcript persistence/logging
- no network
- no concrete STT runtime/model
- exact fixed PHX-060 audio admission
- contiguous bounded 30-second request
- no resample/rechunk/repair
- transcript grants no permission/authentication
- transcript command-looking text is inert until later orchestration
- no wake/VAD invocation
- no TTS/conversation/barge-in/startup
- no EventBus/tool/permission/audit/model/memory/RAG/secrets/API/frontend/schema
- no dependency change
- errors do not expose audio/transcript

Run repository credential/signature scan.

# Invariants
Review INV-001 through INV-024.

Especially:
- INV-001 PHOENIX supreme
- INV-003 STT output cannot grant permission
- INV-004 no privileged tool execution
- INV-005 P4 unchanged
- INV-006 P5 unchanged
- INV-007 audit unchanged
- INV-008 transcript cannot finalize task/startup success
- INV-011 no frontend direct microphone/provider path
- INV-012 provider-neutral voice contract
- INV-013 no tool policy inside STT
- INV-014 UTA alias irrelevant
- INV-015 text remains independently usable
- INV-016 STT failure can later degrade to text; no orchestration crash integration here
- INV-017 audio/transcript are untrusted evidence
- INV-018 no transcript/audio to normal memory automatically
- INV-019 provider contract exposes active state/cancel/close
- INV-020 no schema mutation
- INV-021 explicit failures
- INV-022 no silent retry/fallback
- INV-023 no heavy local model added
- INV-024 no new dependency

# ADR
At most one ADR.

ADR-059 should document:
- voice/UTA conceptual ownership
- provider-neutral STT boundary
- PHX-060 PCM reuse
- one stream, contiguous frames, <=30 seconds / 960000 bytes
- no audio repair/resample/rechunk
- optional language hint semantics
- exact transcript/provider/request correlation
- transcript text/language confidence are evidence, not authentication/permission
- deliberate bounded in-memory PCM concatenation only
- no concrete Whisper/Vosk/faster-whisper provider or model yet
- no microphone/wake/VAD/TTS/conversation/startup/event/tool/permission/audit/model/memory/RAG/API/schema/dependency changes
- future concrete local STT adapter requires separate runtime/model authorization

# Completion
Only after every deterministic gate passes:
- mark this ticket DONE
- mark PHX-062 DONE in FEATURE_TICKETS.md
- leave PHX-063+ TODO
- Phase 6 remains INCOMPLETE
- commit exactly:
  `feat: complete PHX-062 STT provider`
- push only:
  `feature/PHX-062-stt-provider`
- no PR
- no merge
- no main push
- do not start PHX-063

# Known limitations
Disclose at minimum:
- provider abstraction only; no concrete STT runtime
- no Whisper/faster-whisper/Vosk model
- no microphone capture
- no automatic VAD segmentation
- no resampling/audio conversion
- fixed PHX-060 16 kHz mono S16LE input
- <=30 seconds per request
- no streaming partial transcripts
- no word timestamps
- no translation
- no diarization
- language metadata is provider-local evidence
- transcription is not authentication or permission
- no conversation/barge-in/startup integration
- future local STT engine requires explicit approved dependency/model artifact

# Next
PHX-063 — TTS provider. Do not start it.

## Completion evidence — 2026-10-07

Implemented only `phoenix/voice/stt/__init__.py` and
`tests/test_stt_provider.py`; completion records are this ticket,
FEATURE_TICKETS.md and ADR-059. The explicitly authorized package layout keeps
`phoenix.voice.stt` public while preserving PHX-060's top-level layout gate and
every previous production/test file. No dependency or model artifact changed.
Owner is neutral `voice`; UTA is display metadata; PHOENIX remains supreme.

Preparation verified a clean official D:\Project-Phoenix checkout, correct
origin, feature/PHX-062-stt-provider and exact preparation HEAD
463b04515ed39c45d7e9d40074e32933d7f20295. Main was
d31f5e6b82e972104000678025140a4a656d98f2, with 0 behind / 1 ahead.
Required contracts, relevant docs, full ticket, and existing code/tests were
reviewed. No architecture ambiguity required reinterpretation.

### Contract and acceptance evidence

- Exact ten-code safe errors; only canonical enum members accepted; exception
  messages/chains never expose private input or backend diagnostics.
- Frozen extra-forbidden exact eight-field capabilities, three-field request
  and eight-field transcript. Stored snapshots, subclasses, missing fields,
  injected extras and collisions are revalidated and rejected when forged.
- PHX-060 PCM reused unchanged: 16000 Hz, mono, two-byte pcm_s16le. Requests
  detach 1..256 exact frames, require one stream, contiguous ordered sequences
  and nondecreasing UTC timestamps, and reject over 960000 decoded bytes.
  No repair, resample, rechunk, padding, truncation or automatic VAD exists.
- Raw bytes concatenate exactly in source order, without a container, cache,
  logging or I/O. Duration is decoded bytes / 32000.0; fractional, 0.02,
  1.0 and 30.0 second boundaries pass.
- Optional language tokens are ordinary UTF-8-valid strings, trimmed only at
  the outside, 1..32 characters, control-free, preserving case/Unicode. No
  locale lookup, taxonomy, provider selection or language guarantee is inferred.
- Transcript text preserves exact Unicode/whitespace, admits empty evidence,
  is capped at 20000 characters, rejects NUL/surrogates and stays out of repr.
  Optional ordinary finite float confidence 0..1 requires a reported language.
  Hint never fabricates or constrains reported language/confidence.
- Response binds exact operation/provider/stream/first/last sequence values.
  Text/language/confidence grant no authentication, speaker identity, approval,
  task/startup success or action. Actual PermissionGrant/UserPermissionDecision/
  ToolDefinition validation rejects transcript objects and dumps.
- Pure async ABC exposes read-only identity, sorted immutable observational
  active IDs, capabilities/transcribe/cancel/aclose. Two independent test-only
  adapters prove substitution; scoped ACCEPTED cancellation leaves another
  request active. NOT_FOUND/UNSUPPORTED and exact cancellation correlation pass.
  ACCEPTED acknowledges a request, without proving native inference cessation.
- Static allowlists, guarded uncached module execution and a fresh interpreter
  exercise validation/JSON/PCM assembly and inert command-looking output with
  file/DB/socket/DNS/process/thread/task/log/device/environment/project-integration
  paths blocked. No concrete engine, runtime discovery or production fake exists.

The ticket's 147 required categories are covered by 414 parametrized cases:
1..12 errors/language; 13..27 capabilities; 28..50 request; 51..60 PCM assembly;
61..92 transcript; 93..111 ABC/fakes/consumers/cancellation; 112..147 trust,
separation, isolation, protected-source and credential/signature checks.
All acceptance criteria PASS; no prior test was edited or weakened.

### Commands and results

All commands ran from the official checkout. External logs/JUnit XML are under
C:\Users\shoai\.codex\validation\PHX-062-20261007; package artifacts are in
its dist directory. No validation/build artifacts were added to Git.

- `uv run --locked pytest tests/test_stt_provider.py -q`: three consecutive
  PASS results, 414 each (1.62s, 1.45s, 1.49s).
- `uv run --locked pytest tests/test_<suite>.py -q`: all 37 established suites
  ran individually and PASS, matching their baselines:
  vad 403; wake_word_provider 537; prompt_injection_defenses 604;
  urahara_research_flow 492; search_provider 400; screenshot_vision_loop 397;
  windows_ui_automation 493; browser_tool 653; secrets_boundary 402;
  terminal_tool 270; filesystem_tool 649 passed / 16 existing skipped;
  audit_trail 302; permission_engine 926; tool_registry 204;
  memory_schema 244; pudding_memory_service 167; document_ingestion 300;
  vector_store_sqlite 273; retrieval_context 367; provenance_citations 173;
  persistence 12; agent_registry 96; phoenix_orchestrator 101;
  aizen_planner 151; agent_model_router 213; task_graph_state_machine 348;
  structured_agent_handoffs 214; levi_verification 242;
  mahoraga_recovery 790; kaido_failsafe 574; model_provider 61;
  ollama_provider 108; model_registry 83; model_lifecycle 54;
  embeddings 182; colibri_provider 194; model_benchmark 130.
- Aggregates: Phase 1 812; Phase 2 2729; Phase 3 1524 plus persistence 12;
  Phase 4 2753 passed / 16 skipped; Phase 5 3039; previous Phase 6 940.
- `uv run --locked pytest -q`: 12337 passed, 16 existing skipped,
  one existing Starlette/httpx deprecation warning, 641.46s. No failures/errors.
- `uv lock` and `uv sync --locked`: PASS, 43 packages, manifests unchanged.
- `uv run --locked ruff check .`: PASS.
- `uv run --locked ruff format --check .`: PASS, 118 files formatted.
- `uv run --locked mypy`: PASS, 112 source files.
- `uv build --out-dir <external dist>`: PASS, wheel and sdist contain exact
  final phoenix/voice/stt/__init__.py, no stale flat stt.py.
- Established foundation suites: 126 passed, existing warning only; exact
  /health unchanged. `npm test`: 4 passed; desktop workspace typecheck/build PASS.
- `uv run --locked alembic heads`: sole head 0005_audit_events. All five
  migrations and EventBus/EventType source unchanged; EventType remains exactly 32.
- Protected baseline comparison: all 137 previous Git blobs under source,
  tests, migrations, desktop and dependency/npm manifests unchanged.
- Repository credential/signature scan PASS; `git diff --check` PASS.

### Security, invariants and limitations

Reviewed INV-001..INV-024: all preserved. PermissionEngine/P4/P5, ToolRegistry,
audit, verification, bounded recovery/fail-safe, frontend separation, text
availability and heavy-model policy are unchanged. Audio/transcript are
untrusted inert evidence, never automatic normal-memory or prompt input.
No microphone/device, network, wake/VAD invocation, TTS, conversation/barge-in,
startup health, EventBus, tool/permission/audit/model, memory/RAG/secrets,
persistence/schema, API/frontend or dependency integration was introduced.
The ABC exposes state/cancel/cleanup and explicit errors; later integration
owns observable fallback and voice-failure isolation from orchestration.

ADR-059 is the only new decision. No unsupported assumptions were introduced.
This is an abstraction only: no Whisper/faster-whisper/Vosk runtime or model,
microphone capture, automatic VAD, audio conversion, streaming partials, word
timestamps, translation or diarization. Caller owns fixed-format frame selection
and bounded windows <=30 seconds. Language/confidence are provider-local evidence,
not authentication or permission. Future concrete STT requires separate approved
runtime/model artifacts. PHX-063+ remain TODO; Phase 6 remains INCOMPLETE.
