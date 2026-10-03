# PHOENIX V1 Master Specification

## Identity

PHOENIX is a local-first, voice-first multi-agent AI desktop assistant.

## Stack

### Desktop
Tauri 2 + React + TypeScript + Vite + Tailwind + shadcn/ui + Zustand + TanStack Query + React Flow.

### Backend
Python 3.11 + FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic + asyncio + SQLite.

## Hardware profile

**BLAZE-6**
- i7-13650HX 14C/20T
- 24 GB RAM
- RTX 3050 Laptop 6 GB
- Ollama 0.35.1
- Prefer D: for large model/data storage

## Model baseline

- Fast: Qwen3.5 4B / Ollama
- Smart: Qwen3.5 9B / Ollama
- Coding: Qwen2.5-Coder 7B / Ollama
- Fail-safe: Qwen3.5 2B / Ollama
- Embeddings: EmbeddingGemma
- Optional deep: Qwen3.6-35B-A3B / Colibri, benchmark-gated
- Experimental: gpt-oss 20B, AirLLM, larger MoE models

Default normal context: 16K.
KAIDO context: 8K.
Heavy local generations: 1 at a time by default.

## Authority

- PHOENIX: final orchestrator.
- AIZEN: complex planner.
- Specialists: bounded domain work.
- LEVI: verification.
- MAHORAGA: bounded adaptive recovery.
- KAIDO: degraded fail-safe.

## Wake flow

Wake phrase: `Rise, Phoenix`.

PHOENIX says:
> Wake command acknowledged. Initializing Phoenix network. Checking all agents and core systems.

Healthy:
> All agents online. All core systems operational. What's the mission?

Health status must reflect real checks.

## Permission model

P0 internal; P1 observe; P2 low-risk/reversible; P3 modify; P4 sensitive/destructive/external; P5 blocked.

## Persistence

SQLite structured state.
Vector retrieval behind a VectorStore interface; sqlite-vec is the initial candidate.

## Voice

UTA owns voice interaction.
Voice provider is abstract.
Coqui XTTS is a candidate, not a mandatory architecture dependency.

## Computer control preference

API/DOM -> accessibility/native automation -> vision -> mouse/keyboard fallback.

## Public theme

Anime names are display aliases. Neutral internal IDs are stable.
