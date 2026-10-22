# PHOENIX v0.1.0 release evidence — 2026-10-22

Part of PHX-094. It backs the statuses in [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md).
Only summarized numbers are recorded here: no raw traces, prompts, answers or private
content. Live runs used the BLAZE-6 laptop (RTX 3050 6 GB, 23.7 GiB RAM, Windows 11)
with Ollama and Qwen3.5 2B/4B/9B. The raw run outputs stay on the owner's machine.

Cells marked **PENDING** are filled in by the release coordinator when the run finishes.

## 1. Test matrix

### Latest `main` CI

| Workflow | Run | Commit | Result |
|---|---|---|---|
| `test-matrix` | 39708451150 | `702a92f` (PR #108) | PASS, 35 m 38 s |
| `release-installer` | 39708451160 | `702a92f` (PR #108) | PASS, 9 m 56 s |
| `test-matrix` | 39702590156 | PR #109 merge | PASS |

`test-matrix` gates in run 39708451150 (`scripts/test_matrix.py`, PHX-090), all PASS:
`uv-sync`, `ruff-check`, `ruff-format`, `mypy`, `mypy-scripts`, `pytest`, `npm-test`,
`typecheck`, `frontend-build`, `tauri-info`, `cargo-check`, `cargo-test`, `uv-build`.

Reproduce: `gh run view 39708451150 --log | grep "matrix:"`.

### Local results

| Command | Result |
|---|---|
| Security-review suites (5 commands, see `docs/security/SECURITY_REVIEW_2026-10-22.md` "Test evidence") | 1,442 passed, 2 skipped; `node tests/desktop_adapters.mjs` 37/37; `cargo test` 23/23 |
| `uv run pytest tests/test_test_matrix.py tests/test_fyp_benchmark.py tests/test_fyp_multi_agent.py tests/test_metric_benchmark.py -q` (PHX-094 docs checks, on this branch) | 74 passed |

## 2. Live acceptance (2026-10-22, BLAZE-6)

Two prompt sets were exercised. `phoenix-prompts-v6` is the set on `main`.
`phoenix-prompts-v7` is the owner-gated PR #111 (PHX-125b), not on `main`.

| Check | Script | Prompts | Result | Status |
|---|---|---|---|---|
| Playwright Chromium install | `scripts/install_control_browser.py --install` | — | installed and detected | PASS |
| Mission smoke (5 runs) | `scripts/phoenix_mission_smoke.py` | v6 | 3/5 completed; 2 `verification_failed` | PARTIAL |
| Mission smoke (5 runs) | same | v7 (PR #111) | 4/5 completed; 1 `verification_failed` | PARTIAL |
| Knowledge smoke (5 runs) | `scripts/phoenix_knowledge_smoke.py --runs 5` | v6 | 2/5 completed | PARTIAL |
| Knowledge smoke (5 runs) | same | v7 (PR #111) | 5/5 completed | PASS (owner-gated) |
| Web research mission | `scripts/research_mission_smoke.py` | v6 | completed, expected fact found, sources cited, 124 s | PASS |
| Document read (PDF, DOCX) | ad hoc live harness (not in the repo) | v6 | 2/2 completed, expected content found | PASS |
| Document create (Markdown) | ad hoc live harness (not in the repo) | v6, v7 | 0/7 files written (`verification_failed` ×5, `responder_output` ×2); no run claimed a file it did not write | FAIL |
| Computer control smoke | `scripts/phoenix_control_smoke.py` | v6 | crashed (Playwright handlers not registered; smoke DB cleanup error) | FAIL, fixed in PHX-124 (PR #109, ADR-190) |
| Computer control smoke re-run | `scripts/phoenix_control_smoke.py` | v6 | **PENDING: control smoke re-run on `main` after PR #109** | PENDING |
| Colibri deep-tier gate | `scripts/colibri_gate.py --io-bench --live` | — | INCOMPLETE: success 100%, accuracy 86%, TTFT median 11.3 s, RAM headroom 5.3 GiB, NVMe 1,367 MB/s all PASS; tokens/s NOT MEASURED (tokenizer throughput bug, fixed in PHX-124) | INCOMPLETE |
| Colibri gate re-run | same | — | **PENDING: Colibri gate re-run result (verdict, tokens/s, VRAM)** | PENDING |
| Demo rehearsal | `scripts/demo_rehearsal.py` | — | **PENDING: live demo rehearsal results (PASS/FAIL/SKIP/MANUAL per demo 1–8)** | PENDING |

## 3. Security review

Source: [`docs/security/SECURITY_REVIEW_2026-10-22.md`](../security/SECURITY_REVIEW_2026-10-22.md).
Fixes: PHX-123, merged in PR #108 (ADR-180–188); deferrals in ADR-189.

| Severity | Found | Fixed | Deferred |
|---|---|---|---|
| Critical | 0 | — | — |
| High | 0 | — | — |
| Medium | 4 (SR-01–SR-04) | 4 | 0 |
| Low | 9 (SR-05–SR-13) | 7 (SR-05, SR-06, SR-07 page fetch, SR-10, SR-11, SR-12, SR-13) | SR-08 (window-handle grant binding), SR-09 (step-output provenance), SR-07 browser ports |
| Info | 14 (SR-14–SR-27) | SR-14 | SR-15–SR-27 |

- SR-03 was fixed differently from the recommendation, by owner decision: the browser
  folder stays at `D:\Phoenix\browsers`, and a per-user SHA-256 manifest is verified
  before launch (ADR-184).
- SR-04: P4 fills of secret-looking text are refused with `secret_text_refused` (ADR-185).
- The review found no permission bypass, no tool outside ToolRegistry/PermissionEngine,
  no token exposed to JavaScript and no destructive installer behaviour.
- SR-17 (the kill switch does not interrupt an in-flight action) is recorded here as a
  known limitation, as the review recommends.

## 4. FYP numbers

Full method and history: [`docs/24_TEST_EVALUATION.md`](../24_TEST_EVALUATION.md).

| Configuration | Run | Accuracy | Success | Latency median | Notes |
|---|---|---|---|---|---|
| (1) best single model | 2026-10-18 | 93% (4B, thinking off) / 100% (4B, thinking on) | — | 0.3 s / 7.2 s | one generation by one model |
| (2) multi-agent, triage | 2026-10-18 | 64% | 69% | 1.1 s | before PHX-109 |
| (2) multi-agent, triage | 2026-10-22 (v6) | 64% (9/14 scored) | 88% excl. fallbacks (14/16 completed, 0 fallbacks) | 1.09 s (0.85–11.6 s) | DIRECT 14/14 completed, 9/12 scored pass; COMPLEX 0/2 completed; MAHORAGA 2 of 3 actions recovered (67%) |
| (3) adaptive deep (Colibri) | — | **PENDING: config (3) numbers, only if the Colibri gate re-run passes** | | | NOT RUNNABLE until the gate passes |

Metric datasets (PHX-117, live LEVI, 2026-10-22, prompts v6):

| Metric | Value |
|---|---|
| Verification catch rate | 95% (19 caught, 1 escaped of 20 faults); deterministic checks 5/5, model 14/15 |
| By fault | wrong answer 7/7, ungrounded claim 5/5, forged tool evidence 3/3, forged verdict 2/2, injected tool evidence 2/3 |
| False rejections | 20% (1 of 5 controls) |
| User interventions | 6 permission prompts over 8 missions (0.75 per mission, no excess over the minimum); 4/8 missions completed |

## 5. Known limitations

1. Live COMPLEX missions are unreliable on the 6 GB GPU: LEVI rejects some correct
   answers (20% false rejections on the controls), and planner/responder output
   sometimes fails the schema. On short tasks, configuration (2) is slower and less
   accurate than the best single small model.
2. Document creation in a live mission does not work yet (0/7). Reading works.
3. Keyboard/mouse fallback for computer control is not built (checklist C4).
4. Image files are not parsed without an injected parser (`parser_unavailable`).
5. Computer control has not passed a live smoke on `main`; the crash fix (PR #109) awaits
   a re-run.
6. The Colibri deep tier (configuration 3) is ungated until the gate re-run finishes.
7. The kill switch does not interrupt an in-flight action; the action completes or times
   out and its result is discarded (SR-17).
8. KAIDO degraded answers report API status `completed`; the answer itself is labelled
   (SR-16).
9. Deferred security findings: SR-07 (browser ports), SR-08, SR-09, SR-15–SR-27 (ADR-189).
10. The installer is unsigned (PHX-092); Windows SmartScreen warns on first run.
11. The prompt fixes that raise knowledge smoke from 2/5 to 5/5 (v7, PR #111) are not on
    `main` until the owner approves them.

## 6. Owner-only items

| # | Item | Required for v0.1.0 | Status |
|---|---|---|---|
| O1 | Record the demo videos ([`docs/DEMOS.md`](../DEMOS.md)) | yes | open |
| O2 | Real-microphone voice test (wake phrase, STT, barge-in) | yes | open |
| O3 | Approve the PR #111 prompt text (`phoenix-prompts-v7`, PHX-125b) | yes | open |
| O4 | Spotify and YouTube API keys | optional | open |
| O5 | Installer code signing | optional | open |
| O6 | Revisit piper-tts GPL-3.0-or-later (ADR-106) before any public release | before public release | open |
