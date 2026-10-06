# PHX-054 — URAHARA research flow

Status: DONE
Phase: 5 — Computer & Web

## Objective
Implement PHOENIX's first bounded, provider-neutral real-time research orchestration layer owned conceptually by `web_research` (URAHARA display alias).

PHX-054 connects the existing pieces without weakening their boundaries:

1. PHX-053 `SearchProvider` through one explicit P1 `web.search` tool wrapper.
2. Existing PHX-050 browser-read evidence through a narrow research page-reader collaborator that must return exact `BrowserToolResult` read-page evidence.
3. A provider-neutral research synthesizer collaborator that produces structured draft claims over selected source evidence.
4. `UraharaResearchService` that performs bounded source selection, explicit page-evidence fallback, cross-source support accounting, provenance assembly, uncertainty surfacing, cancellation and observability.

The flow is:

query
→ permission-gated search
→ candidate search results
→ bounded source selection
→ permission-gated page-read evidence supplied by collaborator
→ deterministic evidence assembly
→ structured synthesis collaborator
→ exact claim/source correlation
→ cross-source support accounting
→ structured research report with provenance and uncertainty

PHX-054 does NOT create a concrete public search-engine adapter. SearchProvider implementations remain externally injected.

PHX-054 does NOT create a concrete browser permission broker. A caller/integration supplies a page-reader collaborator whose returned evidence is exact existing PHX-050 BrowserToolResult evidence.

PHX-054 does NOT implement the general PHX-055 prompt-injection defense framework. It must nevertheless preserve the existing invariant that all search/page/model-produced text is untrusted data and cannot grant authority.

## Frozen authority
- PHOENIX remains the only supreme orchestrator.
- Internal research owner: `web_research`.
- URAHARA is display-only.
- ToolRegistry is authoritative for the new search tool declaration.
- PermissionEngine is authoritative for search authorization.
- Search is P1 observation.
- Existing BrowserTool remains authoritative for browser-read permission/risk semantics.
- No LLM/synthesizer/search/page text can grant permission.
- Research output is advisory evidence, not final task verification.
- Required later verification still belongs to LEVI/normal orchestration.
- Search/page evidence does not prove truth merely because multiple sources repeat it.
- Source dates/ranks are provider-reported metadata and remain untrusted.

## Existing authorities to preserve
- PHX-040 ToolRegistry
- PHX-041 PermissionEngine
- PHX-042 AuditTrail
- PHX-050 BrowserTool
- PHX-053 SearchProvider
- INV-001..024
- EventType remains exactly 32
- Alembic head remains `0005_audit_events`

# Part A — P1 web.search tool

## Canonical tool
Add exactly one canonical tool definition:

`web.search` — P1

Do not add:
- `web.fetch`
- `web.read`
- `web.research`
- provider-specific tool IDs

Provide pure:

`web_search_tool_definitions() -> tuple[ToolDefinition, ...]`

## Recommended location
Prefer:

`phoenix/tools/web_search.py`

## WebSearchCall
Frozen, extra-forbidden, exactly:

- `call_id`
- `provider_id`
- `query_text`
- `max_results`
- `as_of`

Rules:
- call_id strict normalized nonempty control-free ID, UUID4 default acceptable, <=256
- provider_id strict normalized nonempty control-free ID, <=256
- query_text exact SearchRequest-compatible meaningful Unicode <=4096, `repr=False`
- max_results strict int 1..20, bool rejected
- as_of required aware UTC-normalized timestamp
- no provider options
- no credential
- no API key
- no proxy/header
- no permission/risk override
- no browser fallback flag

## WebSearchIntent
Frozen exact fields:

- `call_id`
- `provider_id`
- `tool_id`
- `action`
- `resource`
- `risk_level`
- `permission_requirement`

Canonical:
- tool_id = `web.search`
- action = `search`
- risk = P1

## Search resource privacy
The canonical resource MUST NOT include raw query text.

Bind exactly:
- operation `search`
- provider_id
- SHA-256 of exact UTF-8 query_text
- max_results
- as_of canonical UTC representation

Use deterministic canonical JSON.

The digest is correlation evidence only; it grants no authority.

## ToolRegistry anti-downgrade
`WebSearchTool` receives:
- exact ToolRegistry
- exact PermissionPolicy
- one injected SearchProvider

At construction:
- snapshot registry
- require canonical `web.search`
- compare every ToolDefinition field against canonical declaration
- reject missing/forged/risk/schema/permission/timeout/cancellation/audit/platform/environment drift
- provider_id must exactly equal injected provider.provider_id after exact normalization
- additional unrelated tools allowed
- construction performs no provider/network/browser/DB/event I/O

## Permission flow
`async execute(...)` accepts:
- exact WebSearchCall
- exact PermissionRequest
- optional grants
- optional user_decision

Require exact request correlation:
- tool_id
- action
- resource

Then call existing PermissionEngine exactly once.

If decision is not ALLOWED:
- return DENIED
- attempted=False
- response=None
- error_code=None
- zero SearchProvider.search I/O
- no BrowserTool
- no audit rows introduced by PHX-054

P1 semantics remain PHX-041 semantics.

## Allowed search execution
On allowed decision:
- construct exactly one SearchRequest
- request_id = call_id
- query_text = exact call query
- max_results = call max
- as_of = call as_of
- call injected SearchProvider.search exactly once
- exact-revalidate SearchResponse
- call response.validate_request(request, provider_id)
- no retry
- no alternate provider
- no browser fallback
- no query rewriting

Empty SearchResponse results are a successful search result, not a research-success claim.

## Search result status
Create frozen `WebSearchToolResult` with exactly:

- `call_id`
- `provider_id`
- `tool_id`
- `resource`
- `status`
- `attempted`
- `permission_decision`
- `response`
- `error_code`

Rules:
- denied -> DENIED, attempted=False, no response/error
- allowed valid response -> SUCCESS, attempted=True, exact response, no error
- provider error -> attempted=True, no response, explicit safe mapped error
- no `effect_applied`; search is observation only
- no audit IDs for P1

Use a closed safe `WebSearchErrorCode` including at least:
- invalid_input
- invalid_response
- unavailable
- timeout
- rate_limited
- unauthorized
- busy
- closed
- wrong_event_loop
- cleanup_failure

Map exact SearchProviderErrorCode values without exposing private provider diagnostics.

## Search cancellation
WebSearchTool exposes request-scoped:

`async cancel(call_id) -> CancellationResult`

Rules:
- validate exact call ID
- delegate only to injected provider.cancel(call_id)
- exact-revalidate CancellationResult
- require provider_id correlation and operation_id == call_id
- no global shutdown
- no provider close
- no hidden task
- cancellation acknowledgement is not proof remote search stopped

## Search provider ownership
Injected SearchProvider remains caller-owned.

WebSearchTool:
- does not close it automatically
- does not mutate it
- does not discover another provider
- does not route/fallback

# Part B — research page evidence boundary

## Existing BrowserTool remains authoritative
PHX-054 must NOT modify BrowserTool production.

Page retrieval is represented to the research flow through exact existing PHX-050 `BrowserToolResult` evidence for `BrowserOperation.READ_PAGE`.

This ensures research does not invent a second browser permission model.

## ResearchPageRequest
Create frozen exact fields:

- `operation_id`
- `source_result_id`
- `requested_url`
- `max_chars`

Rules:
- operation/source IDs strict normalized
- requested_url must exactly satisfy PHX-053 public HTTP(S) URL admission semantics
- max_chars strict int 1..50000
- no permission evidence
- no browser session ID
- no tool callback
- no credentials

## ResearchPageReader
Define a provider-neutral async ABC/protocol used only by UraharaResearchService:

- read-only `reader_id`
- read-only sorted immutable `active_operation_ids`
- `async read(request: ResearchPageRequest) -> BrowserToolResult`
- `async cancel(operation_id: str) -> CancellationResult`
- `async aclose() -> None`

Critical:
- PHX-054 adds NO concrete production reader.
- A future/integration reader is responsible for obtaining BrowserTool permission evidence and executing existing BrowserTool.
- UraharaResearchService MUST exact-revalidate returned BrowserToolResult before consuming any page text.
- The service MUST NOT trust the reader merely because it implements the interface.

## Required BrowserToolResult evidence
A usable page read MUST be exact PHX-050 BrowserToolResult and satisfy:
- operation == READ_PAGE
- status == SUCCESS or PARTIAL_SUCCESS only if PHX-050 contract permits truncated read evidence
- attempted=True
- effect_applied=False
- permission_decision.status == ALLOWED
- permission_decision.risk_level == P1
- tool_id == `browser.read_page`
- final_url present
- text present
- no error_code
- session/resource structure exact per BrowserToolResult validation
- requested/expected URL in the Browser resource exactly equals ResearchPageRequest.requested_url
- final_url must itself satisfy PHX-053 public HTTP(S) lexical admission
- text remains untrusted

If evidence is denied, malformed, wrong operation, wrong URL, wrong risk, wrong tool, missing text, failed, or uncorrelated:
- do not treat it as page evidence
- classify the page attempt explicitly
- never convert it into successful page evidence

## Research page fallback
The research flow may fall back from a missing/denied/failed/malformed page read to the already-admitted SearchResult snippet for that source.

This fallback MUST be:
- deterministic
- explicit
- observable in diagnostics/source evidence
- never silent

Fallback source kind:
- `page_text`
- `search_snippet`

No other fallback.

No BrowserTool call is made by UraharaResearchService itself.

# Part C — source selection and provenance

## ResearchRequest
Frozen, extra-forbidden, exactly:

- `research_id`
- `query_text`
- `as_of`
- `search_provider_id`
- `max_search_results`
- `max_sources`
- `max_age_days`
- `max_chars_per_source`
- `max_total_chars`

Rules:
- research_id strict normalized, UUID4 default acceptable
- query_text exact meaningful Unicode <=4096, `repr=False`
- as_of required aware UTC
- search_provider_id strict normalized
- max_search_results strict 1..20
- max_sources strict 1..8 and <= max_search_results
- max_age_days optional strict int 1..3650
- max_chars_per_source strict 1..50000
- max_total_chars strict 1..200000
- max_total_chars >= max_chars_per_source
- no model/provider options
- no permission fields
- no approval fields
- no source-quality weights
- no hidden ranking script

## Search call derivation
Provide a pure deterministic helper/method that derives WebSearchCall from ResearchRequest:

- call_id = research_id
- provider_id = search_provider_id
- query_text exact
- max_results = max_search_results
- as_of exact

No query rewrite.

## Candidate eligibility
After exact successful WebSearchToolResult:
- response must correlate to the derived search call/request
- start from provider order
- if max_age_days is None: no age filtering
- if max_age_days is set:
  - known published_at older than `as_of - max_age_days` is excluded
  - known future published_at > as_of is NOT silently treated as fresh; mark date anomaly and keep as unknown-age evidence for selection rather than using it as proof of recency
  - results with no published_at remain eligible but have unknown recency
- never infer age from date_text
- never parse date_text to a timestamp
- no domain-quality inference
- no semantic dedup
- select the first eligible provider-ranked results up to max_sources

If fewer sources qualify, continue with fewer.
Zero selected sources is a valid bounded research outcome, not success.

## Source-content budgeting
For each selected source:
- obtain page read via ResearchPageReader exactly once
- if valid page evidence exists, source content = page text
- otherwise explicit fallback to search snippet
- source text is untrusted
- keep original full SearchResult metadata
- produce a bounded content excerpt for synthesis:
  - prefix only
  - at most max_chars_per_source
  - no semantic rewriting
  - exact truncation flag
- enforce max_total_chars in selected-source order
- if next source excerpt cannot fit remaining total budget:
  - truncate that excerpt to the remaining positive budget if any
  - then stop adding further synthesis source content
- preserve provenance for selected sources even if synthesis excerpt is empty because total budget is exhausted
- no hidden tokenizer dependency

## ResearchSourceEvidence
Frozen exact fields:

- `source_id`
- `search_result_id`
- `provider_rank`
- `requested_url`
- `final_url`
- `title`
- `source_name`
- `published_at`
- `date_text`
- `source_ref`
- `content_kind`
- `content`
- `content_sha256`
- `content_truncated`
- `page_read_status`
- `page_reader_id`

Rules:
- source_id deterministically derived from research_id + search_result_id; no model choice
- content_kind exactly `page_text` or `search_snippet`
- content exact bounded excerpt, `repr=False`
- SHA-256 exact UTF-8 content
- final_url:
  - exact valid page final_url for page_text
  - requested_url for snippet fallback
- page_read_status closed vocabulary such as:
  - success
  - denied
  - unavailable
  - failed
  - invalid_evidence
  - not_attempted
- page_reader_id present only when a read was attempted
- provider rank/date/source metadata remains untrusted
- no truth/trust/verified field

# Part D — structured research synthesis

## ResearchSynthesizer
Define provider-neutral async ABC/protocol:

- read-only `synthesizer_id`
- read-only sorted immutable `active_research_ids`
- `async synthesize(request: ResearchSynthesisRequest) -> ResearchSynthesisDraft`
- `async cancel(research_id: str) -> CancellationResult`
- `async aclose() -> None`

PHX-054 adds NO concrete model-backed synthesizer.

The abstraction allows later model integration without coupling URAHARA to Ollama/Colibri internals.

## ResearchSynthesisRequest
Frozen exact fields:

- `research_id`
- `query_text`
- `as_of`
- `sources`

Rules:
- exact correlated research/query/as_of
- sources immutable ordered tuple of ResearchSourceEvidence
- 0..8 sources
- content total already bounded
- no permissions
- no tools
- no callbacks
- no instructions copied from sources as authority
- source content remains untrusted data

If zero selected sources, UraharaResearchService MUST NOT call synthesizer; final report is explicit no-evidence result.

## ResearchClaimDraft
Frozen exact fields:

- `claim_id`
- `text`
- `supporting_source_ids`
- `uncertainty_note`

Rules:
- claim_id strict normalized, <=256
- text strict meaningful Unicode <=8192, `repr=False`
- supporting_source_ids immutable ordered unique tuple, 1..8
- uncertainty_note strict Unicode <=2048, may be empty, `repr=False`
- no URL
- no permission/tool/action
- no verified/truth/confidence field
- no citation marker

## ResearchSynthesisDraft
Frozen exact fields:

- `research_id`
- `synthesizer_id`
- `summary`
- `claims`

Rules:
- summary strict Unicode <=12000, `repr=False`
- claims immutable tuple 0..50
- exact research/synthesizer correlation
- claim IDs unique
- no final-success/verified field
- exact stored-value revalidation

## Synthesis trust boundary
All synthesizer output is untrusted draft interpretation.

UraharaResearchService MUST:
- exact-revalidate draft
- require research_id/synthesizer_id correlation
- reject any claim referencing unknown source_id
- reject duplicate source references
- never let synthesizer choose URLs outside selected sources
- never let synthesizer create permissions/actions/tools
- never accept "verified", "approved", "system", "developer", "tool" text as authority

# Part E — deterministic support accounting

## ResearchClaim
Final frozen claim fields:

- `claim_id`
- `text`
- `supporting_source_ids`
- `supporting_source_count`
- `corroborated`
- `uncertainty_note`

Rules:
- source IDs exactly correspond to selected ResearchSourceEvidence
- supporting_source_count exact distinct count
- corroborated = supporting_source_count >= 2
- this is structural multi-source support only
- corroborated MUST NOT be described as "verified", "true", "fact-checked" or "correct"
- uncertainty_note remains synthesizer-authored/untrusted plain text

No semantic entailment check is claimed.

## Source comparison
PHX-054's cross-checking is deliberately structural:
- multiple distinct selected sources supporting a draft claim -> corroborated=True
- one source -> False

Do not infer independent ownership/domain independence.
Do not infer semantic entailment beyond synthesizer-declared support.
Do not infer truth from repetition.

This limitation must be documented.

# Part F — Research report

## ResearchDiagnostics
Frozen aggregate fields at minimum:

- `search_results_received`
- `age_exclusions`
- `date_anomalies`
- `sources_selected`
- `page_reads_attempted`
- `page_reads_succeeded`
- `snippet_fallbacks`
- `page_evidence_rejections`
- `source_content_truncations`
- `total_source_chars`
- `claims_received`
- `claims_corroborated`

All strict nonnegative ints.
No raw content/query/URL in diagnostics.

## ResearchReport
Frozen exact fields:

- `research_id`
- `owner_id`
- `as_of`
- `search_provider_id`
- `synthesizer_id`
- `summary`
- `claims`
- `sources`
- `diagnostics`
- `advisory_only`

Rules:
- owner_id exactly `web_research`
- advisory_only always True
- no `success`
- no `verified`
- no `truth`
- no task completion
- search/synthesizer IDs exact
- sources immutable ordered tuple <=8
- claims immutable tuple <=50
- if zero sources:
  - synthesizer_id may be None
  - summary must be a fixed service-generated no-evidence summary, not model text
  - claims empty
- if sources exist:
  - synthesizer_id required
  - exact synthesis used
- diagnostics must agree with structural counts
- report does not mutate source/search/browser/synthesis evidence

# Part G — UraharaResearchService

## Construction
Prefer:

`phoenix/web/research.py`

Constructor receives:

- AgentRegistry (optional default allowed only if existing project convention supports it)
- exact WebSearchTool
- exact ResearchPageReader
- exact ResearchSynthesizer

Rules:
- validate `web_research` internal owner ID
- aliases irrelevant
- constructor performs no network/browser/model/DB/event I/O
- collaborators remain caller-owned
- no automatic close of collaborators unless explicitly documented ownership is added; prefer caller-owned
- no provider discovery
- no browser/tool discovery
- no model routing

## Public API
Prefer:

- `owner_id` read-only -> `web_research`
- `active_research_ids` sorted immutable tuple
- `async research(request, *, search_permission_request, search_grants=(), search_user_decision=None) -> ResearchReport`
- `async cancel(research_id) -> CancellationResult`

No hidden background worker.

## Research execution order
For one request:

1. exact-revalidate ResearchRequest
2. register active research ID before first await
3. derive exact WebSearchCall
4. call WebSearchTool.execute once with caller-supplied exact permission evidence
5. exact-revalidate WebSearchToolResult
6. if search denied/failed:
   - return explicit bounded advisory report with no sources/claims and diagnostics OR raise a safe ResearchError according to a frozen documented policy
   - choose ONE policy and test it consistently
   - denial must never become successful evidence
7. validate SearchResponse correlation
8. deterministic age filter + provider-order selection
9. for each selected source in order:
   - create deterministic ResearchPageRequest operation ID
   - call page_reader.read exactly once
   - exact-revalidate returned BrowserToolResult
   - accept only valid correlated P1 read evidence
   - otherwise explicit snippet fallback
   - apply char budgets
10. if zero source evidence remains:
   - do not call synthesizer
   - return fixed advisory no-evidence report
11. construct exact ResearchSynthesisRequest
12. call synthesizer exactly once
13. exact-revalidate draft and correlation
14. validate every claim source reference
15. compute supporting_source_count/corroborated deterministically
16. assemble ResearchDiagnostics
17. return ResearchReport
18. cleanup active state in finally

No retries.
No provider fallback.
No browser-search fallback.
No recursive research.

## Safe research errors
Define closed `ResearchErrorCode` and `ResearchError`.

Include at least:
- invalid_input
- search_denied
- search_failed
- invalid_search_evidence
- page_reader_failure
- synthesis_failure
- invalid_synthesis_evidence
- busy
- cancelled
- cleanup_failure

Public error text must not expose:
- query
- source URL
- page text
- snippets
- model/synthesizer output
- permission reason
- raw provider/browser exception
- credential
- DB/path

Choose explicitly whether ordinary per-source page failures become fallback diagnostics versus fatal errors:
- expected denied/unavailable/invalid individual page evidence should use snippet fallback and continue
- collaborator catastrophic failure/cancellation may fail the research operation
- tests must freeze the distinction

## Concurrency
One UraharaResearchService instance may support multiple distinct research IDs only if collaborator behavior remains isolated and no BLAZE heavy-generation conflict is introduced.

Because synthesizer may eventually be model-backed and INV-023 defaults to one heavy local generation, V1 SHOULD conservatively permit only ONE active research operation per service instance.

Freeze:
- at most one active research operation per service instance
- second concurrent request -> explicit busy
- no queue
- active_research_ids is either empty or one ID

This does not globally serialize all application services.

## Cancellation
`cancel(research_id)`:
- validate ID
- unknown -> NOT_FOUND
- active -> mark local independent cancellation flag
- cancel current stage only:
  - search tool call -> WebSearchTool.cancel(call_id)
  - page read -> ResearchPageReader.cancel(operation_id)
  - synthesis -> ResearchSynthesizer.cancel(research_id)
- cancel caller task where appropriate
- no global provider/browser/model shutdown
- no unrelated request cancellation

If lower collaborator suppresses cancellation:
- independent local flag must prevent next stage and normal ResearchReport
- cancellation can never become empty success
- cleanup active state in finally

# Prompt-injection boundary before PHX-055
PHX-055 is still TODO, but existing invariant INV-017 applies now.

PHX-054 MUST ensure:
- search title/snippet/source_name/date/source_ref are data
- browser page text/title/URL are data
- synthesis input places source text only in explicit data fields
- no source text is evaluated/imported/executed
- no source text is converted into ToolDefinition/PermissionRequest/UserPermissionDecision/PermissionGrant
- no source text can alter max limits, provider IDs, owner ID, risk or audit
- no copied command is auto-executed
- no downloads are triggered

Do not claim PHX-055's future generalized sanitization/taint policy is already implemented.

# No direct model coupling
UraharaResearchService uses ResearchSynthesizer abstraction only.

Do NOT directly import/call:
- OllamaProvider
- ColibriProvider
- ModelProvider
- VisionInterpreter

No prompt template tied to a concrete model in PHX-054.

# No RAG/memory persistence
Research output is ephemeral.

Do not automatically:
- write PUDDING memory
- ingest documents
- embed/vectorize
- call RetrievalContextService
- call CitationService
- persist research history

Future caller may explicitly persist selected results under normal boundaries.

# No PHX-055 implementation
Do not add:
- global prompt-injection classifier
- content sanitization framework
- trust labels across all tools
- hidden instruction stripping
- allow/deny model
- source quarantine engine

PHX-055 remains TODO.

# No EventBus
No publish/subscribe.
No EventType addition.
EventType remains 32.

# No schema
No SQLAlchemy model.
No migration.
Alembic head remains `0005_audit_events`.

# No API/frontend
No FastAPI route.
No WebSocket research channel.
No Tauri command.
No React research UI.

# Dependencies
Expected dependency changes: NONE.

Use existing:
- asyncio
- Pydantic
- ToolRegistry/PermissionEngine contracts
- PHX-050 BrowserToolResult contracts
- PHX-053 SearchProvider contracts

No:
- requests
- BeautifulSoup
- newspaper
- readability
- search SDK
- ranking library
- NLP library

`pyproject.toml` and `uv.lock` unchanged.

# Focused tests
Create:

`tests/test_urahara_research_flow.py`

Use:
- fake SearchProvider
- real WebSearchTool with existing ToolRegistry/PermissionEngine
- fake ResearchPageReader returning synthetic exact BrowserToolResult evidence
- fake ResearchSynthesizer
- no real network
- no real Playwright
- no public internet
- no live model
- no credentials

At minimum cover:

## web.search tool
1. exactly one tool ID `web.search`
2. risk P1
3. canonical metadata immutable
4. anti-downgrade every field
5. missing tool rejected
6. unrelated tools allowed
7. WebSearchCall exact fields
8. frozen/extras rejected
9. provider ID strict
10. query exact/privacy
11. max_results bounds
12. as_of UTC
13. pure describe
14. resource contains query hash not query
15. canonical resource deterministic
16. constructor no provider I/O
17. exact provider ID binding
18. request tool correlation
19. request action correlation
20. request resource correlation
21. PermissionEngine exactly once
22. denied -> zero provider search
23. allowed -> exactly one provider search
24. exact SearchRequest derivation
25. empty search response success
26. malformed provider response rejected
27. provider mismatch rejected
28. request mismatch rejected
29. max result mismatch rejected
30. provider SearchProviderError mapping
31. raw provider error privacy
32. no browser fallback
33. no retry
34. cancel active delegates exact ID
35. cancel unknown shape
36. cancellation response exact correlation
37. injected provider not closed

## page evidence
38. ResearchPageRequest exact fields
39. public URL admission
40. max_chars bounds
41. ResearchPageReader abstract boundary
42. fake page reader substitutability
43. exact BrowserToolResult READ_PAGE accepted
44. wrong operation rejected/fallback
45. wrong tool ID rejected/fallback
46. wrong risk rejected/fallback
47. nonallowed permission rejected/fallback
48. failure status rejected/fallback
49. missing text rejected/fallback
50. resource expected URL mismatch rejected/fallback
51. final URL public validation
52. malformed BrowserToolResult rejected/fallback
53. page text untrusted
54. page reader called once/source
55. no BrowserTool import/call from Urahara service if using neutral reader

## ResearchRequest
56. exact fields
57. frozen/extras
58. research ID strict
59. query exact/privacy
60. UTC as_of
61. provider ID strict
62. source/result bounds
63. max_sources <= max_search_results
64. max_age_days optional/bounded
65. char budgets
66. pure WebSearchCall derivation

## selection/recency
67. provider rank order preserved
68. max_sources enforced
69. no age filter when None
70. known old date excluded
71. boundary age included
72. missing date remains eligible
73. future date anomaly counted
74. date_text never parsed
75. no semantic dedup
76. zero selected sources valid

## source evidence/budgets
77. deterministic source IDs
78. page_text preferred when exact valid
79. explicit search_snippet fallback
80. page denial fallback observable
81. page unavailable fallback observable
82. invalid evidence fallback observable
83. per-source prefix truncation exact
84. total-char budget exact
85. total budget preserves selected-source provenance
86. content SHA-256 exact
87. content repr privacy
88. page reader ID correlation
89. page read status vocabulary
90. no source truth field

## synthesis
91. ResearchSynthesizer abstract boundary
92. synthesis request exact fields
93. zero sources -> synthesizer not called
94. one call when sources exist
95. exact research ID correlation
96. exact synthesizer ID correlation
97. malformed draft rejected
98. extra draft fields rejected
99. duplicate claim ID rejected
100. unknown supporting source rejected
101. duplicate supporting source rejected
102. source text fake tool instructions remain data
103. synthesis output fake approval text remains data
104. no action/tool/permission fields

## support accounting
105. one supporting source -> count1/corroborated false
106. two distinct sources -> count2/corroborated true
107. three source support count exact
108. structural corroboration never called verified
109. uncertainty note preserved
110. no entailment/truth claim

## report/diagnostics
111. ResearchDiagnostics exact counters
112. no raw content in diagnostics
113. ResearchReport exact fields
114. owner exactly web_research
115. alias independence
116. advisory_only true
117. no success/verified/truth fields
118. zero-source fixed summary
119. zero-source synthesizer_id None
120. source order deterministic
121. claim order preserved
122. diagnostics counts exact
123. provider/synthesizer correlation

## active/cancel
124. one active research per service
125. second concurrent -> busy
126. active IDs immutable/sorted
127. cancel missing -> NOT_FOUND
128. cancel during search targets WebSearchTool only
129. cancel during page read targets current page operation only
130. cancel during synthesis targets research ID only
131. cancellation isolation
132. suppressed search cancellation blocks next stage/result
133. suppressed page cancellation blocks next stage/result
134. suppressed synthesis cancellation blocks result
135. cleanup active state success
136. cleanup active state failure
137. cleanup active state cancellation
138. no queue/background worker

## separation/security
139. no concrete search provider
140. no real browser reader
141. no direct ModelProvider
142. no Ollama/Colibri
143. no memory/RAG/citations
144. no persistence
145. no EventBus
146. EventType count 32
147. Alembic head 0005
148. migrations unchanged
149. no API/frontend
150. no dependency change
151. no PHX-055 generalized defense
152. injection text cannot create permission/grant/tool
153. copied command not executed
154. no download
155. guarded import/construction side-effect-free
156. query/page/synthesis content not logged
157. credential/signature scan clean

Add deterministic adversarial cases where useful.

# Targeted verification
Run:

`uv run --locked pytest tests/test_urahara_research_flow.py`

Run THREE consecutive times because cancellation/concurrency is included.

All PASS.

No network/browser/model live smoke is required.

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
- secrets 402
- terminal 270
- filesystem 649 passed / 16 skipped
- audit 302
- permissions 926
- ToolRegistry 204

# Phase-3 regressions
Run individually:
- memory 244
- PUDDING 167
- ingestion 300
- vector store 273
- retrieval 367
- citations 173
- persistence 12

# Phase-2
Run all nine existing suites individually.
Baseline aggregate: 2,729 passed.

# Phase-1
Run all seven existing suites individually.
Baseline aggregate: 812 passed.

# Alembic/event checks
Verify:
- sole Alembic head `0005_audit_events`
- migrations 0001–0005 unchanged
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

Pre-PHX-054 baseline:
- 9,887 passed
- 16 skipped

Expected dependency changes:
NONE.

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
Desktop unchanged.

# Security review
Confirm:
- web.search is P1 and crosses ToolRegistry + PermissionEngine
- no provider search before permission
- raw query absent from resource
- no concrete provider/fallback/retry
- page evidence accepted only from exact allowed BrowserTool READ_PAGE evidence
- Urahara service does not directly bypass BrowserTool
- page/search/synthesis text remains untrusted
- no text can create PermissionGrant/UserPermissionDecision/ToolDefinition
- no downloads/click/fill/terminal/filesystem action
- no P4 action added
- structural corroboration does not claim truth
- no model/provider concrete coupling
- no memory/RAG/citations persistence
- no EventBus/API/frontend/schema/dependency
- cancellation cannot become normal success
- no content logging

Run credential/signature scan.

# Invariant review
Review INV-001..024, especially:
- INV-001 PHOENIX supreme
- INV-003 search/page/synth output cannot grant permission
- INV-004 actual search tool crosses ToolRegistry + PermissionEngine
- INV-005 no P4 change
- INV-006 P5 unchanged
- INV-007 no new P3/P4 execution
- INV-008 report is advisory, not final verification
- INV-011 no frontend direct provider/tool path
- INV-012 provider implementation absent from agent definitions
- INV-013 tool implementation does not decide agent policy
- INV-014 alias independence
- INV-017 web content untrusted
- INV-018 no secrets in normal memory
- INV-019 async research/collaborators expose state+cancellation
- INV-020 no migration
- INV-021 explicit failures
- INV-022 snippet fallback observable
- INV-023 one research/synthesis-heavy operation per service instance
- INV-024 no new dependency

# ADR
At most one ADR.

ADR-055 should record:
- `web.search` P1 wrapper over injected SearchProvider
- query hash in resource, not plaintext query
- exact PermissionEngine crossing and no P1 audit addition
- no concrete search provider/fallback
- page evidence accepted only as exact BrowserTool READ_PAGE evidence via neutral reader collaborator
- explicit snippet fallback
- deterministic provider-order + optional age filtering
- no date_text parsing
- bounded char-based evidence assembly
- provider-neutral ResearchSynthesizer
- structured support accounting, corroborated means >=2 declared supporting sources only
- no truth/verification claim
- advisory-only ResearchReport
- one active research per service
- scoped cancellation
- no direct model/browser/network fallback/memory/RAG/EventBus/API/schema/dependency
- PHX-055 generalized prompt-injection defenses remain future work

# Completion
Only after all deterministic gates pass:
- mark PHX-054 DONE in ticket
- mark PHX-054 DONE in FEATURE_TICKETS.md
- leave PHX-055 TODO
- commit exactly:
  `feat: complete PHX-054 URAHARA research flow`
- push only:
  `feature/PHX-054-urahara-research-flow`
- no PR
- no merge
- no main push
- do not start PHX-055

# Known limitations to disclose
At minimum:
- no built-in concrete search engine adapter
- no built-in concrete browser page-reader adapter/permission broker
- search/page access requires injected collaborators/integration
- no semantic domain-quality scoring
- max_age_days uses only provider-reported published_at
- date_text is never parsed
- page failure may fall back to search snippet
- char budgets are tokenizer-neutral
- synthesis is abstract/caller-supplied, not a concrete local model adapter
- supporting-source count is structural, not semantic entailment or truth verification
- model/search/page content can remain adversarial
- no PHX-055 generalized prompt-injection framework yet
- report remains advisory and cannot finalize task success

# Next
PHX-055 — Prompt-injection defenses. Do not start it.

## Completion evidence — 2026-10-06

All deterministic PHX-054 gates PASS. Starting feature HEAD:
be7fc0e971ec083fe1013074644069b406c2f7ff. Main remains
5694996f67befca1fd23784cfc4bea2b0ba80b7a. No PR/merge/main push or PHX-055 work.

Created phoenix/tools/web_search.py, phoenix/web/research.py and
tests/test_urahara_research_flow.py. Modified only DECISIONS.md (ADR-055),
FEATURE_TICKETS.md and this ticket. No existing runtime/test/dependency file changed.

Public search API: WebSearchCall, WebSearchIntent, WebSearchToolResult,
WebSearchErrorCode, WebSearchError, web_search_tool_definitions, WebSearchTool
(provider_id, describe, async execute/cancel). Exactly one web.search P1 definition;
all eleven declaration fields checked, strict exact snapshots and nested authority
forgeries rejected. Five-field call, seven-field intent and nine-field result.
Query formatting preserved/private in repr; canonical resource binds operation,
provider, SHA-256 query, max_results and canonical UTC as_of. Describe/construction
are pure. Exact request correlation precedes one PermissionEngine evaluation.
Denied: no provider call, attempted=False, response/error=None. Allowed: one exact
derived SearchRequest; response revalidated/correlated, including empty success.
Ten closed error codes match PHX-053; malformed/raw failures are privacy-safe.
No retry/fallback/close/audit addition. Cancellation delegates exact ID to borrowed
provider and revalidates operation/provider/status acknowledgement.

Research API: ResearchErrorCode/ResearchError; ResearchPageRequest/ResearchPageReader;
ResearchRequest/research_search_call; ResearchContentKind/ResearchPageReadStatus;
ResearchSourceEvidence; ResearchSynthesisRequest/ResearchClaimDraft/
ResearchSynthesisDraft/ResearchSynthesizer; ResearchClaim/ResearchDiagnostics/
ResearchReport; UraharaResearchService (owner_id, active_research_ids, async
research/cancel). Only neutral owner web_research; aliases can be renamed/removed.
Constructors/imports allocate no I/O/workers and retain caller-owned collaborators.

Page requests have exactly operation_id/source_result_id/requested_url/max_chars.
URL admission reuses PUBLIC PHX-053 SearchResult validation without private helper
imports/DNS. Reader ABC has read-only ID/sorted active IDs/read/cancel/aclose;
no concrete adapter. Raw/nested browser evidence exactly revalidated before use.
Require READ_PAGE/browser.read_page, allowed P1, attempted/no effect/no error,
valid success/partial read, exact call/resource URL/bound and public final URL.
Bad operation/tool/risk/permission/URL/resource/text/status/contract falls back
explicitly to exact snippet, with classification/counters; catastrophic exceptions
raise page_reader_failure. No direct browser execution or invented consent broker.

Nine-field ResearchRequest has strict integer bounds and UTC reference time.
Pure search call preserves research/provider/query/result/time values. Select first
eligible sources in provider order, 1..8, using optional published_at-only age
filter; cutoff equality includes, absent dates unknown, future anomalies retained.
Raw date_text stays unparsed; no source-quality inference/semantic dedup.
Per-source prefix <=50000 and total <=200000, preserving selected provenance after
budget exhaustion. Sixteen-field source evidence retains original search metadata,
deterministic identity, page/snippet kind, exact UTF-8 SHA-256, truncation, read
classification and reader ID. Page final URL retained; fallback final URL=requested.

Neutral synthesizer ABC exposes ID/sorted active IDs/synthesize/cancel/aclose.
No model implementation/import/call. Four-field synthesis request, four-field claim
draft and four-field synthesis draft exactly revalidate bounded nested evidence,
extra fields/subclasses/duplicates and research/synthesizer/source correlation.
Six-field final claims preserve order/text/uncertainty and derive distinct support
count plus corroborated=(count>=2). This is structural declared support only,
never truth/verification/independence/entailment. Twelve strict bounded diagnostics
agree with final source/claim counts; ten-field report is always advisory_only=True,
owner web_research, no success/verified/truth/completion field. Zero-source report
has fixed service summary, no claims, synthesizer_id=None and zero synth calls.

Execution: exact request/active registration -> one permissioned search -> exact
response -> eligibility/selection -> one page attempt per selected source -> explicit
fallback/budgets -> one synthesis only with sources -> exact support accounting ->
diagnostics/report -> finally cleanup. Search denial/failure raises safe closed error.
One active operation per service/event loop, no queue/background worker. Scoped
cancel sets independent flag and targets current search/page/synthesis only.
Suppressed CancelledError/uncancel cannot advance/finalize. Delayed acknowledgement
cannot cancel unrelated caller work after cleanup; finally clears every terminal path.

### Commands and final results

- THREE consecutive `uv run --locked pytest tests/test_urahara_research_flow.py -q`
  runs: **492 passed each**, no warning. Synthetic fake search/page/synth boundaries
  with REAL ToolRegistry/PermissionEngine only; no internet/browser/model/credentials.
- All **33** prior focused suites ran individually with locked pytest, exit 0.
  PHX-053 **400**, PHX-052 **397**, PHX-051 **493**, PHX-050 **653**.
- Phase 4: secrets **402**, terminal **270**, filesystem **649 passed/16 skipped**,
  audit **302**, permissions **926**, registry **204**; **2753 passed/16 skipped**.
- Phase 3: memory **244**, PUDDING **167**, ingestion **300**, vectors **273**,
  retrieval **367**, citations **173**; **1524 passed**, persistence **12**.
- Phase 2: registry **96**, orchestrator **101**, planner **151**, router **213**,
  task graph **348**, handoffs **214**, LEVI **242**, MAHORAGA **790**, KAIDO **574**;
  **2729 passed**. All nine suites individually.
- Phase 1: provider **61**, Ollama **108**, registry **83**, lifecycle **54**,
  embeddings **182**, Colibri **194**, benchmark **130**; **812 passed**.
- Final `uv run --locked pytest -q`: **10379 passed, 16 skipped**, one existing
  Starlette/httpx warning. Baseline 9887 plus all 492 final new cases.
  An earlier full run hit one existing 10 ms benchmark timing failure
  (`test_slow_optional_probe_does_not_consume_generation_deadline[2]`). Its log
  was retained externally; the unchanged benchmark suite then passed 130 tests,
  followed by this clean full rerun. No prior code/test or timeout was changed.
- `uv lock`; `uv sync --locked`: PASS, manifests/lock unchanged.
- `uv run --locked ruff check .`: PASS; `ruff format --check .`: **108 files** PASS;
  `uv run --locked mypy`: **102 source files** PASS.
- `uv build --out-dir <external-validation>/dist`: sdist/wheel PASS; logs/JUnit/
  archives stay outside repo/staging. Final builds contain both new runtime modules.
- Foundation bootstrap/API/config/persistence/events/hardware: **126 passed**,
  existing warning only; exact /health and source unchanged.
- Desktop `npm test`: **4 passed**; desktop workspace typecheck/build PASS.
- Protected review: **207 existing tracked files unchanged** from origin/main,
  including all prior production/tests/desktop/manifests and five migrations.
  Alembic heads **[0005_audit_events]**, EventType **32**. Credential/signature scan:
  **213 files**, zero matches. `git diff --check` PASS.

### Security, invariants, acceptance and limitations

Reviewed INV-001 through INV-024: PASS within scope. All **157 minimum matrix
boundaries** plus adversarial cases pass. Search is actual P1 registry/permission
execution; P4/P5/audit authority unchanged. Search/page/synth content cannot mint
grants/decisions/tools, change risk/budgets/IDs or trigger any action. Guarded uncached
imports/construction and fake execution deny file/SQLite/network/process/thread/
task/EventBus/logging/environment access. Only Pydantic's plugin flag is stubbed,
without reading any environment value; no secret environment lookup is permitted.
Static imports rule out concrete browser/model/secret/memory/RAG/persistence/UI/
PHX-055 bridges. No logging, download/click/fill/terminal/filesystem/Windows action,
provider fallback, citation formatting, event, schema or dependency integration.
Single ADR-055 records the architecture and frozen failure/cancellation policies.
Complete staged/commit/push verification is reported by the execution run.

Limitations: injected search/page/synthesis collaborators are required; no built-in
search engine, concrete page reader/permission broker or local model synthesizer.
Provider ranks/dates remain untrusted; no semantic quality/independence scoring,
date_text parsing or semantic dedup. Snippet fallback may provide less evidence.
Budgets count characters, not tokens; empty excerpts can retain selected provenance.
Lexical URLs do not prove DNS safety or navigation permission. Browser evidence
establishes supplied structure/correlation, not independent authenticity. Trusted
integration owns genuine consent, admission, replay control, cooperative deadlines,
resource lifetime and application-wide heavy-generation coordination; one-active
limit applies per service. Cancellation acknowledgement does not prove remote stop.
Declared source support proves neither semantic entailment nor truth. Adversarial
text remains possible; PHX-055 generalized defenses are not implemented. Reports
remain ephemeral/advisory and cannot finalize task success. Existing skips/warning
unchanged. Phase 5 remains INCOMPLETE pending PHX-055.

Next: **PHX-055 — Prompt-injection defenses; not started**.
