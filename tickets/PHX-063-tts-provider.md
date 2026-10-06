# PHX-063 — TTS provider

Status: TODO
Phase: 6 — Voice

## Objective
Implement PHOENIX's provider-neutral text-to-speech boundary and canonical bounded PCM synthesis evidence.

PHX-063 establishes:

1. immutable voice-profile provenance and capability contracts,
2. immutable TTS request/result contracts,
3. canonical bounded in-memory PCM output evidence,
4. a provider-neutral async `TtsProvider` interface with lifecycle/cancellation semantics,
5. deterministic evidence that future local TTS engines can implement the same interface without changing PHX-064 conversation/barge-in contracts.

PHX-063 does **not**:
- play audio,
- access microphones or speakers,
- implement wake-word detection,
- implement VAD,
- implement STT,
- implement conversation state or barge-in,
- implement fixed-phrase caching,
- initialize PHOENIX,
- publish voice events,
- add Coqui XTTS or any concrete TTS engine,
- add/download voice models or cloning samples,
- add a runtime dependency.

Coqui TTS/XTTS remains an optional candidate in `docs/25_DEPENDENCIES.md`, not a hard dependency. Promotion requires a separate accepted dependency/model decision.

## Frozen authority
- Conceptual owner internal ID: `voice`.
- Display alias: UTA.
- PHOENIX remains supreme.
- TTS output grants no permission/tool authority.
- PHX-060 owns wake PCM input evidence.
- PHX-061 owns VAD.
- PHX-062 owns STT.
- PHX-063 owns synthesis provider contracts only.
- PHX-064 owns active conversation, audio playback, interruption/barge-in and state transitions.
- PHX-065 owns Rise-Phoenix startup health flow.
- EventType remains exactly 32.
- PermissionEngine, ToolRegistry and AuditTrail remain unchanged.

## Voice-rights policy
PHOENIX's primary distributed voice must be original.

Optional distributed profiles may only declare one of:
- original
- licensed
- consented

This contract records declared provenance evidence only. It does not itself prove legal rights or consent validity.

Do not add:
- celebrity/person names as bundled profiles,
- copyrighted character identities,
- impersonation labels,
- scraped cloning samples,
- voice-cloning input,
- biometric identity claims.

## Dependency decision
Expected dependency changes: **NONE**.

Do NOT add:
- TTS / coqui-ai TTS
- XTTS
- torch / torchaudio
- onnxruntime
- numpy
- scipy
- librosa
- sounddevice
- pyaudio
- portaudio
- ffmpeg
- cloud speech SDKs

`pyproject.toml` and `uv.lock` remain unchanged.

## Recommended implementation
Prefer:
- `phoenix/voice/tts/__init__.py`
- `tests/test_tts_provider.py`

Expected docs/status:
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- this ticket

No prior production module should need semantic modification.

# Part A — vocabularies

## TtsAudioEncoding
Freeze exactly:
- `pcm_s16le`

## VoiceProfileProvenance
Freeze exactly:
- `original`
- `licensed`
- `consented`

## TtsProviderErrorCode
Freeze exactly:
- `invalid_input`
- `invalid_response`
- `unsupported_voice`
- `unsupported_language`
- `unavailable`
- `timeout`
- `busy`
- `closed`
- `wrong_event_loop`
- `cleanup_failure`

Public exception strings expose safe code/category only.

Never include:
- synthesis text,
- PCM/base64,
- voice sample data,
- model paths,
- raw backend exception,
- device names,
- environment,
- credentials.

# Part B — language token
Use provider-neutral optional language tokens:
- ordinary string only,
- outer whitespace normalized,
- nonempty when present,
- control/NUL-free,
- case-preserving,
- <=32 chars,
- no locale/environment lookup,
- no semantic taxonomy normalization.

Examples like `en`, `en-US`, `hi`, `kn` may be admitted without claiming provider support.

# Part C — TtsVoiceProfile

Create frozen, extra-forbidden `TtsVoiceProfile` exactly:

- `profile_id`
- `display_name`
- `provenance`
- `languages`
- `is_primary`

## profile_id
- strict ordinary string
- outer whitespace normalized
- nonempty
- control-free
- case-preserving
- <=128

## display_name
- strict Unicode string
- outer whitespace normalized
- meaningful
- NUL/control-free
- <=256
- display evidence only

## provenance
Exact `VoiceProfileProvenance`.

## languages
- immutable tuple
- 1..32 optional-language-token-shaped strings, but None is not allowed in the tuple
- unique exact normalized values
- preserve order
- no provider support inferred beyond declared capability

## is_primary
Strict bool.

## primary policy
Within a capability snapshot:
- exactly one voice profile must have `is_primary=True`,
- that primary profile MUST have provenance `original`.

Optional non-primary profiles may be original/licensed/consented.

No field may contain:
- celebrity identity,
- character identity,
- biometric identity,
- permission,
- trust,
- clone sample,
- speaker embedding.

# Part D — TtsCapabilities

Create frozen, extra-forbidden `TtsCapabilities` exactly:

- `provider_id`
- `voices`
- `sample_rate_hz`
- `channels`
- `sample_width_bytes`
- `encoding`
- `supports_language_hint`
- `supports_cancellation`

Rules:

## provider_id
- strict normalized nonempty control-free string
- case-preserving
- <=256

## voices
- immutable tuple
- 1..64 exact `TtsVoiceProfile` snapshots
- unique profile IDs
- exactly one primary
- primary provenance original
- no duplicate exact profile IDs

## PCM output capability
- sample_rate_hz: strict int, bool rejected, 8000..48000
- channels: exactly 1
- sample_width_bytes: exactly 2
- encoding: exactly `pcm_s16le`

PHX-063 does not silently resample output.

A future audio-output layer consumes the declared format.

## support flags
- supports_language_hint strict bool
- supports_cancellation strict bool

Capabilities are technical/declarative evidence only.

They:
- do not select a provider,
- do not prove voice rights,
- do not prove quality,
- do not grant permission,
- do not start playback.

Provide exact provider correlation equivalent to:
`capabilities.validate_provider(provider_id)`.

# Part E — TtsRequest

Create frozen, extra-forbidden `TtsRequest` exactly:

- `operation_id`
- `text`
- `voice_profile_id`
- `language_hint`

## operation_id
- strict normalized nonempty control-free string
- case-preserving
- <=256
- UUID4 default acceptable

## text
- ordinary Unicode str only
- preserve exact supplied text
- NUL rejected
- must contain at least one non-whitespace character
- <=4096 characters
- `repr=False`
- no strip/case/punctuation rewrite
- UTF-8 encodable

## voice_profile_id
- same strict identifier rules as profile_id
- request references capability evidence only
- reference itself grants no authority

## language_hint
- None or language token
- no OS locale/environment default

## no backend options
No:
- model path
- speaker WAV/sample
- speaker embedding
- cloning prompt
- temperature
- top-k/top-p
- speed/pitch/rate
- style token
- emotion token
- seed
- device
- API key
- callback
- output path
- tool ID
- permission/risk
- conversation state

Provider-specific configuration belongs to the future concrete adapter.

# Part F — request/capability validation

Provide pure validation equivalent to:

`request.validate_capabilities(capabilities)`

Require:
- exact-revalidate request and capabilities,
- requested voice_profile_id exists exactly in capabilities.voices,
- if language_hint is present:
  - capabilities.supports_language_hint must be true,
  - the selected profile's languages must contain that exact normalized token.

Failure categories:
- missing voice -> `unsupported_voice`
- unsupported language/hint -> `unsupported_language`
- malformed capability evidence -> `invalid_response`

Do not auto-select another voice/language.

Do not fall back to primary implicitly.

# Part G — TtsAudio

Create frozen, extra-forbidden `TtsAudio` exactly:

- `operation_id`
- `provider_id`
- `voice_profile_id`
- `sample_rate_hz`
- `channels`
- `sample_width_bytes`
- `encoding`
- `pcm_base64`
- `pcm_sha256`

## identifiers
Strict exact IDs.

## format
- sample_rate_hz strict int 8000..48000
- channels exactly 1
- sample_width_bytes exactly 2
- encoding exactly pcm_s16le

## pcm_base64
- ordinary str
- `repr=False`
- canonical standard RFC4648 base64 only
- no whitespace/newlines
- strict validated decode
- exact re-encode equality
- decoded bytes nonempty
- even byte length
- decoded bytes <= 8_388_608 (8 MiB)

## pcm_sha256
- exact lowercase SHA-256 of decoded PCM bytes

## duration
Provide pure computed:
`duration_seconds`

= decoded_bytes / (sample_rate_hz * channels * sample_width_bytes)

Require:
- >0
- <=120.0 seconds

No cache.

## deliberate byte access
Provide:
`pcm_bytes_for_playback() -> bytes`

It:
- exact-revalidates stored evidence,
- returns exact raw S16LE PCM,
- performs no I/O,
- does not play sound,
- does not cache.

# Part H — audio correlation

Provide equivalent:

`audio.validate_request(request, capabilities)`

Require exact:
- operation_id
- provider_id
- voice_profile_id
- sample_rate_hz
- channels
- sample_width_bytes
- encoding

Also:
- request must validate against capabilities first,
- provider ID must match capabilities,
- returned voice profile equals requested profile,
- returned PCM format exactly equals declared capability format.

Wrong evidence -> `invalid_response`.

Do not infer synthesis correctness from audio hash/duration.

# Part I — TtsProvider

Define provider-neutral async ABC:

- read-only `provider_id`
- read-only `active_operation_ids`
- `async capabilities() -> TtsCapabilities`
- `async synthesize(request: TtsRequest) -> TtsAudio`
- `async cancel(operation_id: str) -> CancellationResult`
- `async aclose() -> None`

Reuse:
- `CancellationResult`
- `CancellationStatus`

## constructor
Strict provider ID only.

Construction performs no:
- TTS model import/load,
- model download,
- speaker/audio-device access,
- filesystem model discovery,
- environment lookup,
- network,
- DB,
- thread/task.

## active_operation_ids
- sorted immutable tuple
- observability only
- no task handles
- no authority

## synthesize
Future adapters:
- consume only caller-supplied TtsRequest,
- exact-revalidate request/capabilities,
- return correlated bounded TtsAudio,
- no automatic retry/fallback,
- no playback,
- no conversation-state transition.

## cancel
Operation-scoped only.

ACCEPTED means cancellation request acknowledged, not proof native inference stopped.

## aclose
Explicit adapter cleanup.

Base ABC owns no runtime resources.

# Part J — no concrete engine

PHX-063 adds NO production:
- CoquiTtsProvider
- XttsProvider
- WindowsSapiProvider
- cloud TTS provider
- fake/mock provider

Tests may define deterministic fake providers.

Do not dynamically import optional TTS packages.

No model artifact or voice sample.

# Part K — fixed phrase cache separation
Do NOT implement fixed phrase caching here.

Do not persist:
- startup phrases,
- synthesized PCM,
- voice output.

PHX-064/065 may later define cache/use policy.

# Part L — audio playback / barge-in separation
PHX-063 synthesizes bytes only.

Do NOT:
- open speaker/audio device,
- play sound,
- interrupt sound,
- publish voice.speaking,
- publish voice.interrupted,
- implement barge-in,
- maintain SPEAKING state.

PHX-064 owns playback/interruption/conversation integration.

# Part M — startup separation
Do not automatically synthesize:
- "Wake command acknowledged..."
- "All agents online..."
- any startup phrase.

PHX-065 owns startup health sequence.

# Part N — prior voice isolation
Do not:
- call WakeWordProvider,
- call VadProvider,
- call SttProvider,
- modify DEFAULT_WAKE_PHRASE,
- modify PHX-060/061/062 production semantics.

# Part O — authority boundary
TTS request/result/profile text is data.

It cannot:
- create PermissionGrant,
- create UserPermissionDecision,
- create ToolDefinition,
- approve P4,
- override P5,
- disable audit,
- finalize task success.

No ToolRegistry.
No PermissionEngine.
No AuditTrail.

# Part P — no EventBus/model/memory/secrets
No EventBus publication.

EventType remains exactly 32.

No:
- ModelProvider/Ollama/Colibri generation,
- PromptInjectionGuard,
- Memory/RAG,
- SecretStore/SecretMaterial,
- environment credential lookup.

# Part Q — no persistence/API/frontend
No:
- SQLAlchemy voice model,
- migration,
- PCM cache/history,
- TTS history,
- voice-profile persistence,
- FastAPI TTS endpoint,
- WebSocket audio endpoint,
- Tauri speaker command,
- React voice UI,
- Zustand conversation state.

Alembic head remains `0005_audit_events`.

# Part R — side-effect isolation
Importing/constructing/validating:
- enums,
- profile/capability/request/audio contracts,
- TtsProvider fake subclasses,
- pure byte/duration helpers

must perform zero:
- microphone/speaker/device I/O,
- filesystem,
- DB,
- network,
- subprocess,
- thread/task,
- EventBus,
- model/tool/permission work,
- environment-secret lookup.

`pcm_bytes_for_playback()` performs bounded decode/revalidation only.

# Focused tests

Create:
`tests/test_tts_provider.py`

Use deterministic synthetic PCM and test-only fake providers.

No real audio device/model/network.

At minimum cover:

## vocabularies/errors
1. exact encoding vocabulary
2. exact provenance vocabulary
3. exact error vocabulary
4. safe error strings
5. copied/foreign enums rejected

## language/profile
6. language None rules where allowed
7. opaque token accepted
8. outer trim/case preserve
9. blank/control/NUL rejected
10. >32 rejected
11. profile exact five fields
12. profile frozen/extras
13. strict profile ID
14. display name strict
15. provenance exact
16. 1 language accepted
17. 32 languages accepted
18. empty languages rejected
19. >32 rejected
20. duplicate languages rejected
21. strict primary bool
22. no identity/permission/clone fields

## capabilities
23. exact eight fields
24. frozen/extras
25. strict provider ID
26. 1 voice accepted
27. 64 voices accepted
28. zero rejected
29. >64 rejected
30. duplicate profile ID rejected
31. exactly one primary required
32. two primaries rejected
33. zero primary rejected
34. primary original accepted
35. primary licensed rejected
36. primary consented rejected
37. optional licensed accepted
38. optional consented accepted
39. nested exact revalidation
40. sample rate 8000 accepted
41. 48000 accepted
42. below/above rejected
43. bool/float sample rate rejected
44. exact mono
45. exact 2 bytes
46. exact pcm_s16le
47. strict support flags
48. provider correlation
49. no authority claim

## request
50. exact four fields
51. frozen/extras
52. operation default/strict
53. text exact preservation
54. whitespace-containing meaningful text accepted
55. blank-only rejected
56. NUL/surrogate rejected
57. 4096 accepted
58. >4096 rejected
59. text repr privacy
60. strict voice profile ID
61. language None/exact
62. no backend options
63. stored/subclass/extra attacks rejected

## request/capability validation
64. exact selected profile accepted
65. missing voice unsupported_voice
66. language hint supported exact token
67. provider lacks hint support -> unsupported_language
68. selected voice lacks language -> unsupported_language
69. no implicit primary fallback
70. no language substitution
71. forged capabilities invalid_response
72. request exact revalidation

## audio
73. exact nine fields
74. frozen/extras
75. strict IDs
76. sample-rate bounds
77. mono exact
78. width exact
79. encoding exact
80. canonical base64 accepted
81. malformed base64 rejected
82. whitespace base64 rejected
83. noncanonical rejected
84. empty decoded rejected
85. odd bytes rejected
86. 8 MiB accepted if duration bound also valid
87. >8 MiB rejected
88. exact lowercase SHA required
89. wrong/uppercase hash rejected
90. pcm repr privacy
91. byte helper exact
92. byte helper revalidates forged evidence
93. duration formula exact
94. >120 sec rejected
95. no playback/cache/I/O

## correlation
96. exact request/capabilities correlation
97. wrong operation rejected
98. wrong provider rejected
99. wrong voice rejected
100. wrong sample rate rejected
101. wrong channels rejected
102. wrong width rejected
103. wrong encoding rejected
104. request unsupported voice propagates
105. no synthesis-truth claim

## provider ABC
106. exact abstract members
107. abstract base cannot instantiate
108. provider ID read-only/strict
109. construction no I/O
110. fake provider A substitutable
111. fake provider B substitutable
112. capability consumer exact revalidation
113. audio consumer exact revalidation
114. active IDs immutable/sorted
115. cancellation ACCEPTED
116. cancellation NOT_FOUND
117. cancellation UNSUPPORTED
118. cancellation provider correlation
119. cancellation operation correlation
120. explicit aclose
121. no routing/retry/fallback policy

## rights/trust
122. exactly one primary original
123. licensed optional profile evidence inert
124. consented optional profile evidence inert
125. provenance does not prove legal rights
126. no celebrity/character bundled profile
127. no clone sample/embedding
128. TTS text cannot instantiate PermissionGrant
129. TTS text cannot instantiate UserPermissionDecision
130. TTS text cannot instantiate ToolDefinition
131. approval-looking text remains inert

## separation
132. no concrete TTS engine
133. no Coqui/XTTS dependency
134. no TTS model artifact
135. no microphone
136. no speaker/audio-device playback
137. no network
138. no wake provider invocation
139. no VAD provider invocation
140. no STT provider invocation
141. PHX-060 unchanged
142. PHX-061 unchanged
143. PHX-062 unchanged
144. no fixed phrase cache
145. PHX-064 conversation/barge-in separation
146. PHX-065 startup separation
147. no EventBus
148. EventType exactly 32
149. no ToolRegistry
150. no PermissionEngine
151. no AuditTrail
152. no ModelProvider
153. no PromptInjectionGuard
154. no memory/RAG
155. no secrets
156. no persistence
157. Alembic head 0005
158. migrations unchanged
159. no API/frontend
160. no dependency changes
161. guarded import/construction side-effect-free
162. PCM/text logging absent
163. credential/signature scan clean

Add further deterministic adversarial cases where useful.

# Targeted verification
Run:
`uv run --locked pytest tests/test_tts_provider.py`

THREE consecutive times.

All PASS.

## PHX-062 regression
`uv run --locked pytest tests/test_stt_provider.py`
Baseline: 414 passed.

## PHX-061 regression
`uv run --locked pytest tests/test_vad.py`
Baseline: 403 passed.

## PHX-060 regression
`uv run --locked pytest tests/test_wake_word_provider.py`
Baseline: 537 passed.

## PHX-055 regression
Baseline: 604 passed.

## PHX-054 regression
Baseline: 492 passed.

## PHX-053 regression
Baseline: 400 passed.

## PHX-052 regression
Baseline: 397 passed.

## PHX-051 regression
Baseline: 493 passed.

## PHX-050 regression
Baseline: 653 passed.

## Phase 4
Run individually:
- secrets 402
- terminal 270
- filesystem 649 passed / 16 skipped
- audit 302
- permissions 926
- ToolRegistry 204

## Phase 3
Run individually:
- memory schema 244
- PUDDING 167
- ingestion 300
- vector store 273
- retrieval 367
- citations 173
- persistence 12

## Phase 2
Run all nine suites individually.
Baseline aggregate: 2,729.

## Phase 1
Run all seven suites individually.
Baseline aggregate: 812.

# Alembic/events
Verify:
- sole head `0005_audit_events`
- migrations 0001–0005 unchanged
- EventType exactly 32
- EventBus/EventType production unchanged

# Full quality
Run:
- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`

Pre-PHX-063 baseline:
- 12,337 passed
- 16 skipped
- one existing warning

Expected dependency changes: NONE.

# Foundation
Run established foundation suites.
Baseline: 126 passed.
Exact `/health` unchanged.

# Desktop
Run:
- `npm test`
- `npm run typecheck --workspace @phoenix/desktop`
- `npm run build --workspace @phoenix/desktop`

Baseline: 4 passed.

# Security review
Confirm:
- no microphone/speaker/device access,
- no audio/text logging/persistence,
- no network,
- no concrete TTS runtime/model/sample,
- rights provenance constrained to original/licensed/consented,
- primary voice declaration must be original,
- provenance is evidence, not legal proof,
- exact bounded canonical PCM result,
- no playback,
- no wake/VAD/STT invocation,
- no conversation/barge-in/startup,
- no EventBus/tool/permission/audit/model/memory/RAG/secrets/API/frontend/schema,
- no dependency change.

Run credential/signature scan.

# Invariants
Review INV-001..INV-024.

Especially:
- INV-001 PHOENIX supreme
- INV-003 TTS data cannot grant permission
- INV-004 no privileged tool execution
- INV-005 P4 unchanged
- INV-006 P5 unchanged
- INV-007 audit unchanged
- INV-008 synthesis cannot finalize task/startup success
- INV-011 no frontend speaker privilege path
- INV-012 provider-neutral voice contract
- INV-013 no tool policy in TTS
- INV-014 UTA alias removable
- INV-015 text remains independently usable
- INV-016 TTS failure can later degrade to text; no orchestration crash integration
- INV-017 synthesis text/audio are data
- INV-018 no audio/text to normal memory automatically
- INV-019 provider exposes active state/cancel/close
- INV-020 no schema mutation
- INV-021 explicit failures
- INV-022 no hidden retry/fallback
- INV-023 no heavy TTS model runtime added
- INV-024 no new dependency

# ADR
At most one ADR.

ADR-060 should document:
- voice/UTA conceptual ownership,
- provider-neutral TTS boundary,
- Coqui XTTS remains optional candidate,
- no concrete TTS runtime/model/sample/dependency yet,
- original primary voice policy,
- optional distributed profiles only original/licensed/consented,
- provenance is declarative evidence, not legal-rights proof,
- request text preserved and private,
- selected voice/language must be explicitly supported,
- bounded canonical mono S16LE result with provider-declared 8–48 kHz rate,
- 8 MiB and 120-second result bounds,
- no playback/fixed-phrase cache/conversation/barge-in/startup,
- no EventBus/tool/permission/audit/model/memory/RAG/secrets/API/schema/dependency changes.

# Completion
Only after every deterministic gate passes:
- mark this ticket DONE,
- mark PHX-063 DONE in FEATURE_TICKETS.md,
- leave PHX-064/065 TODO,
- Phase 6 remains INCOMPLETE,
- commit exactly:
  `feat: complete PHX-063 TTS provider`
- push only:
  `feature/PHX-063-tts-provider`
- no PR,
- no merge,
- no main push,
- do not start PHX-064.

# Known limitations
Disclose at minimum:
- abstraction only; no concrete TTS engine,
- no Coqui/XTTS runtime/model,
- no audio playback,
- no speaker/device integration,
- no streaming/chunked synthesis,
- no fixed phrase cache,
- no voice cloning,
- no bundled themed/character voices,
- provenance evidence does not independently prove rights/consent,
- mono S16LE only,
- provider-declared 8–48 kHz sample rate,
- <=8 MiB and <=120 seconds per result,
- no rate/pitch/style/emotion controls,
- no conversation/barge-in/startup integration,
- future concrete local TTS requires explicit dependency/model/voice-profile authorization.

# Next
PHX-064 — Voice conversation/barge-in. Do not start it.


# Authoritative compatibility repair — historical regression guards

During the first PHX-063 execution attempt, the implementation correctly stopped before completion because several historical test-only repository-diff guards are not forward-compatible with later tickets once new files become tracked.

This is an authoritative ticket correction, not permission to weaken product behavior.

## Confirmed stale guards

The following historical tests contain process-time repository-diff assertions that were valid only while their own ticket was being implemented, but become false after later legitimate tracked additions:

- `tests/test_vad.py::test_protected_production_migrations_dependencies_and_events`
  compares the current working tree to the PHX-060 baseline across broad `phoenix` scope and therefore rejects later tracked STT/TTS production files.
- `tests/test_stt_provider.py::test_protected_sources_dependency_schema_and_events`
  compares the current working tree to the pre-PHX-062 baseline across broad `phoenix` / `tests` scope and therefore rejects later tracked TTS files.
- `tests/test_search_provider.py::test_all_prior_runtime_tests_schema_desktop_dependencies_unchanged`
  dynamically hashes every protected path from `origin/main`, including historical test files, and therefore prevents legitimate later maintenance of those regression guards.
- `tests/test_urahara_research_flow.py::test_event_schema_dependencies_and_protected_sources_unchanged`
  dynamically hashes every protected path from `origin/main`, including historical tests, and therefore prevents legitimate later maintenance of those regression guards.

These checks are historical ticket-scope/process assertions. They are not product runtime contracts and must not permanently freeze the repository against later tickets.

## Exact authorized repair scope

PHX-063 is explicitly authorized to modify exactly these four historical test files, in addition to the normal PHX-063 files:

- `tests/test_vad.py`
- `tests/test_stt_provider.py`
- `tests/test_search_provider.py`
- `tests/test_urahara_research_flow.py`

No historical production file is authorized for modification.

## Required repair semantics

Repair only the stale repository-diff / dynamic-`origin/main` guards.

Do NOT weaken the functional/security/provider assertions in those suites.

Required principles:

1. Remove or replace ticket-era whole-repository / whole-subtree equality assertions that compare the current working tree against a historical commit or current `origin/main`.
2. Preserve the semantic invariants those tests can validly enforce at runtime, including:
   - EventType remains exactly 32,
   - sole Alembic head remains `0005_audit_events`,
   - required migration-count assertions where already present,
   - DEFAULT_WAKE_PHRASE semantic assertions where already present,
   - subsystem-specific static/import/side-effect/security tests,
   - credential/signature scans,
   - all provider behavior tests.
3. Do not add a broad allowlist for PHX-063 paths to a historical baseline-diff test. The repair must be forward-compatible rather than ticket-specific.
4. Do not skip, xfail, deselect, monkeypatch Git, or fake repository state.
5. Do not weaken tests by making them unconditional passes.
6. Do not modify historical production code to satisfy historical tests.
7. Remove now-unused imports/helpers only when required by Ruff/mypy.
8. The four repaired historical suites must pass with the PHX-063 files staged/tracked.

## Staged-state verification gate

The original blocker appeared only when the new TTS files were represented as tracked changes. Therefore PHX-063 completion requires a staged-state regression proof.

Before declaring completion:

1. stage the complete intended PHX-063 diff,
2. run at minimum:
   - `tests/test_vad.py`
   - `tests/test_stt_provider.py`
   - `tests/test_search_provider.py`
   - `tests/test_urahara_research_flow.py`
   - `tests/test_tts_provider.py`
3. confirm they pass while the complete intended diff is staged,
4. then run the required full regression/quality gates from this ticket while that same intended tracked state is present.

Do not rely on untracked-file behavior as evidence.

## Expected final diff expansion

The final PHX-063 diff may now include exactly these historical test-only compatibility repairs in addition to the previously expected PHX-063 files:

- `tests/test_vad.py`
- `tests/test_stt_provider.py`
- `tests/test_search_provider.py`
- `tests/test_urahara_research_flow.py`

This compatibility repair must be documented truthfully in the PHX-063 completion evidence and ADR-060 testing/compatibility notes.

No separate architecture ADR is required for the test-harness correction.
