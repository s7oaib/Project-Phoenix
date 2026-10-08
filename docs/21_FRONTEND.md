# 21 — Frontend Architecture

## Stack

Implemented through PHX-082: Tauri 2, React 19, TypeScript 6, Vite 8 and CSS.
Tailwind CSS, shadcn/ui, Zustand, TanStack Query and React Flow/XYFlow remain
architecture choices for separately justified later tickets; PHX-080/081/082 add no dependency.

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
PHX-082 replaces Agent Network with the canonical directory below. The other twelve
panels identify their purpose and explicitly remain unconnected.
Backend integration is pending; no operational health, agent, task, permission,
memory, model or voice success is fabricated.

PHX-082 implements the agent directory; PHX-083 owns task graph, PHX-084 permission
center, PHX-085 memory/files, PHX-086 models/voice/settings and PHX-087 observability.
PHX-083 through PHX-087 remain TODO; Phase 8 is INCOMPLETE.

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

## Boundaries

Frontend may render state, send intents, request permission decisions, display tasks/events, and manage local UI preferences.

Those are target boundaries. PHX-080/081/082 only select views, draft/search locally, display
supplied plain-text snapshots and optionally submit to a borrowed callback. There is
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

- Current PHX-080/081/082 UI selection, search and local drafts: React useState/useRef;
  useEffect moves focus after a committed navigation change without stealing initial focus.
- Planned server state: TanStack Query, under a later authorized transport ticket.
- Planned broader UI state: Zustand; no router/history or browser-storage persistence
  is introduced by PHX-080/081/082.
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
passes 45 tests three consecutive times; the three PHX-003 workspace/CSP test bodies
remain unchanged. Historical PHX-055, PHX-052 and PHX-051 guards exclude only App.tsx
and styles.css while retaining all other desktop/backend protection.
Static Tauri builds pass; manual WebView2 mouse/keyboard acceptance remains pending.

LIVE CHAT ACCEPTANCE: NOT IMPLEMENTED — backend POST /chat transport is not yet
present. UI defaults disconnected; fake-port tests are not live integration.
LIVE VOICE ACCEPTANCE: NOT IMPLEMENTED — microphone capture, wake, STT/TTS and
backend voice binding are not provided in this frontend ticket.
MANUAL DESKTOP CHAT/VOICE ACCEPTANCE: PENDING — no real GUI/WebView2 end-to-end
interaction performed; deterministic SSR/AST/build evidence is not a substitute.
