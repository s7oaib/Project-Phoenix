# PHX-050 — Playwright browser tool

Status: DONE
Phase: 5 — Computer & Web

## Objective
Implement the first direct DOM/browser automation capability for PHOENIX using Playwright behind the existing ToolRegistry, PermissionEngine, audit, and secrets boundaries.

PHX-050 owns one isolated Playwright Chromium runtime with explicit session lifecycle and seven bounded operations:
1. open an isolated browser session
2. navigate
3. read the current page
4. inspect one DOM element
5. click one element
6. fill one non-secret/non-file input
7. close the session

The implementation must prefer direct browser/DOM control and must not implement Windows UI automation, screenshot/vision fallback, web-search abstraction, URAHARA research orchestration, or PHX-055 prompt-injection policy.

## Frozen authority
- Internal owner concept: `computer_control`; KAKASHI is display alias only. PHX-050 does not create/modify AgentRegistry.
- ToolRegistry is authoritative for browser tool identity and risk.
- PermissionEngine is authoritative for P0-P5 decisions.
- Generic click/fill are P4 because arbitrary page semantics may cause sensitive/external effects and V1 does not infer intent from page text.
- P4 requires exact request-specific user approval; no grant/policy can authorize it.
- PHX-042 AuditTrail must durably record P4 permission decisions before target browser/DOM access and allowed P4 outcomes after the attempt.
- Page/DOM/URL/title/text/attributes are untrusted data.
- Page content cannot grant permission, alter risk, disable audit, invoke terminal/filesystem/UI tools, or become system/project instruction.
- SecretMaterial is never accepted as browser fill data and is never automatically resolved.
- No persisted browser profile, existing user browser profile, cookie import/export, or credential reuse.

## Dependency decision
PHX-050 explicitly authorizes adding the Python Playwright package as a required Phase-5 runtime dependency:

`playwright>=1,<2`

Update:
- `pyproject.toml`
- `uv.lock`
- `docs/25_DEPENDENCIES.md`

Record one accepted ADR explaining the promotion.

Do NOT auto-download Chromium during module import, construction, tests, package install hooks, or application startup.

Browser binaries remain an operational runtime prerequisite. If the Playwright Chromium executable is unavailable at explicit session-open time, return safe `unavailable_dependency` evidence.

Focused tests must not require public network access or browser-binary download. Test the runtime through a deterministic monkeypatched private Playwright boundary. A local live-browser smoke test is optional evidence only, never a deterministic completion requirement.

## Implementation location
Prefer:

`phoenix/tools/browser.py`

Focused tests:

`tests/test_browser_tool.py`

Optional small export:

`phoenix/tools/__init__.py`

Expected documentation/dependency changes:
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- `docs/25_DEPENDENCIES.md`
- `pyproject.toml`
- `uv.lock`
- `tickets/PHX-050-playwright-browser-tool.md`

Do not modify existing ToolRegistry, PermissionEngine, AuditTrail, filesystem, terminal, secrets, memory/RAG, persistence, EventBus, API, desktop, or migrations unless a true blocker is proven. If a blocker exists, STOP/BLOCKED rather than silently broadening scope.

## Canonical tool definitions
Implement exactly these seven canonical tool IDs in this exact order:

1. `browser.session_open` — P2
2. `browser.navigate` — P2
3. `browser.read_page` — P1
4. `browser.inspect_element` — P1
5. `browser.click` — P4
6. `browser.fill` — P4
7. `browser.session_close` — P2

Provide pure:

`browser_tool_definitions() -> tuple[ToolDefinition, ...]`

No I/O.

No hidden eighth browser capability.

No P3 browser tool in PHX-050.

## Why click/fill are P4
Generic DOM interaction can trigger purchases, account/security changes, messages, deletes, uploads, submissions, or autosave behavior. V1 must not infer a lower risk from page text, selector, role, URL, model output, or caller claims.

Therefore generic click/fill are conservatively P4.

Later specialized lower-risk browser tools may be added only by explicit authority.

## Registry anti-downgrade
BrowserTool receives ToolRegistry.

At construction:
- snapshot/revalidate registry
- require all seven canonical browser definitions
- compare complete registered definitions with canonical definitions
- reject missing IDs
- reject risk downgrade
- reject permission/audit/timeout/cancellation/platform/environment/schema drift
- reject forged ToolDefinition

Additional unrelated tools are allowed.

Do not mutate ToolRegistry.

## Browser operation vocabulary
Implement exactly:

`session_open`
`navigate`
`read_page`
`inspect_element`
`click`
`fill`
`session_close`

Prefer `BrowserOperation(StrEnum)`.

## BrowserCall
Create frozen extra-forbidden `BrowserCall` with exactly:

- `call_id`
- `operation`
- `session_id`
- `url`
- `expected_url`
- `selector`
- `text`
- `max_chars`

Rules:
- call_id strict normalized nonempty, UUID4 default allowed
- session_id strict normalized nonempty, caller supplied
- no arbitrary browser executable
- no browser args
- no user-data directory
- no cookie/storage state
- no proxy
- no headers
- no environment
- no permission/risk field
- no callback/backend/handler

Operation shapes:

session_open:
- only call_id/operation/session_id
- all other fields None

navigate:
- url required
- expected_url/selector/text/max_chars None

read_page:
- expected_url required
- max_chars optional; default 100000 if omitted
- url/selector/text None
- max_chars strict int 1..200000

inspect_element:
- expected_url required
- selector required
- max_chars optional; default 20000
- url/text None
- max_chars strict int 1..50000

click:
- expected_url required
- selector required
- url/text/max_chars None

fill:
- expected_url required
- selector required
- text required
- url/max_chars None
- text strict string, maximum 65536 UTF-8 bytes
- SecretMaterial object must be rejected rather than coerced

session_close:
- only call_id/operation/session_id

No contradictory fields.

## Session identity
Exactly one active browser session is supported in V1.

session_open:
- supplied session_id becomes the active session
- if any session is already active, return safe `session_exists` failure
- no replacement/hot swap

All later operations:
- exact same session_id required

session_close:
- closes and removes active context/browser/Playwright driver state
- a missing/wrong session fails safely

This V1 single-session limit is intentional and must be documented.

No hidden background pool.

## Construction
BrowserTool constructor receives:
- ToolRegistry
- PermissionPolicy
- AuditTrail

Constructor performs NO:
- Playwright start
- Chromium launch
- filesystem browser-profile creation
- network
- DB I/O
- EventBus
- threads/tasks
- provider/model call

Browser process lifecycle begins only after an allowed `session_open`.

## Playwright runtime
Use synchronous Playwright.

Launch:
- Chromium only
- headless=True
- no persistent context
- no existing Chrome/Edge user profile
- no caller executable_path
- no caller launch args
- no extension
- no remote debugging endpoint exposed to caller
- no storage_state import
- no credentials
- no proxy
- no geolocation permissions

Create one fresh incognito BrowserContext and one Page.

Set:
- `accept_downloads=False`
- `service_workers="block"` if supported by the selected Playwright API
- no granted permissions
- bounded default action/navigation timeouts

Do not install browser binaries automatically.

## Browser runtime ownership
The BrowserTool owns only the Playwright/Chromium objects it created for the active session.

Do not close/dispose:
- external browsers
- user browser profiles
- unrelated processes

session_close performs bounded best-effort cleanup.

If browser/driver crashes:
- detect explicit unavailable/session failure
- clear unusable owned session state
- do not fabricate successful browser state

## URL contract
Navigation accepts only explicit ordinary HTTP/HTTPS URLs.

Requirements:
- strict string
- outer whitespace normalized
- nonempty
- maximum 4096 characters
- scheme exactly http or https
- hostname required
- reject username/password/userinfo
- reject NUL/control characters
- reject file:
- reject javascript:
- reject data:
- reject blob: as top-level navigation
- reject chrome:/about:/edge:
- reject localhost
- reject *.localhost
- reject IP literals that are loopback/private/link-local/multicast/reserved/unspecified

Do not silently upgrade or rewrite scheme.

Fragments/query are allowed.

Hostnames may redirect. Return final page URL as untrusted evidence.

V1 does not claim complete DNS-rebinding protection for a public hostname resolving to a private address. Document this limitation explicitly.

## Request interception
At context creation install a fixed network-request guard where Playwright supports it.

At minimum block:
- file:
- javascript:
- top-level non-http/https navigation
- obvious localhost/private IP-literal requests

Do not fetch/resolve URLs yourself before permission.

Do not perform separate DNS probes before permission.

Do not log blocked URLs.

## CSS selector contract
Selectors are caller data, not instructions.

For selector operations:
- strict string
- outer whitespace may be preserved except require meaningful content
- maximum 2048 characters
- reject NUL/control characters
- interpret strictly as CSS locator input
- do not use XPath
- do not use Playwright text-selector engine
- do not use JavaScript evaluation
- do not call `page.evaluate` / `locator.evaluate`
- do not dynamically execute selector text

Prefer a fixed `css=<selector>` locator strategy and reject Playwright selector chaining token `>>` to keep V1 selector semantics narrow.

## Exact element selection
For inspect/click/fill:
- locator must resolve to exactly one element
- zero -> `element_not_found`
- more than one -> `element_ambiguous`
- no automatic first/last selection

For P4 click/fill, element counting occurs only AFTER:
1. call validation
2. pure intent
3. request correlation
4. PermissionEngine decision
5. durable P4 permission audit

No DOM probing before required P4 audit.

## Expected URL binding
read/inspect/click/fill require caller-supplied `expected_url`.

After permission allows target browser I/O:
- read current `page.url`
- require exact equality with expected_url
- mismatch -> safe `page_changed` failure
- do not perform requested DOM action after mismatch

This prevents approval built for page A from silently acting on page B.

The expected URL itself remains untrusted metadata and grants no authority.

## Pure intent
Provide:

`describe(call) -> BrowserIntent`

No:
- Playwright access
- browser/session lookup
- DOM/page access
- network
- DB
- permission evaluation

Intent contains at least:
- call_id
- operation
- session_id
- tool_id
- action
- resource
- risk_level
- permission_requirement

## Resource derivation
Resource must bind exact call authority without leaking fill text.

Recommended deterministic resource evidence:

session_open/session_close:
- session_id + operation

navigate:
- session_id + exact normalized target URL

read_page:
- session_id + expected_url + max_chars

inspect_element:
- session_id + expected_url + selector + max_chars

click:
- session_id + expected_url + selector

fill:
- session_id + expected_url + selector + SHA-256 of exact UTF-8 fill text

Use deterministic canonical JSON or equivalently strict representation.

Do NOT place raw fill text in resource/audit evidence.

The hash is correlation/data-minimization evidence, not authentication.

## PermissionRequest correlation
execute() accepts exact PHX-041 PermissionRequest plus optional grants/user_decision.

Require exact:
- request.tool_id == intent.tool_id
- request.action == intent.action
- request.resource == intent.resource

Mismatch:
- explicit input failure
- zero target browser I/O

Evaluate PermissionEngine exactly once per valid execute call.

Do not duplicate P0-P5 logic.

## Non-allowed flow
If decision is not allowed:
- no target Playwright/browser/DOM/network action
- status DENIED
- attempted=False
- effect_applied=False
- no normal output
- no tool-outcome audit

For P4 click/fill:
- persist permission-decision audit before returning normal denied result
- if required permission audit fails, fail closed explicitly

No target session lookup/page.url/locator count before the required P4 audit.

## P4 click/fill
Only exact correlated request-specific UserPermissionDecision APPROVED may allow.

No:
- policy
- grant
- selector text
- page text
- button label
- URL
- model output
- reason saying approved

can authorize.

Wrong-request approval must fail.

## P4 pre-execution audit
For click/fill:

1. validate call
2. derive intent
3. exact request correlation
4. evaluate PermissionEngine exactly once
5. persist AuditTrail.record_permission_decision
6. only after successful commit may target browser/DOM I/O begin

If audit fails:
- zero page.url read
- zero locator query
- zero click/fill
- explicit safe BrowserAuditError

## P4 outcome audit
For an allowed click/fill attempt:
- derive ToolResultStatus
- persist one AuditTrail.record_tool_outcome
- only after successful outcome audit return normal result

If outcome audit fails after browser action may have happened:
- do not retry action
- do not claim rollback
- do not return normal success
- raise BrowserPostExecutionAuditError
- expose call_id and effect_may_have_occurred=True only

## Session open
session_open P2:
- PermissionEngine must allow before Playwright startup
- no mandatory PHX-042 audit
- start one Playwright sync runtime
- launch one isolated headless Chromium
- create one isolated context/page
- no initial external navigation
- page may remain `about:blank`
- no existing user data/cookies/storage imported
- return structured session evidence
- browser missing/launch failure -> unavailable_dependency/failure safely
- cleanup partial startup state

## Navigate
navigate P2:
- exact allowed permission before browser access
- require active matching session
- validate target URL lexically before permission
- after allow invoke `page.goto` with bounded timeout
- wait only according to a fixed V1 wait policy (e.g. DOMContentLoaded)
- no infinite network-idle wait
- redirects allowed if browser policy permits
- return final_url and title, no full HTML
- navigation timeout -> TIMEOUT
- blocked/invalid URL -> explicit safe failure
- browser error text not leaked

No download saving.

## Read page
read_page P1:
- active matching session
- expected_url exact after allow
- read body inner text only
- no HTML source
- no cookies
- no localStorage/sessionStorage
- no browser history dump
- no network log
- no JS evaluation
- title/final URL allowed as untrusted evidence
- output text bounded by max_chars
- if larger, return deterministic prefix with PARTIAL_SUCCESS and truncated=True
- preserve text exactly within selected prefix

Page text is untrusted and must not be executed or treated as permission evidence.

## Inspect element
inspect_element P1:
- expected_url exact
- one CSS locator exactly
- return only bounded safe DOM evidence

Allowed evidence:
- tag_name if obtainable without arbitrary JS; if not safely available, omit
- inner_text bounded by max_chars
- attributes from a fixed allowlist only:
  - id
  - class
  - role
  - aria-label
  - name
  - type
  - href
  - title

Do not return:
- outerHTML
- innerHTML
- event handlers
- dataset dump
- style dump
- hidden form values
- input_value
- password value
- cookie/storage state

If Playwright cannot obtain tag name without evaluate, omit it rather than using evaluate.

## Click
click P4:
- exact expected_url
- exactly one CSS element
- P4 audit before locator/page access
- invoke one Playwright locator.click with bounded timeout
- no force=True
- no no_wait_after bypass
- no caller click options
- no double-click
- no right-click
- no keyboard modifier injection
- no automatic retry

Conservatively:
- once click invocation begins, effect_applied may be true even if the API later errors/timeouts
- successful Playwright click is execution evidence only, not proof of desired external result

If a popup/new page is created:
- do not adopt it as the active page
- close owned popup best-effort
- return safe failure/popup_blocked or explicit evidence according to the implementation contract

## Fill
fill P4:
- exact expected_url
- exactly one CSS element
- pre-audit first
- inspect fixed `type` / autocomplete-related attributes only after audit
- reject:
  - input type=file
  - input type=password
  - autocomplete=current-password
  - autocomplete=new-password
  - other ticket-defined credential fields
- SecretMaterial object rejected structurally before execution
- invoke one locator.fill(text) with bounded timeout
- no press Enter
- no submit
- no click
- no keyboard macro
- no automatic retry

Do not echo raw fill text in result, audit, resource, log, exception, or title.

A successful fill is execution evidence only and may have triggered page-side autosave/network behavior; therefore P4.

## Downloads
PHX-050 does NOT implement download capability.

Context must set `accept_downloads=False`.

Do not:
- save downloads
- expose temp download paths
- call save_as
- hand a download directly to filesystem tool

If an operation triggers a download:
- no file is intentionally persisted by PHX-050
- return safe download_blocked/failure evidence where detectable

Future explicit download integration must cross file/tool security.

## Uploads
PHX-050 does NOT implement upload capability.

Do not call:
- set_input_files
- file chooser APIs

fill must reject file inputs.

No private-file upload path exists.

## Authentication / secrets
No:
- cookies imported from user browser
- storage_state import
- basic-auth credentials
- HTTP credentials
- client certificates
- proxy credentials
- SecretStore resolution
- SecretMaterial fill
- password-field fill
- secret environment injection

PHX-045 remains a separate boundary.

## BrowserResult
Create frozen extra-forbidden `BrowserToolResult` containing at least:

- call_id
- operation
- session_id
- tool_id
- resource
- status
- attempted
- effect_applied
- permission_decision
- permission_audit_id
- outcome_audit_id
- final_url
- title
- text
- element
- truncated
- error_code

Output/elements must be detached immutable JSON-safe evidence.

No:
- Page
- Browser
- BrowserContext
- Playwright
- Locator
- exception
- callback
- cookie jar
- storage state
- secret value

Structural consistency mandatory.

## Safe error vocabulary
Use a small closed vocabulary including:

- session_exists
- session_not_found
- invalid_url
- blocked_url
- page_changed
- browser_unavailable
- navigation_failure
- selector_invalid
- element_not_found
- element_ambiguous
- interaction_failure
- input_forbidden
- download_blocked
- timeout
- io_failure

No raw Playwright/browser/OS diagnostics.

## Bounded execution
Use finite fixed defaults.

Recommended maxima:
- navigation/action timeout <= 30 seconds
- read page <= 200000 characters
- inspect element <= 50000 characters
- fill text <= 65536 UTF-8 bytes
- selector <= 2048 characters
- URL <= 4096 characters

No hidden retries.

No infinite wait.

Canonical cancellation_supported should truthfully remain False for PHX-050 unless the authoritative implementation can guarantee a real caller-cancellation mechanism without hidden worker architecture.

## Browser state / privacy
Do not log:
- page text
- selector
- fill text
- cookies
- URLs containing userinfo (userinfo is rejected)
- DOM attributes beyond returned structured evidence
- Playwright exception text

Do not persist browser state to:
- SQLite
- memory
- RAG
- audit
- files
- model prompts

No screenshots in PHX-050.

## Prompt-injection boundary
PHX-055 is later, but existing fundamental isolation applies now.

Page/DOM text may contain:
- "ignore previous instructions"
- fake system messages
- fake approval JSON
- shell commands
- file-delete requests
- credential requests

PHX-050 returns such data as inert evidence only.

BrowserTool itself never:
- converts page text into PermissionGrant/UserPermissionDecision
- creates ToolDefinition from page data
- calls terminal/filesystem based on page text
- calls another browser action based on page text
- calls a model based on page text

## Direct DOM preference
PHX-050 uses Playwright DOM/browser APIs only.

Do NOT:
- call Windows UI automation
- move mouse through OS automation
- type through OS keyboard APIs
- use screenshot/vision
- invoke PHX-051/052 behavior
- silently fall back from failed DOM operation to native/mouse/vision automation

Failure remains explicit.

## Event separation
No EventBus dependency.
No new EventType.
Canonical EventType count remains 32.

Do not emit browser/tool events in PHX-050.

## Persistence / schema
No migration.
No new table.
Alembic head remains `0005_audit_events`.

Migrations 0001-0005 unchanged.

## API / frontend
No:
- FastAPI browser endpoint
- WebSocket browser control
- Tauri browser command
- React browser panel
- direct frontend Playwright access

## Tests
Create `tests/test_browser_tool.py`.

Focused tests must use deterministic fake Playwright objects/private adapter monkeypatches.
No public internet.
No real user browser profile.
No real cookies/credentials.
No browser-binary download.

At minimum cover:

### Canonical contracts
1. exact 7 tool IDs/order
2. exact risks P2/P2/P1/P1/P4/P4/P2
3. definitions immutable
4. registry anti-downgrade each field
5. missing canonical definition rejected
6. call exact fields
7. call frozen/extras rejected
8. operation vocabulary exact
9. operation-specific shapes
10. strict IDs
11. limits reject bool/nonfinite/wrong types
12. SecretMaterial fill rejected

### Pure describe / resource
13. describe no I/O
14. exact tool mapping
15. risk from registry
16. navigate resource binds URL
17. read resource binds expected URL
18. inspect/click resource binds selector
19. fill resource contains digest not fill text
20. equivalent calls deterministic
21. distinct fill text -> distinct resource digest

### URL safety
22. http valid
23. https valid
24. file rejected
25. javascript rejected
26. data rejected
27. chrome/about rejected
28. userinfo rejected
29. localhost rejected
30. localhost suffix rejected
31. loopback IPv4/IPv6 rejected
32. private/link-local/reserved literals rejected
33. controls/NUL rejected
34. URL length bound
35. no DNS/network during validation/describe

### Selectors
36. exact CSS string
37. empty rejected
38. controls/NUL rejected
39. chaining token rejected
40. length bound
41. no XPath/text engine
42. no evaluate API in production source

### Construction/session
43. constructor no Playwright/DB/network I/O
44. session_open denied -> zero Playwright start
45. session_open allowed -> one isolated startup
46. headless fixed
47. no persistent context/user profile
48. accept_downloads false
49. no storage_state/cookies import
50. duplicate session fails without replacement
51. wrong session fails
52. session_close allowed closes owned state
53. close missing session safe failure
54. launch missing dependency safe result
55. partial startup cleanup

### Permission
56. exact request correlation
57. mismatch tool -> zero browser I/O
58. mismatch action -> zero browser I/O
59. mismatch resource -> zero browser I/O
60. PermissionEngine exactly once
61. page text cannot approve
62. selector text cannot approve
63. P4 click no approval -> denied
64. P4 fill no approval -> denied
65. P4 grant cannot allow
66. P4 policy cannot allow
67. wrong-request P4 approval rejected
68. exact approval allows

### P4 audit
69. denied click permission decision audited
70. denied fill permission decision audited
71. allowed click permission audit committed before page.url
72. allowed fill permission audit committed before page.url
73. pre-audit failure -> zero page/session/locator I/O
74. click outcome audit after attempt
75. fill outcome audit after attempt
76. outcome-audit failure explicit
77. no action retry after outcome-audit failure
78. audit row contains no fill text
79. audit row contains no page text

### Navigate
80. requires active session after allow
81. one goto
82. bounded timeout
83. fixed wait policy
84. redirects return final URL
85. title returned
86. navigation timeout -> timeout
87. raw browser error suppressed
88. no download save

### Read
89. expected_url exact
90. page_changed prevents body read
91. body inner text returned
92. title/final URL returned
93. bound exact
94. oversized -> deterministic prefix partial_success/truncated
95. no HTML
96. no cookies/storage/history
97. page text inert

### Inspect
98. page_changed prevents locator
99. zero locator -> not_found
100. >1 -> ambiguous
101. exactly one
102. text bounded
103. fixed attribute allowlist only
104. no input value
105. no outerHTML/innerHTML
106. no JS evaluate

### Click
107. pre-audit before page access
108. expected_url exact
109. exact one locator
110. one click only
111. no force
112. no modifiers/double/right click
113. no retry
114. attempted/effect evidence truthful
115. timeout/failure safe
116. popup blocked/closed where fake signals it
117. page output cannot trigger second action

### Fill
118. pre-audit before page access
119. expected_url exact
120. exact locator
121. file input rejected
122. password input rejected
123. current/new-password autocomplete rejected
124. one fill only
125. no enter/submit/click
126. fill text omitted from result
127. fill text omitted from audit
128. no retry
129. autosave possibility documented as P4 limitation

### Downloads/uploads/secrets
130. accept_downloads false
131. no save_as
132. no set_input_files
133. no file chooser
134. no SecretStore resolve
135. no SecretMaterial reveal
136. no password fill
137. no cookie import/export

### Isolation
138. no Windows UI automation
139. no screenshot/vision
140. no search provider/URAHARA
141. no terminal/filesystem chaining
142. no EventBus
143. EventType 32
144. no schema migration
145. Alembic head 0005
146. no API/frontend
147. dependency change only Playwright/docs/lock
148. guarded import/construction/describe side-effect free
149. no page text logging/model/memory/RAG forwarding
150. credential/signature scan clean

Add further deterministic adversarial cases where useful.

## Targeted verification
Run:

`uv run --locked pytest tests/test_browser_tool.py`

All deterministic focused tests PASS.

No browser binary/public internet requirement.

If an optional local live Chromium smoke test is run, report it separately and do not make absence a failure.

## Phase-4 regressions
Run individually:

`tests/test_secrets_boundary.py`
`tests/test_terminal_tool.py`
`tests/test_filesystem_tool.py`
`tests/test_audit_trail.py`
`tests/test_permission_engine.py`
`tests/test_tool_registry.py`

Existing baselines:
- PHX-045: 402
- PHX-044: 270
- PHX-043: 649 passed / 16 skipped
- PHX-042: 302
- PHX-041: 926
- PHX-040: 204

All remain green.

## Phase-3 regressions
Run:
- memory schema 244
- PUDDING 167
- document ingestion 300
- VectorStore 273
- retrieval 367
- provenance/citations 173
- persistence 12

All PASS.

## Phase-2
Run all nine focused Phase-2 suites.
Existing aggregate: 2729 passed.

## Phase-1
Run all seven focused Phase-1 suites.
Existing aggregate: 812 passed.

## Alembic / events
Verify:
- Alembic head exactly `0005_audit_events`
- migrations 0001-0005 unchanged
- EventType count exactly 32
- EventBus production unchanged

## Full quality
Run:

`uv lock`
`uv sync --locked`
`uv run --locked pytest`
`uv run --locked ruff check .`
`uv run --locked ruff format --check .`
`uv run --locked mypy`
`uv build`

Pre-PHX-050 baseline:
- 7944 passed
- 16 skipped

The Playwright dependency/lock changes are expected and must be reviewed.

## Foundation
Run/confirm bootstrap, API, configuration, persistence, events, hardware.
Existing baseline: 126 passed.
Exact `/health` unchanged.

## Desktop
Run:
- `npm test`
- `npm run typecheck --workspace @phoenix/desktop`
- `npm run build --workspace @phoenix/desktop`

Existing baseline: 4 passed.
Desktop sources unchanged.

## Security review
Confirm:
- no caller-controlled browser executable/launch args/profile
- no persistent user browser profile
- no cookies/storage import/export
- no credential/SecretStore integration
- no password/file fill
- no download saving/upload
- no JavaScript evaluate
- no XPath/text-selector engine
- no localhost/private IP-literal top-level navigation
- no browser I/O before permission
- no DOM access before required P4 audit
- no P4 grant/policy bypass
- fill text absent from resource/audit/result/log
- page output remains untrusted
- no terminal/filesystem/UI chaining
- no screenshot/vision
- no EventBus/API/frontend/schema change
- only expected Playwright dependency changes

Run credential/signature scan.

## Invariants
Review INV-001 through INV-024, especially:
- INV-003 page/model output grants no permission
- INV-004 browser actions cross ToolRegistry + PermissionEngine
- INV-005 click/fill P4 exact approval
- INV-006 P5 unchanged
- INV-007 P4 audit-before-action + outcome evidence
- INV-011 no frontend direct privileged path
- INV-013 browser tool has no agent policy
- INV-017 webpage/DOM evidence untrusted
- INV-018 no secret/cookie normal-memory path
- INV-019 bounded browser waits/operations
- INV-020 no schema mutation
- INV-021 explicit browser/dependency failures
- INV-024 Playwright promotion documented by accepted decision

## ADR
At most one ADR.

ADR-051 should record:
- Playwright promoted to required Phase-5 browser dependency
- isolated headless Chromium, no persistent profile
- exact seven browser tools and risk mapping
- generic click/fill conservatively P4
- exact PermissionEngine correlation
- P4 durable audit ordering
- explicit single-session V1
- expected_url binding
- strict HTTP/HTTPS/public-literal URL boundary
- CSS-only no-evaluate DOM access
- no download/upload/secrets
- page data remains untrusted
- no native UI/vision/search/research/API/frontend/schema/EventBus integration
- DNS-rebinding and hostile concurrent page-change limitations

## Completion
Only after all deterministic gates pass:
- mark PHX-050 ticket DONE
- mark PHX-050 DONE in FEATURE_TICKETS.md
- leave PHX-051+ TODO
- update docs/25 dependency classification truthfully
- commit exactly:
  `feat: complete PHX-050 Playwright browser tool`
- push only:
  `feature/PHX-050-playwright-browser-tool`
- no PR
- no merge
- no main push
- do not start PHX-051

## Known V1 limitations to disclose
At minimum:
- browser binary must be installed operationally; tests do not download it
- one active isolated session only
- no persistent authenticated user profile
- no upload/download
- no secrets/password filling
- generic click/fill P4
- no cancellation
- browser process/action and audit DB are not atomic
- DNS rebinding through a public hostname is not claimed fully prevented
- hostile concurrent page navigation after expected_url check is not fully atomic with DOM action
- no native UI/vision fallback
- page action completion is execution evidence, not verified business outcome

## Next
PHX-051 — Windows UI automation. Do not start it.

## Verified completion evidence — 2026-10-06

All deterministic acceptance gates PASS. Starting feature HEAD was
`68dfbe494992fa39659f4f0b4bd36419362d0c90`; origin/main remained
`3c17b8a5291871c236cc3726b2b91097657d4ed4`. Implementation remains on
`feature/PHX-050-playwright-browser-tool`; PHX-051 and later tickets remain TODO.

### Files and dependency promotion

- Created `phoenix/tools/browser.py` and `tests/test_browser_tool.py`.
- Modified `pyproject.toml`, `uv.lock`, `docs/25_DEPENDENCIES.md`, `DECISIONS.md`,
  `FEATURE_TICKETS.md` and this ticket.
- Supporting test-only changes in `tests/test_bootstrap.py`,
  `tests/test_vector_store_sqlite.py` and `tests/test_retrieval_context.py`
  reconcile historical global dependency freezes with this ticket's explicitly
  authorized promotion. Bootstrap still asserts the complete exact runtime set.
  Browser tests now compare the entire Python project configuration and lock
  against origin/main, permitting only Playwright plus its required transitives
  and exact Phoenix dependency references. Existing third-party records remain equal;
  desktop dependencies and protected production-source freezes remain intact.
- Only new direct dependency: `playwright>=1,<2`, locked **1.63.0**. New transitives
  **greenlet 3.5.6**, **pyee 13.0.1**; **39** resolved packages. No pre-existing
  package version, artifact, dependency or unrelated metadata changed.
- One accepted decision: **ADR-051**. No export, API/frontend or other production
  subsystem change. No binary download or installation hook.

### Contracts, permission and audit

Public API: `BrowserOperation`, `BrowserErrorCode`, `BrowserCall`, `BrowserIntent`,
`BrowserElement`, `BrowserToolResult`, `BrowserInputError`, `BrowserAuditError`,
`BrowserPostExecutionAuditError`, `browser_tool_definitions`, `BrowserTool`.
The internal owner remains `computer_control`; no AgentRegistry change.

Exactly seven ordered canonical tools: session_open P2, navigate P2, read_page P1,
inspect_element P1, click P4, fill P4, session_close P2, all under `browser.`.
Complete metadata snapshots reject missing definitions, forged evidence and drift
in all eleven definition fields; unrelated tools remain allowed. Contracts are
frozen, extra-forbidden, exact-revalidated and strict. BrowserCall has exactly the
eight frozen fields and operation shapes. Caller IDs are opaque, normalized,
case-preserving, nonempty control-free strings; call_id defaults to UUID4.

Pure describe performs no session/runtime lookup, permission evaluation or I/O.
Canonical compact JSON resources bind operation/session, exact target/expected
URL, selector and effective read bound. Fill binds SHA-256 of exact UTF-8 text,
never raw text; the digest grants no authentication or permission. Exact
PermissionRequest tool/action/resource correlation precedes exactly one existing
PermissionEngine evaluation. Nonallowed results are denied, unattempted, have no
effect/output/outcome audit and perform zero target browser/session I/O.

Click/fill require exact request-specific approval. Grants, policy, wrong-request
approvals and approved-looking page/selector/reason text cannot bypass P4. P4
permission audit commits before any owned session/browser/page/locator access,
including denied decisions. An independent SQLite connection verifies durability
before every fake browser method. Pre-audit failure raises safe BrowserAuditError
with zero target I/O. Allowed P4 attempts produce exactly one durable outcome
audit before normal return, including failed/pre-action attempts. Outcome failure
raises BrowserPostExecutionAuditError with call_id and possible-effect evidence;
no action or audit retry, rollback claim or normal success.

### Runtime, URL and DOM boundary

Only an allowed explicit session_open lazily starts synchronous Playwright and
one owned headless Chromium, fresh nonpersistent context and blank page. Fixed
options: accept_downloads=False, service_workers=block, permissions=[], default
action/navigation timeouts 30,000 ms. No executable/args/proxy/profile/credentials/
cookies/storage/headers/environment supplied by caller. One active session;
duplicates never replace it. Wrong/missing IDs fail without target access.
Explicit session_close removes owned state and attempts context/browser/driver
cleanup once each. Partial startup and crash/disconnection cleanup are tested.
The internal CDP session is released through owned context/browser/driver cleanup.

URLs are ordinary HTTP/HTTPS strings, outer-trimmed, <=4,096 characters, with a
hostname and no userinfo, controls, backslashes, localhost/suffix or nonpublic
IP literals. Unicode IDNA and Chromium numeric/short/octal/hex IPv4 spellings are
checked conservatively. No validation-time fetch or DNS probe. A fixed context
request guard checks URLs before continuation. Chromium normally follows HTTP
redirects without re-running Playwright routing; a fixed owned-page CDP Fetch
response guard checks every received redirect Location before continuing it.
Unsafe, ambiguous or malformed redirect evidence fails closed. Protocol failure
does not silently resume; it marks owned state unusable. No response body is
fetched by the guard and no caller protocol command or JavaScript is exposed.
External child-frame and worker requests are rejected rather than letting their
separate Chromium targets escape the owned page's response guard. Tests cover
all five redirect statuses, safe absolute/relative targets, private/obfuscated
destinations, malformed headers, guard failure and operation/audit evidence.

Navigate performs one goto with 30,000-ms timeout and DOMContentLoaded wait;
returns validated final URL and bounded title as untrusted evidence. Reads and
interactions check exact current expected_url after permission/pre-audit.
Selectors are CSS-only, <=2,048 characters, control-free, without chaining or
XPath/text engines. Inspect/click/fill count exactly one match; zero and multiple
matches fail explicitly. No first/nth selection, evaluate, HTML or hidden values.
Read returns body inner text, default 100,000/max 200,000 characters; inspect
returns element text, default 20,000/max 50,000, and only id/class/role/aria-label/
name/type/href/title, each <=4,096. Prefix truncation is deterministic, Unicode
preserving and reported as partial_success/truncated. Tag name is omitted.
Detached element attributes are immutable mapping snapshots and JSON-safe.

Click invokes one bounded ordinary locator.click, without force/options/modifiers
or retry. Fill accepts exact explicit ordinary text <=65,536 UTF-8 bytes,
including empty/meaningful whitespace, structurally rejects SecretMaterial and
checks type=file/password and current/new-password autocomplete after pre-audit.
It invokes one fill with no Enter, submit or click. Once either action starts,
possible-effect evidence remains true even after failure/timeout. Interactions
return no page title/URL/text that could reflect raw fill text. Popups are closed
best effort and never adopted; detected downloads fail safely without save/path
exposure; no upload/file chooser or SecretStore resolution exists.

Results are frozen, exact-revalidated JSON-safe evidence with all seventeen
required fields, consistent permission/attempt/effect/audit/status/output shapes
and closed error codes. Raw Playwright/OS errors and private exception chains are
suppressed. Page evidence stays inert and is never executed, logged, promoted
into authority or forwarded to another action/subsystem.

### Verification results

- `uv run --locked pytest tests/test_browser_tool.py -q`: **653 passed**,
  **34.86 s**, deterministic fake runtime/DOM/protocol only. Autouse guards forbid
  real Playwright startup; no public network, profiles, credentials or downloads.
- Optional live Chromium smoke: **NOT RUN**, bundled executable absent. No binary
  downloaded. This is not a deterministic completion gate.
- All **29** established regression suites ran individually and exited **0**.
- Phase 4: secrets **402**, terminal **270**, filesystem **649 passed/16 skipped**,
  audit **302**, permission **926**, registry **204**; **2,753 passed/16 skipped**.
- Phase 3: memory **244**, PUDDING **167**, ingestion **300**, vector store **273**,
  retrieval **367**, provenance **173**; **1,524 passed**, plus persistence **12**.
- Phase 2: registry **96**, orchestrator **101**, planner **151**, router **213**,
  task graph **348**, handoffs **214**, LEVI **242**, MAHORAGA **790**, KAIDO **574**;
  **2,729 passed**.
- Phase 1: provider **61**, Ollama **108**, model registry **83**, lifecycle **54**,
  embeddings **182**, Colibri **194**, benchmark **130**; **812 passed**.
- Final full `uv run --locked pytest -q`: **8,597 passed, 16 skipped**, **231.00 s**;
  baseline 7,944 plus 653 new cases, with one existing Starlette/httpx warning.
- `uv lock`; `uv sync --locked`: PASS. `ruff check .`: PASS;
  `ruff format --check .`: **96 files** PASS; `mypy`: **90 source files** PASS.
- `uv build --out-dir <external validation directory>/dist`: PASS, source archive
  and wheel. Validation logs, JUnit and build archives remain outside the repo.
- Foundation bootstrap/API/config/persistence/events/hardware: **126 passed**,
  existing warning only; exact /health and API source unchanged.
- Desktop `npm test`: **4 passed**; workspace typecheck and build: PASS.
- Alembic head **0005_audit_events**, **32** EventTypes, all five migration Git
  blobs unchanged. **186** protected tracked files compare equal to origin/main.
- Repository credential/signature scan: no private-key, GitHub/OpenAI/AWS/Slack
  credential signatures. Changed/untracked scope reviewed against an exact
  allowlist; no profile/state/DB/WAL/download/screenshot/cache/build artifacts.
- Reviewed INV-001 through INV-024. Canonical permissions/P4/P5/audit remain
  intact; no privileged bypass, agent/model authority, memory/RAG/embedding
  forwarding, terminal/filesystem/native UI/vision/search/URAHARA/PHX-055 action,
  EventBus, API/frontend or schema integration. New dependency promotion explicit.

### Limitations

One isolated session; browser binaries are operational prerequisites. No persistent
authentication/profile, secrets/password fill, uploads/downloads, public
cancellation, native UI/vision fallback or automated business-outcome verification.
External child-frame/worker network access is deliberately unsupported in V1.
Public-host DNS rebinding is not fully prevented. Hostile page changes after the
exact URL check remain a race with DOM action. Browser effects and SQLite audit
are not atomic; outcome failure cannot prove rollback. A fill can trigger page
autosave/network effects. Playwright's synchronous APIs own their normal driver;
individual navigation/action waits are fixed and finite, but context/browser/driver
close exposes no public hard timeout. Cleanup makes one cooperative best-effort
attempt per owned resource, without hidden workers/retries or a process-wide hard
deadline claim. Returned prefixes are bounded; Playwright obtains the underlying
inner text before slicing. Trusted integration owns consent authenticity, replay
control and admission of caller URLs/selectors/text; arbitrary ordinary strings
are not classified as secrets. No general prompt-injection classifier is added.
