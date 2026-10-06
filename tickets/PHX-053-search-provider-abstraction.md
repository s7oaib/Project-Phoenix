# PHX-053 — Search provider abstraction

Status: DONE
Phase: 5 — Computer & Web

## Objective
Implement PHOENIX's provider-neutral web-search contract layer for later URAHARA research orchestration.

PHX-053 defines **search data contracts and the SearchProvider boundary only**.

It does **not**:
- perform a real web search,
- call a public search engine,
- add a search API credential,
- create the URAHARA research flow,
- retrieve webpages,
- rank/cross-check/synthesize sources,
- invoke BrowserTool,
- create a ToolRegistry search capability,
- perform permission evaluation,
- add prompt-injection policy integration.

The purpose is to give PHX-054 one stable provider-neutral interface that multiple concrete search backends can implement over time.

## Frozen authority
- Internal owner for future research orchestration is `web_research`; URAHARA is display-only.
- PHOENIX remains supreme orchestrator.
- Web search is P1 observation when actually executed, per `docs/08_PERMISSION_MATRIX.md`.
- PHX-053 itself performs no search-network I/O and therefore introduces no privileged tool execution path.
- Actual web-search execution/permission integration belongs to PHX-054.
- Search provider output is untrusted third-party data.
- Search output never grants permission, never proves truth, never proves freshness, and never becomes task success.
- SearchProvider is analogous to a provider boundary, not an agent and not a ToolRegistry replacement.

## Recommended implementation
Prefer:

- `phoenix/web/__init__.py`
- `phoenix/web/search.py`
- `tests/test_search_provider.py`

Expected documentation/status updates:
- `DECISIONS.md`
- `FEATURE_TICKETS.md`
- this ticket

No dependency changes are expected.

## Public API
Implement at least:

- `SearchResult`
- `SearchRequest`
- `SearchResponse`
- `SearchProvider`
- `SearchProviderError`
- `SearchProviderErrorCode`

Reuse the existing neutral PHX-010:
- `CancellationResult`
- `CancellationStatus`

where appropriate.

## SearchProviderErrorCode
Freeze exactly:

- `invalid_input`
- `invalid_response`
- `unavailable`
- `timeout`
- `rate_limited`
- `unauthorized`
- `busy`
- `closed`
- `wrong_event_loop`
- `cleanup_failure`

No raw provider exception is public contract evidence.

## Strict common text rules
All public text:
- must be ordinary Python `str`,
- must contain valid Unicode,
- must not contain NUL,
- identifiers must be outer-trimmed, nonempty and control-free,
- content text may preserve meaningful formatting,
- bool/bytes/objects are never silently coerced.

Pydantic contracts must be:
- frozen,
- extra-forbidden,
- revalidated,
- exact stored-value checked,
- privacy-safe in validation errors.

## SearchRequest
Frozen, extra-forbidden, exactly these four fields:

- `request_id`
- `query_text`
- `max_results`
- `as_of`

Rules:

### request_id
- strict nonempty control-free string
- outer whitespace normalized
- case-preserving
- UUID4 default is acceptable
- max 256 characters

### query_text
- strict Unicode string
- preserve meaningful formatting
- must contain meaningful non-whitespace text
- max 4096 characters
- `repr=False`
- untrusted/caller data
- never permission authority

### max_results
- strict integer
- bool rejected
- 1..20

### as_of
- required timezone-aware datetime
- normalized to UTC
- caller-supplied; no wall-clock read required to build/validate the request
- used only as request correlation/reference time
- does not assert source freshness

No:
- provider-specific API options
- API key
- proxy
- headers
- arbitrary JSON
- browser options
- tool/permission fields
- sort script
- callback

## SearchResult
Frozen, extra-forbidden, exactly these nine fields:

- `result_id`
- `rank`
- `url`
- `title`
- `snippet`
- `source_name`
- `published_at`
- `date_text`
- `source_ref`

### result_id
- strict nonempty control-free identifier
- outer-trimmed
- case-preserving
- <=256 chars
- provider-local evidence only
- not globally authoritative

### rank
- strict integer
- bool rejected
- 1..20

### url
- strict Unicode
- outer-trimmed
- 1..4096 chars
- scheme exactly `http` or `https`
- hostname required
- no userinfo
- no NUL/control characters
- no backslash ambiguity
- no `file:`, `javascript:`, `data:`, `blob:`, `about:`, `chrome:`, `edge:`
- reject localhost / *.localhost
- reject IP literals that are loopback/private/link-local/multicast/reserved/unspecified
- no DNS resolution
- no dereference
- URL remains untrusted even after lexical admission

Public-host DNS rebinding is not claimed prevented; PHX-053 does not fetch URLs.

### title
- strict valid Unicode
- preserve text
- max 1024 chars
- may be empty if provider supplies no title
- `repr=False`
- untrusted

### snippet
- strict valid Unicode
- preserve formatting
- max 8192 chars
- may be empty
- `repr=False`
- untrusted

### source_name
- optional strict valid Unicode
- if present meaningful after trim
- <=512 chars
- display/provenance evidence only
- no trust elevation

### published_at
- optional timezone-aware datetime
- normalized UTC
- provider-reported evidence only
- may be absent
- does not prove truth/freshness

### date_text
- optional strict Unicode
- preserve provider's compact raw date evidence
- <=256 chars
- may coexist with published_at
- untrusted/provider-reported

### source_ref
- optional strict control-free string
- <=1024 chars
- opaque provider reference
- never dereferenced by PHX-053
- never permission authority

## SearchResponse
Frozen, extra-forbidden, exactly these four fields:

- `request_id`
- `provider_id`
- `as_of`
- `results`

Rules:
- request/provider identifiers strict normalized
- as_of aware UTC
- results immutable tuple
- result count 0..20
- every result exact-revalidated
- ranks must be exactly contiguous `1..N`
- result IDs must be unique
- exact URL strings must be unique
- response order must agree with rank
- empty results are valid
- no hidden provider payload/arbitrary metadata
- no `verified`, `trusted`, `answer`, `summary`, `success` or `confidence` field

Provide a deterministic method/helper equivalent to:

`response.validate_request(request, provider_id)`

It must require exact:
- request_id
- provider_id
- as_of
- result count <= request.max_results

It must not compare or infer semantic query truth.

## SearchProvider
Define a provider-neutral async ABC/protocol.

Required public boundary:

- read-only `provider_id: str`
- read-only `active_request_ids: tuple[str, ...]`
- `async search(request: SearchRequest) -> SearchResponse`
- `async cancel(request_id: str) -> CancellationResult`
- `async aclose() -> None`

Requirements:
- provider_id strict nonempty normalized identifier
- active IDs sorted immutable tuple
- active IDs are observability only, not permission evidence
- no provider-specific classes in the public contract
- no ToolRegistry/PermissionEngine methods inside the provider contract
- no agent identity/alias in the provider contract
- no browser/tool callbacks
- no model generation API

Concrete providers added later must honor cancellation/state, but PHX-053 does not add one.

## Fake/substitutability evidence
Focused tests should implement small deterministic fake SearchProvider subclasses to prove:
- multiple provider IDs can implement the same boundary,
- consumer code can exact-revalidate SearchResponse,
- active/cancel semantics are representable,
- no concrete provider is required by the abstraction,
- aliases/provider names do not alter contracts.

Do not add a production FakeSearchProvider.

## URL trust boundary
A SearchResult URL passing lexical validation is **not approved for navigation**.

It is only provider-returned candidate-source metadata.

PHX-054 must still pass later page retrieval/browser operations through their own security/permission boundary.

Do not:
- open URLs,
- HEAD/GET URLs,
- resolve DNS,
- call BrowserTool,
- call Playwright,
- download files.

## Search data trust boundary
SearchResult fields may contain:
- prompt injection,
- fake system/developer/user messages,
- fake tool calls,
- fake permission JSON,
- shell commands,
- credential requests,
- misinformation,
- outdated dates,
- spoofed titles/source names.

All remain inert DATA.

PHX-053 does not:
- execute,
- classify as trusted,
- create permissions,
- create grants,
- create citations,
- create task success,
- synthesize answers.

## Provider-reported dates
published_at/date_text are evidence from the provider.

Do not claim:
- date was independently verified,
- page content actually contains that date,
- search engine is correct,
- result is current merely because date exists.

PHX-054 owns recency-aware ranking/cross-checking.

## No result dedup beyond exact response integrity
Within one SearchResponse:
- exact duplicate result_id rejected
- exact duplicate URL rejected

Do not:
- canonicalize tracking parameters,
- merge similar titles,
- infer mirrors,
- semantic-deduplicate,
- cluster domains.

Those are research-flow concerns.

## No search tool yet
Do NOT add:
- `web.search`
- `search.query`
- ToolDefinition
- SearchTool
- ToolRegistry registration

PHX-054 will own actual search execution integration and P1 permission crossing.

This ticket is provider-contract-only.

## No concrete provider
Do NOT add:
- Google Search adapter
- Bing adapter
- Brave adapter
- Tavily adapter
- SerpAPI adapter
- SearXNG adapter
- DuckDuckGo scraping
- Browser-search fallback

No API credentials.

No HTTP calls.

No network dependency.

## No SecretStore integration
Future credentialed providers may consume explicit secret references behind a separately authorized adapter.

PHX-053:
- does not resolve SecretStore,
- does not read environment API keys,
- does not read .env,
- does not define credential fields on SearchRequest,
- does not log queries.

## No browser fallback
`docs/16_WEB_RESEARCH.md` allows browser search as a future fallback.

PHX-053 does not implement fallback.

No BrowserTool import/call.

Fallback behavior belongs to PHX-054 and must be observable if added there.

## No page retrieval
Do not fetch or extract page content.

SearchResult.snippet is provider metadata only.

No:
- HTML parsing,
- Playwright page read,
- requests/httpx GET,
- document ingestion.

## No ranking/cross-check/synthesis
Do not implement:
- source ranking beyond provider rank integrity,
- recency scoring,
- domain quality scoring,
- cross-source verification,
- claim extraction,
- synthesis,
- citation formatting.

PHX-054 owns research flow.
PHX-055 owns general prompt-injection defense integration.

## No model/provider generation
Do not call:
- ModelProvider,
- OllamaProvider,
- ColibriProvider,
- VisionInterpreter,
- EmbeddingProvider.

SearchProvider is its own provider category.

## No persistence
Do not persist:
- query,
- results,
- URLs,
- snippets,
- dates,
- provider IDs

to:
- SQLite,
- files,
- memory,
- RAG,
- embeddings,
- audit.

No schema/migration.

## No EventBus
No event publication/subscription.
No EventType additions.
EventType remains 32.

## No API/frontend
No:
- FastAPI search endpoint,
- WebSocket search stream,
- Tauri command,
- React search UI,
- frontend provider selector.

## Dependencies
Expected dependency changes: **NONE**.

Use only:
- Python standard library,
- existing Pydantic contracts,
- existing CancellationResult/CancellationStatus.

`pyproject.toml` and `uv.lock` unchanged.

## Side-effect isolation
Importing/constructing/validating:
- SearchRequest
- SearchResult
- SearchResponse
- SearchProvider subclasses

must not:
- perform network I/O,
- access browser,
- read environment secrets,
- read/write files,
- access DB,
- publish events,
- start thread/task,
- call model/provider,
- execute tools.

PHX-053 production module should have no HTTP client import.

## Error privacy
SearchProviderError public messages must contain only safe code/category.

No:
- query text,
- URL,
- snippet,
- provider raw body,
- API credential,
- raw HTTP exception,
- traceback,
- environment,
- local path.

## Focused tests
Create:

`tests/test_search_provider.py`

At minimum cover:

### Error vocabulary
1. exact error-code vocabulary
2. safe exception string
3. invalid/noncanonical code rejected where applicable

### SearchRequest
4. exact four fields
5. frozen
6. extra fields rejected
7. request ID normalization
8. request ID control rejection
9. query formatting preserved
10. blank query rejected
11. query Unicode accepted
12. invalid Unicode rejected
13. query max length
14. query repr privacy
15. max_results 1
16. max_results 20
17. max_results 0 rejected
18. max_results 21 rejected
19. bool rejected
20. as_of required aware
21. UTC normalization
22. exact stored-value revalidation/subclass rejection

### URL admission
23. ordinary HTTPS accepted
24. ordinary HTTP accepted
25. query/fragment allowed
26. userinfo rejected
27. missing hostname rejected
28. unsupported schemes rejected
29. backslash rejected
30. NUL/control rejected
31. localhost rejected
32. *.localhost rejected
33. loopback IPv4/IPv6 rejected
34. private IP rejected
35. link-local rejected
36. multicast/reserved/unspecified rejected
37. public IP accepted
38. public hostname accepted
39. no DNS/network side effect
40. URL length bound

### SearchResult
41. exact nine fields
42. frozen/extras rejected
43. result ID normalization
44. rank strict integer
45. bool rank rejected
46. rank bound
47. title exact/preserved
48. empty title allowed
49. title length bound
50. title repr privacy
51. snippet exact/preserved
52. empty snippet allowed
53. snippet length bound
54. snippet repr privacy
55. source_name optional
56. source_name meaningful/bounded
57. published_at optional
58. published_at aware UTC
59. naive published_at rejected
60. date_text optional/preserved/bounded
61. source_ref optional opaque
62. source_ref controls rejected
63. provider-looking trust text grants nothing
64. exact stored-value revalidation

### SearchResponse
65. exact four fields
66. frozen/extras rejected
67. provider ID normalization
68. UTC as_of
69. empty results valid
70. <=20 results
71. >20 rejected
72. tuple/list input snapshots to immutable tuple
73. ranks exactly 1..N
74. rank gap rejected
75. rank duplicate rejected
76. wrong order rejected
77. duplicate result ID rejected
78. duplicate URL rejected
79. exact nested result revalidation
80. no truth/verified/confidence/answer fields
81. validate_request exact request ID
82. validate_request exact provider ID
83. validate_request exact as_of
84. validate_request max_results
85. no semantic query claim

### SearchProvider
86. abstract boundary exact required members
87. cannot instantiate abstract provider
88. fake provider substitutability A
89. fake provider substitutability B
90. provider ID strict in fake implementations/consumer validation
91. active IDs immutable/sorted
92. cancellation ACCEPTED shape
93. cancellation NOT_FOUND shape
94. one fake response exact validation
95. malformed response rejected by consumer validation
96. provider mismatch rejected
97. request mismatch rejected
98. no ToolRegistry/PermissionEngine coupling
99. no model coupling
100. no agent alias authority

### Trust/separation
101. prompt injection snippet remains exact data
102. fake system role text remains exact data
103. fake permission JSON remains exact data
104. copied shell command remains exact data
105. fake source_name "trusted" grants nothing
106. published_at does not create verified field
107. URL validation does not fetch/dereference
108. no browser import/call
109. no HTTP client import in production search module
110. no SecretStore
111. no env read
112. no persistence
113. no memory/RAG/embedding
114. no citations
115. no ranking/cross-check/synthesis
116. no EventBus
117. EventType count 32
118. Alembic head 0005
119. migrations unchanged
120. no API/frontend
121. no dependency change
122. guarded import/contract construction side-effect-free
123. query/result content not logged
124. credential/signature scan clean

Add further deterministic adversarial cases as useful.

## Targeted verification
Run:

`uv run --locked pytest tests/test_search_provider.py`

All PASS.

No live network test.
No search-engine credential.
No public internet.

## PHX-052 regression
Run:

`uv run --locked pytest tests/test_screenshot_vision_loop.py`

Baseline:
397 passed.

## PHX-051 regression
Run:

`uv run --locked pytest tests/test_windows_ui_automation.py`

Baseline:
493 passed.

## PHX-050 regression
Run:

`uv run --locked pytest tests/test_browser_tool.py`

Baseline:
653 passed.

## Phase-4 regressions
Run individually:
- secrets: 402
- terminal: 270
- filesystem: 649 passed / 16 skipped
- audit: 302
- permissions: 926
- ToolRegistry: 204

All remain green.

## Phase-3 regressions
Run individually:
- memory schema: 244
- PUDDING: 167
- ingestion: 300
- vector store: 273
- retrieval: 367
- citations: 173
- persistence: 12

All PASS.

## Phase-2
Run all nine existing Phase-2 suites individually.
Baseline aggregate: 2,729.

## Phase-1
Run all seven existing Phase-1 suites individually.
Baseline aggregate: 812.

## Alembic / events
Verify:
- Alembic head exactly `0005_audit_events`
- migrations 0001–0005 unchanged
- EventType count exactly 32
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

Pre-PHX-053 full baseline:
- 9,487 passed
- 16 skipped

Expected dependency changes:
**NONE**

`pyproject.toml` and `uv.lock` unchanged.

## Foundation
Run established foundation suites.
Baseline: 126 passed.
Exact `/health` unchanged.

## Desktop
Run:
- `npm test`
- `npm run typecheck --workspace @phoenix/desktop`
- `npm run build --workspace @phoenix/desktop`

Baseline: 4 passed.
Desktop files/dependencies unchanged.

## Security review
Confirm:
- production PHX-053 performs no network I/O
- no concrete search provider
- no API key/env secret path
- no URL dereference/DNS lookup
- no ToolRegistry/PermissionEngine bypass
- actual search permission integration deferred to PHX-054
- no browser fallback
- no page fetch/download
- search output remains untrusted
- fake permission/tool/system/shell text remains inert
- no truth/verified/task-success fields
- no memory/RAG/audit/persistence
- no EventBus/API/frontend/schema
- no dependency changes
- safe error privacy

Run credential/signature scan.

## Invariants
Review INV-001 through INV-024, especially:
- INV-001 PHOENIX remains supreme orchestrator
- INV-003 provider/result text grants no permission
- INV-004 no actual privileged tool call is introduced by this abstraction-only ticket
- INV-005/P4 unchanged
- INV-006/P5 unchanged
- INV-011 no frontend direct provider path
- INV-012 provider-specific implementation does not leak into agent definitions
- INV-013 no tool/agent policy implementation
- INV-017 web/search data remains untrusted
- INV-018 no secret in normal memory
- INV-019 provider contract exposes cancellation and active state
- INV-020 no schema mutation
- INV-021 provider failures have explicit vocabulary
- INV-022 no fallback is silently implemented
- INV-024 no new dependency

## ADR
At most one ADR.

ADR-054 should document:
- provider-neutral SearchProvider boundary
- no concrete provider/network in PHX-053
- exact SearchRequest/SearchResult/SearchResponse contracts
- P1 actual-search permission integration deferred to PHX-054
- public HTTP(S) URL lexical admission without dereference
- search result/date/source metadata remains untrusted/provider-reported
- no truth/freshness claims
- exact ordered rank/ID/URL response integrity
- no browser fallback/page retrieval/ranking/cross-check/synthesis
- no secrets/model/memory/RAG/audit/EventBus/API/frontend/schema/dependency changes

## Completion
Only if every deterministic gate passes:
- mark this ticket DONE
- mark PHX-053 DONE in FEATURE_TICKETS.md
- leave PHX-054 and PHX-055 TODO
- commit exactly:
  `feat: complete PHX-053 search provider abstraction`
- push only:
  `feature/PHX-053-search-provider-abstraction`
- no PR
- no merge
- no main push
- do not start PHX-054

## Known limitations to disclose
At minimum:
- no concrete search engine adapter yet
- no network execution in PHX-053
- no provider credential integration
- no browser fallback
- no page retrieval
- URL admission does not resolve DNS and does not claim DNS-rebinding protection
- provider rank/date/title/snippet/source fields may be wrong/adversarial
- no semantic dedup/ranking/cross-check/synthesis
- actual P1 search permission/tool integration belongs to PHX-054
- cancellation/active semantics are part of the provider contract; concrete-provider behavior is future work

## Next
PHX-054 — URAHARA research flow. Do not start it.

## Completion evidence — 2026-10-06

All deterministic gates PASS. Created phoenix/web/__init__.py,
phoenix/web/search.py and tests/test_search_provider.py. Modified only this
ticket, FEATURE_TICKETS.md and DECISIONS.md (one accepted ADR-054). No existing
runtime or test is changed, including the PHX-052 historical dependency-test
repair. No dependencies changed; pyproject.toml and uv.lock match origin/main.

Public API: SearchResult, SearchRequest, SearchResponse, SearchProvider,
SearchProviderError and SearchProviderErrorCode. Reuse PHX-010 CancellationResult
and CancellationStatus; no new cancellation vocabulary. Future owner web_research
is conceptual, URAHARA display-only; PHOENIX retains sole supreme authority.
Actual P1 search tool/permission execution is deferred to PHX-054.

Contracts have exact 4/9/4 fields, frozen/extra-forbidden, exact stored-value
revalidation including nested values/subclasses/copied extras/colliding extras.
All text is ordinary valid Unicode without NUL. IDs trim outer whitespace,
preserve case, reject controls and bound normalized length to 256 characters.
Query preserves meaningful original formatting <=4096 and omits repr/str;
title/snippet preserve exact text (empty valid), <=1024/8192 and omit repr/str.
Strict max_results/rank 1..20 reject bool/coercion. Explicit aware datetime/ISO
evidence normalizes UTC, rejecting naive/numeric data; no wall-clock default.
Source_name optionally preserves meaningful text <=512; published_at optionally
preserves provider-reported UTC; date_text preserves raw <=256 evidence.
Source_ref is optional opaque control-free text <=1024, never dereferenced;
empty opaque/date evidence is permitted. No rank/date/name means trusted/current.

HTTP(S) URLs <=4096 are lexically checked for hostname/authority, userinfo,
controls/backslash, localhosts, nonpublic IPv4/IPv6 and ambiguous numeric/bracket
spellings. Original admitted spelling remains; query/fragment/public IP/IDNA
metadata is supported. Zero DNS lookup, URL opening, fetch or download. This
does not claim DNS-rebinding prevention or approval for later navigation.
Results snapshot to immutable tuples, 0..20 including empty; ranks exactly 1..N
in tuple order, unique normalized IDs and exact URLs. No semantic deduplication.
validate_request exact-revalidates receiver/request and checks request_id,
provider_id, UTC as_of and max_results without semantic query correctness claims.

SearchProvider ABC exposes read-only normalized provider_id, abstract sorted
immutable active_request_ids and async search/cancel/aclose. Test-only providers
prove interchangeable identities, exact consumer validation, sorted concurrent
state and request-scoped ACCEPTED/NOT_FOUND cancellation, cleanup and neutral
result shapes. No production concrete/fake provider, HTTP client, BrowserTool,
search ToolDefinition, permission call or actual web search exists.

Closed error vocabulary exactly invalid_input, invalid_response, unavailable,
timeout, rate_limited, unauthorized, busy, closed, wrong_event_loop,
cleanup_failure; only safe code public text. Validation failures suppress raw
Pydantic input/extra names and exception chains. Fake system/developer/tool/
permission/shell/credential/trust/date content remains exact inert data, with no
truth/verified/confidence/answer/success fields or automatic execution. No
ranking/cross-check/synthesis, citation generation, SecretStore/env keys, model/
vision, memory/RAG/embedding, persistence/audit, EventBus, API/frontend or schema.

Verification commands and results:
- uv run --locked pytest tests/test_search_provider.py: 400 passed (recorded
  external JUnit/log), covering all 124 minimum matrix boundaries plus adversarial
  identity, Unicode, URL syntax, exact revalidation and correlation cases.
- Individually ran all 32 established prior suites, each locked pytest exit 0.
  Phase 5: screenshot/vision 397; Windows UIA 493; browser 653.
- Phase 4: secrets 402; terminal 270; filesystem 649 passed/16 skipped; audit 302;
  permissions 926; registry 204. Aggregate 2753 passed/16 skipped.
- Phase 3: memory 244; PUDDING 167; ingestion 300; vectors 273; retrieval 367;
  citations 173; persistence 12. Aggregate 1536 passed.
- Phase 2 all nine individually: 2729 passed. Phase 1 all seven: 812 passed.
- Full uv run --locked pytest: 9887 passed, 16 skipped, one existing warning;
  exactly 9487 baseline plus 400 new cases. Reports remain outside repository.
- uv lock / uv sync --locked: PASS, no manifest/lock changes.
- Ruff check / format check: PASS, 105 files formatted; mypy PASS, 99 source files.
- uv build: PASS, source archive/wheel outside repo.
- Foundation bootstrap/API/config/persistence/events/hardware: 126 passed,
  exact /health unchanged. npm test: 4 passed; desktop typecheck/build PASS.
- Protected-file review: 203 prior tracked files unchanged. All five migrations
  byte-identical after Git normalization, Alembic heads [0005_audit_events];
  EventType 32; EventBus/events untouched. Credential/signature scan: zero hits.
- git diff --check: PASS. Security and INV-001..024 review: PASS within scope.

Guarded uncached module evaluation, contract construction/revalidation/correlation
and pure provider construction deny file/env-secret/DB/network/DNS/process/thread/
task/EventBus/logging side effects. Pydantic's framework plugin-disable switch
is stubbed without environment reads. Static import/call analysis permits only
stdlib, Pydantic and neutral CancellationResult; no tool/model/agent execution.
All prior source/tests/migrations/desktop/manifests remain intact; no credential,
query/result log, HTTP dump, database, cache or build artifact enters the commit.

Known limitations: no concrete engine/network/credential integration, browser
fallback or page retrieval; lexical URL admission does not resolve DNS or prevent
rebinding. Provider ranks/dates/titles/snippets/sources may be wrong/adversarial.
No semantic dedup/ranking/cross-check/synthesis or freshness/truth verification.
Concrete cancellation/state/deadlines are future adapter obligations; fake
evidence proves representability only. Later navigation requires independent
permission/security; cancellation acceptance is not remote cessation. All
implementation acceptance boundaries PASS; staged review/commit/push are recorded
in the final lifecycle report. PHX-054 and PHX-055 remain TODO; Phase 5 ongoing.
Next PHX-054 — URAHARA research flow; not started.
