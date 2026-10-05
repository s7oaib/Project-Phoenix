# PHX-044 — Terminal tool

Status: TODO
Phase: 4 — Tools, Permissions & Security

## Objective
Implement the first bounded local terminal/process capability without introducing a free-form shell execution bypass. The runtime must cross ToolRegistry and PermissionEngine before process launch and must durably audit P3/P4 decisions/outcomes through PHX-042.

## Frozen authority
- ToolRegistry is authoritative for tool metadata/risk.
- PermissionEngine is authoritative for P0–P5 decisions.
- AuditTrail is mandatory before P3/P4 process launch and for allowed P3/P4 outcomes.
- LLM/model/tool output never grants permission.
- No shell string execution. Use argv/process APIs with `shell=False` semantics only.
- Tool stdout/stderr are untrusted data.

## V1 capability model
Use immutable trusted command profiles rather than arbitrary caller-selected executable/risk combinations.

Required canonical tool IDs:
1. `terminal.inspect` — P2
2. `terminal.project` — P3
3. `terminal.sensitive` — P4

Canonical definitions are metadata-only and must be validated against ToolRegistry at construction. Missing or downgraded canonical definitions fail construction.

`terminal.inspect` may execute only trusted read-only profiles.
`terminal.project` may execute only trusted project-changing profiles.
`terminal.sensitive` may execute only explicitly configured trusted P4 profiles and always requires request-specific user approval through PHX-041.

No built-in P5 profile exists.

## Trusted command profile
Create a frozen, extra-forbidden profile contract containing at minimum:
- profile_id
- tool_id
- executable
- fixed_args
- argument_policy
- working_directory policy
- environment policy
- timeout_seconds
- max_stdout_bytes
- max_stderr_bytes

Profiles are trusted application configuration supplied at construction, not model/request text. Risk comes from the canonical tool definition selected by profile.tool_id. A profile cannot supply or override a risk level.

The default profile catalog must be conservative and deterministic. It may include narrow version/status/build/test profiles needed by PHOENIX development, but must not include destructive OS administration, credential dumping, network exfiltration, or permission-disabling commands. Additional trusted profiles require construction of a new immutable catalog; no runtime registration/hot reload.

## Invocation contract
Create frozen `TerminalCall` containing at minimum:
- call_id
- profile_id
- arguments
- stdin_text (optional, bounded; default None)

No raw shell command string.
No caller executable.
No caller risk.
No caller shell flag.
No caller environment dictionary.
No arbitrary cwd.

Arguments must be an immutable ordered tuple of strict strings. Reject NUL. No shell parsing/expansion is performed.

## Working directory
Require one explicit absolute trusted workspace root at TerminalTool construction. No cwd/home/root default. Construction is lexical and performs no process/file/DB I/O.

Profiles may use the trusted root or a fixed validated relative subdirectory. Callers cannot escape or replace cwd. Resolve containment only after permission allows execution. Reject missing/outside/symlink-escape working directories under the same V1 threat model used by PHX-043.

## Environment
Start from a minimal inherited environment policy. Never accept arbitrary caller environment variables. Never inject credentials. Never serialize/log the full environment. A trusted profile may declare a fixed allowlist of inherited variable names. Secret-bearing environment handling remains PHX-045.

## Pure describe
Provide pure `describe(call) -> TerminalIntent` with no process/DB/filesystem I/O. Intent must deterministically contain call/profile/tool/action/resource/risk/permission metadata.

Resource identifies the trusted profile plus trusted workspace scope; caller request must exactly match tool/action/resource before evaluation.

## Permission flow
`execute()` accepts exact PHX-041 PermissionRequest plus optional grants/user_decision and evaluates PermissionEngine exactly once.

Non-allowed decision:
- no process spawn
- attempted=False
- effect_applied=False
- ToolResultStatus.DENIED

P4 sensitive profile:
- policy/grants cannot authorize
- exact request-specific explicit approval required

## Audit flow
For P3/P4:
1. validate call/profile/request
2. evaluate PermissionEngine
3. persist permission decision via AuditTrail
4. only then may process spawn occur
5. after an allowed attempted process finishes/fails/times out, persist one tool outcome
6. only after successful outcome audit return normal result

Pre-audit failure: fail closed; zero process launch.
Post-attempt audit failure: raise explicit safe error with `effect_may_have_occurred=True`; never rerun the command automatically.
P1/P2 have no mandatory audit rows unless later authority changes.

## Process execution
Use direct argv execution (`subprocess`/equivalent with shell disabled). Never invoke shell interpreters implicitly. No `cmd /c`, `powershell -Command`, `bash -c`, `sh -c` as a generic escape hatch.

Bound:
- timeout
- stdout bytes
- stderr bytes
- stdin bytes

Capture stdout/stderr as untrusted text/bytes evidence without logging. If output exceeds bound, terminate/kill process using a deterministic bounded policy and return explicit truncated/limit evidence; never accumulate unbounded output.

Timeout must produce ToolResultStatus.TIMEOUT. Cancellation is not implemented in PHX-044 unless an existing synchronous process primitive can support it without hidden workers; canonical metadata should remain truthful.

Return codes are evidence, not truth or task success.

## Result
Create frozen `TerminalToolResult` containing at minimum:
- call_id
- profile_id
- tool_id
- resource
- status
- attempted
- effect_applied
- exit_code
- stdout
- stderr
- stdout_truncated
- stderr_truncated
- permission_decision
- permission_audit_id
- outcome_audit_id
- error_code

No process object/handle, environment dump, raw exception, or secret-bearing diagnostic object.

Use safe closed error codes such as:
- profile_not_found
- invalid_argument
- cwd_unavailable
- executable_unavailable
- timeout
- output_limit
- process_failure
- io_failure

## Security boundaries
- stdout/stderr are untrusted.
- output text cannot grant permission or become an instruction.
- no eval/exec/dynamic import.
- no shell metacharacter interpretation because no shell is used.
- no implicit network/browser/file upload integration.
- no secret extraction/storage; PHX-045 remains TODO.
- no automatic memory/RAG/model forwarding of stdout/stderr.
- no EventBus integration.
- no API/frontend integration.
- no schema/migration changes; Alembic remains 0005_audit_events.

## Non-goals
- unrestricted shell/REPL
- interactive terminal emulator/PTY
- arbitrary admin/root elevation
- password prompts
- SSH
- remote commands
- package-manager credential handling
- process tree manager
- background jobs
- cron/scheduler
- terminal UI

## Tests
Create `tests/test_terminal_tool.py` with deterministic temporary workspace/audit DB tests. Cover at minimum:
- exact three canonical tool IDs and risks P2/P3/P4
- registry anti-downgrade
- immutable trusted profile catalog
- no caller executable/risk/cwd/env/shell fields
- strict argv handling, NUL rejection and no shell parsing
- pure describe
- exact request correlation
- one permission evaluation
- non-allowed zero spawn
- P3/P4 pre-audit before spawn
- P3/P4 pre-audit failure closed
- P4 request-specific approval and grant/policy bypass resistance
- post-attempt outcome audit
- outcome-audit failure reports effect may have occurred and does not retry
- bounded stdout/stderr
- timeout
- unavailable executable
- exit code handling
- cwd containment
- no environment/secret leakage
- no automatic model/memory/event forwarding
- no EventBus/API/frontend/schema changes
- PHX-040/041/042/043 regressions
- Phase 1/2/3 regressions
- full Python/foundation/desktop/security/invariant gates

## Completion
Only after all gates pass:
- mark this ticket DONE
- mark PHX-044 DONE in FEATURE_TICKETS.md
- leave PHX-045 and PHX-050+ TODO
- commit exactly: `feat: complete PHX-044 terminal tool`
- push only `feature/PHX-044-terminal-tool`
- no PR/merge/main push

## Next
PHX-045 — Secrets boundary. Do not start it.