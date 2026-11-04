# Voice and persona ADRs (agent D, 315-319)

## ADR-315: Instant acknowledgement from cached Piper clips
A small fixed set of lines ("On it, boss." and variants, DRAFT pending owner approval) is rendered once through the existing Piper provider, cached as JSON under the app data dir (`voice_cache/`, key = voice profile + text), and played the moment a request is understood, in parallel with the mission. The clip is awaited before the answer plays (one playback at a time) and cancelled with the turn. Rendering runs before the pipeline starts, so Piper is never busy with a real turn. Any failure means no clip; the pipeline is never blocked. No new dependency.

## ADR-316: Completion lines are code templates over real data
Site completion is spoken as "Boss, site ready. N sections." built from the status reader's real section count; failed/pending have fixed lines. No LLM, no prompt change. Wording is DRAFT pending owner approval.

## ADR-317: Pre-warm at backend start
`phoenix/models/prewarm.py`, started from the launcher in daemon threads (off with `PHOENIX_PREWARM=0` and under pytest): Ollama `POST /api/generate` with `keep_alive: "30m"` for `qwen3.5:4b` (OllamaProvider has no keep_alive option and models/ollama.py is not in this change's scope, so the same endpoint is called directly with httpx, an existing dependency), plus a Piper and Whisper load-and-drop to pay import and disk-cache cost once. Each stage logs `ok` and milliseconds; failures are swallowed.

## ADR-318: Voice transcripts go through handle_command first
`MissionVoiceResponder` calls `phoenix.sites.command.handle_command(text, active_site_id)` (import guarded; expected signature in the `SiteCommand` Protocol). `passthrough` or any error keeps the existing mission flow; `site` speaks the templated completion line once a `SiteStatusReader` reports ready. `active_site_id` is the last site this voice run touched.

## ADR-319: Stage timing and VOICE_RULE
`voice_timing stage=stt|first_token|first_audio ms=N` logs carry the turn id as correlation id and never text. `first_token` is answer-text-available (mission settled; the voice path does not stream tokens); `first_audio` is logged for the ack and for the answer. `VOICE_RULE` (`phoenix/voice/persona.py`) is appended to answer prompts only through `apply_voice_rule` when `PHOENIX_VOICE_RULE=1`; default OFF. Barge-in verified: interrupt sets the output stop flag, the stream is aborted (device buffer flushed) within one 50 ms block; a cancelled responder now also cancels its mission and the pending ack clip.
