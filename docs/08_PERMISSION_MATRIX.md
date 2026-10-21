# 08 — Permission Matrix

## P0 — Internal
No confirmation: planning, inference, internal communication, memory retrieval.

## P1 — Observe
Within granted scope: read selected files, inspect logs, web search, read page, scoped screenshot.

## P2 — Low-risk/reversible
May be auto-approved by policy: open app/URL, scroll, media playback, create new safe output, read-only terminal command.

## P3 — Modify
Requires policy approval or scoped task/session grant: edit/overwrite files, build/test commands, git commit, app config change, install project dependencies.

## P4 — Sensitive/destructive/external
Always explicit approval: delete data, send message/email, upload private files, publish, purchase/payment, admin/root command, system software install, account/security changes.

## P5 — Blocked
Reveal stored credentials, disable permission enforcement, silently upload private data, hide audit history, self-escalate.

## Grant fields

- actor/agent
- action
- resource/scope
- reason/task
- duration: once/task/session/persistent
- risk
- timestamp
- user decision

LLM output can request a grant; it cannot create one.

## Computer-control actions in missions (PHX-112, ADR-150/151)

Risk levels are the tools' canonical declarations; PermissionEngine decides.

| Tool | Action | Level | How a mission gets it |
|---|---|---|---|
| browser | session_open | P2 | user grant (this session / always) |
| browser | navigate | P2 | user grant, per exact URL |
| browser | read_page, inspect_element | P1 | user grant, per exact page and bound |
| browser | click, fill | P4 | one explicit decision on the exact request |
| browser | session_close | P2 | PHOENIX teardown policy rule (ADR-153) |
| windows | list_windows, inspect_window, inspect_control | P1 | user grant |
| windows | focus_window | P2 | user grant |
| windows | invoke_control, set_text, close_window | P4 | one explicit decision |
| screen | capture_window | P1 | user grant; vision text is advisory (P0 inference) |
| terminal | `terminal.inspect` profile | P2 | user grant |
| terminal | `terminal.project` profile | P3 | user grant, this session only |
| terminal | `terminal.sensitive` profile | P4 | one explicit decision |
| any | P5 | P5 | blocked; never reachable |

P3/P4 actions write durable permission and outcome audit rows. A mission runs
at most one P4 action; a P4 decision is used once, for the one linked re-run.
