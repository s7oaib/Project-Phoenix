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
