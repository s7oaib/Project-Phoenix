# 21 — Frontend Architecture

## Stack

Implemented through PHX-084: Tauri 2, React 19, TypeScript 6, Vite 8 and CSS.
Tailwind CSS, shadcn/ui, Zustand, TanStack Query and React Flow/XYFlow remain
architecture choices for separately justified later tickets; PHX-080/081/082/083/084 add no dependency.

## PHX-080 app shell

App.tsx and styles.css own a self-contained local shell with persistent PHOENIX
branding, "Rise from the ashes.", primary navigation and a main landmark.
Mission, Workspace and System group fourteen stable view IDs:
onboarding, home, agents, tasks, memory, files, computer, research, media,
permissions, models, voice, settings and observability. Labels follow the closed
information architecture in docs/20_UI_UX_SPEC.md. Home / Chat is the default;
the pure resolver maps unknown inputs to home without string coercion or dynamic code.

Native named buttons update local React state, the main heading/panel and
aria-current="page". A skip-to-main link, visible focus and post-render main focus
support keyboard use without global keyboard hooks or a microphone. The cinder/amber
CSS uses system fonts, readable contrast, reachable scrolling navigation at the
unchanged 800x600 window size, a stacked narrow layout and reduced-motion treatment.

PHX-081 replaces only the Home preview with the conversation surface below.
PHX-082 replaces Agent Network with the canonical directory below; PHX-083 adds
the read-only Task Graph. PHX-084 adds the Permission Center. The other ten
panels remain explicitly unconnected.
Backend integration is pending; no operational health, agent, task, permission,
memory, model or voice success is fabricated.

PHX-082 implements the agent directory and PHX-083 the read-only task view.
PHX-084 implements permission evidence/human intent, PHX-085 owns memory/files,
PHX-086 models/voice/settings and PHX-087 observability. PHX-085 through PHX-087
remain TODO; Phase 8 is INCOMPLETE.

## PHX-081 chat / voice presentation

Home has a semantic Conversation heading, a keyboard-focusable scrollable transcript,
an explicitly labelled Local draft only textarea and a disabled-by-default Send.
App-owned React state retains drafts and the one-flight guard across navigation;
no persistence or automatic submission exists. Native maxLength and send admission
limit raw input to 4000 UTF-16 code units, rejecting empty/whitespace and oversized
input, then sending only trimmed nonempty text. The visible counter explains the limit.

App accepts an optional typed ChatPort with connected/unavailable/degraded status,
readonly user/assistant message snapshots and sendText(text): Promise<void>. Default
App has no port. Shape checks reject missing data/methods and avoid evaluating data
accessors; there is no HTTP/WebSocket/native implementation or availability probe.
Only an explicitly connected supplied callback can receive an admitted draft. One
request remains in flight until settlement; no timeout, polling or background worker.
A native Promise resolving undefined acknowledges the submission. Only that result
clears an unchanged matching draft; exceptions/malformed acknowledgements retain it
and display safe retry guidance. No assistant response or user history is appended.

Cancel waiting is local UI cancellation, not transport cancellation: the draft is
retained even after late acknowledgement, delivery remains unknown, and duplicate
sends stay blocked until the borrowed Promise settles. An adapter that never settles
keeps the pending guard; a future transport owner must provide actual cancellation.

Only supplied snapshots become history. Accept at most the first 64 entries, each
with exactly id/role/content data fields, unique nonblank opaque IDs up to 128 code
units, user or assistant role and nonblank content up to 4000 code units. Omit malformed,
duplicate or oversized entries completely and show an explicit omission notice;
never silently truncate permission/error text. Render accepted content through JSX
text escaping, preserving whitespace and wrapping long strings. No HTML/Markdown,
code, link or image interpretation and no frontend permission authority.

The adjacent/stacked Voice area explicitly says Voice unavailable / Not connected,
explains its disabled microphone button and keeps text available. Capture, wake,
STT/TTS and playback have no frontend binding; the separate Voice destination stays
a placeholder for PHX-086. No microphone prompt, capture or synthesized telemetry.

Native form/textarea/button semantics, descriptive reasons, visible focus and polite
pending/result feedback supplement the unchanged skip link/navigation. Cinder/amber
CSS bounds transcript scrolling, leaves the composer in document flow and stacks the
panels below 1000px, including the unchanged 800x600 Tauri window. System fonts,
contrast and reduced motion remain intact.

## PHX-082 Agent Network presentation

Agent Network presents exactly 30 defined logical identities: PHOENIX, the sole
supreme orchestrator, and 29 specialists. A frozen display-only TypeScript reference
in App.tsx retains every internal ID, alias, professional role and source order from
DEFAULT_AGENT_DEFINITIONS and docs/03_AGENT_ROSTER.md. Node tests compare all triples
against both files. This is a directory of definitions, never initialized-agent or
online-agent evidence. Aliases are display metadata, not authoritative lookup keys.

App-owned local search and canonical-ID selection survive navigation independently
of the unchanged chat draft/flight state. Native search input and helper admission
limit input to 80 UTF-16 units; case-insensitive substring matching uses alias, ID
and role in canonical order. Clear search restores all 30. Match counts and no-results
copy are accessible. Default selection is phoenix; unknown/alias/object selection
retains the last valid ID, never manufactures a PHOENIX identity. Details retain the
selected role while filtering and explicitly label off-list selection with a reachable
Clear search control. Native selection buttons expose aria-pressed and aria-controls;
no global shortcuts, initial focus theft or permission/agent action controls.

App accepts an optional caller-owned passive readonly AgentPort: status is exactly
connected/unavailable/degraded and agents is a readonly array of records with only
internalId, state and optional currentTask/provider strings. There are no operations.
Default App supplies no port and displays Live agent state not connected. Absent
individual evidence says Status not reported, never idle. Unavailable/degraded ports
display no activity even if records are supplied. An explicitly connected valid port
is labelled caller-supplied evidence, never proof of a live backend connection.

Admission snapshots the entire array before any record is displayed: at most 30
unique known canonical IDs, closed idle/working/waiting_permission/degraded states,
exact own data fields, task <=240 and model/provider <=120 UTF-16 units. Reject sparse
or extended arrays, accessors, extras, duplicate/unknown IDs, malformed or oversized
records as a whole with generic safe notice. No partial task/provider clipping, raw
objects or exception leakage. Only validated copied primitives reach React JSX text
escaping; HTML/Markdown/permission-looking content remains inert.

Descriptor reads avoid property getters, coercion and ordinary Proxy get traps;
throwing or inconsistent reflective traps fail safely. Browser JavaScript cannot
identify or sandbox arbitrary same-realm proxies: reflective traps may run, and a
forwarding proxy is indistinguishable from data. Caller objects remain trusted
infrastructure; these checks bound display data rather than authenticate evidence
or grant permission. No data is written back to the borrowed port.

Cinder/amber CSS uses readable native inputs/buttons, wrapped details/status text,
a bounded scrolling result list, two columns at wide widths and stacking below
1000px for the unchanged 800x600 native window. Narrow search controls stack below
460px. Existing sidebar reachability, skip link, focus and reduced-motion rules stay
intact. PHX-081 chat code remains byte-identical apart from view composition.

No GET /agents, HTTP/WebSocket, Tauri IPC, provider probe, model assignment, execution,
dispatch, permission action, storage, timer or polling is implemented. No dependency,
backend, Rust, manifest, lock or CSP/capability change. Forty-five deterministic
SSR/pure/AST/hook tests and static native builds pass; SSR is not native GUI evidence.

LIVE AGENT STATUS ACCEPTANCE: NOT IMPLEMENTED — no live GET /agents transport is implemented.
LIVE AGENT CONTROL ACCEPTANCE: NOT IMPLEMENTED — no agent execution, dispatch or permission actions are exposed.
MANUAL NATIVE AGENT NETWORK ACCEPTANCE: PENDING — no real end-to-end WebView2 interaction has been independently verified.

## PHX-083 Task Graph presentation

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
to main, apart from task-view composition. At PHX-083 completion eleven views stayed
placeholders; PHX-084 leaves ten.
No GET/POST /tasks, WS/events, execution, cancel/retry, approval, verification action,
provider/tool/native call, persistence or new dependency. Python/backend/Rust/Tauri
configs/CSP/empty permissions/manifests/locks remain unchanged. PHOENIX stays supreme.

LIVE TASK GRAPH ACCEPTANCE: NOT IMPLEMENTED — no live GET /tasks or /tasks/{id}
backend transport or WS /events is connected.
LIVE TASK EXECUTION/CANCELLATION ACCEPTANCE: NOT IMPLEMENTED — no execution,
cancellation, transition, recovery, verification or permission-action controls.
MANUAL NATIVE TASK GRAPH ACCEPTANCE: PENDING — no verified end-to-end WebView2
mouse/keyboard interaction; SSR/hook/CSS/native build evidence is not GUI acceptance.

## PHX-084 Permission Center

PHX-084 replaces only Permissions with a Permission Center for caller-supplied
request/evaluation evidence and explicit human intent. PermissionEngine (PHX-041)
remains the sole authorization authority. Default App has no PermissionPort and says
No permission source connected, with no invented requests, grants or timestamps.
Connected means caller-supplied evidence, not live backend connectivity; unavailable
or degraded ports hide requests and stale details. Malformed evidence fails closed.

Optional readonly PermissionPort has only status (connected/unavailable/degraded),
requests and optional submitDecision(requestId, approved|denied): Promise<void>.
Each entry contains request and optional evaluation. Request fields are exactly
requestId, actorId, toolId, action, resource, reason, requestedAt and optional
taskId/sessionId. Evaluation fields are exactly requestId, actorId, toolId, riskLevel,
permissionRequirement, status, reasonCode and optional matchedGrantId,
matchedPolicyRuleId/userDecisionId. Missing evaluation says Evaluation not reported;
no risk/status is inferred from request text or identifiers.

Closed evaluation statuses are allowed, approval_required, denied, blocked; risks
are P0 through P5. Display admission mirrors PHX-041 consistent_evidence exactly:
p0_internal: allowed/P0/no evidence; explicit_user_approval: allowed/P1-P4/only
userDecisionId; explicit_user_denial: denied/P0-P4/only userDecisionId; matching_grant:
allowed/P1-P3/only matchedGrantId; matching_policy: allowed/P2-P3/only
matchedPolicyRuleId; p1_scope_required, p2_approval_required, p3_approval_required and
p4_explicit_approval_required: approval_required/respective P1-P4/no evidence;
p5_blocked: blocked/P5/no evidence. These checks validate supplied output shape,
never derive risk or evaluate policy, grants, evidence precedence or authorization.

Whole-snapshot admission requires 0..64 unique request IDs, exact own data descriptors,
no missing/extra fields, symbols, accessors, sparse/extended arrays or bad primitives.
IDs (request, actor, tool, task, session and optional evidence) are nonblank already
outer-trimmed strings <=128 UTF16 units. Meaningful action/permissionRequirement are
<=256, resource <=512 and non-whitespace reason <=2000, with formatting preserved.
requestedAt is <=64 units and a real Gregorian ISO UTC date/time with Z or +00:00;
no fabricated local time, clock, date sorting or generated decision timestamp exists.
Every evaluation must correlate requestId/actorId/toolId exactly. Invalid or oversized
entries reject the whole snapshot; no truncation, repair or partial display. Admitted
primitives/records/arrays are detached and frozen; caller-owned data never changes.
Reflection failures become generic notices; getters/coercion are avoided. Reflective
Proxy traps may run: validation is not a same-realm JavaScript sandbox or authentication.

Local case-insensitive search covers admitted request/evaluation strings and is capped
at 80 UTF16 units, preserving source order with match counts, empty/no-match notices
and Clear search. App owns selection: first admitted ID by default, only admitted IDs
can be selected, selection survives navigation, removed IDs reset to first/none and
source loss/degradation clears details. Off-filter selected details are labelled.
All supplied text is inert JSX, with no HTML, Markdown, links, images or code execution.

Native Approve/Deny buttons deliver only a request-specific human intent. Eligibility
requires a valid connected complete snapshot, exact correlated approval_required
P1/P2/P3/P4 reason pair and a validated callback. Missing callback disables controls
with an explanation. P0, P5, allowed, denied, blocked and unevaluated requests never
offer decision controls. P5 is unconditionally blocked. There is no bulk action,
remember choice, grant duration, policy editing, cancellation or tool execution.

The callback is invoked only from explicit native click handlers, never render,
mount, validation, search, selection, navigation, effects, timers or background work.
It receives only the exact request ID and approved or denied. One global intent may
be pending; the guard survives navigation/source loss and blocks duplicates until
settlement. A native Promise resolving undefined acknowledges delivery only, leaves
the evaluation unchanged and blocks that request ID until valid supplied evidence
changes away from approval_required or the request disappears. Source disconnection,
malformed evidence and temporarily absent evaluation conservatively retain that guard.
Synchronous/thenable/malformed results, throws and rejections fail generically, retain
evidence and allow deliberate retry after settlement. No automatic retry, timeout,
polling or local cancellation is claimed; a never-settled callback remains pending.
No UserPermissionDecision, decision IDs/timestamps, grants, policy results, audit writes
or authorization are created. Trusted host/backend must authenticate and correlate
human intent and independently supply authoritative evidence.

Named native inputs/buttons, semantic headings, aria-pressed/controls, disabled-state
explanations and polite feedback preserve skip/navigation focus. Long text wraps,
list scrolling remains reachable and list/detail stack below 1000px for the unchanged
800x600 Tauri window. Existing contrast, narrow layout and reduced motion remain.
All fourteen routes and PHX-080 shell/081 chat/082 agent/083 task behavior remain;
their core implementation blocks are byte-equal to prior main. Ten views still show
honest placeholders. Production CSP, empty capabilities, Rust, backend, manifests,
locks and dependencies are unchanged. PHOENIX remains supreme; LEVI/recovery/modes
and ToolRegistry/PermissionEngine/AuditTrail boundaries remain intact.

LIVE PERMISSION TRANSPORT: NOT IMPLEMENTED — no GET /permissions, POST approve/deny,
HTTP/WebSocket or backend permission client is connected; API still only GET /health.
TRUSTED DECISION AUTHENTICATION: NOT IMPLEMENTED — callback delivers UI intent only;
trusted adapter/backend must authenticate/correlate it and create authoritative evidence.
GRANT / AUDIT / TOOL EXECUTION: NOT IMPLEMENTED — no grant creation, audit persistence,
policy evaluation, native execution, tools or permission bypass in this ticket.
MANUAL NATIVE PERMISSION CENTER ACCEPTANCE: PENDING — no WebView2 end-to-end
mouse/keyboard acceptance; SSR/AST/hook/CSS/build evidence is not native GUI proof.

## Boundaries

Frontend may render state, send intents, request permission decisions, display tasks/events, and manage local UI preferences.

Those are target boundaries. PHX-080/081/082/083/084 only select views, draft/search locally, display
supplied plain-text snapshots and optionally submit chat or permission human intent
to a borrowed callback. There is
no HTTP/WebSocket, backend client, provider, database, tool, permission decision or
Tauri IPC/plugin invocation. Existing production/development CSP, empty permission
grants, entry point, native Rust and dependency manifests remain unchanged.

Frontend may not:
- call Ollama directly
- call Colibri directly
- execute privileged OS actions directly
- access SQLite directly
- implement permission policy

## State

- Current PHX-080/081/082/083/084 UI selection, search and local drafts: React useState/useRef;
  useEffect moves focus after a committed navigation change without stealing initial focus.
- Planned server state: TanStack Query, under a later authorized transport ticket.
- Planned broader UI state: Zustand; no router/history or browser-storage persistence
  is introduced by PHX-080/081/082/083/084.
- Planned live orchestration state: backend WebSocket events, not connected yet.

## Suggested later structure

```text
src/
  app/
  features/
    chat/
    voice/
    agents/
    tasks/
    permissions/
    memory/
    files/
    models/
    settings/
    observability/
  components/
  lib/
  api/
```

## Reliability

PHX-080 provides a safe unknown-view fallback and explicit not-yet-connected panels.
PHX-081 adds explicit unavailable/degraded, pending, safe failure and local cancel
states. Later transport integration still needs real reconnect/cancellation and
runtime error boundaries.

Deterministic Node React SSR, pure resolver and TypeScript AST contracts cover
navigation, focus wiring, draft admission, snapshot safety and security. Deterministic
hook-handler tests verify pending/duplicate/ack/error/cancellation behavior, including
navigation during an in-flight send; these are not native DOM event tests. Frontend
passes 87 tests three consecutive times; the three PHX-003 workspace/CSP test bodies
remain unchanged. Historical PHX-055, PHX-052 and PHX-051 guards exclude only App.tsx
and styles.css while retaining all other desktop/backend protection.
Static Tauri builds pass; manual WebView2 mouse/keyboard acceptance remains pending.

LIVE CHAT ACCEPTANCE: NOT IMPLEMENTED — backend POST /chat transport is not yet
present. UI defaults disconnected; fake-port tests are not live integration.
LIVE VOICE ACCEPTANCE: NOT IMPLEMENTED — microphone capture, wake, STT/TTS and
backend voice binding are not provided in this frontend ticket.
MANUAL DESKTOP CHAT/VOICE ACCEPTANCE: PENDING — no real GUI/WebView2 end-to-end
interaction performed; deterministic SSR/AST/build evidence is not a substitute.
