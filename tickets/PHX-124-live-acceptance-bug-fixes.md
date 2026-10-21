# PHX-124 — Live acceptance bug fixes

## Status
IN_PROGRESS (implemented and tested on a local branch; awaiting PR review and a live gate re-run)

## Phase
M5 release hardening

## Objective
Fix the three defects found by the first live acceptance run on BLAZE-6.

## Authoritative documents
- `/AGENTS.md`
- `/RULES.md`
- `/MENTAL_MODEL.md`
- `/INVARIANTS.md`
- `/MASTER_SPEC.md`
- `DECISIONS.md` ADR-079, ADR-120, ADR-190

## Dependencies
PHX-050 (browser tool), PHX-112 (control smoke), PHX-116 (Colibri gate).

## Requirements
1. Browser sessions open with real Playwright (`_open` registers closures, not
   bound methods of the slotted `_Session`); a regression test uses the real
   Playwright handler wrapping.
2. `scripts/phoenix_control_smoke.py` releases its audit DB before temp
   cleanup and always writes its report on Windows.
3. `scripts/colibri_gate.py` measures throughput when Colibri reports no
   usage (tokenizer re-count of the streamed text); a missing measurement stays
   INCOMPLETE. VRAM sampling is reported explicitly.

## Non-goals
No policy, prompt-text, threshold or `phoenix/models/benchmark.py` change. No
Colibri or GPU run.

## Verification
- `uv run pytest tests/test_browser_tool.py tests/test_phoenix_control_smoke.py tests/test_colibri_gate.py`
- Live: real Chromium from `D:\Phoenix\browsers` through `BrowserTool` against
  a `127.0.0.1` `http.server` fixture (session_open success; route closure
  aborted the local request; session_close success). Before the fix the same
  script gave `browser_unavailable`.
- `uv run python scripts/test_matrix.py --allow-dirty`

## Acceptance criteria
- Browser session_open succeeds against real Chromium; offline tests fail on
  the old code (107 failed, 114 errors) and pass on the new.
- Control smoke report is written even if temp cleanup fails.
- Gate: `throughput_method` recorded; no usage and no tokenizer gives
  NOT_MEASURED/INCOMPLETE; VRAM samples and device-wide meaning reported.

## Completion evidence
See ADR-190 and the PR description.
