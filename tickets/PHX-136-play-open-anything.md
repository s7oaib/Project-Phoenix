# PHX-136 — Play on YouTube, open anything

## Status
DONE (code and tests written on `feature/PHX-136-play-open-anything`; local
only, not pushed; tests not run at the owner's request, ruff and mypy clean)

## Phase
M3 capabilities (final-review follow-up, 2026-10-23)

## Objective
"Play X on youtube" / "play some music" start a video, not a results page;
"open <installed app>" and "open <website>" work for any app or public site,
each behind one P1 "Always allow".

## Authoritative documents
- `/AGENTS.md`, `/RULES.md`, `/INVARIANTS.md`
- `tickets/PHX-130-quick-commands.md`, `tickets/PHX-133-more-quick-commands.md`
- `DECISIONS.md` ADR-250..254

## Requirements
- Play: fetch YouTube's public results page (httpx, 5 s, desktop UA, no API
  key) only after ALLOWED; open the first `videoId` (music: random of top 5,
  random fixed query) as `https://www.youtube.com/watch?v=<id>`; fall back to
  the results page. "search youtube for X" unchanged.
- Apps: Start-menu `.lnk` index scanned at startup; fuzzy name match, tie →
  normal pipeline; `os.startfile(.lnk)` with no arguments.
- Sites: bare domain or fixed known name → `https://<domain>/` only.
- One wide P1 scope per tool; earlier fixed-scope grants stay valid.

## Changed protected sources (pinned at the PHX-136 commit)
- `phoenix/api/missions.py` (matches through the runner, which holds the
  shortcut index); leaves the PHX-133 pin.

## Acceptance
- `tests/test_quick_commands_play_open.py` (fakes only: no launch, no fetch).
- Existing quick-command tests updated: "play X on youtube" now plays,
  "open evil.com" is now a website (refusals use a path or port instead).
