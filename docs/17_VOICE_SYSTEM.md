# 17 — Voice System

Owner: **UTA**.

## Wake phrase

> **Rise, Phoenix.**

## Startup speech

> Wake command acknowledged. Initializing Phoenix network. Checking all agents and core systems.

Healthy:

> All agents online. All core systems operational. What's the mission?

## Components

- wake-word provider
- VAD
- STT provider
- conversation-state manager
- TTS provider
- audio output
- barge-in/interruption
- fixed phrase cache

## States

SLEEPING, LISTENING, THINKING, SPEAKING, DEGRADED_VOICE, TEXT_ONLY.

## Requirements

- Wake phrase starts an active conversation.
- Follow-ups do not need repeated wake phrase until timeout/sleep.
- User can interrupt speech.
- Voice failure falls back to text.
- TTS is provider-neutral.
- Coqui XTTS is a candidate, not a hard dependency.
- PHOENIX uses an original primary voice.
- Optional themed agent voices should use original/licensed/consented profiles for distribution.

## Implementation (PHX-114, ADR-105 to ADR-109)

All local, on the CPU (the GPU stays with Ollama), behind the PHX-060 to
PHX-065 contracts:

| Component | Engine | Code |
| --- | --- | --- |
| Wake | faster-whisper `tiny.en` (int8, beam 1) on each speech burst, matching "Rise, Phoenix" variants | `phoenix/voice/engines/whisper.py` |
| VAD / endpointing | EnergyVad (PHX-061), 30 ms frames: -45 dBFS; 450 ms ends a wake utterance, 810 ms a request | `phoenix/voice/engines/pipeline.py` |
| STT | faster-whisper `base.en` (int8, beam 5), English | `phoenix/voice/engines/whisper.py` |
| TTS | Piper `en_US-norman-medium` (ONNX Runtime CPU) | `phoenix/voice/engines/piper.py` |
| Microphone / audio output | sounddevice (PortAudio): 16 kHz capture, interruptible playback in 50 ms blocks | `phoenix/voice/engines/sound.py` |
| Conversation / barge-in | PHX-064 manager; barge-in at -28 dBFS for 240 ms | `phoenix/voice/engines/pipeline.py` |
| Startup | PHX-065 coordinator on the first wake of a run; a short chime after that | `phoenix/voice/engines/pipeline.py` |
| Backend service and API | `VoiceService`, `/voice/status`, `/voice/start`, `/voice/stop` | `phoenix/api/voice.py`, `phoenix/api/routes/voice.py` |

Flow: "Rise, Phoenix" leads to the ritual or a chime, then LISTENING. A
request goes to STT, then THINKING: it is submitted as a normal chat mission
(`MissionService.submit`, PHOENIX triage, PermissionEngine), and its answer is
stored in the conversation. Then SPEAKING: a short spoken rendering of the
answer. Then LISTENING again; follow-ups need no wake phrase for 30 s of
silence, after which voice goes back to SLEEPING. Saying "Rise, Phoenix" and
the request in one breath also works.

Fallbacks:

- Unclear speech is retried quietly twice, then TEXT_ONLY.
- A TTS or playback failure gives DEGRADED_VOICE; answers stay in the chat.
- Missing models, a dead microphone or missing native libraries make the
  state `unavailable` with a closed code. Text chat is unaffected
  (INV-015, INV-016).

Setup and checks (owner-run):

- `uv run python scripts/voice_setup.py` downloads and verifies the pinned
  models into `D:\Phoenix\models\voice`. Nothing downloads automatically.
- `uv run python scripts/voice_smoke.py measure|speak|mic|live`.
- Voice is off until `POST /voice/start`, or `PHOENIX_VOICE_AUTOSTART=1` in
  the backend's environment.

Measured on BLAZE-6 (CPU):

- Wake check: about 0.36 s after the 0.45 s end-of-speech hangover.
- STT for a 3.2 s utterance: about 0.5 s.
- Piper: first audio in about 45 ms; a 4 s reply in about 0.16 s.

Limitations:

- No acoustic echo cancellation: use headphones or moderate volume, or
  PHOENIX can interrupt itself.
- Endpointing is amplitude-based.
- Wake matching is text-based evidence.
- Piper is GPL-3.0-or-later (ADR-106).
- The desktop mic toggle and indicator are a follow-up.
