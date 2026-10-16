# 09 — Security

## Threats

- prompt injection
- malicious webpages/files
- command injection
- path traversal
- secret leakage
- privilege escalation
- unsafe agent self-modification
- dependency/supply-chain compromise
- forged completion/audit information

## Untrusted inputs

- web content
- uploaded files
- model output
- tool stdout/stderr
- document metadata
- third-party responses

## Controls

- schema validation
- scoped paths
- safe process argument handling
- secret isolation
- permission checks
- audit events
- output sanitization
- content/instruction separation
- dependency pinning
- least privilege

## Secrets

Use OS-appropriate secure storage where possible. Never put secrets in normal logs, embeddings, normal conversational memory, or model prompts unless strictly required.

## Prompt injection

External content cannot override system/project rules. Copied commands are never auto-executed simply because a webpage/document requests it.

## Local API

PHX-101, ADR-085. The backend (`python -m phoenix.api`) is reachable only by the PHOENIX desktop shell.

- **Bind.** It binds `127.0.0.1` or `::1` only. Other hosts are refused before anything binds.
- **Per-launch token.** The shell generates at least 32 random bytes on every launch, encoded as URL-safe base64 without padding, and writes the token as one line to the backend's stdin. It is never passed in argv or the environment. Without a valid token within 10 s the backend refuses to start. There is no insecure or development bypass.
- **Every request is checked**, in middleware before routing, so `/health`, `/ready` and unknown paths are covered too:
  - exactly one `Host`, equal to the bound `host:port`, which defeats DNS rebinding;
  - no `Origin`, or exactly one of `http://tauri.localhost`, `https://tauri.localhost` or `tauri://localhost`;
  - the token, compared in constant time. HTTP sends `Authorization: Bearer <token>`. A WebSocket offers the subprotocols `phoenix.v1` and `phoenix.auth.<token>`; a failure closes it with 1008 before accept, and only `phoenix.v1` is echoed.
- **No CORS.** There is no CORS for any origin. Browsers cannot read responses, and foreign origins are refused.
- **Errors.** Errors are `{"error": {"code", "message", "request_id"}}` with fixed messages. Request bodies, header values and the token are never echoed.
- **Logging.** The token is never logged, and its holder's `repr` is redacted. The access log and proxy-header trust are off.
