# PHX-106 — Tauri backend sidecar

Status: IN_PROGRESS (implemented with PHX-107 for the 2026-10-21 review demo; recommendations R1–R12 await owner confirmation)
Phase: 10 — Product Integration (milestone M2, desktop)
Branch: `feature/PHX-106-107-desktop-connected` (local commits only; no push, no PR)
Base: `origin/main` `f8aaef0` (PHX-104 merged)

## Objective

The native PHOENIX app starts and owns the Python backend, and is the only path from the webview to it. The per-launch API token stays in Rust (PHX-101 R6, confirmed by the owner): the webview calls two narrow Tauri commands, and live events arrive as Tauri events.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/INVARIANTS.md` (INV-004, INV-011, INV-019, INV-021, INV-022), `/MASTER_SPEC.md`
- `docs/09_SECURITY.md` ("Local API"), `docs/13_API_CONTRACTS.md`, `docs/14_EVENT_SYSTEM.md`, `docs/21_FRONTEND.md`
- ADR-075, ADR-077, ADR-084 to ADR-089; PHX-100 to PHX-104

## Recommendations (R1–R12)

- **R1 — Rust proxy.** The shell holds the token and forwards HTTP and WebSocket traffic; the token never reaches webview JavaScript, any command result, event, log, command line or environment. `Token`'s `Debug` is redacted; the WebSocket header value is marked sensitive.
- **R2 — Demo/dev launch mode.** `<python> -m phoenix.api --host 127.0.0.1 --port 0 --data-dir <dir>` as an argument array (no shell), working directory the repository root, `CREATE_NO_WINDOW`. Root: `PHOENIX_BACKEND_ROOT` at run time, else at build time, else the checkout the binary was built from. Python: `PHOENIX_BACKEND_PYTHON`, else `<root>/.venv`. Data: `PHOENIX_DATA_DIR` (the backend's own setting), else `~/.phoenix/data` (the backend default). Backend stderr goes to `<data>/phoenix.desktop-backend.log` (it never contains the token). PHX-092 replaces this with a bundled backend.
- **R3 — Token.** 32 bytes from `getrandom`, URL-safe base64 without padding (43 characters), written as one line on the child's stdin, then stdin is closed.
- **R4 — Readiness.** The backend is ready only when stdout yields one JSON object line with `event` `phoenix.backend.ready`, `host` `127.0.0.1` and a non-zero port, within 90 s. EOF first is `backend_exited`; the deadline kills the child and reports `ready_timeout`. The exit code is never used (a graceful CTRL_BREAK also exits 3).
- **R5 — Lifecycle.** A watcher polls the child every 500 ms; an unexpected exit reports `degraded` / `backend_exited` (no automatic restart). App exit kills the backend. On Windows the child is created suspended, put in a kill-on-close job object and then resumed, so the venv launcher's real interpreter (a grandchild) is in the job too; if the app itself dies, Windows kills the whole tree.
- **R6 — Single instance.** An exclusive OS lock on `<data>/phoenix.desktop.lock` (std `File::try_lock`), held for the app's life. A second app on the same data directory reports `degraded` / `already_running` and starts no backend.
- **R7 — Commands.** Exactly two: `api_request(method, path, body)` and `backend_status()`. `api_request` forwards only 18 exact (method, route) pairs of the REST v1 contract: `GET /health`, `/ready`, `/missions`, `/missions/{id}`, `/permissions`, `/permissions/{id}`, `/grants`, `/conversations`, `/conversations/{id}/messages`, `/agents`, `/models`, `/settings`; `POST /chat`, `/missions/{id}/cancel`, `/missions/{id}/run-again`, `/permissions/{id}/approve`, `/permissions/{id}/deny`; `PATCH /settings`. `{id}` is one canonical lowercase UUID; only listed query keys (`limit`, `offset`, `conversation_id`, `include_inactive`) with `[A-Za-z0-9-]{1,64}` values pass; bodies are JSON objects up to 64 KiB on POST/PATCH only. Destructive routes no adapter uses (grant revoke, conversation deletes) and everything else (`/events`, `/docs`, `..`, encoded or absolute paths) are refused before any I/O. Errors are closed codes (`route_not_allowed`, `invalid_body`, `backend_unavailable`, `transport_failed`, `invalid_response`). `backend_status` returns `{"state": "starting" | "ready" | "degraded", "code"?}`, never the port.
- **R8 — HTTP client.** `ureq` 3 without default features (no TLS), the environment proxy explicitly off (it would otherwise receive the token), no redirects, 30 s timeout, JSON responses up to 4 MiB. Host is `127.0.0.1:<port>` and no Origin is sent (PHX-101 R5).
- **R9 — Event bridge.** One `tungstenite` 0.28 client (handshake feature only) to `WS /events`, offering `phoenix.v1, phoenix.auth.<token>` and requiring `phoenix.v1` echoed. Text frames up to 64 KiB that are JSON objects with a string `event_type` are reduced to the eight envelope keys and emitted to the main window as `phoenix:event`. Connection changes go out as `phoenix:events-status` (`{connected, code}`); after any close (1013, 1001/1012, errors) it reconnects with a 250 ms–5 s backoff. The read loop runs every second, which also answers uvicorn's pings.
- **R10 — ACL and CSP.** `build.rs` declares the two commands as an app manifest, so each is an ACL permission. The single capability `desktop-backend-proxy` (main window) grants exactly `allow-api-request`, `allow-backend-status`, `core:event:allow-listen` and `core:event:allow-unlisten`; no plugin. Production `connect-src` is `ipc: http://ipc.localhost` (Tauri 2's IPC custom protocol; nothing else), and the dev CSP adds the same two sources. The regenerated permission TOML files are git-ignored.
- **R11 — Dependencies.** New crates: `ureq` 3.4.2 (+ `ureq-proto`), `tungstenite` 0.28.0 (+ `sha1`, `data-encoding`, `rand` 0.9, `utf-8`). Direct but already locked: `base64` 0.22, `getrandom` 0.3, `serde`, `serde_json`, `windows-sys` 0.61 (job object, toolhelp and thread features).
- **R12 — Guards.** `Cargo.toml`, `Cargo.lock`, `src/main.rs` and `build.rs`, with `tauri.conf.json`, move to the implementation commit `e34ca3d` in a separate pin commit (PHX-088 precedent): `workspace.test.mjs` byte pins and `PHX_106_PATHS` in `test_prompt_injection_defenses.py`; the two `git diff origin/main` guards exclude the changed and new desktop files by exact name. The capability/CSP test now asserts the exact capability, the exact production CSP, the dev `connect-src`, the build-script command list, the registered handlers and that no Tauri plugin exists.

## Verification

- `cargo test` (16 tests: ready-line parsing, bounded wait, token shape and redaction, launch arguments, data-dir lock, route allowlist and refusals, body bounds, closed transport error, envelope sanitising, sensitive subprotocol header), `cargo check`, `cargo clippy --all-targets`, `cargo fmt --check`.
- Release exe (`npm run tauri:build --workspace @phoenix/desktop`) smoke-tested through WebView2 remote debugging: backend `ready`; `GET /ready`, `/health`, `/settings`, `/agents`, `/permissions` 200 through the proxy; `/events`, `DELETE /conversations` and `/missions/../settings` refused; `fetch` blocked by the CSP; event stream connected; a second instance on the same data directory reports `already_running`; killing the interpreter reports `backend_exited`; closing the window and hard-killing the app both leave no backend process.

## Acceptance

- Native app starts the backend and `/ready` returns 200 through the proxy: PASS (smoke above, no live models).
- Live end-to-end mission with an approval in the native app: PENDING (main session demo with live models).

## Limitations

- Demo/dev launch mode only; a release build resolves the repository it was built from. Packaging is PHX-092.
- No automatic backend restart; the user restarts the app after `backend_exited`.
- App exit terminates the backend (no graceful CTRL_BREAK); running missions are recorded `interrupted` at the next start (PHX-102) and can be run again.
