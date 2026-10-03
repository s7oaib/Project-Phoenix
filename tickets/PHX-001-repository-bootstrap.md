# PHX-001 — Repository Bootstrap

## Status
DONE

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

## Completion evidence — 2026-10-04

- Approved `phoenix/` subsystem package markers, `apps/desktop` npm workspace and
  `tests/` exist; no application behavior or runtime dependency was introduced.
- Python 3.11 packaging uses Hatchling and uv; development dependencies and npm
  workspace metadata are locked. The verification commands below also provide
  the reproducible developer setup; uv creates an ignored `.venv`.
- `uv lock`, `uv sync --locked`, `uv build`: PASS.
- `uv run --locked pytest`: PASS, 11 bootstrap checks.
- `uv run --locked ruff check .`, `uv run --locked ruff format --check .`,
  `uv run --locked mypy`: PASS (12 Python source files).
- `npm install --package-lock-only --ignore-scripts`, `npm ci --ignore-scripts`,
  `npm test`: PASS, one workspace check; zero npm audit vulnerabilities reported.
- SHA-256 comparison preserves all pre-existing Markdown files except the approved
  decision log and this ticket/index status updates.
- `git check-ignore` verifies environment, virtualenv, Node/Tauri output, caches,
  local agent state, model data and database patterns; examples remain visible.
  Git-visible files contain no generated runtime artifacts or credential signatures.
- Relevant invariants INV-001–007, INV-009–018 and INV-020–024 are preserved by
  absence of product behavior; INV-008 is satisfied by the passing validation evidence.
  INV-019 introduces no runtime obligation because there are no application operations.
- ADR-016 and ADR-017 record packaging/tooling and npm workspace decisions.
- Initial verification used a separate local Git repository without a remote,
  commit or push; official branch integration is recorded below.
- `SECURITY.md` does not exist in this baseline; `/docs/09_SECURITY.md` was reviewed.
- PHX-002 and PHX-003 remain TODO. Desktop source/configuration and backend behavior
  belong to those tickets; no desktop launch/build or TypeScript configuration exists yet.

## Official branch integration — 2026-10-04

- Transferred only the PHX-001 bootstrap files and three approved document updates
  into a fresh clone at `D:\Project-Phoenix`, on
  `feature/PHX-001-repository-bootstrap`, preserving official Git history.
- Removed `DEVELOPMENT.md` from the accidental workspace; it was never added to
  the official clone. No replacement Markdown document was created.
- Node metadata was not pinned to Node 25 and remains unchanged. PHX-003 owns
  the actual desktop toolchain requirements. ADR-016 and ADR-017 remain accepted.
- All listed Python/npm verification commands were rerun successfully in the
  official clone, with 11 Python tests and one npm workspace test passing.
- Diff, ignore patterns and credential-signature checks passed; generated
  environments, caches, build output and local state are excluded from integration.
