# PHX-055 — Prompt-injection defenses

Status: TODO
Phase: 5 — Computer & Web

## Objective
Implement PHOENIX's first reusable prompt-injection defense boundary without pretending that heuristic detection can make hostile content safe.

PHX-055 closes Phase 5 by adding two layers:

1. **PromptInjectionGuard** — a deterministic, side-effect-free security boundary that marks external text as untrusted data, detects a bounded set of suspicious instruction patterns, preserves exact content, and renders a fixed content/instruction-separated prompt envelope.
2. **GuardedResearchSynthesizer** — an adapter for the existing PHX-054 `ResearchSynthesizer` boundary that ensures research source text reaches a future model-backed responder only through the PHX-055 guarded prompt envelope.

The defense model is intentionally layered:

- all external content is untrusted even when no heuristic signal is detected,
- heuristic signals are observable warnings only,
- fixed instruction/data separation is the primary prompt-level defense,
- PermissionEngine remains the only permission authority,
- ToolRegistry remains the only concrete tool declaration boundary,
- no model/search/web/document/tool text may create grants, approvals, tools, risk changes, or execution authority,
- typed outputs remain exact-revalidated,
- downstream execution still requires the existing tool/permission/audit architecture.

PHX-055 does **not** claim prompt injection is solved perfectly. It provides deterministic isolation, observability, and a guarded model-input boundary.

## Frozen owner
Conceptual security owner internal ID:

`perimeter_security`

Display alias:

GOJO

The alias grants no authority.

GOJO/security logic may advise/isolate content, but PermissionEngine remains authoritative.

## Existing authorities to preserve
- PHOENIX is supreme.
- INV-003: no LLM output directly grants permission.
- INV-004: privileged tool calls cross ToolRegistry + PermissionEngine.
- INV-005/P4 and INV-006/P5 remain unchanged.
- INV-017: web/uploaded external content is untrusted.
- PHX-041 PermissionEngine remains unchanged.
- PHX-045 SecretMaterial boundary remains unchanged.
- PHX-050 BrowserTool remains unchanged.
- PHX-052 vision output remains advisory.
- PHX-053 SearchProvider output remains untrusted.
- PHX-054 UraharaResearchService remains advisory.
- EventType remains 32.
- Alembic head remains `0005_audit_events`.

# Scope

## Recommended implementation
Prefer:

- `phoenix/security/prompt_injection.py`
- `phoenix/web/guarded_synthesis.py`
- `tests/test_prompt_injection_defenses.py`

Optional minimal exports:
- `phoenix/security/__init__.py`
- `phoenix/web/__init__.py`

Expected status/docs updates:
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- this ticket

Expected dependency changes:

**NONE**

Do not modify existing production files unless a true blocker is proven. In particular, PHX-054 `research.py` should remain unchanged; the guarded synthesizer is an adapter that already satisfies its existing `ResearchSynthesizer` interface.

# Part A — PromptInjectionGuard

## Public API
Implement at least:

- `UntrustedContentOrigin`
- `PromptInjectionSignal`
- `PromptInjectionSeverity`
- `UntrustedContentItem`
- `PromptInjectionAssessment`
- `PromptBoundaryRequest`
- `GuardedPrompt`
- `PromptInjectionErrorCode`
- `PromptInjectionError`
- `PromptInjectionGuard`

No tool execution API.

## Origin vocabulary
Freeze exactly:

- `web_search`
- `web_page`
- `uploaded_document`
- `tool_output`
- `model_output`
- `vision_output`
- `external_text`

Origin is provenance only.

It never establishes trust.

## Signal vocabulary
Freeze exactly:

- `authority_impersonation`
- `instruction_override`
- `permission_bypass`
- `tool_execution_request`
- `credential_exfiltration`
- `prompt_exfiltration`
- `policy_manipulation`
- `role_delimiter`

Signals are deterministic heuristics only.

Absence of signals MUST NOT mean "safe", "trusted", or "verified".

## Severity vocabulary
Freeze exactly:

- `none`
- `suspicious`
- `high`

Semantics:

- `none`: no V1 heuristic matched; content is STILL untrusted.
- `suspicious`: only lower-tier instruction/role-manipulation signals.
- `high`: at least one permission/tool/credential/prompt/policy manipulation signal.

Severity MUST NOT:
- grant permission,
- lower risk,
- block P4 confirmation,
- mark content trusted,
- prove malicious intent.

## PromptInjectionErrorCode
Freeze exactly:

- `invalid_input`
- `invalid_content`
- `boundary_overflow`
- `invalid_assessment`
- `busy`
- `cancelled`
- `invalid_response`
- `closed`
- `wrong_event_loop`
- `cleanup_failure`

Public error strings contain safe code/category only.

No raw external content.

# Part B — Untrusted content contracts

## UntrustedContentItem
Frozen, extra-forbidden, exactly:

- `content_id`
- `origin`
- `source_id`
- `field_name`
- `text`
- `text_sha256`

Rules:

### content_id
- strict nonempty control-free ID
- outer whitespace normalized
- case-preserving
- <=256 chars

### origin
- exact `UntrustedContentOrigin`

### source_id
- strict nonempty control-free ID
- outer whitespace normalized
- case-preserving
- <=256 chars

### field_name
- strict nonempty control-free string
- outer whitespace normalized
- <=128 chars

### text
- ordinary Unicode string only
- preserve exact content and formatting
- NUL rejected
- <=50000 characters
- `repr=False`
- may be empty
- NEVER interpreted as instruction authority

### text_sha256
- exact lowercase SHA-256 of `text.encode("utf-8")`

No:
- trust flag
- permission flag
- action
- tool ID
- role
- callback
- executable object

Exact stored-value revalidation required.

## Content-size bound
A guarded boundary may contain:

- 0..64 untrusted content items
- <=200000 total content characters

Overflow fails explicitly.

Do not silently truncate within PromptInjectionGuard.

Upstream subsystems own their own content budgets.

# Part C — Deterministic assessment

## PromptInjectionAssessment
Frozen, extra-forbidden, exactly:

- `content_id`
- `content_sha256`
- `signals`
- `severity`
- `suspicious`
- `data_only`

Rules:
- assessment content ID/hash exactly correlate to one UntrustedContentItem
- signals immutable ordered unique tuple
- signal order is canonical enum order, never match-discovery order
- suspicious == bool(signals)
- data_only is always literal True
- severity exactly follows frozen mapping
- no "trusted"/"safe"/"verified" field

## Detection view
PromptInjectionGuard preserves original text unchanged.

For detection only, derive a normalized scan view with a deterministic stdlib-only transform such as:

- Unicode NFKC normalization,
- casefold,
- removal of Unicode format/control obfuscation characters used solely for scanning,
- whitespace normalization for phrase matching.

Do NOT replace or mutate the original item text.

## Heuristic families
Implement deterministic bounded phrase/pattern matching for the frozen signal categories.

At minimum detect representative variants of:

### authority_impersonation
- "system message"
- "developer message"
- "administrator instruction"
- "I am the system"
- fake authority headers

### instruction_override
- "ignore previous instructions"
- "disregard previous instructions"
- "forget prior instructions"
- "override earlier instructions"

### permission_bypass
- "you are approved"
- "permission granted"
- "bypass permission"
- "no confirmation required"
- "skip approval"

### tool_execution_request
- "run this command"
- "execute this command"
- "call the tool"
- "delete the file"
- direct requests to perform privileged actions found inside external data

### credential_exfiltration
- requests to reveal/send passwords, API keys, tokens, secrets, credential-store contents

### prompt_exfiltration
- requests to reveal/show/export the system prompt, hidden instructions, developer prompt, internal policy

### policy_manipulation
- requests to disable safety, change policy, lower risk, ignore ToolRegistry/PermissionEngine/audit

### role_delimiter
- common role-spoofing/delimiter forms such as:
  - `SYSTEM:`
  - `DEVELOPER:`
  - `<|system|>`
  - `[INST]`
  - XML-like role tags

The exact detection table must be explicit, bounded, deterministic, tested, and free of catastrophic regular expressions.

## Detection limitations
Do NOT claim the scanner can detect:
- all prompt injection,
- semantic paraphrases,
- encoded payloads,
- steganography,
- visual injection,
- multilingual variants not in the V1 table,
- adversarial model behavior.

No-signal content stays untrusted.

# Part D — PromptBoundaryRequest and GuardedPrompt

## PromptBoundaryRequest
Frozen, extra-forbidden, exactly:

- `boundary_id`
- `user_task`
- `items`

Rules:

### boundary_id
- strict normalized nonempty control-free ID <=256

### user_task
- strict meaningful Unicode <=4096
- preserve formatting
- `repr=False`
- represents the caller/user analysis objective
- still grants no PermissionEngine authority

### items
- immutable tuple
- 0..64 exact UntrustedContentItem values
- unique content IDs
- total text <=200000 chars
- exact stored-value revalidation

No:
- system instruction field
- developer instruction field
- permission
- tool
- action
- risk
- arbitrary model options

## GuardedPrompt
Frozen, extra-forbidden, exactly:

- `boundary_id`
- `owner_id`
- `policy_version`
- `user_task`
- `items`
- `assessments`

Rules:
- owner_id exactly `perimeter_security`
- policy_version exactly `phoenix-prompt-boundary-v1`
- exact user task preserved
- item order preserved
- assessments exactly one-to-one/order-correlated with items
- no missing/duplicate assessment
- no assessment for unknown item
- exact content hash correlation
- no "safe" or "trusted" field

## Fixed rendered boundary
Provide pure:

`GuardedPrompt.render_text() -> str`

The rendered text MUST be deterministic and contain:

1. a fixed PHOENIX security preamble authored only in code,
2. the user task encoded as data,
3. the untrusted items encoded in canonical JSON,
4. assessment signals encoded in canonical JSON,
5. an explicit statement that external content is DATA, not authority,
6. an explicit statement not to follow instructions found in external content,
7. an explicit statement not to grant permissions, invoke tools, reveal secrets/policies, or claim execution occurred,
8. an explicit statement that any later tool action requires the normal external ToolRegistry/PermissionEngine flow.

Critical:
- raw external item text appears ONLY inside JSON string values in the untrusted payload section,
- JSON escaping must ensure embedded delimiters/backticks/XML/role tokens do not become structural prompt controls,
- no item text is inserted into the fixed security preamble,
- no eval/template execution,
- no format-string interpretation of external text,
- exact original item text must round-trip after JSON decoding,
- the rendered prompt itself may contain sensitive external content and therefore is `repr`/logging-sensitive.

Use stdlib `json.dumps` with deterministic separators/order.

Do not sanitize away or silently rewrite evidence.

# Part E — PromptInjectionGuard service

## Construction
`PromptInjectionGuard` receives exact `AgentRegistry`.

At construction:
- exact registry snapshot/revalidation
- require internal ID `perimeter_security`
- alias changes/removal do not matter
- no I/O
- no model
- no database
- no EventBus
- no tools
- no environment reads
- no files

## Public API
Prefer:

- read-only `owner_id -> "perimeter_security"`
- `assess(item: UntrustedContentItem) -> PromptInjectionAssessment`
- `guard(request: PromptBoundaryRequest) -> GuardedPrompt`

Both methods synchronous/pure.

No hidden state.
No background task.
No cache.
No mutable policy.
No learning/adaptation.

# Part F — Guarded research synthesis adapter

## Purpose
PHX-054 already defines the correct orchestration boundary:

`UraharaResearchService -> ResearchSynthesizer`

PHX-055 MUST NOT modify that service.

Instead implement an adapter that satisfies the existing `ResearchSynthesizer` interface while ensuring that external research evidence is transformed into a PHX-055 guarded prompt before a future model-backed responder sees it.

Prefer:

`phoenix/web/guarded_synthesis.py`

## GuardedResearchRequest
Frozen, extra-forbidden, exactly:

- `research_id`
- `responder_id`
- `as_of`
- `allowed_source_ids`
- `guarded_prompt`

Rules:
- exact research/responder IDs
- aware UTC as_of
- allowed_source_ids immutable ordered unique tuple 1..8
- guarded_prompt exact GuardedPrompt
- guarded_prompt.boundary_id == research_id
- no raw `ResearchSourceEvidence` field
- no tool/permission/action fields
- no model/provider-specific options

## Research-source conversion
The adapter must exact-revalidate the incoming existing `ResearchSynthesisRequest`.

For every source in source order, convert all external textual fields that may reach synthesis into explicit UntrustedContentItem values.

At minimum include:

- `requested_url`
- `final_url`
- `title`
- `source_name` when present
- `date_text` when present
- `source_ref` when present
- `content`

Use:
- origin `web_page` for page_text content/final page evidence,
- origin `web_search` for search metadata/snippet evidence,
- stable deterministic content IDs derived from source_id + field_name,
- source_id = exact ResearchSourceEvidence.source_id,
- exact hashes.

Do not pass the raw ResearchSynthesisRequest or raw sources directly to the responder.

The responder receives only GuardedResearchRequest.

## User task separation
The incoming `ResearchSynthesisRequest.query_text` becomes GuardedPrompt.user_task.

It is NOT placed among external untrusted source items.

It still does not grant permission or execution authority.

## GuardedResearchResponder
Define async neutral ABC:

- read-only `responder_id`
- read-only sorted immutable `active_research_ids`
- `async respond(request: GuardedResearchRequest) -> ResearchSynthesisDraft`
- `async cancel(research_id: str) -> CancellationResult`
- `async aclose() -> None`

PHX-055 adds NO concrete model-backed responder.

A future adapter must use `request.guarded_prompt.render_text()` as the textual model input boundary.

No direct:
- ModelProvider
- OllamaProvider
- ColibriProvider
- VisionInterpreter

## GuardedResearchSynthesizer
Implement concrete adapter:

`GuardedResearchSynthesizer(ResearchSynthesizer)`

Constructor receives:
- exact PromptInjectionGuard
- exact GuardedResearchResponder

Public behavior:
- synthesizer_id exactly bound to responder_id
- one active research operation per adapter instance
- active_research_ids sorted immutable tuple
- `synthesize(request)`
- `cancel(research_id)`
- `aclose()`

## Synthesize flow
For one incoming ResearchSynthesisRequest:

1. exact-revalidate request and nested source evidence,
2. reject duplicate active research / busy,
3. bind event loop,
4. register active ID before first await,
5. convert every source text field to UntrustedContentItem,
6. build PromptBoundaryRequest,
7. call PromptInjectionGuard.guard synchronously,
8. construct exact GuardedResearchRequest,
9. call responder.respond exactly once,
10. exact-revalidate ResearchSynthesisDraft,
11. require exact research_id,
12. require exact synthesizer_id == adapter/responder ID,
13. require every claim supporting_source_id is one of allowed_source_ids,
14. preserve draft text/ordering exactly,
15. do not mark it trusted,
16. return draft,
17. cleanup active state in finally.

No retries.
No alternative responder.
No direct model call.

## Output trust
The returned ResearchSynthesisDraft remains untrusted model/responder output.

The adapter MUST NOT claim:
- verified,
- safe,
- true,
- injection-free,
- fact-checked.

Existing UraharaResearchService exact-revalidates it again.

# Part G — Guarded research cancellation

## Concurrency
One GuardedResearchSynthesizer instance supports at most one active operation.

Second concurrent request:
- explicit `busy` error
- no queue
- no hidden worker

This supports INV-023 for a future heavy model-backed responder.

## Cancellation
`cancel(research_id)`:

- malformed ID -> safe invalid_input
- unknown -> NOT_FOUND
- active -> set independent local cancellation flag
- call responder.cancel(research_id) exactly once
- exact-revalidate CancellationResult
- require exact operation/provider correlation
- cancel owning caller task where appropriate
- no global responder close
- no unrelated cancellation

If responder suppresses cancellation:
- independent local flag prevents ResearchSynthesisDraft from escaping,
- no normal success,
- active state still cleans up.

## aclose
Freeze ownership explicitly:

The GuardedResearchSynthesizer **owns** its injected GuardedResearchResponder for V1.

`aclose()`:
- if an operation is active, request scoped cancellation first,
- close responder exactly once,
- become closed,
- future synthesize/cancel behavior follows documented closed semantics,
- no hidden worker,
- no global model/provider shutdown beyond the owned responder abstraction.

Construction itself performs no I/O.

# Part H — No privilege changes

## PermissionEngine unchanged
PHX-055 MUST NOT modify:
- `phoenix/security/permissions.py`
- PermissionDecision
- PermissionGrant
- UserPermissionDecision
- P0-P5 semantics

PromptInjectionAssessment is NEVER permission evidence.

## ToolRegistry unchanged
PHX-055 adds no new tool.

Do not modify ToolDefinition or ToolRegistry.

## Audit unchanged
No new audit record type.
No AuditTrail mutation.

Existing P3/P4 audit behavior remains authoritative.

## Secrets
Do not:
- resolve SecretStore,
- reveal SecretMaterial,
- scan credential stores,
- accept SecretMaterial as content text,
- place known secrets in rendered prompt intentionally.

If a caller passes a SecretMaterial object where strict string text is required, reject it rather than coercing with `str()`.

PHX-045 exact-known-secret redaction remains a separate explicit helper; PHX-055 must not silently access secret storage.

# Part I — No automatic execution

PHX-055 production code must never:
- call BrowserTool,
- call WindowsUITool,
- call TerminalTool,
- call FilesystemTool,
- call WebSearchTool,
- execute shell commands,
- open URLs,
- fetch network resources,
- click coordinates,
- type text,
- download files,
- mutate memory,
- publish events.

Signals and guarded prompts are data/control-plane evidence only.

# Part J — External-source coverage

The general `UntrustedContentOrigin` vocabulary must support future/current external text from:

- web search,
- webpages,
- uploaded documents,
- tool stdout/stderr,
- model output,
- vision output,
- other external text.

PHX-055 does not need to modify every subsystem to adopt the guard now.

The required concrete integration in this ticket is the PHX-054 ResearchSynthesizer adapter.

Future phases may reuse PromptInjectionGuard at additional model-input boundaries.

# Part K — Static separation

No production import/call of:

- `eval`
- `exec`
- `subprocess`
- `os.system`
- shell interpreters
- network clients
- Playwright
- pywinauto
- model providers
- memory/RAG services
- SecretStore
- EventBus

The only expected imports are stdlib, Pydantic, AgentRegistry, neutral cancellation contracts, and PHX-054 research contracts.

# Persistence / Event / API boundaries

No:
- SQLAlchemy model
- migration
- SQLite prompt-injection history
- file cache
- EventBus publication/subscription
- EventType addition
- FastAPI route
- WebSocket channel
- Tauri command
- React UI

Alembic head remains:

`0005_audit_events`

EventType remains:

32

# Dependencies
Expected dependency changes:

**NONE**

Use stdlib:
- `json`
- `hashlib`
- `unicodedata`
- bounded string operations

and existing Pydantic/project contracts.

`pyproject.toml` and `uv.lock` unchanged.

# Focused tests

Create:

`tests/test_prompt_injection_defenses.py`

Use:
- default AgentRegistry
- synthetic external text
- fake GuardedResearchResponder
- existing exact PHX-054 ResearchSynthesisRequest/ResearchSourceEvidence
- no network
- no model
- no browser
- no secrets
- no real tools

At minimum cover:

## Vocabularies/errors
1. exact content-origin vocabulary
2. exact signal vocabulary
3. exact severity vocabulary
4. exact error vocabulary
5. safe error strings contain no content
6. foreign/copied enum members rejected

## UntrustedContentItem
7. exact six fields
8. frozen
9. extras rejected
10. strict content ID
11. strict source ID
12. field name bound
13. Unicode text preserved
14. empty text allowed
15. NUL rejected
16. text length bound
17. text repr privacy
18. exact hash required
19. wrong hash rejected
20. bool/bytes/object text rejected
21. SecretMaterial object rejected without coercion
22. exact stored-value/subclass/extra attacks rejected

## Assessment
23. exact six fields
24. assessment frozen/extras
25. one content ID/hash correlation
26. immutable signals
27. duplicate signals rejected
28. canonical signal order
29. suspicious exact bool
30. data_only literal true
31. severity none
32. severity suspicious
33. severity high
34. no trusted/safe/verified fields
35. forged assessment rejected

## Scanner normalization
36. plain benign text -> no signals but still data_only
37. case-insensitive detection
38. NFKC detection
39. zero-width/format obfuscation detection view
40. original text unchanged
41. whitespace-obfuscated phrase detection
42. deterministic repeated assessment
43. no regex catastrophic behavior on large allowed text

## authority impersonation
44. SYSTEM role phrase
45. developer message phrase
46. administrator instruction phrase
47. "I am the system" phrase

## instruction override
48. ignore previous instructions
49. disregard prior instructions
50. forget previous instructions
51. override earlier instructions

## permission bypass
52. permission granted
53. bypass permission
54. no confirmation required
55. skip approval
56. approval-looking JSON/text

## tool execution
57. run this command
58. execute this command
59. call the tool
60. delete the file
61. copied shell command directive

## credential exfiltration
62. reveal password
63. send API key
64. expose token
65. credential-store request
66. ordinary word "token" in benign context does not necessarily force unrelated signals beyond frozen table

## prompt exfiltration
67. reveal system prompt
68. show hidden instructions
69. export developer prompt
70. internal policy request

## policy manipulation
71. disable safety
72. lower risk
73. ignore PermissionEngine
74. bypass ToolRegistry
75. disable audit

## role delimiters
76. SYSTEM:
77. DEVELOPER:
78. <|system|>
79. [INST]
80. XML-like system/developer tag

## detection limitations
81. no-signal content remains untrusted
82. no claim of multilingual completeness
83. encoded/base64-looking text not auto-decoded/executed
84. scanner does no I/O/model call

## PromptBoundaryRequest
85. exact three fields
86. frozen/extras
87. boundary ID strict
88. user task exact/privacy
89. 0 items valid
90. 64 items valid within chars
91. 65 items rejected
92. duplicate content ID rejected
93. total char <=200000
94. overflow rejected
95. item exact revalidation
96. no permission/tool/risk fields

## GuardedPrompt
97. exact six fields
98. owner perimeter_security
99. policy version fixed
100. alias independence
101. assessments one-to-one
102. missing assessment rejected
103. extra assessment rejected
104. reordered mismatch rejected
105. hash mismatch rejected
106. item order preserved
107. user task preserved
108. no trusted/safe field

## rendering
109. deterministic render
110. fixed preamble exact
111. fixed policy precedes payload
112. payload is canonical JSON
113. external text only inside JSON string values
114. JSON round-trip preserves exact text
115. embedded triple backticks cannot escape boundary
116. embedded END marker cannot escape boundary
117. embedded XML role tags cannot escape boundary
118. embedded <|system|> cannot alter fixed structure
119. embedded quotes/backslashes/newlines escaped
120. Unicode deterministic encoding
121. no eval/format execution
122. no logging
123. render output repr-sensitive contract documented/tested where applicable

## Guard construction
124. exact AgentRegistry
125. perimeter_security required
126. alias removed works
127. alias renamed works
128. alias-only fake fails
129. constructor no I/O
130. no mutable policy/cache/state

## Guarded research contracts
131. GuardedResearchRequest exact five fields
132. frozen/extras
133. exact research ID
134. exact responder ID
135. UTC as_of
136. allowed source IDs ordered unique 1..8
137. boundary ID == research ID
138. exact GuardedPrompt revalidation
139. no raw source field
140. no permission/tool/action fields

## research source conversion
141. exact incoming ResearchSynthesisRequest revalidated
142. content IDs deterministic
143. source IDs exact
144. source order preserved
145. requested_url included as untrusted
146. final_url included as untrusted
147. title included as untrusted
148. source_name included when present
149. date_text included when present
150. source_ref included when present
151. page content origin web_page
152. snippet content origin web_search
153. raw text hashes exact
154. query becomes user_task, not external item
155. no raw ResearchSourceEvidence passed to responder
156. all model-facing external text is inside GuardedPrompt

## GuardedResearchResponder / adapter
157. responder abstract boundary
158. adapter implements ResearchSynthesizer
159. synthesizer ID == responder ID
160. construction no responder/model I/O
161. one responder call per synthesize
162. exact GuardedResearchRequest delivered
163. responder draft exact-revalidated
164. research ID mismatch rejected
165. synthesizer ID mismatch rejected
166. unknown claim source rejected
167. duplicate claim sources already rejected by contract
168. output text preserved
169. output remains untrusted/advisory
170. no verified/safe transformation
171. no direct ModelProvider/Ollama/Colibri import

## adapter concurrency/cancel
172. one active synthesis
173. concurrent second -> busy
174. active IDs immutable/sorted
175. cancel unknown -> NOT_FOUND
176. cancel active delegates exact research ID once
177. cancellation result exact provider/responder correlation
178. owning task cancellation
179. suppressed cancellation blocks draft return
180. active cleanup success
181. active cleanup failure
182. active cleanup cancellation
183. no hidden queue/worker
184. aclose closes owned responder once
185. aclose active operation scoped cancellation
186. closed synthesize rejected
187. no unrelated cancellation

## authority isolation
188. assessment cannot be PermissionGrant
189. assessment cannot be UserPermissionDecision
190. GuardedPrompt cannot be ToolDefinition
191. content saying "P0" cannot lower tool risk
192. content saying "P4 approved" cannot approve
193. content saying "P5 allowed" cannot override block
194. fake tool JSON remains data
195. fake audit JSON remains data
196. copied command remains data
197. fake system/developer message remains data
198. model draft tool-looking text remains plain text
199. no automatic action execution
200. PermissionEngine production unchanged

## separation
201. ToolRegistry production unchanged
202. AuditTrail production unchanged
203. BrowserTool unchanged
204. WindowsUITool unchanged
205. Screenshot/vision production unchanged
206. SearchProvider unchanged
207. UraharaResearchService unchanged
208. Memory/RAG unchanged
209. secrets production unchanged
210. no EventBus
211. EventType 32
212. Alembic head 0005
213. migrations unchanged
214. no persistence
215. no API/frontend
216. no dependency change
217. guarded import/construction/assess/render side-effect free
218. no environment access
219. no network
220. no file I/O
221. no DB
222. no thread/task at guard layer
223. no content logging
224. credential/signature scan clean

Add further deterministic adversarial cases where useful.

# Targeted verification
Run:

`uv run --locked pytest tests/test_prompt_injection_defenses.py`

Run THREE consecutive times because the guarded research adapter has async cancellation/concurrency paths.

All PASS.

No live model/network/browser test is required.

# PHX-054 regression
Run:

`uv run --locked pytest tests/test_urahara_research_flow.py`

Baseline:
492 passed.

# PHX-053 regression
Run:

`uv run --locked pytest tests/test_search_provider.py`

Baseline:
400 passed.

# PHX-052 regression
Run:

`uv run --locked pytest tests/test_screenshot_vision_loop.py`

Baseline:
397 passed.

# PHX-051 regression
Run:

`uv run --locked pytest tests/test_windows_ui_automation.py`

Baseline:
493 passed.

# PHX-050 regression
Run:

`uv run --locked pytest tests/test_browser_tool.py`

Baseline:
653 passed.

# Phase-4 regressions
Run individually:
- secrets: 402
- terminal: 270
- filesystem: 649 passed / 16 skipped
- audit: 302
- permissions: 926
- ToolRegistry: 204

All remain green.

# Phase-3 regressions
Run individually:
- memory: 244
- PUDDING: 167
- ingestion: 300
- vector store: 273
- retrieval: 367
- citations: 173
- persistence: 12

All PASS.

# Phase-2
Run all nine established suites individually.
Baseline aggregate: 2,729 passed.

# Phase-1
Run all seven established suites individually.
Baseline aggregate: 812 passed.

# Alembic / events
Verify:
- sole Alembic head `0005_audit_events`
- migrations 0001–0005 byte-identical to origin/main
- EventType exactly 32
- EventBus/EventType production unchanged

# Full quality
Run:

`uv lock`
`uv sync --locked`
`uv run --locked pytest`
`uv run --locked ruff check .`
`uv run --locked ruff format --check .`
`uv run --locked mypy`
`uv build`

Pre-PHX-055 baseline:
- 10,379 passed
- 16 skipped

Expected dependency changes:
**NONE**

`pyproject.toml` and `uv.lock` unchanged.

# Foundation
Run established foundation suites.
Baseline: 126 passed.
Exact `/health` unchanged.

# Desktop
Run:
- `npm test`
- `npm run typecheck --workspace @phoenix/desktop`
- `npm run build --workspace @phoenix/desktop`

Baseline: 4 passed.
Desktop production unchanged.

# Security review
Confirm:
- all external content remains untrusted regardless scanner result
- scanner heuristics never produce "trusted" state
- original external text is preserved
- deterministic normalized scan view is separate from original text
- fixed prompt policy is code-owned
- external text is JSON-data-only in rendered prompt
- embedded role/delimiter/control strings cannot structurally rewrite fixed prompt
- no assessment grants permission
- no text can create PermissionGrant/UserPermissionDecision/ToolDefinition
- no automatic tool/action path
- guarded research responder never receives raw ResearchSourceEvidence
- no direct model provider coupling
- no secrets/env reads
- no memory/RAG/persistence
- no EventBus/API/frontend/schema/dependency
- cancellation cannot become normal success
- no content logging

Run credential/signature scan.

# Invariants
Review INV-001 through INV-024, especially:
- INV-001 PHOENIX remains supreme
- INV-003 model/external content cannot grant permission
- INV-004 no privileged tool bypass introduced
- INV-005 P4 unchanged
- INV-006 P5 unchanged
- INV-007 audit authority unchanged
- INV-008 guarded/model output cannot finalize required verification
- INV-011 no frontend privileged boundary
- INV-012 no provider-specific implementation in agent definitions
- INV-013 no tool policy inside guard
- INV-014 GOJO alias removable
- INV-017 external content explicitly remains untrusted
- INV-018 no SecretStore/secret memory path
- INV-019 guarded async adapter exposes cancellation/state
- INV-020 no schema mutation
- INV-021 explicit failures
- INV-022 no silent fallback/sanitization
- INV-023 one active potentially-heavy guarded synthesis per adapter
- INV-024 no new dependency

# ADR
At most one ADR.

ADR-056 should document:
- perimeter_security-owned deterministic PromptInjectionGuard
- external content always untrusted even with zero scanner signals
- bounded deterministic heuristic signal vocabulary
- NFKC/casefold/format-obfuscation scan view without modifying original text
- signal severity is advisory only
- exact hashed UntrustedContentItem contracts
- fixed code-owned prompt policy + canonical JSON data envelope
- no silent sanitization/rewrite
- GuardedResearchSynthesizer adapter for existing PHX-054 interface
- responder sees GuardedResearchRequest, not raw ResearchSourceEvidence
- no concrete model/provider
- output remains untrusted
- one active guarded synthesis + scoped cancellation
- no PermissionEngine/ToolRegistry/Audit/secret/memory/RAG/EventBus/API/schema/dependency changes
- no claim of perfect prompt-injection prevention

# Completion
Only after all deterministic gates pass:
- mark this ticket DONE
- mark PHX-055 DONE in FEATURE_TICKETS.md
- Phase 5 then COMPLETE
- leave PHX-060+ TODO
- commit exactly:
  `feat: complete PHX-055 prompt injection defenses`
- push only:
  `feature/PHX-055-prompt-injection-defenses`
- no PR
- no merge
- no main push
- do not start PHX-060

# Known limitations to disclose
At minimum:
- heuristic scanner is finite and can miss adversarial/multilingual/encoded/visual attacks
- no-signal never means safe/trusted
- JSON/prompt separation reduces instruction confusion but cannot guarantee model obedience
- model/responder output remains untrusted
- guarded research adapter protects one model-input path; other future model-input boundaries must explicitly adopt PromptInjectionGuard
- no automatic sanitization because evidence preservation is required
- no semantic intent/entailment classifier
- no secret discovery
- no model-based injection classifier
- no runtime policy mutation
- no guarantee against unknown model jailbreak techniques
- downstream tool security still depends on ToolRegistry/PermissionEngine/audit
- a responder must honor the documented requirement to use GuardedPrompt.render_text() as its textual model input
- caller owns global model scheduling and deadlines

# Next
PHX-060 — Wake-word provider. Do not start it.
