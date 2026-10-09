# PHX-086 — Models / Voice / Settings
Status: DONE
Phase: 8 — Frontend
Branch: feature/PHX-086-models-voice-settings
Starting main/base SHA: 66cfbfdff7a4a0f94312641cf1bdcc2c682b5061
Expected unchanged main during implementation: 66cfbfdff7a4a0f94312641cf1bdcc2c682b5061

## Objective

Replace only the existing Models, Voice, and Settings placeholders in the PHOENIX desktop shell with accessible, responsive, truthful workspaces. Models and Voice are read-only displays of optional caller-supplied evidence. Settings is a structured, read-only overview of which configuration areas are and are not connected; it is not a settings editor.

This is frontend presentation work only. Do not add model/provider discovery, model selection/loading/unloading/downloads, inference, microphone/audio-device access, recording/playback, wake-word activation, voice-profile changes, runtime configuration, persistence, transport, API routes, Tauri IPC, or privileged actions. Default App must remain disconnected and must not fabricate provider/model/voice health or applied settings.

PHX-086 does not complete Phase 8. PHX-087 remains TODO. Manual native WebView2 acceptance remains PENDING unless independently performed.

## Authoritative context

Read current main versions of the project authority in the authority order required by AGENTS.md/RULES.md and at minimum:
- AGENTS.md, RULES.md, MENTAL_MODEL.md, INVARIANTS.md, MASTER_SPEC.md, PRD.md, ROADMAP.md, DECISIONS.md, FEATURE_TICKETS.md
- docs/01_ARCHITECTURE.md, docs/05_MODEL_STRATEGY.md, docs/06_HARDWARE_PROFILE.md, docs/09_SECURITY.md
- docs/17_VOICE_SYSTEM.md, docs/20_UI_UX_SPEC.md, docs/21_FRONTEND.md, docs/23_OBSERVABILITY.md, docs/25_DEPENDENCIES.md
- tickets/PHX-010-model-provider-interface.md, tickets/PHX-012-model-capability-registry.md, tickets/PHX-013-model-lifecycle-manager.md
- tickets/PHX-060-wake-word-provider.md, tickets/PHX-061-vad.md, tickets/PHX-062-stt-provider.md, tickets/PHX-063-tts-provider.md, tickets/PHX-064-voice-conversation-barge-in.md, tickets/PHX-065-rise-phoenix-startup-health-sequence.md
- tickets/PHX-080-app-shell-navigation.md, tickets/PHX-081-chat-voice-ui.md, tickets/PHX-082-agent-network-ui.md, tickets/PHX-083-task-graph-ui.md, tickets/PHX-084-permission-center.md, tickets/PHX-085-memory-files-workspace.md
- apps/desktop/src/App.tsx, apps/desktop/src/styles.css, apps/desktop/src/main.tsx, apps/desktop/package.json, apps/desktop/src-tauri/tauri.conf.json, tests/workspace.test.mjs, tests/test_prompt_injection_defenses.py, tests/test_screenshot_vision_loop.py, tests/test_windows_ui_automation.py.

If any authority conflicts or the proposed presentation DTO contract conflicts with an existing canonical contract, preserve existing architecture and STOP BLOCKED. Do not silently invent or reconcile a new domain contract.

## Ownership and frozen system truth

- PHOENIX remains the sole supreme orchestrator.
- ModelProvider (PHX-010), ModelCapabilityRegistry (PHX-012), ModelLifecycleManager (PHX-013), the router, and model providers retain their existing owners. Capability evidence is not routing, a preference, model residency, successful inference, or authorization.
- PHX-060–064 own wake/VAD/STT/TTS/voice conversation contracts; PHX-065 owns startup health sequencing. Voice state is not whole-application readiness.
- No concrete microphone, audio output, wake-word, STT, TTS, or model-runtime implementation may be assumed from the existence of a provider contract.
- PermissionEngine remains the sole authorization authority. Model capability flags and voice state cannot grant permission.
- Current API behavior remains GET /health only. Planned API contracts do not authorize new routes or calls.
- Default App has no models/voice transport. An optional caller-owned port is presentation evidence only, not proof of a live backend or actual hardware/provider health.

## Exact scope

Only these seven paths may change:
1. apps/desktop/src/App.tsx
2. apps/desktop/src/styles.css
3. tests/workspace.test.mjs
4. docs/21_FRONTEND.md — only after all deterministic gates pass
5. DECISIONS.md — append exactly one accepted ADR-073 only after all gates pass
6. FEATURE_TICKETS.md — only after all gates pass
7. tickets/PHX-086-models-voice-settings.md — mark DONE only after all gates pass

Do not edit any other ticket, backend/Python source, database/migration, API/router, event vocabulary, model/voice implementation, permission/audit code, Rust/Tauri source/config/capabilities/CSP, dependency manifest/lockfile, build configuration, or historical test file. No new dependencies. If a gate requires changes outside this allowlist, stop BLOCKED and report evidence; do not widen the allowlist yourself.

## Proposed presentation DTO contract — confirm before implementation

These are display DTOs only. They do not replace PHX-010/012/013 or PHX-060–065 canonical contracts. The user must confirm this contract before Codex begins implementation.

### ModelPort

Top-level exact keys:
- required status: connected | unavailable | degraded
- required entries: readonly list of model display entries

Each entry has required providerId and modelId. Optional fields:
- providerStatus: healthy | degraded | unavailable
- providerCheckedAt: UTC timestamp text
- supportsStreaming: boolean
- supportsStructuredOutput: boolean
- supportsToolUse: boolean
- supportsVision: boolean
- supportsCancellation: boolean
- contextLimitTokens: positive safe integer
- capabilitiesRefreshedAt: UTC timestamp text

Bounds and validation:
- entries maximum 64; reject duplicate (providerId, modelId) pairs.
- providerId/modelId: nonblank bounded strings, maximum 128 UTF-16 code units each.
- timestamps: valid UTC ISO-8601 timestamp text, maximum 64 UTF-16 code units.
- status and capability booleans must match exact enumerations/types; no coercion.
- contextLimitTokens must be a positive safe integer when present.
- no arbitrary metadata/detail dictionaries, provider URLs, paths, environment data, credentials, model outputs, prompts, or raw exceptions.
- missing optional values display “Not reported.”
- unavailable/degraded top-level status suppresses all supplied entries. A connected status means only “caller-supplied evidence.”

### VoicePort

Top-level exact keys:
- required status: connected | unavailable | degraded
- optional state: sleeping | listening | thinking | speaking | degraded_voice | text_only
- required components: readonly list

Each component has required:
- id: wake_word | vad | stt | tts | conversation | audio_output
- status: available | degraded | unavailable
- optional checkedAt: valid UTC ISO-8601 timestamp text, maximum 64 UTF-16 code units

Bounds and validation:
- at most six component entries, with no duplicate IDs.
- state and component values use the exact enumerations above.
- unavailable/degraded top-level status suppresses state and component details.
- no device names/IDs, microphone permissions, PCM/audio, transcripts, synthesized speech, voice samples/profiles, raw exception text, local model paths, or credentials.
- missing optional values display “Not reported.”
- this is caller-reported voice evidence only; it cannot assert microphone access, actual recording/playback, startup success, or whole-app health.

### Settings workspace

Use no SettingsPort and invent no settings domain keys: no authoritative settings contract or persistence surface exists in this ticket. Replace the placeholder with a calm, semantic overview that clearly distinguishes:
- Model/provider configuration: not connected; no model selection/load/download controls.
- Voice/device configuration: not connected; no microphone/audio-device access or recording/playback controls.
- Application preferences: no persisted app-preference editor is implemented by PHX-086.
- Privacy and permissions: existing Permission Center and PermissionEngine remain authoritative; no local override.
- Storage/integration: no new persistence or backend transport.

The overview must not render pretend current values, enabled controls, saved-success messages, or nonfunctional toggles. It must clearly state that settings changes cannot be made or saved in this workspace yet. If the user chooses a different settings scope, update this ticket before implementation rather than guessing.

## Shared workspace requirements

- Keep all 14 canonical navigation IDs/labels, Home default/fallback, navigation groups, PHOENIX branding/tagline, shell, skip link, landmarks, focus behavior, selected navigation state, and PHX-081–085 behavior unchanged.
- Replace exactly Models, Voice, and Settings placeholders; other disconnected views stay honest.
- Use existing cinder/amber palette, system fonts, visible focus, readable contrast, reduced-motion support, and responsive behavior for the unchanged 800x600 Tauri window and narrow widths.
- Use semantic headings, explicit labels, native keyboard-operable controls only where a real local UI action exists, and polite status/count feedback. Read-only evidence cards must not look actionable.
- Bound/scroll long lists and wrap long text without making navigation or actions unreachable.
- Do not use arbitrary HTML/Markdown, links, file paths, provider URLs, or external content. Render untrusted strings only as inert escaped text.
- Keep each workspace's search/selection state independent; no state should leak between Model, Voice, Settings, or existing workspaces.
- No network/API/WebSocket/Tauri invoke/event/filesystem/shell/clipboard/plugin/provider calls, polling/timers, background workers, settings writes, persistence, model inference, model loading/unloading, or microphone/audio access.
- Preserve PHX-084's security semantics and all ten PHX-041 evidence combinations unchanged.
- Preserve the PHX-081 chat, PHX-082 agent directory, PHX-083 task graph, PHX-084 permission center, and PHX-085 memory/files implementation blocks byte-for-byte except strictly necessary view composition/type plumbing. Preserve all original foundation test bodies and prior workspace security assertions. Do not weaken historical guards.

## Frontend admission and error semantics

- Exact own data-property allowlists only; reject unexpected string/symbol keys, accessors, malformed arrays, duplicate entries, invalid types, out-of-bounds values, and reflective failures.
- Validate the entire snapshot before any row becomes visible; do not partially display malformed snapshots.
- Detach/freeze accepted primitive snapshots before rendering. Catch reflection errors and expose generic safe errors only.
- JavaScript Proxy traps may execute; this is not a sandbox, authentication boundary, or proof of source truth.
- Search must be bounded, case-insensitive, source-order preserving, and local only. Counts must reflect admitted/filter-matched records accurately. Selection must reset safely when its record disappears or its port becomes unavailable/degraded.
- Do not claim an optional caller port is connected merely because an object is present.

## Required tests

Extend only tests/workspace.test.mjs. Test:
- ModelPort exact top-level and entry allowlists; every required/optional field; all bounds; duplicate composite IDs; missing optional values; malformed timestamps; wrong status/capability types; invalid safe-integer boundaries; sparse/extended/accessor/symbol/proxy-reflection failures; detached frozen snapshots; no partial display; disconnected/degraded suppression; local search/order/counts/clear/selection reset.
- VoicePort exact allowlists; state/component enumerations; duplicate/unknown component IDs; six-entry cap; timestamp validation; missing optional values; sparse/extended/accessor/symbol/proxy-reflection failures; detached frozen snapshots; no partial display; disconnected/degraded suppression; honest voice-only semantics.
- Settings workspace never suggests settings are editable/saved, has no fake toggles or callbacks, and does not introduce transport or persistence.
- Inert escaped text; no provider/model/audio/device/file links or executable content.
- 14-route navigation and all PHX-081–085 workspace behavior remains unchanged.
- Responsive CSS/focus/reduced motion/bounded scrolling and native keyboard semantics.
- Exact source/API call allowlist and protected architecture invariants.
- Baseline is 115 npm tests from merged PHX-085; final npm test must pass three consecutive times with identical counts and increased coverage. Record exact counts, no failures/skips.

## Required deterministic gates

Run every applicable command from the current PHX-085 completion ticket and project authority, without weakening, filtering, retries-as-passes, warning suppression, or changing historical test files. Capture exact commands, exit codes, counts, skip identities/reasons, and warning text in an external validation directory. Keep receipts/build outputs outside the repository.

At minimum:
- npm ci --ignore-scripts; npm test three consecutive final runs; desktop typecheck/build; Tauri info; Cargo check/test; release build with existing documented external-output override, without launching GUI/dev server.
- uv sync --locked; Ruff check; Ruff format check; mypy; wheel and sdist build outside repository with exact package-source/dependency metadata parity and unchanged Python requirement.
- full Python suite three consecutive final runs, preserving the PHX-085 baseline of 15,721 passed, 16 existing skips and one existing warning unless the checked-in current main proves otherwise.
- all 19 PHX-085 focused backend regressions at their current main baseline, including model lifecycle/provider/registry/router, startup, voice conversation/TTS/STT/VAD/wake, memory/files/document and permission/audit/security suites.
- PHX-055 exact protected-source guard x3 and complete prompt-injection suite x3 (604 baseline each).
- full screenshot/vision and Windows UI automation suites (397 and 493 baseline respectively), with exact historical guard runs repeated as required by the project ticket.
- exact /health behavior, manifest/lock/CSP/capabilities/native source invariants, dependency metadata and 77-source wheel/sdist parity.
- invariant review INV-001–024; credential/private-path/debug-output/artifact review; exact seven-path allowlist; clean diff/status. Confirm existing PHX-085 DTO contracts and its 28 new tests remain unchanged except strictly necessary integration.

Before choosing counts, verify the actual baseline on current main/ticket history. Do not assume stale counts if a current main receipt contradicts them. No command is considered passing unless its actual exit status and output are observed. Do not claim local GUI/WebView2 acceptance; manual native acceptance remains PENDING.

## Documentation and delivery

Only after every deterministic gate passes:
- append exactly one accepted ADR-073 describing the bounded, read-only model/voice evidence ports and the explicit non-editor Settings workspace; do not rewrite ADR-072.
- narrowly update docs/21_FRONTEND.md.
- mark PHX-086 DONE in this ticket and FEATURE_TICKETS.md. Phase 8 remains INCOMPLETE because PHX-087 remains TODO.
- commit exactly with subject: feat: complete PHX-086 models voice settings
- push only feature/PHX-086-models-voice-settings. No PR, merge, main push, force-push, tag, or PHX-087 work from Codex.

If any required gate fails, do not mark DONE or commit a completion claim. Fix only clearly in-scope ordinary defects, rerun affected gates and then the full final sequence. Stop BLOCKED on an authority conflict, protected-file requirement, unsafe repair, unavailable required prerequisite, or three identical failures. Never weaken tests/security or alter baseline skips to force green.

## Completion report

Return one complete evidence report with branch/base/final SHAs; exact changed paths and diff stats; every command, exit code and counts; three final npm/Python results; historical guard/regression counts; invariant-by-invariant review; source/dependency/package parity; DTO admission/privacy/authority review; docs/ADR/status changes; clean working tree and feature-only push proof; known limitations; and explicit statements:
- LIVE MODEL/VOICE TRANSPORT: NOT IMPLEMENTED.
- MODEL LOAD/UNLOAD/DOWNLOAD/INFERENCE: NOT IMPLEMENTED.
- MICROPHONE/AUDIO DEVICE/RECORDING/PLAYBACK: NOT IMPLEMENTED.
- SETTINGS EDIT/PERSISTENCE: NOT IMPLEMENTED.
- MANUAL NATIVE WEBVIEW2 ACCEPTANCE: PENDING.
- PHASE 8: INCOMPLETE; PHX-087: TODO; PHX-087 NOT STARTED.

## Completion evidence — 2026-10-10

PHX-086 replaces exactly Models, Voice and Settings. ModelPort and VoicePort
are optional caller-owned readonly evidence ports without callbacks; default App
has neither and reports no evidence source connected. A connected status means
caller-supplied evidence only. No operational health or settings are fabricated.

The user's implementation request explicitly specified the exact proposed DTOs
and bounds; that instruction confirms the ticket's presentation contract.
ModelPort has exactly status (connected/unavailable/degraded) and entries (0..64).
Each entry requires providerId/modelId: meaningful strings at most 128 UTF16 units,
preserved as supplied. The pair, never modelId alone, identifies evidence; a
length-prefixed internal inspection key avoids delimiter collisions. Optional
fields are providerStatus (healthy/degraded/unavailable), providerCheckedAt,
supportsStreaming, supportsStructuredOutput, supportsToolUse, supportsVision,
supportsCancellation, contextLimitTokens and capabilitiesRefreshedAt. The five
flags must be booleans; contextLimitTokens a positive safe integer. Both timestamp
fields are real calendar UTC ISO text (Z/+00:00), at most 64 UTF16 units. Missing
optional fields say Not reported; explicit false remains false. No timestamp,
freshness, residency, preference, installed model, default context or support is
inferred. These partial display DTOs do not reconstruct canonical domain objects.

VoicePort has exactly status, components (0..6) and optional state. State is
sleeping/listening/thinking/speaking/degraded_voice/text_only. Component fields
are id, status and optional checkedAt; IDs are wake_word/vad/stt/tts/conversation/
audio_output, unique within a snapshot. Component status is available/degraded/
unavailable; checkedAt uses the same bounded real UTC validation. Only supplied
components appear; missing state/timestamp says Not reported. All state/component
details are suppressed when top-level status is unavailable/degraded. UTA owns
voice, while PHOENIX alone owns overall startup/readiness.

Whole-snapshot admission reuses established own-descriptor, exact-field, bounded
array/text and UTC helpers. Missing/extra/symbol/accessor fields, sparse/extended
arrays, duplicate pairs/components, invalid types/enums/booleans/integers/time,
oversized values or reflection failures reject the whole snapshot. Validation
precedes status-based suppression. Admitted primitive records, arrays and roots
are detached and frozen. Exceptions have generic wording, with no diagnostics or
input logging. Proxy traps may execute; validation is not a sandbox, authentication
boundary or proof of source truth. No arbitrary metadata, URL/path/environment,
credential/exception, prompt/output, device/permission, PCM/transcript/audio or
voice-profile fields are admitted. Trusted future adapters still own authentication
and privacy policy; shape checks do not guarantee strings contain no private data.

Source-order case-insensitive local search retains the established 80 UTF16 cap,
counts, Clear search and no-match feedback. Model boolean/number evidence is
searchable as true/false and numeric text. App-owned independent query/inspection
state survives navigation and resets to first/none on removed records or source
loss. Off-filter selected evidence is explicitly labelled. Passive article cards
have separate named native Inspect evidence buttons for the real local detail
action; cards have no click handler, pointer/hover affordance or runtime action.
Inspection never selects a runtime model or activates a voice component.

Settings has no SettingsPort, domain schema, state, callbacks, controls, pretend
values, toggles or save messages. Five semantic sections describe model/provider
configuration and voice/device configuration as not connected, no persisted app
preference editor, unchanged Permission Center/PermissionEngine authority, and no
new storage/backend integration. Settings cannot be changed or saved here.

Normal React escaping keeps strings inert, without links, HTML/Markdown execution,
external resources, clipboard or dereferencing. Semantic headings, labels, polite
atomic counts/selection feedback and native inspection controls retain keyboard
use. Cinder/amber, system fonts, visible focus and reduced motion remain. Wrapped
details and bounded lists stack at narrow widths/800x600; no focus theft on search.
All 14 routes, Home default/fallback, groups, branding, skip link, landmarks and
focus wiring remain. PHX-081–085 logic prefix and all 28 PHX-085 tests are checked
byte for byte, except necessary composition/harness plumbing. Five destinations
remain honest placeholders.

Provider health, capabilities, residency and application readiness stay distinct.
Voice-channel evidence proves no microphone access, recording/playback, wake
activation or startup success. Capability flags grant no permissions; unchanged
PermissionEngine and all ten PHX-041 evidence combinations remain authoritative.
No live API/network/WebSocket, Tauri IPC/events/plugin, provider/model operation,
microphone/audio/device access, file/shell/clipboard, settings mutation/persistence,
polling/timers/worker or dependency was added. Current API remains GET /health.
Native security/configuration, backend, manifests/locks and historical tests are
unchanged. SSR/hooks/AST/native builds are deterministic evidence only; manual
native WebView2 acceptance remains PENDING. Phase 8 INCOMPLETE; PHX-087 TODO.

Verified preparation 51954675399cf00e9c12f8da34f732b59531ae7d based on unchanged main 66cfbfdff7a4a0f94312641cf1bdcc2c682b5061.
Clean completed PHX-085 checkout was safely switched to a local tracking checkout
of the existing remote preparation branch; no remote branch/preparation recreation.
The stale PHX-065 authority path was resolved to the actual existing unchanged
tickets/PHX-065-rise-phoenix-startup-health.md. No contract conflict was found.

All commands below exited zero. CARGO_TARGET_DIR=C:\Users\shoai\.codex\validation\PHX-086-20261010\cargo-target.
Tauri external config overrides only build.beforeBuildCommand/frontendDist; no
tracked runtime/security/build configuration changed, no GUI/dev server launched.

| Exact command | Exit |
| --- | --- |
| npm ci --ignore-scripts | 0 |
| npm test (pre-edit baseline) | 0; 115 passed |
| uv run --locked pytest tests/test_screenshot_vision_loop.py::test_no_action_persistence_or_dependency_change -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\historical-screenshot_vision_loop-exact-1.xml | 0 |
| uv run --locked pytest tests/test_screenshot_vision_loop.py::test_no_action_persistence_or_dependency_change -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\historical-screenshot_vision_loop-exact-2.xml | 0 |
| uv run --locked pytest tests/test_screenshot_vision_loop.py::test_no_action_persistence_or_dependency_change -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\historical-screenshot_vision_loop-exact-3.xml | 0 |
| uv run --locked pytest tests/test_windows_ui_automation.py::test_source_architecture_and_immutability -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\historical-windows_ui_automation-exact-1.xml | 0 |
| uv run --locked pytest tests/test_windows_ui_automation.py::test_source_architecture_and_immutability -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\historical-windows_ui_automation-exact-2.xml | 0 |
| uv run --locked pytest tests/test_windows_ui_automation.py::test_source_architecture_and_immutability -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\historical-windows_ui_automation-exact-3.xml | 0 |
| uv run --locked pytest tests/test_screenshot_vision_loop.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\historical-screenshot_vision_loop.xml | 0 |
| uv run --locked pytest tests/test_windows_ui_automation.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\historical-windows_ui_automation.xml | 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\guards-exact-1.xml | 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\guards-exact-2.xml | 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\guards-exact-3.xml | 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\guards-suite-1.xml | 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\guards-suite-2.xml | 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\guards-suite-3.xml | 0 |
| uv run --locked pytest tests/test_api.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-api.xml | 0 |
| uv run --locked pytest tests/test_bootstrap.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-bootstrap.xml | 0 |
| uv run --locked pytest tests/test_agent_registry.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-agent_registry.xml | 0 |
| uv run --locked pytest tests/test_agent_model_router.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-agent_model_router.xml | 0 |
| uv run --locked pytest tests/test_tool_registry.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-tool_registry.xml | 0 |
| uv run --locked pytest tests/test_permission_engine.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-permission_engine.xml | 0 |
| uv run --locked pytest tests/test_audit_trail.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-audit_trail.xml | 0 |
| uv run --locked pytest tests/test_secrets_boundary.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-secrets_boundary.xml | 0 |
| uv run --locked pytest tests/test_spotify_integration.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-spotify_integration.xml | 0 |
| uv run --locked pytest tests/test_youtube_integration.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-youtube_integration.xml | 0 |
| uv run --locked pytest tests/test_document_generation.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-document_generation.xml | 0 |
| uv run --locked pytest tests/test_upload_parser_routing.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-upload_parser_routing.xml | 0 |
| uv run --locked pytest tests/test_rise_phoenix_startup.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-rise_phoenix_startup.xml | 0 |
| uv run --locked pytest tests/test_voice_conversation.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-voice_conversation.xml | 0 |
| uv run --locked pytest tests/test_tts_provider.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-tts_provider.xml | 0 |
| uv run --locked pytest tests/test_stt_provider.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-stt_provider.xml | 0 |
| uv run --locked pytest tests/test_vad.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-vad.xml | 0 |
| uv run --locked pytest tests/test_wake_word_provider.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-wake_word_provider.xml | 0 |
| uv run --locked pytest tests/test_task_graph_state_machine.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\regressions-task_graph_state_machine.xml | 0 |
| uv run --locked pytest --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\full-run-1.xml | 0 |
| uv run --locked pytest --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\full-run-2.xml | 0 |
| uv run --locked pytest --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\full-run-3.xml | 0 |
| uv sync --locked | 0 |
| uv run --locked ruff check . | 0 |
| uv run --locked ruff format --check . | 0 |
| uv run --locked mypy | 0 |
| uv build --out-dir C:\Users\shoai\.codex\validation\PHX-086-20261010\dist | 0 |
| npm.cmd test | 0 |
| npm.cmd test | 0 |
| npm.cmd test | 0 |
| npm.cmd run typecheck --workspace @phoenix/desktop | 0 |
| npm.cmd run build --workspace @phoenix/desktop -- --outDir C:\Users\shoai\.codex\validation\PHX-086-20261010\frontend-dist | 0 |
| npm.cmd run tauri:info --workspace @phoenix/desktop | 0 |
| cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml | 0 |
| cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml | 0 |
| npm.cmd run tauri:build --workspace @phoenix/desktop -- --config C:\Users\shoai\.codex\validation\PHX-086-20261010\tauri-build-paths.json | 0 |
| uv run --locked pytest tests/test_memory_schema.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\impacted-memory_schema.xml | 0 |
| uv run --locked pytest tests/test_pudding_memory_service.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\impacted-pudding_memory_service.xml | 0 |
| uv run --locked pytest tests/test_document_ingestion.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\impacted-document_ingestion.xml | 0 |
| uv run --locked pytest tests/test_vector_store_sqlite.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\impacted-vector_store_sqlite.xml | 0 |
| uv run --locked pytest tests/test_retrieval_context.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\impacted-retrieval_context.xml | 0 |
| uv run --locked pytest tests/test_provenance_citations.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\impacted-provenance_citations.xml | 0 |
| uv run --locked pytest tests/test_filesystem_tool.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\impacted-filesystem_tool.xml | 0 |
| uv run --locked pytest tests/test_model_provider.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\models-model_provider.xml | 0 |
| uv run --locked pytest tests/test_model_registry.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\models-model_registry.xml | 0 |
| uv run --locked pytest tests/test_model_lifecycle.py -q --junitxml=C:\Users\shoai\.codex\validation\PHX-086-20261010\models-model_lifecycle.xml | 0 |

Final npm three runs: 141 passed each, zero failed/skipped (115 prior + 26 new).
TypeScript/Vite/Tauri info/Cargo check/test/release passed; zero Rust tests, build
evidence only. Full Python three runs: 15,721 passed, same 16 skip identities,
types/reasons/text and same existing Starlette/httpx warning each, compared exactly
with accepted PHX-085 baseline. No filters/xfail/skip changes or warning suppression.
No incomplete/interrupted run counted. Durable hidden external one-shot runners
were independent of tool-host lifecycle, without OS-setting/permission changes.

PHX-055/052/051 exact guards passed three times each. Complete prompt-injection
suite passed 604 three times; screenshot/vision 397 and Windows UI automation 493.
All 19 baseline regressions passed: API 8, bootstrap 11, agent registry 96, router
213, ToolRegistry 204, PermissionEngine 926, audit 302, secrets 402, Spotify 345,
YouTube 358, document generation 284, upload/parser 336, startup 509, conversation
950, TTS 602, STT 414, VAD 403, wake 537, task graph 348.
Model provider/registry/lifecycle targeted counts: 61/83/54, all passed.
memory_schema: 244 passed; exit 0.
pudding_memory_service: 167 passed; exit 0.
document_ingestion: 300 passed; exit 0.
vector_store_sqlite: 273 passed; exit 0.
retrieval_context: 367 passed; exit 0.
provenance_citations: 173 passed; exit 0.
filesystem_tool: 649 passed, 16 identical existing skips; exit 0.

Locked sync: 49 packages; Ruff lint/format pass (133 formatted files); mypy 127
sources passed. External wheel/sdist contain 77 source bytes equal to working
files/Git-normalized main, unchanged direct dependency metadata and Requires-Python
<3.12,>=3.11. API eight tests prove repeated exact GET /health 200/application-json,
status ok/service phoenix-api/installed package version, 404 unknown route and
factory isolation. No model/voice/settings routes are added or called.

No product/test repair was needed. One read-only external status monitor failed
with exit 1 because Windows CP1252 stdout could not encode the npm information
glyph. Explicit UTF8 stdout in external monitor.py repaired it; retest exit 0.
No project/test changes or failed/incomplete validation gate resulted.
Complete staged implementation was reviewed after all gates and before these
four completion doc/status edits. Credential/private-content/path/debug/artifact
reviews, whitespace, protected hashes and exact seven-path checks passed.
All prior logic and PHX-085 test blocks remain byte-equal; original three foundation
bodies remain unchanged. Receipts and build/package outputs are outside the repo.

| Invariant | Individual evidence |
| --- | --- |
| INV-001 | Exact 30-entry frontend/document/Python ordered parity and sole phoenix Supreme orchestrator checked by npm; full orchestrator tests pass, production unchanged. |
| INV-002 | strategist/AIZEN retains Chief strategist/master planner metadata and subordinate designation; no executable authority; registry 96/router 213 and full planner tests pass. |
| INV-003 | No LLM output, request reason or display status grants authority; no direct PermissionEngine call or locally created decision. Explicit native button callback delivers intent only; PermissionEngine 926 unchanged and passed. |
| INV-004 | App has no tool/IPC imports; ToolRegistry 204 and PermissionEngine 926 passed. |
| INV-005 | P4 review requires an admitted exact P4/p4_explicit_approval_required evaluation and explicit native click. Callback sends only a request ID and human intent; trusted identity correlation and authorization remain PermissionEngine 926, unchanged. |
| INV-006 | P5/p5_blocked is the only admitted P5 evaluation; no Approve/Deny controls or callback eligibility. Exhaustive 1,920 combination tests and unchanged PermissionEngine 926/SecretStore 402 pass. |
| INV-007 | AuditTrail 302 passed; audit/persistence source unchanged; no UI privileged actions. |
| INV-008 | All 60 gate commands plus npm ci exit zero before DONE; three final npm/full sequences follow last product/test edit. Baseline 115 verified before edits, final 141 includes all 28 PHX-085 tests unchanged. No product/test repair was needed. Live model/voice transport, settings editing and manual native acceptance remain unimplemented/pending. |
| INV-009 | Recovery source unchanged; test_mahoraga_recovery.py::test_explicit_positive_budgets and ::test_bounded_replan_membership_failed_step_and_order are included in all three full pytest runs. |
| INV-010 | No recovery mode controls; test_kaido_failsafe.py::test_decision_is_degraded_only is included in all three full pytest runs; source unchanged. |
| INV-011 | Exact npm AST call allowlist permits React/local helpers and one supplied sendText call only inside sendDraft and one supplied submitDecision call only inside deliverPermissionIntent; provider/network/native/media/storage APIs banned; CSP connect-src none and empty capabilities unchanged. |
| INV-012 | No provider or agent production edits; registry 96 and router 213 passed. |
| INV-013 | No tool/policy production edits; ToolRegistry 204 passed, inert permission/audit metadata protected. |
| INV-014 | Only exact canonical IDs select agent details/status; permission IDs remain opaque exact request IDs, never grant authority; every alias lookup rejected by npm; Python alias-removal/collision registry tests pass; 14 routes unchanged. |
| INV-015 | Native controlled local textarea remains editable while disconnected; named form Send and all 14 nav buttons work without a microphone; disabled voice explained. |
| INV-016 | No voice initialization in shell; startup 509, conversation 950, TTS 602, STT 414, VAD 403, wake 537 passed. |
| INV-017 | PHX-086 exact bounded ModelPort/VoicePort descriptors, enums, booleans, positive safe integers, real UTC and composite identities validate whole snapshots even while hidden. Frozen primitive copies and generic failures preserve privacy; 26 new tests prove inert JSX, no resources/actions and source-order query/selection. Settings is a non-editor without a port. PHX-085 exact whole-snapshot own-descriptor admission, 64 unique records per port, six memory classes/two file kinds, confirmed primitive/UTC bounds, safe size integers, detached frozen evidence, generic failures and React escaping verified by 28 new tests. Content/source references remain inert; no links, bytes, metadata or instructions executed. Proxy reflection is not a sandbox. Snapshot roles limited to user/assistant, exact data fields, count 64, content 4000, ID 128, unique IDs; malformed/oversize entries omitted with notice, complete accepted text React-escaped; no parsing or external resources. Task whole-graph admission checks 1..40 steps, <=120 edges, exact known owners, 11 canonical task states, strict text/array/timeout bounds, no duplicate/unknown/self dependencies or cycles, source-priority traversal, inert JSX and generic errors. Agent snapshots admit at most 30 unique known IDs, 4 closed states, own data fields only, task<=240/provider<=120 UTF16; invalid whole snapshot rejected with generic notice; JSX escaping tested. Permission snapshots enforce <=64 unique requests, exact own data fields/correlation, UTF16 bounds/strict UTC, ten exact evidence combinations, generic reflection failure, detached frozen primitives and inert JSX; model-like approval text never changes eligibility. Prompt 604, upload 336 passed. |
| INV-018 | No PHX-085 storage, logging, clipboard, callbacks, transport or admission policy. Display validation does not guarantee private data contains no secrets; trusted future adapters must enforce existing PHX-031 mandatory secret rejection. SecretStore 402 and impacted memory service tests pass; memory/security production unchanged. |
| INV-019 | Caller-triggered one-flight send exposes pending state and Cancel waiting. Local cancellation retains draft even after late ack, explicitly cannot abort delivery, and duplicates remain blocked until adapter settles; no timer/polling/hidden worker. Permission intents expose global pending state, prevent all duplicates until settlement, retain pending across navigation/source loss and explain that UI cannot cancel adapter delivery; no cancellation feature promised. Hook/helper tests pass. |
| INV-020 | No migration changes; historical guard preserves sole Alembic head 0005_audit_events and EventType 32, exact test x3 passed. |
| INV-021 | Only connected supplied Promise<void> ack can clear matching draft; synchronous/thenable/malformed result and exceptions fail safely, retain draft, never append transcript; Permission intent requires native Promise resolving undefined; sync/thenables/malformed acknowledgements/throws/rejections fail safely with deliberate retry and unchanged evaluation. ACK blocks repeat ID until valid evidence changes away or ID disappears; never reports authorization. All provider/tool regressions pass. |
| INV-022 | Model/voice source loss hides all records and voice state, safely resets selection, preserves independent navigation queries and labels off-filter inspection. Missing optional fields are Not reported; false is preserved; no health/residency/startup/settings inferred. Memory/file absent, empty, malformed, unavailable and degraded states are explicit, hidden records suppressed after full validation; selection resets on loss, persists on navigation, off-filter selected evidence labelled. Missing optional fields show Not reported; zero preserved. Unknown view falls back Home; unknown/alias agent selection retains last valid identity, no identity invention; missing evidence Status not reported, disconnected/degraded no active claims; off-list selection explicitly labelled; task selection resets across task IDs/missing steps/disconnect, default no graph; Permission default source disconnected; unavailable/degraded/malformed evidence suppresses stale details, missing evaluation never infers risk/status/actions, removed selection resets to first/none and off-filter notice explicit. Chat/voice fallback preserved. |
| INV-023 | No model generation path; phoenix/models/lifecycle.py default capacity one remains unchanged; test_model_lifecycle.py::test_global_capacity_blocks_different_providers_and_models and invalid/positive capacity cases pass in all three full suites. |
| INV-024 | All npm/Python manifests and locks unchanged; existing-dependency UI only; package dependency metadata equals main. |

External receipts: C:\Users\shoai\.codex\validation\PHX-086-20261010.
Final staged scope and normal feature-only delivery receipts will capture the
completion SHA, matching remote HEAD, unchanged main, 0 behind / 2 ahead and clean
working tree before the final report. Frozen ticket preserved except Status and
this appended evidence. No PR/merge/main push/tag/next-ticket work.

LIVE MODEL/VOICE TRANSPORT: NOT IMPLEMENTED.
MODEL LOAD/UNLOAD/DOWNLOAD/INFERENCE: NOT IMPLEMENTED.
MICROPHONE/AUDIO DEVICE/RECORDING/PLAYBACK: NOT IMPLEMENTED.
SETTINGS EDIT/PERSISTENCE: NOT IMPLEMENTED.
MANUAL NATIVE WEBVIEW2 ACCEPTANCE: PENDING.
PHASE 8: INCOMPLETE; PHX-087: TODO; PHX-087 NOT STARTED.
