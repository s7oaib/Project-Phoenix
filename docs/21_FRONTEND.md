# 21 — Frontend Architecture

## Stack

Tauri 2, React, TypeScript, Vite, Tailwind CSS, shadcn/ui, Zustand, TanStack Query, React Flow/XYFlow.

## Boundaries

Frontend may render state, send intents, request permission decisions, display tasks/events, and manage local UI preferences.

Frontend may not:
- call Ollama directly
- call Colibri directly
- execute privileged OS actions directly
- access SQLite directly
- implement permission policy

## State

- Server state: TanStack Query
- UI state: Zustand
- Live orchestration state: WebSocket events

## Suggested structure

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

Include error boundaries, reconnect UX, explicit loading/degraded states, and cancellation controls.
