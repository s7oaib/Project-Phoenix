# PHX-091 — FYP benchmark suite

Status: DONE (configuration 1 baseline); continuation IN_PROGRESS (configuration 2 runner built, live GPU run pending); configuration 3 NOT RUNNABLE
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

## Completion evidence

**Delivered:**
- New: `benchmarks/fyp/tasks-v1.toml`, `scripts/fyp_benchmark.py` and `tests/test_fyp_benchmark.py` (10 fake-provider tests).
- Updated: `tests/matrix.toml` (file mapped; the `mypy-scripts` gate now covers both scripts), `docs/24_TEST_EVALUATION.md`, ADR-079 and this ticket.
- `phoenix/`, `alembic/`, `apps/desktop/` and `pyproject.toml` are unchanged.

**Model setup** (owner-approved; evidence under `validation/PHX-091-model-setup-20261015-*`):
- The existing Ollama store (50 files, 22.2 GB) was copied to `D:\Phoenix\models` and verified file by file. The C: originals are untouched.
- User `OLLAMA_MODELS` was set, Ollama restarted, and all 7 existing local models stayed visible.
- qwen3.5:2b/4b/9b, qwen2.5-coder:7b and embeddinggemma:300m were pulled.
- **Correction (2026-10-15):** the pulls actually landed in the default C: store
  (`%USERPROFILE%\.ollama\models`, then 37.3 GB), not on D:.
  - **Cause:** the Ollama desktop app ignores the `OLLAMA_MODELS` environment
    variable and uses its own model-location setting.
  - **Why it went unnoticed:** the setup check only confirmed that the 7
    pre-existing models were visible, and those exist in both stores.
  - **Fix:** the missing files were copied C: → D: (copy-only, verified with 0
    size mismatches), so `D:\Phoenix\models` now holds every model.
  - **Remaining:** the desktop app still uses C: until its model location is
    changed in its settings. The C: copies are untouched and can be deleted
    after that.

**Thinking finding** (ADR-079): through OllamaProvider, qwen3.5:2b returned empty answers on all 16 cases at a 128-token budget, with 0% accuracy. A direct API check confirmed the cause, Qwen3.5's default thinking. Defaults are therefore a 2048-token budget and a 180 s timeout. A follow-up ticket for provider thinking control has been drafted separately.

**Live baseline: NOT YET RUN.**
- A full run was started on 2026-10-15 at 01:17 IST and stopped deliberately at about 01:53, while still on T0. The owner unplugged the laptop overnight, and battery throttling plus capacity make a multi-hour GPU run invalid.
- No results are claimed; see `validation/PHX-091-fyp-baseline-20261015-011750/NOTE.md`.

**Thinking-off row: RUN 2026-10-15, then INVALIDATED (CPU-only, not BLAZE-6 evidence).**

> **Correction (2026-10-15):** this run used no GPU.
> - **Windows:** the RTX 3050 was disconnected from the bus (Device Manager Code 45, `CM_PROB_PHANTOM`; only the Intel iGPU was present).
> - **Ollama:** at its 12:00 IST start it reported `inference compute ... library=cpu` and `total_vram="0 B"`. Last night at 00:44 and 00:50 it reported CUDA on the RTX 3050 with 6.0 GiB.
> - **`nvidia-smi`:** its "insufficient permissions" error was a symptom of the missing device, not a permissions problem.
>
> **Validity:**
> - All timings below (TTFT, latency, tok/s) are CPU-only and invalid for BLAZE-6.
> - The accuracy column is kept for reference only. Placement affects speed, and is not expected to affect greedy answers, but this is unverified.
>
> **Guard:** the runner now probes Ollama placement (`/api/ps`, `size_vram` vs `size`) before and after each tier. It refuses CPU-only placement unless `--allow-cpu` is passed, so this cannot recur silently.
>
> **Re-run:** both rows must be re-run with the GPU enabled.

Original record (owner request, after PHX-017):
- `scripts/fyp_benchmark.py` gained `--think {default,on,off}` (default keeps
  today's behaviour), recorded as `provider_options` in the results and as a
  `Thinking:` line in the summary. Five more fake-provider tests (15 total).
- Command `python scripts/fyp_benchmark.py --think off`, on AC power with no
  other GPU work, on PR #66 head `8547d2a` plus this flag. 12:31–12:48 IST,
  exit 0. Same v1 task set, 1 warm-up and 3 measured runs, temperature 0,
  2048-token cap, 180 s timeout. Evidence in
  `validation/PHX-091-fyp-baseline-think-off-20261015-123111` (results, summary,
  log, provenance, exact flag diff).

  | Tier | Model | Accuracy | Success | TTFT median ms | Latency median ms | tok/s median |
  | --- | --- | --- | --- | --- | --- | --- |
  | T0 | qwen3.5:2b | 71% | 100% | 127 | 572 | 23 |
  | T1 | qwen3.5:4b | 93% | 100% | 193 | 930 | 15 |
  | T2 | qwen3.5:9b | 71% | 100% | 267 | 1472 | 9 |
  | T2-code | qwen2.5-coder:7b | 86% | 100% | 143 | 1128 | 11 |

- Failures are deterministic (3/3 each): `reasoning-arithmetic-chain` on all
  tiers; 9b also `instruction-word-count`, `untrusted-uppercase-data` and
  `untrusted-count-data`; 2b also `instruction-word-count`,
  `reasoning-schedule` and `untrusted-count-data`; coder also
  `untrusted-count-data`.
- VRAM is unknown for this run. `nvidia-smi` refused with "insufficient
  permissions", which the correction above explains: the GPU was absent, so the
  model ran fully on CPU.

**GPU baselines (2026-10-15): the BLAZE-6 evidence for configuration (1).**

These rows supersede the CPU-only row above.

Setup common to all three runs:
- **Code:** branch `fix/PHX-091-gpu-evidence`. Runs 1 and 2 used `73cbf5b`; run 3 used `32cd40f`, which changes docs only, so the runner is identical.
- **Task set:** the v1 task set, sha256 `2958f14fe8d6e021…`.
- **Trials:** 1 warm-up and 3 measured runs per case, at temperature 0, with a 2048-token cap and a 180 s timeout.
- **Runtime:** headless `ollama serve` (Ollama 0.40.4) with `OLLAMA_CONTEXT_LENGTH=16384` and `OLLAMA_MODELS=D:\Phoenix\models`, with the desktop app closed. The RTX 3050 6 GB (driver 610.62) was on CUDA.
- **Power:** AC throughout.
- **Placement guard:** on, with no `--allow-cpu`. Placement was identical before and after every tier.

Evidence directories, each holding the results JSON, summary, run log and provenance:

| Run | Command | Local time (IST) | Directory under `validation/` |
| --- | --- | --- | --- |
| 1 | `--think off` | 15:48–15:57 | `PHX-091-fyp-gpu-think-off-20261015-154843` |
| 2 | default | 15:57–17:32 | `PHX-091-fyp-gpu-think-default-20261015-155753` |
| 3 | default, `--model T1=qwen3.5:4b --model T2=qwen3.5:9b` | 17:34–18:48 | `PHX-091-fyp-gpu-think-default-rerun-T1T2-20261015-173438` |

Run 3 exists because of an overlap in run 2.
- **What overlapped:** an unrelated full test matrix ran 16:18:39–16:30:46 IST (cargo and pytest, CPU-heavy). It covered most of run 2's T1 window and the first 2.5 minutes of its T2 window.
- **What run 3 changed:** it re-ran T1 and T2 with nothing else running. Its rows replace run 2's T1 and T2 rows.
- **Result:** run 3 reproduced run 2's accuracy and failures exactly. Latency moved by 6–10%.
- **What run 2 still provides:** its T0 and T2-code rows, which ran clean.

| Thinking | Tier | Model | Placement | Accuracy | Success | Latency median ms | Output tok/s median* | Source |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| off | T0 | qwen3.5:2b | GPU 100% | 79% | 100% | 218 | 68 | run 1 |
| off | T1 | qwen3.5:4b | GPU 100% | 93% | 100% | 326 | 50 | run 1 |
| off | T2 | qwen3.5:9b | partial 53% | 79% | 100% | 1251 | 10 | run 1 |
| off | T2-code | qwen2.5-coder:7b | partial 73% | 86% | 100% | 537 | 25 | run 1 |
| default (on) | T0 | qwen3.5:2b | GPU 100% | 86% | 100% | 6475 | ~49 | run 2 |
| default (on) | T1 | qwen3.5:4b | GPU 100% | **100%** | 100% | 7217 | ~40 | run 3 |
| default (on) | T2 | qwen3.5:9b | partial 53% | 93% | 94% | 34666 | ~10 | run 3 |
| default (on) | T2-code | qwen2.5-coder:7b | partial 73% | 86% | 100% | 515 | 25 | run 2 |

\* **How the throughput column was measured:**
- **Thinking off:** the harness value.
- **Thinking on, Qwen3.5:** the harness value is inflated (377–3570 tok/s). Its PHX-016 formula divides *all* output tokens, including hidden thinking tokens, by the interval from the first *visible* text token to completion. The `~` values are output tokens ÷ total latency, computed from the results JSON.
- **TTFT:** with thinking on it measures time to the first visible answer token, so it includes the whole reasoning time (median 7.0 s for T1, 34.6 s for T2).
- **Harness:** it is frozen (ADR-029) and was not changed. The limitation is recorded in ADR-079.

Observations (deterministic: every listed failure occurred in 3 of 3 measured runs):
- **Thinking off failures:**

  | Tier | Failed cases |
  | --- | --- |
  | T0 | `instruction-word-count`, `reasoning-schedule`, `untrusted-count-data` |
  | T1 | `reasoning-arithmetic-chain` |
  | T2 | `instruction-word-count`, `reasoning-arithmetic-chain`, `untrusted-count-data` |
  | T2-code | `reasoning-arithmetic-chain`, `untrusted-count-data` |

- **Thinking on:**
  - T1 failed no case, in run 2 or run 3.
  - T0 failed `code-function-name` and `structured-json-from-text`.
  - T2's only failure was `untrusted-uppercase-data`: it hit the 180 s timeout (`deadline_exceeded`) in both runs 2 and 3.
  - T2-code results matched thinking off.
- **Thinking off vs on, Qwen3.5:**
  - Accuracy: thinking on raises it by 7–14 points.
  - Latency: thinking on costs roughly 20–30×, from about 0.2–1.3 s to about 6.5–35 s median.
  - qwen2.5-coder has no thinking mode, so its two rows match.
- **Placement and VRAM:**
  - Up to 4.3 GB of the 6 GB GPU was used.
  - T2 and T2-code are only partly GPU-resident at 16K context. Their offload explains most of their latency.
- **System RAM:** in use across the whole machine, not just the model. The peak was 20.3 GB of 24 GB, during T2 with thinking on.

**Closure.** Configuration (1) is measured on BLAZE-6 in both thinking modes. Configurations (2) and (3) stay NOT RUNNABLE in this ticket, reported with their reasons.
- **Configuration (2):** needs PHX-029 and PHX-046, then a runner mode under the PHX-091 continuation.
- **Configuration (3):** the owner decided on 2026-10-15 to install Colibri and run it in that continuation.

## Continuation: configuration (2) runner — IN_PROGRESS (2026-10-18)

Branch `feature/PHX-091-config2-runner`, base main `f8aaef0`. This is the
PHX-029 "PHX-091 configuration (2)" follow-up, after PHX-029, PHX-046 and
PHX-047 landed. The FYP review is on 2026-10-21.

**Delivered (no live model calls):**
- `scripts/fyp_multi_agent.py`, reached through
  `scripts/fyp_benchmark.py --config phoenix-multi-agent`. The baseline script
  only gains the dispatch and a corrected configuration (2) reason.
  - Each v1 case runs as one mission through `PhoenixRuntime.run`. The runtime
    is built as in `scripts/phoenix_mission_smoke.py`: a default
    `OllamaProvider` plus a `think=False` one for the structured roles, the
    default tier table and only the installed models. It is text-only (no
    filesystem tool).
  - It has its own sequential loop, not `BenchmarkRunner` (PHX-029 finding 9).
  - `--path complex` (default), `direct` or `triage`. `triage` is a no-op
    until PHX-105: it runs COMPLEX and records the requested and used paths.
  - `--warmup 0 --runs 1` by default; both are recorded.
  - Scoring: the unchanged `ScoringRule.score` on `MissionResult.text`. A
    mission without text (failed, cancelled, waiting) counts as not passed.
  - A runtime defect (unexpected exception) is recorded as `error` /
    `runtime_defect` with the exception type only. The run continues, and the
    exit code is then 1.
  - The CPU-only placement guard checks every tier model before the run.
- **Metrics:**
  - accuracy and success rate;
  - completed, failed, waiting_permission, cancelled and error counts;
  - mission latency (median/min/max over completed missions, plus over all
    missions);
  - generations per mission;
  - summed input and output tokens;
  - LEVI passed/failed verdicts, in total and per mission;
  - a failure-code histogram (`code/responder_error`);
  - the prompt set version;
  - per-category accuracy;
  - generations by role and model, with each role's thinking mode;
  - RAM/VRAM sampled at mission boundaries.
- **NOT RUNNABLE, with reasons:** routing accuracy (owned by PHX-105),
  verification catch rate (needs fault-injected cases), recovery,
  memory/research/computer control, Colibri I/O, user intervention, and
  configuration (3).
- **Comparison:** `--compare <config1-results.json>` (repeatable) writes
  `.fyp-bench/fyp-comparison.md` and a `comparison` section. Each
  configuration (1) tier (best first per file) sits next to configuration (2),
  with accuracy, success, median latency, tokens per task, a same-task-set
  check and the evidence directory. `--from-results` re-renders from a saved
  results file without models.
- **Privacy:** as ADR-029, hashes and lengths only, never prompts or answers.
- **Tests:** `tests/test_fyp_multi_agent.py` (11 tests).
  - It uses a real `PhoenixRuntime` with a scripted model. The same fake also
    drives the configuration (1) harness for `--compare`.
  - It is mapped in `tests/matrix.toml`, and the script was added to the
    `mypy-scripts` gate.
- **Read-only check:** `--compare` was exercised offline against the three
  real configuration (1) GPU evidence files with a scripted configuration (2)
  run (scratch output only). All three parse, and the task-set hashes match.

**Framing the comparison honestly:**
- **Models:** configuration (2) mixes models by role. Per the tier table,
  planner, assignment and LEVI run on 9B; specialists and the final answer on
  4B; build_engineering on coder 7B; 2B is the fallback.
- **Thinking:** structured roles use `think=False`. Specialist and
  final_answer use the model default, so Qwen3.5 thinks.
- **Latency:** configuration (2) latency is whole-mission time over about 6–12
  generations, including model swaps on the 6 GB GPU. Compare it with
  single-generation latency only with that caveat.
- **Exact-match cases:** they may lose points to verbose synthesis. This is
  reported, not tuned away (PHX-029).

**Live run.** The owner or main session runs it on the GPU, with headless
`ollama serve` as for the configuration (1) GPU baselines:

```text
python scripts/fyp_benchmark.py --config phoenix-multi-agent \
  --compare <validation>/PHX-091-fyp-gpu-think-off-20261015-154843/fyp-benchmark-results.json \
  --compare <validation>/PHX-091-fyp-gpu-think-default-rerun-T1T2-20261015-173438/fyp-benchmark-results.json
```

- **Expected duration:** about 16 cases × 1–2 min ≈ 20–35 min per measured run,
  plus model loads for the placement checks.
- **Optional third `--compare`:** run 2 (`...think-default-20261015-155753`)
  for the thinking-on T0 and T2-code rows. Its T1 and T2 rows are superseded
  by run 3.

**Remaining:** the live GPU run and its evidence. This section then becomes
DONE.
