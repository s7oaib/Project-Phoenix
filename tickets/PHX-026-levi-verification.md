# PHX-026 — LEVI Verification

Status: TODO
Phase: 2 — Orchestration

## Objective
Implement the first provider/tool-independent LEVI verification runtime boundary. LEVI (`verifier`) validates candidate step output against explicit verification criteria and produces immutable verification evidence. Required verification that does not pass must not satisfy the verification gate.

PHX-026 is verification only. It does not repair, recover, re-plan, execute tools/models, grant permissions, transition task state, emit events, persist results, or finalize PHOENIX user-facing success.

## Authority boundaries
- PHOENIX (`phoenix`) remains the sole supreme orchestrator and final user-facing authority.
- LEVI's authoritative internal ID is `verifier`; alias `LEVI` is display-only and removable.
- LEVI verifies; it does not repair by default.
- A verification pass grants no permission and does not authorize any tool/action.
- A failed or missing required verification cannot satisfy the verification requirement.
- MAHORAGA recovery remains PHX-027; KAIDO remains PHX-028.

## Implementation location
Prefer `phoenix/agents/verifier.py` with focused tests in `tests/test_levi_verification.py`.

## Verification request
Create an immutable, extra-forbidden neutral request containing at least:
- `verification_id` — generated UUID4 by default or caller-supplied strict nonempty ID
- `task_id`
- `step_id`
- `subject_agent_id` — authoritative internal ID of the agent whose candidate output is being checked
- `objective`
- `expected_output_schema`
- `completion_rule`
- `candidate_output`

All strings are untrusted data. Meaningful text fields must contain non-whitespace content while preserving meaningful formatting. Do not log them automatically.

## Graph/handoff correlation
The normal LEVI runtime entry point must receive a PHX-024 `TaskGraph` and PHX-025 `HandoffEnvelope` and derive/revalidate request evidence rather than trusting caller overrides.

Require:
- `handoff.task_id == graph.task_id`
- selected graph step exists at `handoff.step_id`
- `handoff.target_agent_id == step.owner_agent_id`
- `handoff.objective == step.objective`
- `handoff.input_refs == step.input_refs`
- `handoff.timeout_seconds == step.timeout_seconds`
- request `subject_agent_id == handoff.target_agent_id`
- request `completion_rule == step.completion_rule`
- request `expected_output_schema == handoff.expected_output_schema`

If correlation fails, fail explicitly. Do not repair or substitute evidence.

## Agent identity validation
Use PHX-020 `AgentRegistry` neutral internal IDs only.
- Construction validates `verifier` exists.
- `subject_agent_id` must exist in the same validated registry snapshot.
- Alias rename/removal does not affect behavior.
- Alias `LEVI` cannot establish verifier authority.

## Verification responder
Define one minimal provider-neutral async collaborator contract, e.g. `VerificationResponder.verify(request) -> VerificationDraft`.

It must expose no Ollama/Colibri/ModelProvider/httpx/model ID/provider ID/tool/permission objects. Tests use deterministic fake responders only.

## Check draft contract
A responder returns a frozen, extra-forbidden `VerificationDraft` correlated to `verification_id` and containing a nonempty ordered tuple of verification checks.

Each check should contain only small neutral evidence such as:
- `check_id` — strict unique nonempty ID
- `criterion` — meaningful text
- `passed` — strict bool
- `evidence_summary` — meaningful safe text

No executable callbacks, raw provider payloads, arbitrary metadata dictionaries, permissions, tools, or repair instructions.

## Exact revalidation
Treat responder output as untrusted runtime data. Revalidate the exact draft and nested check contracts by value. Reject subclasses/extended payloads, correlation mismatch, empty checks, duplicate normalized check IDs, malformed booleans, and extra fields.

## Final LEVI result
Create an immutable `VerificationResult` containing at least:
- `verification_id`
- `task_id`
- `step_id`
- `subject_agent_id`
- `verifier_id` fixed to `verifier`
- `verdict`
- validated ordered checks

Use a small verdict vocabulary:
- `passed`
- `failed`

The runtime computes the verdict itself:
- PASSED only when every validated check has `passed=True`
- FAILED when one or more validated checks has `passed=False`

The responder must not supply an authoritative overall verdict.

A LEVI PASSED result means only that this verification boundary passed its supplied checks. It is not a permission grant and is not PHOENIX user-facing completion.

## Verification requirement gate
Provide a pure helper/result that evaluates only the PHX-025 verification requirement, e.g. `verification_requirement_satisfied(handoff, result)`.

Required semantics:
- `handoff.verification_required == False` and no result: satisfied for the verification requirement only
- required == True and result missing: not satisfied
- required == True and correlated result verdict FAILED: not satisfied
- required == True and correlated result verdict PASSED: satisfied
- if a result is supplied, task/step/subject correlation must be validated; mismatches fail explicitly rather than being ignored

The helper must not transition task state or claim overall task/user success.

## Runtime API
Implement one obvious async LEVI verification entry point, e.g. `await verifier.verify(graph=..., handoff=..., candidate_output=..., responder=..., verification_id=...)`.

Required flow:
1. revalidate exact graph and handoff contracts against the verifier's registry snapshot
2. verify graph/handoff correlation
3. validate/generate verification ID
4. derive the neutral VerificationRequest
5. register the verification as active before awaiting the responder
6. invoke responder exactly once
7. strictly revalidate/correlate the returned draft/checks
8. compute verdict from checks
9. return immutable VerificationResult with `verifier_id="verifier"`
10. cleanup active state in `finally`

## Active verification tracking
Track active verification IDs in memory within one `LeviVerifier` instance.
- distinct IDs may run concurrently
- simultaneous duplicate IDs fail explicitly
- active inspection returns a deterministic immutable tuple of IDs
- do not expose `asyncio.Task` publicly
- IDs may be reused after cleanup
- no hidden worker tasks

If using caller-task ownership for cancellation, reject nested operations that would make request-scoped cancellation ambiguous.

## Cancellation
Provide request-scoped cooperative cancellation with only `accepted` / `not_found` states.
- cancelling A must not cancel B
- `CancelledError` propagates after cleanup
- if a responder suppresses cancellation, the verifier must not finalize a successful verification after local cancellation was requested
- accepted means local cancellation was requested, not proof that remote/model compute stopped

## Failure and cleanup
Responder failures, malformed drafts, correlation failures, validation failures, and cancellation remain explicit. No failure becomes PASSED. Active state must be removed on all terminal paths.

## No repair/recovery
On FAILED verification:
- return FAILED verification evidence
- do not modify candidate output
- do not retry
- do not call AIZEN
- do not call another agent/model/tool
- do not invoke MAHORAGA

PHX-027 owns recovery.

## Task-state separation
Do not modify or automatically transition PHX-024 `TaskStateMachine`.
- running verification does not itself transition to VERIFYING
- PASSED does not transition to COMPLETED
- FAILED does not transition to FAILED/RECOVERING

Later orchestration wiring owns truthful state changes.

## Handoff separation
Do not modify PHX-025 `HandoffEnvelope` or `HandoffBuilder`. `verification_required` is an obligation flag only. PHX-026 consumes it but does not add verification fields to the handoff.

## Security
- Candidate output, objectives, criteria, schemas, completion rules, and evidence summaries are untrusted data.
- No eval/exec/dynamic import/shell interpretation.
- No permission creation or ToolRegistry bypass.
- Verification PASS cannot authorize P3/P4 or override P5.
- No secrets, prompts, outputs, or raw provider payloads are automatically logged or persisted.

## No provider/model/tool execution
PHX-026 must not import concrete Ollama/Colibri/embedding adapters, call ModelProvider, acquire lifecycle leases, discover models, run benchmarks, execute tools, or contact the network. The neutral responder hides future verification implementation.

## No events yet
Do not emit `verification.started`, `verification.passed`, `verification.failed`, task events, or agent events in PHX-026. Do not alter the canonical 32-event vocabulary. Event wiring belongs to later orchestration/application integration.

## No persistence/API/frontend
No Alembic/database tables, files, API endpoints, WebSockets, React/Tauri/Zustand integration, or background workers.

## Side-effect isolation
Importing/constructing contracts/verifier and evaluating the pure requirement gate must perform no network, filesystem writes, SQLite/Alembic access, subprocess/thread creation, hidden asyncio tasks, hardware scan, events, tools, permissions, or model calls. Only explicit `verify()` may await the supplied responder.

## Deterministic tests
At minimum cover:
1. authoritative verifier ID is `verifier`
2. missing verifier internal ID fails construction
3. LEVI alias alone cannot establish authority
4. renamed/removed LEVI alias does not change behavior
5. verification IDs generated uniquely and caller IDs preserved
6. strict request/text validation and immutability
7. exact graph/handoff correlation on task, step, target, objective, input refs, and timeout
8. unknown step and graph/handoff mismatch fail explicitly
9. subject internal ID validated without alias fallback
10. responder called exactly once with derived request
11. exact draft/check revalidation rejects subclasses/extras
12. nonempty checks required
13. duplicate normalized check IDs rejected
14. draft verification-ID correlation required
15. all true checks => PASSED
16. any false check => FAILED
17. check order preserved
18. result `verifier_id` is exactly `verifier`
19. result is immutable and contains no final-user-success or permission authority
20. required=false + no result satisfies only the verification requirement
21. required=true + no result does not satisfy it
22. required=true + FAILED does not satisfy it
23. required=true + PASSED satisfies it
24. gate rejects mismatched task/step/subject result
25. active ID visible while responder suspended
26. active state removed after success/failure/malformed output/cancellation
27. duplicate active verification ID rejected without corrupting original
28. verification ID reusable after cleanup
29. cancel active -> accepted; unknown -> not_found
30. cancellation A does not cancel B
31. responder suppression of cancellation cannot finalize PASSED
32. verifier performs no repair/retry/replan/recovery
33. TaskStateMachine is not mutated
34. PhoenixOrchestrator/AizenPlanner/AgentModelRouter/HandoffBuilder are not invoked
35. no provider/model/lifecycle/tool/permission/event/persistence/API/frontend behavior
36. import/construction isolation passes
37. PHX-020 through PHX-025 regressions remain green
38. Phase-1 regressions remain green
39. Phase-0 foundation regressions remain green
40. exact `/health`, Alembic baseline, and canonical 32 events remain unchanged
41. desktop regressions remain green

Concurrency/cancellation tests must use deterministic `asyncio.Event`/`wait_for` checkpoints, not arbitrary sleeps.

## Dependencies
Expected dependency changes: none. Use Python 3.11, asyncio, Pydantic v2, PHX-020 AgentRegistry, PHX-024 TaskGraph, and PHX-025 HandoffEnvelope only.

## Completion gate
Only after all deterministic requirements and regressions pass:
- mark this ticket DONE
- change only PHX-026 to DONE in `FEATURE_TICKETS.md`
- PHX-027 and PHX-028 remain TODO
- optional single ADR may document the durable verification boundary
- commit exactly: `feat: complete PHX-026 LEVI verification`
- push only `feature/PHX-026-levi-verification`
- do not create PR, merge, modify/push main, or start PHX-027
