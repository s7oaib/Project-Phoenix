# 23 — Observability

## Metrics

- task/step latency
- provider/model
- TTFT
- tokens/sec
- RAM/VRAM/CPU
- disk I/O where available
- tool success/failure
- verification result
- recovery attempts
- permission decisions

## Logs

Structured logs include timestamp, severity, subsystem, correlation ID, task/step IDs, safe message, and error type.

Never log secrets.

## Task trace

A trace should answer:
- what PHOENIX decided
- which agents ran
- which tools ran
- which permissions were requested
- what failed
- whether recovery happened
- what LEVI verified
