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
