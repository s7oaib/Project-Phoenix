# 25 — Dependency Classification

## Required V1 runtime/platform

### Ollama
Default local model runtime.

### Tauri / React / FastAPI / SQLite ecosystem
Core application platform.

## Optional V1 runtime

### Colibri
https://github.com/JustVugg/colibri

Optional deep MoE inference backend using storage/RAM/VRAM tiering.

### Coqui TTS
https://github.com/coqui-ai/TTS

Candidate local TTS backend behind the voice-provider abstraction.

## Experimental/reference inference

### AirLLM
https://github.com/lyogavin/airllm

Large-model inference/training under constrained VRAM; experimental provider/reference.

## Development/training

### Soup
https://github.com/MakazhanAlpamys/Soup

Fine-tuning/post-training/export experiments. Not required runtime.

### RTK
https://github.com/rtk-ai/rtk

Optional compact terminal-output tooling for coding agents.

### Nightshift
https://github.com/marcus/nightshift

Optional development maintenance automation.

## Design references

### ECC
https://github.com/affaan-m/ECC

Reference for coordinated engineering agents, skills, memory, review, and verification. Phoenix must not become an ECC wrapper.

### Karpathy-inspired skills
https://github.com/multica-ai/andrej-karpathy-skills

Reference for think-before-coding, simplicity, surgical changes, and goal-driven verification.

### Ponytail
https://github.com/DietrichGebert/ponytail

Reference for avoiding unnecessary implementation complexity.

## Rule

Promoting an optional/reference dependency to required runtime requires an accepted decision.
