# 01 — Architecture

## Layers

```text
Desktop UI
  Tauri 2 + React/TypeScript
        |
Local API / WebSocket
  FastAPI
        |
Application Services
        |
Phoenix Core
  Orchestrator
  Agent Registry/Router
  Task Graph
  Verification/Recovery
        |
Subsystems
  Tools
  Permissions
  Memory/RAG
  Voice
  Model Providers
  Persistence
        |
Capabilities
  Ollama / Colibri
  OS / Browser
  Search
  Spotify / YouTube
```

## Dependency direction

Outer adapters may depend on core contracts. Core contracts must not depend on Tauri, FastAPI, Ollama, Colibri, or a specific vector engine.

## Suggested backend package boundaries

```text
phoenix/
  core/
  agents/
  models/
  tools/
  memory/
  permissions/
  voice/
  recovery/
  persistence/
  api/
```

## Constraints

- Frontend never invokes privileged tools directly.
- FastAPI routes call application services.
- Tool execution returns structured results.
- Provider failures are explicit.
- Optional adapters must be replaceable.
