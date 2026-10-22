# PHOENIX v0.1.0 release checklist (Full PRD v1)

Part of PHX-094. This is the definition of "production done" for PHOENIX v0.1.0
(`pyproject.toml` version `0.1.0`). One row per must-have in [`PRD.md`](../../PRD.md)
"V1 requirements" and per protected-architecture rule in [`AGENTS.md`](../../AGENTS.md).
The numbers behind each row are in
[`RELEASE_EVIDENCE_2026-10-22.md`](RELEASE_EVIDENCE_2026-10-22.md).

Status values:

- **DONE**: built, on `main`, covered by the CI test matrix, and live-checked where a
  live check applies.
- **DONE-WITH-LIMITS**: built and tested; a named limit applies (live result below
  target, part of the scope not built, or an owner setup step).
- **OPEN**: not built, or not working live. Not release-ready as claimed.
- **MANUAL**: needs a check that only the owner can perform (hardware, accounts,
  approval, recording).

Baseline: `main` at `702a92f` (PR #108), CI `test-matrix` run 39708451150 and
`release-installer` run 39708451160, both green.

v0.1.0 is release-ready when every row is DONE, DONE-WITH-LIMITS with the limit stated
in the release notes, or MANUAL with the owner's sign-off, and no row is OPEN without an
explicit owner deferral.

## PRD V1 requirements

### Voice

| # | Requirement | Status | Evidence |
|---|---|---|---|
| V1 | Wake phrase default `Rise, Phoenix` | DONE-WITH-LIMITS | PHX-060, PHX-114 (ADR-105); `tests/test_wake_word_provider.py`, `tests/test_voice_pipeline.py`. Limit: not yet tested with a real microphone (owner item O2). |
| V2 | Local-first STT where practical | DONE-WITH-LIMITS | PHX-062, PHX-114: faster-whisper on the CPU (ADR-105); `tests/test_voice_pipeline.py`. Limit: real-mic test pending (O2). |
| V3 | VAD and barge-in | DONE-WITH-LIMITS | PHX-061, PHX-064, PHX-114; `tests/test_voice_conversation.py`, `tests/test_voice_pipeline.py`. Limit: real-mic test pending (O2). |
| V4 | TTS through a provider abstraction | DONE-WITH-LIMITS | PHX-063, PHX-114: Piper behind the TTS provider (ADR-106). Limit: piper-tts is GPL-3.0-or-later; revisit before a public release (O6). |
| V5 | Text fallback if voice fails | DONE | Text chat is the default input (PHX-081, PHX-107); the desktop voice toggle is optional (PHX-122, ADR-170). Rise-Phoenix degraded policy (PHX-065, ADR-062). |

### Orchestration

| # | Requirement | Status | Evidence |
|---|---|---|---|
| O1 | Direct path for simple tasks | DONE | PHX-105 triage; `tests/test_request_triage.py`; FYP config (2) 2026-10-22: 14/14 DIRECT missions completed, median 1.09 s. |
| O2 | AIZEN planning for complex tasks | DONE-WITH-LIMITS | PHX-022, PHX-029, PHX-121 combined planner; `tests/test_aizen_planner.py`. Limit: live COMPLEX missions fail often on the 6 GB GPU (config (2) 2026-10-22: 0/2 COMPLEX completed; mission smoke 3/5 on `main`). The PHX-121 v6 text and the v7 prompt fixes (PR #111) await owner approval (O3). |
| O3 | Bounded specialist roles | DONE | PHX-020, PHX-025; `tests/test_agent_registry.py`, `tests/test_phoenix_orchestrator.py`. |
| O4 | Permission checks before actions | DONE | PHX-041, PHX-046; `tests/test_permission_engine.py`; security review 2026-10-22: no permission bypass found. |
| O5 | LEVI verification when required | DONE-WITH-LIMITS | PHX-026, PHX-118 (ADR-100); live catch rate 95% (19/20). Limit: 20% false rejections (1 of 5 controls) and LEVI rejects some correct live answers. |
| O6 | MAHORAGA bounded recovery | DONE | PHX-027, PHX-109 (ADR-093); `tests/test_mahoraga_missions.py`; config (2) 2026-10-22: 2 of 3 recovery actions recovered. |
| O7 | KAIDO degraded essentials | DONE-WITH-LIMITS | PHX-028, PHX-119 (ADR-155–158); `tests/test_kaido_missions.py`. Limit: degraded answers settle with API status `completed` (SR-16, deferred, ADR-189). |

### Models

| # | Requirement | Status | Evidence |
|---|---|---|---|
| M1 | Ollama default provider | DONE | PHX-011, PHX-100; `tests/test_ollama_provider.py`; every live run on 2026-10-22 used Ollama (Qwen3.5 4B/9B). |
| M2 | Colibri optional deep provider | DONE-WITH-LIMITS | PHX-015, PHX-116 (ADR-120); `tests/test_colibri_provider.py`, `tests/test_colibri_gate.py`. Live gate 2026-10-22: INCOMPLETE (throughput not measured; fixed in PHX-124). **PENDING: Colibri gate re-run result (PASS / FAIL / INCOMPLETE).** |
| M3 | Routing uses capability and hardware state | DONE | PHX-007 BLAZE-6, PHX-012, PHX-023; `tests/test_hardware.py`, `tests/test_agent_model_router.py`, `tests/test_model_registry.py`. |

### Memory/RAG

| # | Requirement | Status | Evidence |
|---|---|---|---|
| R1 | Working, episodic, semantic, user, project and agent memory | DONE | PHX-030, PHX-031; `docs/10_MEMORY_SYSTEM.md`; `tests/test_memory_schema.py`, `tests/test_pudding_memory_service.py`. |
| R2 | User-visible memory controls | DONE-WITH-LIMITS | PHX-085, PHX-122 files and memory view (ADR-170–174). Limit: the owner's live check in the desktop app is pending (PHX-122). |
| R3 | Document indexing/retrieval | DONE-WITH-LIMITS | PHX-032–035, PHX-110 (ADR-130–134); `tests/test_knowledge_missions.py`. Live knowledge smoke: 2/5 on `main` (prompts v6), 5/5 with the v7 fixes in PR #111 (owner approval O3). |

### Computer

| # | Requirement | Status | Evidence |
|---|---|---|---|
| C1 | Browser automation | DONE-WITH-LIMITS | PHX-050, PHX-112 (ADR-150–154), PHX-124 (ADR-190); `tests/test_browser_tool.py`. Playwright Chromium install: PASS. Live control smoke 2026-10-22 crashed; the fix merged in PR #109. **PENDING: control smoke re-run on `main`.** |
| C2 | App/window control | DONE-WITH-LIMITS | PHX-051, PHX-112 (ADR-052, ADR-152); `tests/test_windows_ui_automation.py`. Limit: same pending live control smoke as C1. |
| C3 | Screenshot/vision | DONE | PHX-052 (ADR-053); `tests/test_screenshot_vision_loop.py`. Vision output is advisory only. |
| C4 | Keyboard/mouse fallback | OPEN | No raw keyboard or mouse tool exists. Windows control uses native UIA patterns only, and ADR-052 deliberately avoids pywinauto keyboard fallback. Needs an owner decision: build it, or defer it from v0.1.0. |
| C5 | Risk-based approvals | DONE | PHX-041 P0–P5, PHX-084, PHX-122 approval summaries; SR-04 fixed (P4 fills of secret-looking text refused, ADR-185). |

### Documents/media

| # | Requirement | Status | Evidence |
|---|---|---|---|
| D1 | Read PDF/DOCX/TXT/Markdown/CSV/images/code where supported | DONE-WITH-LIMITS | PHX-032, PHX-072, PHX-113; `tests/test_document_ingestion.py`, `tests/test_upload_parser_routing.py`. Live document read: PASS (PDF and DOCX). Limit: images need an injected parser and otherwise return `parser_unavailable` (`docs/19_DOCUMENT_SYSTEM.md`). |
| D2 | Generate PDF/DOCX/Markdown/CSV reports | OPEN | PHX-073 generators (`tests/test_document_generation.py`) and `document.create` for Markdown/DOCX/CSV. Live document-create mission: FAIL (0 files written in 7 runs; no false success claimed). PDF is not offered as a mission output. |
| D3 | Spotify control and built-in YouTube experience | MANUAL | PHX-070, PHX-071, PHX-113 (ADR-063, ADR-064, ADR-140); `tests/test_spotify_integration.py`, `tests/test_youtube_integration.py`. Live use needs the owner's Spotify and YouTube keys (O4, optional). |

## Protected architecture (AGENTS.md)

| # | Rule | Status | Evidence |
|---|---|---|---|
| A1 | PHOENIX remains supreme orchestrator | DONE | PHX-021, PHX-100; `tests/test_phoenix_orchestrator.py`; security review: "Protected-architecture items hold". |
| A2 | AIZEN plans but does not become supreme authority | DONE | PHX-022, PHX-121; `tests/test_aizen_planner.py`. |
| A3 | PermissionEngine is deterministic | DONE | PHX-041; `tests/test_permission_engine.py`. |
| A4 | Frontend never calls Ollama/Colibri/privileged OS tools directly | DONE | CSP `connect-src ipc:` in `apps/desktop/src-tauri/tauri.conf.json`; Rust proxy (PHX-106/107); `tests/workspace.test.mjs`, `tests/desktop_adapters.mjs`. |
| A5 | Tools go through ToolRegistry | DONE | PHX-040; `tests/test_tool_registry.py`; security review: no tool reachable outside ToolRegistry/PermissionEngine. |
| A6 | LEVI can gate completion | DONE | PHX-026, PHX-118; `tests/test_levi_verification.py`; live: 19 of 20 injected faults rejected. |
| A7 | MAHORAGA recovery is bounded | DONE | PHX-027, PHX-109 (ADR-093); `tests/test_mahoraga_recovery.py`, `tests/test_mahoraga_missions.py`. |
| A8 | KAIDO is degraded/fail-safe mode only | DONE | PHX-028, PHX-119; `tests/test_kaido_failsafe.py`, `tests/test_kaido_missions.py`. |

## Release gates

| # | Gate | Status | Evidence |
|---|---|---|---|
| G1 | CI test matrix green on `main` | DONE | `test-matrix` run 39708451150 on `702a92f`: all 13 gates PASS. |
| G2 | Installer builds on `main` | DONE-WITH-LIMITS | `release-installer` run 39708451160 green; PHX-092 (ADR-125–129). Limit: unsigned installer (O5, optional). |
| G3 | Security review: no open Critical/High; Mediums fixed | DONE | `docs/security/SECURITY_REVIEW_2026-10-22.md`; PHX-123 merged in PR #108 (ADR-180–189). |
| G4 | FYP numbers recorded | DONE | `docs/24_TEST_EVALUATION.md`; config (1) and (2) measured; config (3) waits on M2. |
| G5 | Demo rehearsal | MANUAL | PHX-093, `docs/DEMOS.md`, `scripts/demo_rehearsal.py`. **PENDING: live demo rehearsal results.** |
| G6 | Demo videos recorded | MANUAL | `docs/DEMOS.md` (O1). |
