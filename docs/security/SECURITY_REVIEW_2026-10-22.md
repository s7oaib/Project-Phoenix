# PHOENIX Consolidated Security Review — 2026-10-22

Part of PHX-094 release evidence. Read-only review; no production code changed.

- Base: `5722431` → reviewed head `origin/main` `2011b78` (176 files, +42k lines).
- Scope: LEVI grounding (PHX-118/118b), settings/onboarding (108), KAIDO (119), voice (114), metrics (117),
  Colibri gate (116), observability (115), installer/NSIS/uninstall/CI (092), memory/RAG + web research +
  documents/media (120), computer control incl. P-levels / P4 one-time approvals / kill switch / Playwright (112),
  combined planner (121), desktop Rust proxy + UI (122).
- Checked against: AGENTS.md protected architecture, INVARIANTS.md, docs/08 permission matrix, docs/09.
- Method: code reading, localhost-only proof scripts (URL admission / redirect re-check with httpx MockTransport,
  httpx log capture), targeted tests.

## Summary

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 4 |
| Low | 9 |
| Info | 14 |

No permission bypass, no tool reachable outside ToolRegistry/PermissionEngine, no token exposure to JS,
no destructive installer behaviour found. Protected-architecture items hold.

## Findings

| ID | Sev | Location | Scenario | Recommended fix |
|---|---|---|---|---|
| SR-01 | Medium | `phoenix/api/structured_logging.py:225-228` (root logger set to INFO; no override for `httpx`/`httpcore`) | httpx 0.28 logs `HTTP Request: GET <full URL>` at INFO to `<data_dir>/logs/phoenix.jsonl` and stderr. Leaks YouTube search queries (`tools/youtube.py:1073-1080`), Spotify GET params (`tools/spotify.py:1142`), every web-research URL including query-string tokens/emails. Proven by local script (`...reset?code=abc123XYZ&email=a@b.c` logged verbatim). Violates INV-018 / ADR-145 (logs carry IDs, codes, timings only). | In `configure_logging` set `httpx`, `httpcore`, `hpack` loggers to WARNING (or allowlist `phoenix.*`, `uvicorn.error`). Add regression test asserting no URL in log output. |
| SR-02 | Medium | `phoenix/web/page_fetch.py:223-230` (`_check_host` resolves) vs `:240` (httpx re-resolves on connect); acknowledged in docstring `:6-8` | DNS-rebinding TOCTOU: attacker domain (e.g. SEO-poisoned search result) with TTL 0 resolves public at check, then 127.0.0.1/192.168.x at connect → GET to Ollama :11434 or LAN admin pages; body flows into research sources/model context. | Pin the vetted IP: custom httpx transport/network backend that connects to the checked address with Host header + SNI = hostname; or verify peer address after connect and abort if non-global. |
| SR-03 | Medium | `phoenix/tools/browser_setup.py:21,29,36-38`; `phoenix/api/server.py:734-736` | Default Playwright browser dir `D:/Phoenix/browsers`. `D:\` ACL grants Authenticated Users Modify (inherited), so another local user can plant `chromium-*\...\chrome.exe`; PHOENIX launches it as the owner during computer control. Install check only globs the folder name. Multi-user PCs only. | Default to per-user `%LOCALAPPDATA%\PHOENIX\browsers` (or under data dir); otherwise create with owner-only ACL and refuse dirs writable by others. |
| SR-04 | Medium | `phoenix/agents/control_steps.py:1358-1364` (`_summarize`); `apps/desktop/src/adapters/CapabilityViews.tsx:155`, `core.ts:117` | P4 `browser.fill` / `windows.set_text` whose text `looks_like_secret()` is shown only as `[hidden: N characters]` with no reveal. Prompt-injected prior-step content can steer a fill of a secret into an attacker page; user approves a payload they cannot see. sha256 resource binding proves integrity, not informed consent. | Send text to the authenticated UI and mask client-side behind a reveal control, or refuse P4 fills of secret-looking text until revealed; at minimum show destination origin prominently. Record in DECISIONS.md. |
| SR-05 | Low | `phoenix/web/page_fetch.py:171-183` (`_global`), `phoenix/web/search.py:133-142` | 6to4 `2002::/16` wrapping 127.0.0.1 / 169.254.169.254 and `192.0.0.0/24` are admitted (Python 3.11.7 `is_global` gaps). Exploitable only with a 6to4 relay. | Explicit deny for `2002::/16`, `2001::/32`, `192.0.0.0/24`; for IPv6 answers evaluate `ipv4_mapped`/`sixtofour`/`teredo` embedded IPv4 against IPv4 rules. |
| SR-06 | Low | `phoenix/web/page_fetch.py:215, 251-255`; `phoenix/tools/web_research.py:221`; `phoenix/agents/research_steps.py:435` | httpx timeout is per-phase; a drip-feed page holds a fetch until the 300 s step timeout. Declared 90 s tool timeout not enforced. Availability only. | `asyncio.timeout(timeout_seconds)` around each fetch; `wait_for` around tool `execute` with the definition timeout. |
| SR-07 | Low | `phoenix/web/page_fetch.py:241-244`; `phoenix/web/search.py:104` | Redirect https→http downgrade allowed; any port admitted (e.g. `:22`). | Refuse scheme downgrade on redirect; restrict page fetch/browser to ports 80/443. |
| SR-08 | Low | `phoenix/agents/control_steps.py:949-951`; `phoenix/tools/windows_ui.py:327-340` | `windows.inspect_window` resource carries only hwnd (no expected pid/class); P1 grants may be PERSISTENT, so an "always" grant can match an unrelated window that later reuses the HWND. P1 read-only. | Include `expected_process_id`/`expected_class_name` in the resource, or cap hwnd-bound grants at SESSION. |
| SR-09 | Low | `control_steps.py:745-747`, `research_steps.py:382-384`, `capability_steps.py:671-673` | Prior-step outputs (derived from web pages / documents / tool output) are re-labelled `MODEL_OUTPUT` for later step prompts. Still guarded as untrusted data (not a bypass), but provenance is lost so page text in step 1 can steer step 2 proposals. | Propagate strongest upstream origin label into later-step evidence. |
| SR-10 | Low | `phoenix/api/structured_logging.py:65-69` | Redaction pattern misses JSON-quoted keys (`{"api_key": "abcd1234efgh"}` unchanged) and redacts only the scheme in `Authorization: Basic <cred>`. | Allow optional closing quote before `:`/`=`; consume scheme + credential for authorization/cookie keys; add tests. |
| SR-11 | Low | `apps/desktop/src-tauri/src/backend.rs:340-347` | Backend stderr → `phoenix.desktop-backend.log` via `File::create`: truncated per launch but uncapped during a run; captures raw output bypassing the redacting logger (early tracebacks, warnings). | Size-cap/rotate, or only persist stderr on startup failure. |
| SR-12 | Low | `.github/workflows/release-installer.yml:77,79,83,87,102` | Actions pinned by mutable tags (incl. third-party `astral-sh/setup-uv`); supply-chain risk for installer artifact. Permissions otherwise minimal. | Pin to full commit SHAs; Dependabot for updates. |
| SR-13 | Low | `scripts/voice_setup.py:61-64` | Model download written without upper bound; size/SHA-256 checked only after completion (disk-fill). | Abort once bytes exceed `file.size_bytes`. |
| SR-14 | Info/Low | `apps/desktop/src-tauri/src/proxy.rs:30,145-163`; test `:637` | `filename` query accepts encoded `/`, `\`, `%00`, `..`; test at `:637` is refused only because of an extra `&limit=1`, so it does not prove what it implies. Backend rejects these (`phoenix/documents/parsers.py:185-201`) and filename is metadata only (`api/knowledge.py:418-480`) — no traversal. | Fix test to use `filename=..%2F..%2Fx.md` alone; optionally refuse `%2F %5C %3A %00-%1F` in Rust. |
| SR-15 | Info/Low | `apps/desktop/src-tauri/src/proxy.rs:59, 276-289`; adapter `core.ts:1151` | Approve route forwards any JSON ≤64 KiB; webview script could send `{"duration":"always"}` for a persistent grant (adapter always sends `session`). Mitigated by strict CSP and no HTML sinks; backend still caps grant forms per risk (P3 session-only, P4 none). | Pin approve body in Rust, or document in DECISIONS.md that "always" is reachable by design. |
| SR-16 | Info | `phoenix/agents/runtime.py:976`; `phoenix/api/missions.py:521-524` | KAIDO degraded answers settle with API status `completed` (trace state DEGRADED, answer labelled). Status-only clients may treat as verified. | Distinct `degraded` status or required `verified=false`. |
| SR-17 | Info | `phoenix/agents/control_steps.py:1149-1163` (documented docs/15) | Kill switch does not interrupt an in-flight action; it completes/times out and result is discarded. | Document in release evidence; consider cooperative abort for browser actions. |
| SR-18 | Info | `phoenix/agents/research_steps.py:109-129` (ADR-137) | One `search:<provider>` P1 grant (may be persistent) covers all future queries and top-result fetches → SSRF hardening (SR-02/05/07) is load-bearing. | Fix SR-02 before enabling persistent research grants by default. |
| SR-19 | Info | `phoenix/api/voice.py:174-265` | Ambient audio transcripts become ordinary missions and can use existing persistent grants. Voice never approves (verified). | Push-to-talk / wake word for missions with control steps. |
| SR-20 | Info | `phoenix/web/duckduckgo.py:165,178` | DDG response not streamed / byte-capped (fixed TLS endpoint; memory only). | Stream with byte cap like page_fetch. |
| SR-21 | Info | `phoenix/documents/parsers.py:185-200` | Upload display names allow Unicode Cf (e.g. U+202E bidi) → UI spoofing only. | Reject category Cf. |
| SR-22 | Info | `phoenix/tools/filesystem.py:154-156` (pre-existing, PHX-048) | `CONIN$`/`CONOUT$` missing from reserved device list. | Add them. |
| SR-23 | Info | `phoenix/tools/filesystem.py:593-612` + `document_files.py:477,507-515` (pre-existing) | Check-then-open race on workspace path (junction swap by local process). `xb` prevents overwrite. | Handle-relative / no-follow open and verify final path by handle. |
| SR-24 | Info | `apps/desktop/src-tauri/src/proxy.rs:367-373, 233` | Tauri IPC loads full body before base64 length cap; only own webview can exhaust memory. | Optional IPC size limit. |
| SR-25 | Info | `apps/desktop/src-tauri/src/backend.rs:128-134,159-177` | `PHOENIX_BACKEND_ROOT`/`PHOENIX_BACKEND_PYTHON` honoured in installed mode and drop `-I`; needs user-env control already. | Ignore overrides when `<exe>/backend` exists. |
| SR-26 | Info | `packaging/windows/installer-hooks.nsh:16-20` | Opt-in delete-data removes `%USERPROFILE%\.phoenix\data` (DB, memory, knowledge, logs). Behaviour correct; checkbox text should state contents. | Clarify checkbox wording. |
| SR-27 | Info | `scripts/colibri_gate.py:126`; `phoenix/api/routes/setup.py:158-161` | Gate manifest rejects `..` but not absolute names (tracked manifest, hash-only → negligible). `GET /onboarding` returns absolute data dir (token-gated, ADR-110). | Reject absolute/drive names; align observability docstrings. |

## Checked and OK

**Protected architecture / permissions**
- Every control action goes through ToolRegistry + PermissionEngine: PHOENIX pre-checks (`control_steps.py:663-668, 1049-1060`), tool re-decides and matches request to call (`browser.py`, `windows_ui.py` execute); `_execute` (`control_steps.py:1420-1435`) has no side path.
- Model cannot downgrade P-level: risk from tool `describe()` must equal `CONTROL_TOOL_RISKS`; P0/P5 rejected (`control_steps.py:1013-1022`); operation allowlist + field shapes (`531-589`); no raw keystroke/Win+R action; terminal/screenshot tools not wired (`server.py:737-742`).
- P4 one-time: `approve_once` requires PENDING + WAITING_PERMISSION in one transaction (`persistence/missions.py:747-769`); in-memory, keyed to re-run, popped once (`api/missions.py:415, 750-762`; `control_steps.py:1135-1138`); max one P4 per mission (`876-878, 1132-1133, 1161-1165`); bound to actor/tool/action/resource incl. URL+selector+text sha256 or hwnd+pid+text sha256 (`1025-1036`); never persisted as a grant (`281-294`). Concurrent double-approve fails.
- Grant scope: control grants P1–P3 only, P3 session-only, P4 no grant form (`control_steps.py:229-261`); media/document P2 session-only (`capability_steps.py:168-211`); research P1 only; `MissionService.approve` builds grants only from stored request scope (`api/missions.py:662-723`).
- Approve route workspace exemption (`routes/permissions.py:107-114`) applies only to research tools, media tools and control actor — none use file-path resources; document requests (absolute paths, `document_files.py:640`) keep the workspace check.
- Voice never approves/denies: no approve/deny/grant call in `api/voice.py`, `routes/voice.py`, `voice/engines/pipeline.py`; pending permission only spoken (`voice.py:259-262`).
- KAIDO: no tools/ToolRegistry/PermissionEngine (`kaido.py:14-18, 335-338`); no grants on its path (`runtime.py:952-956`); LEVI verification failure is not masked (`runtime.py:1147-1155`); trace forbids COMPLETED with KAIDO answer (`runtime.py:498-509`).
- Combined planner emits objectives only; capability lines are PHOENIX constants (`combined_planning.py`); step domain assignment enforced (`control_steps.py:1575-1582`).
- LEVI final grounding shortcut (`final_grounding.py:160-194`): single line <200 chars, not JSON, must equal last LEVI-verified successful `read_text` output, verbatim word-bounded labelled value, injection guard clean; else model check; empty evidence never passes.
- Control LEVI (`control_steps.py:1690-1755`) and research LEVI (`research_steps.py:529-629`, deterministic: grant match, cited URLs fetched, quotes/numbers present in source).
- Untrusted content guarded in all prompts: research sources (`research_steps.py:297-312, 491-506`), control observations (`control_steps.py:1496-1504, 816-831`), retrieval chunks (`retrieval_steps.py:543-562`); retrieval P0 via PermissionEngine (`426-483`). Web research requires PermissionEngine decision before any I/O (`tools/web_research.py:296-319`); fetched text labelled untrusted (`:134`).
- Kill switch tripped by `runtime.cancel` (`runtime.py:1031-1034`), checked before each action and in worker (`control_steps.py:896-899, 1273-1276`), teardown queued (`787-804`).

**SSRF / network**
- Every redirect hop re-admitted and re-resolved; max 5 hops; all DNS answers must be global; empty answer refused (`page_fetch.py:211, 230, 236-244`). Proven blocked: redirect to 127.0.0.1, name→10.x, mixed public/loopback answers, `[::ffff:7f00:1]`, `file:`, scheme-relative.
- `follow_redirects=False`, `trust_env=False`, no cookies; text content types only; byte cap before decode; error codes only (`page_fetch.py:87-92, 139-154, 209-222, 248-264`).
- URL admission (`search.py:90-143`) blocks loopback, decimal/octal/hex/short IPv4, 169.254.169.254, fe80::, fc00::, 64:ff9b::/96, 100.64/10, 198.18/15, ::, broadcast, `localhost`/`localhost.`/`*.localhost`, userinfo, `%` in host, backslash, port 0, controls.
- DuckDuckGo results pass same admission; no redirects, no env proxy (`duckduckgo.py:99-118, 139-144`).
- Ollama VRAM probe: no proxy, no redirects, 256 KiB cap (`observability.py:287-332`).

**Path traversal / files**
- Document create: rejects `\ : * ? < > | "` NUL (no ADS/drive/UNC), absolute and `//`, `.`/`..`, trailing dot/space, device names (`document_files.py:218-224`, `filesystem.py:146-164`); reparse-point/symlink check per component and root containment (`filesystem.py:556-612`); exclusive create `xb` + read-back (`document_files.py:499-526`); permission request matched to `describe()` resource (`:647-678`); CSV formula neutralisation (`:96-98, 529-540`); model-proposed paths pass same validators (`capability_steps.py:448-507`).
- Upload: filename metadata only, never a path; ≤255 chars, no `/ \ :`, no controls, not `.`/`..`, extension allowlist; rollback on failure (`routes/knowledge.py:51,126-134`; `knowledge.py:418-481`; `parsers.py:185-200`).
- DELETE `/files/{uuid}`, `/memory/{uuid}`: Uuid-typed, DB rows only, memory scoped to user (`routes/knowledge.py:145-149, 188-192`; `knowledge.py:390-416, 595`).
- Body limit 64 KB default, 8 MB only on exact `/files/upload`, declared and chunked enforced (`body_limit.py:23-89`).

**Desktop / Rust proxy**
- Exact segment-wise route matching; `{id}` lowercase UUID only; trailing `/`, `//`, `..`, `%2e%2e`, extra segments, `#`, CRLF, non-ASCII refused (`proxy.rs:121-141, 251`; tests `:468-552`). 31 routes; `/events`, `/docs`, `/openapi.json`, grant revoke, conversation deletes not exposed. Methods GET/POST/PATCH/DELETE uppercase only.
- Query: per-route key allowlist, no duplicates/empties, plain values `[A-Za-z0-9-]{1,64}`, text keys unreserved + well-formed `%XX`, path ≤512 (`proxy.rs:165-183`).
- Base64 decoder strict/canonical, length capped before decode, 1 B–8 MiB result, exact `{content_base64}` body, POST-only (`proxy.rs:197-240`); matches backend `MAX_UPLOAD_BYTES`.
- Token: 32 bytes getrandom, Rust-only, Debug-redacted (`backend.rs:52-67`); passed via stdin only, no shell (`:197-214, 342-376`); status to webview has no port/token (`:77-83`); only `api_request` and `backend_status` commands (`main.rs:20-23`); errors are fixed codes (`proxy.rs:94-103, 350-357`); WS auth header sensitive (`events.rs:75-83`).
- HTTP client: no env proxy, no redirects, 30 s, 4 MiB cap, 127.0.0.1 only (`proxy.rs:299-314`). Event bridge 127.0.0.1, `phoenix.v1` subprotocol, 64 KiB frames, envelope keys only (`events.rs:56-137`).
- Tauri capability: main window, two commands + event listen; no fs/shell/http/remote URLs. Production CSP strict (`tauri.conf.json:17-25`). No `dangerouslySetInnerHTML`/`innerHTML`/`eval` in `apps/desktop/src`.

**Secrets / observability**
- Logs: fixed correlation fields, ID-shaped only (`structured_logging.py:113-123`); exceptions log type + frames, never message (`146-149`); 5 MiB × 5 rotation (`215-216`); API token registered for redaction before logging (`80-84`, `launcher.py:259-273`).
- Event allowlist (`mission_events.py`): new KAIDO/voice/research/capability fields are closed sets or UUIDs; no transcript/query/URL/path/typed text; control events dropped rather than leaked. Event logs built only from allowlisted payloads (`observability.py:575-599`). Metrics route GET-only behind token, closed model (`routes/observability.py:17-22`).
- Media credentials in Windows Credential Store (`server.py:132, 726`); YouTube key in header, not URL (`youtube.py:1081`).
- Settings: only workspace root and mission path writable, workspace validated (`persistence/settings.py:24-38`, `routes/system.py:129-143`); web research off by default, env-only enable (`core/config.py:29-32`). Onboarding detection-only (`api/onboarding.py:21-23, 49-61`).

**Installer / CI / scripts**
- Uninstaller deletes only fixed sub-paths, guards empty profile path, skips user data on update/silent, never touches custom data dir; per-user install, no elevation (`installer-hooks.nsh:14-20`, `tauri.installer.conf.json:35`).
- Embedded CPython pinned python.org URL + SHA-256 (`build_installer.py:59-61, 119-135`); wheels hash-required, binary-only, frozen export (`183-206`); no shell (`86-95`); only git-tracked migrations staged (`211-224`).
- CI: `permissions: contents: read`, `pull_request` (not `_target`), no secrets, no event data in `run:`, artifact upload only (`release-installer.yml:65-66`).
- Voice model downloads HF-only with size + SHA-256 before move (`voice_setup.py:55-66`); Colibri gate localhost default, no shell (`colibri_gate.py:550-553`); browser install only on explicit `--install`, no runtime download (`install_control_browser.py:48-49`, `browser_setup.py:41-45`); Playwright context `accept_downloads=False`, service workers blocked, no permissions, fresh context (`browser.py:924-926`).

## Test evidence

| Command | Result |
|---|---|
| `pytest tests/test_computer_control_steps.py tests/test_kaido_missions.py tests/test_final_grounding.py tests/test_prompt_injection_defenses.py tests/test_voice_pipeline.py -q` | 808 passed, 1 skipped |
| `pytest tests/test_web_research.py tests/test_knowledge_api.py tests/test_media_document_steps.py -q` | 163 passed, 1 skipped |
| `pytest tests/test_observability.py tests/test_settings_onboarding.py tests/test_build_installer.py tests/test_secrets_boundary.py -q` | 471 passed |
| `node tests/desktop_adapters.mjs` | 37/37 passed |
| `cargo test` (apps/desktop/src-tauri) | 23/23 passed |

## Recommended before release

1. SR-01 (one-line logger level fix + test) — INV-018 violation.
2. SR-02 + SR-05 + SR-07 (SSRF hardening) — load-bearing given persistent research grants (SR-18).
3. SR-04 decision recorded in DECISIONS.md (reveal control or refuse hidden P4 fills).
4. SR-03 move browser dir to per-user location.
