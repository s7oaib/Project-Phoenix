# PHX-048 — FilesystemTool root hardening

Status: DONE (R1–R7 applied under the owner's overnight instruction of 2026-10-18, "do which is recommended"; the owner reviews them in the PR, and merging confirms them)
Phase: 4 — Tools, Permissions & Security (follow-up from PHX-104, scheduled before PHX-112)
Branch: `feature/PHX-048-filesystem-root-hardening` (local commits only; no push, no PR)
Base: `origin/main` `1b51f26` (PHX-105, PR #79)

## Objective

Make FilesystemTool check its own root on every call, not only when it is constructed, so a root replaced by a symlink or junction after validation is refused instead of followed outside the workspace.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/INVARIANTS.md` (INV-004, INV-017, INV-021), `/MASTER_SPEC.md`
- ADR-089 (the finding, "Follow-up: FilesystemTool root hardening"), `tickets/PHX-104-rest-api-v1.md` (the follow-up text, R23)
- PHX-043 (FilesystemTool), `docs/13_API_CONTRACTS.md` (workspace validation)

## Defect

The workspace root is validated at PATCH, at startup and before each mission (PHX-104). `FilesystemTool._target` then called `root.resolve(strict=True)`, which follows a junction or symlink that replaced the root after validation, so a swap during a mission was still followed outside the workspace.

## Recommendations (R1–R7, applied under the owner's overnight instruction)

- **R1 — One check, every operation.** A new `_checked_root` runs first in `_target`, before anything resolves. `_target` is the only way any operation reaches a target, so `read_text`, `list_directory`, `stat` and the P2–P4 operations (`create_text`, `create_directory`, `write_text`, `delete_file`) are all covered.
- **R2 — Link detection.** `lstat` the root and refuse a symlink, junction or other reparse point through the existing `_link` (`S_ISLNK` or `FILE_ATTRIBUTE_REPARSE_POINT` in `st_file_attributes`). The project runs Python 3.11, so `os.path.isjunction` (3.12+) is not used. The root must also be a directory.
- **R3 — Real path equals the root.** `os.path.realpath(root, strict=True)` must equal the configured root under `os.path.normcase(os.path.normpath(...))`, the normalization the constructor already applies: case-insensitive on Windows, exact elsewhere. Containment is then checked against that real path, as before.
- **R4 — Failure shape (INV-021).** Every refusal, and any `OSError` while checking (for example, a deleted root), is the existing `root_unavailable` code: an allowed, attempted `FAILURE` with `effect_applied=False` and no output. No new error code, status or event; `FilesystemToolResult`'s validator is unchanged. In a mission the document step fails `tool_failed` through the existing tool-step path.
- **R5 — Order unchanged.** The constructor and `describe` still do no I/O. Permission evaluation and the P3/P4 permission audit run before the check, as before, so a refused P3/P4 call records its decision and a `FAILURE` outcome. The per-component `lstat`, link refusal and resolved-containment checks are unchanged.
- **R6 — Non-canonical spellings are refused.** A root configured by an 8.3 short name, a `subst` drive, a path through a symlinked or junctioned ancestor, or (outside Windows) a different case now fails `root_unavailable` on every call, matching PHX-104's `workspace_link` rule. The API stores the canonical real path; pytest's temporary roots are resolved. `scripts/phoenix_mission_smoke.py` (outside this ticket's lane) passes an unresolved `TemporaryDirectory`; it works on the owner's machine, and a one-line `os.path.realpath` there is the recommended follow-up if it is run where the temporary folder has a short-name spelling.
- **R7 — Guards and pins.** `phoenix/tools/filesystem.py` (from `origin/main`) is pinned to the PHX-048 implementation commit (`PHX_048_COMMIT` / `PHX_048_PATHS`, `2e04e22f361f64110742dd8974dece216d567a7f`), in a separate pin commit; it keeps one baseline. The two `git diff origin/main` guards that named it (`tests/test_screenshot_vision_loop.py`, `tests/test_windows_ui_automation.py`) drop it in favour of the hash pin. The PR must merge with a merge commit.

## Requirements

1. `phoenix/tools/filesystem.py`: `_checked_root` and its use in `_target`; the module docstring states the per-call check.
2. Tests in `tests/test_filesystem_tool.py`.
3. Records: ADR-094, this ticket, `FEATURE_TICKETS.md`, `docs/13_API_CONTRACTS.md`.

## Non-goals

Handle-based (race-proof) filesystem access; changing PHX-104's workspace validation or preflight; new error codes or events; changing callers in `phoenix/agents`, `phoenix/api`, `apps/desktop` or `scripts`.

## Tests

- A real NTFS junction swapped in for the root is refused for every operation, both between calls and inside one call (the root is a directory at `lstat` and a junction when its real path is taken): `root_unavailable`, nothing opened or listed, the outside file and the original root unchanged.
- A symlinked root, and a root under a symlinked ancestor, are refused for every operation (skipped without the symbolic-link privilege, as the existing tests are); the same with a real junction on Windows.
- A root deleted after construction fails `root_unavailable` for every operation, nothing is recreated, and P3/P4 calls still record two audit events.
- A case-different but otherwise identical root is accepted on Windows; a normal root works for every operation in sequence.
- Each refused result revalidates as a `FilesystemToolResult`. Existing filesystem, e2e and REST tests are unchanged in behaviour.

## Open issues (for the owner)

- A swap between the root check and a later operation within one call is still outside the V1 threat model; the residual window shrinks from a whole mission to the inside of one call.
- R6's smoke-script follow-up, if wanted.

## Acceptance

- [x] Root checked on every call; refusals are explicit `root_unavailable` failures with no effect and no content
- [x] Targeted tests, ruff, format and strict mypy pass
- [ ] Owner reviews R1–R7
