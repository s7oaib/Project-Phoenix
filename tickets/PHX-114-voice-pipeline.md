# PHX-114 — Voice pipeline ("Rise, Phoenix")

Status: DONE (backend). The desktop mic toggle and voice-state indicator are a follow-up (see "Desktop").
Phase: 10 — Product Integration (milestone M3, capabilities)
Branch: `feature/PHX-114-voice-pipeline` (local commits only; no push, no PR)
Base: `origin/main` `5722431` (PR #85)

## Objective

The user says "Rise, Phoenix", then talks to PHOENIX like Siri and hears spoken replies. Concrete, offline engines go behind the existing PHX-060 to PHX-065 contracts:

- faster-whisper wake spotting and transcription on the CPU;
- Piper speech;
- sounddevice capture and playback.

They are joined in a live loop. A spoken request is submitted exactly like a typed chat. A voice failure never affects text chat.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md` ("Voice-first, not voice-only"), `/MENTAL_MODEL.md` ("Voice is an interface channel")
- `/INVARIANTS.md`: INV-001, INV-003, INV-011, INV-015, INV-016, INV-019, INV-021, INV-022
- `/MASTER_SPEC.md` (wake flow), `docs/17_VOICE_SYSTEM.md`, `docs/13`, `docs/14`, `docs/25`
- PHX-060 to PHX-065 (contracts), PHX-081 and PHX-086 (UI), PHX-101 (security), PHX-103 (events), PHX-104/105 (chat path)

## Owner decisions (final, 2026-10-21)

- **Wake.** A small always-on faster-whisper model on the CPU (tiny/base, int8) matches "Rise, Phoenix" variants.
- **STT.** faster-whisper, CPU int8, base or small, picked by measured latency.
- **TTS.** Piper with a local English voice.
- **Devices.** Microphone capture through sounddevice.
- **Offline.** Everything runs offline, and the GPU stays with Ollama.
- **Downloads.** The Python packages, the Whisper models and one Piper English voice are pre-approved. Models live under `D:\Phoenix\models\voice` and are fetched only by an explicit setup step, never automatically.
- **Everything else.** Pick the recommended option and record it.

## Decisions (recommended defaults applied; the owner reviews R1 to R14)

- **R1 — Placement.** The new package `phoenix/voice/engines/` holds:
  - `models.py`: the pinned manifest and the models directory;
  - `whisper.py`: wake and STT;
  - `piper.py`: TTS;
  - `sound.py`: microphone and output;
  - `pipeline.py`: the live loop.

  `phoenix/api/voice.py` holds `VoiceService`, the mission responder and the health probes. `phoenix/api/routes/voice.py` holds the routes. The PHX-060 to PHX-065 modules are byte-identical. No new top-level module goes in `phoenix/voice`; the PHX-060 guard pins that directory's top-level files.
- **R2 — Models (ADR-109).**
  - Wake: `tiny.en`. STT: `base.en` (`PHOENIX_VOICE_STT_MODEL` may choose `tiny.en` or `small.en`).
  - TTS: Piper `en_US-norman-medium`, a male US voice trained from scratch on public-domain LibriVox speech.
  - Each file is pinned to a Hugging Face commit, a size and a SHA-256 digest.
  - `scripts/voice_setup.py` is the only downloader. The backend checks sizes and loads with `local_files_only=True`.
  - Models directory: `PHOENIX_VOICE_MODELS_DIR`, else `D:\Phoenix\models\voice` when it exists, else `~/.phoenix/models/voice`.
- **R3 — CPU only.** Whisper uses `device="cpu"` and `compute_type="int8"`, with 4 threads for wake and 8 for STT. Piper uses `use_cuda=False`.
- **R4 — Wake spotting.** An energy endpointer cuts utterances (450 ms of silence ends one; 240 ms of speech is the minimum). The first 3 s of each utterance (the PHX-060 window) go to `tiny.en`, beam 1, and the text is matched by `wake_phrase_score`:
  - a Phoenix-like word among the first four words;
  - preceded, ignoring fillers such as "the" or "of", by a rise-like word;
  - threshold 0.7.

  Whisper hears the phrase as "rise of Phoenix", "Rias, Phoenix" or "Brian's a Phoenix". All of these match; "Phoenix is a city" does not. Transcribing only speech bursts keeps the "always-on" model idle in silence.
- **R5 — The ritual once, then a chime.** The first wake of a run goes through the PHX-065 `RisePhoenixStartupCoordinator`: acknowledgement, six real health checks (database at head, available memory, model provider, tool registry, voice, agent registry), then the ready or degraded line. Later wakes play a 160 ms earcon. A wake utterance of 2.2 s or more also holds a request ("Rise, Phoenix, what time is it?"), and it becomes the first turn.
- **R6 — Turns.** End of speech is 810 ms of silence, and an utterance is at most 27.9 s (inside the 30 s STT window). Each turn goes through the PHX-064 `VoiceConversationManager`: STT, responder, TTS, playback. Follow-ups need no wake phrase for 30 s of silence; then voice sleeps again. The pipeline owns that window; the manager's own window is 60 s longer, so a request started just before the window closes is not lost.
- **R7 — Same path as `POST /chat` (INV-001, INV-003).** `MissionVoiceResponder` makes the same `MissionService` calls as the route:
  - one conversation per voice run, created like a chat without a `conversation_id`;
  - `submit(conversation_id, text, None)`, so PHOENIX triages the request (PHX-105);
  - every tool call still passes PermissionEngine.

  The responder then polls the settled mission (at most 600 s) and reads the stored answer. It never approves, denies or revokes anything. A mission waiting for permission is announced aloud and left to the user in the app. The spoken text is a short rendering (`speakable_text`: markup and URLs removed, code replaced by "code is shown on screen", at most about 600 characters plus "The full answer is on screen."). The full answer stays in the conversation.
- **R8 — Barge-in.** While PHOENIX speaks, every microphone frame feeds a stricter energy detector: -28 dBFS for 8 frames, 240 ms. The endpointer uses -45 dBFS. Sustained speech interrupts playback within one 50 ms output block, and the speech becomes the next turn. There is no acoustic echo cancellation, so loud speakers can self-interrupt (see "Limitations").
- **R9 — Failures stay in voice (INV-015, INV-016, INV-021, INV-022).**
  - A failed or empty transcription is retried silently twice, then the state becomes `text_only`.
  - A TTS or playback failure gives `degraded_voice`: answers keep arriving in the chat without speech.
  - A wake engine that fails five times in a row, a microphone that stops, missing models or missing native libraries end the run with state `unavailable` and a closed code (`models_missing`, `dependency_unavailable`, `microphone_unavailable`, `voice_failure`).
  - `start`, `stop` and `status` never raise, and text chat, `/ready` and missions are untouched.
- **R10 — API (ADR-108).** `GET /voice/status`, `POST /voice/start` (202, models load in the background) and `POST /voice/stop`. They sit behind PHX-101 like every route. Voice is off until requested, or until `PHOENIX_VOICE_AUTOSTART=1` at launch (the owner's opt-in for the demo, since the desktop toggle is a follow-up). The status holds the state, a code, the wake phrase, IDs and six component availabilities (the desktop `VoicePort` IDs). It never holds a transcript or an answer.
- **R11 — Events (ADR-108).**
  - `voice.listening`, `voice.speaking` and `voice.interrupted` join the PHX-103 allowlist with `state`, `session_id`, `turn_id`, `mission_id` and `code` only, with closed values or UUIDs.
  - `voice.listening` carries every state change that is not speaking or a barge-in (for example `thinking` with its `mission_id`, or `sleeping`).
  - The correlation ID is the voice session.
  - The vocabulary stays at 32.
  - The manager's and coordinator's own events go to a private bus, because their payloads are outside the allowlist.
- **R12 — Dependencies (ADR-105 to ADR-107).**
  - Required: `faster-whisper>=1.2.1,<2` (1.2.1), `piper-tts>=1.8,<2` (1.8.0, GPL-3.0-or-later; see ADR-106), `sounddevice>=0.5.6,<0.6` (0.5.6) and `numpy>=2,<3` (2.4.6).
  - Constraint: `[tool.uv] constraint-dependencies = ["huggingface-hub<1"]`. This avoids `httpx2`, which replaces Starlette's TestClient transport, and `typer`.
  - Lock: 21 new packages; no existing record changes except `phoenix`.
  - Engines are imported lazily through `importlib`, so importing the backend loads no native library, and mypy needs no new overrides.
- **R13 — Pins.**
  - `mission_events.py` moves from the PHX-109 pin, and `server.py` from the PHX-104 pin, to the PHX-114 implementation commit.
  - `routes/__init__.py` is pinned there too.
  - `pyproject.toml` and `uv.lock` move from `829670b` to the same commit in `tests/workspace.test.mjs`.
  - This lands in a separate pin commit, so the PR must merge with a merge commit.
  - Guards updated for the new behaviour: the route table (`test_rest_api.py`), the event mapping (`test_mission_events.py`), the distribution requirements (`test_bootstrap.py`), the websockets lock gate (sets aside exactly the PHX-114 delta, which `test_voice_pipeline.py` pins) and the `phoenix/api` diff guards (exclude the two new modules).
- **R15 — Merged with PHX-108.**
  - `server.py` keeps both PHX-108's ModelRegistrar refresher and the voice
    service. Voice starts after the refresher and stops first.
  - `routes/__init__.py` installs PHX-108's setup routes and the voice routes.
  - PHX-108's ADR-112 `voice` capability flag is flipped to `enabled`; live
    availability stays in `GET /voice/status`.
  - The three files move to a new PHX-114 pin at the resolution commit; the
    PHX-108 and PHX-118 pin blocks stay.
  - The owner accepted piper-tts's GPL-3.0 licence for the FYP (ADR-106); it
    must be revisited before any public release.
- **R14 — Primary voice provenance.** The Piper voice is declared `original` in the PHX-063 sense: a stock synthetic voice that imitates no character or voice actor. Its dataset is public domain. This is a declaration, not a rights proof (ADR-109).

## Requirements

1. `phoenix/voice/engines/` (new), `phoenix/api/voice.py` and `phoenix/api/routes/voice.py` (new).
2. `server.py`: `BackendServices.voice`, `ServerConfig.voice_autostart` and `voice_models_dir`, voice stopped first at shutdown, and `build_server_app(voice_factory=...)` for tests.
3. `routes/__init__.py`: the voice router. `mission_events.py`: the voice allowlist.
4. `scripts/voice_setup.py` (setup) and `scripts/voice_smoke.py` (owner-run checks and measurements).
5. Records: ADR-105 to ADR-109, this ticket, `FEATURE_TICKETS.md` and docs 13, 14, 17 and 25.

## Non-goals

- Desktop UI wiring (follow-up).
- Acoustic echo cancellation; a trained wake-word model; streaming or partial transcripts; streaming TTS playback.
- Non-English speech, speaker identification and voice cloning.
- Voice approval of permissions: never, by design.
- Cancelling a mission by voice.
- GPU inference.

## Tests

New `tests/test_voice_pipeline.py` (51 tests; 1 live test skipped unless `PHOENIX_VOICE_LIVE=1`). It covers:

- **Wake matching and text.** Wake-phrase variants and non-wake speech; transcript stripping; spoken rendering; STT window chunking.
- **faster-whisper adapters.** CPU, int8 and `local_files_only` loading; hallucination filtering; contract correlation; closed error codes with no content; missing models never loaded.
- **Piper adapter.** An original primary profile, CPU only, warm-up, and correlated audio.
- **sounddevice adapters (fake module).** Consecutive 30 ms frames; rate fallback with resampling; failing closed; interruptible playback; output resampling.
- **Pipeline (fake mic, engines and output).**
  - Wake, listen, answer, speak, follow-up, then sleep.
  - The ritual on the first wake, then the chime.
  - Wake and request in one breath.
  - Noise and non-wake speech start nothing.
  - Barge-in cuts a 10 s playback, and that speech becomes the next turn.
  - Quiet echo does not interrupt.
  - STT retries, then `text_only`.
  - TTS failure gives `degraded_voice`.
  - Wake errors are skipped until they persist.
  - A microphone failure ends the run.
- **Events and service.** The allowlist mirrors the pipeline's states and codes; content is refused; the service publishes only allowlisted events; failures become `unavailable` with a code.
- **Backend (real composition root and scripted model).**
  - The routes need the token, and nothing starts by itself.
  - A spoken request becomes a triaged chat mission, its answer is spoken, a typed follow-up works, and no event is dropped.
  - Missing models and a dead microphone leave chat, missions and `/ready` working.
  - Autostart works.
- **Records.** The pinned manifest; `voice_setup.py --check` never downloads; the engines stay offline and on the CPU; the exact dependency delta against `5722431`.

## Measured on BLAZE-6 (2026-10-21; CPU only, GPU untouched; other agents were loading the CPU about 20 to 60%)

`uv run python scripts/voice_smoke.py measure --runs 5`. The test speech is Piper output resampled to 16 kHz.

| Measure | Result |
| --- | --- |
| Model load | tiny.en 0.6 s, base.en 0.4 s, Piper load + warm-up 3.6 s (once, at start) |
| Wake detection ("Rise, Phoenix.", 1.9 s) | 5/5 detected; transcribe + match median 356 ms (319 to 517 ms). Add the 450 ms endpointer hangover: about 0.8 s from end of speech to wake |
| STT, 3.2 s utterance, base.en beam 5 | median 512 ms (469 to 547); a second run under more load had a median of 578 ms. Transcript exact |
| STT, same utterance, small.en beam 5 | median 1833 ms (1572 to 1995); also exact. Hence base.en |
| TTS time to first audio (Piper, first sentence) | median 45 ms (34 to 96) |
| TTS full reply (the 4.1 s ready line; playback waits for this today) | median 164 ms (133 to 265) |

Device check: the default input opened at 16 kHz and delivered consecutive 30 ms frames. The room was silent and nobody spoke; only the level was read. The output opened at 22.05 and 16 kHz. No audio was played on the owner's speakers, and no live conversation was run. That check is the owner's: `voice_smoke.py live`, or the app with `PHOENIX_VOICE_AUTOSTART=1`.

## Desktop

Follow-up, not done here: `apps/desktop` is hash-pinned, and the chat panel's voice copy is guarded by `workspace.test.mjs`. The change needs:

- a voice port in `core.ts` (`GET /voice/status`, refreshed on `voice.*` events, plus start and stop);
- a mic toggle and state indicator in `App.tsx` replacing "Voice unavailable";
- the matching UI tests.

The backend status already uses the desktop's `VoicePort` component IDs and states. Until then, voice is started with `PHOENIX_VOICE_AUTOSTART=1` in the backend's environment, which the Tauri shell inherits.

## Limitations

- **No echo cancellation.** At high speaker volume PHOENIX's own voice can exceed the barge-in gate and interrupt itself. Headphones or moderate volume avoid this. The gate is configurable (`VoicePipelineConfig`).
- **Energy endpointing.** It is amplitude, not speech. Steady loud noise can open utterances; those cost a tiny.en pass each, and any text is rejected by the matcher. A quiet speaker may need a closer microphone. `voice_smoke.py mic` prints the level against the -45 dBFS gate.
- **Wake matching is text-based.** Rare phrases that sound alike ("the price of Phoenix") can wake it. It is evidence, never authority: a false wake only opens listening.
- **Spoken answers are a short rendering.** Long answers, code and links are on screen. While a mission runs (THINKING) the microphone is not processed, so voice cannot cancel a mission; the app can.
- **Licence.** Piper is GPL-3.0-or-later (ADR-106); distribution terms need the owner's review before any public release.
- **Hardware.** Live accuracy with real voices and room noise is not measured (no microphone test was run here).
- **Shutdown.** Native inference and playback cannot be pre-empted mid-call. Stop cancels at the next await, and playback stops within one 50 ms block.

## Acceptance

- [x] Concrete wake, STT, TTS, microphone and output adapters behind the PHX-060 to PHX-064 contracts
- [x] Live loop: wake, listen (VAD end of speech), STT, chat mission (same path as `POST /chat`), TTS; barge-in; text fallback; isolated failures
- [x] Authenticated `/voice` routes and allowlisted `voice.*` events
- [x] Unit and integration tests with fake audio and models; skip-marked live test; `scripts/voice_smoke.py`
- [x] Latencies measured on this laptop, CPU only
- [x] Models downloaded by the explicit setup script into `D:\Phoenix\models\voice` and verified
- [ ] Owner reviews R1 to R14 (including the Piper licence, ADR-106)
- [ ] Owner's live microphone run (`voice_smoke.py live`, then the app with `PHOENIX_VOICE_AUTOSTART=1`)
- [ ] Desktop mic toggle and indicator (follow-up)
