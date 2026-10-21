# 05 — Model Strategy

## Provider contract

Agents depend on `ModelProvider`, not provider internals.

Provider capabilities include:
- health
- generation
- streaming
- structured output metadata
- tool-use metadata
- vision metadata
- context limits
- cancellation
- metrics

## BLAZE-6 candidates

| Tier | Runtime | Candidate | Purpose |
|---|---|---|---|
| T0 | Ollama | Qwen3.5 2B | KAIDO fail-safe |
| T1 | Ollama | Qwen3.5 4B | Fast everyday Phoenix |
| T2 | Ollama | Qwen3.5 9B | Smart reasoning/AIZEN/vision |
| T2-code | Ollama | Qwen2.5-Coder 7B | FRANKY coding |
| Embed | Ollama | EmbeddingGemma | Memory/RAG |
| T3 | Colibri | Qwen3.6-35B-A3B | Optional deep strategy |
| Experimental | Ollama/other | gpt-oss 20B | Benchmarks |

## Defaults

- Normal context: 16K.
- KAIDO context: 8K.
- Prefer retrieval over oversized context.
- One heavy local generation at a time on BLAZE-6 unless benchmarks justify more.
- Keep-alive prevents constant model swaps.

## Colibri

Colibri is for optional higher-total-parameter MoE inference using storage/RAM/VRAM tiering. It does not magically shrink model intelligence requirements; it changes residency and movement of weights.

Deep mode must be benchmarked for RAM, VRAM, NVMe I/O, TTFT, throughput, and quality on the actual laptop.

## Freeze gate

Exact model routing is not permanently frozen until local benchmarks cover quality, tool calling, structured output, coding, planning, vision, TTFT, throughput, RAM, VRAM, and disk I/O.

### Model routing freeze criteria (benchmark gate, ADR-119)

The runtime tier table (`default_tier_table()` in `phoenix/agents/runtime.py`) is
**provisional** until this gate passes. It is frozen one role at a time. A
frozen role keeps its first-choice model, and a later change needs new evidence
against the same gate.

**Evidence.** The evidence must meet all of the following:
- Fixed sets at their pinned versions: `benchmarks/fyp/tasks-v1.toml`,
  `benchmarks/routing/routing-v1.toml`, and `benchmarks/metrics/*-v1.toml`.
  No ad-hoc prompts.
- Runs on BLAZE-6, on AC power, with the model fully on the GPU or partly
  offloaded. CPU-only runs never count.
- At least 3 measured runs per candidate. The fixed prompt set version, the
  Ollama version and the thinking setting are recorded in the results JSON.
- Every metric reported MEASURED or NOT RUNNABLE with a reason (ADR-079).

**Per-role thresholds.** A role freezes when its first-choice candidate meets
every row that applies to it:

| Role | Metric and set | Threshold |
| --- | --- | --- |
| Triage (PHOENIX) | `routing_accuracy`, routing-v1 | ≥ 90% overall, ≥ 80% in every category, fallbacks ≤ 5% |
| Structured roles (planner, assignment, document turns) | schema-valid outputs: no `responder_output/schema` failures in configuration (2) | ≥ 95% of generations |
| LEVI (verifier roles) | live `verification_catch_rate`, verification-v1 | ≥ 80% catch rate, ≤ 20% false rejections, 100% of the deterministic detector |
| Final answers (configuration 2) | tasks-v1 | No wrong answer reported as LEVI-verified; accuracy no lower than 10 points below the best single model of configuration (1) |
| Coding (T2-code) | tasks-v1 `coding` category | ≥ the best general model's coding accuracy |
| Vision | PHX-052 vision benchmark | Frozen only when that metric is MEASURED; until then, provisional |
| Memory and research roles | `memory_retrieval_accuracy` hit@3 ≥ 80%; research pass rate ≥ 80% | After PHX-110 or PHX-111; until then, provisional |
| Every role | latency and memory | DIRECT median ≤ 5 s; COMPLEX median ≤ 180 s; peak VRAM within 6 GB with one heavy generation at a time (INV-023) |
| Deep tier (T3, Colibri) | RAM, VRAM, NVMe disk I/O, TTFT, throughput and quality on BLAZE-6 (PHX-116) | Never default-routed until it beats T2 on quality at an accepted latency |

**Tie-breaks.** A larger model replaces a smaller one only if it wins by at
least 5 points on the role's metric within the latency limits. Otherwise the
smaller model stays (INV-023).

**Re-opening.** A frozen role re-opens when any of these changes: its model
(tag or digest), the prompt set version, a major Ollama version, or the
benchmark set version. A freeze or re-open is recorded in `DECISIONS.md` with
the results files that justify it.
