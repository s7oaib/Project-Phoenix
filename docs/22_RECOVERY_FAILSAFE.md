# 22 — Recovery & Fail-Safe

## MAHORAGA

Purpose: bounded adaptive recovery.

May:
- retry with adjusted parameters
- switch tool
- switch provider/model
- choose alternate agent
- re-plan a bounded subgraph
- wait/backoff

Must:
- classify failure
- obey max attempts
- emit recovery events
- re-evaluate permission when scope/risk changes

May not:
- bypass permission
- hide failure
- loop indefinitely
- escalate destructively without approval

## KAIDO

Purpose: essential degraded capability.

Possible triggers:
- primary model provider unavailable
- resource pressure
- deep provider unavailable
- critical subsystem degradation

KAIDO supports basic conversation, diagnostics, recovery guidance, and safe limited tool routing. It is not normal high-quality mode.
