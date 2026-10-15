# 24 — Testing & FYP Evaluation

## Software tests

- unit
- integration
- API contract
- migrations
- model-provider contracts
- routing
- permissions
- tools
- recovery
- voice states
- frontend
- end-to-end

## Security tests

- prompt injection
- path traversal
- command argument injection
- permission bypass
- secret leakage
- malicious documents
- forged tool results

## FYP comparison

Compare:
1. single local model baseline
2. Phoenix multi-agent orchestration
3. Phoenix + adaptive model routing/deep tier

## Metrics

- task success
- routing accuracy
- verification catch rate
- recovery success
- memory retrieval accuracy
- research evidence quality
- computer-control completion
- TTFT/latency
- RAM/VRAM
- token usage
- Colibri disk I/O
- user intervention count

Use a fixed, versioned benchmark task set.

### FYP benchmark suite (PHX-091)

`python scripts/fyp_benchmark.py` runs the fixed task set
`benchmarks/fyp/tasks-v1.toml` (`phoenix-fyp` 1.0.0) against each single-model
baseline tier through the PHX-016 harness. It writes
`.fyp-bench/fyp-benchmark-results.json` (schema-versioned) and a Markdown
summary.

- Configurations (2) and (3) are reported as NOT RUNNABLE until model-backed
  PHOENIX responders exist.
- Every metric above is reported either as measured or as NOT RUNNABLE with a
  reason (ADR-079).
- Changing any case requires a new task-set version.
- Use `--dry-run` to validate the task set without models.

## Automated test matrix (PHX-090)

`python scripts/test_matrix.py` runs every required gate declared in
`tests/matrix.toml`: dependency sync, ruff, mypy, full pytest, the frontend Node
tests, typecheck, frontend build, Tauri info, cargo check/test and the Python
package build. It writes `.test-matrix/test-matrix-results.json`
(schema-versioned) and `.test-matrix/test-matrix-summary.md`, with one log per
gate under `.test-matrix/logs/`.

- Every `tests/test_*.py` and `tests/*.test.mjs` file must be mapped in
  `tests/matrix.toml` to one or more of the categories above. Use snake_case ids,
  for example `api_contract` or `path_traversal`.
- A category with no automated tests must be declared as a `[[gap]]` with a
  reason. Gaps are reported as GAP and never count as PASS.
- Native WebView2 acceptance and live model or device runs are `[[manual]]` rows
  with evidence references. They are reported as MANUAL, never as automated PASS.
- The run fails on any gate failure or timeout, missing tool, unmapped or missing
  test file, undeclared gap, or failed category, or if a gate changes the working
  tree. It requires a clean tree unless `--allow-dirty` is passed, which is
  recorded in the results.
- `--validate-only` checks the manifest alone. `--gate <id>` runs a subset and
  reports PARTIAL.
- The pytest suite includes `tests/test_test_matrix.py`, so adding a test file
  without mapping it fails the normal test run.

The `test-matrix` GitHub Actions workflow runs the same command on
`windows-latest` for pull requests to `main` and for pushes to `main`, and uploads
`.test-matrix/` as an artifact. `tauri:build` (the release executable) is not part
of the matrix; the native release build and its acceptance remain a manual,
evidence-backed step.
