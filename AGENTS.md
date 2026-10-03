# AGENTS.md — Coding-Agent Execution Contract

This file governs Codex and any other coding agent working in PHOENIX.

## Before changing code

Read:
1. `RULES.md`
2. `MENTAL_MODEL.md`
3. `INVARIANTS.md`
4. `MASTER_SPEC.md`
5. Relevant subsystem docs
6. Current feature ticket
7. Existing code/tests touching the change

Do not load all documents for every task; use the smallest relevant context after the required global files.

## Execution rules

- Preserve approved architecture.
- Do not silently reinterpret requirements.
- If ambiguous, stop and ask rather than inventing behavior.
- Make the smallest complete change.
- No unrelated refactors.
- No speculative abstractions.
- Reuse existing capabilities before adding dependencies.
- Every behavior change gets appropriate tests.
- Never bypass security/permissions to make a test pass.
- Never claim completion without evidence.
- Every changed line must trace to the ticket or a necessary supporting change.
- Meaningful architecture/security/product decisions must be recorded in `DECISIONS.md`.

## Completion protocol

1. Implement.
2. Run targeted tests.
3. Run impacted subsystem tests.
4. Check `INVARIANTS.md`.
5. Check permission/security implications.
6. Update `DECISIONS.md` if needed.
7. Update ticket status and `FEATURE_TICKETS.md`.
8. Report changed files, commands, results, decisions, assumptions, limitations, and acceptance status.

## Protected architecture

- PHOENIX remains supreme orchestrator.
- AIZEN plans but does not become supreme authority.
- PermissionEngine is deterministic.
- Frontend never calls Ollama/Colibri/privileged OS tools directly.
- Tools go through ToolRegistry.
- LEVI can gate completion.
- MAHORAGA recovery is bounded.
- KAIDO is degraded/fail-safe mode only.
