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
summary. `--think {default,on,off}` sets the Ollama thinking control (PHX-017,
ADR-080) and is recorded in the results; `default` keeps each model's own
behaviour.

- Configuration (2) runs separately with `--config phoenix-multi-agent`
  (`scripts/fyp_multi_agent.py`). It sends each v1 case through
  `PhoenixRuntime.run` (default path COMPLEX; `--path direct|complex|triage`,
  where `triage` is a no-op until PHX-105) with a default and a `think=False`
  Ollama provider and the runtime tier table. It uses its own mission loop
  rather than BenchmarkRunner (single-lease deadlock, PHX-029). It scores the
  final answer with the same exact/contains rules and reports accuracy, outcome
  counts, mission latency, generations, tokens, LEVI verdicts, failure codes,
  per-category accuracy and the prompt set version to
  `.fyp-bench/fyp-multi-agent-results.json` plus a summary.
  - `--compare <config1-results.json>` (repeatable) adds a side-by-side table;
    `--from-results` re-renders it without models.
  - Defaults: 0 warm-up and 1 measured run. A run takes about 20–35 minutes
    (16 missions of 1–2 minutes).
  - Configuration (2) mixes models by role and thinking varies by role; the
    report states this.
  - Verification catch rate stays NOT RUNNABLE (needs fault-injected cases);
    routing accuracy belongs to PHX-105.
- Configuration (3) is reported as NOT RUNNABLE: the runtime has no adaptive
  routing or Colibri deep-tier path.
- Every metric above is reported either as measured or as NOT RUNNABLE with a
  reason (ADR-079).
- Changing any case requires a new task-set version.
- Use `--dry-run` to validate the task set without models.
- Before and after each tier, the runner records where Ollama placed the model
  (GPU, partial or CPU, from `/api/ps`). It refuses CPU-only runs unless
  `--allow-cpu` is passed, because Ollama falls back to CPU silently when the
  GPU is unavailable.
- With thinking on, the harness `tokens_per_second` is inflated and TTFT
  includes reasoning time. The harness counts hidden thinking tokens but
  measures only the visible-text interval. Report thinking-on throughput as
  output tokens ÷ total latency instead (ADR-079).

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
- `end_to_end` is covered by `tests/test_phoenix_mission_e2e.py` (PHX-046,
  ADR-082): full PHOENIX missions with permissioned read-only filesystem steps,
  real orchestration, permission, tool and audit components, and a scripted
  model. Its `[[gap]]` is removed. The owner-run live counterpart,
  `scripts/phoenix_mission_smoke.py`, is not a CI gate.
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
