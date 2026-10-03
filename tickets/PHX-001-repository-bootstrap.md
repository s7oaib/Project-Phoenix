# PHX-001 — Repository Bootstrap

## Status
TODO

## Phase
Phase 0 — Foundation

## Objective
Create the PHOENIX repository structure and baseline developer tooling without implementing product features.

## Authoritative documents
- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `/docs/01_ARCHITECTURE.md`
- `/docs/21_FRONTEND.md`

## Requirements

- Create the approved top-level backend/frontend structure.
- Establish Python 3.11 project environment metadata.
- Establish Tauri/React/TypeScript workspace metadata.
- Add test directories and basic tooling configuration.
- Add `.gitignore` and safe environment example files.
- Do not add product behavior.
- Do not add orchestration frameworks.

## Non-goals

- No Ollama integration.
- No FastAPI routes beyond what is strictly required by later tickets.
- No UI feature implementation.
- No agent logic.
- No database implementation.

## Verification

- Repository structure matches architecture.
- Python project metadata parses/installs.
- Frontend package metadata resolves.
- No secrets are committed.
- `INVARIANTS.md` remains satisfied.

## Acceptance criteria

- Clean bootstrap is ready for PHX-002 and PHX-003.
- No speculative feature code exists.
- Changed files and verification commands are reported.
