# PHX-090 — Automated test matrix

Status: DONE (CI workflow's first run happens on the PR)
Phase: 9 — Validation & Release
Branch: feature/PHX-090-automated-test-matrix
Starting main/base SHA: 1aa595d5d1a688d2877c98309818a931eb654b5b

## Objective

Turn PHOENIX's existing automated tests and required gates into one deterministic, repeatable test matrix. The matrix must:

1. Run every required gate with a single command, in a fixed order, with recorded results.
2. Map every existing test file to the software and security test categories defined in `docs/24_TEST_EVALUATION.md`, using a versioned manifest.
3. Report pass/fail per gate and per category, and list every category with no automated coverage as an explicit **gap**. A gap is never a pass.
4. Emit machine-readable results suitable as release evidence for PHX-094.

This is test infrastructure only. It adds no product behaviour.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md`, `/MASTER_SPEC.md`, `/ROADMAP.md`
- `docs/24_TEST_EVALUATION.md`: the category list this matrix must cover.
- `docs/25_DEPENDENCIES.md`: no new dependency without a decision.
- ADR-075, ADR-076, ADR-077 and the Phase 8 gate history: the existing required gate set.

## Dependencies

Phase 8 is COMPLETE (ADR-077), and PHX-090 is authorized.

## Current state (surveyed 2026-10-14 on `1aa595d`)

- **Python:** 50 `tests/test_*.py` files, roughly one per ticket; about 15,737 tests. No pytest markers or categories. `pyproject.toml` sets only `testpaths` and `--import-mode=importlib`.
- **Frontend:** a single `tests/workspace.test.mjs` (181 tests), run by root `npm test`.
- **Required gates:** they exist only as a command list in external Phase 8 evidence, run by hand:
  - npm test, typecheck, frontend build, `tauri:info`;
  - cargo check/test;
  - `uv sync --locked`, ruff check/format, mypy, `uv build`;
  - full pytest, plus 3 protected-source guards.
- **CI:** none (no `.github/`). The only PR check is CodeRabbit. This gap let the PHX-088 guard regression reach `main`.
- **Guard constraints:**
  - `tests/test_prompt_injection_defenses.py` pins every `phoenix/`, `alembic/` and `apps/desktop/` file to `origin/main`.
  - `tests/workspace.test.mjs` preserves earlier test blocks byte-for-byte.
  - Editing existing test files to add categories is therefore unsafe.

## Requirements (proposed)

1. **Single runner.** Add `scripts/test_matrix.py`, standard library only:
   - It runs the required gate set in a fixed order: environment checks first, then static checks, builds and test suites.
   - It uses explicit timeouts and per-gate logs.
   - It records the exact command, exit code, duration and source HEAD/clean status for each gate.
   - It exits non-zero if any required gate fails.
   - It never modifies tracked files, and it refuses to report success from a dirty tree unless `--allow-dirty` is passed. That flag is recorded in the results.
2. **Category manifest.** Add `tests/matrix.toml`, versioned and reviewed:
   - It maps each test file (and, where needed, a test-name pattern) to one or more `docs/24` categories. The software categories are unit, integration, API contract, migrations, model-provider contracts, routing, permissions, tools, recovery, voice states, frontend and end-to-end. The security categories are prompt injection, path traversal, command argument injection, permission bypass, secret leakage, malicious documents and forged tool results.
   - Existing test files are **not edited**.
3. **Coverage validation.** The runner fails if:
   - a test file is not in the manifest;
   - a manifest entry points to a missing file;
   - a category has no mapped tests and isn't declared as a gap with a reason.
4. **Results.** Write `test-matrix-results.json` (schema-versioned) and a human-readable summary to an output directory outside tracked paths. Include per-gate results, per-category status (PASS, FAIL or GAP), and test counts parsed from the JUnit/node reporter output.
5. **Self-tests.** Add `tests/test_test_matrix.py`. It covers manifest parsing, the unmapped-file, missing-file and undeclared-gap failures, result-schema output, and exit-code behaviour, using fake gate commands only.
6. **Docs.** Update `docs/24_TEST_EVALUATION.md` with how to run the matrix and how to read the results, record an ADR, and update `FEATURE_TICKETS.md`.

## Owner decisions (confirmed 2026-10-14)

- **D1: CI.** Local runner **plus** a GitHub Actions workflow that runs the matrix on `windows-latest` for pull requests to `main` and for pushes to `main`.
- **D2: Gaps.** Map existing tests and report empty categories as explicit **GAP** rows with reasons. Gap-filling tests are follow-up tickets.
- **D3: Categorization.** External manifest `tests/matrix.toml`. No existing test file is edited.
- **D4: Manual layer.** Native WebView2 acceptance and live-model or hardware runs appear as **MANUAL** rows that link to their evidence. They are never counted as an automated PASS.

## Non-goals

- No product, backend, frontend or native code changes. `phoenix/`, `alembic/` and `apps/desktop/` stay byte-identical.
- No new runtime or test dependencies without an ADR (standard library only for the runner).
- No FYP benchmark comparisons or performance metrics; those belong to PHX-091.
- No packaging or installers (PHX-092), and no demo scenarios (PHX-093).
- No weakening, skipping or rewriting of existing tests or guards.

## Expected files/components

- `scripts/test_matrix.py` (new)
- `tests/matrix.toml` (new)
- `tests/test_test_matrix.py` (new)
- `docs/24_TEST_EVALUATION.md`, `DECISIONS.md`, `FEATURE_TICKETS.md` (updated)
- `.github/workflows/test-matrix.yml` (only if D1 = b)

## Verification

- The runner's self-tests pass.
- Running the matrix on a clean tree reproduces the existing gate results: npm test 181/181, full pytest with 0 failures, cargo, ruff, mypy, builds and guards all pass.
- A deliberately unmapped dummy test file in a temporary copy makes the matrix fail.
- Every `docs/24` category shows PASS or a declared GAP, never silently missing.
- The existing guards still pass, and the protected paths are unchanged.

## Acceptance criteria

- One command runs all required gates and produces schema-versioned results plus a summary.
- 100% of test files are mapped. All 19 `docs/24` categories are reported, and gaps are explicit with reasons.
- The matrix fails on any gate failure, unmapped file, missing file or undeclared gap.
- No existing test, guard or product file is modified.
- The ADR is recorded and the ticket ledger updated.

## Completion evidence

**Changed files:**
- New: `scripts/test_matrix.py`, `tests/matrix.toml`, `tests/test_test_matrix.py`, `.github/workflows/test-matrix.yml`, and this ticket.
- Updated: `docs/24_TEST_EVALUATION.md`, `DECISIONS.md` (ADR-078), `FEATURE_TICKETS.md`, and `.gitignore` (adds `.test-matrix/`).
- `phoenix/`, `alembic/`, `apps/desktop/`, `pyproject.toml` and the existing tests are unchanged.

**Local run on 2026-10-14** (`python scripts/test_matrix.py --allow-dirty`, because the work was uncommitted; source `1aa595d`): **PASS**.
- All 13 gates passed: uv-sync, ruff check/format, mypy, mypy-matrix-runner, pytest (15,737 passed / 16 skipped / 0 failed), npm test (181/181), typecheck, frontend build, tauri-info, cargo check/test and uv build.
- The gates did not modify the working tree.
- A clean-tree run is the GitHub Actions run on the PR.

**Self-tests:** 16 pass in `tests/test_test_matrix.py`. While writing them I found and fixed a vacuous-PASS bug: a category with zero mapped files now reports UNMAPPED, which fails the run.

**Category coverage (51 test files + 1 Node file):**

| Category | Files | Category | Files |
| --- | --- | --- | --- |
| unit | 52 | prompt_injection | 15 |
| integration | 20 | path_traversal | 4 |
| api_contract | 2 | command_argument_injection | 1 |
| migrations | 5 | permission_bypass | 14 |
| model_provider_contracts | 4 | secret_leakage | 12 |
| routing | 1 | malicious_documents | 1 |
| permissions | 12 | forged_tool_results | 14 |
| tools | 11 | **end_to_end** | **GAP** |
| recovery | 4 | | |
| voice_states | 6 | | |
| frontend | 1 | | |

**Classification method.**
- A read-only classification pass cited real test names for each assignment.
- Owner-side review was conservative:
  - repository credential-signature scans don't count as `secret_leakage`;
  - UI approval controls don't count as `permission_bypass`;
  - name-only `integration` and `forged_tool_results` claims were checked against the test bodies.

**Thin categories (one file each):** routing, frontend, command_argument_injection and malicious_documents. These are candidates for follow-up tickets, alongside the end_to_end gap.

**Known limitations:**
- The category map is a reviewed judgement, not proof of exhaustive coverage.
- `tauri:build` and native acceptance remain manual rows.
- The first CI run on GitHub's `windows-latest` hasn't happened yet; it runs on the PR.
