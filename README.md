# PHOENIX

PHOENIX is a **local-first, voice-first multi-agent AI desktop assistant** for intelligent personal computing and autonomous task orchestration.

The user experiences one assistant — PHOENIX. Internally, PHOENIX delegates work to specialized agents, local models, tools, memory, verification, recovery, web research, computer control, media, and document systems.

## Wake experience

Wake phrase:

> **Rise, Phoenix.**

Startup response:

> **Wake command acknowledged. Initializing Phoenix network. Checking all agents and core systems.**

Healthy response:

> **All agents online. All core systems operational. What's the mission?**

## Primary stack

- Desktop: Tauri 2
- Frontend: React + TypeScript + Vite
- UI: Tailwind CSS + shadcn/ui
- State: Zustand + TanStack Query
- Graphs: React Flow / XYFlow
- Backend: Python 3.11 + FastAPI
- Validation: Pydantic v2
- Persistence: SQLite + SQLAlchemy 2 + Alembic
- Vector search: VectorStore abstraction; sqlite-vec candidate
- Local inference: Ollama
- Optional deep inference: Colibri
- Voice: provider abstraction; Coqui XTTS candidate

## Development hardware

**BLAZE-6**
- Intel Core i7-13650HX, 14 cores / 20 threads
- 24 GB RAM
- NVIDIA RTX 3050 Laptop GPU, 6 GB VRAM
- Ollama 0.35.1
- Preferred large-data/model storage: `D:\Phoenix\`

## Source-of-truth order

1. `AGENTS.md`
2. `RULES.md`
3. `MENTAL_MODEL.md`
4. `INVARIANTS.md`
5. `MASTER_SPEC.md`
6. `PRD.md`
7. Accepted decisions in `DECISIONS.md`
8. Relevant subsystem documents in `docs/`
9. Current feature ticket
10. Existing code/tests

If authoritative documents conflict, stop and resolve the conflict before coding.
