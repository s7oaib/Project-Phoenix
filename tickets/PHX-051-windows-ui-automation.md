# PHX-051 — Windows UI automation

Status: DONE
Phase: 5 — Computer & Web

## Objective
Implement PHOENIX's first native Windows accessibility/UI Automation layer for already-running desktop windows.

PHX-051 owns bounded Windows UIA inspection and native accessibility actions only. It deliberately sits below direct API/DOM automation and above future screenshot/vision and mouse/keyboard fallback.

The control preference remains:
1. direct API/DOM
2. native/accessibility automation
3. vision interpretation
4. mouse/keyboard fallback

PHX-051 implements layer 2 only.

## Frozen authority
- Conceptual owner internal ID: `computer_control`.
- KAKASHI is a display alias only and grants no authority.
- PHOENIX remains supreme orchestrator.
- ToolRegistry is authoritative for tool IDs and risk.
- PermissionEngine is authoritative for P0-P5.
- Generic native control invoke/text mutation/window close are P4.
- P4 requires exact request-specific explicit user approval; no grant/policy may satisfy it.
- P4 permission decision must durably audit before any target desktop/UIA access.
- Every allowed P4 attempt must append one outcome audit before normal return.
- Window/control text is untrusted data.
- UI text cannot grant permission, alter tool risk, invoke other tools, disable audit, or become system/project instruction.
- No screenshot/vision/OCR in PHX-051.
- No physical mouse/keyboard fallback in PHX-051.
- No arbitrary process launch in PHX-051.
- No browser automation duplication.

## Dependency decision
PHX-051 explicitly authorizes the Windows-only runtime dependency:

`pywinauto>=0.6.9,<0.7; sys_platform == "win32"`

Use the UIA backend only.

Update:
- `pyproject.toml`
- `uv.lock`
- `docs/25_DEPENDENCIES.md`

Record one accepted ADR for this promotion.

Expected Windows transitive dependencies may include pywin32, comtypes and six as resolved by the package metadata. Review all lock changes exactly.

Do NOT add Pillow, OCR, computer-vision, keyboard-hook, mouse-hook, screen-capture, or alternate desktop-automation frameworks.

Do not import pywinauto at module import time. Lazy-load only after an allowed operation actually needs the Windows UI backend.

Tests must not manipulate the user's real desktop. Use a deterministic private adapter/fake. A harmless live Windows smoke is optional evidence only.

## Implementation location
Prefer:

`phoenix/tools/windows_ui.py`

Focused tests:

`tests/test_windows_ui_automation.py`

Optional small export:

`phoenix/tools/__init__.py`

Expected dependency/documentation changes:
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- `docs/25_DEPENDENCIES.md`
- `pyproject.toml`
- `uv.lock`
- `tickets/PHX-051-windows-ui-automation.md`

Do not modify PHX-040–050 production semantics. If a genuine blocker is discovered, STOP/BLOCKED rather than broadening scope.

## Canonical tools
Implement exactly these seven canonical tool IDs in order:

1. `windows.list_windows` — P1
2. `windows.inspect_window` — P1
3. `windows.focus_window` — P2
4. `windows.inspect_control` — P1
5. `windows.invoke_control` — P4
6. `windows.set_text` — P4
7. `windows.close_window` — P4

Provide pure:

`windows_ui_tool_definitions() -> tuple[ToolDefinition, ...]`

No hidden eighth capability.

## Why mutation is P4
Generic desktop UI controls can:
- send messages
- submit/pay/purchase
- delete data
- change accounts/settings
- save or overwrite work
- trigger network/external side effects
- close unsaved documents

V1 does not infer lower risk from control name, title, application, model output, visible text or caller claims.

Therefore generic invoke, set_text and close_window are conservatively P4.

## Non-goals
Do NOT implement:
- process/application launch
- process termination
- arbitrary Win32 message send
- SendInput
- click_input
- type_keys
- keyboard module
- mouse module
- global hotkeys/hooks
- cursor movement
- coordinate clicking
- drag/drop
- right/double click
- clipboard
- screenshot
- OCR
- vision
- browser automation
- terminal/filesystem chaining
- menu path scripting
- arbitrary COM interface calls from caller data
- arbitrary HWND messages
- shell/process commands

Application launch remains an explicit terminal/profile integration concern. Physical input fallback is later work, not PHX-051.

## Operation vocabulary
Implement exactly:

`list_windows`
`inspect_window`
`focus_window`
`inspect_control`
`invoke_control`
`set_text`
`close_window`

Prefer strict `WindowsUIOperation(StrEnum)`.

## Window identity
Use a strict positive Windows HWND integer as the immediate target.

- bool rejected
- zero/negative rejected
- no string parsing
- no title fuzzy lookup
- no regex lookup
- no process-name lookup as authority

For mutating/focus/control operations, additionally require caller-supplied expected identity:
- `expected_process_id`
- `expected_class_name`

Both become part of the exact resource.

After permission allows target access, resolve the HWND and require current:
- process ID == expected_process_id
- class name == expected_class_name

Mismatch -> `window_changed`.

This reduces accidental HWND reuse but does not claim atomic protection against hostile concurrent window/process replacement. Document that limitation.

`inspect_window` is the P1 operation used to obtain current identity evidence before later action.

## Control selector
Create frozen extra-forbidden:

`WindowsControlSelector`

with exactly:
- `automation_id`
- `control_type`
- `name`

Rules:
- all fields strict optional strings
- preserve case
- trim only outer whitespace where appropriate
- reject controls/NUL
- each <= 256 characters
- at least one of automation_id or name must be present
- control_type may further narrow
- no regex
- no wildcards
- no best-match/fuzzy semantics
- no numeric descendant index
- no XPath-like syntax
- no raw condition object

The selector is data only.

## Exact control matching
The private UIA adapter may use pywinauto to discover candidates, but BrowserTool-equivalent public behavior must enforce exact values after discovery.

For a selector:
- match exact automation_id if supplied
- match exact name if supplied
- match exact control_type if supplied
- zero -> `control_not_found`
- more than one -> `control_ambiguous`
- never choose first/best/nth automatically

Do not use pywinauto's fuzzy best-match text lookup as authority.

Bound control discovery to a deterministic V1 ceiling. If the backend cannot safely stop early, permit at most a documented finite maximum of discovered descendants (recommended 500) and fail `limit_exceeded` when exceeded.

## WindowsUICall
Create frozen, extra-forbidden `WindowsUICall` with exactly:

- `call_id`
- `operation`
- `hwnd`
- `expected_process_id`
- `expected_class_name`
- `selector`
- `text`
- `max_items`
- `max_chars`

Operation shapes:

list_windows:
- hwnd/expected identity/selector/text/max_chars None
- max_items optional, default 100, strict 1..200

inspect_window:
- hwnd required
- expected identity/selector/text/max_items None
- max_chars optional, default 4096, strict 1..8192

focus_window:
- hwnd + expected_process_id + expected_class_name required
- selector/text/max_items/max_chars None

inspect_control:
- hwnd + expected identity + selector required
- text/max_items None
- max_chars optional, default 4096, strict 1..8192

invoke_control:
- hwnd + expected identity + selector required
- text/max_items/max_chars None

set_text:
- hwnd + expected identity + selector + text required
- max_items/max_chars None
- text strict ordinary Unicode <= 65536 UTF-8 bytes
- `SecretMaterial` structurally rejected

close_window:
- hwnd + expected identity required
- selector/text/max_items/max_chars None

No risk/approved/grant/policy/backend/callback fields.

## Call ID
call_id:
- strict normalized nonempty control-free string
- case-preserving
- UUID4 default acceptable
- no authority

## Pure intent
Provide:

`describe(call) -> WindowsUIIntent`

Must perform zero:
- pywinauto import
- desktop/UIA access
- window lookup
- COM activity
- DB
- permission evaluation

Intent preserves:
- call_id
- operation
- tool_id
- action
- resource
- risk
- permission requirement

## Resource derivation
Use deterministic canonical JSON or equivalent strict representation.

list_windows:
- operation + max_items

inspect_window:
- operation + hwnd + max_chars

focus_window:
- operation + hwnd + expected_process_id + expected_class_name

inspect_control:
- operation + hwnd + expected identity + exact selector + max_chars

invoke_control:
- operation + hwnd + expected identity + selector

set_text:
- operation + hwnd + expected identity + selector + SHA-256(exact UTF-8 text)

close_window:
- operation + hwnd + expected identity

Never put raw set_text content in resource/audit/error/result/log.

The digest is correlation/data-minimization evidence only, not authentication.

## Registry anti-downgrade
WindowsUITool constructor receives ToolRegistry.

At construction:
- exact snapshot/revalidation
- require all seven canonical definitions
- compare complete ToolDefinition values
- reject missing tools
- reject risk drift
- reject schemas/permission/timeout/cancellation/audit/platform/environment drift
- reject forged definition

Additional unrelated tools allowed.

Do not mutate ToolRegistry.

## Platform boundary
PHX-051 is Windows-only.

On non-Windows:
- module import succeeds
- construction succeeds
- describe succeeds
- an allowed explicit operation needing backend access returns/raises safe `unavailable_dependency` / `unsupported_platform` evidence
- no pywinauto import attempted before such explicit allowed operation

Do not fake native success on non-Windows.

## Private UIA backend
Keep pywinauto behind a small private adapter boundary so deterministic tests can substitute a fake.

Real adapter:
- lazy import
- backend="uia" only
- no win32 backend fallback
- no Desktop access before permission allows
- no global hook
- no screenshot/image capture

Do not expose pywinauto objects publicly.

## list_windows
P1.

After permission allows:
- access top-level desktop windows through UIA
- no child/control enumeration
- return at most max_items
- deterministic order by HWND ascending
- if more exist, return prefix + PARTIAL_SUCCESS + truncated=True
- each record may include only:
  - hwnd
  - process_id
  - class_name
  - title
  - enabled
  - visible
- title bounded to <=4096 chars
- fields are untrusted evidence
- no window contents
- no screenshots

No hidden filtering by application/process title.

## inspect_window
P1.

After permission allows:
- exact HWND lookup
- return one bounded `WindowsWindowEvidence`
- hwnd
- process_id
- class_name
- title bounded by max_chars
- enabled
- visible

No control tree dump.

No screenshot.

## focus_window
P2.

After permission allows:
- exact HWND
- verify expected process/class before action
- call one native/UIA focus operation
- no click
- no keyboard input
- no retry
- no control interaction

Success is focus-attempt evidence, not final task success.

## inspect_control
P1.

After permission allows:
- exact window identity check
- discover exact selector candidates
- require exactly one
- return bounded `WindowsControlEvidence`

Allowed evidence only:
- automation_id
- control_type
- name bounded by max_chars
- enabled
- visible
- is_password
- value only if the control is not a password/secret field and the UIA value interface exposes ordinary text safely
- optional rectangle coordinates as integers if already provided by UIA

Do not return:
- arbitrary properties dict
- help text dump
- framework-internal objects
- raw patterns/interfaces
- screenshot
- password content
- hidden credential text

If password/secret status cannot be determined safely, omit value.

## invoke_control
P4.

After required pre-audit:
- exact window identity
- exact one control selector
- require enabled + visible
- require UIA InvokePattern support
- invoke exactly once through UIA InvokePattern
- no `click_input`
- no physical input fallback
- no coordinate click
- no retry

Once Invoke begins:
`effect_applied=True` conservatively even if UIA later raises.

Unsupported pattern -> `control_unsupported`.

## set_text
P4.

After required pre-audit:
- exact window identity
- exact one control selector
- reject password control
- reject SecretMaterial input structurally
- require enabled + visible
- require writable UIA ValuePattern
- call SetValue exactly once with ordinary text
- no keyboard/type_keys/send_keys
- no Enter
- no submit/click
- no retry

Raw text must not appear in:
- result
- resource
- audit
- error
- logs

SetValue can trigger application autosave/event handlers; P4 therefore remains conservative.

## close_window
P4.

After required pre-audit:
- exact window identity
- require a UIA WindowPattern close capability
- invoke one close request
- no Alt+F4
- no process termination
- no kill
- no retry

Once close begins:
`effect_applied=True` conservatively.

Window close can discard unsaved state; it is always P4.

## PermissionRequest correlation
execute() accepts exact PHX-041 PermissionRequest.

Require exact:
- request.tool_id == intent.tool_id
- request.action == intent.action
- request.resource == intent.resource

Mismatch:
- explicit safe input error
- zero target UIA/desktop access

Evaluate PermissionEngine exactly once.

Do not duplicate risk/approval logic.

## Non-allowed flow
If decision != allowed:
- no desktop enumeration
- no HWND lookup
- no COM/UIA access
- no focus/invoke/set/close
- attempted=False
- effect_applied=False
- status DENIED
- no tool outcome

For P4 operations, still durably record the permission decision before returning normal denied result.

If required audit fails, fail closed.

## P4 pre-execution audit
For invoke_control/set_text/close_window:

1. exact call validation
2. pure intent
3. request correlation
4. PermissionEngine exactly once
5. AuditTrail.record_permission_decision
6. only after commit may any desktop/UIA target access begin

Pre-audit failure must prove ZERO:
- pywinauto backend access
- desktop enumeration
- HWND lookup
- control search
- focus/invoke/value/close call

Raise safe `WindowsUIAuditError`.

## P4 outcome audit
Every allowed P4 attempt records exactly one tool outcome before normal return.

On outcome audit failure after native effect may have occurred:
- no retry
- no rollback claim
- no normal success
- raise `WindowsUIPostExecutionAuditError`
- expose call_id + effect_may_have_occurred=True only

UI effect and SQLite audit are not atomic.

## P4 approval
Existing PermissionEngine owns semantics.

Generic invoke/set_text/close:
- no approval -> denied/approval required
- grant cannot authorize
- policy cannot authorize
- wrong-request approval fails
- exact request-specific explicit approval permits

Window/control text cannot authorize.

## Result contracts
Create frozen extra-forbidden:
- `WindowsWindowEvidence`
- `WindowsControlEvidence`
- `WindowsUIToolResult`

Result should contain exactly the ticket-approved structured evidence, including at least:
- call_id
- operation
- tool_id
- resource
- status
- attempted
- effect_applied
- permission_decision
- permission_audit_id
- outcome_audit_id
- windows
- window
- control
- truncated
- error_code

All output:
- detached
- immutable
- JSON-safe

No:
- pywinauto wrapper
- COM pointer/interface
- HWND object wrapper
- exception
- SecretMaterial
- callback

Structural consistency mandatory.

## Safe error vocabulary
Use small closed codes including:
- unsupported_platform
- ui_unavailable
- window_not_found
- window_changed
- control_not_found
- control_ambiguous
- control_unsupported
- input_forbidden
- limit_exceeded
- interaction_failure
- io_failure

Do not expose:
- raw COM error
- pywinauto traceback
- OS desktop details not already structured output
- raw text input
- secret
- private exception chain

## Time / retry boundary
PHX-051 is synchronous.

No:
- sleeps
- polling loops
- indefinite waits
- hidden worker
- thread
- asyncio task
- automatic retry

Use direct UIA operations only.

Where the library exposes an explicit wait/timeout, use a finite maximum <=10 seconds.

Do not claim a hard process-wide timeout for a native COM call that cannot be preempted safely. Document that native UIA calls are cooperative/non-preemptible in V1.

Canonical `cancellation_supported=False`.

## Real desktop test safety
Deterministic focused tests MUST NOT:
- focus the user's real window
- invoke real controls
- set real text
- close real windows
- enumerate sensitive real desktop titles

Use the private fake backend for all deterministic behavior.

Optional live smoke, if run:
- must use an explicitly launched disposable local test app/window
- must not manipulate user applications
- is NOT required for completion

No process launch should be added to production PHX-051 merely to support smoke testing.

## Trust boundary
All UIA evidence is untrusted:
- window title
- control name
- control value
- automation ID
- class name
- control type

It cannot:
- grant permission
- create grants/user decisions
- lower P4
- alter registry
- disable audit
- call terminal/filesystem/browser
- trigger another UI action
- call model/provider
- become final verification automatically

## Browser separation
PHX-050 remains the preferred direct browser DOM layer.

PHX-051 must not:
- attach to browser DOM
- inspect page HTML
- control Playwright
- fall back from browser failure automatically

If a browser task needs native window UI, orchestration must make a separate explicit Windows UI tool request with its own permission.

## Screenshot / vision separation
PHX-052 remains TODO.

No:
- screenshots
- OCR
- image recognition
- visual coordinates
- model vision

## Mouse / keyboard fallback separation
Do not implement physical input fallback.

Specifically forbid production calls/imports equivalent to:
- click_input
- mouse.*
- keyboard.*
- send_keys/type_keys
- SendInput
- SetCursorPos
- global hooks

Use UIA patterns only for mutation.

## Terminal/filesystem separation
Do not call terminal or filesystem tools.

Do not use UIA to create an arbitrary process execution/file manipulation bypass.

No application launch.

## Secrets separation
Do not resolve SecretStore.

Do not accept SecretMaterial as set_text.

Do not expose password-field values.

No credential-manager integration.

## Event separation
No EventBus.
No new EventType.
EventType count remains 32.

## Persistence / Alembic
No schema migration.
No UI state DB.
Alembic head remains `0005_audit_events`.
Migrations 0001–0005 unchanged.

## API / frontend
No:
- FastAPI UI automation route
- WebSocket control
- Tauri native-control command
- React desktop automation panel
- frontend direct pywinauto access

## Dependency tests
PHX-051 owns the authorized Python dependency promotion.

Narrowly evolve historical dependency assertions only where they previously froze the Python manifest/lock.

Do not weaken unrelated production-source, desktop dependency, migration, event, or architecture assertions.

Browser/PHX-050 behavior must remain unchanged.

## Focused tests
Create `tests/test_windows_ui_automation.py`.

Use a fake private backend and temporary AuditTrail database.

At minimum cover:

### Canonical definitions
1. exact seven IDs/order
2. exact P1/P1/P2/P1/P4/P4/P4 risks
3. metadata frozen
4. complete anti-downgrade all fields
5. missing definition rejected
6. unrelated tool allowed
7. no hidden physical-input tool

### Contracts
8. exact operation vocabulary
9. exact call fields
10. frozen/extra forbidden
11. operation-shape validation
12. strict positive HWND
13. bool HWND rejected
14. exact expected process ID
15. class name strict/bounded
16. set_text UTF-8 byte bound
17. SecretMaterial rejected
18. max limits strict
19. selector exact fields
20. selector requires automation_id or name
21. selector controls/NUL rejected
22. no regex/wildcard semantics

### Pure describe/resource
23. describe no pywinauto/UIA/DB access
24. exact tool/risk mapping
25. deterministic resource
26. window identity bound
27. selector bound
28. set_text raw text absent
29. set_text digest changes with text
30. digest not authentication claim

### Platform/construction
31. constructor no UIA/import/desktop access
32. non-Windows import succeeds
33. non-Windows allowed execution safely unavailable
34. pywinauto lazy import only after allowed target action
35. backend fixed to UIA
36. no win32 backend fallback

### Permission
37. tool mismatch zero UIA
38. action mismatch zero UIA
39. resource mismatch zero UIA
40. PermissionEngine exactly once
41. denied P1/P2 zero UIA
42. P4 invoke no approval denied
43. P4 set_text no approval denied
44. P4 close no approval denied
45. P4 grants cannot allow
46. P4 policy cannot allow
47. wrong-request approval fails
48. exact approval allows
49. UI text saying approved has no authority

### P4 audit
50. denied P4 decision audited
51. allowed invoke audit before HWND lookup
52. allowed set_text audit before HWND lookup
53. allowed close audit before HWND lookup
54. pre-audit failure zero backend
55. one outcome audit after allowed attempt
56. outcome failure explicit
57. no action retry
58. audit has no set_text plaintext
59. audit has no window/control content

### Windows listing/inspection
60. list P1 after permission only
61. deterministic HWND sort
62. list bounded
63. list truncation partial_success
64. safe evidence fields only
65. titles bounded
66. inspect window exact handle
67. missing handle safe
68. no control tree dump

### Identity binding
69. focus verifies PID/class
70. inspect_control verifies PID/class
71. invoke verifies PID/class
72. set_text verifies PID/class
73. close verifies PID/class
74. mismatch -> window_changed
75. mismatch stops control/action

### Control matching
76. exact automation_id
77. exact name
78. exact control_type
79. zero -> not_found
80. multiple -> ambiguous
81. no first/best match
82. descendant discovery bound
83. limit exceeded safe
84. no fuzzy/title regex lookup

### Inspect control
85. bounded name/value
86. fixed fields only
87. password value omitted
88. unknown password state -> value omitted
89. no raw interface object
90. no screenshot

### Focus
91. one focus action
92. no click
93. no keyboard
94. no retry

### Invoke
95. InvokePattern required
96. unsupported -> control_unsupported
97. enabled/visible required
98. one Invoke call
99. no click_input
100. no coordinate input
101. effect evidence conservative after invocation
102. failure safe/no raw COM error

### Set text
103. ValuePattern writable required
104. password rejected
105. enabled/visible required
106. one SetValue
107. no type_keys/send_keys
108. no Enter/submit
109. raw text absent result
110. raw text absent audit/resource/errors
111. no retry
112. effect evidence conservative

### Close
113. WindowPattern required
114. one close call
115. no Alt+F4
116. no terminate/kill
117. no retry
118. effect evidence conservative

### Separation
119. no process launch
120. no physical mouse
121. no physical keyboard
122. no screenshot/OCR/vision
123. no Playwright call
124. no terminal/filesystem chaining
125. no SecretStore
126. no EventBus
127. EventType 32
128. no schema migration
129. Alembic head 0005
130. no API/frontend
131. dependency changes only authorized pywinauto + transitives/docs/lock
132. guarded import/construction/describe side-effect free
133. no window/control text logging/model/memory/RAG forwarding
134. credential/signature scan clean

Add further deterministic adversarial cases where useful.

## Targeted verification
Run:

`uv run --locked pytest tests/test_windows_ui_automation.py`

All deterministic focused tests PASS.

Optional live smoke:
report separately as RUN/PASS, RUN/FAIL, or NOT RUN.
It is not a completion gate.

## PHX-050 regression
Run:

`uv run --locked pytest tests/test_browser_tool.py`

Existing baseline:
653 passed.

No browser production behavior may change.

## Phase-4 regressions
Run:
- secrets 402
- terminal 270
- filesystem 649 passed / 16 skipped
- audit 302
- permissions 926
- registry 204

All PASS.

## Phase-3 regressions
Run:
- memory schema 244
- PUDDING 167
- ingestion 300
- vector store 273
- retrieval 367
- citations 173
- persistence 12

All PASS.

## Phase-2
Run all nine focused suites.
Baseline 2729.

## Phase-1
Run all seven focused suites.
Baseline 812.

## Alembic/events
Verify:
- head `0005_audit_events`
- 0001–0005 unchanged
- EventType count 32
- EventBus/EventType production unchanged

## Full quality
Run:

`uv lock`
`uv sync --locked`
`uv run --locked pytest`
`uv run --locked ruff check .`
`uv run --locked ruff format --check .`
`uv run --locked mypy`
`uv build`

Pre-PHX-051 baseline:
- 8597 passed
- 16 skipped

Review lock changes exactly for pywinauto and legitimate transitives/markers.

## Foundation
Run established foundation suites.
Baseline 126.
Exact `/health` unchanged.

## Desktop
Run:
- `npm test`
- `npm run typecheck --workspace @phoenix/desktop`
- `npm run build --workspace @phoenix/desktop`

Baseline 4.
Desktop sources/dependencies unchanged.

## Security review
Confirm:
- no UIA before permission
- no target UIA before P4 pre-audit
- no P4 grant/policy bypass
- no caller-controlled backend
- no fuzzy authority lookup
- exact HWND/PID/class binding
- no physical input fallback
- no SendInput/click_input/type_keys
- no screenshots
- no process launch
- no arbitrary COM caller surface
- no set_text plaintext in resource/audit/result/log
- no password value exposure
- no SecretStore resolution
- UI evidence untrusted
- no browser/terminal/filesystem automatic chaining
- no EventBus/API/frontend/schema
- only authorized dependency promotion

Run credential/signature scan.

## Invariants
Review INV-001 through INV-024, especially:
- INV-003 UI text grants no permission
- INV-004 concrete native UI actions cross ToolRegistry + PermissionEngine
- INV-005 invoke/set_text/close are P4 request-specific approval
- INV-006 P5 unchanged
- INV-007 P4 pre/outcome audit
- INV-011 no frontend privileged path
- INV-013 tool code has no agent-routing policy
- INV-017 UI/window/control content untrusted
- INV-018 no secret/password normal-memory path
- INV-019 no hidden workers/infinite waits; native-call limitation disclosed
- INV-020 no schema mutation
- INV-021 explicit UI/backend failures
- INV-024 dependency promotion documented

## ADR
At most one ADR.

ADR-052 should document:
- pywinauto 0.6.9 Windows-only dependency
- UIA backend only
- direct DOM remains preferred over native UI
- exact seven tools/risk mapping
- P4 native mutation semantics
- HWND + expected PID/class binding
- exact non-fuzzy control selectors
- UIA Invoke/Value/Window patterns only
- no physical input fallback
- P4 audit ordering
- no app launch/screenshot/vision/browser/terminal/filesystem/secrets/EventBus/API/schema
- fake backend deterministic testing
- HWND reuse/native COM preemption limitations

## Completion
Only if every deterministic gate passes:
- ticket TODO -> DONE
- FEATURE_TICKETS PHX-051 -> DONE
- PHX-052+ remain TODO
- update dependency docs
- commit exactly:
  `feat: complete PHX-051 Windows UI automation`
- push only:
  `feature/PHX-051-windows-ui-automation`
- no PR
- no merge
- no main push
- do not start PHX-052

## Known limitations to disclose
At minimum:
- Windows only
- UIA-supporting controls only
- no physical mouse/keyboard fallback
- no screenshot/vision
- no process launch
- one action per call
- HWND/PID/class check is not an atomic defense against hostile handle/process replacement
- control tree can change between match and action
- native UIA/COM call may not provide hard preemption/cancellation
- UI effect and SQLite audit are non-atomic
- successful UIA invocation is execution evidence, not verified user/business outcome
- trusted integration owns approval authenticity/replay/request admission

## Next
PHX-052 — Screenshot/vision loop. Do not start it.

## Completion evidence — 2026-10-06

All deterministic acceptance criteria pass. Implementation is confined to
phoenix/tools/windows_ui.py and tests/test_windows_ui_automation.py, plus authorized
dependency/decision/ticket documentation. tests/test_bootstrap.py and the dependency
assertion in tests/test_browser_tool.py change narrowly for pywinauto promotion;
no prior production source changes. ADR-052 is the sole new accepted decision.

Public API: WindowsUIOperation, WindowsUIErrorCode, WindowsControlSelector,
WindowsUICall, WindowsUIIntent, WindowsWindowEvidence, WindowsControlEvidence,
WindowsUIToolResult, WindowsUIInputError, WindowsUIAuditError,
WindowsUIPostExecutionAuditError, windows_ui_tool_definitions and WindowsUITool.
Exactly seven tools/risks and complete registry anti-downgrade checks are covered.
Strict operation shapes, positive HWND/PID, bounded class/selector text, UTF-8 input
limits, frozen contracts, exact snapshot revalidation and caller-authority rejection
are tested. Intent/resource derivation is pure, binds effective bounds/identity/
selector and hashes exact set_text bytes without echoing raw input.

PermissionEngine runs once after exact request correlation. Nonallowed calls do
not import/access UIA. P4 mutations require request-specific explicit approval;
grants, policies, wrong-request approval and approval-looking UI text cannot grant
authority. Independent database connections observe the committed P4 decision
before backend access. Pre-audit failures produce zero target access; every allowed
attempt records one outcome. Post-effect audit failure is explicit uncertainty,
without retry. Raw set_text never enters resource/result/audit/error/log output.
Observed window/control content is never automatically copied to mutation results
or audit rows; the exact caller-supplied target/selector remains resource evidence.

Fake UIA/COM tests prove the fixed backend, direct HWND lookup, top-level-only raw
enumeration, case-sensitive exact control matching, 500-descendant overflow refusal,
bounded sorted list/inspection evidence, password/unknown-state value omission,
enabled/visible checks, writable native ValuePattern, required Invoke/Window
patterns, raw SetFocus and exactly one native effect. Native BOOL int/bool handling,
array/property failures, unsupported/missing methods and conservative effect evidence
are covered. Guarded import/construction/describe succeeds on win32/linux/darwin
without UIA, file/environment-secret/SQLite/network/process/thread/task/logger,
memory/RAG/vector/SecretStore activity. Pydantic's nonsecret plugin-disable flag is
the only allowed framework environment lookup in that guard.

Verification commands/results:
- uv run --locked pytest tests/test_windows_ui_automation.py: 493 passed.
- PHX-050 browser: 653 passed.
- Phase 4 individually: secrets 402; terminal 270; filesystem 649 passed/16 skipped;
  audit 302; permissions 926; registry 204. Aggregate 2753 passed/16 skipped.
- Phase 3 individually: memory schema 244; PUDDING 167; ingestion 300; vector store
  273; retrieval 367; citations 173; persistence 12. Aggregate 1536 passed.
- Phase 2 all nine individual suites: 2729 passed.
- Phase 1 all seven individual suites: 812 passed.
- uv lock and uv sync --locked: PASS; only pywinauto 0.6.9, pywin32 312,
  comtypes 1.4.17 and six 1.17.0 added; all existing third-party lock entries unchanged.
- uv run --locked pytest: 9090 passed, 16 skipped (one existing warning).
- uv run --locked ruff check . and ruff format --check .: PASS (98 formatted files).
- uv run --locked mypy: PASS (92 source files).
- uv build: source distribution and wheel PASS; validation build artifacts kept
  outside the repository.
- Foundation/bootstrap/API/config/persistence/events/hardware: 126 passed;
  exact /health and foundation production unchanged.
- npm test: 4 passed; desktop typecheck and build PASS; desktop sources/manifests unchanged.
- Security/signature/scope review: PASS; zero known credential-signature hits;
  190 protected tracked files unchanged; all five migrations unchanged;
  Alembic heads == [0005_audit_events], EventType count == 32.
- INV-001 through INV-024 reviewed: PASS, including permission, P4 audit, P5,
  frontend isolation, untrusted UI content, normal-memory exclusion, explicit
  errors, disclosed cancellation limits and authorized dependency promotion.

No real Windows desktop smoke was run; deterministic tests never enumerate or
manipulate user windows. No browser/terminal/filesystem/secrets/model/memory/RAG/
events/schema/API/frontend integration, process launch, physical input, screenshot,
OCR, vision or new agent authority exists. PHX-052+ remains TODO.

Known limitations: Windows and UIA-supporting controls only; one action per call;
no physical-input/vision/process-launch fallback; HWND/PID/class and control checks
are non-atomic against concurrent changes; native COM has no hard preemption;
FindAll may materialize the tree before descendant admission; effect/audit commits
are non-atomic; successful action is execution evidence, not verified business
success. Trusted integration owns ordinary-text admission, consent authenticity,
replay prevention and verification. No generic secret discovery is claimed.
