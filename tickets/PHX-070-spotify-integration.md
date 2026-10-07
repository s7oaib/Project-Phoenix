# PHX-070 — Spotify integration

Status: DONE
Phase: 7 — Media & Documents
Branch: feature/PHX-070-spotify-integration-recovery
Starting main/base SHA: 2ee67fad9c3864039185448cf80e0984fa258607
Expected main: 2ee67fad9c3864039185448cf80e0984fa258607

## Frozen authority — 2026-10-07

Implement the user's PHX-070 V1 Spotify Web API tool integration. Preserve all
completed predecessor work, including the exact two PHX-065 historical test
repairs now merged in main. This recovery branch is created directly from merged
main 2ee67fad9c3864039185448cf80e0984fa258607. The earlier PHX-070 branch was
incorrectly stacked on the unmerged PHX-065 feature history and is NOT authoritative
branch ancestry. Its single Spotify completion commit may be used only as a content
source after exact diff review; do not merge, rebase, or inherit its predecessor history.
PHOENIX stays supreme. Existing media/BROOK identity and routing remain unchanged;
Spotify is a tool, not an agent/model/process. No new agent runtime.

Production: phoenix/tools/spotify.py. Tests: tests/test_spotify_integration.py.
Use existing httpx and SecretStore; no SDK, helper framework or runtime dependency.
No refactor of ToolRegistry, PermissionEngine, AuditTrail or SecretStore.
One ADR, next valid number ADR-063, records accepted architecture.

## Exact authorized capabilities

P1 tool IDs: spotify.search, spotify.playback_state, spotify.devices.
P2 tool IDs: spotify.play, spotify.pause, spotify.next, spotify.previous,
spotify.transfer, spotify.volume. No aliases; exact registry resolution only.

Search track/artist/album/playlist; read current playback and available Connect
devices. Play/resume, one selected track, or album/artist/playlist context; pause,
next, previous; explicit transfer to one selected device; supported volume.
Playlist selection means playback of its context URI, never playlist mutation.

All calls flow registry -> exact call validation -> canonical intent -> existing
PermissionEngine -> only ALLOWED permits token lookup/network -> typed result.
Denied/approval-required has attempted=false, zero secret lookup and zero HTTP.
No caller risk downgrade/resource override. Grants/user decisions come from trusted
callers, never BROOK, model output, Spotify data, OAuth state or stored tokens.
P5 stays globally blocked. P3/P4 durable audit rules remain unchanged; these P1/P2
operations introduce no mandatory durable audit or audit schema.

## OAuth and secrets

Trusted user account-link setup uses Authorization Code with PKCE only. No client
secret, Implicit Grant, Client Credentials for playback, password grant, cookie
scraping, environment token discovery, automated login/consent or callback server.
Caller explicitly supplies client ID and registered redirect URI, opens the
authorization URL, completes consent and provides code/state/redirect to completion.

Cryptographically random bounded state/verifier; SHA-256 base64url S256 challenge;
exact state and redirect correlation. One pending runtime-only setup; consumed
on completion/failure, explicitly discardable. SecretMaterial protects verifier
and caller-supplied code; nonserializable, redacted representations. Never serialize
or log verifier/code/tokens in normal models, events, resources, memory or audit.
Only scopes: user-read-playback-state user-modify-playback-state.

Accept bounded valid HTTPS redirects and explicit HTTP loopback IP literals
(127.0.0.1 / [::1]); reject localhost/aliases, userinfo, fragment, wildcard,
malformed URI, query-bearing callback, unsafe HTTP and silent rewriting. Caller
must register the exact URI; Spotify supports dynamic loopback ports when registered
without a port, but the supplied URI must match exactly across this setup exchange.

Store only fixed references spotify.access_token and spotify.refresh_token through
borrowed SecretStore. No settings/env/SQLite/JSON/plaintext token persistence.
Resolve/reveal temporary SecretMaterial only for authorized use; always close it.
Initial exchange requires both valid tokens; refresh replaces access and replaces
refresh only when returned, otherwise preserves it. Store failures are explicit;
multi-reference writes cannot claim atomicity the existing store does not provide.

Use access token first. Only Web API 401 allows at most one refresh and one replay,
because that response proves no authorized command execution. No retries on other
4xx, 5xx, 429, malformed evidence, permission denial or device restrictions. No
background refresh timer/worker/scheduler. Failed refresh requires caller-owned
reauthorization. No credential lifetime promised beyond Spotify's current rules.

## Official platform evidence checked 2026-10-07

- [PKCE](https://developer.spotify.com/documentation/web-api/tutorials/code-pkce-flow)
- [Redirects](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri)
- [Refresh](https://developer.spotify.com/documentation/web-api/tutorials/refreshing-tokens)
- [Search](https://developer.spotify.com/documentation/web-api/reference/search)
- [Playback state](https://developer.spotify.com/documentation/web-api/reference/get-information-about-the-users-current-playback)
- [Devices](https://developer.spotify.com/documentation/web-api/reference/get-a-users-available-devices)
- [Play](https://developer.spotify.com/documentation/web-api/reference/start-a-users-playback)
- [Pause](https://developer.spotify.com/documentation/web-api/reference/pause-a-users-playback)
- [Next](https://developer.spotify.com/documentation/web-api/reference/skip-users-playback-to-next-track)
- [Previous](https://developer.spotify.com/documentation/web-api/reference/skip-users-playback-to-previous-track)
- [Transfer](https://developer.spotify.com/documentation/web-api/reference/transfer-a-users-playback)
- [Volume](https://developer.spotify.com/documentation/web-api/reference/set-volume-for-users-playback)
- [July changes](https://developer.spotify.com/documentation/web-api/references/changes/july-2026)

Current search maximum is 10 per type, default 5, offset 0..1000. PHX-070 accepts
limit 1..10, four unique types maximum, meaningful query <=512 characters; no
external audio flag. Current refresh guidance gives dashboard refresh tokens six
months, without extending lifetime on access refresh; user may need manual
reauthorization. July development quotas are per developer account, with 429
quota evidence; no quota retry. Endpoint definitions below follow current official
references, not older larger-limit examples.

## Fixed endpoint/method contract

Authorization URL: https://accounts.spotify.com/authorize (constructed only).
Token: POST https://accounts.spotify.com/api/token, form-encoded PKCE exchange or
refresh, explicit client_id, no client_secret or Basic authorization.
Web API origin: https://api.spotify.com/v1, fixed paths only:

| Tool | Method | Path | Success |
| --- | --- | --- | --- |
| spotify.search | GET | /search | 200 |
| spotify.playback_state | GET | /me/player | 200 or 204 no-active-playback |
| spotify.devices | GET | /me/player/devices | 200 |
| spotify.play | PUT | /me/player/play | 204 |
| spotify.pause | PUT | /me/player/pause | 204 |
| spotify.next | POST | /me/player/next | 204 |
| spotify.previous | POST | /me/player/previous | 204 |
| spotify.transfer | PUT | /me/player | 204 |
| spotify.volume | PUT | /me/player/volume | 204 |

Play sends no media body for resume, uris:[one track URI], or context_uri for
album/artist/playlist. Optional exact device_id on play/pause/next/previous/volume.
Transfer requires exactly one device_id and explicit boolean play intent (false
preserves playback). Volume strict integer 0..100, bool rejected. No guessed device
or persisted selection. Spotify URI must be exact authorized type with 22 base62
ID; arbitrary URLs rejected. Device ID is bounded opaque ordinary identifier,
not guessed by fuzzy name. Nullable/missing response device IDs remain unavailable
for selection; restricted and supports_volume evidence is preserved truthfully.

Playback controls require Premium and a controllable Spotify client/device where
Spotify requires them. Reads may still work; no fake support. 403 playback maps
to explicit premium_or_device_required. 204 command success means documented API
acceptance, not independently confirmed physical playback; no speculative readback.

## HTTP, contracts and lifetime

Owned HTTPX client: verify=True, trust_env=False, follow_redirects=False, finite
connect/read/write/pool timeouts and total operation deadline. Canonical origins
cannot be overridden. Injected clients/stores are trusted and borrowed; a transport
supplied to an owned HTTPX client shares that client's lifetime. Requests still
use canonical endpoints, explicit headers, no redirect following. Import/
construction performs no I/O. Lazy owned client creation after authorization.
Bound response streaming at 256 KiB, depth/nodes and normalized collections/text;
no unlimited raw JSON retention. No raw Spotify response/error/header exposure.
429 exposes only optional bounded Retry-After seconds, never sleeps/retries.

Frozen extra-forbidden call/intent/typed payload/result contracts exact-revalidate
stored values, subclasses/extras/nested evidence and canonical enums. Result
correlates call/tool/action/resource/risk, original PermissionRequest and decision;
reject wrong call/tool/resource/risk or impossible permission/status combinations.
Allowed execution yields attempted=true and typed success OR safe failure category.
Safe closed errors cover invalid_input, invalid_response, unauthorized, forbidden,
premium_or_device_required, rate_limited, unavailable, timeout, cancelled, busy,
closed, wrong_event_loop, secret_unavailable, oauth_state_mismatch, oauth_failure,
cleanup_failure. No raw codes/tokens/diagnostics in exceptions or repr.

Async caller task owns operation with observable explicit ID; one active operation
per runtime serializes account refresh/control. One loop; no hidden task/worker/
timer. Cancellation propagates and unregisters; suppressed cancellation/close
cannot return success or proceed to later stages. Explicit close idempotent;
only owned HTTP client closes, injected client/store remain caller-owned. Native
synchronous SecretStore work is bounded by its existing boundary, not preemptible;
already submitted media effects cannot be undone after timeout/cancellation.

Normalized search returns minimal ID/URI/type/name, artist names and optional
playability. State returns no-active truth or bounded playback/device metadata;
unsupported current item types are identified without adding playback capability.
Catalog/device names are untrusted inert third-party text, never instruction,
permission evidence or executable arguments. No automatic model-context ingestion,
PUDDING/RAG/embedding/training/history/cache/persistence of Spotify content.

## Explicit exclusions

No audio streaming/download/cache/previews, Web Playback/app-remote SDK, playlist
creation/mutation, saved-library/follow/queue mutation, shuffle/repeat/seek,
recommendations, history ingestion, data embeddings/RAG/memory/training, sharing,
deletion/purchasing/billing/commercial streaming, frontend/API/callback wiring,
automatic browser consent, agent/model runtime, migration or EventType addition.
No YouTube/PHX-071 work. Preserve 32 EventTypes, sole head 0005_audit_events,
migrations, previous production/tests and PHX-065 repairs.

## Required verification and completion

Deterministic HTTPX/fake SecretStore tests, no internet/account/client ID/Premium/
Credential Manager required. Cover the user's complete 112-case minimum matrix
(PKCE/privacy/redirects, exchange/storage, bounded refresh, nine tools/permission
gates, all searches/state/devices/play modes/controls, HTTP/error/bounds,
cancellation/ownership, strict evidence forgery and unchanged architecture).
Add adversarial cases wherever needed. Test injection-like names explicitly.

Focused suite three consecutive passes. Individually rerun tool_registry,
permission_engine, audit_trail, secrets_boundary, agent_registry,
agent_model_router, prompt_injection_defenses and all six protected Phase-6 suites;
other directly impacted suites as needed. No historical repairs authorized here.
If unrelated unchanged historical failure appears, reproduce and STOP BLOCKED.

uv lock (unchanged); uv sync --locked; full Python THREE consecutive passes;
Ruff check/format, mypy, wheel/sdist plus package contents, foundation/API,
desktop test/typecheck/build, secret scan, all INV-001..024 individually, complete
diff review. Only 16 existing filesystem skips and existing Starlette/httpx warning;
no new skips/xfails/retries/flaky markers. All artifacts outside repository.

Live smoke optional and nonblocking. Without explicitly authorized credentials:
LIVE SPOTIFY ACCEPTANCE: PENDING — requires user-owned Spotify developer app and
manual OAuth consent. Do not create an app or request client secret.

Only after every gate passes: mark PHX-070 DONE; Phase 7 remains INCOMPLETE;
PHX-071/072/073 TODO. Update ticket/FEATURE_TICKETS/DECISIONS; media docs only
narrow implemented V1 wording if needed. Exactly one completion commit:
feat: complete PHX-070 spotify integration
Normal push only feature/PHX-070-spotify-integration-recovery; no PR, merge, main push,
force push or predecessor rewrite. Final report includes all 55 requested fields,
exact files/tool IDs/risks/scopes/endpoints/commands/results/security/invariants,
commit/local-remote relation/main safety, limitations/live pending and PHX-071
NOT STARTED. A failed required gate forbids DONE/commit/push.

## Recovery source delta — authoritative handling

The previously completed but incorrectly stacked Spotify commit is:

`0a938a4ffa251df3bcd33d57360346fae059e95a`

Its parent is PHX-065 feature completion commit:

`f7f1efb7ceec7f1f92b01e3a3c44eb695919f868`

The old Spotify commit changed exactly these six ticket-owned paths relative to that parent:

- DECISIONS.md
- FEATURE_TICKETS.md
- docs/18_MEDIA_INTEGRATIONS.md
- phoenix/tools/spotify.py
- tests/test_spotify_integration.py
- tickets/PHX-070-spotify-integration.md

For recovery:

1. Do not merge or rebase the old PHX-070 branch.
2. Do not cherry-pick the old commit as branch ancestry.
3. You MAY reuse its implementation content by extracting the exact delta from
   `f7f1efb7ceec7f1f92b01e3a3c44eb695919f868..0a938a4ffa251df3bcd33d57360346fae059e95a`
   for only:
   - DECISIONS.md
   - FEATURE_TICKETS.md
   - docs/18_MEDIA_INTEGRATIONS.md
   - phoenix/tools/spotify.py
   - tests/test_spotify_integration.py
4. Do NOT import/replace this authoritative ticket from the old branch. This recovery
   ticket remains the source of truth and is updated only at final completion.
5. Before applying any old delta, verify the merged main content at the touched
   pre-existing paths matches the old commit's PHX-065 parent semantics sufficiently
   for a clean patch. Resolve only PHX-070-owned differences; never reintroduce
   pre-squash PHX-065 history or duplicate ADR/status content.
6. After applying the recovered content, rerun every deterministic gate in this ticket.
   Prior test receipts are historical evidence only and do not substitute for fresh
   recovery-branch verification.
7. Final recovery completion must still be exactly one new implementation/completion
   commit with subject:
   `feat: complete PHX-070 spotify integration`
8. After completion the recovery branch must be 0 behind merged main and exactly two
   commits ahead: this ticket preparation commit plus the PHX-070 completion commit.
9. The obsolete stacked branch `feature/PHX-070-spotify-integration` must not be
   force-moved, merged, or used for future ticket ancestry.
10. PHX-071 remains TODO and must not be started.

## Current official platform verification — 2026-10-07

The existing PHX-070 platform assumptions were rechecked against current Spotify
developer documentation after the ancestry issue was discovered:

- Authorization Code with PKCE remains appropriate where a client secret cannot be safely stored.
- Redirect URIs require exact matching; HTTPS is required except explicit loopback IP literals,
  and localhost is not allowed.
- Search currently defaults to 5 results per type and allows a maximum of 10 with offset up to 1000.
- Dashboard refresh tokens have a six-month lifetime; access-token refresh does not extend that lifetime.
- Playback controls such as start/resume and volume require Spotify Premium and suitable controllable devices.
- Development Mode quota accounting changed in July 2026 and 429 responses may identify quota exhaustion.

These are validation facts only; they do not authorize capabilities beyond the frozen
PHX-070 scope above.


## Fresh recovery completion evidence - 2026-10-07

Recovered content only from source commit 0a938a4ffa251df3bcd33d57360346fae059e95a
relative to f7f1efb7ceec7f1f92b01e3a3c44eb695919f868. The old parent tree was
byte-identical to merged main 2ee67fad9c3864039185448cf80e0984fa258607;
all three pre-existing target blobs were checked before applying the five-path
Git patch. No old commit/history or old ticket was imported. Starting recovery
HEAD e1e2a37d520f2531909d34aa4e2d80b2b6ea82f9 remains the completion parent.

Fresh complete source/test review preserves the frozen V1 design. Production
Spotify code matches the reviewed source blob exactly. Added three refresh storage
failure cases (rotation failure, partial non-atomic write, omitted-refresh access
write failure), one JSON node-budget case, and stronger device assertions. The
non-ASCII mismatched-state fixture uses its intended UTF-8 character.

Fresh required commands and results:

| Gate | Result |
| --- | --- |
| uv run --locked pytest tests/test_spotify_integration.py -q, three consecutive runs | 345 passed each |
| tool_registry / permission_engine / audit_trail / secrets_boundary | 204 / 926 / 302 / 402 passed individually |
| agent_registry / agent_model_router / prompt_injection_defenses | 96 / 213 / 604 passed individually |
| rise_phoenix_startup / voice_conversation / tts_provider | 509 / 950 / 602 passed individually |
| stt_provider / vad / wake_word_provider | 414 / 403 / 537 passed individually |
| uv lock; uv sync --locked | PASS, manifests unchanged |
| uv run --locked pytest, three consecutive complete runs | 14743 passed, 16 existing filesystem skips, one existing Starlette/httpx warning each |
| uv run --locked ruff check . | PASS |
| uv run --locked ruff format --check . | PASS |
| uv run --locked mypy | PASS |
| uv build --out-dir <external-validation>/dist | PASS, exact Spotify source verified in wheel and sdist |
| Foundation bootstrap/API/config/persistence/events/hardware | 126 passed, exact /health unchanged |
| npm test | 4 passed |
| npm run typecheck --workspace @phoenix/desktop | PASS |
| npm run build --workspace @phoenix/desktop | PASS |

All JUnit/logs/commands/review scripts/build artifacts remain outside the project:
C:/Users/shoai/.codex/validation/PHX-070-recovery-20261007. Historical receipts
were not reused as gate evidence. All 16 skip IDs/reasons match PHX-065 merged
baseline; no new skip/xfail/retry/flaky marker. No unrelated historical test repair.

All 145 pre-existing production/test/migration/desktop/dependency blobs match merged
main, including startup implementation/tests, model benchmark and Ollama provider
tests. ToolRegistry, PermissionEngine, AuditTrail, SecretStore, AgentRegistry/BROOK,
PHOENIX, P4/P5 and P3/P4 audit authority remain unchanged. Exact nine P1/P2 Spotify
definitions gate secret/network use through existing deterministic permissions.
No memory/RAG/model-context/training ingestion, audio runtime, hidden worker,
frontend/API, dependency, schema or EventType change. EventType remains 32;
five migrations, sole head 0005_audit_events. Full six-file credential-signature
scan passed. INV-001 through INV-024 individually reviewed and preserved.

| Invariant | Fresh review |
| --- | --- |
| INV-001 | PASS: PHOENIX remains the only orchestrator; SpotifyTool exposes tool evidence. |
| INV-002 | PASS: AIZEN definitions and execution unchanged; no permission or finalization delegation. |
| INV-003 | PASS: Only existing deterministic PermissionEngine evaluates trusted grants/decisions; provider text never authorizes. |
| INV-004 | PASS: Nine exact ToolRegistry definitions and canonical call/intent/request validation precede secret lookup and HTTP. |
| INV-005 | PASS: Only P1/P2 Spotify operations; global P4 explicit-confirmation implementation unchanged. |
| INV-006 | PASS: P5 deterministic blocking and all prior permission logic unchanged. |
| INV-007 | PASS: P3/P4 AuditTrail, migrations and durable audit requirements unchanged. |
| INV-008 | PASS: Exact successful status/response/outcome correlation required; no physical-playback verification claim. |
| INV-009 | PASS: MAHORAGA budget unchanged; only one explicit 401 refresh and one replay, no generic recovery loops. |
| INV-010 | PASS: KAIDO stays degraded policy; all Phase-6 production unchanged. |
| INV-011 | PASS: Frontend and API source unchanged; no OAuth callback/UI wiring. |
| INV-012 | PASS: Provider-independent AgentRegistry/BROOK definitions unchanged. |
| INV-013 | PASS: Tool supplies no agent policy; existing PermissionEngine owns authorization. |
| INV-014 | PASS: Neutral media identity retained; BROOK alias grants no authority. |
| INV-015 | PASS: Text and voice paths unchanged; Spotify has no orchestration dependency. |
| INV-016 | PASS: Voice unchanged; safe Spotify failures are bounded tool evidence. |
| INV-017 | PASS: Spotify names are bounded inert third-party text; no ingestion/context execution. |
| INV-018 | PASS: Only fixed SecretStore token references; runtime verifier/code, no normal memory persistence. |
| INV-019 | PASS: Observable active ID, total/HTTP deadlines, propagated cancellation, close and borrowed ownership. |
| INV-020 | PASS: All five versioned migration blobs unchanged; sole head 0005_audit_events. |
| INV-021 | PASS: Closed 16 error categories and documented success statuses; no raw diagnostics or fake playback success. |
| INV-022 | PASS: Explicit refresh/error evidence; no automatic device/backend fallback. |
| INV-023 | PASS: No model generation or hidden task; shared BLAZE-6 scheduling unchanged. |
| INV-024 | PASS: No added dependency/SDK; manifests unchanged; research docs introduce no runtime dependency. |


Exactly six ticket-owned final paths: DECISIONS.md, FEATURE_TICKETS.md,
docs/18_MEDIA_INTEGRATIONS.md, phoenix/tools/spotify.py,
tests/test_spotify_integration.py, tickets/PHX-070-spotify-integration.md.
One ADR-063; no duplicated ADR-062 or predecessor status change. Required gates
passed before TODO -> DONE. Phase 7 remains INCOMPLETE; PHX-071/072/073 TODO.
The final delivery report follows the latest 62-field execution request and records
the single completion commit and feature-only
normal push; no PR, merge, reset, rebase, cherry-pick, amend or force push.

LIVE SPOTIFY ACCEPTANCE: PENDING - requires user-owned Spotify developer app and
manual OAuth consent. Premium/Connect/account restrictions remain explicit; API
acceptance is not proof of audible playback. SecretStore multi-reference writes
are non-atomic; partial rotation can require explicit recovery. Python/HTTPX
immutable secret copies cannot be guaranteed wiped. Synchronous native store work
and already-entered media effects cannot be preempted/undone. No callback/UI
wiring, refresh worker, streaming/cache or quota infrastructure. PHX-071 NOT STARTED.
