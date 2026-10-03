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
