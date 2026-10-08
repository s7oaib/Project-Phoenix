# PHX-080 — App shell / navigation

Status: DONE
Phase: 8 — Frontend
Branch: feature/PHX-080-app-shell-navigation
Starting main/base SHA: 4a70867203a70d3a85e2c141ef0dbb92bdff42e2
Expected main: 4a70867203a70d3a85e2c141ef0dbb92bdff42e2

## Objective

Replace the PHX-003 static foundation landing page with a polished, accessible,
responsive PHOENIX desktop APP SHELL and working, local UI-only navigation.

PHX-080 establishes composition, top-level IA/navigation, selected-view state,
honest placeholder screen boundaries, accessibility, responsive design and
error-safe presentation. It does NOT implement the screens owned by PHX-081..087
or make backend/API/privileged OS/LLM/tool calls.

PHOENIX remains the single user-facing identity and supreme orchestrator.
All agent role names remain presentation only.

## Authoritative sources

Read AGENTS.md, RULES.md, MENTAL_MODEL.md, INVARIANTS.md, MASTER_SPEC.md, PRD.md,
ROADMAP.md, DECISIONS.md, FEATURE_TICKETS.md,
docs/01_ARCHITECTURE.md, docs/13_API_CONTRACTS.md, docs/20_UI_UX_SPEC.md,
docs/21_FRONTEND.md, docs/25_DEPENDENCIES.md,
tickets/PHX-003-tauri-react-desktop-foundation.md,
apps/desktop/src/App.tsx, apps/desktop/src/styles.css,
apps/desktop/src/main.tsx, apps/desktop/package.json,
apps/desktop/src-tauri/tauri.conf.json, tests/workspace.test.mjs,
tests/test_prompt_injection_defenses.py.

The Phase 8 sequence stays:
PHX-080 app shell/navigation,
PHX-081 chat/voice,
PHX-082 agent network,
PHX-083 task graph,
PHX-084 permission center,
PHX-085 memory/files,
PHX-086 models/voice/settings,
PHX-087 observability/developer.

Do not pre-implement any of those feature tickets.

## Frozen frontend/backend privilege boundary

PHX-080 is CLIENT-SIDE VIEW COMPOSITION ONLY:
- no direct Ollama/Colibri/model calls,
- no FastAPI/HTTP/WebSocket call yet,
- no SQLite/document/memory/tool call,
- no Tauri invoke, events, filesystem/shell/clipboard, opener, plugins or native command,
- no approval/deny execution,
- no agent/tool policy in frontend,
- no permission/health/voice state fabricated from placeholders.

The existing restrictive Tauri production/development CSP, empty capabilities and
zero IPC/plugin grants are preserved byte-for-byte.

No backend, migration, EventType, Rust or tool change.

Future frontend state will arrive only through the Phoenix backend transport under
separately authorized tickets; no direct privileged tool/provider imports.

## Explicit frozen dependency policy

PHX-080 uses existing React 19, TypeScript 6, Vite 8, CSS and npm workspace only.

NO new npm dependencies in PHX-080:
- no React Router,
- no Tailwind installation,
- no shadcn,
- no Zustand,
- no TanStack Query,
- no React Flow,
- no icon/animation/test/DOM framework,
- no Tauri plugin.

These remain architecture choices for later justified tickets, not silent mandatory
PHX-080 promotions.

MUST remain unchanged:
- package.json (root and desktop),
- package-lock.json,
- pyproject.toml,
- uv.lock,
- Cargo.toml,
- Cargo.lock,
- tauri.conf.json,
- Vite config,
- TypeScript config,
- desktop Rust source,
- index.html, unless separately authorized as a blocker.

## App shell UX

Use a coherent, original PHOENIX visual identity:
- deliberate dark/cinder-neutral background with restrained amber accent,
- readable contrast and typography (system fonts only),
- calm product-UI density and legible hierarchy,
- not an Avengers/JARVIS imitation,
- no remote fonts, logos or images,
- no unverified live-stat badges, fake assistant messages or fabricated tasks.

Required major regions:
1. persistent PHOENIX brand and concise local-first identity,
2. semantic primary navigation with clear selected page,
3. main landmark containing the selected title/placeholder,
4. visible honest integration-state wording: frontend shell only / backend not connected,
5. responsive narrow-width layout (including existing 800x600 Tauri window),
6. meaningful empty/not-yet-wired state without pretending a feature works.

Retain PHOENIX as the single product identity and preserve the phrase
Rise from the ashes. as branding, not as a fabricated actual wake/status assertion.

## Closed V1 navigation information architecture

Exactly fourteen top-level view IDs (stable internal values):

- onboarding
- home
- agents
- tasks
- memory
- files
- computer
- research
- media
- permissions
- models
- voice
- settings
- observability

Human-readable labels aligned to docs/20_UI_UX_SPEC.md:
- Onboarding
- Home / Chat
- Agent Network
- Task Graph
- Memory
- Files / Documents
- Computer Control
- Research
- Media
- Permissions
- Models
- Voice
- Settings
- Observability / Developer

Group navigation meaningfully, such as Mission, Workspace, System; group design is
presentation-only. Each stable ID has exactly one nav item and one corresponding
placeholder main-panel identity.

No alias grants authority; no agent is instantiated.

Default selected view: home.

Unknown view IDs fail safe to home in the pure route resolver; no dynamic execution
or arbitrary component names from input.

No URL/router/history synchronization for PHX-080.
No localStorage/sessionStorage persistence.
Selected view is local React UI state only.

## Navigation interaction

Clicking/activating a nav button changes the rendered main panel, selected
aria-current/page state and heading. Navigation is genuine and not decorative.

Support native keyboard Button Enter/Space semantics and visible focus treatment.
Provide skip-to-main link. Main title or main region must be focusable after a
view change without hiding or trapping focus. Do not install global keyboard hooks.

Provide a plain text interface path usable without microphone/voice.

Buttons are buttons (no href="#" pseudo-links). No dead-end action controls
advertising backend execution. Disabled/pending feature controls are not needed;
the navigational shell itself stays usable.

Use simple React useState/useRef and pure helpers. No router/state lib.

All interactive controls must have accessible names.
Use aria-current="page" or equivalent consistent active navigation evidence.
Use aria-label for major nav groups if appropriate.
Use proper headings/nav/main/aside/section semantics.
No misleading role=alert for normal placeholders.
An optional polite live region may announce selection changes.

## Architecture / file boundaries

Prefer a small self-contained shell in:
- apps/desktop/src/App.tsx
- apps/desktop/src/styles.css

Retain:
- apps/desktop/src/main.tsx
- src-tauri sources/config.

App.tsx may export pure route ID/normalization metadata helpers useful for testing.
Prefer not to create a separate app/router/component framework for PHX-080.

Avoid unused placeholder import trees.

Code must pass current strict TypeScript, including
noUncheckedIndexedAccess and noUnusedLocals.

CSS:
- responsive wide and narrow layout,
- no horizontal body overflow,
- sidebar/nav remains reachable at narrow widths,
- visible focus, hover/active separation,
- reduced-motion support,
- high contrast readability,
- no reliance on nonlocal assets or CSS inline style exemptions,
- no animations necessary for functional navigation.

The existing Tauri main window dimensions stay unchanged; adapt to them rather
than changing native window authority/config just for this shell.

## Honest placeholder contract

Every view except home may render a distinctive title, purpose sentence and
"This workspace is not connected yet" or equivalent explicit placeholder.

Home may show shell overview and forthcoming Phase 8 feature areas, but must NOT:
- claim PHOENIX backend online,
- claim agents online,
- claim voice listening,
- claim tasks running,
- claim permissions approved,
- claim memory synced,
- claim models installed/available,
- start or simulate chat/tool actions.

The navigation tests must distinguish REAL local view selection from NOT IMPLEMENTED
backend functionality.

No user-provided HTML dangerouslySetInnerHTML.

## PHX-003 workspace test evolution — explicitly authorized

File:
tests/workspace.test.mjs

The original test "foundation screen renders semantic text without interactive feature
controls" asserts the old no-nav placeholder and calls the component function
directly. This is intentionally obsolete for PHX-080.

Replace only this obsolete screen-test contract with semantic app-shell/navigation
assertions that:
- compile current TSX with the existing TypeScript/Vite/React dependencies,
- render via React.createElement(App) / renderToStaticMarkup instead of direct
  hook-bearing function invocation,
- verify main landmark, PHOENIX identity, brand phrase, nav and selected home,
- verify exactly fourteen canonical views and unique IDs,
- verify pure unknown-view fallback,
- verify all menu items remain reachable by button/keyboard semantics,
- verify placeholders do not claim live backend/agent/voice/permission success,
- verify strict accessibility names/current page and skip-to-main/focus support,
- verify no privilege/HTTP/WebSocket API call/import introduced,
- verify responsive/reduced-motion CSS markers.

No new test library or real GUI/browser required; deterministic Node SSR/source/AST
tests are acceptable with the existing runtime. Do not claim SSR alone proves
native end-to-end mouse/keyboard interaction. The React event handler wiring must
be inspected/tested through pure helper/AST evidence as feasible.

Preserve the three other existing workspace tests unchanged in meaning:
- private linked npm workspace,
- empty Tauri IPC capabilities and strict production CSP,
- React-refresh dev CSP hash.

No removal or weakening of CSP/no-IPC assertions.

Current starting npm test baseline: 4 passed. Final count may increase.

## PHX-055 historical frontend guard — explicitly authorized narrow repair

File:
tests/test_prompt_injection_defenses.py

Only test:
test_protected_existing_sources_dependencies_schema_and_event_vocabulary

The existing test compares all preexisting apps/desktop files to origin/main.
This would reject PHX-080's legitimate app-shell changes.

Preserve all existing exact current-main equality protection for:
- phoenix/ existing production,
- alembic/ existing schema,
- apps/desktop/ existing files OTHER THAN:
  - apps/desktop/src/App.tsx
  - apps/desktop/src/styles.css.

Exclude EXACTLY those two PHX-080-owned frontend files from the historical
full-file equality loop.

Do not exclude any other desktop file.
No broad apps/desktop removal.
No backend guard relaxation.
Preserve EventType = 32 and Alembic sole-head 0005_audit_events assertions.
Preserve every other prompt-injection/security test.

Add a narrow explanatory comment that PHX-080 owns exactly those two shell paths
and workspace tests own their new UI/security assertions.

Run the repaired exact historical test THREE consecutive times.
Run complete tests/test_prompt_injection_defenses.py THREE consecutive times,
current baseline 604 passed each.

Any other historical test requiring authorized evolution must be individually
reproduced and STOP BLOCKED; no ad-hoc historical change.

## Additional PHX-080 tests

Expand tests/workspace.test.mjs or add ONLY minimal Node test file if unavoidable.
No frontend test framework dependency.

Test:
- brand/semantic hierarchy,
- fourteen ID uniqueness and canonical labels,
- initial home view and selected marker,
- unknown ID fallback,
- view-to-heading/placeholder correlation through pure testable components,
- React selected-view state update wiring,
- keyboard-native nav buttons/focus handling,
- skip link and main landmark,
- markup contains no mocked backend claims,
- no Tauri IPC/API/provider/tool imports/calls,
- no eval/unsafe HTML,
- responsive CSS breakpoint/reduced-motion/focus-visible,
- no package manifest/lock/CSP/Rust changes,
- status/docs scope.

Tests should not depend on remote browsers, local GUI interaction, WebView2 automation,
internet access, filesystem tool/permission backend or fake privileged endpoints.

## Integration / regression gates

Required before completion:

### Frontend
- npm ci --ignore-scripts (use approved lock; no lock update)
- npm test THREE consecutive times; all workspace tests pass,
- npm run typecheck --workspace @phoenix/desktop,
- npm run build --workspace @phoenix/desktop,
- npm run tauri:info --workspace @phoenix/desktop where available,
- cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml,
- cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml,
- npm run tauri:build --workspace @phoenix/desktop (no bundle, if existing
  Windows prerequisites present); do not launch a long-running GUI or dev server.

If a native host prerequisite is genuinely missing, distinguish a platform/environment
blocker and STOP BLOCKED rather than report fabricated pass.

### Historical guard
- exact PHX-055 repair test x3,
- entire prompt-injection suite x3, baseline 604 each.

### Core regressions
Run individually:
- tests/test_api.py,
- tests/test_bootstrap.py (foundation exact current dependency contract),
- tests/test_agent_registry.py, baseline 96,
- tests/test_agent_model_router.py, baseline 213,
- tests/test_tool_registry.py, baseline 204,
- tests/test_permission_engine.py, baseline 926,
- tests/test_audit_trail.py, baseline 302,
- tests/test_secrets_boundary.py, baseline 402,
- tests/test_spotify_integration.py, baseline 345,
- tests/test_youtube_integration.py, baseline 358,
- tests/test_document_generation.py, baseline 284,
- tests/test_upload_parser_routing.py, baseline 336,
- protected Phase-6 startup/conversation/TTS/STT/VAD/wake suites:
  509/950/602/414/403/537.

### Full Python and quality
- uv sync --locked, pyproject.toml/uv.lock unchanged,
- full Python pytest THREE consecutive times,
- current main baseline 15,721 passed, 16 existing Windows filesystem/symlink
  skips, one existing Starlette/httpx warning,
- same skip identities/reasons and warning only,
- zero new skips/xfails/retries/flaky markers,
- uv run --locked ruff check .,
- uv run --locked ruff format --check .,
- uv run --locked mypy,
- Python wheel/sdist build outside repository and package byte parity,
- /health unchanged,
- credential/signature scan,
- INV-001..024 individually reviewed,
- complete scope/diff review.

Node validation artifacts stay outside repository.
No generated dist/target/node_modules/.venv files committed.

## ADR-067

Only after all gates pass, add at most ONE accepted ADR-067 documenting:

- PHX-080 shell/navigation only,
- exact fourteen stable IDs and local state,
- React/CSS with no new frontend dependencies,
- responsive/accessibility/focus strategy,
- untrusted placeholders versus real backend state,
- no backend/HTTP/WebSocket/Tauri privilege,
- existing CSP/capabilities unchanged,
- PHX-003 obsolete screen-test evolution and PHX-055 exact two-path guard exclusion,
- no PHX-081..087 feature implementation.

## Documentation and status

Only after all gates pass:
- update docs/21_FRONTEND.md narrowly to describe implemented PHX-080 shell,
  deferred data/state transports and later ownership,
- PHX-080 ticket status DONE,
- FEATURE_TICKETS PHX-080 DONE,
- Phase 8 remains INCOMPLETE because PHX-081..087 TODO,
- PHX-081..087 remain TODO,
- no PHX-081 implementation and no next-ticket preparation by Codex.

## Allowed final diff

Exactly expected files:
- apps/desktop/src/App.tsx
- apps/desktop/src/styles.css
- tests/workspace.test.mjs
- tests/test_prompt_injection_defenses.py
- docs/21_FRONTEND.md
- DECISIONS.md
- FEATURE_TICKETS.md
- tickets/PHX-080-app-shell-navigation.md

Maximum expected final scope: 8 paths.

No new dependencies, manifests, lockfiles, Tauri configuration, Rust, API, Python
production, database/migrations, tool/permission/model/agent/memory/voice/media or
document implementation.

If a new file is truly necessary, STOP BLOCKED and report rationale rather than
silently expanding scope.

Historical guard test diff must be exactly its authorized two-path exclusion and comment.
Workspace tests may evolve shell assertions but retain the three security/foundation
tests' meaning.

## Completion gate and commit

Only after all gates pass:
1. mark ticket DONE, PHX-080 DONE, Phase 8 INCOMPLETE,
2. add ADR-067 only if supported by implementation/evidence,
3. full git diff --check,
4. review all unstaged source/status/docs/test changes,
5. stage ONLY eight authorized files,
6. git diff --cached --check, --stat and full content review,
7. create EXACT completion commit:

feat: complete PHX-080 app shell navigation

Do NOT amend preparation commit.

Push only:
feature/PHX-080-app-shell-navigation

Normal push; no force push, PR, merge, main, PHX-081.

Expected completed branch:
- 0 behind main,
- exactly 2 ahead main (ticket prep + completion).

## Failure handling

If any required gate fails:
- reproduce individually,
- STOP BLOCKED,
- preserve local working tree,
- PHX-080 stays TODO,
- Phase 8 INCOMPLETE,
- no completion commit/push,
- no PHX-081.

No unrelated historical repair.

## Live acceptance

No live backend integration or native GUI automation is required for deterministic
app-shell completion. Report truthfully:

LIVE DESKTOP NAVIGATION ACCEPTANCE: PENDING — deterministic React render/navigation
contract tests and static Tauri build passed; manual end-to-end WebView2 mouse/keyboard
smoke has not been performed.

BACKEND-CONNECTED FRONTEND ACCEPTANCE: NOT IMPLEMENTED — PHX-080 is a local UI-only
app shell. Later Phase 8 tickets own transport, real state and action binding.

Those two are nonblocking after all ticketed deterministic/native build gates pass.

## Completion report

Return at minimum:
1. PHX-080 STATUS
2. OFFICIAL LOCAL PATH
3. BRANCH
4. STARTING HEAD
5. MAIN SHA
6. FINAL COMMIT SHA
7. EXACT CHANGED PATHS
8. UI ARCHITECTURE
9. FOURTEEN ROUTE IDS/LABELS
10. STATE / DEFAULT / FALLBACK
11. NAVIGATION INTERACTION
12. ACCESSIBILITY / FOCUS
13. RESPONSIVE CSS
14. DESIGN / BRANDING
15. HONEST PLACEHOLDERS
16. TEXT-FIRST OPERATION
17. BACKEND / HTTP / WEBSOCKET BOUNDARY
18. TAURI IPC/CSP BOUNDARY
19. DEPENDENCIES/MANIFESTS
20. PHX-003 WORKSPACE TEST EVOLUTION
21. PHX-055 GUARD REPAIR EXACT x3
22. PROMPT-INJECTION SUITE x3
23. NPM TEST RUN 1
24. NPM TEST RUN 2
25. NPM TEST RUN 3
26. TYPECHECK
27. VITE BUILD
28. TAURI INFO
29. CARGO CHECK
30. CARGO TEST
31. TAURI BUILD
32. BACKEND REGRESSIONS
33. MEDIA/DOCUMENT REGRESSIONS
34. PHASE-6 REGRESSIONS
35. FULL PYTHON RUN 1
36. FULL PYTHON RUN 2
37. FULL PYTHON RUN 3
38. RUFF
39. FORMAT
40. MYPY
41. PACKAGE
42. SECURITY SCAN
43. INV-001 THROUGH INV-024
44. FINAL DIFF
45. ADR-067
46. DOCS
47. FEATURE_TICKETS / PHASE 8
48. COMMIT/PUSH
49. AHEAD/BEHIND
50. WORKING TREE
51. MAIN SAFETY
52. LIVE DESKTOP NAVIGATION ACCEPTANCE
53. BACKEND-CONNECTED FRONTEND ACCEPTANCE
54. KNOWN LIMITATIONS
55. NEXT TICKET PHX-081 NOT STARTED

No PR, merge, main push or PHX-081 work.


## Authoritative blocker resolution — historical PHX-052 / PHX-051 desktop guards — 2026-10-08

PHX-080 implementation correctly stopped after full Python verification reported
15,719 passed, 2 failed, 16 existing skips and the one existing warning.
The two failures reproduce independently in historical source-protection guards
because their Git diff commands protect the whole apps/ tree against current
origin/main, including precisely the two desktop shell source files PHX-080
is explicitly authorized to change.

This section supersedes only the original eight-file limit and the original
stop-on-first-fixable-test-failure workflow; all other ticket requirements remain.

### Exact historical repair 1 — Screenshot / vision

File: tests/test_screenshot_vision_loop.py

Only test: test_no_action_persistence_or_dependency_change

In that test's existing git diff origin/main -- [positive paths] invocation,
retain ALL existing positive paths, including "apps". Append EXACTLY these two
negative pathspecs, after all positive paths:
- :(exclude)apps/desktop/src/App.tsx
- :(exclude)apps/desktop/src/styles.css

Preserve all AST prohibited-operation/import assertions, EventType exactly 32,
sole Alembic head 0005_audit_events, and every other protected path and assertion.
Do not exclude the apps directory as a whole. Do not alter screenshot/vision
production or any other screenshot tests. Add one precise explanatory comment.

### Exact historical repair 2 — Windows UI automation

File: tests/test_windows_ui_automation.py

Only test: test_source_architecture_and_immutability

Retain EVERY entry of the existing "paths" positive-path list, including "apps".
Append EXACTLY these two negative pathspecs, after the existing positive paths:
- :(exclude)apps/desktop/src/App.tsx
- :(exclude)apps/desktop/src/styles.css

Preserve all AST, no-input, no-launch, no-background, EventType 32, migration-head,
source-isolation, and other test assertions. Do not change the Windows UI production,
its dependency promotion test or any other Windows UI tests. Add one precise comment.

### Verify exceptions are exactly scoped

Before changes, prove the real current diff under "apps" includes only the
authorized two frontend source paths.

After changes, check that both guarded git diff commands report no differences
for every other protected path. Negative pathspecs must occur after '--' and must
not mask unauthorized paths.

Run each repaired exact test THREE consecutive times.

Run the entire screenshot/vision and Windows UI automation test suites individually.
Record exact pass/skip/warning counts and preserve existing skip identities.
Also re-run PHX-055 exact guard x3 and its complete 604-test suite x3.
An unchanged historical test outside these three authorized historical guard files
that fails requires a new blocker authorization. No other historical test edits.

The specifically authorized historical guard files are now:
- tests/test_prompt_injection_defenses.py (previous authority)
- tests/test_screenshot_vision_loop.py (this amendment)
- tests/test_windows_ui_automation.py (this amendment).

### Scoped autonomous repair and retry loop

To honor PHX-080's end-to-end completion goal, do NOT stop at the first new
PHX-080-local test, formatting, typecheck or source defect. Continue a local
IMPLEMENT -> RUN TARGETED TEST -> DIAGNOSE -> REPAIR -> RE-RUN loop when the
repair is demonstrably within the already-authorized final file set,
architecture and security constraints.

For each failure:
1. capture the exact command, failure text and reproduction outside the repo;
2. classify implementation/test-local defect vs historical guard vs genuine
   host/dependency/security/out-of-scope failure;
3. if an in-scope deterministic defect, make the smallest repair and re-run
   the exact failing test/gate, then all affected tests, then the entire remaining
   required matrix;
4. do not count failed/partial runs as passing; a new change invalidates related
   old gate receipts;
5. no suppression of warnings, xfail, skips, test deletion, ignored errors,
   broad exclusions, force pushes, altered privileges or reduced assertions.

Retry transient command/tool execution problems only when safe/idempotent. After
three consecutive identical failures with no new diagnostic evidence or no
meaningful progress, STOP BLOCKED with full evidence rather than loop endlessly.
Likewise STOP immediately if a fix needs an additional path or a changed authority,
touches a protected subsystem, requires unsafe cleanup, changes security invariants,
or cannot be completed without unavailable external prerequisites.
Never silently broaden the scope.

The scope maximum is now TEN final paths, consisting of the original eight plus:
- tests/test_screenshot_vision_loop.py
- tests/test_windows_ui_automation.py

The authoritative preparation is now TWO commits ahead of main: original ticket
and this clarification. On a full PASS, make exactly ONE completion commit after
both preparations, and push the feature branch only. The resulting feature branch
must be exactly THREE commits ahead of unchanged main and zero behind.

No PR, merge, force push, main push, or PHX-081 work.

## PHX-080 completion receipt — 2026-10-08

Completed after authoritative preparation 574459e5e2b5d6a0a5a2eb1f26e421409e6b63c3,
following the original 7770b0a0aa40de49594662c6279371aab0cc11e2 preparation.
Main remains 4a70867203a70d3a85e2c141ef0dbb92bdff42e2. The ticket-only fast-forward
preserved all four original implementation hashes. All frozen requirements and the
historical-guard amendment above are retained; the final authorized scope is ten paths.

Implemented local React shell/navigation for the exact fourteen IDs/labels in this
ticket, with default/fallback home, native named buttons, selected page evidence,
heading/panel updates, post-render main focus, skip link, responsive cinder/amber
CSS and honest unconnected panels. No chat/voice or PHX-081..087 feature is started.
No backend/client/HTTP/WebSocket, privileged tool/provider/database/native IPC,
permission operation or fabricated backend state. CSP, empty permission grants,
Rust, entry point, manifests, dependencies, schema and migration history are unchanged.

### Repair evidence

- PHX-055: only the two-path exclusion and explanatory comment. Ruff repaired
  four LF endings to match existing CRLF; normalized bytes and Python AST are
  equivalent before/after formatting. No repository-wide formatter or configuration change.
- PHX-052 screenshot and PHX-051 Windows UI: each exact historical guard alone
  adds a comment and the two exact negative pathspecs after all positive paths.
  Every other source line/assertion, positive apps protection, prohibited operation,
  EventType 32, sole Alembic head 0005_audit_events and dependency proof is preserved.
- Actual apps diff contains only App.tsx/styles.css; excluding exactly those paths
  leaves an empty protected diff. An external Git index/worktree fixture confirms
  all other protected probes, native configuration, entry point and similar names
  remain detected. No other test in these historical files changed.

### Completed verification

| Command / gate | Result |
|---|---|
| npm ci --ignore-scripts | PASS; approved lock unchanged; zero reported vulnerabilities |
| npm test, three consecutive runs | 14 passed / 14 passed / 14 passed; no skips or failures |
| npm run typecheck --workspace @phoenix/desktop | PASS |
| npm run build --workspace @phoenix/desktop | PASS |
| npm run tauri:info --workspace @phoenix/desktop | PASS; existing MSVC/WebView2 toolchain |
| cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml | PASS |
| cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml | PASS; zero native unit tests, zero failures |
| npm run tauri:build --workspace @phoenix/desktop | PASS; existing no-bundle workflow; no GUI launched |
| Exact PHX-055, PHX-052 and PHX-051 repaired guards | Each 1 passed x3 consecutively |
| Complete prompt-injection suite | 604 passed x3 consecutively |
| Complete screenshot / Windows UI suites | 397 / 493 passed; zero skips or warnings |
| API / bootstrap | 8 / 11 passed; API retains existing warning and exact /health |
| AgentRegistry / model router / ToolRegistry | 96 / 213 / 204 passed |
| PermissionEngine / AuditTrail / SecretStore | 926 / 302 / 402 passed |
| Spotify / YouTube / document generation / upload parser | 345 / 358 / 284 / 336 passed |
| Startup / conversation / TTS / STT / VAD / wake | 509 / 950 / 602 / 414 / 403 / 537 passed |
| uv run --locked pytest, three consecutive runs | 15,721 passed each; identical 16 existing skips and one existing warning |
| uv sync --locked | PASS; 49 existing packages; no dependency changes |
| uv run --locked ruff check . | PASS |
| uv run --locked ruff format --check . | PASS; 133 files already formatted |
| uv run --locked mypy | PASS; 127 source files |
| uv build, outside repository | PASS; wheel and sdist contain the same 77 Python sources and dependency metadata |
| Package source / health parity | Working-source bytes equal packaged bytes; all Python Git blobs match main after EOL normalization; exact /health unchanged |
| Credential/signature and artifact review | No added credential signatures detected; no generated build/validation artifacts staged |
| Three preserved workspace/CSP tests | Bodies unchanged; Node SSR/pure/AST evidence does not claim native GUI clicks |

The validation ledger, command logs, JUnit receipts, package and guard proofs remain
outside the repository in C:/Users/shoai/.codex/validation/PHX-080-loop-20261008.
The prior 15,719-pass/two-failure run is recorded as failed, individually reproduced,
then repaired only under the amendment; it is not counted toward the three passes.

### Individual invariant review

| Invariant | Concrete evidence |
|---|---|
| INV-001 | One PHOENIX identity; orchestration source unchanged; orchestrator tests pass in full suite |
| INV-002 | No AIZEN authority/import; registry 96/router 213 pass; planner source unchanged |
| INV-003 | No model-derived permission decision; deterministic permission suite 926 passes |
| INV-004 | No tool/IPC path in App AST; ToolRegistry 204 and permissions 926 pass |
| INV-005 | No approve/deny controls; explicit-user correlation and sensitive-action checks remain passing |
| INV-006 | No privileged UI execution; blocked/P5 permission and secret checks remain passing |
| INV-007 | AuditTrail 302 passes; audit/persistence source unchanged |
| INV-008 | Failures preserved in ledger; completion only after three full passes and every required gate |
| INV-009 | Recovery source unchanged; positive-budget/bounded-replan tests pass in full suite |
| INV-010 | KAIDO degraded-only test passes in full suite; no UI recovery authority |
| INV-011 | App AST permits only React/local state/navigation/focus; unchanged CSP and empty capabilities |
| INV-012 | No provider/agent implementation changes; registry/router regressions pass |
| INV-013 | No tool-policy change; registry inert metadata assertions pass |
| INV-014 | Stable view IDs/product labels; registry alias-removal and collision assertions pass |
| INV-015 | Fourteen native text-named navigation buttons; no microphone required |
| INV-016 | No voice initialization; all six Phase-6 suites pass at frozen counts |
| INV-017 | No unsafe HTML/remote document input; prompt-injection 604 x3 and upload 336 pass |
| INV-018 | No frontend storage; SecretStore 402 passes; memory/security source unchanged |
| INV-019 | Selection introduces no long/background work; existing cancellation/state tests pass |
| INV-020 | Migration source unchanged; exact historical guards retain sole head 0005_audit_events |
| INV-021 | Explicit pending panels, no simulated success; provider/tool failure tests pass |
| INV-022 | Unknown ID resolves visibly to home heading/current marker; integration status remains pending |
| INV-023 | No generation path; unchanged lifecycle default capacity one and lifecycle tests pass |
| INV-024 | No manifest/lock/dependency changes; package dependency metadata matches main |

ADR-067 records the local UI/security/accessibility and exact historical evolution.
PHX-080 is DONE; Phase 8 is INCOMPLETE; PHX-081 through PHX-087 stay TODO.

LIVE DESKTOP NAVIGATION ACCEPTANCE: PENDING — deterministic React render/navigation
contract tests and static Tauri build passed; manual end-to-end WebView2 mouse/keyboard
smoke has not been performed.

BACKEND-CONNECTED FRONTEND ACCEPTANCE: NOT IMPLEMENTED — PHX-080 is a local UI-only
app shell. Later Phase 8 tickets own transport, real state and action binding.
