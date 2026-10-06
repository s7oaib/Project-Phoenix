# PHX-044 — Terminal tool

Status: DONE
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

## Completion evidence — 2026-10-06

### Implementation and authority

Added `phoenix/tools/terminal.py` and `tests/test_terminal_tool.py`. Public contracts
are TerminalArgumentPolicy, TerminalErrorCode, TerminalCommandProfile,
TerminalProfileRegistry, TerminalCall, TerminalIntent, TerminalToolResult,
TerminalInputError, TerminalAuditError, TerminalPostExecutionAuditError,
terminal_tool_definitions and TerminalTool.describe/execute. Canonical ordered IDs
remain terminal.inspect P2, terminal.project P3 and terminal.sensitive P4. Complete
ToolRegistry metadata validation prevents downgrade; the immutable explicit catalog
defaults empty and admits no caller risk, executable, cwd, environment or shell.

Profiles declare absolute executables, fixed argv, none/exact-allowlist arguments,
fixed contained cwd, ordinary inherited environment names and strict bounded input,
output and timeout policy. Frozen exact-revalidated contracts reject extras and
stored forgeries. Describe is lexical and pure. Its resource binds profile/cwd plus
the exact configuration, arguments and stdin digest without exposing raw inputs to
audit. Exact request tool/action/resource correlation precedes one PermissionEngine
evaluation. Nonallowed decisions perform no target I/O/spawn and return denied,
attempted=False, effect_applied=False. P3 task/session and request-specific explicit
P4 approval remain PHX-041 authority; P4 grants/policies cannot bypass it.

P3/P4 permission decisions commit before cwd resolution, temporary stdin or launch;
an independent database connection verifies ordering. Required pre-audit failure
launches nothing. Every allowed attempt appends outcome evidence before returning a
normal result, including pre-launch, nonzero, timeout and output-limit failures.
Post-attempt audit failure raises a safe distinct error with call ID and
effect_may_have_occurred=True, without retry or a normal success result. P2 has no
mandatory rows. Output, raw exceptions and full environments never enter audit.

Execution is one synchronous direct child with shell=False, binary pipes, no PTY,
communicate or reader thread. Fair bounded pipe reads use Windows PeekNamedPipe or
POSIX nonblocking os.read. Stdin defaults disabled, with a 64 KiB UTF-8 ceiling and
post-authorization temporary seekable input closed after the attempt. Final argv is
at most 16 KiB; strict argument sequences allow at most 64 strings, each at most
4096 UTF-8 bytes. Timeout defaults to 30 seconds, maximum 300. Each output stream
defaults to 64 KiB, maximum 1 MiB raw captured bytes. Excess/timeout kills the
direct child with bounded best-effort waits; both truncation flags mark incomplete
capture. UTF-8 replacement can expand encoded result text to three times that
ceiling. Zero/nonzero/timeout/missing executable/I/O map to explicit canonical
statuses and the eight closed ticket error codes. Modifying profiles retain
possible-effect evidence after launch, including failures; pre-launch failures and
P2 inspection report no mutation. Exit codes are evidence, not verified task success.

### Commands and results

- `uv run --locked pytest tests/test_terminal_tool.py -q`: **270 passed**, final
  focused run **47.33 s**, no skips. Includes harmless real child execution, literal
  metacharacters, real junction escape rejection, bounded floods on both streams,
  timeout/reaping, unconsumed maximum stdin, UTF-8 replacement, durable audit
  ordering, audit failures/no retry, exact approval binding and output privacy.
- All **27 required suites** ran individually with
  `uv run --locked pytest tests/test_<suite>.py -q`, each exit 0. PHX-043 **649 passed,
  16 skipped**; PHX-042 **302**, PHX-041 **926**, PHX-040 **204** passed. Existing
  symlink skips are Windows privilege limitations; real NTFS junction cases pass.
- Phase 3: memory_schema **244**, pudding_memory_service **167**, document_ingestion
  **300**, vector_store_sqlite **273**, retrieval_context **367**,
  provenance_citations **173**; feature aggregate **1,524**, plus persistence **12**.
- Phase 2: agent_registry **96**, phoenix_orchestrator **101**, aizen_planner **151**,
  agent_model_router **213**, task_graph_state_machine **348**,
  structured_agent_handoffs **214**, levi_verification **242**, mahoraga_recovery
  **790**, kaido_failsafe **574**; aggregate **2,729 passed**.
- Phase 1: model_provider **61**, ollama_provider **108**, model_registry **83**,
  model_lifecycle **54**, embeddings **182**, colibri_provider **194**,
  model_benchmark **130**; aggregate **812 passed**.
- `uv lock`; `uv sync --locked`: PASS, **36 packages**; manifests and lock unchanged.
- `uv run --locked pytest -q`: **7,542 passed, 16 skipped**, **170.79 s**, one existing
  Starlette/httpx deprecation warning. Baseline 7,272 plus 270 admitted tests.
- `uv run --locked ruff check .`: PASS; `uv run --locked ruff format --check .`:
  PASS, **92 files**; `uv run --locked mypy`: PASS, **86 source files**.
- `uv build --out-dir <external-validation>/python-build`: PASS, sdist and wheel;
  validation reports/build archives remain outside the repository.
- `uv run --locked pytest tests/test_bootstrap.py tests/test_api.py
  tests/test_config.py tests/test_persistence.py tests/test_events.py
  tests/test_hardware.py -q`: **126 passed**, existing warning only; exact /health
  and foundation behavior unchanged.
- `npm test`: **4 passed**; `npm run typecheck --workspace @phoenix/desktop` and
  `npm run build --workspace @phoenix/desktop`: PASS; desktop sources unchanged.
- Protected baseline review: **186 files unchanged** after Git normalization.
  Credential/signature scan: **191 repository files**, zero matches. All production
  registry, permissions, audit, filesystem, persistence, migrations, events, agents,
  models, memory/RAG, API/frontend and dependencies match supplied origin/main.
  Alembic heads exactly **[0005_audit_events]** and **EventType count 32**.
  `git diff --check`: PASS; only five intended files enter the final staged review.

### Security, invariants, acceptance and limitations

Reviewed INV-001 through INV-024 and every ticket acceptance boundary: PASS. Process
execution crosses canonical ToolRegistry and deterministic PermissionEngine; P4
remains explicit, P5 blocked/unreachable, and required audit enforces pre/post
ordering. Guarded uncached-module import/construction/describe/denial blocks target
I/O, DB connections, migrations, network, hidden workers and EventBus activity.
Stdout/stderr never acquire approval or instruction authority and are not chained,
logged, persisted to audit, ingested into memory/RAG or sent to models. Existing
core/security production and tests remain unchanged. ADR-049 is the sole new ADR.

Profiles are trusted configuration, not a sandbox: integration owns classification,
non-secret scope, approval authenticity and replay control. Cwd checks follow the
single-user PHX-043 model, without hostile concurrent path replacement protection.
Process effects and audit writes are not atomic. Cancellation, process-tree control,
background jobs and interactive interpreters are outside V1. Native process creation
is not preemptible; failed OS cleanup is best effort. Windows behavior is exercised;
POSIX/macOS branches are declared but not executed on this host. PHX-045 retains
secret handling; no secret store, EventBus/API/frontend bridge, migration or new
dependency is introduced.

All deterministic implementation, regression, security and invariant gates PASS.
Final staged review, exact feature commit and normal push are recorded in the
execution report. PHX-040 through PHX-044 are DONE. PHX-045 and PHX-050+ remain TODO.
Next: **PHX-045 — Secrets boundary; not started**.
