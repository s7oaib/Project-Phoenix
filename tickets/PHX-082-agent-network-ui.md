# PHX-082 — Agent Network UI

Status: DONE
Phase: 8 — Frontend
Branch: feature/PHX-082-agent-network-ui
Starting main/base SHA: 4656d7f87421c5b9db1512e8b3376ce27ab61e89
Expected unchanged main: 4656d7f87421c5b9db1512e8b3376ce27ab61e89

## Goal

Replace PHX-080's Agent Network placeholder with a useful, accessible, responsive
PHOENIX agent-directory UI. Display the 30 frozen canonical logical identities
(PHOENIX plus 29 specialists), their alias and professional role, enable local
search and safe local selection/detail inspection, and optionally present bounded
agent status evidence supplied by a trusted caller. No live backend transport,
no invented online states, and no ability to launch, command or authorize agents.

This is frontend PRESENTATION ONLY. Live agent-list, tasks and model/provider data
are not available from the current FastAPI app, which still exposes only GET /health.

## Authorities to read in full

- AGENTS.md, RULES.md, MENTAL_MODEL.md, INVARIANTS.md, MASTER_SPEC.md,
  PRD.md, ROADMAP.md, DECISIONS.md, FEATURE_TICKETS.md
- docs/01_ARCHITECTURE.md, docs/03_AGENT_ROSTER.md,
  docs/04_AGENT_ORCHESTRATION.md, docs/13_API_CONTRACTS.md,
  docs/20_UI_UX_SPEC.md, docs/21_FRONTEND.md, docs/25_DEPENDENCIES.md
- tickets/PHX-020-agent-registry.md, if named ticket exists (inspect rather
  than invent its spelling), tickets/PHX-080-app-shell-navigation.md,
  tickets/PHX-081-chat-voice-ui.md
- phoenix/agents/registry.py, tests/test_agent_registry.py,
  phoenix/api/app.py
- apps/desktop/src/App.tsx, apps/desktop/src/styles.css,
  apps/desktop/src/main.tsx, tests/workspace.test.mjs
- tests/test_prompt_injection_defenses.py,
  tests/test_screenshot_vision_loop.py,
  tests/test_windows_ui_automation.py,
  apps/desktop/src-tauri/tauri.conf.json

## Identity invariant — exactly 30

The canonical roster is the ordered tuple
`DEFAULT_AGENT_DEFINITIONS` in `phoenix/agents/registry.py`, and the
corresponding exact ordered table in `docs/03_AGENT_ROSTER.md`.

There are exactly 30 identities:
- 1 PHOENIX supreme orchestrator,
- 29 specialized roles.

Freeze internal IDs, display aliases, and professional roles exactly as the
source-of-truth roster. No alias-based identity or authority lookup.
No invented 31st agent, no omitted identity, no role reassignment.

The frontend may include a frozen display-only TypeScript roster constant
inside App.tsx. Do not import Python or load files dynamically at runtime.
Deterministic Node tests MUST prove exact tuple/order equality against the
canonical roster table read from docs/03_AGENT_ROSTER.md and additionally
verify the Python registry tuple/order correspondence using stable source or
existing canonical test evidence. Never allow presentation data to become a
separate authoritative agent registry.

AIZEN plans but PHOENIX decides. LEVI verifies; MAHORAGA recovery never
grants permissions; KAIDO degraded fail-safe only. No roster entry becomes
an executable agent merely by being rendered.

## Exact functional UI

The existing navigation's `agents` view becomes Agent Network with:
- heading "Agent Network",
- explicit static-roster information ("30 defined logical roles; live status
  unavailable until connected", or an equivalent honest statement),
- search field for alias, exact internal ID and professional role,
- visible match count and no-results state,
- stable ordered directory cards or rows for matching agents,
- per-agent selection button with accessible selected state,
- readable detail pane for currently selected agent:
  alias, internal ID and professional role; distinct PHOENIX supreme role,
- optional status/current-task/model-or-provider display ONLY from a separately
  trusted caller-provided snapshot,
- honest absence of runtime state by default ("Live agent state not connected"),
- no buttons purporting to activate/stop/dispatch/reassign/approve an agent.

Search:
- local React state only; no navigation or search endpoint,
- input strict string; maximum 80 UTF-16 code units,
- case-insensitive ASCII-friendly matching, safe for arbitrary Unicode,
- filter only the frozen display roster; no execution or dynamic import,
- default empty filter shows every canonical identity,
- no result is safe and informative,
- clearing filter restores all 30 in canonical order.

Selection:
- default selected agent is "phoenix",
- only a canonical internal ID may select a detail,
- unknown/alias/object selection cannot impersonate PHOENIX or create an agent,
- preserve selection across navigation while App remains mounted,
- if current selection is filtered out, detail selection remains explicitly
  distinguishable from matches or resets deterministically to a visible agent;
  avoid an inaccessible off-list selection,
- native buttons, keyboard Enter/Space semantics, visible focus,
  aria-pressed or equivalent for selected directory item,
- screen reader labels / correct landmark hierarchy,
- no global keyboard shortcut and no initial focus theft.

All content is plain text rendered via JSX escaping; no raw Markdown/HTML.

## Optional borrowed agent status snapshot, NOT a transport

App may accept a narrow optional `agentPort` or `agentSnapshot`, caller owned.
Prefer a passive readonly port shape without operations:
- status: 'connected' | 'unavailable' | 'degraded',
- agents: readonly status records containing `internalId`, `state`, optional
  `currentTask`, optional `provider` (or `modelProvider`).

The statuses are a CLOSED PRESENTATION vocabulary, not a claim that backend
currently emits them. Suggested accepted states when explicitly supplied:
'idle', 'working', 'waiting_permission', 'degraded'. Render them as labelled
borrowed data; never synthesize them as default live state.

Requirements:
- default App() supplies no agent port/snapshot,
- if port absent/unavailable/degraded, no "online/active" claims,
- only known canonical IDs accepted, no alias lookup,
- at most 30 snapshot entries, one per canonical ID,
- reject duplicate IDs, unsupported state, extra/malformed fields,
  oversized task/provider fields (max 240 and 120 UTF-16 units respectively),
  accessors/proxies throwing or misleading keys should not execute during
  data admission; use own data descriptor checks where appropriate,
- represent omitted/invalid entries via safe generic notice,
- never display private exceptions or raw malformed objects,
- absent per-agent evidence displays 'Status not reported', not 'idle',
- no automatic polling, timers, WebSocket, network, persistence or model probes,
- no dynamic status transition or tool/permission action,
- source state is not permission evidence and cannot grant execution.

If no optional borrowed snapshot is necessary for clean implementation,
a read-only typed passive boundary plus deterministic tests is preferred over
pretending to integrate with nonexistent backend API.

Do not write data back to the port.

## Preserve PHX-080 and PHX-081

Maintain exactly fourteen existing navigation view IDs/labels, all groups,
home default/unknown fallback, brand, skip-to-main, focus behavior, responsive
800x600 shell and no backend connectivity.

Home chat remains fully functional WITHIN ITS EXISTING PRESENTATION SCOPE:
- local draft input and limit 4000,
- optional borrowed ChatPort,
- disconnected default with disabled Send,
- pending/ack/error/cancel semantics,
- text-first fallback and disabled microphone,
- no fake assistant replies.

The other 12 non-Home/non-Agent views remain honest placeholders.
Do not implement PHX-083 Task Graph, PHX-084 Permissions, PHX-085 Files/Memory,
PHX-086 models/settings or PHX-087 observability.

Do not mutate PHX-081's frozen chat code except unavoidable narrow presentation
composition and safe agent-view routing.

## Security, privacy, execution boundaries

NO:
- backend API/HTTP/WebSocket/fetch/XHR/URL access,
- Ollama, Colibri, model provider, direct agent execution,
- local agent import/deserialization or Python execution,
- Tauri IPC/native shell/filesystem/clipboard/global shortcuts,
- permission decisions, tool calls, audit writes,
- browser storage, IndexedDB or DB,
- dynamic arbitrary JSX/HTML/Markdown execution,
- microphone/audio/media permissions,
- added runtime dependencies, new imports from external packages,
- Tauri CSP/capability/security changes,
- Rust, Python production, migration or EventType changes,
- fabricated online counts, tasks, providers, timers or telemetry.

The Tauri production CSP remains connect-src 'none', permissions remain empty.
No new backend route. Agent roster is presentation reference, not runtime truth.
The entire interface remains useful offline for examining defined agent roles.

## File scope

Modify only:
1. apps/desktop/src/App.tsx
2. apps/desktop/src/styles.css
3. tests/workspace.test.mjs

After EVERY required gate passes also:
4. docs/21_FRONTEND.md
5. DECISIONS.md — at most ADR-069, exact one new accepted ADR if implemented
6. FEATURE_TICKETS.md
7. tickets/PHX-082-agent-network-ui.md

MAXIMUM: SEVEN paths including authoritative preparation ticket.
No new files, manifests, lockfiles, Tauri/Rust/backend/source tests or dependencies.
No edits to existing historical Python guard files are authorized.

## Node test evolution

Starting `npm test`: 28 passed.
The `tests/workspace.test.mjs` test expecting all non-home views to be
placeholders becomes obsolete for `agents` specifically. Evolve that exact
assumption so all OTHER twelve remaining views still assert disconnected
placeholder behavior. Preserve all three original npm-workspace/Tauri
security-test bodies and every existing PHX-080/081 route, chat, accessibility
and security assertion.

Extend the existing TypeScript AST call allowlist only for exact local
search/selection/status validation helpers; retain bans on provider/network/
native/media/storage/dynamic code functions.

Add synthetic deterministic tests for:
- 30 canonical ID/alias/role ordered equality against roster,
- distinct PHOENIX supreme identity and 29 specialist count,
- no alias lookup for internal authority,
- accessible search input / no-results / match count,
- zero-query all-30, alias/ID/role search and case handling,
- query bounds and odd Unicode/HTML-looking strings,
- deterministic selected default, unknown/alias fallback,
- selected detail fields / aria-pressed focusable buttons,
- stable filter and view-switch selection semantics,
- default no backend state or fabricated availability,
- supplied passive port valid/invalid status and exact-ID matching,
- duplicate, unknown, extra-key, accessor/proxy, oversized, malformed
  or spoofed snapshot rejection,
- only supplied state/task/provider shown as escaped inert text,
- no privileged action controls, no network/backend/Tauri calls,
- unchanged 14 routes, unchanged Home chat and voice display,
- responsive 800x600 and small widths, keyboard focus, reduced motion,
- unchanged manifests/lockfiles/CSP/empty permissions.

Use only existing Node, TypeScript compiler AST and React SSR/hook harness where
appropriate; do not install a DOM/test framework.
SSR alone does NOT prove native WebView2 mouse/keyboard operation.
No fake production runtime adapters may be mounted by default.

## Historical guards — pre-authorized no-change regressions

These exact security guards already exclude ONLY App.tsx and styles.css
against origin/main while protecting every other existing file:
- PHX-055 `tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary`
- PHX-052 `tests/test_screenshot_vision_loop.py::test_no_action_persistence_or_dependency_change`
- PHX-051 `tests/test_windows_ui_automation.py::test_source_architecture_and_immutability`.

DO NOT edit these three test files.
Run each exact guard THREE consecutive times.
Run complete suites and require:
- prompt injection 604
- screenshot vision 397
- Windows UI automation 493
with unchanged skip/warning behavior.

Any new historical failure outside currently authorized frontend Node tests:
reproduce individually, STOP BLOCKED; do not alter additional historic files.

## Verification

### Frontend/native
- npm ci --ignore-scripts; manifests/lock unchanged.
- npm test THREE consecutive complete passes, final count >28.
- npm run typecheck --workspace @phoenix/desktop
- npm run build --workspace @phoenix/desktop
- npm run tauri:info --workspace @phoenix/desktop
- cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml
- cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
- npm run tauri:build --workspace @phoenix/desktop
- exact node tests of new agent roster/filter/selection/snapshots and
  preserved home chat passes.
- no native live GUI required for deterministic acceptance.

### Backend regressions
Run individually:
- tests/test_api.py — 8
- tests/test_bootstrap.py — 11
- tests/test_agent_registry.py — 96
- tests/test_agent_model_router.py — 213
- tests/test_tool_registry.py — 204
- tests/test_permission_engine.py — 926
- tests/test_audit_trail.py — 302
- tests/test_secrets_boundary.py — 402
- tests/test_spotify_integration.py — 345
- tests/test_youtube_integration.py — 358
- tests/test_document_generation.py — 284
- tests/test_upload_parser_routing.py — 336
- tests/test_rise_phoenix_startup.py — 509
- tests/test_voice_conversation.py — 950
- tests/test_tts_provider.py — 602
- tests/test_stt_provider.py — 414
- tests/test_vad.py — 403
- tests/test_wake_word_provider.py — 537.

### Python/quality/security
Main baseline: 15,721 passed, 16 existing Windows skips, one existing
Starlette/httpx warning.
- uv sync --locked
- full uv run --locked pytest THREE consecutive complete passes
- exact same 16 skip identities/reasons and warning
- no new skip, xfail, flaky, retry, warning suppression
- uv run --locked ruff check .
- uv run --locked ruff format --check .
- uv run --locked mypy
- Python wheel/sdist build outside repository; exact source and dependency
  byte parity with main, and unchanged /health.
- credential and private artifact signature scan,
- INV-001 through INV-024 individually evidenced,
- full git diff review.
All external validation receipts outside repository.

## Bounded autonomous repair loop

IMPLEMENT → TARGETED VERIFY → REPRODUCE FAILURE → CLASSIFY → REPAIR WITHIN SEVEN
PATHS → RETEST → ALL REQUIRED GATES, repeatedly for authorized TypeScript/React/
CSS/Node-contract/lint/build defects.

Do not stop on a routine in-scope deterministic failure.
After fixes rerun all affected gates; final npm x3 and Python x3 are three
complete consecutive passing runs after the last relevant source/test change.

STOP BLOCKED immediately if a fix requires an eighth file, protected historical
test edit, backend/native privilege, architecture change, new dependency or absent
host prerequisite. Also stop after three identical failures with no meaningful
progress or new evidence. Do not loop indefinitely; never suppress/remove
security tests or hide errors to make a green result.

## ADR-069 and status

Only after every gate passes:
- add accepted ADR-069 documenting canonical display directory,
  thirty identities, internal ID/alias distinction, static roster with exact
  authoritative parity tests, borrowed passive status-only evidence and default
  disconnected state, local search/selection, no agent execution or privileges,
  no new dependency/transport, preserved chat and Tauri boundaries.
- update docs/21_FRONTEND.md narrowly;
- mark PHX-082 ticket DONE and FEATURE_TICKETS PHX-082 DONE;
- Phase 8 stays INCOMPLETE (PHX-083..087 TODO);
- no PHX-083 implementation or preparation by Codex.

## Completion / Git

Before completion inspect all seven-file diffs and authorized limits.
`git diff --check`, `git diff --cached --check`, staged stat/full diff.
No generated dist/target/node_modules/logs/receipts/artifacts/secrets.

Only after all gates pass: ONE completion commit:
`feat: complete PHX-082 agent network ui`

Push feature branch only, normal non-force push. No main push, PR, merge or PHX-083.

Expected completed branch exactly 0 behind / 2 ahead main, clean tree.

## Truthful acceptance

LIVE AGENT STATUS ACCEPTANCE: NOT IMPLEMENTED — the backend GET /agents endpoint
and live agent status transport are not implemented; UI presents a static
authoritative roster plus optional synthetic supplied snapshot tests only.

LIVE AGENT CONTROL ACCEPTANCE: NOT IMPLEMENTED — no agent dispatch,
execution, reconfiguration, start/stop or permission operation is exposed.

MANUAL NATIVE AGENT NETWORK ACCEPTANCE: PENDING — no verified live WebView2
mouse/keyboard end-to-end test; SSR/pure/AST/native build are not equivalent.

Those are nonblocking for this presentation-only ticket.

## Full final report required

1. STATUS
2. OFFICIAL PATH
3. BRANCH
4. PREPARATION SHA
5. EXPECTED/OBSERVED MAIN
6. COMPLETION SHA
7. EXACT PATHS
8. CANONICAL 30 / 29+1
9. ROSTER PARITY
10. ROLE/ALIAS/INTERNAL-ID DISTINCTION
11. DIRECTORY LAYOUT
12. SEARCH/FILTER
13. SELECTION/DETAIL
14. ACCESSIBILITY
15. RESPONSIVE CSS
16. DEFAULT UNAVAILABLE STATE
17. BORROWED PASSIVE STATUS CONTRACT
18. SNAPSHOT ADMISSION / BOUNDS / FORGERY
19. TEXT INERTNESS
20. NO EXECUTION/PRIVILEGE
21. PHX-080 SHELL REGRESSION
22. PHX-081 CHAT/VOICE REGRESSION
23. DEPENDENCIES/LOCKS
24. TAURI CSP/CAPABILITIES
25. NPM TEST RUN 1
26. NPM TEST RUN 2
27. NPM TEST RUN 3
28. TYPECHECK
29. VITE BUILD
30. TAURI INFO
31. CARGO CHECK
32. CARGO TEST
33. TAURI BUILD
34. PHX-055 EXACT x3
35. PHX-052 EXACT x3
36. PHX-051 EXACT x3
37. THREE HISTORICAL FULL SUITES
38. BACKEND/SECURITY REGRESSIONS
39. MEDIA/DOCUMENT REGRESSIONS
40. VOICE REGRESSIONS
41. FULL PYTHON RUN 1
42. FULL PYTHON RUN 2
43. FULL PYTHON RUN 3
44. RUFF
45. FORMAT
46. MYPY
47. PACKAGE / HEALTH
48. SECURITY SCAN
49. INV-001..024 INDIVIDUAL
50. ADR-069
51. DOCUMENTATION
52. TICKET/PHASE STATUS
53. FINAL DIFF
54. COMPLETION COMMIT/PUSH
55. AHEAD/BEHIND
56. CLEAN TREE / UNCHANGED MAIN
57. LIVE AGENT STATUS ACCEPTANCE
58. LIVE AGENT CONTROL ACCEPTANCE
59. MANUAL GUI ACCEPTANCE
60. KNOWN LIMITATIONS
61. NEXT PHX-083 NOT STARTED.

No PR, merge, main push or PHX-083.

## Completion evidence — 2026-10-08

Started clean on feature/PHX-082-agent-network-ui at preparation
2047cca5aa87f949a0f74d3ca23a6c180246862e, main
4656d7f87421c5b9db1512e8b3376ce27ab61e89. Initial topology 0 behind / 1 ahead,
only this ticket beyond main. No reset/rebase/amend/force/main mutation/PR/merge.

Implemented only the agents directory in App.tsx/styles.css: all 30 exact ordered
ID/alias/role triples, sole PHOENIX supreme designation, local 80-UTF16-unit search,
match/no-results/clear behavior, native aria-pressed canonical-ID selection and detail
inspection. App-owned search/selection survives navigation; unknown/alias selection
retains last valid identity. Off-list selection is explicitly labelled. Search and
status are independent of unchanged PHX-081 draft/flight/chat helpers.

Optional passive readonly AgentPort exposes status and agents only, no operations.
Default has no port and displays Live agent state not connected / Status not reported.
Only explicitly connected validated borrowed data can show supplied state/task/provider.
At most 30 unique known IDs, four closed states, exact own data fields, task<=240 and
provider<=120 UTF16; any malformed/duplicate/unknown/extra/accessor/sparse/oversize entry
rejects the entire snapshot with generic notice. No writeback, raw errors or text clipping.
React escapes text literally. Reflective proxy failures are safe; arbitrary same-realm
Proxy reflective traps are not sandboxed and forwarding proxies are indistinguishable
from ordinary data. Trusted caller infrastructure is required; status grants no authority.

Native search/buttons/details and polite match/selection announcements, wrapped plain
text and bounded result scrolling remain accessible. Responsive stacking below 1000px
covers unchanged 800x600 shell; small search layout, focus and reduced motion retained.
All 14 routes/labels/brand/groups/default/fallback/skip/focus remain unchanged; Home
chat draft 4000, borrowed ChatPort, pending/duplicate/ack/error/local cancellation,
escaped transcript and disabled microphone remain intact. Other 12 views unconnected.

### Commands and results

- npm ci --ignore-scripts PASS; manifests/lock unchanged; zero audit vulnerabilities.
- npm test THREE consecutive final runs: 45 passed each, zero failures/skips.
- npm run typecheck --workspace @phoenix/desktop: PASS.
- npm run build --workspace @phoenix/desktop: PASS.
- npm run tauri:info --workspace @phoenix/desktop: PASS.
- cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml: PASS.
- cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml: PASS; zero Rust tests.
- npm run tauri:build --workspace @phoenix/desktop: PASS; static release executable.
- Exact PHX-055/052/051 guards: 1 passed x3 each; files byte-identical to main.
- Complete prompt/screenshot/Windows suites: 604/397/493 passed.
- All 18 individual regressions PASS: API 8, bootstrap 11, registry 96, router 213,
  ToolRegistry 204, permissions 926, audit 302, secrets 402, Spotify 345, YouTube 358,
  document generation 284, upload routing 336, startup 509, conversation 950,
  TTS 602, STT 414, VAD 403, wake 537.
- Full uv run --locked pytest THREE consecutive final runs: 15,721 passed each,
  identical 16 existing Windows skip identities/reasons and one Starlette/httpx warning
  compared with PHX-081/main baseline. No new skip, xfail, filter, retry or workaround.
- uv sync --locked PASS: 49 unchanged packages.
- uv run --locked ruff check . PASS.
- uv run --locked ruff format --check . PASS: 133 files already formatted.
- uv run --locked mypy PASS: 127 source files.
- uv build --out-dir <external-validation>/dist PASS: wheel and sdist.
  All 77 Python source files byte-equal to worktree/Git-normalized main; direct
  dependency metadata and Requires-Python >=3.11,<3.12 unchanged; no private artifacts.
- GET /health exact regression PASS and API bytes unchanged.
- Added-line credential signatures, exact source/CSP/empty permissions protection,
  EventType 32/sole Alembic head 0005_audit_events and all 24 invariant reviews PASS.
- git diff --check and complete source/test/documentation/staged review PASS.

All ordinary implementation gates passed without a repair failure. Seventeen focused
agent tests supplement every existing shell/chat/voice contract. Narrowly exclude
agents from the obsolete all-nonhome-placeholder assertion; exact local helper calls
extend the AST allowlist while network/native/provider/storage/code bans remain.
Three original npm-workspace/Tauri security test bodies unchanged. Chat code block
byte equality to main is tested. Hook handlers exercise actual local search/select/
clear/navigation and prior composer behavior, not native DOM/WebView2 evidence.

Receipts, JUnit, package proofs, gate ledger, failure ledger and invariant review live
outside the repository at C:/Users/shoai/.codex/validation/PHX-082-20261008.
Exactly seven final paths: apps/desktop/src/App.tsx, apps/desktop/src/styles.css,
tests/workspace.test.mjs, docs/21_FRONTEND.md, DECISIONS.md, FEATURE_TICKETS.md and
this ticket. No Python/historical test/backend/Rust/config/manifest/lock changes.
ADR-069 is the sole new accepted decision. PHX-082 DONE; Phase 8 INCOMPLETE;
PHX-083..087 TODO. One completion commit feat: complete PHX-082 agent network ui
and normal feature-only push, remote equality, clean 0-behind / 2-ahead topology
and unchanged main are verified in the final delivery report.

### Individual invariant evidence

| Invariant | Concrete evidence |
|---|---|
| INV-001 | Exact 30-entry frontend/document/Python ordered parity and sole phoenix Supreme orchestrator checked by npm; full orchestrator tests pass, production unchanged. |
| INV-002 | strategist/AIZEN retains Chief strategist/master planner metadata and subordinate designation; no executable authority; registry 96/router 213 and full planner tests pass. |
| INV-003 | No model/permission calls or decisions in App; PermissionEngine 926 passed. |
| INV-004 | App has no tool/IPC imports; ToolRegistry 204 and PermissionEngine 926 passed. |
| INV-005 | No approve/deny controls; sensitive user correlation protected by PermissionEngine 926. |
| INV-006 | No privileged execution path; sensitive/blocked permission and P5 secret tests unchanged and passed. |
| INV-007 | AuditTrail 302 passed; audit/persistence source unchanged; no UI privileged actions. |
| INV-008 | All required gates have recorded zero exits before DONE; live agent status/control, chat/voice and manual acceptance explicitly remain unimplemented/pending. |
| INV-009 | Recovery source unchanged; positive-budget and bounded-replan tests included in full pytest. |
| INV-010 | No recovery mode controls; KAIDO degraded-only tests included in full pytest; source unchanged. |
| INV-011 | Exact npm AST call allowlist permits React/local helpers and one supplied sendText call only inside sendDraft; provider/network/native/media/storage APIs banned; CSP connect-src none and empty capabilities unchanged. |
| INV-012 | No provider or agent production edits; registry 96 and router 213 passed. |
| INV-013 | No tool/policy production edits; ToolRegistry 204 passed, inert permission/audit metadata protected. |
| INV-014 | Only exact canonical IDs select details/status; every alias lookup rejected by npm; Python alias-removal/collision registry tests pass; 14 routes unchanged. |
| INV-015 | Native controlled local textarea remains editable while disconnected; named form Send and all 14 nav buttons work without a microphone; disabled voice explained. |
| INV-016 | No voice initialization in shell; startup 509, conversation 950, TTS 602, STT 414, VAD 403, wake 537 passed. |
| INV-017 | Snapshot roles limited to user/assistant, exact data fields, count 64, content 4000, ID 128, unique IDs; malformed/oversize entries omitted with notice, complete accepted text React-escaped; no parsing or external resources. Agent snapshots admit at most 30 unique known IDs, 4 closed states, own data fields only, task<=240/provider<=120 UTF16; invalid whole snapshot rejected with generic notice; JSX escaping tested. Prompt 604, upload 336 passed. |
| INV-018 | No storage or backend in App; SecretStore 402 passed; memory/security production unchanged. |
| INV-019 | Caller-triggered one-flight send exposes pending state and Cancel waiting. Local cancellation retains draft even after late ack, explicitly cannot abort delivery, and duplicates remain blocked until adapter settles; no timer/polling/hidden worker. Hook/helper tests pass. |
| INV-020 | No migration changes; historical guard preserves sole Alembic head 0005_audit_events and EventType 32, exact test x3 passed. |
| INV-021 | Only connected supplied Promise<void> ack can clear matching draft; synchronous/thenable/malformed result and exceptions fail safely, retain draft, never append transcript; all provider/tool regressions pass. |
| INV-022 | Unknown view falls back Home; unknown/alias agent selection retains last valid identity, no identity invention; missing evidence Status not reported, disconnected/degraded no active claims; off-list selection explicitly labelled; chat/voice fallback preserved. |
| INV-023 | No model generation path; phoenix/models/lifecycle.py default capacity one remains unchanged; lifecycle tests in full pytest. |
| INV-024 | All npm/Python manifests and locks unchanged; existing-dependency UI only; package dependency metadata equals main. |

### Acceptance limitations

LIVE AGENT STATUS ACCEPTANCE: NOT IMPLEMENTED — no live GET /agents transport is implemented.
LIVE AGENT CONTROL ACCEPTANCE: NOT IMPLEMENTED — no agent execution, dispatch or permission actions are exposed.
MANUAL NATIVE AGENT NETWORK ACCEPTANCE: PENDING — no real end-to-end WebView2 interaction has been independently verified.

Snapshots are structural display evidence, not authenticated activity or a JavaScript
sandbox. No live backend/provider/model integration, polling, agent control or native
GUI end-to-end acceptance. Existing live chat/voice remain unimplemented. PHX-083
NOT STARTED. No PR, merge or main push.
