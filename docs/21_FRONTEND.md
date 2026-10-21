# 21 — Frontend Architecture

## Stack

Implemented through PHX-087: Tauri 2, React 19, TypeScript 6, Vite 8 and CSS.
Tailwind CSS, shadcn/ui, Zustand, TanStack Query and React Flow/XYFlow remain
architecture choices for separately justified later tickets; PHX-080/081/082/083/084/085/086/087 add no dependency.
PHX-107 adds exactly one: `@tauri-apps/api` 2.12.1 (matching the `tauri` crate), used only by
`src/adapters/tauri.ts` (see "PHX-106/107 live desktop integration" below).

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
the read-only Task Graph. PHX-084 adds the Permission Center; PHX-085 adds the
read-only Memory and Files workspaces. PHX-086 adds read-only Models, Voice and
Settings. PHX-087 adds read-only Observability. The other four panels remain explicitly unconnected.
Backend integration is pending; no operational health, agent, task, permission,
memory, model or voice success is fabricated.

PHX-082 implements the agent directory and PHX-083 the read-only task view.
PHX-084 implements permission evidence/human intent; PHX-085 implements read-only
memory/file evidence. PHX-086 implements model/voice evidence and a non-editor
Settings overview. PHX-087 displays caller-supplied diagnostics; Phase 8 remains INCOMPLETE pending explicit phase-level acceptance policy.

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
PHX-106/107 (ADR-091, ADR-092) supersede this for the native app only: two Tauri
commands and three shell events, with App.tsx itself still free of network code.

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
- PHX-107: server state lives in `src/adapters/core.ts` (`PhoenixStore`, read through
  `useSyncExternalStore`); REST is the source of truth and shell events trigger refetches.

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

## PHX-085 Memory / Files workspace

PHX-085 replaces exactly Memory and Files / Documents with read-only, caller-owned
presentation ports. Default App has neither port and reports no source connected.
Connected means supplied evidence only, not a live backend or verified storage.
Unavailable/degraded sources hide all rows and stale details after full admission;
malformed snapshots are rejected whole with generic wording. No records are invented.

The user explicitly confirmed these exact DTO keys and caps. MemoryPort is readonly
{status, records}; FilePort is readonly {status, items}. Status is exactly connected,
unavailable or degraded. Neither port has callbacks. Memory records require id and
type (working, episodic, semantic, user, project, agent); optional keys are content,
sourceType, sourceRef, scope, confidence, importance, createdAt, updatedAt,
lastAccessedAt, expiresAt, embeddingRef. Files require id, kind, name; kind is exactly
source_document or generated_artifact. Optional keys are format, mediaType,
sourceType, sourceRef, contentHash, timestamp, sizeBytes. Raw objects, arbitrary
metadata, document content, binary bytes, actions and storage claims are rejected.

Both lists admit 0..64 unique opaque IDs. UTF16 bounds: IDs and embedding refs 128,
memory content 4000, source refs 512, scope/name 256, other text 128, UTC timestamps
64. Text must be meaningful; IDs and memory provenance/scope/embedding references
must already be trimmed, consistent with PHX-030. Content and file text formatting
are preserved. Confidence/importance are finite numbers 0..1; sizeBytes is a safe
nonnegative integer. UTC timestamps use valid calendar dates and Z or +00:00 only,
with no local clock or expiry inference. If creation and another memory timestamp
are both supplied, updated/accessed/expiry cannot precede creation; partial DTOs
never fabricate missing timestamps. Missing optional fields say Not reported;
numeric zero remains visible. These DTOs do not replace the Python domain schemas.

Admission uses exact own data descriptors and detached frozen primitive records,
arrays and snapshots. Unexpected string/symbol keys, missing fields, accessors,
sparse/extended arrays, duplicates, oversized/invalid values and reflection failures
reject the whole snapshot. Getters and arbitrary coercion are avoided. Same-realm
Proxy traps can execute: these checks are neither a sandbox nor authentication.
All external content, names, references and hashes render as escaped inert text,
with no HTML/Markdown execution, links, clipboard, logging or source dereferencing.

Case-insensitive local search covers all admitted strings and numeric evidence,
is capped at 80 UTF16 units, preserves source order, and provides match counts,
no-match feedback and Clear search. Native selection buttons choose only admitted
IDs. App-owned query/selection survives navigation, resets to first/none on source
loss or removed IDs, and explicitly labels selection outside the current filter.
Memory and Files state remains independent of each other and older workspaces.

Semantic headings, labelled inputs, native buttons, aria-pressed/aria-controls and
polite atomic counts/selection feedback preserve keyboard operation. Existing
visible focus, reduced motion, cinder/amber palette and system fonts remain. Wrapped
details and bounded scrollable lists stack at narrow widths; search does not steal
focus. All 14 navigation IDs, branding, skip link, landmarks, default/fallback,
focus wiring and PHX-081/082/083/084 logic are preserved. Eight other destinations
remain honest placeholders. The entire prior logic prefix and three original
foundation test bodies are checked byte for byte.

PUDDING owns memory, ROBIN document generation and VEGAPUNK indexing. Supplied
content/provenance does not prove authoritative memory, source truth, persistence
or retrieval. File metadata does not prove existence, readability, upload or
indexing; supplied hashes are not independently verified. Generated artifacts are
visibly distinct and may exist only in memory, never claimed saved to disk.
Existing PHX-031 secret-rejection policy remains the future trusted adapter's
responsibility; display validation cannot establish that supplied content is safe.
PermissionEngine remains the sole authorization authority; all ten PHX-041 evidence
combinations and PHX-084 intent semantics remain unchanged.

No live API/network/HTTP/WebSocket, Tauri IPC/plugin, filesystem/file-open, upload,
download/save/export/delete, memory mutation/forget, parsing/indexing/persistence,
polling/timers/background work, privileged action or new dependency was added.
Only GET /health exists; no memory/file route is added or called. Native sources,
configuration, CSP/capabilities, manifests/locks, backend and historical tests stay
unchanged. Ports need future trusted adapters; transport remains NOT IMPLEMENTED.
Manual native WebView2 acceptance remains PENDING: SSR/hooks/AST/build evidence
does not establish end-to-end GUI acceptance. Phase 8 is INCOMPLETE; PHX-086 and
PHX-087 remain TODO. PHX-086 was NOT STARTED.

Validation: three final npm runs each 115 passed; all prescribed backend/native,
package/security gates passed. See PHX-085 completion evidence for exact commands
and counts. Builds and receipts are outside the repository.

## PHX-086 Models / Voice / Settings

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

Validation: baseline npm 115 verified before edits; final three runs each 141
passed. All prescribed and impacted backend/native/package/security gates passed.
Exact commands/counts are in the PHX-086 receipt; all outputs are external.

## PHX-087 Observability / Developer — accepted 2026-10-12

Observability displays optional caller-supplied diagnostic evidence only. Default
App supplies no ObservabilityPort and reports No diagnostic source connected.
There is no telemetry collection, live transport, API/IPC, runtime/provider hook,
filesystem, clipboard/export, logging or persistence. GET /health stays unchanged.

The explicitly confirmed display DTO has exactly status
(connected/unavailable/degraded), metrics, logs and traces. Each list admits at
most 64 entries with IDs unique within that collection. IDs are meaningful,
already-trimmed strings up to 128 UTF-16 code units. IDs may repeat across lists.

Metrics require id/name/value, with optional unit/recordedAt. Names are exactly
task_step_latency, provider_model, ttft, tokens_per_sec, ram, vram, cpu, disk_io,
tool_success_failure, verification_result, recovery_attempts, permission_decisions.
Values retain their finite-number or meaningful-string type (strings <=256 units);
units <=32. No inferred unit, conversion, aggregation, sorting or freshness claim.
Logs require id/timestamp/severity/subsystem/message, with optional
correlationId/taskId/stepId/errorType. Severities are debug/info/warning/error/critical;
subsystem/errorType <=128 and message <=1000. Traces require
id/timestamp/kind/summary, with optional correlationId/taskId/stepId/actorId.
Kinds are decision/agent/tool/permission/failure/recovery/verification; summary
<=1000. Optional IDs use the same ID bound. Timestamps are real calendar UTC ISO
text ending Z/+00:00, <=64 units. Missing optional evidence is Not reported.

Whole-snapshot own-data validation rejects extra/missing/string/symbol/accessor
fields, malformed/sparse/extended arrays, duplicate IDs, wrong types/enums/bounds,
non-finite numbers, invalid UTC dates and reflective failures before any row
renders. Accepted primitive records, lists and root are detached and frozen.
Unavailable/degraded snapshots are fully validated, then all details suppressed.
Missing source differs from supplied empty data and never implies inactivity.
Source loss hides details and safely resets selected records. Generic failures
never echo rejected content. Proxy traps may run; validation is not a sandbox,
authentication boundary, source-truth check or redaction guarantee. A trusted
future producer must enforce redaction and source authentication.

Three independent local search/inspection states preserve source order, bound
search to 80 UTF-16 units, report exact counts and support Clear search/no-match
feedback. Native named inspection buttons change local display only. Cards stay
passive; supplied text stays escaped and inert, without links or dereferencing.
Headings/labels, polite counts/selection feedback, visible focus, reduced motion,
system fonts, cinder/amber, wrapping and bounded lists preserve keyboard use at
narrow widths and the unchanged 800x600 window. All 14 routes, Home default,
shell focus/landmarks and PHX-080–086 behavior remain preserved except necessary
composition and test-harness plumbing. PHOENIX alone orchestrates; PermissionEngine
alone authorizes. Diagnostics cannot prove execution, permissions or recovery.

Fresh authoritative main: frontend 141; Python 15,721 passed/16 skipped/one warning.
Three final frontend runs each passed 181 (40 added cases), with no failures,
cancellations or skips. Three final Python runs each passed 15,721 with the same
16 skip identities/types/reasons/source lines and existing Starlette/httpx warning.
Only the bound absolute checkout root differs in raw baseline skip locations.
All 60 required final command results, native builds, focused/historical guards,
package/dependency/source parity and INV-001–024 reviews passed. Earlier incomplete
launcher attempts and three CRLF baseline failures are preserved; none supplies
passing credit. Validation resumed under explicit approvals after reviewed job
containment and byte-verified main-checkout repair, without changing tests.
Exact commands, receipts, raw JUnit, comparisons and limitations are outside the
repository: C:\Users\shoai\.codex\validation\PHX-087-phase-review-20261012.

PHX-087 deterministic acceptance: DONE. Manual native WebView2 acceptance: PENDING.
Phase 8 remains INCOMPLETE; the phase-status decision is BLOCKED because final
phase-level acceptance policy is unresolved under execution authority section 9.
Builds and deterministic checks do not establish manual GUI or live integration.

## PHX-106/107 live desktop integration (ADR-091, ADR-092)

The native app starts the backend itself and is the only path to it. The Rust shell
generates the per-launch token, starts `python -m phoenix.api` from the repository's
`.venv` (demo/dev launch mode until PHX-092), reads the ready line, locks the data
directory against a second instance, and owns the process tree. The token never
reaches JavaScript.

Webview access is exactly:

- `invoke('api_request', { method, path, body })` — 20 allowlisted REST v1 routes (18 from
  PHX-106 plus PHX-108's read-only `GET /onboarding` and `GET /capabilities`)
  (docs/13), canonical UUID segments, listed query keys, JSON bodies up to 64 KiB;
  anything else is `route_not_allowed`;
- `invoke('backend_status')` — `starting`, `ready` or `degraded` with a closed code;
- `listen` on `phoenix:event` (content-free event envelopes from `WS /events`),
  `phoenix:events-status` and `phoenix:backend-status`.

The capability grants only `allow-api-request`, `allow-backend-status`,
`core:event:allow-listen` and `core:event:allow-unlisten`; production `connect-src` is
`ipc: http://ipc.localhost`. No plugin, file, shell or HTTP access exists in the webview.

`src/adapters/core.ts` maps REST and events to App's existing ports: Chat (POST /chat,
latest 64 transcript entries, long answers in ordered parts), Permissions (pending
requests, P1–P4 `approval_required` evidence, approve with `session` or deny; the
linked re-run is followed), Task Graph and Agent Network (live step and agent states
from events; objectives and dependencies are not published), Models and Observability
(recent events and timings, plus the backend's `GET /metrics` snapshot and recent
structured event logs since PHX-115, ADR-148). Memory, Files and Voice stay unconnected. `main.tsx`
renders `ConnectedApp` only inside the native shell; a plain browser keeps the honest
unconnected App. App.tsx is unchanged by PHX-107, so its captions still describe ports as
caller-supplied; PHX-108 adds only the header status line (below).

Verification labels (review M1): every completed mission is labelled from mission detail.
`verified` needs path `complex` and a last task state of `completed`; a `direct` answer is
`unverified · direct`; a `complex` mission that completed while its task `failed` is the
PHX-109 DIRECT fallback, `unverified (direct fallback)`, never "planned steps". Each PHOENIX
answer in Chat starts with a text badge, `[Verified by LEVI]` or `[Unverified · …]`, taken
from its own mission (answer IDs are `<mission_id>:answer`).

Connection bar (PHX-107, revised by PHX-108): above App it shows backend and live-stream
state, the document workspace (read-only; it is changed in Settings or Onboarding), the
mission path for this launch ("Automatic (PHOENIX decides)" omits `path` so PHX-105 triage
decides; direct and planned steps are explicit overrides; it starts from the saved default),
and cancel / run again for the tracked mission.

## PHX-108 Settings, onboarding and capability views (ADR-110 to ADR-114)

`src/adapters/SetupViews.tsx` renders four things from the store's backend state; every
action is a store method over an allowlisted route.

- **First-run screen.** When the backend is ready and `GET /onboarding` reports setup
  incomplete, ConnectedApp shows the onboarding steps instead of App, in the order they
  must be done: (1) the local model runtime (running / installed but not running / not
  found / degraded, named by the backend), (2) the tier models, required (`qwen3.5:4b`,
  `qwen3.5:9b`) or optional, with the backend's install command for a missing one once
  the runtime runs, (3) the data folder, free space and database, (4) the document folder
  (a folder form; each `workspace_*` code explained). "Re-check" reloads onboarding,
  models and settings (the backend registers any model whose runtime started since); the
  screen also re-checks every 3 s while shown. "Continue to PHOENIX" (or "Continue
  without finishing") hides it for this launch; nothing is persisted, so it returns next
  launch only if setup is still incomplete.
- **Onboarding view.** The same steps, any time, without the continue button.
- **Settings view (writable).** Document workspace folder; default mission path (saved
  with `PATCH /settings`, applied to this launch at once and to new launches); read-only
  model tiers; Voice and Memory placeholders driven by the `voice` and `memory` capability
  flags (PHX-114, PHX-110); retention; the PermissionEngine authority note.
- **Computer Control, Research and Media views.** Each states its capability flag(s)
  from `GET /capabilities` (`computer_control`; `web_research`; `media` and
  `document_generation`): "not yet enabled — arrives with PHX-11x" with the carrying
  agent, or, once a later ticket flips the flag, "enabled — ask PHOENIX in Home / Chat".
  Unknown status says so; no view offers an action.

App.tsx changes only after `export const diagnosticMetricNames` (its historical byte
checks are unchanged): `ViewPanel` and `App` accept optional `panels` for exactly these
five views and an optional header `statusLine`. Without them (a plain browser, every
existing test) App renders its honest defaults. ConnectedApp passes the panels and
"Connected to the local PHOENIX backend — live data".

Demo: `uv sync` in the repository, then `npm run tauri:dev --workspace @phoenix/desktop`
(or run the release build from `npm run tauri:build --workspace @phoenix/desktop`).
Overrides: `PHOENIX_BACKEND_ROOT`, `PHOENIX_BACKEND_PYTHON`, `PHOENIX_DATA_DIR`.
Backend stderr is written to `<data>/phoenix.desktop-backend.log`.
