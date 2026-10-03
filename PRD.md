# PHOENIX V1 Product Requirements Document

## Product statement

PHOENIX is a **local-first, voice-first multi-agent AI desktop assistant** that lets the user interact naturally with one assistant while specialized agents coordinate local models, memory, tools, computer control, research, media, documents, verification, and recovery.

## Goals

- Natural voice and text interaction.
- Local model execution through Ollama by default.
- Hidden multi-agent orchestration behind one PHOENIX identity.
- Long-term local memory.
- RAG over uploaded/project files.
- Real-time web research with provenance.
- Safe desktop/browser control.
- Local document generation.
- Spotify and YouTube integration.
- Independent verification.
- Adaptive recovery.
- Hardware-aware model routing.

## V1 requirements

### Voice
- Wake phrase default: `Rise, Phoenix`.
- Local-first STT where practical.
- VAD and barge-in.
- TTS through a provider abstraction.
- Text fallback if voice fails.

### Orchestration
- Direct path for simple tasks.
- AIZEN planning for complex tasks.
- Bounded specialist roles.
- Permission checks before actions.
- LEVI verification when required.
- MAHORAGA bounded recovery.
- KAIDO degraded essentials.

### Models
- Ollama default provider.
- Colibri optional deep provider.
- Routing uses capability and hardware state.

### Memory/RAG
- Working, episodic, semantic, user, project, and agent memory.
- User-visible memory controls.
- Document indexing/retrieval.

### Computer
- Browser automation.
- App/window control.
- Screenshot/vision.
- Keyboard/mouse fallback.
- Risk-based approvals.

### Documents/media
- Read common files including PDF/DOCX/TXT/Markdown/CSV/images/code where supported.
- Generate PDF/DOCX/Markdown/CSV reports.
- Spotify control and built-in YouTube experience.

## Non-goals

- Mobile app.
- Multi-user SaaS.
- Distributed GPU cluster.
- Unrestricted financial actions.
- Foundation-model training from scratch.
- Hundreds of independent agents.
- Unrestricted always-on desktop autonomy.
- Bundling unauthorized exact voice-actor clones.
