# PHX-070 — Spotify integration

Status: DONE
Phase: 7 — Media & Documents
Branch: feature/PHX-070-spotify-integration
Starting HEAD: f7f1efb7ceec7f1f92b01e3a3c44eb695919f868
Expected main: 98198ee7d349e57999bc3bdbf2e2588b3fafbc61

## Frozen authority — 2026-10-07

Implement the user's PHX-070 V1 Spotify Web API tool integration. Preserve all
completed predecessor work, including the exact two PHX-065 historical test
repairs. Branch from PHX-065, never main. Initial fetch verified exact predecessor
and main, 0 behind / 4 ahead, clean tree, and no existing PHX-070 branch or code.
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
Normal push only feature/PHX-070-spotify-integration; no PR, merge, main push,
force push or predecessor rewrite. Final report includes all 55 requested fields,
exact files/tool IDs/risks/scopes/endpoints/commands/results/security/invariants,
commit/local-remote relation/main safety, limitations/live pending and PHX-071
NOT STARTED. A failed required gate forbids DONE/commit/push.

## Completion evidence — 2026-10-07

Starting branch: feature/PHX-065-rise-phoenix-startup-health.
Starting SHA: f7f1efb7ceec7f1f92b01e3a3c44eb695919f868.
Observed origin/main: 98198ee7d349e57999bc3bdbf2e2588b3fafbc61, matching expected.
Created feature/PHX-070-spotify-integration directly from predecessor (no extra
preparation commit). Exactly one production file phoenix/tools/spotify.py; exactly
one new test file tests/test_spotify_integration.py; no previous code/test edit.
Created this ticket before production implementation, then marked feature
IN_PROGRESS. Documentation changes are this ticket, FEATURE_TICKETS.md, ADR-063
in DECISIONS.md and narrow Spotify V1 wording in docs/18_MEDIA_INTEGRATIONS.md.

All required automated acceptance gates PASS:

| Gate | Evidence |
| --- | --- |
| Focused PHX-070 x3 | 341 passed each; 0 skips/xfails/errors/failures |
| ToolRegistry | 204 passed |
| PermissionEngine | 926 passed |
| AuditTrail | 302 passed |
| SecretStore boundary | 402 passed |
| AgentRegistry / model router | 96 / 213 passed |
| Prompt injection | 604 passed |
| Startup / conversation | 509 / 950 passed |
| TTS / STT / VAD / wake | 602 / 414 / 403 / 537 passed |
| Full Python x3 | 14,739 passed, 16 existing filesystem skips, 1 existing warning each |
| uv lock / uv sync --locked | PASS; manifests/lockfiles unchanged |
| Ruff / format | PASS; 126 files formatted |
| Strict mypy | PASS; 120 source files |
| Wheel / sdist | PASS; both package copies of Spotify code match source exactly |
| Foundation/API | 126 passed; same existing warning |
| Desktop | npm test 4 passed; typecheck and build PASS |
| Security/signature scan | PASS; no real Spotify credentials/private payload used |
| Protected blobs | All 145 existing production/test/migration/frontend/manifest paths unchanged |
| Events/migrations | 32 EventTypes; 5 revisions; sole head 0005_audit_events |
| Invariants | INV-001 through INV-024 individually PASS |
| Scope/diff | Exactly six ticket-owned files; no unrelated edits/artifacts |

The 341 synthetic cases cover the required auth/PKCE, exact state/redirect and
single-use setup, OAuth token exchange/storage, optional rotation and one-refresh/
one-replay ceiling, all nine risk/permission gates, input/correlation forgeries,
all four catalog types/limits/offsets/null entries/inert injection-like names,
state/device truth, every control/play mode, volume boundaries, fixed hosts,
no redirect/ambient environment/TLS settings, closed status/error/response bounds,
cancellation suppression, total deadlines (including blocking store overrun),
busy/closed/wrong-loop lifetime, close failure and borrowed ownership. Architecture
matrix cases additionally use existing regression suites and exact protected-blob
comparison. No skip/xfail/retry/flaky marker added. All 16 filesystem skip case IDs
and reasons match the PHX-065 full-suite receipt exactly. The unchanged warning is
StarletteDeprecationWarning for httpx in starlette.testclient.

Gate commands (all run from D:/Project-Phoenix):

- uv run --locked pytest tests/test_spotify_integration.py -q --junitxml=<external focused receipt>, three consecutive runs.
- uv run --locked pytest tests/test_<suite>.py -q --junitxml=<external regression receipt>, individually for each of the 13 suites above.
- uv lock; uv sync --locked.
- uv run --locked pytest --junitxml=<external full receipt>, three consecutive runs.
- uv run --locked ruff check .; uv run --locked ruff format --check .; uv run --locked mypy.
- uv build --out-dir <external>/dist; wheel/sdist payload verification.
- uv run --locked pytest tests/test_bootstrap.py tests/test_api.py tests/test_config.py tests/test_persistence.py tests/test_events.py tests/test_hardware.py -q --junitxml=<external>/foundation.xml.
- npm.cmd test; npm.cmd run typecheck --workspace @phoenix/desktop; npm.cmd run build --workspace @phoenix/desktop.
- git diff --check; git diff --cached --check; protected hash/scope/secret scan; actual EventType/Alembic count/head checks.

Exact command arrays, per-command exit codes, times, logs, JUnit XML, package
artifacts and review receipts remain outside the repository in
C:/Users/shoai/.codex/validation/PHX-070-20261007. Nothing there is committed.
Only synthetic HTTPX transports and fake SecretStore values were used; no Spotify
API connection, real login/account/device/Premium or Credential Manager operation.
Known limitations, Premium/Connect requirements, caller-owned callback/UI and
nontransactional/best-effort secret lifetime constraints remain as frozen above.

INV-001/002: PHOENIX/AIZEN authority unchanged. INV-003/004: deterministic permission
evaluation precedes secrets/HTTP; untrusted names do not grant authority. INV-005/006:
P4 confirmation/P5 blocking unchanged. INV-007: P3/P4 audit unchanged. INV-008:
failure cannot become success and completion requires gates. INV-009: MAHORAGA
unchanged; one refresh/replay only. INV-010: KAIDO remains degraded-only. INV-011:
frontend/API unchanged. INV-012/013: provider-independent roles and agent policy
unchanged. INV-014: neutral media identity preserved. INV-015/016: text/voice/
orchestration preserved with Phase-6 regressions. INV-017: inert untrusted content.
INV-018: secrets excluded from normal memory/events/audit. INV-019: observable
active ID, finite deadlines, propagated cancellation and explicit close. INV-020:
versioned migrations unchanged. INV-021/022: explicit failures and bounded observable
401 recovery, no silent success/fallback. INV-023: no generation/concurrency change.
INV-024: no required research repository or new dependency. Full individual review
is retained in external invariants.json.

LIVE SPOTIFY ACCEPTANCE: PENDING — requires user-owned Spotify developer app and
manual OAuth consent. This is optional and does not block deterministic completion.
Phase 7 remains INCOMPLETE; PHX-071/072/073 remain TODO. PHX-071 NOT STARTED.
Delivery uses exactly one completion commit with subject
feat: complete PHX-070 spotify integration, normal push only to this feature branch;
no main push, PR, merge, force push or predecessor rewrite. External delivery.json
records the resulting SHA and verified remote/local/main/clean-tree state.
