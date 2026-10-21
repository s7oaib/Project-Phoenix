# PHX-117 — Metric datasets

Status: DONE (datasets, scorers and runner; scripted CI measurement). Live
LEVI catch rate and live intervention runs await a BLAZE-6 GPU session.
Memory, research and computer control stay NOT RUNNABLE until PHX-110, 111
and 112 land.
Phase: 10 — Product Integration (M4 evaluation)
Branch: feature/PHX-117-metric-datasets
Starting main/base SHA: 57224311c1449f7a71ff0417ed11a04a2df7c0a1 (`origin/main`, merge of #85)

## Objective

Make every docs/24 metric measurable for the FYP, or NOT RUNNABLE with a
justified reason (ADR-079). This needs fixed, versioned, sha-pinned, harmless
datasets, deterministic scorers and runner support for:
- verification catch rate;
- memory retrieval accuracy;
- research evidence quality;
- computer-control completion;
- user intervention count;
- recovery success, wired consistently.

The ticket also writes the model routing freeze criteria (the docs/05
benchmark gate).

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md`, `/MASTER_SPEC.md`
- `docs/24_TEST_EVALUATION.md` (12 metrics; ADR-079 measured-or-NOT-RUNNABLE)
- `docs/05_MODEL_STRATEGY.md` (freeze gate)
- `tickets/PHX-091-fyp-benchmark-suite.md`, `tickets/PHX-105-request-triage.md`
  (versioned-set and sha-pin pattern), `tickets/PHX-109-mahoraga-missions.md`

## Owner decisions (2026-10-21)

The owner chose the recommended option for every question, recorded as
ADR-115 to ADR-119:

- **Q1 → R1, ADR-115.** One directory, `benchmarks/metrics/`, holds five
  `<name>-v1.toml` sets.
  - Each set's sha256 and version are pinned in `tests/test_metric_datasets.py`.
  - Fixture pages are pinned through per-page sha256 entries in the set.
  - Text fixtures are inline in the TOML.
- **Q2 → R2, ADR-116.** Verification catch rate uses producer-side fault
  injection.
  - Scripted producers carry the fault; only LEVI's verifier turns are the
    model under test.
  - CI uses a scripted always-pass verifier, which gives the deterministic
    floor and is labelled not a FYP number. The live run uses Ollama.
  - Controls measure false rejections. Inconclusive missions are excluded
    from the rate.
- **Q3 → R3, ADR-117.** Datasets for capabilities not yet built ship now.
  - Their scorers carry fixed calibration vectors, which `--dry-run` checks.
  - The live metric is NOT RUNNABLE until the ticket is DONE and an adapter is
    registered.
- **Q4 → R4, ADR-118.**
  - One intervention is one `waiting_permission` result. There is no
    clarification turn in v1.
  - The runner re-runs with a session grant for exactly the pending request.
  - Recovery success comes from configuration (2) `MissionTrace.recoveries`.
- **Q5 → R5, ADR-119.** Per-role freeze thresholds go into docs/05. Nothing is
  frozen yet.

## Datasets

| Set | Version | Size | sha256 |
| --- | --- | --- | --- |
| `verification-v1.toml` (`phoenix-verification`) | 1.0.0 | 25 cases: 5 controls; 7 wrong answers, 5 ungrounded claims, 3 injected tool evidence, 3 forged tool evidence, 2 forged verdicts | `000cf1df072ac987927fd5e192058b1a825c87241b32cf08d432681ac7db09b1` |
| `interventions-v1.toml` (`phoenix-interventions`) | 1.0.0 | 8 missions, 3 fixture files, max 4 rounds | `3442bf3622ed4bb39242c09bccdae8072de12e0dddb7528d19398ed739a326aa` |
| `memory-v1.toml` (`phoenix-memory`) | 1.0.0 | 20 questions (6 direct, 6 paraphrase, 6 distractor, 2 multi-source) over 10 documents; 5 calibration vectors | `862ebc6849bec243a280cf44faf58ffc2cdcb9ece92af96bc9fd055eea64c700` |
| `research-v1.toml` (`phoenix-research`) | 1.0.0 | 8 questions, 8 `.test` sources (4 distractors); 7 calibration vectors | `fdeecdb9755a7e9a034a0198f6c5570696b042779107dbedf91e933e2116f3ad` |
| `computer-control-v1.toml` (`phoenix-computer-control`) | 1.0.0 | 10 tasks in 5 categories on 6 pinned local pages; 6 calibration vectors | `68828d30a32555a36b01f924819fb4f08a5d37a08e49038e3319b478b4c329b7` |

## Metric status after this ticket

| Metric | Status | Where |
| --- | --- | --- |
| verification catch rate | MEASURED (CI scripted floor: 25%, 5/20; 0% false rejections). Live LEVI is pending a GPU session | `scripts/metric_benchmark.py` |
| user intervention count | MEASURED (scripted: 8 prompts over 8 missions, 0 excess, 8/8 completed); configuration (2) reports its own | `scripts/metric_benchmark.py`, `scripts/fyp_multi_agent.py` |
| recovery success | MEASURED from configuration (2) traces (PHX-109); also via `--config2-results` | `scripts/fyp_multi_agent.py`, `scripts/metric_benchmark.py` |
| memory retrieval accuracy | NOT RUNNABLE: PHX-110 is TODO (the set and scorer are validated) | — |
| research evidence quality | NOT RUNNABLE: PHX-111 is TODO (the set and scorer are validated) | — |
| computer-control completion | NOT RUNNABLE: PHX-112 is TODO (the set and scorer are validated) | — |

Configuration (1) (`scripts/fyp_benchmark.py`) reports these six metrics as
NOT RUNNABLE. Each reason now names where the metric is measured.

## Requirements

- Fixed, versioned and sha-pinned sets. The file name must match the major
  version. Validation is strict, with exact keys and bounded one-line text.
- Content is harmless and local only, with no private data. Fixture pages are
  refused if they reference anything remote or loadable.
- Deterministic scorers. Reports hold IDs, closed codes and counts only, never
  prompts, answers or file text (ADR-029).
- The runner modes:
  - `--dry-run`: validates every set and replays the calibration vectors;
  - `--scripted`: CI, no Ollama;
  - live: Ollama, with the same CPU-only refusal as the other runners.
- The capability gate checks `FEATURE_TICKETS.md` and the `ADAPTERS` registry.
- No change under `phoenix/`, no new dependency, event type, migration or API
  change.

## Non-goals

- Running any live model in this ticket (no GPU or Ollama use).
- Building PHX-110, 111 or 112, or their adapters.
- Freezing any model route.
- Colibri disk I/O (PHX-116).

## Changed files

- `benchmarks/metrics/` (new): the five sets, plus the six
  `computer-control-v1/*.html` fixture pages.
- `scripts/metric_datasets.py` (new): loaders, validators, scorers and
  calibration.
- `scripts/metric_benchmark.py` (new): the runner, fault injection, the
  capability gate and reports.
- `scripts/fyp_benchmark.py`: the configuration (1) NOT RUNNABLE reasons point
  to the PHX-117 sets and runner.
- `scripts/fyp_multi_agent.py`:
  - `user_intervention_count` is MEASURED (`summary.interventions`, plus a
    summary line);
  - the memory, research and computer-control reasons are shared with
    configuration (1).
- `tests/test_metric_datasets.py` and `tests/test_metric_benchmark.py` (new);
  `tests/test_fyp_benchmark.py` and `tests/test_fyp_multi_agent.py` are
  updated.
- `tests/matrix.toml`: the two new files are mapped, and both scripts are added
  to the `mypy-scripts` gate.
- `.gitignore`: `.metric-bench/`.
- `docs/24_TEST_EVALUATION.md` and `docs/05_MODEL_STRATEGY.md` (freeze
  criteria); `DECISIONS.md` (ADR-115 to ADR-119); `FEATURE_TICKETS.md`.

## Tests

- `tests/test_metric_datasets.py`:
  - sha256 and version pins for all five sets;
  - the directory holds exactly the pinned files;
  - fixture pages are pinned and local only;
  - composition of every set;
  - calibration replay;
  - invalid sets and copies are rejected (verification, interventions, memory,
    research and computer control), including tampered, extra and
    remote-loading fixture pages;
  - outcome rules and summaries for every scorer.
- `tests/test_metric_benchmark.py`:
  - the dry run, which needs no runtime, and its failure on a calibration
    mismatch;
  - the full scripted run, which checks:
    - the deterministic floor;
    - each forgery caught at its document step;
    - permission prompts at the minimum;
    - the metric table;
    - privacy;
  - live-mode routing, where only LEVI turns reach the "live" provider;
  - CPU-only refusal;
  - the capability gate (TODO, DONE without an adapter, DONE with one);
  - recovery wiring from a configuration (2) file;
  - forgery scoping and restore;
  - script step order.
- All of these run without network access, behind an autouse no-network
  guard.

## Verification

- `uv run python scripts/metric_benchmark.py --dry-run` validates all five
  sets, and every calibration vector matches.
- `uv run python scripts/metric_benchmark.py --scripted` gives a catch rate of
  25% (5 caught, 15 escaped, 0 inconclusive) and 0/5 false rejections, with
  8 permission prompts over 8 missions, all completed.
- Targeted tests: 64 passed (`tests/test_metric_datasets.py` and
  `tests/test_metric_benchmark.py`). The FYP and routing runner tests:
  64 passed.
- Full matrix (`uv run python scripts/test_matrix.py`): see "Completion
  evidence".

## Acceptance criteria

- [x] All five sets are fixed, versioned, sha-pinned and harmless. Their
  validators reject invalid sets.
- [x] Verification catch rate runs through `PhoenixRuntime` with a scripted
  model for CI and has a live mode.
- [x] The memory, research and computer-control sets and scorers are defined
  and checked by `--dry-run`. Their live metrics are NOT RUNNABLE, with the
  capability detected.
- [x] User intervention count is measured from mission traces.
- [x] Recovery success is wired consistently into the configuration (1) and
  (2) rows and into the metric runner.
- [x] The model routing freeze criteria are in docs/05 (ADR-119).
- [x] The test files are mapped in `tests/matrix.toml`, and the full matrix
  passes.

## Invariants and security check

- INV-003/004/005: grants come only from the harness, acting as the user. Each
  one is exactly the pending request, for the session, at P1. PermissionEngine
  and ToolRegistry are used unchanged.
- INV-008: a LEVI failure is never counted as success. Inconclusive missions
  are excluded and reported.
- INV-017: fixture files and pages are untrusted data. Injected-instruction
  cases test exactly that.
- INV-021/022: defects are recorded by exception type and never hidden.
- The forgery harness wraps `FilesystemTool.execute` for one case and one tool
  instance, then restores it. It runs in the benchmark process only; product
  code is untouched.
- No secrets, no network and no GPU in CI.

## Limitations

- The scripted catch rate is a harness floor, not a FYP number. The live LEVI
  number needs a GPU session.
- The research rubric measures evidence, not correctness; fact coverage is
  reported beside it.
- The 20 faults are a fixed sample. They do not estimate the system's overall
  error rate.
- The computer-control observation format is a contract that PHX-112 must
  produce.

## Completion evidence

- Implementation commit `1ee11642d8bcd34d50d158abb1306cb01d80f7b9`.
- `uv run python scripts/test_matrix.py` on that commit, clean tree
  (2026-10-21, 09:05–09:56 UTC): **PASS**.
  - All 13 gates passed, including `mypy-scripts` with both new scripts.
  - pytest: 16409 passed, 0 failed, 30 skipped.
  - npm: 195/195.
  - All 19 docs/24 categories passed.
- An earlier standalone `pytest -q` gave the same result: 16409 passed and
  30 skipped.
- No frozen file changed. Nothing under `phoenix/` was touched, so there was
  no re-pin and the second commit is evidence only.
