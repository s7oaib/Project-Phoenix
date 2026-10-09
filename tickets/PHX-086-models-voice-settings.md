# PHX-086 — Models / Voice / Settings
Status: TODO
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
