# PHX-046 — Permissioned tool steps in the PHOENIX mission runtime

Status: DONE (`phoenix-prompts-v2` text approved by the owner 2026-10-15)
Phase: 4 — Tools, Permissions & Security (integration)
Branch: `feature/PHX-046-permissioned-tool-steps` (local commits only)
Base: main `be40f1b` (includes PHX-029)

## Objective

Let PHX-029 mission steps use read-only filesystem tools through ToolRegistry and
PermissionEngine. Then add the `end_to_end` test that clears the PHX-090 matrix gap.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md` (especially INV-003/004/005/007/008/017/021), `/MASTER_SPEC.md`
- `docs/07`, `docs/08`, `docs/09`
- PHX-029, PHX-040 to PHX-043, PHX-055; ADR-081

## Approved scope (owner, 2026-10-15)

- **Allowed tools.** Only `filesystem.read_text`, `filesystem.list_directory` and `filesystem.stat` (all P1), under an explicit caller-provided workspace root. Allowlisted domain: `documents` (`document_analyst`).
- **The model only proposes.** It may reply `{"tool_request": {"operation": ..., "relative_path": ...}}`. The runtime builds the `FilesystemCall` and `PermissionRequest`, and `PermissionEngine` decides. Grants come only from caller (user) evidence, never from model text (INV-003).
- **Approval required.** `APPROVAL_REQUIRED` moves the task `RUNNING → WAITING_PERMISSION` and ends the mission as `waiting_permission`, with no effect. There is no interactive approval loop or UI.
- **Tool output is untrusted.** It reaches models only through `PromptInjectionGuard` as `TOOL_OUTPUT` (INV-017).
- **LEVI correlation check.** LEVI adds a deterministic check that the tool evidence correlates (call id, ALLOWED, `effect_applied`).
- **Execution.** `FilesystemTool.execute` is synchronous; run it with `asyncio.to_thread`.
- **Excluded.** P2 and above, terminal, browser, UI automation and media tools.

## Tests

- New file `tests/test_phoenix_mission_e2e.py`.
  - Real components: orchestrator, AIZEN, router, task graph, handoff, LEVI, lifecycle, ToolRegistry, PermissionEngine, FilesystemTool on `tmp_path`, and AuditTrail on temp SQLite. The model is scripted.
  - Cases:
    - a granted read produces a verified result;
    - no grant gives `waiting_permission` and no effect;
    - a P4 or unlisted tool proposal is rejected;
    - injected file content stays inert;
    - model text claiming a grant grants nothing;
    - forged tool evidence fails LEVI.
- Map the file as `integration, end_to_end, permissions, tools, prompt_injection, permission_bypass, forged_tool_results`, and **delete the `[[gap]] end_to_end` block** in `tests/matrix.toml`.
- Live smoke: `scripts/phoenix_mission_smoke.py`, run by the owner and recorded externally. It is not a CI gate.

## Owner decisions (2026-10-15)

Recorded in ADR-082.

| # | Topic | Decision |
|---|---|---|
| 1 | Structure | Edit `phoenix/agents/prompts.py` and `runtime.py`; tool logic in the new `phoenix/agents/tool_steps.py`; `responders.py` stays byte-identical. Re-pin the two edited files to the PHX-046 implementation commit (`PHX_046_COMMIT` / `PHX_046_PATHS`, mirroring PHX-017), as two commits: implementation, then the pin. Merge with a merge commit. |
| 2 | Prompts | `phoenix-prompts-v2`, scoped to tool-step roles: the planner may include workspace file-reading steps; `DOMAIN_DESCRIPTIONS["documents"]`; new roles `document_specialist` and `document_answer`; document-step expected output and completion rule; the final answer may use file evidence. Verifier unchanged. v1 safety language kept; every prompt keeps a `PHOENIX ROLE:` first line. The owner approved the exact text on 2026-10-15. |
| 3 | Tool calls per step | Exactly one (`read_text`, `list_directory` or `stat`). A second request, or one in the answer turn, is not executed. |
| 4 | Grants | `PhoenixRuntime.run(request, path, *, grants=(), session_id=None)`; SESSION or PERSISTENT P1 grants for `document_analyst` with the fixed `DOCUMENT_TOOL_REASON`. Grants come only from the caller. |
| 5 | Waiting | New `MissionKind.WAITING_PERMISSION` with `pending_permission: PermissionRequest` (absolute resource). The task moves `RUNNING → WAITING_PERMISSION` and stays there (non-terminal); a rerun with a grant is a new mission. Traces keep IDs, statuses and hashes only. |
| 6 | Errors | New `tool_rejected` (unlisted, P2+ or P4 operation, or non-P1 `describe()`) and `tool_failed` (FilesystemTool FAILURE); an invalid `tool_request` shape is `responder_output`. No retries. |
| 7 | Audit | P3/P4 policy: no P1 rows from the runtime; the end-to-end test asserts zero rows. `MissionTrace.tool_calls` holds `ToolCallRecord` entries. |
| 8 | Neutrality | `phoenix.tools.filesystem` and `phoenix.tools.registry` allowed only in `runtime.py` and `tool_steps.py`; all other bans kept. |
| 9 | Live smoke | `scripts/phoenix_mission_smoke.py` (owner-run, hashes/lengths/status/timings only, not a CI gate), added to the `mypy-scripts` gate. |

## Non-goals

- Write, delete or other P2+ tools.
- Approval UI.
- MAHORAGA/KAIDO.
- Events.
- Persistence.
- API/frontend.

## Completion evidence

- **Changed files.**
  - `phoenix/agents/prompts.py` (v2), `phoenix/agents/runtime.py`, new `phoenix/agents/tool_steps.py`;
  - `tests/test_phoenix_runtime.py` (v2 version, neutrality rule), new `tests/test_phoenix_mission_e2e.py`;
  - `tests/test_prompt_injection_defenses.py` (second commit: `PHX_046_COMMIT` pins `prompts.py` and `runtime.py` to the implementation commit `95c5afad908933ea92ab61c6c2e5b86fdd04e0e0`; merge with a merge commit);
  - `tests/matrix.toml` (e2e mapping, `[[gap]] end_to_end` removed, smoke script in `mypy-scripts`);
  - new `scripts/phoenix_mission_smoke.py`;
  - `DECISIONS.md` (ADR-082), `FEATURE_TICKETS.md`, `docs/24_TEST_EVALUATION.md`, this ticket.
- **`responders.py`** is byte-identical to `origin/main`.
- **Targeted tests** (offline, scripted model): `tests/test_phoenix_runtime.py` 54 passed; `tests/test_phoenix_mission_e2e.py` 39 passed (36 plus 3 review regression tests). Cases: SESSION and PERSISTENT grants (read, list, stat) complete with `deterministic.tool_evidence` passing; no grant, another session or another file → `waiting_permission`, nothing attempted, file content absent from every prompt, zero audit rows; delete/write/create/unknown operations and path traversal → `tool_rejected` with `execute` never called; injected file instructions stay inside the guarded `TOOL_OUTPUT` item and a follow-up `tool_request` in the answer turn is not executed; grant claims, extra fields and second requests in model text → schema/not-JSON rejection; forged call ID, request ID or grant ID → `verification_failed` without the model verifier; missing or oversized file → `tool_failed`; cancellation during the tool call; one heavy generation at a time; `documents` disallowed without a filesystem tool; non-document grants rejected.
- **No live model calls.** The smoke script was only run with `--dry-run`; the live run waits for the owner.
- **Independent review.** A read-only security review found no security defect: tools run only on PermissionEngine ALLOWED, grants never come from model text, path escapes are blocked by FilesystemTool itself, file content stays out of the trace and errors, and LEVI's evidence check cannot be bypassed. Follow-up commit `93fa3dc` rejects duplicate caller grants before the mission starts (previously an uncoded error mid-mission, fail-closed) and ends a truncated listing as `tool_failed` before the answer turn.
- **Matrix.** `python scripts/test_matrix.py` on commit `413fb27` (clean tree): all 13 gates PASS; pytest 15,876 passed, 0 failed; npm 181/181; `end_to_end` PASS, no gaps remain. After `93fa3dc`: e2e, runtime and guard tests 697 passed; ruff and strict mypy clean. CI reruns the full matrix on the PR.
- **Prompt approval.** On 2026-10-15 the owner reviewed the `phoenix-prompts-v2` changes and answered "Approve + open PR".
