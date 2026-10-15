# PHX-091 — FYP benchmark suite

Status: IN_PROGRESS
Phase: 9 — Validation & Release
Branch: feature/PHX-091-fyp-benchmark-suite
Starting main/base SHA: e9a5c393b2fd615b2664a0804a889f236d404877

## Objective

Provide the fixed, versioned FYP evaluation required by `docs/24_TEST_EVALUATION.md`:
- a benchmark task set;
- deterministic scoring;
- the docs/24 metrics;
- a runner that produces schema-versioned, reproducible reports.

It also runs the configurations that can run today. The others are reported explicitly as **NOT RUNNABLE** with a reason; nothing is estimated or fabricated.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md`, `/MASTER_SPEC.md`
- `docs/24_TEST_EVALUATION.md`: the FYP comparison and metrics.
- `docs/05_MODEL_STRATEGY.md` and `docs/06_HARDWARE_PROFILE.md`: tiers, BLAZE-6, storage policy.
- ADR-029 (PHX-016 runtime benchmark harness), which this ticket reuses unchanged.

## Findings that shape scope (2026-10-15)

- **Orchestration isn't model-backed.** `PhoenixOrchestrator`, `AizenPlanner` and `LeviVerifier` accept injected responder protocols only. `orchestrator.py` says "without provider/tool integration", and nothing in `phoenix/agents` uses `ModelProvider`. FYP configurations (2) and (3) therefore cannot run, and the PHX-090 matrix reports `end_to_end` as a GAP.
- **Colibri (the deep tier) is not installed.**
- **The MASTER_SPEC models were not installed.** Owner-approved setup: the existing Ollama store was copied to `D:\Phoenix\models`, user `OLLAMA_MODELS` points there, and qwen3.5:2b/4b/9b, qwen2.5-coder:7b and embeddinggemma:300m (16.64 GB) were pulled. Setup evidence is external, under `validation/PHX-091-model-setup-*`.

## Owner decisions (2026-10-15)

- **D1: scope.** Build the task set, scoring, metrics and runnable single-model baseline now. Declare configurations (2) PHOENIX multi-agent and (3) PHOENIX adaptive routing/deep tier as NOT RUNNABLE until a separate integration ticket wires real model-backed responders.
- **D2: models.** Use the MASTER_SPEC models on D: (see Findings).

## Requirements

1. **Task set.**
   - `benchmarks/fyp/tasks-v1.toml`: a fixed, versioned set (`phoenix-fyp`, version `1.0.0`) of harmless text cases grouped by capability.
   - Scoring is deterministic only: `exact` or `contains`, never an LLM judge. Unscored cases are allowed but reported as unscored.
   - Changing any case requires a version bump.
2. **Runner.**
   - `scripts/fyp_benchmark.py` loads the task set into the existing `BenchmarkSuite` and runs `BenchmarkRunner` against each configured Ollama model through the shared `ModelLifecycleManager`, with no harness changes.
   - It writes `fyp-benchmark-results.json` (schema-versioned) and a Markdown summary to the gitignored `.fyp-bench/`.
3. **Configurations.**
   - Baseline rows: one per model tier (T0 qwen3.5:2b, T1 qwen3.5:4b, T2 qwen3.5:9b, T2-code qwen2.5-coder:7b).
   - Rows (2) and (3) are always emitted as NOT RUNNABLE with their reason.
4. **Metrics.** Report every docs/24 metric:
   - Measured for the baseline: task success (score accuracy and success rate), TTFT/latency, tokens/s, RAM/VRAM, token usage.
   - NOT RUNNABLE, with reasons: routing accuracy, verification catch rate, recovery success, memory retrieval accuracy, research evidence quality, computer-control completion, Colibri disk I/O, user intervention count.
5. **Privacy.** Following ADR-029, reports contain hashes and lengths of outputs, never prompts' answers verbatim, and no private URLs or keys.
6. **Tests.** `tests/test_fyp_benchmark.py` uses a fake `ModelProvider` only (no network or models). It covers task-set loading and validation, runner output schema, NOT RUNNABLE rows, metric coverage and CLI behaviour. The file is mapped in `tests/matrix.toml`.

## Non-goals

- No changes to `phoenix/`, `alembic/`, `apps/desktop/` or `pyproject.toml`.
- No new runtime or test dependencies.
- No model-backed orchestration or integration; that belongs to a separate ticket.
- No LLM-as-judge scoring.
- No packaging or demos.
- Live benchmark results are evidence, not committed artifacts. The run is recorded externally, with a summary in the completion evidence.

## Verification

- `tests/test_fyp_benchmark.py` passes.
- The PHX-090 matrix passes with the new file mapped.
- A live baseline run on BLAZE-6 completes for all four tiers, with reports recorded as evidence.

## Acceptance criteria

- The task set is versioned and fixed, and the scoring is deterministic.
- One command produces schema-versioned results covering all three FYP configurations: the baseline measured, (2) and (3) explicitly NOT RUNNABLE.
- Every docs/24 metric is reported as measured or NOT RUNNABLE with a reason, never silently missing.
- No product code changes, and the existing gates pass.
- An ADR is recorded and the ledger updated.

## Completion evidence (partial; status IN_PROGRESS)

**Delivered:**
- New: `benchmarks/fyp/tasks-v1.toml`, `scripts/fyp_benchmark.py` and `tests/test_fyp_benchmark.py` (10 fake-provider tests).
- Updated: `tests/matrix.toml` (file mapped; the `mypy-scripts` gate now covers both scripts), `docs/24_TEST_EVALUATION.md`, ADR-079 and this ticket.
- `phoenix/`, `alembic/`, `apps/desktop/` and `pyproject.toml` are unchanged.

**Model setup** (owner-approved; evidence under `validation/PHX-091-model-setup-20261015-*`):
- The existing Ollama store (50 files, 22.2 GB) was copied to `D:\Phoenix\models` and verified file by file. The C: originals are untouched.
- User `OLLAMA_MODELS` was set, Ollama restarted, and all 7 existing local models stayed visible.
- qwen3.5:2b/4b/9b, qwen2.5-coder:7b and embeddinggemma:300m were pulled.

**Thinking finding** (ADR-079): through OllamaProvider, qwen3.5:2b returned empty answers on all 16 cases at a 128-token budget, with 0% accuracy. A direct API check confirmed the cause, Qwen3.5's default thinking. Defaults are therefore a 2048-token budget and a 180 s timeout. A follow-up ticket for provider thinking control has been drafted separately.

**Live baseline: NOT YET RUN.**
- A full run was started on 2026-10-15 at 01:17 IST and stopped deliberately at about 01:53, while still on T0. The owner unplugged the laptop overnight, and battery throttling plus capacity make a multi-hour GPU run invalid.
- No results are claimed; see `validation/PHX-091-fyp-baseline-20261015-011750/NOTE.md`.

**Remaining to close this ticket:** run `python scripts/fyp_benchmark.py` on AC power with no other model or GPU work, and record the results as evidence.
