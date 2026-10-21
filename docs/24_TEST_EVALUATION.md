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
  `PhoenixRuntime.run` with a default and a `think=False` Ollama provider and
  the runtime tier table. The default `--path triage` passes no path, so
  PHOENIX triages each request (PHX-105) and the decided path and triage
  outcome are recorded per mission; `--path complex` forces the full pipeline
  as a stress test and `--path direct` forces one reply. It uses its own mission loop
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
  - Live results (2026-10-18, BLAZE-6, evidence in the PHX-091 ticket), with
    configuration (1)'s best at 93% / 0.3 s (4B, thinking off) and 100% / 7.2 s
    (4B, thinking on) for comparison:

    | Path | Accuracy | Success | Latency median | Main failures |
    | --- | --- | --- | --- | --- |
    | forced COMPLEX | 43% | 38% | 122 s | schema, truncated |
    | triage (default) | 64% | 69% | 1.1 s | provider_unavailable, verification_failed |

    On these short tasks with 6 GB of VRAM, multi-agent is slower and less
    accurate than the best single small model. Its value is that no wrong
    answer is ever reported as verified: every forced-COMPLEX answer that
    passed LEVI was correct, and the one wrong triage answer
    (`reasoning-arithmetic-chain`) came from the DIRECT path, which has no LEVI
    check. Permissioned tool use is the other gain. The next levers are
    constrained JSON decoding and MAHORAGA retries (PHX-109).
  - **Verified and unverified results are reported separately.** The headline
    success excludes unverified DIRECT fallbacks; completions including them
    are labelled as such.
    - A DIRECT fallback is PHX-109 MAHORAGA answering on the DIRECT path after
      AIZEN's plan fails twice. The decided path stays COMPLEX, the task ends
      `failed` and the answer is unverified.
    - The per-path breakdown labels DIRECT missions unverified by design. It
      splits COMPLEX missions into LEVI-verified completions, fallbacks and
      failures.
    - `recovery_success` is MEASURED only when the traces carry PHX-109
      recoveries.
    - The single runs above predate PHX-109, so they have no fallbacks: their
      success is the same with or without them.
  - 3-run triage evidence (2026-10-18, 48 missions each; re-rendered from the
    stored trials with `--from-results`). The with-MAHORAGA run used the local
    integration branch `ca85fad` (PHX-109, PR #84, not on main):

    | | with MAHORAGA | without MAHORAGA |
    | --- | --- | --- |
    | Completed (incl. unverified fallbacks) | 40 (83%) | 29 (60%) |
    | DIRECT fallbacks | 3 | 0 |
    | Success excl. fallbacks | 77% | 60% |
    | Accuracy / excl. fallbacks | 64% / 60% | 50% / 50% |
    | DIRECT missions (unverified) | 34, 22/28 scored pass | 28, 20/24 |
    | COMPLEX: LEVI-verified / fallback / failed | 3 / 3 / 8 of 14 | 1 / 0 / 19 of 20 |

    Caveat: those traces predate `MissionTrace.recoveries`, so fallbacks were
    detected from the task states (a completed mission whose COMPLEX task
    ended `failed`).
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

### Routing benchmark (PHX-105)

`python scripts/routing_benchmark.py` measures the `routing_accuracy` metric:
PHOENIX's triage responder alone (`TRIAGE_SYSTEM`, `phoenix-prompts-v4`, one
thinking-off generation per case on `qwen3.5:4b` by default) against the fixed
routing set `benchmarks/routing/routing-v1.toml` (`phoenix-routing` 1.0.0, 60
harmless requests labelled `direct` or `complex`, each with a one-line
rationale, in six categories: factual, rewrite_format, workspace_files,
multi_step, ambiguous and adversarial).

- It writes `.routing-bench/routing-benchmark-results.json` (schema-versioned)
  and a Markdown summary: accuracy, a confusion matrix, per-category accuracy,
  latency (median, p95, min, max) and the fallback count with reasons.
- A triage failure counts as the product's fallback, `complex`; accuracy over
  the cases triage itself decided is reported separately.
- The set is versioned and its sha256 is pinned in `tests/test_routing_benchmark.py`;
  changing any case requires a new version and file.
- `--dry-run` validates the set without models. Like the FYP runner, a live
  run records Ollama's placement and refuses CPU-only runs unless `--allow-cpu`.
- `scripts/fyp_benchmark.py` keeps reporting `routing_accuracy` as NOT RUNNABLE
  in its baseline run and points to this script.

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
- `tests/test_web_research.py` (PHX-111) covers web research offline: the
  DuckDuckGo provider and page reader over `httpx.MockTransport` fixtures,
  the P1 research tool, LEVI's deterministic citation checks and full research
  missions (granted, waiting for permission, injected pages, fabricated
  citations, rate limiting). Its owner-run live counterpart,
  `scripts/research_mission_smoke.py`, is not a CI gate.
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
