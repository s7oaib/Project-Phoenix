# PHX-133 — More quick commands

## Status
DONE (code and tests written on `feature/PHX-133-more-quick-commands`; local
only, not pushed; tests not run at the owner's request)

## Phase
M3 capabilities (final-review follow-up, 2026-10-23)

## Objective
More instant, deterministic commands (no model), same safety pattern as
PHX-130: links (Wikipedia, GitHub, Gmail, Google Maps, Google News), local
time/date answers, and launching Notepad, Calculator and File Explorer.

## Authoritative documents
- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md`
- `tickets/PHX-130-quick-commands.md`, ADR-220..222
- `DECISIONS.md` ADR-235..239

## Requirements
1. `desktop.open_link` allowlist: exactly the template hosts (+4).
2. Time/date from the local clock, no tool, mission completes at once.
3. `desktop.open_app`: P1, fixed AppName list, absolute system paths resolved
   at startup, `os.startfile` with no arguments, its own single scope.
4. All PHX-130 behaviours kept; strict negatives go to the normal pipeline.

## Changed protected sources (pinned at the PHX-133 commit)
`phoenix/agents/mission_events.py`, `phoenix/agents/runtime.py`,
`phoenix/api/missions.py`, `phoenix/api/routes/missions.py`,
`phoenix/api/routes/permissions.py`, `phoenix/api/server.py`.

## Acceptance
- Router positives/negatives (injection-like text, other hosts, arguments,
  "open notepad and delete ...").
- PermissionEngine path for apps; app "always" grant covers every app and not
  links; missing app fails with nothing launched.
- Clock answer: no tool, no model, `quick_answers` in the trace.
- Fakes only: no real app or browser is launched by tests.
