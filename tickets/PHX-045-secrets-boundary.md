# PHX-045 — Secrets boundary

Status: TODO
Phase: 4 — Tools, Permissions & Security

## Objective
Implement PHOENIX's first explicit secrets boundary so credentials are represented by opaque references, secret material is runtime-only and deliberately hard to serialize/log accidentally, and Windows can use OS credential storage without introducing a normal-memory/database/model-prompt secret path.

PHX-045 is a security boundary, not a user-facing secrets manager and not a permission bypass.

## Frozen authority
- PermissionEngine remains authoritative for P0-P5 decisions.
- P5 remains blocked, including "reveal stored credentials".
- LLM/model/tool/document/web output cannot create or reveal secret authority.
- Secrets never enter normal logs, embeddings, normal conversational memory, RAG chunks/context, audit payloads, or model prompts unless a later explicitly authorized integration strictly requires transient use.
- Tool stdout/stderr and file/document content remain untrusted.
- Secret references are identifiers only; possession of a reference grants no permission.
- No normal PHOENIX database table stores secret plaintext.

## V1 public boundary
Prefer implementation in:

`phoenix/security/secrets.py`

Focused tests:

`tests/test_secrets_boundary.py`

Public concepts should include:
- `SecretReference`
- `SecretMaterial`
- `SecretStore` protocol
- `WindowsCredentialStore`
- safe secret-store errors/error codes
- a pure exact-known-secret redaction helper

No API/frontend integration in PHX-045.

## SecretReference
Create a frozen, extra-forbidden, exact-revalidated reference with exactly:
- `secret_id`

Rules:
- strict string
- outer whitespace normalized
- nonempty
- case-preserving
- bounded length
- conservative identifier grammar suitable for deriving a Windows Credential Manager target
- no plaintext secret
- no username/password/token value
- serialization is allowed because the reference is nonsecret metadata

A reference never proves:
- authorization
- existence
- ownership
- trust
- successful authentication

## SecretMaterial
Secret values MUST NOT use an ordinary Pydantic model or dataclass representation that dumps the plaintext.

Implement a small runtime-only class with these properties:
- accepts one explicit nonempty Unicode secret text value
- no implicit environment/file/model lookup
- internally retains mutable byte-oriented material where practical
- `repr(material)` never contains plaintext
- `str(material)` never contains plaintext
- JSON serialization fails
- pickle/copy/deepcopy are rejected or remain redacted/non-duplicating according to the strict implementation
- no `model_dump`, `dict`, `asdict` plaintext path
- exposes one deliberately named trusted-use method, e.g. `reveal_text_for_trusted_use()`
- supports explicit `close()`
- close best-effort zeroizes mutable internal buffers
- use after close fails explicitly
- optional context-manager support may close automatically
- equality/hash must not expose plaintext

Document honestly that Python cannot guarantee elimination of all immutable interpreter copies; zeroization is best effort, not cryptographic proof.

## SecretStore protocol
Define a provider-neutral synchronous protocol equivalent to:
- `put(reference, material) -> None`
- `resolve(reference) -> SecretMaterial`
- `delete(reference) -> bool`

No:
- list-all plaintext
- export
- dump
- reveal-to-model
- search-by-secret-value
- callback execution
- tool execution
- PermissionEngine mutation

The protocol itself grants no authorization. Trusted callers must already be inside an authorized integration path.

## WindowsCredentialStore
Implement a Windows OS-backed adapter using only the Python standard library / `ctypes`.

Use Windows Credential Manager generic credentials.

Requirements:
- constructor performs no OS credential read/write/delete
- import is safe on non-Windows
- operations on unsupported platforms fail with a safe explicit unavailable error
- derive one deterministic Credential Manager target from the normalized `secret_id` and a fixed PHOENIX namespace/prefix
- do not accept caller-controlled arbitrary target names
- use generic credentials, not domain passwords
- do not persist plaintext anywhere else
- no filesystem/database fallback
- no environment-variable fallback
- no registry fallback
- no local JSON/text file fallback
- no in-memory production fallback
- `put` writes only the supplied secret material
- `resolve` returns a new `SecretMaterial`
- `delete` returns True if removed and False when absent where Windows semantics permit deterministic distinction
- Windows API buffers are freed correctly
- temporary mutable buffers containing secret bytes are best-effort zeroized
- OS/Win32 error text, target paths and secret plaintext are not exposed through public exceptions
- no automatic retry loop

Tests MUST NOT write to the user's real Windows Credential Manager. Use a deterministic injected/monkeypatched WinAPI boundary or equivalent safe fake at the adapter boundary while still testing exact marshaling/cleanup semantics.

## Store identity / target derivation
Use one fixed PHOENIX target namespace, for example:
`PHOENIX/<secret_id>`

The exact frozen prefix chosen by implementation must be:
- deterministic
- documented in ADR/completion evidence
- not caller-controlled
- not secret

Do not expose the target as permission authority.

## Error contract
Use a small closed safe vocabulary such as:
- not_found
- unavailable
- invalid_reference
- invalid_material
- store_failure
- decode_failure

No public error may include:
- plaintext secret
- credential blob
- full Win32 system error message
- raw pointer/buffer
- traceback
- environment
- private SQL/path data

## Exact-known-secret redaction
Implement one pure helper equivalent to:
`redact_known_secrets(text, materials) -> str`

Rules:
- input text strict string
- materials are explicit `SecretMaterial` instances supplied by trusted caller
- replace exact currently known secret substrings with one constant marker, e.g. `[REDACTED]`
- deterministic longest-first behavior for overlapping known values
- do not retain/store the supplied materials
- closed materials fail explicitly
- helper performs no logging/I/O
- helper does NOT claim to discover unknown or semantic secrets
- helper is not a substitute for admission policy or secret isolation

## P5 reveal boundary
Do NOT create a production tool capable of returning plaintext stored credentials.

Do NOT add:
- `secrets.reveal` executable tool
- FastAPI secret reveal endpoint
- WebSocket/Tauri secret reveal command
- model-accessible secret resolver
- P5 override

Tests must prove existing PHX-041 P5 semantics still block a synthetic reveal-style ToolDefinition/PermissionRequest even with:
- explicit user approval
- grants
- policy-looking evidence
- request text saying approved

This test is evidence only; do not register the synthetic reveal tool in production.

## Normal-memory separation
PHX-030/031 remain authoritative.

Prove:
- `SecretMaterial` is not accepted as normal MemoryRecord content or metadata
- PHX-045 does not call PuddingMemoryService
- no automatic conversion of secret to string for memory
- no secret reference is automatically promoted into memory content
- no schema/migration is added for secrets

A nonsecret `SecretReference.secret_id` may be held by trusted future integration metadata only; PHX-045 itself does not add such storage.

## RAG / embedding separation
Prove:
- no DocumentIngestionService call
- no VectorStore add/search call
- no EmbeddingProvider call
- no secret value is embedded
- no secret value enters RetrievalContextResult/CitationBundle
- no automatic secret document ingestion
- no automatic secret indexing

## Audit separation
PHX-042 remains authoritative.

Never put plaintext secret material into:
- AuditRecord
- request.reason
- resource
- correlation ID
- tool result status evidence

PHX-045 does not change audit schema or behavior.

Do not add secret values to audit merely for redaction testing.

## Filesystem separation
PHX-043 remains unchanged.

Do not:
- scan filesystem for secrets
- store secret files
- read .env automatically
- read SSH keys
- read browser credential files
- broaden filesystem trusted root

## Terminal separation
PHX-044 remains unchanged.

Do not:
- inject secret material into process environment
- inject secret material into argv
- inject secret material into stdin
- read secret environment variables
- alter terminal inherited-environment allowlist

Future trusted integrations may explicitly design secret injection with permission/audit; PHX-045 does not.

## Logging / diagnostics
PHX-045 production code must perform no secret-content logging.

No:
- `print(secret)`
- logger secret interpolation
- exception plaintext
- repr plaintext
- test failure message that contains synthetic secret value where avoidable

Static and dynamic tests should protect this boundary.

## Side-effect isolation
Importing/constructing:
- SecretReference
- SecretMaterial
- WindowsCredentialStore

must not:
- access Credential Manager
- read environment secrets
- read files
- connect SQLite
- open network
- start subprocess
- start thread/task
- publish EventBus
- call provider/model
- execute tools

Only explicit store methods may call the OS credential backend.

## No EventBus
No EventBus dependency.
No new event types.
Canonical EventType count remains 32.

## No schema change
No Alembic migration.
No table.
Alembic head remains `0005_audit_events`.
Migrations 0001-0005 unchanged.

## No API/frontend
Do not add:
- secrets endpoint
- secrets settings UI
- Tauri secret command
- direct frontend credential access
- browser-facing plaintext secret object

## Dependencies
Expected dependency changes: NONE.

Use standard library only for the Windows adapter.
Do not add:
- keyring
- pywin32
- cryptography
- custom crypto
- plaintext fallback libraries

Do not invent home-grown encryption.

## Tests
Create `tests/test_secrets_boundary.py`.

At minimum cover:

### Reference contract
1. exact one-field contract
2. frozen / extra-forbidden
3. normalization
4. case preservation
5. invalid IDs
6. conservative grammar
7. deterministic target derivation
8. reference serialization contains no secret material

### SecretMaterial
9. exact value preservation for trusted use
10. repr redacted
11. str redacted
12. JSON serialization rejected
13. pickle rejected
14. copy/deepcopy behavior cannot duplicate plaintext silently
15. close
16. best-effort zeroization evidence
17. use-after-close rejected
18. context-manager close if implemented
19. no plaintext in ordinary exception messages

### Redaction
20. exact known secret redacted
21. repeated occurrence redacted
22. overlapping values deterministic longest-first
23. Unicode secret redacted
24. unknown secret-like text unchanged
25. closed material rejected
26. no I/O/logging

### SecretStore / Windows adapter
27. protocol shape
28. construction no I/O
29. unsupported platform explicit unavailable
30. fixed PHOENIX target namespace
31. caller cannot choose target
32. put marshals supplied secret only
33. resolve returns fresh SecretMaterial
34. absent resolve -> not_found
35. delete true
36. delete absent false
37. WinAPI memory/free semantics
38. temporary buffer cleanup
39. public errors safe
40. no retries
41. no file/env/DB fallback
42. no real Windows Credential Manager mutation in tests

### P5
43. synthetic reveal tool risk P5
44. P5 blocked with no approval
45. P5 blocked with explicit approval
46. P5 blocked with denial
47. P5 blocked with grant-like evidence
48. P5 blocked with policy-looking evidence
49. production secrets module exposes no reveal tool

### Separation
50. no Pudding call
51. no secret normal-memory write
52. no document ingestion
53. no embedding
54. no VectorStore
55. no citation/context insertion
56. no audit plaintext
57. no filesystem scanning
58. no terminal injection
59. no EventBus
60. EventType count 32
61. Alembic head 0005
62. migrations unchanged
63. no API/frontend
64. no dependency change
65. guarded import/construction side-effect free
66. credential/signature scan remains clean

Add further deterministic adversarial cases as useful.

## Regression
Run:
- `tests/test_terminal_tool.py`
- `tests/test_filesystem_tool.py`
- `tests/test_audit_trail.py`
- `tests/test_permission_engine.py`
- `tests/test_tool_registry.py`
- all Phase-3 suites
- all Phase-2 suites
- all Phase-1 suites
- foundation
- desktop

No prior production behavior may be weakened.

## Quality gates
Run:
- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`
- `npm test`
- desktop typecheck/build

## Security / invariants
Review INV-001 through INV-024, especially:
- INV-003: secret/model evidence grants no permission.
- INV-004: no privileged tool bypass.
- INV-005: P4 unchanged.
- INV-006: P5 reveal remains blocked.
- INV-007: audit behavior unchanged.
- INV-011: no frontend credential bypass.
- INV-017: external/tool text remains untrusted.
- INV-018: secrets do not enter normal conversational memory.
- INV-020: no schema mutation.
- INV-021: secret-store failures are explicit and privacy-safe.
- INV-024: no new dependency becomes required.

## ADR
At most one ADR.

ADR-050 may record:
- opaque nonsecret SecretReference
- runtime-only redacted/nonserializable SecretMaterial
- best-effort zeroization limitation
- provider-neutral SecretStore protocol
- Windows Credential Manager standard-library adapter
- fixed PHOENIX target namespace
- no plaintext DB/file/env fallback
- exact-known-secret redaction only
- P5 reveal stays blocked
- no memory/RAG/audit/model/terminal/filesystem integration
- no EventBus/API/frontend/schema/dependency changes

## Completion
Only after all deterministic gates pass:
- mark ticket DONE
- mark PHX-045 DONE in FEATURE_TICKETS.md
- Phase 4 is complete
- leave PHX-050+ TODO
- commit exactly: `feat: complete PHX-045 secrets boundary`
- push only `feature/PHX-045-secrets-boundary`
- no PR/merge/main push

## Next
PHX-050 — Playwright browser tool. Do not start it.
