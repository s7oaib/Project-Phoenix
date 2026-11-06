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

## Quick commands

Say or type "open browser", "open youtube", "open google", "search youtube
for <x>" (or "play <x> on youtube") and "search google for <x>" (or "google
<x>"): PHOENIX opens the link in your normal default browser at once, with no
AI planning (PHX-130, ADR-220). The first time, PHOENIX asks to open links;
choose **Always allow** (the approve API's `{"duration": "always"}`) and it
never asks again. Revoke it any time under Settings, Permissions (grants).

PHX-133 adds "open wikipedia", "search wikipedia for <x>", "open github",
"open gmail", "open maps", "search maps for <x>", "directions to <x>", "open
google news"; "what time is it" / "what's the date" (answered at once from
the local clock); and "open notepad", "open calculator", "open file
explorer" / "open files" (one separate **Always allow** covers the apps).

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

## License

PHOENIX is free software: you can redistribute it and/or modify it under the terms of
the GNU General Public License as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later version. See [LICENSE](LICENSE).

Copyright (C) 2026 Shoaib H Jamadar, Mohammed Mueiz and Mohammed Soud,
Department of AIML, HKBK College of Engineering.

Text-to-speech uses [Piper](https://github.com/rhasspy/piper), which is licensed under
the GPL-3.0; PHOENIX's installer bundles it, which is why PHOENIX is GPL-licensed too.
