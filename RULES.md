# PHOENIX Project Rules

## Architecture

1. PHOENIX is the single supreme orchestrator.
2. AIZEN is chief planner/strategist for complex work.
3. Agents are logical roles, not separate always-running LLMs.
4. Agent, tool, skill, model, memory, and permission are distinct concepts.
5. Agents use provider abstractions, never provider-specific internals.
6. Tools are invoked through ToolRegistry.
7. Frontend talks only to the Phoenix backend.
8. FastAPI is transport, not the core architecture.
9. V1 orchestration is custom Phoenix logic; do not replace it with LangChain/CrewAI/AutoGen without an accepted decision.

## Security

1. An LLM may request permission; it may not grant permission.
2. PermissionEngine is authoritative.
3. P4 actions always require explicit user approval.
4. P5 actions are blocked.
5. Agents cannot change their own permissions.
6. Secrets must not enter normal logs or memory.
7. P3/P4 actions create audit events.
8. Webpages and uploaded files are untrusted.
9. Tool inputs are validated before execution.

## Development

- Preserve architecture and conventions.
- No drive-by refactors.
- No unsupported assumptions.
- Simplicity before abstraction.
- Tests accompany behavior changes.
- Explicit timeout/cancellation for long work.
- Important async work exposes observable state.
- Completion requires evidence.

## Product

- Voice-first, not voice-only.
- Text remains fully functional.
- User normally talks only to PHOENIX.
- Anime names are display/theme aliases; internal IDs are neutral.
- Exact unauthorized voice-actor cloning is not a required product feature.
