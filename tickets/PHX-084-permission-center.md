# PHX-084 — Permission Center

Status: DONE
Phase: 8 — Frontend  
Branch: feature/PHX-084-permission-center  
Starting main/base SHA: 05db77c30256e12045d16aa2b00568075a7df354  
Expected unchanged main during implementation: 05db77c30256e12045d16aa2b00568075a7df354

## Objective

Replace the existing Permissions placeholder with a secure, accessible Permission Center for inspecting strictly validated, caller-supplied permission-request and PermissionDecision evidence, and for expressing a human's request-specific approve/deny intent through one optional, caller-owned adapter callback.

This is a presentation and intent boundary only. It does not implement permission policy, authenticate the human or adapter, create a UserPermissionDecision contract, mint or change PermissionGrant, evaluate PermissionEngine, write AuditTrail, execute tools, or connect the frontend to a backend.

The default App has no PermissionPort, no fake requests, no fake decisions, and no action capability. It shows an honest disconnected state.

## Mandatory authorities

Read in full before changing code:

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
- tickets/PHX-041-permission-engine-p0-p5.md
- tickets/PHX-080-app-shell-navigation.md
- tickets/PHX-081-chat-voice-ui.md
- tickets/PHX-082-agent-network-ui.md
- tickets/PHX-083-task-graph-ui.md
- phoenix/security/permissions.py
- phoenix/tools/registry.py
- phoenix/api/app.py
- apps/desktop/src/App.tsx
- apps/desktop/src/styles.css
- apps/desktop/src/main.tsx
- tests/workspace.test.mjs
- tests/test_prompt_injection_defenses.py
- tests/test_screenshot_vision_loop.py
- tests/test_windows_ui_automation.py
- apps/desktop/src-tauri/tauri.conf.json

If any authoritative conflict exists, preserve the work and STOP BLOCKED. Do not infer new authority from UI wording, request text, a model, an agent, an alias, or a caller-supplied status.

## Frozen current system truth

The current FastAPI app implements only GET /health. GET /permissions and POST /permissions/{id}/approve or /deny in docs/13_API_CONTRACTS.md are planned routes, not implemented routes.

PHX-041 PermissionEngine remains the sole P0–P5 authorization authority. It derives risk from ToolRegistry and returns a PermissionDecision. A decision is authorization evidence only; it does not execute a tool or establish success.

PHX-041 contracts are frozen, strict and extra-forbidden:

PermissionRequest fields:
- request_id
- actor_id
- tool_id
- action
- resource
- reason
- requested_at
- optional task_id
- optional session_id

PermissionDecision fields:
- request_id
- actor_id
- tool_id
- risk_level
- permission_requirement
- status
- reason_code
- optional matched_grant_id
- optional matched_policy_rule_id
- optional user_decision_id

Exact status vocabulary:
- allowed
- approval_required
- denied
- blocked

Exact risk vocabulary:
- P0, P1, P2, P3, P4, P5

Exact reason-code vocabulary:
- p0_internal
- explicit_user_approval
- explicit_user_denial
- matching_grant
- matching_policy
- p1_scope_required
- p2_approval_required
- p3_approval_required
- p4_explicit_approval_required
- p5_blocked

Exact status/risk/reason/evidence consistency is specified by PermissionDecision.consistent_evidence in phoenix/security/permissions.py. Mirror its structural output-contract validation for display admission; do not evaluate policy, grants, or authorization in TypeScript.

P5 always remains blocked/p5_blocked and can never be overridden by the UI. The UI must never expose approve/deny controls for P5. P4 requires explicit request-specific approval every time. An approval intent for one request never becomes a reusable grant.

UserPermissionDecision contains decision_id, request_id, decision and decided_at. The UI does not construct or forge this contract, invent IDs/timestamps, or authenticate the source of a click. Only a separately trusted adapter may turn the human's explicit intent into trusted evidence and submit it to the authoritative backend/PermissionEngine flow when that integration exists.

PHX-042 owns audit persistence. This ticket must not add audit writes, history persistence, grants, policy editing or security-engine changes.

## Required workspace

Replace only the existing permissions view placeholder with a Permission Center containing:

1. A heading and clear statement that this is a permission-request review surface, not the authorization engine.
2. An honest disconnected default: “No permission source connected.” No sample requests, grants, approvals, blocked actions or synthetic history.
3. A request list in caller-supplied source order with a local search field and match count.
4. A selected request detail panel showing request ID, actor/agent ID, tool ID, requested action, exact resource/scope, reason, request timestamp, and optional task/session correlation.
5. If a valid PermissionDecision is supplied, display its risk level, permission requirement, status and reason code, plus only the evidence IDs its contract permits. Label this as caller-supplied evaluation evidence, not live transport or execution proof.
6. If no evaluation is supplied, display “Evaluation not reported”; do not infer risk, status, policy, grant or action eligibility.
7. Explicit request-specific Approve and Deny buttons only for a valid supplied evaluation with status approval_required and a structurally consistent P1/P2/P3/P4 risk/reason pair, and only when a caller-supplied submitDecision callback exists.
8. For absent callbacks, disable the controls and explain that decision submission is not connected. Never imply a disabled click is pending.
9. For allowed, denied or blocked evaluations, render evidence read-only; do not show active decision buttons. P5 is always read-only and blocked.
10. Clear empty, filtered-empty, malformed, disconnected, degraded, pending and failed states.
11. Native keyboard-accessible controls, named inputs/buttons, aria-pressed or equivalent selected-state semantics, aria-controls where appropriate, semantic headings/landmarks, visible focus, polite result feedback and preserved skip-to-main behavior.
12. Responsive cards/list and detail layout at the existing 800x600 Tauri window, narrow-window stacking, long-text wrapping, bounded scrolling and reduced-motion support.

No grant duration selector, “remember my choice”, persistent grant, task/session grant creation, policy editor, bulk approval, bulk denial, Run/Execute control, or other privileged action.

The UI/UX specification asks permission prompts to show requested action, agent, resource, reason, risk, duration/scope and approve/deny controls. Use request task/session fields to show supplied scope. Do not invent a grant duration or imply that the request itself carries a PermissionGrant; PHX-084 does not manage grants.

## Optional PermissionPort contract

Add a narrow typed optional PermissionPort on App, consistent with the existing caller-owned ChatPort, AgentPort and TaskGraphPort presentation boundaries.

The port may contain only:
- status: connected, unavailable or degraded
- requests: readonly ordered list of strict request entries
- optional submitDecision(requestId, decision): Promise<void>

The callback signature is exactly request ID plus decision value approved or denied. It is an intent-delivery adapter only. No callbacks for grant creation, policy changes, execution, audit, tool dispatch, permission evaluation, refresh, or arbitrary methods.

The default App supplies no PermissionPort and performs no probing or transport.

If status is unavailable or degraded, suppress all supplied requests/evaluations and all stale selection/details even if data is present. Show the corresponding honest explanation. If the port is malformed, reject the entire snapshot with generic safe feedback and no partial request details.

A connected port is caller-supplied data, not proof of a live backend. Always visibly state that live permission transport is not implemented.

## Strict presentation data shape

Use camelCase display records that correspond exactly to PHX-041 contracts. They are copies for presentation, not new Python permission contracts.

Each request entry has exactly:
- request: required request snapshot
- optional evaluation: PermissionDecision snapshot

Request snapshot required fields:
- requestId
- actorId
- toolId
- action
- resource
- reason
- requestedAt

Optional request fields:
- taskId
- sessionId

Evaluation required fields:
- requestId
- actorId
- toolId
- riskLevel
- permissionRequirement
- status
- reasonCode

Optional evaluation fields:
- matchedGrantId
- matchedPolicyRuleId
- userDecisionId

Reject any unexpected own string or symbol key, missing required field, accessor, malformed descriptor, wrong primitive type, inconsistent optional evidence ID, unknown enum, duplicate request ID, mismatched request/evaluation requestId, mismatched actorId/toolId, sparse/extended array, or throwing reflective operation. Read own data descriptors only; do not invoke getters, coerce values or inspect request text as instructions. Catch reflective failures and return a generic rejection notice without raw diagnostics.

The top-level port accepts only the exact declared fields status, requests and optional submitDecision. A supplied submitDecision must be a function when present. Do not call it during validation, rendering, search, selection, mount, or navigation.

## Bounded admission

Apply whole-snapshot rejection, not partial rendering or silent truncation.

- Requests array: 0–64 entries.
- Request IDs and actor/tool/task/session IDs: exact, nonblank, already outer-trimmed strings, maximum 128 UTF-16 code units.
- Action: meaningful exact string, maximum 256 UTF-16 code units.
- Resource: meaningful exact string, maximum 512 UTF-16 code units.
- Reason: meaningful non-whitespace string, maximum 2000 UTF-16 code units; preserve original formatting.
- requestedAt: strict ISO-8601 timestamp normalized to UTC, with explicit UTC marker Z or +00:00; maximum 64 code units; validate without converting it into fabricated local time.
- permissionRequirement: meaningful exact string, maximum 256 UTF-16 code units.
- riskLevel/status/reasonCode: exact closed vocabularies listed above.
- All optional evidence IDs: if present, exact nonblank already-trimmed strings, maximum 128 UTF-16 code units.
- request IDs must be unique within the snapshot.
- Each evaluation's requestId, actorId and toolId must match its enclosing request exactly.
- Request/evaluation arrays and records must be copied to detached primitive snapshots before rendering.
- Caller objects are never mutated.
- Do not sort by current time, invent timestamps, derive a status from request text, or fabricate request rows.

Mirror PermissionDecision's structural consistency exactly:
- p0_internal: allowed, P0, no evidence IDs.
- explicit_user_approval: allowed, risk P1–P4, userDecisionId only.
- explicit_user_denial: denied, risk P0–P4, userDecisionId only.
- matching_grant: allowed, risk P1/P2/P3, matchedGrantId only.
- matching_policy: allowed, risk P2/P3, matchedPolicyRuleId only.
- p1_scope_required: approval_required, P1, no evidence IDs.
- p2_approval_required: approval_required, P2, no evidence IDs.
- p3_approval_required: approval_required, P3, no evidence IDs.
- p4_explicit_approval_required: approval_required, P4, no evidence IDs.
- p5_blocked: blocked, P5, no evidence IDs.

This is schema-consistency validation only. Never derive the risk from a request or descriptive text, re-evaluate policy/grants, or make an authorization decision.

## Human decision-intent behavior

A request is actionable only if all conditions hold:
- the port status is connected;
- the full snapshot is valid;
- a matching evaluation is present and structurally valid;
- evaluation status is approval_required;
- risk/reason pair is exactly one of P1/p1_scope_required, P2/p2_approval_required, P3/p3_approval_required or P4/p4_explicit_approval_required;
- submitDecision exists and has passed shape validation.

P0 and P5 never expose approval controls. No approval or denial button is enabled for allowed, denied, blocked, malformed, unevaluated, unavailable or degraded entries.

Only a real native button click may invoke submitDecision(requestId, approved|denied). Never invoke a callback from model text, request reason, keyboard shortcuts, rendering, an effect, automatic policy, a timer or a test harness in production code.

One decision intent may be in flight at a time. Prevent duplicates while pending. Require a native Promise resolving to undefined to treat callback completion as delivery acknowledgement; synchronous returns, malformed results and arbitrary thenables are failures. Catch thrown/rejected errors and show generic safe feedback without raw exceptions or private details.

A successful callback means only that the adapter acknowledged receiving an intent. It is NOT confirmation of a recorded UserPermissionDecision, a changed PermissionDecision, a created grant, authorization, tool execution, audit persistence or successful action.

After callback success, mark that request ID as “intent submitted; waiting for updated permission evidence” and block repeat submissions for the same request until it disappears or its supplied evaluation changes away from approval_required. Do not locally change the request/evaluation status. If the callback fails, retain the request and allow a deliberate user retry after settlement. No automatic retry, timeout, polling or cancellation.

Do not generate decision IDs or timestamps in the UI. Do not persist intent state in localStorage/sessionStorage/database. If the adapter never settles, retain the pending guard and say it is waiting; do not claim cancellation of delivery.

## Search, selection and display

- Search is local-only, case-insensitive and bounded to 80 UTF-16 code units.
- Search only admitted request/evaluation text fields; do not execute, parse, render as HTML/Markdown, or interpret them.
- Preserve source order.
- Default selection is the first admitted request, or none when empty.
- Select only request IDs present in the admitted snapshot.
- Selection survives navigation while App remains mounted.
- If the selected request disappears, falls outside the admitted snapshot, or the port disconnects/degrades, reset safely to the first admitted request or no selection.
- If filtering hides the selected request, label the off-filter selection explicitly and provide a Clear search action; never show unrelated details as the selected row.
- Request reason, resource, action, IDs, permission requirement and any other caller strings are untrusted inert text rendered by normal React JSX escaping.
- No HTML, Markdown, URL, code, image or link interpretation. No dangerouslySetInnerHTML.
- Display caller-supplied status as evidence only. Never state that an allowed decision proves execution or success.
- A request reason containing “approve”, “deny”, P0–P5 or instructions has no authority.

## Frozen security and dependency policy

Do not add:
- fetch, XMLHttpRequest, WebSocket, EventSource, network calls or availability probes;
- GET/POST permission endpoint implementation;
- Tauri invoke, plugin/IPC, shell, filesystem, clipboard or native calls;
- imports/calls of PermissionEngine, ToolRegistry, AuditTrail or Python;
- direct decision evaluation, grant minting, grant modification, policy editing, audit writes or tool execution;
- browser storage, database, event subscription, polling, timer, worker, background operation or auto-retry;
- new EventType, migration, backend/Rust/API changes or runtime dependency.

Production Tauri CSP remains unchanged, including production connect-src 'none'; permissions/capabilities remain empty. All package manifests and lockfiles remain unchanged.

No permission UI state may grant AIZEN, agents, models, caller data, task graph text or a request any execution authority. PHOENIX remains the sole supreme orchestrator; PermissionEngine remains the only P0–P5 authority; PHX-042 owns audit history.

## Exact authorized paths

Implementation:
1. apps/desktop/src/App.tsx
2. apps/desktop/src/styles.css
3. tests/workspace.test.mjs

Only after all required gates pass:
4. docs/21_FRONTEND.md
5. DECISIONS.md — exactly one new accepted ADR-071
6. FEATURE_TICKETS.md
7. tickets/PHX-084-permission-center.md

Maximum final diff: seven paths. No eighth path without explicit new authority.

Do not edit historical Python security guard files or any backend, Python, Rust, API, migration, Tauri configuration, manifest or lock file.

## Node test requirements

Baseline after PHX-083: 65 npm tests pass.

Preserve all PHX-080 shell, PHX-081 chat/voice, PHX-082 Agent Network and PHX-083 Task Graph tests. Preserve the three original PHX-003 workspace/Tauri/CSP test bodies and all historical security guard files unchanged.

Update the old placeholder assertion narrowly to exempt exactly home, agents, tasks and permissions. The other ten views must remain honest placeholders.

Extend AST call allowlists only for exact new local pure permission-validation and UI intent helper names. Do not weaken network/native/storage/permission-engine prohibitions.

Add deterministic tests for:
- default disconnected state with no synthetic requests or evaluations;
- unavailable/degraded status suppressing stale supplied requests and details;
- valid caller-supplied request/evaluation rendering with all exact fields;
- no evaluation means risk/status not reported and no action controls;
- all exact request and decision field names, required/optional fields and extra-key rejection;
- request/evaluation correlation mismatch;
- all statuses, P0–P5 and ten reason codes matching PHX-041 structural contract;
- P5 always blocked and never actionable;
- only a valid approval_required P1–P4 entry exposes controls;
- optional callback absence renders disabled/explained actions;
- native button clicks submit exact request ID and approved/denied value only;
- callback is never called by render, mount, search, selection or navigation;
- duplicate/in-flight guard, successful intent acknowledgement, no local decision mutation, failure/rejection, malformed acknowledgement and deliberate retry;
- successful intent prevents duplicate submission until updated evidence or request removal;
- malformed arrays, holes, extra keys/symbols, accessors, throwing proxies, wrong primitive types and oversized values reject whole snapshot safely;
- strict UTC timestamp validation and no fabricated time;
- request ID uniqueness and evaluation ID correlation;
- all string bounds, exact whitespace/case preservation, no coercion;
- selection, search, filter-hidden selection, request replacement/removal and disconnect reset;
- inert HTML/Markdown/URL/command-looking text;
- no grant creation, duration control, bulk action, tool execution or permission-policy logic;
- PHX-080/081/082/083 preservation;
- exact 14 navigation IDs/labels and ten remaining placeholders;
- unchanged Tauri CSP/capabilities and package manifests/locks.

Use only existing React SSR, TypeScript AST and hook-harness techniques. Do not add a test framework or claim SSR proves real mouse/keyboard GUI acceptance.

## Required gates

Run npm ci --ignore-scripts and prove manifests/locks unchanged.

Run npm test THREE consecutive complete times. Every run must pass; final count must exceed 65. After any relevant repair, restart the three-run sequence.

Run:
- npm run typecheck --workspace @phoenix/desktop
- npm run build --workspace @phoenix/desktop
- npm run tauri:info --workspace @phoenix/desktop
- cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml
- cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml
- npm run tauri:build --workspace @phoenix/desktop

All must pass. No GUI acceptance claim from a static build.

Run each exact historical guard THREE times:
- PHX-055 tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary
- PHX-052 tests/test_screenshot_vision_loop.py::test_no_action_persistence_or_dependency_change
- PHX-051 tests/test_windows_ui_automation.py::test_source_architecture_and_immutability

Then run complete historical suites unchanged:
- prompt injection: 604
- screenshot/vision: 397
- Windows UI automation: 493

Do not edit those historical test files. If an untouched historical guard requires a new exception, stop BLOCKED and request authority.

Run individual regressions:
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
- tests/test_wake_word_provider.py — 537
- tests/test_task_graph_state_machine.py — 348

Run the complete locked Python suite THREE consecutive times. Baseline is 15,721 passed, 16 existing Windows skips, one existing Starlette/httpx warning. Require identical skip identities/reasons and warning; no new skips/xfails, test suppression, flaky markers, retry decorators or warning suppression.

Run:
- uv sync --locked
- uv run --locked ruff check .
- uv run --locked ruff format --check .
- uv run --locked mypy

Build wheel and sdist outside the repository. Verify all 77 Python sources, direct dependency metadata, Python requirement and exact GET /health remain unchanged. Perform credential/private-artifact scan and individual INV-001 through INV-024 review with concrete evidence.

## Bounded autonomous repair loop

Do not stop at the first ordinary, in-scope UI, TypeScript, CSS, Node-test, formatting or build failure.

Repeat:
1. Capture exact failing command and exit status.
2. Reproduce the failure independently.
3. Classify root cause: implementation, test assertion, environment, or out-of-scope guard.
4. Make the smallest authorized fix.
5. Rerun the isolated failure.
6. Rerun affected frontend/security regressions.
7. Continue all required gates; any relevant code/test change invalidates its earlier receipts.
8. Finish with three consecutive clean npm runs and three consecutive clean Python runs after the last relevant repair.

Do not weaken tests, remove required checks, widen scope, bypass policy, or mark DONE with any required gate failing. STOP BLOCKED for a required eighth file, backend/API/PermissionEngine/AuditTrail/Tauri/dependency change, missing hard native prerequisite, genuine security concern, or three identical failures without progress/new evidence.

No reset, clean, rebase, amend, force push, main push, PR, merge or PHX-085 work from Codex.

## Documentation and completion

Only after all gates pass:
- add exactly one accepted ADR-071 describing request/evaluation evidence display, caller-owned decision-intent adapter, no local authorization/policy, P5 blocking, per-request P4 approval, strict snapshot bounds, no transport, no grant/audit/execution, and preserved PHX-080–083;
- update docs/21_FRONTEND.md narrowly;
- mark PHX-084 DONE in this ticket and FEATURE_TICKETS.md;
- keep Phase 8 INCOMPLETE and PHX-085–087 TODO;
- accurately record that live permission transport and manual native acceptance remain unimplemented/pending.

Review complete unstaged/staged diffs. Run git diff --check, git diff --cached --check, inspect staged stat and the entire staged diff. Stage only the seven authorized paths, no logs, receipts, caches, builds, databases, credentials or generated artifacts.

After every required gate passes, create exactly one completion commit:
feat: complete PHX-084 permission center

Push normally to feature/PHX-084-permission-center only. Never amend the ticket-preparation commit, force-push, push main, create a PR, merge or start PHX-085.

Expected successful topology: local and remote feature HEAD match; zero behind and exactly two ahead of unchanged main; clean working tree.

## Final report requirements

Provide a numbered report covering:
1. status, official path, branch, preparation SHA, expected/observed main SHA, completion SHA and exact paths;
2. Permission Center layout, disconnected default and passive port contract;
3. exact PHX-041 request/evaluation fields, statuses, P0–P5/risk/reason/evidence consistency;
4. snapshot bounds, whole-snapshot rejection, correlation, proxy/accessor handling and inert text;
5. request selection/search/filter behavior and reset rules;
6. action eligibility, P5 blocking, callback pending/success/failure/retry and no local authorization;
7. explicit preservation of PHX-080/081/082/083 and ten remaining placeholders;
8. npm test runs 1/2/3 and typecheck/Vite/Tauri/Cargo results;
9. historical exact guards x3 and all three historical full suites;
10. all 19 listed backend/security/media/voice/task-graph regressions;
11. full Python runs 1/2/3 with exact skips/warning;
12. Ruff/format/mypy/package/health parity;
13. security scan and INV-001–024 evidence;
14. ADR-071, documentation and ticket/phase status;
15. final diff, exact commit/push, ahead/behind, clean tree and unchanged main;
16. live permission transport, real decision authentication, grant/audit integration and manual WebView2 acceptance limitations;
17. PHX-085 NOT STARTED.

LIVE PERMISSION TRANSPORT: NOT IMPLEMENTED — no GET /permissions or POST approve/deny route is connected.

TRUSTED DECISION AUTHENTICATION: NOT IMPLEMENTED — the optional callback only receives an explicit UI intent; the trusted host/backend must authenticate/correlate it and produce authoritative PermissionEngine evidence.

GRANT / AUDIT / TOOL EXECUTION: NOT IMPLEMENTED by this ticket.

MANUAL NATIVE PERMISSION CENTER ACCEPTANCE: PENDING — no verified end-to-end WebView2 interaction.

These are explicit boundaries of this presentation ticket, not claims of successful integration.

PHX-084 remains TODO until every required gate passes. Phase 8 remains INCOMPLETE. PHX-085 is NOT STARTED.

## Completion evidence — 2026-10-09

Acceptance: DONE after all required deterministic gates passed; live/manual limitations
remain explicit below. Official repository D:/Project-Phoenix, feature branch
feature/PHX-084-permission-center; preparation 0fd7ee0653c81a4c8e560633482a013fc24a04bf,
main 05db77c30256e12045d16aa2b00568075a7df354. Exactly seven authorized paths:
App.tsx, styles.css, tests/workspace.test.mjs, docs/21_FRONTEND.md, DECISIONS.md,
FEATURE_TICKETS.md and this ticket. No backend/native/manifests/locks changes.

Implementation and accepted ADR-071: strict exact own-data request/evaluation
admission, 0..64 unique IDs, exact field bounds/correlation/UTC and all ten PHX-041
consistency rules; detached frozen primitives, inert JSX, safe generic errors,
source-order bounded search/selection, conservative reset and supplied evidence labels.
Only explicit eligible native P1-P4 clicks deliver approved/denied request ID intent.
P5 has no controls. Global pending guard and native Promise<void> delivery ACK never
mutate evaluation or create authorization, grant, decision ID/time, audit or execution.
Repeated ID remains blocked until valid evidence changes away or disappears; failure
retains request for deliberate retry. Reflection can run Proxy traps; no JS sandbox
or authentication claim. PHX-080/081/082/083 core blocks and behavior preserved.

| Required command / gate | Observed result |
| --- | --- |
| npm ci --ignore-scripts | PASS; manifests/locks unchanged |
| uv run --locked pytest tests/test_screenshot_vision_loop.py::test_no_action_persistence_or_dependency_change -q --junitxml=[external validation directory]\historical-screenshot_vision_loop-exact-1.xml | exit 0 |
| uv run --locked pytest tests/test_screenshot_vision_loop.py::test_no_action_persistence_or_dependency_change -q --junitxml=[external validation directory]\historical-screenshot_vision_loop-exact-2.xml | exit 0 |
| uv run --locked pytest tests/test_screenshot_vision_loop.py::test_no_action_persistence_or_dependency_change -q --junitxml=[external validation directory]\historical-screenshot_vision_loop-exact-3.xml | exit 0 |
| uv run --locked pytest tests/test_windows_ui_automation.py::test_source_architecture_and_immutability -q --junitxml=[external validation directory]\historical-windows_ui_automation-exact-1.xml | exit 0 |
| uv run --locked pytest tests/test_windows_ui_automation.py::test_source_architecture_and_immutability -q --junitxml=[external validation directory]\historical-windows_ui_automation-exact-2.xml | exit 0 |
| uv run --locked pytest tests/test_windows_ui_automation.py::test_source_architecture_and_immutability -q --junitxml=[external validation directory]\historical-windows_ui_automation-exact-3.xml | exit 0 |
| uv run --locked pytest tests/test_screenshot_vision_loop.py -q --junitxml=[external validation directory]\historical-screenshot_vision_loop.xml | exit 0 |
| uv run --locked pytest tests/test_windows_ui_automation.py -q --junitxml=[external validation directory]\historical-windows_ui_automation.xml | exit 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary -q --junitxml=[external validation directory]\guards-exact-1.xml | exit 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary -q --junitxml=[external validation directory]\guards-exact-2.xml | exit 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary -q --junitxml=[external validation directory]\guards-exact-3.xml | exit 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py -q --junitxml=[external validation directory]\guards-suite-1.xml | exit 0 |
| uv run --locked pytest tests/test_api.py -q --junitxml=[external validation directory]\regressions-api.xml | exit 0 |
| uv run --locked pytest tests/test_bootstrap.py -q --junitxml=[external validation directory]\regressions-bootstrap.xml | exit 0 |
| uv run --locked pytest tests/test_agent_registry.py -q --junitxml=[external validation directory]\regressions-agent_registry.xml | exit 0 |
| uv run --locked pytest tests/test_agent_model_router.py -q --junitxml=[external validation directory]\regressions-agent_model_router.xml | exit 0 |
| uv run --locked pytest tests/test_tool_registry.py -q --junitxml=[external validation directory]\regressions-tool_registry.xml | exit 0 |
| uv run --locked pytest tests/test_permission_engine.py -q --junitxml=[external validation directory]\regressions-permission_engine.xml | exit 0 |
| uv run --locked pytest tests/test_audit_trail.py -q --junitxml=[external validation directory]\regressions-audit_trail.xml | exit 0 |
| uv run --locked pytest tests/test_secrets_boundary.py -q --junitxml=[external validation directory]\regressions-secrets_boundary.xml | exit 0 |
| uv run --locked pytest tests/test_spotify_integration.py -q --junitxml=[external validation directory]\regressions-spotify_integration.xml | exit 0 |
| uv run --locked pytest tests/test_youtube_integration.py -q --junitxml=[external validation directory]\regressions-youtube_integration.xml | exit 0 |
| uv run --locked pytest tests/test_document_generation.py -q --junitxml=[external validation directory]\regressions-document_generation.xml | exit 0 |
| uv run --locked pytest tests/test_upload_parser_routing.py -q --junitxml=[external validation directory]\regressions-upload_parser_routing.xml | exit 0 |
| uv run --locked pytest tests/test_rise_phoenix_startup.py -q --junitxml=[external validation directory]\regressions-rise_phoenix_startup.xml | exit 0 |
| uv run --locked pytest tests/test_voice_conversation.py -q --junitxml=[external validation directory]\regressions-voice_conversation.xml | exit 0 |
| uv run --locked pytest tests/test_tts_provider.py -q --junitxml=[external validation directory]\regressions-tts_provider.xml | exit 0 |
| uv run --locked pytest tests/test_stt_provider.py -q --junitxml=[external validation directory]\regressions-stt_provider.xml | exit 0 |
| uv run --locked pytest tests/test_vad.py -q --junitxml=[external validation directory]\regressions-vad.xml | exit 0 |
| uv run --locked pytest tests/test_wake_word_provider.py -q --junitxml=[external validation directory]\regressions-wake_word_provider.xml | exit 0 |
| uv run --locked pytest tests/test_task_graph_state_machine.py -q --junitxml=[external validation directory]\regressions-task_graph_state_machine.xml | exit 0 |
| uv run --locked pytest --junitxml=[external validation directory]\full-run-1.xml | exit 0 |
| uv run --locked pytest --junitxml=[external validation directory]\full-run-2.xml | exit 0 |
| uv run --locked pytest --junitxml=[external validation directory]\full-run-3.xml | exit 0 |
| uv sync --locked | exit 0 |
| uv run --locked ruff check . | exit 0 |
| uv run --locked ruff format --check . | exit 0 |
| uv run --locked mypy | exit 0 |
| uv build --out-dir [external validation directory]\dist | exit 0 |
| npm.cmd test | exit 0 |
| npm.cmd test | exit 0 |
| npm.cmd test | exit 0 |
| npm.cmd run typecheck --workspace @phoenix/desktop | exit 0 |
| npm.cmd run build --workspace @phoenix/desktop | exit 0 |
| npm.cmd run tauri:info --workspace @phoenix/desktop | exit 0 |
| cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml | exit 0 |
| cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml | exit 0 |
| npm.cmd run tauri:build --workspace @phoenix/desktop | exit 0 |


Three final npm runs: 87 passed, 0 failed/skipped each. TypeScript, Vite, Tauri info,
Cargo check/test and Tauri build passed. All three historical exact guards passed
three times each; full prompt/screenshot/Windows suites: 604/397/493. Individually
run API 8, bootstrap 11, registry 96, router 213, ToolRegistry 204, PermissionEngine
926, audit 302, secrets 402, Spotify 345, YouTube 358, documents 284, upload 336,
startup 509, conversation 950, TTS 602, STT 414, VAD 403, wake 537, task graph 348.
Full Python runs 1/2/3: 15,721 passed and the identical 16 existing Windows skip
identities/reasons plus the identical existing Starlette/httpx warning; no new
skip/xfail/marker/retry/filter/warning suppression. Ruff check/format and mypy pass.
Wheel and sdist built outside repo: all 77 Python source bytes equal worktree and
Git-normalized main, identical direct dependencies and Requires-Python <3.12,>=3.11.
API eight tests prove repeated exact GET /health 200/application-json,
status ok/service phoenix-api/installed package version; unknown route 404;
factory isolation and source hash to main unchanged. No permission API routes.

Bounded repairs: initial node --test tests/workspace.test.mjs exited 1, reproduced
with --test-name-pattern='permission data rejects' (exit 1): the new assertion
mistakenly required optional evaluation. A minimal test-only fix excluded that
optional key from missing-required-key cases; missing-evaluation/no-controls coverage
remained. Focused retest passed.

A focused unavailable/degraded-source test then reproduced an implementation gap
(exit 1 twice): suppression occurred before nested request/evaluation correlation
validation. Moving suppression after whole-snapshot admission fixed it; focused
retest passed. All frontend gates and the three-run full Python sequence restarted
after this last product/test repair. The earlier passed full run and intentionally
interrupted second run are preserved under external pre-repair and excluded.

An external-only documentation helper encoding error (py_compile exit 1) was
reproduced and repaired with explicit UTF8 output; py_compile then passed. No
project file or product/test gate had changed for that support-script repair.

Later verification sequences ended with Windows process status 1073807364 at 46%
and 18%, without pytest failure reports or completed JUnit results. The last active
memory suite passed in isolation (167 tests). An observed code-mode-host restart
coincided with the second termination. These sequences are preserved under external
process-termination and host-restart and excluded from acceptance. The identical
one-shot verifier ran hidden via the Windows process service with original PATH,
independently of the tool-host lifecycle; no OS settings, permissions, product/test
code or final test filtering changed. Its three fresh complete runs passed and
matched the exact existing skips/warning. No out-of-scope project change or gate
bypass. The standalone preliminary TypeScript check also passed.

Documentation-only diff check failed (exit 1) and reproduced with new Date/Status
hard-break trailing spaces and extra EOF blanks. New prose also had doubled line
breaks from Windows helper newline translation. Narrow formatting repair kept
prior content and frozen ticket body intact except Status; isolated diff check
then passed. Product/tests and final npm/Python sequences were unchanged.

Security review: exact seven-path maximum, no untracked/private artifacts, added-line
credential signature scan found none (not a comprehensive secret-detection guarantee).
Protected backend/native/manifests/locks/historical guards and prior tickets unchanged;
three original PHX-003 test bodies byte-equal. No network/IPC/provider/storage/timer/
polling/worker, policy/risk derivation, grant/audit persistence or tool execution.

| Invariant | Individual evidence |
| --- | --- |
| INV-001 | Exact 30-entry frontend/document/Python ordered parity and sole phoenix Supreme orchestrator checked by npm; full orchestrator tests pass, production unchanged. |
| INV-002 | strategist/AIZEN retains Chief strategist/master planner metadata and subordinate designation; no executable authority; registry 96/router 213 and full planner tests pass. |
| INV-003 | No LLM output, request reason or display status grants authority; no direct PermissionEngine call or locally created decision. Explicit native button callback delivers intent only; PermissionEngine 926 unchanged and passed. |
| INV-004 | App has no tool/IPC imports; ToolRegistry 204 and PermissionEngine 926 passed. |
| INV-005 | P4 review requires an admitted exact P4/p4_explicit_approval_required evaluation and explicit native click. Callback sends only a request ID and human intent; trusted identity correlation and authorization remain PermissionEngine 926, unchanged. |
| INV-006 | P5/p5_blocked is the only admitted P5 evaluation; no Approve/Deny controls or callback eligibility. Exhaustive 1,920 combination tests and unchanged PermissionEngine 926/SecretStore 402 pass. |
| INV-007 | AuditTrail 302 passed; audit/persistence source unchanged; no UI privileged actions. |
| INV-008 | All 48 required commands and npm ci have zero final exits before DONE; optional-evaluation assertion and unavailable/degraded admission-order repairs documented; final npm/full Python sequences restarted; live permission transport/authentication, grants/audit/execution and manual native acceptance explicitly remain unimplemented/pending. |
| INV-009 | Recovery source unchanged; test_mahoraga_recovery.py::test_explicit_positive_budgets and ::test_bounded_replan_membership_failed_step_and_order are included in all three full pytest runs. |
| INV-010 | No recovery mode controls; test_kaido_failsafe.py::test_decision_is_degraded_only is included in all three full pytest runs; source unchanged. |
| INV-011 | Exact npm AST call allowlist permits React/local helpers and one supplied sendText call only inside sendDraft and one supplied submitDecision call only inside deliverPermissionIntent; provider/network/native/media/storage APIs banned; CSP connect-src none and empty capabilities unchanged. |
| INV-012 | No provider or agent production edits; registry 96 and router 213 passed. |
| INV-013 | No tool/policy production edits; ToolRegistry 204 passed, inert permission/audit metadata protected. |
| INV-014 | Only exact canonical IDs select agent details/status; permission IDs remain opaque exact request IDs, never grant authority; every alias lookup rejected by npm; Python alias-removal/collision registry tests pass; 14 routes unchanged. |
| INV-015 | Native controlled local textarea remains editable while disconnected; named form Send and all 14 nav buttons work without a microphone; disabled voice explained. |
| INV-016 | No voice initialization in shell; startup 509, conversation 950, TTS 602, STT 414, VAD 403, wake 537 passed. |
| INV-017 | Snapshot roles limited to user/assistant, exact data fields, count 64, content 4000, ID 128, unique IDs; malformed/oversize entries omitted with notice, complete accepted text React-escaped; no parsing or external resources. Task whole-graph admission checks 1..40 steps, <=120 edges, exact known owners, 11 canonical task states, strict text/array/timeout bounds, no duplicate/unknown/self dependencies or cycles, source-priority traversal, inert JSX and generic errors. Agent snapshots admit at most 30 unique known IDs, 4 closed states, own data fields only, task<=240/provider<=120 UTF16; invalid whole snapshot rejected with generic notice; JSX escaping tested. Permission snapshots enforce <=64 unique requests, exact own data fields/correlation, UTF16 bounds/strict UTC, ten exact evidence combinations, generic reflection failure, detached frozen primitives and inert JSX; model-like approval text never changes eligibility. Prompt 604, upload 336 passed. |
| INV-018 | No storage or backend in App; SecretStore 402 passed; memory/security production unchanged. |
| INV-019 | Caller-triggered one-flight send exposes pending state and Cancel waiting. Local cancellation retains draft even after late ack, explicitly cannot abort delivery, and duplicates remain blocked until adapter settles; no timer/polling/hidden worker. Permission intents expose global pending state, prevent all duplicates until settlement, retain pending across navigation/source loss and explain that UI cannot cancel adapter delivery; no cancellation feature promised. Hook/helper tests pass. |
| INV-020 | No migration changes; historical guard preserves sole Alembic head 0005_audit_events and EventType 32, exact test x3 passed. |
| INV-021 | Only connected supplied Promise<void> ack can clear matching draft; synchronous/thenable/malformed result and exceptions fail safely, retain draft, never append transcript; Permission intent requires native Promise resolving undefined; sync/thenables/malformed acknowledgements/throws/rejections fail safely with deliberate retry and unchanged evaluation. ACK blocks repeat ID until valid evidence changes away or ID disappears; never reports authorization. All provider/tool regressions pass. |
| INV-022 | Unknown view falls back Home; unknown/alias agent selection retains last valid identity, no identity invention; missing evidence Status not reported, disconnected/degraded no active claims; off-list selection explicitly labelled; task selection resets across task IDs/missing steps/disconnect, default no graph; Permission default source disconnected; unavailable/degraded/malformed evidence suppresses stale details, missing evaluation never infers risk/status/actions, removed selection resets to first/none and off-filter notice explicit. Chat/voice fallback preserved. |
| INV-023 | No model generation path; phoenix/models/lifecycle.py default capacity one remains unchanged; test_model_lifecycle.py::test_global_capacity_blocks_different_providers_and_models and invalid/positive capacity cases pass in all three full suites. |
| INV-024 | All npm/Python manifests and locks unchanged; existing-dependency UI only; package dependency metadata equals main. |


External logs/JSON/XML/package artifacts only:
C:/Users/shoai/.codex/validation/PHX-084-20261009. Gate acceptance, exact skip/warning
comparison, package parity and security review receipts precede this status change.
Full staged-diff/scope and normal feature-only delivery receipts are captured there;
completion SHA is reported after commit rather than embedded in itself. Frozen
authoritative ticket body preserved except its Status line and this appended receipt.

LIVE PERMISSION TRANSPORT: NOT IMPLEMENTED — no GET /permissions or POST approve/deny.
TRUSTED DECISION AUTHENTICATION: NOT IMPLEMENTED — callback delivers UI intent;
trusted host/backend must authenticate/correlate and create authoritative evidence.
GRANT / AUDIT / TOOL EXECUTION: NOT IMPLEMENTED by this ticket.
MANUAL NATIVE PERMISSION CENTER ACCEPTANCE: PENDING — no verified native WebView2
end-to-end mouse/keyboard test; deterministic SSR/AST/hooks/builds are not GUI proof.
Phase 8 INCOMPLETE; PHX-085 through PHX-087 TODO. PHX-085 NOT STARTED.
