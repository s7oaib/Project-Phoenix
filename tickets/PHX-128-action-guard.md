# PHX-128 — Performed-action guard

## Status
DONE (implemented and tested on `fix/PHX-128-action-guard`; awaiting PR review
and the owner's live re-run of demo 6)

## Phase
M3 capabilities (live-run follow-up, 2026-10-22)

## Objective
A computer-control mission must never report a state-changing action (click,
fill/type/set text, invoke, close a window, run a terminal command) that was
not performed. Owner-approved 2026-10-22: code only, no prompt text changes.

## Authoritative documents
- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md`, `/MASTER_SPEC.md`
- `DECISIONS.md` ADR-195 (pattern), ADR-210

## Dependencies
PHX-112, PHX-125a (main 78df9bc).

## Requirements
1. A control step whose objective asks for a state-changing action fails LEVI
   (`deterministic.performed_action`, `verification_failed`, model check
   skipped, no retry) unless a matching control call of that step was ALLOWED,
   attempted, succeeded and applied its effect.
2. The final answer of a mission with control steps, whose request asks for
   such an action, fails the same way unless a matching call of the mission
   did.
3. The check only adds a failure; read-only control steps and other domains
   are unaffected.

## Acceptance
- Captured demo-6 shape (click requested, only navigate + read_page ran) fails
  `verification_failed` without consulting LEVI's model.
- An approved P4 click completes; a performed click never overrides a failing
  model check.
- Read-only and negated ("do not click") control requests complete.
- Final-answer level case fails when no step clicked.
- Text and document missions with action words complete.

## Limitations
- Media play/pause is not covered (media calls record `effect_applied` false
  by design); recorded in ADR-210.
- Keyword detection is English and heuristic; a miss falls back to today's
  behaviour.
