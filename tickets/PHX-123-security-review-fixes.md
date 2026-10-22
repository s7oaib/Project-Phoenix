# PHX-123 — Security review fixes

## Status
DONE (merged in PR #108 as `702a92f`; main CI `test-matrix` run 39708451150 green)

## Phase
M5 release (PHX-094 release evidence)

## Objective
Fix the four Medium findings of the consolidated security review of 2026-10-22
(`docs/security/SECURITY_REVIEW_2026-10-22.md`) and the Lows that are cheap and safe,
with the owner's recommended defaults; defer anything that needs a design change.

## Authoritative documents
- `/AGENTS.md`, `/RULES.md`, `/INVARIANTS.md` (INV-017, INV-018), `/MASTER_SPEC.md`
- `docs/security/SECURITY_REVIEW_2026-10-22.md`
- ADR-136, ADR-145, ADR-150, ADR-151, ADR-173; new ADR-180 to ADR-189

## Requirements (owner's instruction)
1. SR-01: HTTP client loggers at WARNING; no request URL with a query in `phoenix.jsonl`.
2. SR-02: page fetch connects to the vetted address (Host header and TLS name kept) and
   re-vets every redirect hop; fake-resolver tests (public, then private).
3. SR-03: keep `D:\Phoenix\browsers` and its ACLs; the install script writes a SHA-256
   manifest in `%LOCALAPPDATA%\PHOENIX\browser-manifest.json`; PHOENIX verifies before
   launching Chromium and refuses with a clear code on mismatch.
4. SR-04: refuse P4 fill/set_text of secret-looking text with a clear code.
5. Lows where cheap and safe (SR-05, SR-06, SR-07, SR-10, SR-11, SR-12, SR-13, SR-14);
   SR-08 only if straightforward. Deferred items listed with reasons.

## Non-goals
Moving the browser folder or changing ACLs; prompt text changes; Info items other than
SR-14; new dependencies.

## Acceptance
- Every fixed finding has a regression test; the review document has a Status column.
- ADR-180 to ADR-189 recorded; protected-file pins updated (two-commit pattern).
- Full test matrix passes.

## Result
See ADR-180 to ADR-189 and the Status column of the review document.
