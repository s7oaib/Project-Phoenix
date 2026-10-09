# PHX-084 — Permission Center

Status: TODO  
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