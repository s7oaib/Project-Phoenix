# PHX-045 — Secrets boundary

Status: DONE
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

## Completion evidence — 2026-10-06

### Implementation and public contracts

Added `phoenix/security/secrets.py` and `tests/test_secrets_boundary.py`. Public
API: SecretReference, SecretMaterial, SecretStore, WindowsCredentialStore,
SecretError, SecretErrorCode and redact_known_secrets. No existing production or
test file changed. Documentation changes are this ticket, FEATURE_TICKETS.md and
the single ADR-050 in DECISIONS.md.

SecretReference has exactly one frozen, extra-forbidden, exact-revalidated field.
secret_id is a strict ordinary string, trimmed outside, case-preserving, 1..128
ASCII characters, grammar `[A-Za-z0-9][A-Za-z0-9_.-]{0,127}`. Controls reject before
trimming; path separators, wildcard/target syntax, extras, stored forgeries,
subclasses and malformed extra containers fail. IDs are nonsecret metadata,
serializable, and prove no authorization, existence, ownership, trust or
authentication. Callers must not use secret values as identifiers. No reference
is automatically persisted to normal memory.

SecretMaterial is a runtime-only slotted class, not a Pydantic model/dataclass.
It accepts one explicit nonempty valid Unicode string, preserves whitespace/NUL
and exact text, and retains mutable UTF-8 storage. Repr/str use fixed redaction
markers. JSON/Pydantic JSON, all pickle protocols, copy/deepcopy, __getstate__,
dict/asdict/bytes/vars reject; no model_dump/dict plaintext method exists.
Equality/hash use identity; subclasses and reinitialization reject. The explicit
reveal_text_for_trusted_use method returns exact text only while open, without
permission, logging or persistence. Close is idempotent, marks closed, overwrites
the original buffer in place, and blocks later reveal/put/redaction/context entry.
Context exit closes on consumer failure. Python cannot guarantee erasure of every
immutable input/decoded/revealed/interpreter/OS copy; zeroization is best effort,
not cryptographic proof. Trusted callers own authorization, consumer admission
and explicit lifetime.

SecretStore is provider-neutral, synchronous put/resolve/delete only, granting no
authorization. WindowsCredentialStore is standard-library ctypes, GENERIC type,
fixed `PHOENIX/<normalized secret_id>` target and LOCAL_MACHINE persistence for
the current user's later logons on this machine, without requesting roaming.
IDs preserve spelling, but Windows targets are case-insensitive: case-only IDs
share a credential. Import/construction does no credential I/O; operations lazily
load the system DLL with declared Win32 ABI. Unsupported platforms/DLL/logon
sessions fail unavailable. The adapter accepts no caller-selected target/prefix.

Put exact-revalidates reference/open exact material, marshals only supplied UTF-8
bytes without a terminator, enforces the 2,560-byte OS limit, and wipes temporary
buffers after success or failure. Resolve reads one fixed target, validates generic
type/target/nonempty bounded blob/strict UTF-8, returns fresh independent closable
material, wipes temporary/bounded OS-owned blobs and calls CredFree on every
nonnull allocation. Malformed sizes free without unsafe oversized access.
Cleanup failure closes a produced result before raising. Missing resolve is
not_found; delete is True when removed, False for Windows ERROR_NOT_FOUND.
Operations never retry or fall back. Safe error codes are exactly not_found,
unavailable, invalid_reference, invalid_material, store_failure and decode_failure;
public messages/formatted exception chains exclude values, targets and raw OS
diagnostics. Tests deny the real WinDLL loader and exclusively fake OS calls;
no real credential was read, written or deleted.

Redaction takes strict text and an explicit ordered list/tuple of open exact
materials, replaces exact repeated substrings with `[REDACTED]`, and supports
Unicode/whitespace/NUL. Longer values claim overlaps first across original text;
equal lengths follow caller order. Inserted markers are not rescanned. It performs
no I/O/logging/persistence and retains no materials or regex-cache patterns.
Unknown token-like text stays unchanged: this is neither semantic-secret discovery
nor a substitute for isolation, admission policy, permissions or PHX-055.

### Commands and final results

- `uv run --locked pytest tests/test_secrets_boundary.py -q`: **402 passed**,
  final focused run **0.89 s**, external JUnit. Covers strict/reference forgeries,
  runtime privacy/serialization/state hooks/lifetime, fake marshalling/free/wiping,
  missing/malformed/errors/no retries/platforms, redaction overlaps/purity, P5 and
  normal-memory/document/context/citation/audit-object exclusion. Twelve synthetic
  P5 combinations cover no/approved/denied decisions and no/grant/policy/both
  evidence, with safe/approved request and permission/description text. All remain
  blocked/p5_blocked without evidence IDs; no reveal capability is registered.
- Individually ran all **28 required prior suites** using
  `uv run --locked pytest tests/test_<suite>.py -q` with external JUnit/logs:
  every exit code **0**.
- Phase 4: terminal_tool **270**, filesystem_tool **649 passed, 16 skipped**,
  audit_trail **302**, permission_engine **926**, tool_registry **204**.
  Existing feature aggregate **2,351 passed, 16 skipped**. Skips remain the
  existing Windows symlink privilege limitations; real junction tests pass.
- Phase 3: memory_schema **244**, pudding_memory_service **167**,
  document_ingestion **300**, vector_store_sqlite **273**, retrieval_context **367**,
  provenance_citations **173**; feature aggregate **1,524 passed**, plus
  persistence **12 passed**.
- Phase 2: agent_registry **96**, phoenix_orchestrator **101**, aizen_planner **151**,
  agent_model_router **213**, task_graph_state_machine **348**,
  structured_agent_handoffs **214**, levi_verification **242**, mahoraga_recovery
  **790**, kaido_failsafe **574**; aggregate **2,729 passed**.
- Phase 1: model_provider **61**, ollama_provider **108**, model_registry **83**,
  model_lifecycle **54**, embeddings **182**, colibri_provider **194**,
  model_benchmark **130**; aggregate **812 passed**.
- `uv lock`; `uv sync --locked`: PASS, **36 packages**, no manifest/lock changes.
- `uv run --locked pytest -q`: final **7,944 passed, 16 skipped**, **436.26 s**,
  one existing Starlette/httpx warning. Baseline 7,542 plus 402 new cases. Final
  complete run includes the __getstate__ and missing-extra-state hardening.
- `uv run --locked ruff check .`: PASS; `uv run --locked ruff format --check .`:
  PASS, **94 files**; `uv run --locked mypy`: PASS, **88 source files**.
- `uv build --out-dir <external-validation>/python-build`: PASS, sdist/wheel.
  Wheel content matches the final secrets module and excludes test fixtures.
- Foundation: `uv run --locked pytest tests/test_bootstrap.py tests/test_api.py
  tests/test_config.py tests/test_persistence.py tests/test_events.py
  tests/test_hardware.py -q`: **126 passed**, existing warning only; /health unchanged.
- `npm test`: **4 passed**; `npm run typecheck --workspace @phoenix/desktop`
  and `npm run build --workspace @phoenix/desktop`: PASS; desktop files unchanged.
- Protected-file review: **189 files unchanged** from origin/main after Git
  normalization. All five migrations, schema, events, dependencies, API/frontend,
  memory/RAG, agents/models, registry, permissions, audit, filesystem and terminal
  remain unchanged. Alembic heads exactly **[0005_audit_events]**; EventType **32**.
- Repository credential/signature scan: **194 files**, **zero matches**.
  `git diff --check`: PASS. Reports, archives and caches do not enter staging.

### Security, invariants, acceptance and limitations

Reviewed INV-001 through INV-024: PASS within ticket scope. Reference/material
evidence grants no permission. P4 and P5 authority remains the unchanged
PermissionEngine; prior privileged tool and audit semantics stay unchanged.
Guarded uncached imports/construction on simulated Windows/Linux/macOS and guarded
fake store operations deny actual credential access, environment/file/database
access, network/process/thread/task creation, logging, EventBus, permission/audit,
memory/document/embedding/vector/retrieval/citation calls. Pydantic's ordinary
plugin switch is stubbed without reading the environment; other environment
access stays denied. Static production imports exclude every PHOENIX subsystem.

No automatic memory/reference persistence, secret table, schema/migration change,
RAG/embedding/vector/context/citation insertion, audit plaintext, model/provider
forwarding, filesystem credential scan or terminal argv/env/stdin injection exists.
No EventBus/event, FastAPI/WebSocket/Tauri/React secret flow, dependency addition,
plaintext DB/file/JSON/registry/env fallback, production in-memory store or custom
encryption exists. All **66 listed minimum acceptance boundaries** and the added
adversarial, regression and quality gates PASS. ADR-050 is the sole new decision.

Limitations: caller authorization/admission/lifetime are explicit responsibilities;
exact redaction cannot discover unknown secrets; Python erasure is best effort.
Windows namespace/case semantics do not protect against other applications under
the same user or OS administrators. Hostile process introspection/native pointer
tampering is outside this boundary. Live Credential Manager roundtrips are
deliberately untested; fake WinAPI ABI/behavior tests and simulated non-Windows
unavailability cover the deterministic contract. Future integrations require
separate permission/audit design. Existing skips/warning remain unchanged.

PHX-040 through PHX-045 are DONE; **Phase 4 COMPLETE**. Final staged review and
feature commit/push evidence are reported by the execution run. PHX-050+ remain
TODO. Next: **PHX-050 — Playwright browser tool; not started**.
