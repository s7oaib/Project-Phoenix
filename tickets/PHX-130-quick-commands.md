# PHX-130 — Quick commands

## Status
DONE (implemented and tested on `feature/PHX-130-quick-commands`; local only,
not pushed)

## Phase
M3 capabilities (final-review follow-up, 2026-10-23)

## Objective
"open browser", "open youtube", "open google", "search youtube for X" and
"search google for X", typed in Chat or spoken after "Rise, Phoenix", open the
link in the user's default browser in about a second, with no AI planning.
Chat answers stay fast (model warm-up and keep-alive).

## Authoritative documents
- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md`
- `DECISIONS.md` ADR-220, ADR-221, ADR-222

## Owner decisions (2026-10-23)
- Open in the normal default browser; PHOENIX only launches a URL.
- Permission: "Always allow" for this narrow action, revocable.

## Requirements
1. Deterministic router before triage, shared by chat and voice.
2. `desktop.open_link` in ToolRegistry, P1, PermissionEngine-decided,
   two-host allowlist, `os.startfile` (no shell, no arguments).
3. First use asks; "always" grant never asks again; revocable.
4. Completed mission with a fixed answer, trace/events, label "quick";
   deterministic LEVI check.
5. Warm the triage/DIRECT model at startup; Ollama `keep_alive` 30m.

## Acceptance
- Router positive/negative and injection-like inputs; URL encoding.
- PermissionEngine path; always grant persists across restart and skips the
  prompt; revoke brings it back.
- Voice transcript uses the same router; non-quick requests unchanged.
- Live: POST /chat "open youtube" and "search youtube for lofi music" with an
  always grant open the browser; latency measured.
