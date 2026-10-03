# 02 — System Flow

## Startup

1. Desktop starts.
2. Backend starts/attaches.
3. Database/migrations validated.
4. Hardware profile loaded.
5. Model providers checked.
6. Tool subsystem checked.
7. Voice subsystem checked.
8. Agent registry loaded.
9. PHOENIX enters `SLEEPING` or `READY_TEXT`.

## Wake

```text
SLEEPING
  |
"Rise, Phoenix"
  |
UTA
  |
PHOENIX initializes
  |
real health checks
  |
READY or DEGRADED
```

## Simple query

User -> PHOENIX -> model -> response.

## Complex query

User -> PHOENIX -> AIZEN -> router -> specialist agents -> LEVI if required -> PHOENIX -> user.

## Tool action

Agent -> ToolRegistry -> PermissionEngine -> execute/approval -> audit -> structured result.

## Failure

Failure -> classify -> MAHORAGA when recoverable -> bounded retry/replan -> PHOENIX; KAIDO only when degraded-mode criteria are met.

## Voice states

`SLEEPING -> LISTENING -> THINKING -> SPEAKING -> LISTENING`

Barge-in cancels speech and returns control to the active conversation.
