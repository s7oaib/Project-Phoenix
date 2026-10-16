# PHX-101 — Local API security

Status: IN_PROGRESS (implemented on a local branch; awaiting owner review of this ticket and the diff)
Phase: 10 — Product Integration (milestone M1, backend)
Branch: `feature/PHX-101-local-api-security` (local commits only; created from the PHX-100 tip)
Base: `feature/PHX-100-backend-composition-root`

## Objective

Only the PHOENIX desktop shell may call the local backend. Other local programs and web pages in any browser must not be able to read from it or act through it. This covers every HTTP request and every WebSocket.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md` (Security 6), `/INVARIANTS.md` (INV-003, INV-011, INV-018, INV-021), `/MASTER_SPEC.md`
- `docs/09_SECURITY.md`, `docs/13_API_CONTRACTS.md`, `docs/14_EVENT_SYSTEM.md`
- PHX-100 and ADR-084

## Owner decision (2026-10-16, given directly to the main session)

The backend binds 127.0.0.1 only, and every launch uses a fresh token:

- The Tauri shell generates a fresh secret on each launch and hands it to the backend.
- Every HTTP request and WebSocket must carry it.
- Origin and Host are checked.
- There is no CORS for other origins.
- Other local apps and web pages must not be able to call the backend.

## Findings

- After PHX-100 the backend has `GET /health` and `GET /ready`, and no WebSocket route. PHX-103 will add `WS /events`.
- **Tauri 2 origins**, verified in the locked `tauri 2.12.1` crate (`src/manager/mod.rs`, `tauri_protocol_url` / `get_app_url` tests):
  - Windows and Android serve the app from `http://tauri.localhost`, or `https://tauri.localhost` with `useHttpsScheme`;
  - macOS and Linux use `tauri://localhost`;
  - dev builds load `devUrl` (`http://127.0.0.1:5173` in `apps/desktop/src-tauri/tauri.conf.json`).
- **Cross-origin calls.** The webview origin (`http://tauri.localhost`) differs from `http://127.0.0.1:<port>`. So a direct webview `fetch` that sends `Authorization` needs a CORS preflight to succeed, and with no CORS it will fail. WebSockets are not subject to CORS, but browsers always send `Origin` on them.
- **Uvicorn.** It logs no headers. Its access log, which would log paths and queries, is already off (PHX-100 R9), as is proxy-header trust.
- **No new dependencies.** `hmac`, `secrets`, `base64` and Starlette's datastructures are enough.

## Recommendations (R, pending owner confirmation)

- **R1 — Token handoff on stdin.**
  - **Delivery.** The launcher reads one line from stdin, waiting at most 10 s and reading at most 1024 characters. The token never appears in argv or the environment, so it is not visible in process listings.
  - **Format.** URL-safe base64 without padding, 43–512 characters, decoding to at least 32 bytes. The shell should use 32 bytes from the OS CSPRNG. The backend checks only the format; it cannot verify entropy.
  - **Order.** The host is validated before the token is read. If the token is missing or malformed, the launcher exits with code 2, a message that never contains the token, and no bind.
- **R2 — No `--dev-insecure` flag.** An auth bypass flag is an easy way to ship an open port by accident. For local development, generate a token and pipe it in, then use the same value with curl or a client:

  ```
  python -c "import secrets; print(secrets.token_urlsafe(32))" > token.txt
  python -m phoenix.api < token.txt
  ```

- **R3 — HTTP authentication.** Every route requires `Authorization: Bearer <token>`. That includes `/health` and `/ready`, and also unknown paths, so route discovery is blocked too.
  - The check runs in pure ASGI middleware, outside FastAPI routing, and compares with `hmac.compare_digest` on bytes.
  - The scheme name is case-insensitive.
  - The request is refused if the `Authorization` header is repeated, or if the header has extra parts.
  - A failure returns 401 with `WWW-Authenticate: Bearer`.
- **R4 — Host.** Exactly one `Host` header, equal to the bound literal `127.0.0.1:<port>` or `[::1]:<port>`. `localhost` and other names are refused (403 `forbidden_host`), which defeats DNS rebinding. Host is checked before Origin and before the token.
- **R5 — Origin.** If `Origin` is present it must exactly match one of `http://tauri.localhost`, `https://tauri.localhost` or `tauri://localhost`. Otherwise the response is 403 `forbidden_origin`. `null` and a repeated `Origin` header are refused. A request with no `Origin` (native clients, the Rust shell) relies on the token alone. The Vite dev origin is **not** allowed (see Q1).
- **R6 — No CORS at all.** No CORS middleware is installed, not even for the Tauri origins. Browsers therefore cannot read cross-origin responses, and cross-origin writes are refused by the Origin check. Consequence: the webview cannot `fetch` the backend directly. **Recommendation for PHX-106 and PHX-107:** the Rust shell owns the token and proxies HTTP and WebSocket traffic for the webview through Tauri commands and events. The token then never enters webview JavaScript, so XSS there cannot steal it. If the owner prefers direct webview calls, PHX-107 must add an exact-origin CORS allowlist for the Tauri origins only (Q2).
- **R7 — WebSocket authentication through `Sec-WebSocket-Protocol`.**
  - **Client.** It offers `phoenix.v1, phoenix.auth.<token>`. Browsers can set this through `new WebSocket(url, [...])`, and the Rust shell can set it too.
  - **Server.** The middleware checks Host, Origin and the token **before** the app sees the handshake. A failure is refused with close code 1008 before accept (uvicorn sends HTTP 403), so an unauthenticated socket never reaches application code.
  - **Accepting.** Endpoints accept only through `accept_websocket()`. That helper re-checks the token (fail closed if the app has no security installed) and echoes only `phoenix.v1`, never the token.
  - **Why not first-message auth.** It would accept unauthenticated sockets, hold them open for the timeout and need its own state machine. It would also let a foreign origin open a socket before failing. Base64url characters are valid in a subprotocol token, and nothing on this loopback path logs headers.
- **R8 — Error envelope.** Errors look like `{"error": {"code", "message", "request_id"}}`.
  - It applies to 401, 403, 404 and 422, and also to 405, other HTTP errors and 500.
  - Messages are fixed text: never the exception detail, request body, header values or token. 422 never echoes input.
  - `request_id` is generated by the server (`uuid4().hex`) and also returned as `X-Request-ID`. A client-supplied ID is ignored.
- **R9 — Composition root requires security.** `build_server_app(settings, *, security, ...)` takes a required `LocalApiSecurity`. No unauthenticated server can be built through the composition root. `create_app()` itself stays unchanged and unauthenticated, but nothing serves it.
- **R10 — Redaction.** `LocalApiSecurity` keeps the token as bytes, and its `repr` shows no token. Neither the token nor any `Authorization` value is logged. Tests capture all log records at DEBUG and stdout/stderr to prove it.

## Open questions

- **Q1 — Dev-mode origin.** The Tauri dev webview loads `http://127.0.0.1:5173`. Should PHX-106 add an explicit dev-only origin option, or does R6 (the Rust proxy, so no browser Origin) make that unnecessary?
- **Q2 — Webview transport.** Should the Rust shell proxy the webview's traffic (R6, recommended), or should the webview call the backend directly with exact-origin CORS? This is decided in PHX-106/107.
- **Q3 — Token rotation.** The token is per launch with no in-process rotation. Is that acceptable for V1?

## Requirements

1. **New `phoenix/api/security.py`.**
   - `parse_token`.
   - `LocalApiSecurity`, a frozen object with a redacted `repr`, holding the token, the bound loopback host and the port.
   - `LocalApiSecurityMiddleware`, pure ASGI, for HTTP and WebSocket scopes.
   - `install_local_api_security(app, security)`: the middleware, the error envelope handlers and `app.state.local_api_security`.
   - `accept_websocket(websocket)`.
   - Constants for the Tauri origins and the WebSocket subprotocol and prefix.
2. **`phoenix/api/server.py`.** `build_server_app` requires `security` and installs it (R9).
3. **`phoenix/api/launcher.py`.**
   - Validate the host, then read and validate the token from stdin (R1), then bind and build `LocalApiSecurity` for the real bound port.
   - Refuse to start without a valid token.
   - No `--dev-insecure` (R2).
4. **`tests/test_screenshot_vision_loop.py` and `tests/test_windows_ui_automation.py`.** Exclude `phoenix/api/security.py` by name, the same way as PHX-100.
5. **Records.**
   - ADR-085 in `DECISIONS.md`.
   - A "Local API" section in `docs/09_SECURITY.md`.
   - A note in `docs/13_API_CONTRACTS.md` on authentication and errors.

## Non-goals

- A production WebSocket route (PHX-103).
- REST routes (PHX-104).
- The Tauri token generation and sidecar (PHX-106).
- Frontend transport (PHX-107).
- TLS on loopback.
- Multi-user or remote access.
- Token persistence or rotation.

## Tests

New `tests/test_api_security.py`, mapped in `tests/matrix.toml` as `unit, integration, api_contract, permission_bypass, secret_leakage`. Updated `tests/test_api_server.py`: the app is built with a test `LocalApiSecurity`, and every request carries the token and the bound Host. Cases:

- **Tokens.** Missing, wrong, malformed and correct tokens: no scheme, other scheme, empty, extra parts, repeated header, wrong length, non-ASCII. All routes are protected, including `/health`, `/ready` and unknown paths.
- **Constant-time compare.** `hmac.compare_digest` is used, checked with a spy.
- **Host.** A wrong Host is rejected: other port, `localhost`, a rebinding name, the IPv6 form on an IPv4 bind, and a repeated header.
- **Origin.** Foreign, `null` and repeated Origins are rejected. All three Tauri origins and a missing Origin are accepted.
- **CORS.** No CORS headers on normal or preflight requests.
- **Envelope.** The error envelope on 401, 403, 404, 405 and 422, with no input echoed.
- **Secrets.** The token never appears in any response, in logs captured at DEBUG, in stdout/stderr, or in `repr`.
- **Startup.** It is refused without a token (empty, malformed or late stdin), with exit code 2 and no bind. The non-loopback refusal from PHX-100 still holds, and the token is not read in that case.
- **WebSocket helper.** It accepts with the right subprotocol and rejects a missing or wrong token, a foreign Origin, a wrong Host, and an app without security. This uses a minimal WebSocket endpoint defined only inside the test.
- **Launcher.** With the token on stdin, the real launcher binds port 0 and prints the single ready line.

## Acceptance

- Targeted tests pass.
- `ruff check`, `ruff format --check` and strict mypy are clean.
- `python scripts/test_matrix.py --validate-only` passes, and `git diff --check` is clean.
- The full matrix runs once on the final clean tip.
- No existing `phoenix/`, `alembic/` or `apps/` file changes.

## Completion evidence

See the bottom of this file once implemented.
