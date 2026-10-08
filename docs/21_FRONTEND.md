# 21 — Frontend Architecture

## Stack

Implemented through PHX-080: Tauri 2, React 19, TypeScript 6, Vite 8 and CSS.
Tailwind CSS, shadcn/ui, Zustand, TanStack Query and React Flow/XYFlow remain
architecture choices for separately justified later tickets; PHX-080 adds no dependency.

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

Home previews future workspace areas; the other thirteen panels identify their
purpose and explicitly say they are not connected. Backend integration is pending.
No health, agent, task, permission, memory, model or voice success is fabricated.
There is no chat submission or feature action in this shell.

PHX-081 owns chat/voice, PHX-082 agent network, PHX-083 task graph, PHX-084 permission
center, PHX-085 memory/files, PHX-086 models/voice/settings and PHX-087 observability.
Their data, transport and action bindings remain deferred; Phase 8 is incomplete.

## Boundaries

Frontend may render state, send intents, request permission decisions, display tasks/events, and manage local UI preferences.

Those are target boundaries. PHX-080 only selects local views: no HTTP/WebSocket,
backend client, provider, database, tool, permission decision or Tauri IPC/plugin
invocation. Existing production/development CSP, empty permission grants, entry
point, native Rust and dependency manifests remain unchanged.

Frontend may not:
- call Ollama directly
- call Colibri directly
- execute privileged OS actions directly
- access SQLite directly
- implement permission policy

## State

- Current PHX-080 UI selection: React useState/useRef; useEffect moves focus after
  a committed navigation change without stealing initial focus.
- Planned server state: TanStack Query, under a later authorized transport ticket.
- Planned broader UI state: Zustand; no router/history or browser-storage persistence
  is introduced by PHX-080.
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
Later connected features need error boundaries, reconnect UX, explicit
loading/degraded states and cancellation controls.

Deterministic Node React SSR, pure resolver and TypeScript AST contracts cover
navigation, focus wiring and security; the three PHX-003 workspace/CSP contracts
remain intact. Historical PHX-055, PHX-052 and PHX-051 guards exclude only App.tsx
and styles.css while retaining all other desktop/backend protection.
Static Tauri builds pass; manual WebView2 mouse/keyboard acceptance remains pending.
