# PHX-046 — Permissioned tool steps in the PHOENIX mission runtime

Status: TODO (scope approved with PHX-029 on 2026-10-15, Q1/Q10/Q13; starts after PHX-029 merges)
Phase: 4 — Tools, Permissions & Security (integration)

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

## Non-goals

- Write, delete or other P2+ tools.
- Approval UI.
- MAHORAGA/KAIDO.
- Events.
- Persistence.
- API/frontend.

## Completion evidence

To be filled at completion.
