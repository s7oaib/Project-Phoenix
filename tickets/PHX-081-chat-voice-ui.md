# PHX-081 — Chat / voice UI

Status: TODO
Phase: 8 — Frontend
Branch: feature/PHX-081-chat-voice-ui
Starting main/base SHA: 9d7b05ca676aa63caec9fc8acdc238da3b421bf5
Expected unchanged main: 9d7b05ca676aa63caec9fc8acdc238da3b421bf5

## Objective

Replace PHX-080's Home / Chat informational preview with an accessible, responsive
text-first chat and voice-conversation UI, preserving its 14-view navigation shell.
Provide honest local drafting, bounded plain-text transcript presentation, safe
send-request interaction when and only when a separately trusted adapter is supplied,
and a clear voice-availability/control surface that does not request microphone
access or claim voice capture is already connected.

This is FRONTEND PRESENTATION and caller-supplied transport-adapter composition only.
No live backend connection is in scope; no simulated assistant success may substitute.

## Source-of-truth and preflight

Read:
- AGENTS.md, RULES.md, MENTAL_MODEL.md, INVARIANTS.md,
  MASTER_SPEC.md, PRD.md, ROADMAP.md, DECISIONS.md, FEATURE_TICKETS.md
- docs/01_ARCHITECTURE.md
- docs/13_API_CONTRACTS.md
- docs/17_VOICE_SYSTEM.md
- docs/20_UI_UX_SPEC.md
- docs/21_FRONTEND.md
- docs/25_DEPENDENCIES.md
- tickets/PHX-003-tauri-react-desktop-foundation.md
- tickets/PHX-064-voice-conversation-barge-in.md
- tickets/PHX-080-app-shell-navigation.md
- phoenix/api/app.py
- apps/desktop/src/App.tsx, apps/desktop/src/styles.css
- apps/desktop/src/main.tsx
- tests/workspace.test.mjs
- tests/test_prompt_injection_defenses.py
- tests/test_screenshot_vision_loop.py
- tests/test_windows_ui_automation.py
- apps/desktop/src-tauri/tauri.conf.json
- apps/desktop/package.json, package.json.

Authoritative current backend reality, independently inspected at base SHA:
`phoenix/api/app.py` only implements GET /health.
The REST POST /chat and WS /events mentioned in docs/13_API_CONTRACTS.md are
future planned endpoints, NOT implemented. No PHX-081 UI code may presume they
exist, synthesize replies, probe port numbers or silently request them.

The PHX-064 backend conversation service explicitly does not capture microphones,
enumerate devices, add concrete audio backends or frontend integration.
Do not claim the frontend can record, speak, wake, STT or TTS without approved
runtime integration.

## Frozen PHOENIX architecture

- PHOENIX alone is supreme orchestrator.
- UTA is display alias for conceptual voice role, never a permission token.
- Chat text/STT transcripts are untrusted data, never privilege/intent authentication.
- UI cannot grant any P0-P5 permission or invoke ToolRegistry/PermissionEngine.
- Existing Tauri capabilities/permissions remain EMPTY.
- Existing production/dev CSP remains byte-identical: production connect-src 'none'.
- Never call Ollama, Colibri, models, agents, tools or databases directly.
- No FastAPI/HTTP/WebSocket/fetch/XHR/Tauri invoke/plugin/browser media API.
- No navigator.mediaDevices/getUserMedia, speechSynthesis, SpeechRecognition,
  MediaRecorder, AudioContext, global shortcut or native event hooks.
- No localStorage/sessionStorage, DB, file reads/writes, analytics, telemetry
  or external image/font downloads.
- No API/backend/Rust/migration/EventType changes.

The frontend must make no claim that any message was sent or voice recorded until
a separately authorized trusted runtime bridge exists.

## Exact PHX-081 scope

Only extend existing:
- `apps/desktop/src/App.tsx`
- `apps/desktop/src/styles.css`

Only evolve existing:
- `tests/workspace.test.mjs`

Documentation/status after PASS:
- `docs/21_FRONTEND.md`
- `DECISIONS.md` (one ADR-068 at most)
- `FEATURE_TICKETS.md`
- `tickets/PHX-081-chat-voice-ui.md`

Expected maximum SEVEN changed paths total (including ticket).

Do not create a new production TSX/component/test framework file without blocker
authorization. The local UI can stay self-contained in App.tsx for this ticket.

No direct or transitive npm/Python/Rust dependency promotion.
Do not modify any manifest/lockfile/config.
No Tauri settings/capability change.
No backend code/test change.
No other frontend navigation view IDs or labels change.

## Required Home / Chat presentation

Home view becomes a recognizably useful chat/conversation workspace:
- plain-text message transcript region, with semantic accessible chat title,
- separate clearly labelled local draft composer,
- 1..4000 non-whitespace content bound for actual send attempts,
- character-count/readable input-limit evidence,
- sensible empty transcript state explaining no backend connection,
- "Send" command visible but DISABLED when no trusted adapter/connection,
- no fake sent messages, assistant replies, prior history, generated timestamps,
- async pending state and duplicate-submit prevention only if adapter attached,
- explicit error/retry guidance with no raw native/private diagnostic leakage,
- draft retained on error/cancellation, cleared only on acknowledged send,
- no local persistent message history,
- scrollable bounded transcript area and text wrapping,
- no HTML/Markdown execution when rendering text.

Use native textarea and button semantics.
Textarea remains editable when disconnected, clearly labelled LOCAL DRAFT ONLY.
Disabled Send is not marketed as working.
Normal input and keyboard navigation work without a microphone.
Any keyboard send shortcut must obey exactly the same disabled/validation/pending
admission as clicking Send. No global keyboard listeners/hotkeys.
No unrequested auto-submit on page load or focus.

Local pure helpers should be exported where useful for deterministic Node testing,
but must never interpret user text as commands or use eval/dynamic code.

## Typed borrowed chat boundary (NO adapter implementation)

A strictly presentation-only, OPTIONAL caller-provided `chatPort` may be defined in
App.tsx, with fixed TypeScript shape such as:

- status: 'connected' | 'unavailable' | 'degraded'
- messages: readonly entries with unique opaque ID, role ('user' | 'assistant'),
  content (plain text), and no privileged fields
- sendText(text: string): Promise<void>

Use a deliberately narrow exact equivalent if it improves implementation.
Do not add timeout, polling, WebSocket or background transport.

The default App() MUST NOT supply a port; it remains truly disconnected.

When no port, or status !== connected:
- do not invoke callbacks,
- disable Send,
- do not append a user message to transcript,
- do not claim successful submission.

When explicit trusted port is present and connected (test fake only until a future
integration ticket):
- send only the validated trimmed text,
- one submission in flight,
- no double-send,
- keep draft on rejection; safe error message,
- clear draft on acknowledged successful Promise resolution, not before,
- NEVER manufacture an assistant response from local UI,
- message updates are driven solely by supplied snapshot, not local transcript
  mutation claimed to be backend success.

Do not interpret port status as proof of user identity or permission.
A future trusted app-composition layer, NOT PHX-081, must bind real transport;
the server remains owner of authentication, tool policy and audit.

If a borrowed port lacks a required method or malformed data, fail safely without
executing arbitrary objects or displaying raw exception text.
Use TypeScript types plus small runtime shape/limit checks where appropriate.
Do not persist port/messages outside component memory.

## Chat view data safety and bounds

Messages:
- only 'user' or 'assistant' display labels; no 'system' or 'developer' role
  impersonation from caller data,
- render as JSX text (React escaped), not dangerouslySetInnerHTML,
- no raw Markdown/HTML parsing, image/link injection, external URL loading,
- deterministic empty / unavailable / failed states,
- protect UI from unbounded messages and long unbroken strings,
- no clipping away security-critical permission/error text,
- no fabricated user messages to look like real server history.

No arbitrary HTML from source/assistant text.
No browser storage or timers to fabricate response streaming.

No background polling, hidden promise loops or inferred backend availability.

## Required voice conversation surface

Home chat workspace also presents an adjacent/stacked voice area:
- clear "Voice" heading and text-first fallback statement,
- an honest "Not connected" or "Voice unavailable" state by default,
- an explicit disabled microphone/listen button with accessible explanation,
- no microphone permission prompt,
- no capture, VAD, STT, TTS, voice playback, wake detection or synthesized
  voice transcript,
- no false "listening", "speaking" or "ready" state.

Optional static descriptive text can mention supported backend state vocabulary
sleeping/listening/thinking/speaking/degraded_voice/text_only from
docs/17_VOICE_SYSTEM.md as FUTURE capability reference, not live telemetry.

The separate "Voice" top-level navigation page stays the original PHX-080
not-connected placeholder; PHX-086 owns voice preferences/settings later.
No PHX-086 implementation.

The wake phrase "Rise, Phoenix." may appear only as explanatory future planned
behavior, never an assertion that recognition is running.

## PHX-080 shell preservation

Retain:
- fourteen exact view IDs and labels,
- home default; unknown fallback,
- Mission/Workspace/System groups,
- PHOENIX / Rise from the ashes branding,
- nav aria-current + native buttons,
- skip-to-main and committed-navigation focus,
- responsive reachable navigation at 800x600,
- other thirteen honest placeholder panels,
- no API/frontend privilege.

A new local chat textarea must NOT prevent navigation/skip link focus.
Preserve narrow accessibility/responsive CSS and reduced-motion treatment.
Voice controls must be disabled while unconnected, with accessible reason.

## Deterministic node test authority

`tests/workspace.test.mjs` currently contains 14 tests.

Preserve the three independent PHX-003 foundation/security tests in meaning and
keep their bodies unchanged:
- private linked npm workspace,
- empty Tauri IPC capability and restrictive production CSP,
- exact React-refresh dev CSP hash.

Preserve PHX-080's existing route/identity/unknown/focus/navigation/CSS/invariants
coverage. Only evolve now-obsolete Home-specific assumptions (e.g. test forbidding
all inputs/textarea, rigid AST helper call allowlist) so they meaningfully verify
the real new PHX-081 UI.

Do NOT disable the rule against backend/IPC/network/media APIs. Extend the
allowlist only for the exact permitted local React handlers/helper functions.

Add targeted tests for:
- default disconnected home chat and editable draft/disabled send,
- no fake transcript and no live voice claims,
- explicit microphone disabled explanation,
- plain text message rendering and untrusted markup literal safety,
- exact message role handling / bounded list,
- 4000 char validation, whitespace rejection,
- no send without connected borrowed port,
- connected fake-port send admission, pending guard, ack clearing, failure retaining
  draft and safe error (via pure helpers/AST or existing Node test tools),
- no auto-start audio or network,
- none of 14 view IDs/labels mutated,
- unknown route fallback, keyboard/skip focus,
- styles preserve 800x600 and mobile navigation,
- unchanged Tauri config/CSP/capabilities,
- unchanged package manifests and lockfiles,
- no extra frontend dependency.

Existing no-DOM-dependency Node SSR/TypeScript AST tests can test component
structure/pure intent helpers. Do not falsely claim SSR proves real native event
delivery; manual GUI acceptance stays pending.

Expect npm test count >14 (additional focused tests).
No new npm test runner or package.

## Historical guard preflight

PHX-080 introduced three narrowly authorized historical guard repairs:
- tests/test_prompt_injection_defenses.py,
- tests/test_screenshot_vision_loop.py,
- tests/test_windows_ui_automation.py.

All three already exclude EXACTLY:
- apps/desktop/src/App.tsx,
- apps/desktop/src/styles.css,

while protecting other desktop/backend paths.

PHX-081 may modify only these two previously-owned desktop sources plus the
already-owned workspace Node test and docs/status paths.

Therefore NO additional historical guard change is expected and NONE is authorized.

Re-run exact PHX-055/052/051 guard tests x3 and complete suites:
- prompt injection 604,
- screenshot/vision 397,
- Windows UI 493.

If another untouched historical guard fails, reproduce it and STOP BLOCKED for
authority before editing.

## Required validation gates

### Frontend / native

- `npm ci --ignore-scripts`, lock unchanged.
- `npm test` THREE consecutive complete passes, count >14 as tests added.
- `npm run typecheck --workspace @phoenix/desktop`.
- `npm run build --workspace @phoenix/desktop`.
- `npm run tauri:info --workspace @phoenix/desktop`.
- `cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml`.
- `cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml`.
- `npm run tauri:build --workspace @phoenix/desktop`.
- No GUI/dev server launched as acceptance evidence.

### Historical exact tests x3

- PHX-055 `test_protected_existing_sources_dependencies_schema_and_event_vocabulary`
- PHX-052 `test_no_action_persistence_or_dependency_change`
- PHX-051 `test_source_architecture_and_immutability`

### Historical whole suites

- prompt injection 604,
- screenshot vision 397,
- Windows UI 493,
preserving any existing skip/warning behavior.

### Core / media / voice regressions

Run individually:
- tests/test_api.py (8)
- tests/test_bootstrap.py (11)
- tests/test_agent_registry.py (96)
- tests/test_agent_model_router.py (213)
- tests/test_tool_registry.py (204)
- tests/test_permission_engine.py (926)
- tests/test_audit_trail.py (302)
- tests/test_secrets_boundary.py (402)
- tests/test_spotify_integration.py (345)
- tests/test_youtube_integration.py (358)
- tests/test_document_generation.py (284)
- tests/test_upload_parser_routing.py (336)
- tests/test_rise_phoenix_startup.py (509)
- tests/test_voice_conversation.py (950)
- tests/test_tts_provider.py (602)
- tests/test_stt_provider.py (414)
- tests/test_vad.py (403)
- tests/test_wake_word_provider.py (537).

### Full Python / quality

Pre-PHX-081 main baseline:
- 15,721 passed,
- 16 existing Windows filesystem/symlink skips,
- 1 existing Starlette/httpx warning.

- `uv sync --locked`
- full `uv run --locked pytest` THREE consecutive times.
- Expected Python count unchanged at 15,721 because production Python/test
  files untouched.
- Preserve exact skip identities/reasons/warning; zero new skip/xfail/flaky.
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- Python wheel/sdist outside repo, exact package/metadata parity.
- exact `/health` unchanged.
- security/credential scan.
- INV-001..024 individually reviewed.
- final full git diff.

## Scoped autonomous retry loop

Run an autonomous:
IMPLEMENT → TARGETED TEST → DIAGNOSE → MINIMAL IN-SCOPE REPAIR → RE-RUN → FULL GATES
loop for ordinary PHX-081-owned TypeScript, React, CSS, Node test, formatting,
or reproducible validation-command defects. Do not stop at the first repairable
failure. Preserve all current work.

For each failure capture exact command/evidence outside repository and classify.
Rerun the exact failing gate, then affected regressions, then any complete suites
invalidated by the repair. The final npm and full Python three-run gates must
be three consecutive full passes after the last relevant change.

STOP BLOCKED for:
- a required new/modified unauthorized path,
- missing backend/runtime bridge requiring privileged integration,
- security/permission/CSP violations,
- mutation of protected source or historical tests,
- missing native host prerequisite,
- three identical failures with no new evidence or meaningful progress.

No workaround through xfail, skips, warning suppression, test removal, broad
type-ignore/noqa, weaker guard exclusions or extra dependencies.
Do not loop endlessly without progress.

## ADR-068

Only after every gate passes, add exactly one accepted ADR-068 covering:
- honest presentation-only chat/voice UI,
- typed borrowed callback seam but no live backend binding,
- default disconnected and inert local draft,
- plain-text and role safety,
- optional connected fake-port test evidence vs real integration,
- explicit pending/ack/error semantics,
- voice unavailable/no microphone capture,
- PHX-080 navigation and historical guards unchanged,
- no API/Tauri/privileged changes or dependencies,
- remaining live acceptance limitations.

## Documentation and completion status

Only after all required gates pass:
- update docs/21_FRONTEND.md narrowly,
- mark this ticket DONE,
- mark PHX-081 DONE in FEATURE_TICKETS.md,
- Phase 8 stays INCOMPLETE (PHX-082..087 TODO),
- PHX-082 remains NOT STARTED,
- no later-ticket work.

## Expected final scope

Exactly at most SEVEN changed paths, including preparation ticket:
1. apps/desktop/src/App.tsx
2. apps/desktop/src/styles.css
3. tests/workspace.test.mjs
4. docs/21_FRONTEND.md
5. DECISIONS.md
6. FEATURE_TICKETS.md
7. tickets/PHX-081-chat-voice-ui.md

No new file without explicit blocker authority.
No manifest/lock/Tauri/Rust/Python source/test changes.
No editing historical guards or PHX-080 status.
No fake network/audio implementation.

## Final review/commit/push

Run `git diff --check`. Inspect every unstaged diff.
Stage only authorized files. Run:
- `git diff --cached --check`,
- `git diff --cached --stat`,
- `git diff --cached`.
Review all content for new privileges/claims, tests, style, dependency invariants.

Only after all gates PASS, create one completion commit:
`feat: complete PHX-081 chat voice ui`

Do NOT amend the preparation commit.

Push only branch `feature/PHX-081-chat-voice-ui`.
Normal push, no force push.
No PR/merge/main push.
No PHX-082.

Expected completion topology: 0 behind / 2 ahead main, clean working tree.

## Acceptance limitations

Report exactly:
- LIVE CHAT ACCEPTANCE: NOT IMPLEMENTED — backend POST /chat transport is not yet
  present. UI defaults disconnected; fake-port tests are not live integration.
- LIVE VOICE ACCEPTANCE: NOT IMPLEMENTED — microphone capture, wake, STT/TTS and
  backend voice binding are not provided in this frontend ticket.
- MANUAL DESKTOP CHAT/VOICE ACCEPTANCE: PENDING — no real GUI/WebView2 end-to-end
  interaction performed; deterministic SSR/AST/build evidence is not a substitute.

These are nonblocking for an explicitly presentation-only PHX-081, but must never
be called end-to-end chatbot or live voice success.

## Completion report

Provide:
1. STATUS (DONE/PASS or BLOCKED)
2. OFFICIAL LOCAL PATH
3. BRANCH
4. STARTING PREP HEAD
5. EXPECTED/OBSERVED MAIN
6. FINAL COMMIT
7. EXACT PATHS
8. APP SHELL PRESERVATION
9. CHAT VIEW LAYOUT
10. DRAFT / VALIDATION CONTRACT
11. DEFAULT DISCONNECTED STATE
12. CHAT PORT TYPE / NO REAL IMPLEMENTATION
13. SEND / PENDING / FAILURE SEMANTICS
14. TRANSCRIPT / ROLE PLAIN-TEXT RULES
15. ERROR PRIVACY / EMPTY STATE
16. VOICE PANEL
17. MIC / AUDIO PRIVILEGE BOUNDARY
18. TEXT-FIRST / KEYBOARD ACCESSIBILITY
19. RESPONSIVE CSS
20. NO PROVIDER/BACKEND/NETWORK
21. NO TAURI IPC/CSP CHANGE
22. NO DEPENDENCY CHANGE
23. NPM TEST RUN 1
24. NPM TEST RUN 2
25. NPM TEST RUN 3
26. TYPESCRIPT
27. VITE BUILD
28. TAURI INFO
29. CARGO CHECK
30. CARGO TEST
31. TAURI BUILD
32. PHX-055 EXACT GUARD x3
33. PHX-052 EXACT GUARD x3
34. PHX-051 EXACT GUARD x3
35. THREE HISTORICAL FULL SUITES
36. BACKEND/API/SECURITY REGRESSIONS
37. MEDIA/DOCUMENT REGRESSIONS
38. VOICE REGRESSIONS
39. FULL PYTHON RUN 1
40. FULL PYTHON RUN 2
41. FULL PYTHON RUN 3
42. RUFF
43. FORMAT
44. MYPY
45. PACKAGE / HEALTH
46. SECURITY SCAN
47. INV001..024 INDIVIDUAL
48. ADR-068
49. DOCUMENTATION
50. PHX-081 / PHASE8 STATUS
51. FINAL DIFF
52. COMMIT/PUSH
53. AHEAD/BEHIND
54. WORKING TREE / MAIN SAFETY
55. LIVE CHAT ACCEPTANCE
56. LIVE VOICE ACCEPTANCE
57. MANUAL GUI ACCEPTANCE
58. KNOWN LIMITATIONS
59. NEXT PHX-082 NOT STARTED

No PR, merge, main push or PHX-082.
