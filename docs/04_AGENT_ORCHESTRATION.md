# 04 — Agent Orchestration

## Principles

- Use the fewest agents necessary.
- Simple requests can stay inside PHOENIX.
- Complex requests may be decomposed by AIZEN.
- Handoffs are structured, not open-ended agent chat.
- Every step has owner, input, expected output, timeout, and completion rule.
- Parallelism is bounded by dependencies and hardware.

## Task states

`CREATED -> PLANNING -> READY -> RUNNING -> WAITING_PERMISSION -> VERIFYING -> COMPLETED`

Other states:
`FAILED`, `RECOVERING`, `DEGRADED`, `CANCELLED`.

## Handoff envelope

- task_id
- step_id
- source_agent
- target_agent
- objective
- input_refs
- constraints
- expected_output_schema
- timeout
- permission_scope
- verification_required

## Routing inputs

- domain
- complexity
- modality
- tool need
- model capability
- permission risk
- provider health
- resource pressure

## Verification

Required verification failure prevents task success.

## Recovery

MAHORAGA may retry, switch parameters, tool, model/provider, agent, or re-plan a bounded subgraph. It may not grant permissions or hide failure.
