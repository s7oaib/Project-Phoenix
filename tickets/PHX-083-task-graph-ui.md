# PHX-083 — Task Graph UI

Status: DONE
Phase: 8 — Frontend
Branch: feature/PHX-083-task-graph-ui
Starting main/base SHA: 09c5b8dcab19b33289caba4c04e2f3b3dfa42752
Expected unchanged main: 09c5b8dcab19b33289caba4c04e2f3b3dfa42752

## Objective

Replace the current Task Graph placeholder in the PHX-080 app shell with an
accessible, useful **read-only task dependency and step-detail view**. It must
be capable of rendering a strictly validated, caller-supplied PHX-024-compatible
task/step graph, with source-stable topological ordering, dependency links as
inert accessible text/buttons and clear read-only state evidence.

Default runtime is DISCONNECTED and contains NO made-up tasks or progress.
No backend API transport, agent execution, task creation, cancellation, permission
approval, event streaming, task-state transition or audit action is implemented.

PHX-083 is a **presentation boundary only**.

## Mandatory repository authorities

Read all in full:
- AGENTS.md
- RULES.md
- MENTAL_MODEL.md
- INVARIANTS.md
- MASTER_SPEC.md
- PRD.md
- ROADMAP.md
- DECISIONS.md
- FEATURE_TICKETS.md
- docs/01_ARCHITECTURE.md
- docs/03_AGENT_ROSTER.md
- docs/04_AGENT_ORCHESTRATION.md
- docs/08_PERMISSION_MATRIX.md
- docs/09_SECURITY.md
- docs/13_API_CONTRACTS.md
- docs/14_EVENT_SYSTEM.md
- docs/20_UI_UX_SPEC.md
- docs/21_FRONTEND.md
- docs/25_DEPENDENCIES.md
- tickets/PHX-024-task-graph-state-machine.md
- tickets/PHX-080-app-shell-navigation.md
- tickets/PHX-081-chat-voice-ui.md
- tickets/PHX-082-agent-network-ui.md
- phoenix/agents/task_graph.py
- phoenix/agents/registry.py
- phoenix/api/app.py
- apps/desktop/src/App.tsx
- apps/desktop/src/styles.css
- apps/desktop/src/main.tsx
- tests/workspace.test.mjs
- tests/test_prompt_injection_defenses.py
- tests/test_screenshot_vision_loop.py
- tests/test_windows_ui_automation.py
- apps/desktop/src-tauri/tauri.conf.json.

If any real authority conflict arises, preserve work and STOP BLOCKED.

## Frozen current backend truth

Current \`phoenix/api/app.py\` implements only GET /health.
\`GET /tasks\`, \`GET /tasks/{id}\`, \`POST /tasks\`,
\`POST /tasks/{id}/cancel\` and WS /events listed in the API contracts
are **future intended routes, not implemented**.

PHX-024 \`TaskGraph\` is an immutable graph definition with canonical step owner
IDs and deterministic source-stable topological queries. Its memory-only
\`TaskStateMachine\` tracks legal task-level transitions; state markers do not
prove agents executed, verification succeeded, permissions approved or progress.
**There is no per-step runtime state in PHX-024.**

Therefore PHX-083 must not claim a live task, completed step, running agent or
validated verification unless a separately trusted caller supplies explicit
display evidence. A test fake proves UI acceptance only, not live backend integration.

## PHOENIX authority and scope

- PHOENIX is the single supreme orchestrator.
- AIZEN plans, not executes supreme decisions.
- LEVI verification outcomes remain separate evidence, not inferred from state.
- MAHORAGA recovery cannot grant permissions or hide failure.
- KAIDO degraded-only.
- PermissionEngine remains sole permission authority.
- Status data, task objectives, graph structure and agent aliases never grant authority.
- No model/provider, ToolRegistry, PermissionEngine, AuditTrail or filesystem action.
- No REST/WebSocket/fetch/XHR/browser storage/Tauri invoke/plugin/IPC.
- No native permissions, microphone/audio, global hotkeys or background polling.
- No new EventType, migration, DB, backend or Rust change.
- Tauri capabilities stay empty and production connect-src 'none' remains unchanged.

## Exact canonical task-state vocabulary

Use EXACT serialized values from PHX-024:
- created
- planning
- ready
- running
- waiting_permission
- verifying
- completed
- failed
- recovering
- degraded
- cancelled

These are **task-level states only**, not per-step states. An unknown state
rejects the borrowed snapshot; never guess, coerce, or add statuses.

Status may be displayed as caller-supplied task state evidence only when the
separate port is connected and the snapshot passes validation. A "completed"
marker is not evidence of LEVI verification or real task success; a
"waiting_permission" marker is not an approvable permission request.
Never render an Approve/Deny/Run/Cancel/Retry action from these markers.

## UI deliverable

Existing \`tasks\` navigation renders a Task Graph workspace with:
1. heading, purpose and integration availability;
2. truthful default "No task graph connected" state, no synthetic task rows;
3. optionally supplied task ID and task-level state with provenance qualifier
   "Caller-supplied task state; not live transport or verified completion";
4. deterministic, accessible graph representation:
   - ordered step cards in **topological order**,
   - visual source-stable dependency grouping/layer indication,
   - visible directed edges as dependency labels ("Depends on …"),
   - distinction between ROOT/no-dependency and downstream steps,
   - a readable textual dependency list that works with CSS disabled,
   - total step count and edge count only from admitted graph;
5. local selected-step detail panel with canonical owner agent internal ID and
   optional display alias from PHX-082 roster, objective, expected output,
   completion rule, timeout and inert input references;
6. keyboard-focusable native step selection buttons with aria-pressed/aria-controls,
   accessible headings and visible focus;
7. a small static task-state vocabulary legend if useful, clearly labeled
   **reference vocabulary**, not currently reported states;
8. responsive layout usable at existing 800x600 Tauri window; no horizontal
   overflow, text wrapping, bounded scrolling, reduced-motion support.

No arbitrary SVG connectors, canvas simulation, charts dependency or
third-party graph library needed. Use HTML list/card/CSS for a robust,
source-stable DAG presentation. If adding SVG, it must be static code-owned,
fully accessible and cannot import user markup or network assets; prefer no SVG.

No fabricated duration, task completion %, active tasks, "online" agents,
verification result, permission approval, timestamps, activity events or
progress bars. The graph's structural roots/readiness are **not runtime readiness**.

## Optional passive caller-owned task port (no transport)

A narrow read-only \`TaskGraphPort\` may be passed to App as an optional prop:
- \`status\`: 'connected' | 'unavailable' | 'degraded'
- \`graph\`: a strict optional snapshot with:
  - taskId: string
  - optional taskState: one of eleven canonical states
  - steps: readonly array of strict step records.

A step record must use the PHX-024 step fields, in camelCase display form:
- stepId: string
- ownerAgentId: exact canonical agent internal ID
- objective: string
- dependsOn: readonly exact step IDs
- inputRefs: readonly strings
- expectedOutput: string
- timeoutSeconds: finite positive number
- completionRule: string.

Never derive a task state from individual steps (there is no per-step runtime
state in PHX-024). Do not introduce per-step claimed runtime statuses.

The optional port contains **data only, never callbacks or action methods**.
It may not mutate graph state or request task actions. Do not define or
implement a production API client.

Default App() has no port; task graph remains empty/unavailable.
If port.status !== connected, suppress supplied graph and state even if present
and show the disconnected/degraded explanation. If malformed, reject the whole
snapshot, give a generic safe notice and show no partial graph.
No raw exceptions, private task text or stack traces.

## Strict bounded data admission

Use only primitive own data descriptors where practical to avoid invoking
untrusted getters and coercion. Reflective Proxy traps may run but exceptions
must be caught. Do not claim same-realm JS is a security sandbox.

Limits:
- taskId: nonblank exact string, length 1..128 UTF-16 units;
- steps: 1..40 ordered records;
- stepId: nonblank exact string, length 1..128 UTF-16 units;
- each ownerAgentId must exactly match PHX-020 canonical roster internal ID;
- objective: meaningful string, length 1..600 UTF-16 units;
- expectedOutput: meaningful string, length 1..400 UTF-16 units;
- completionRule: meaningful string, length 1..400 UTF-16 units;
- timeoutSeconds: finite positive number >0 and <=86400; no boolean/coercion;
- dependsOn: ordered array, maximum 12 per step, total graph edges <=120;
- inputRefs: ordered array, maximum 8 per step;
- each input ref: meaningful exact string length <=128 UTF-16 units;
- no duplicate step IDs, duplicate dependencies or self-dependency;
- every dependency must refer to an existing step in same graph;
- no cycles; deterministic topological ordering, stable by original step
  source-index when multiple steps are ready;
- no malformed array holes/extra enumerable or symbol fields, accessors or
  unexpected keys at port, graph, step and array levels;
- any nonfinite/NaN timeout, wrong type, bad field, unknown owner,
  illegal taskState, unknown dependency, cycle or oversized graph rejects ALL.

No "repair" of invalid graphs via dropped dependencies or truncated steps.
Do not silently make unknown owners PHOENIX.
No user-provided dynamic style, HTML, component name or URL.

All text is React JSX-escaped. Never evaluate or interpret objectives,
input refs, completion rules or task state as instructions.

A shallow derived readonly copy of primitives/arrays must be used for rendering
only after whole-graph validation and acyclicity proof. No input mutation.
Be honest: bounded presentation validation does not hard-limit browser memory
or authenticate caller-provided task evidence.

## Stable graph traversal

Implement a **pure**, deterministic topological-order helper over the
validated step definitions. Follow PHX-024's Kahn/heap-like source-priority
semantics: whenever a step becomes eligible, compare its original source
position against every ready step and pick the earliest. Do not merely layer
and sort at the end if that changes tie-breaking.

Input dependencies are edges "upstream prerequisite -> dependent".
Roots are step records with zero dependencies in source order.
Step cards display source-stable topological sequence.
Edge count equals sum of dependency array lengths.

Do not claim any step is completed, ready to run or verified merely because
its structural dependencies are present. Do not display a "Run" button.
If validation fails, return no partial graph/selection.

Selected step:
- default first in topological order of current admitted graph;
- user may select only IDs present in the admitted graph;
- when graph/task ID changes, missing selected ID must fall back deterministically
  to the first topological step (avoid stale selection leaking across tasks);
- preserve a valid selected ID across navigation when App remains mounted;
- if port disconnects, selection is inert and no stale details show;
- no state writes to the caller-supplied graph.

## Preservation of PHX-080, PHX-081, PHX-082

Retain all 14 exact navigation IDs/labels, branding, Home default/fallback,
Mission/Workspace/System nav groups, skip link, navigation focus, semantics,
responsive 800x600 layout and other views' placeholders.

PHX-081:
- unchanged draft, ChatPort and acknowledgement/cancel/duplicate semantics,
- bounded plaintext transcript,
- disabled voice/microphone and text-first fallback.

PHX-082:
- all 30 canonical agent IDs/aliases/roles/order unchanged,
- bounded agent search/selection and passive optional agent snapshot unchanged,
- no invented live agent statuses, execution or permissions.

The "all other views are placeholders" Node test must now exempt exactly
home, agents and tasks; the remaining eleven views retain honest placeholders.

No implementation of PHX-084 permissions, PHX-085 memory/files,
PHX-086 models/voice/settings or PHX-087 observability.
No live backend task transport or actions.

## Frozen dependency and native policy

Use current React/TypeScript/CSS/Node only. NO React Flow, XYFlow, D3, Dagre,
ELK, router, icon, animation, test, state or DOM library, and no new npm/Python/
Rust dependencies.

Do not modify package.json, package-lock.json, pyproject.toml, uv.lock,
Cargo.toml/Cargo.lock, Tauri config, CSP/capabilities, Rust, Vite or TS configs,
apps/desktop/src/main.tsx, backend, tests outside exact frontend tests.

No filesystem IO/network/port activity from App.tsx.

## Exactly authorized files

Implementation:
1. apps/desktop/src/App.tsx
2. apps/desktop/src/styles.css
3. tests/workspace.test.mjs

After ALL gates pass:
4. docs/21_FRONTEND.md
5. DECISIONS.md (exactly one new ADR-070, accepted)
6. FEATURE_TICKETS.md
7. tickets/PHX-083-task-graph-ui.md

Maximum final diff: SEVEN paths including ticket. No new file.

Historical Python security guards already exclude exactly the two shell source
paths, so those tests must remain unchanged. No additional historical fix
without a new explicit authority.

## Deterministic Node test matrix

Current baseline: 45 \`npm test\` passes.

Keep three PHX-003 workspace/Tauri/CSP test bodies byte-identical in meaning.
Keep all PHX-080 navigation/accessibility/contrast/reduced-motion tests.
Keep all PHX-081 chat/voice and PHX-082 canonical roster/status tests.

Evolve obsolete "other views are placeholders" assumption exactly to exclude
home, agents, tasks, while preserving all eleven remaining placeholder proofs.
Extend AST function-call allowlist only for precise new local pure graph helpers,
not network/native/media/storage/system calls.

Add targeted tests for:
- default disconnected Task Graph, no synthetic examples or fake state;
- app composition and selection across navigation;
- data-only optional port; no method execution or default transport;
- strict task/step field/types, extras/accessors/proxies/sparse arrays;
- 1..40 steps, 0..120 edges, per-node cap/timeout/text/ref bounds;
- unknown/alias owner ID rejected, canonical IDs accepted;
- duplicate IDs/dependencies, unknown dependencies, self-dependency, cycles;
- source-stable Kahn topological ordering in nontrivial tie cases;
- empty roots / invalid topology rejection;
- correct root/edge count and dependency text;
- task-level 11 states, optional absence, invalid values rejected;
- no per-step runtime state or inferred verification/permission success;
- owner ID and display alias as separate fields;
- selected first step, valid selection, unknown selection and task-change reset;
- no dangling selection/details on disconnect;
- read-only graph; no Run, Cancel, Retry, Approve, Deny buttons;
- untrusted HTML/Markdown/command-looking fields rendered as plain text;
- no data mutation, no caller method/callback invoked, safe generic errors;
- responsive 800x600/narrow CSS, keyboard, focus/skip;
- PHX-081 chat/voice unchanged and PHX-082 Agent Network unchanged;
- exact 14 shell destinations and other 11 placeholders;
- manifest/CSP/permissions unchanged, no new dependencies.

Use only existing SSR/TS compiler AST/hook harness. Do not fabricate WebView2
end-to-end evidence. If a documented test expectation has become obsolete,
replace narrowly with equivalent precise PHX-083 assertion, not blanket removal.

## Required gates

### Native/frontend
- npm ci --ignore-scripts; all manifests/lock unchanged;
- npm test THREE consecutive complete passes; final count >45;
- npm run typecheck --workspace @phoenix/desktop;
- npm run build --workspace @phoenix/desktop;
- npm run tauri:info --workspace @phoenix/desktop;
- cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml;
- cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml;
- npm run tauri:build --workspace @phoenix/desktop;
- no native GUI required for deterministic acceptance.

### Historical security guards
Each exact three times:
- PHX-055 tests/test_prompt_injection_defenses.py::
  test_protected_existing_sources_dependencies_schema_and_event_vocabulary
- PHX-052 tests/test_screenshot_vision_loop.py::
  test_no_action_persistence_or_dependency_change
- PHX-051 tests/test_windows_ui_automation.py::
  test_source_architecture_and_immutability

Whole suites, unchanged:
- prompt injection 604
- screenshot vision 397
- Windows UI automation 493.

### Backend/media/voice regressions
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
Baseline 15,721 passed, 16 existing Windows skips and one existing
Starlette/httpx warning.
- uv sync --locked;
- complete uv run --locked pytest THREE consecutive passes;
- all 15,721, exact existing skip identities/reasons and warning,
  no xfail/new skip/new warning/test suppression;
- uv run --locked ruff check .;
- uv run --locked ruff format --check .;
- uv run --locked mypy;
- build wheel/sdist outside repo; exact Python source/metadata parity,
  unchanged GET /health;
- credential/private artifact scan;
- INV-001 through INV-024 individually evidenced;
- complete unstaged/staged diff checks;
- no new runtime dependency.

## Bounded autonomous fix-and-retry loop

Do not stop at the first ordinary React/TypeScript/CSS/Node-test/formatting/build
failure within authorized scope. Repeat:
REPRODUCE -> DIAGNOSE -> MINIMAL IN-SCOPE REPAIR -> FOCUSED RETEST ->
AFFECTED REGRESSIONS -> COMPLETE REQUIRED GATES.

Keep a gate ledger outside repository. A change invalidates relevant earlier
receipts; final npm x3 and Python x3 must be complete consecutive clean runs
after last relevant changes.

STOP BLOCKED if:
- a required fix needs an EIGHTH file or historical guard edit;
- backend/API/Tauri/permission/migration/dependency changes are needed;
- a new task execution/control authority would be introduced;
- genuine native host prerequisite unavailable;
- three identical failures have no progress/new evidence.
No arbitrary infinite retries, force push, reset/clean, security bypass,
xfail/skips/retry decorator or warning suppression.

## ADR/status after all gates pass

Add accepted ADR-070 addressing:
- PHX-083 read-only in-memory DAG visualization of borrowed evidence;
- PHX-024 canonical 11 task-level states, no per-step runtime statuses;
- structural DAG validation/topological source-priority;
- canonical owner IDs vs aliases and PHOENIX authority;
- no invented runtime readiness, completion/verification/permission evidence;
- disconnected default, no GET /tasks/WS backend transport;
- no task create/cancel/retry/approve action;
- strict bounded snapshot, inert JSX text, no Tauri/backend/provider change;
- preserved PHX-080/081/082 and no additional dependency;
- live/manual acceptance pending.

Update docs/21_FRONTEND.md narrowly, ticket Status DONE and
FEATURE_TICKETS PHX-083 DONE only AFTER all gates pass.
Phase 8 INCOMPLETE (PHX-084..087 TODO), PHX-084 NOT STARTED.

## Final code-review scope / commit

Maximum seven authorized paths:
- apps/desktop/src/App.tsx
- apps/desktop/src/styles.css
- tests/workspace.test.mjs
- docs/21_FRONTEND.md
- DECISIONS.md
- FEATURE_TICKETS.md
- tickets/PHX-083-task-graph-ui.md

Review \`git diff --check\`, complete diff, staged \`git diff --cached
--check\`, staged stat and full staged diff. Stage no artifacts, secrets,
logs, receipts, caches, builds, DBs or generated files.

Only after every required gate passes, create EXACTLY ONE completion commit:
\`feat: complete PHX-083 task graph ui\`

No amendment of preparation.
Normal push FEATURE BRANCH ONLY:
\`feature/PHX-083-task-graph-ui\`.

Expected successful topology 0 behind / 2 ahead unchanged main and clean tree.
NO PR, merge, main push or PHX-084.

## Live acceptance honesty

LIVE TASK GRAPH ACCEPTANCE: NOT IMPLEMENTED — no live GET /tasks or /tasks/{id}
backend transport or WS /events; the UI defaults disconnected and tests use
synthetic caller-supplied graphs only.

LIVE TASK EXECUTION/CANCELLATION ACCEPTANCE: NOT IMPLEMENTED — frontend cannot
dispatch, transition, cancel, recover, verify or approve tasks.

MANUAL NATIVE TASK GRAPH ACCEPTANCE: PENDING — no real WebView2 mouse/keyboard
end-to-end demonstration; SSR/hook/CSS/native build gates are not live GUI evidence.

These limitations do not block presentation-only PHX-083.

## Final 61-field handoff

1. STATUS
2. OFFICIAL PATH
3. BRANCH
4. STARTING PREP HEAD
5. EXPECTED/OBSERVED MAIN
6. COMPLETION SHA
7. EXACT CHANGED PATHS
8. TASK UI LAYOUT
9. DEFAULT DISCONNECTED STATE
10. OPTIONAL PASSIVE PORT CONTRACT
11. CANONICAL 11 TASK STATES
12. NO PER-STEP RUNTIME STATUS
13. SNAPSHOT EXACT FIELD VALIDATION
14. BOUNDS / DUPLICATES / PROXY SAFETY
15. CYCLE/UNKNOWN/SELF-DEPENDENCY REJECTION
16. SOURCE-PRIORITY TOPOLOGICAL ORDER
17. ROOTS/DEPENDENCY EDGES
18. SELECTED STEP / GRAPH ID RESET
19. OWNER CANONICAL AGENT ID / ALIAS
20. STEP DETAIL METADATA
21. NO EXECUTION / PERMISSIONS / VERIFICATION CLAIMS
22. INERT TEXT / HTML INJECTION DEFENSE
23. ACCESSIBILITY / KEYBOARD
24. RESPONSIVE 800X600 LAYOUT
25. PHX-080 SHELL REGRESSION
26. PHX-081 CHAT/VOICE REGRESSION
27. PHX-082 AGENT NETWORK REGRESSION
28. NPM TEST RUN 1
29. NPM TEST RUN 2
30. NPM TEST RUN 3
31. TYPECHECK
32. VITE BUILD
33. TAURI INFO
34. CARGO CHECK
35. CARGO TEST
36. TAURI BUILD
37. PHX-055 EXACT x3
38. PHX-052 EXACT x3
39. PHX-051 EXACT x3
40. THREE HISTORICAL FULL SUITES
41. BACKEND/SECURITY REGRESSIONS
42. MEDIA/DOCUMENT REGRESSIONS
43. VOICE REGRESSIONS
44. FULL PYTHON RUN 1
45. FULL PYTHON RUN 2
46. FULL PYTHON RUN 3
47. RUFF
48. FORMAT
49. MYPY
50. PACKAGE/HEALTH
51. SECURITY SCAN
52. INV-001..024 EVIDENCE
53. ADR-070
54. DOCUMENTATION
55. TICKET/PHASE STATUS
56. FINAL DIFF
57. COMMIT/PUSH
58. AHEAD/BEHIND, CLEAN TREE/MAIN
59. LIVE TASK GRAPH ACCEPTANCE
60. LIVE TASK EXECUTION / MANUAL GUI ACCEPTANCE
61. KNOWN LIMITATIONS / NEXT PHX-084 NOT STARTED

No PR, merge, main push or PHX-084.

## Completion evidence — 2026-10-08

Started clean on feature/PHX-083-task-graph-ui at preparation
b03c5b3cdd795053504795ade840c00dbabbcfde; main 09c5b8dcab19b33289caba4c04e2f3b3dfa42752.
Initial 0 behind / 1 ahead with only this ticket changed. All frozen ticket
requirements remain intact. No reset/clean/rebase/amend/force/PR/merge/main push.

PHX-083 adds a read-only Task Graph workspace to the existing React/TypeScript/CSS
shell. Default App supplies no TaskGraphPort and shows No task graph connected with
no invented task rows/progress. Only an explicitly connected, validated borrowed
snapshot provides task ID, optional task-level state, steps and metadata. Unavailable
or degraded ports suppress all graph/state; malformed input shows a generic notice.

TaskGraphPort is passive readonly data: status connected/unavailable/degraded and an
optional graph containing taskId, optional taskState and ordered steps. Each step has
only stepId, ownerAgentId, objective, dependsOn, inputRefs, expectedOutput,
timeoutSeconds and completionRule. Canonical agent IDs alone establish owner identity;
PHX-082 aliases are separate display metadata. There are no callbacks or action methods.

Exactly eleven serialized task states match Python PHX-024: created, planning, ready,
running, waiting_permission, verifying, completed, failed, recovering, degraded and
cancelled. No per-step runtime status or automatic transition exists. The qualifier
Caller-supplied task state; not live transport or verified completion stays visible.
Completed is not execution/LEVI evidence; waiting_permission is not an approval request.

Whole-graph admission requires own data fields, nonblank exact task/step IDs <=128
UTF-16 units, 1..40 steps, exact known owner IDs, meaningful objective <=600,
expectedOutput/completionRule <=400, finite numeric timeout >0 and <=86400, <=12
dependencies per step and <=120 total edges, <=8 input refs each meaningful <=128.
Reject wrong types, unsupported states, extras/symbols/accessors, sparse or extended
arrays, duplicate step IDs/dependencies, unknown/self dependencies and cycles as a
whole. No text truncation, dependency repair or partial private-data display.
Input refs may repeat, matching PHX-024. Caller inputs are never mutated; only detached
frozen primitive/array snapshots render after acyclicity proof.

Pure Kahn traversal repeatedly picks the earliest original source position among all
eligible nodes, including newly eligible earlier nodes. A bounded forty-node scan
matches Python's heap semantics without a graph dependency. Roots retain source order;
edge count derives only from admitted dependencies. Ordered native cards show inert
Depends on text, root/downstream designation and structural dependency layers.
Structure never establishes runtime readiness, completion, permission or verification.

App-owned local selection starts at the first topological step, accepts only current
graph step IDs, survives navigation and resets when task identity changes, the selected
step disappears or the port disconnects. Guarded React render adjustment compares
identity fields only; there is no background effect, timer or task state mutation.
Details show canonical owner/alias, objective, expected output, completion rule,
timeout definition with no running timer, dependencies and inert input references.

Native named selection buttons use aria-pressed/aria-controls; labelled sections and
polite selection feedback complement unchanged navigation focus/skip behavior. Wrapped
plain text and bounded step scrolling stack below 1000px for the unchanged 800x600
window. Existing contrast, narrow navigation and reduced-motion treatment remain.
HTML/Markdown/command-looking IDs, objectives, rules and refs are JSX-escaped text.
Descriptor checks avoid getters/coercion/ordinary Proxy get traps, catch reflective
failures and reveal no exceptions. Reflective traps may run: same-realm JS is not a
sandbox, and snapshot shape does not authenticate activity or hard-limit browser memory.

PHX-080's fourteen destinations/default/fallback/branding/groups/focus remain intact.
PHX-081's chat code and PHX-082's entire roster/search/status/UI block stay byte-equal
to main, apart from task-view composition. Eleven remaining views stay placeholders.
No GET/POST /tasks, WS/events, execution, cancel/retry, approval, verification action,
provider/tool/native call, persistence or new dependency. Python/backend/Rust/Tauri
configs/CSP/empty permissions/manifests/locks remain unchanged. PHOENIX stays supreme.

### Verification results

- npm ci --ignore-scripts PASS; all manifests/locks unchanged, zero audit vulnerabilities.
- npm test THREE final consecutive runs: 65 passed each, zero failures/skips.
- TypeScript, Vite build, Tauri info, Cargo check/test and Tauri release build PASS;
  Cargo defines zero Rust tests. No GUI or dev server was launched.
- Exact PHX-055/052/051 guards 1 passed x3 each; historical files unchanged.
  Whole prompt/screenshot/Windows suites 604/397/493 passed.
- All 18 required individual regressions PASS: API 8, bootstrap 11, registry 96,
  router 213, ToolRegistry 204, permissions 926, audit 302, secrets 402,
  Spotify 345, YouTube 358, documents 284, upload 336, startup 509,
  conversation 950, TTS 602, STT 414, VAD 403, wake 537.
- Additional PHX-024 test_task_graph_state_machine.py: 348 passed unchanged.
- Full uv run --locked pytest THREE consecutive runs: 15,721 passed each,
  same 16 existing skip identities/reasons and one Starlette/httpx warning as main.
  No new skip/xfail/retry/flaky workaround/filter or warning suppression.
- uv sync --locked PASS: 49 packages; Ruff check PASS; format PASS: 133 files;
  strict mypy PASS: 127 source files.
- External wheel/sdist PASS: all 77 Python sources byte-equal to worktree and
  Git-normalized main; direct dependency metadata and Requires-Python unchanged.
  Exact GET /health regression and API source parity PASS; no private artifacts.
- Credential signature/source/native boundary scan and individual INV-001..024 PASS.
- Full unstaged/staged diff review, git diff --check and cached --check PASS.

Twenty added Node tests cover the Task Graph, including independently enumerated
legal source-priority orders for 1,536 four-node DAG/source-order cases. Existing
45 tests remain active; only tasks is narrowly exempted from the placeholder test,
with eleven placeholders retained and exact local helper names added to AST admission.
Three original PHX-003 workspace/CSP test bodies and historical Python guards unchanged.
The initial npm run passed 64/65: one new regex matched the explicit denial
"no permission or verified success". Individually reproduced, classified as a local
assertion defect, repaired by retaining action/status bans and asserting that denial.
Focused retest and strict typecheck passed; all final three-run gates follow the repair.
No implementation/security assertion, dependency or historical test was bypassed.

Exactly seven final paths: App.tsx, styles.css, workspace.test.mjs,
docs/21_FRONTEND.md, DECISIONS.md, FEATURE_TICKETS.md and this ticket.
Receipts, logs, JUnit, package proofs and failure ledger remain outside the repo at
C:/Users/shoai/.codex/validation/PHX-083-20261008. ADR-070 is the sole new accepted ADR.
PHX-083 DONE; Phase 8 INCOMPLETE; PHX-084..087 TODO. One completion commit
feat: complete PHX-083 task graph ui and normal feature-only push, matching remote SHA,
clean 0-behind / 2-ahead topology and unchanged main are recorded in final delivery.

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
| INV-008 | All required gates have recorded zero exits before DONE; live tasks/agent status/control, chat/voice and manual acceptance explicitly remain unimplemented/pending. |
| INV-009 | Recovery source unchanged; positive-budget and bounded-replan tests included in full pytest. |
| INV-010 | No recovery mode controls; KAIDO degraded-only tests included in full pytest; source unchanged. |
| INV-011 | Exact npm AST call allowlist permits React/local helpers and one supplied sendText call only inside sendDraft; provider/network/native/media/storage APIs banned; CSP connect-src none and empty capabilities unchanged. |
| INV-012 | No provider or agent production edits; registry 96 and router 213 passed. |
| INV-013 | No tool/policy production edits; ToolRegistry 204 passed, inert permission/audit metadata protected. |
| INV-014 | Only exact canonical IDs select details/status; every alias lookup rejected by npm; Python alias-removal/collision registry tests pass; 14 routes unchanged. |
| INV-015 | Native controlled local textarea remains editable while disconnected; named form Send and all 14 nav buttons work without a microphone; disabled voice explained. |
| INV-016 | No voice initialization in shell; startup 509, conversation 950, TTS 602, STT 414, VAD 403, wake 537 passed. |
| INV-017 | Snapshot roles limited to user/assistant, exact data fields, count 64, content 4000, ID 128, unique IDs; malformed/oversize entries omitted with notice, complete accepted text React-escaped; no parsing or external resources. Task whole-graph admission checks 1..40 steps, <=120 edges, exact known owners, 11 canonical task states, strict text/array/timeout bounds, no duplicate/unknown/self dependencies or cycles, source-priority traversal, inert JSX and generic errors. Agent snapshots admit at most 30 unique known IDs, 4 closed states, own data fields only, task<=240/provider<=120 UTF16; invalid whole snapshot rejected with generic notice; JSX escaping tested. Prompt 604, upload 336 passed. |
| INV-018 | No storage or backend in App; SecretStore 402 passed; memory/security production unchanged. |
| INV-019 | Caller-triggered one-flight send exposes pending state and Cancel waiting. Local cancellation retains draft even after late ack, explicitly cannot abort delivery, and duplicates remain blocked until adapter settles; no timer/polling/hidden worker. Hook/helper tests pass. |
| INV-020 | No migration changes; historical guard preserves sole Alembic head 0005_audit_events and EventType 32, exact test x3 passed. |
| INV-021 | Only connected supplied Promise<void> ack can clear matching draft; synchronous/thenable/malformed result and exceptions fail safely, retain draft, never append transcript; all provider/tool regressions pass. |
| INV-022 | Unknown view falls back Home; unknown/alias agent selection retains last valid identity, no identity invention; missing evidence Status not reported, disconnected/degraded no active claims; off-list selection explicitly labelled; task selection resets across task IDs/missing steps/disconnect, default no graph; chat/voice fallback preserved. |
| INV-023 | No model generation path; phoenix/models/lifecycle.py default capacity one remains unchanged; lifecycle tests in full pytest. |
| INV-024 | All npm/Python manifests and locks unchanged; existing-dependency UI only; package dependency metadata equals main. |

### Acceptance limitations

LIVE TASK GRAPH ACCEPTANCE: NOT IMPLEMENTED — no live GET /tasks or /tasks/{id}
backend transport or WS /events is connected.
LIVE TASK EXECUTION/CANCELLATION ACCEPTANCE: NOT IMPLEMENTED — no execution,
cancellation, transition, recovery, verification or permission-action controls.
MANUAL NATIVE TASK GRAPH ACCEPTANCE: PENDING — no verified end-to-end WebView2
mouse/keyboard interaction; SSR/hook/CSS/native build evidence is not GUI acceptance.

Caller evidence is structural, not authenticated runtime truth or a JS/memory sandbox.
No live task transport or execution; input refs/timeouts/rules are descriptive only.
PHX-084 NOT STARTED. No PR, merge or main push.
