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
