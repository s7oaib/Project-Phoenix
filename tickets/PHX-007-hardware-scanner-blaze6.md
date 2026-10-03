# PHX-007 — Hardware Scanner / BLAZE-6

Status: TODO

Phase: 0 — Foundation

## Objective

Implement an explicit, read-only hardware scanner that produces a typed local hardware snapshot and evaluates the development machine against the frozen BLAZE-6 profile without introducing model/provider runtime behavior.

## Authoritative context

Read before implementation:

- `AGENTS.md`
- `RULES.md`
- `MENTAL_MODEL.md`
- `INVARIANTS.md`
- `MASTER_SPEC.md`
- `PRD.md`
- `DECISIONS.md`
- `docs/01_ARCHITECTURE.md`
- `docs/05_MODEL_STRATEGY.md`
- `docs/06_HARDWARE_PROFILE.md`
- `docs/09_SECURITY.md`
- `docs/23_OBSERVABILITY.md`
- `docs/24_TEST_EVALUATION.md`
- this ticket

## Dependencies

- PHX-001 through PHX-006 — DONE

## Requirements

### Boundary

- Keep hardware discovery independent of FastAPI, Tauri/React, persistence, EventBus, model providers, agents, tools, permissions, voice, and web systems.
- Prefer a small module under `phoenix/core/` unless existing architecture indicates a better foundation boundary.
- Importing the module or constructing scanner objects must not perform a scan.
- Scanning occurs only through an explicit function/method call.

### Typed snapshot

Provide a typed immutable/validated snapshot covering at least:

- OS/platform identifier
- CPU model when discoverable
- physical CPU core count when discoverable
- logical processor count
- total system RAM
- visible local filesystem volumes/mounts with total/free bytes where safely discoverable
- NVIDIA GPU entries when `nvidia-smi` is available, including GPU name and total VRAM
- NVIDIA driver version when exposed by the same read-only query
- scan timestamp in timezone-aware UTC

Unknown/unavailable fields must be represented truthfully as unavailable/`None`, not fabricated.

### BLAZE-6 assessment

Evaluate the observed snapshot against the frozen development hardware profile:

- Intel Core i7-13650HX
- 14 physical cores
- 20 logical processors
- approximately 24 GB RAM
- NVIDIA GeForce RTX 3050 Laptop GPU
- 6144 MiB VRAM

The assessment must:

- expose per-check evidence/reasons rather than only a magic boolean
- tolerate normal RAM reporting differences using a documented narrow range/tolerance
- use stable hardware identity for matching
- not require planning-time free-space values to remain identical
- not require the currently installed Ollama version to classify hardware as BLAZE-6
- never claim BLAZE-6 when required identity evidence contradicts the profile
- surface unavailable evidence separately from contradictory evidence

Do not introduce EMBER/INFERNO/SUPERNOVA auto-classification in this ticket.

### GPU discovery

- Prefer `nvidia-smi` for NVIDIA name/VRAM evidence when available.
- Do not rely on Windows `AdapterRAM` when better NVIDIA runtime data is available.
- Invoke external commands without `shell=True`.
- Use fixed argument lists, bounded timeout, captured output, and explicit error handling.
- Failure/missing `nvidia-smi` must degrade GPU evidence truthfully and must not crash the entire scanner.
- Do not contact any network service.

### CPU/RAM/storage discovery

- Use read-only OS/runtime facilities.
- A small, well-established system-introspection dependency such as `psutil` is acceptable if justified; do not add multiple overlapping hardware libraries.
- Do not use deprecated `wmic` as an architectural requirement.
- Do not mutate system state.
- Do not create PHOENIX directories merely to test drives.

### Safety and side effects

An explicit scan may perform read-only system inspection and bounded read-only subprocess execution. It must not:

- create/delete/write files or directories
- initialize SQLite or run Alembic
- bind/connect network sockets
- contact Ollama/Colibri services
- start persistent processes, threads, or background tasks
- request elevation/admin rights
- execute PowerShell/shell command strings derived from input
- change environment variables or machine configuration

### No fake runtime health

- Do not change `GET /health`.
- Do not emit `phoenix.ready` or other startup events.
- Do not treat the scanner as provider/model health.
- Do not implement PHX-010+ model routing/runtime behavior.

## Suggested API shape

Names may differ, but keep the public surface small, for example:

- `HardwareSnapshot`
- `GpuInfo`
- `DiskInfo`
- `Blaze6Assessment`
- `scan_hardware()`
- `assess_blaze6(snapshot)`

Prefer dependency-injectable helpers for command execution/system readings where that materially improves deterministic testing.

## Tests

At minimum verify:

1. module import is side-effect free
2. typed snapshot validates expected data
3. UTC timestamp is timezone-aware
4. logical/physical core data is represented correctly
5. RAM bytes are represented without lossy string-only storage
6. disk totals/free bytes are represented as integers
7. mocked `nvidia-smi` output parses GPU name/VRAM/driver correctly
8. multiple NVIDIA GPUs can be represented without conflation
9. missing `nvidia-smi` is handled truthfully
10. command timeout/failure is handled truthfully
11. malformed `nvidia-smi` output does not fabricate GPU data
12. command invocation uses fixed argv and no shell execution
13. BLAZE-6 known-good fixture matches
14. CPU mismatch fails the relevant profile check
15. logical-core mismatch fails the relevant profile check
16. GPU model mismatch fails the relevant profile check
17. VRAM mismatch fails the relevant profile check
18. normal RAM tolerance is documented/tested
19. missing evidence is distinguished from contradiction
20. changing free disk space does not change BLAZE-6 hardware identity classification
21. scan does not create files/directories/databases
22. scan performs no network bind/connect
23. scan starts no persistent/background work
24. FastAPI `/health` remains unchanged
25. configuration/persistence/events regressions remain green

Use mocked deterministic unit tests for platform variance. A live smoke scan on BLAZE-6 is required as additional evidence but must not replace deterministic tests.

## Live BLAZE-6 verification

On the official development machine, run the scanner and report the observed values for:

- CPU model
- physical cores
- logical processors
- RAM total
- NVIDIA GPU name
- VRAM
- NVIDIA driver version
- detected volumes/free space
- BLAZE-6 assessment

Do not hard-code the live observed result into scanner logic just to make the profile pass.

## Non-goals

- no Ollama provider implementation
- no Colibri integration
- no model loading
- no model lifecycle manager
- no model benchmark harness
- no agent routing
- no startup health sequence
- no WebSocket transport
- no database schema/migration changes
- no frontend hardware UI
- no telemetry upload
- no remote inventory
- no automatic performance tuning

## Verification

Run at minimum:

- targeted hardware tests
- `uv lock`
- `uv sync --locked`
- `uv run --locked pytest`
- `uv run --locked ruff check .`
- `uv run --locked ruff format --check .`
- `uv run --locked mypy`
- `uv build`
- `npm test`
- desktop typecheck/build
- `git diff --check`
- live scanner smoke run on BLAZE-6

If a new dependency is added, update lockfiles using normal tooling and justify it.

## Security review

Confirm:

- no secrets/credentials are collected
- no serial numbers, machine UUIDs, usernames, MAC addresses, or other unnecessary persistent identifiers are collected
- no raw environment dump
- no network activity
- no shell injection surface
- no privileged/elevated command
- no hardware result is treated as permission/authorization evidence

## Acceptance criteria

- typed explicit read-only hardware scanner exists
- import/construction is side-effect free
- scanner gracefully represents unavailable evidence
- `nvidia-smi` is preferred for NVIDIA VRAM evidence when present
- BLAZE-6 assessment is evidence-based and deterministic
- planning-time free space is not used as immutable profile identity
- tests cover success, unavailable, malformed, and mismatch cases
- live BLAZE-6 smoke scan is reported truthfully
- prior Phase 0 functionality remains green
- no future ticket behavior is introduced
- security/invariants reviewed
- ticket/index are updated only after successful verification

## Completion evidence

When complete, append concise evidence covering files, dependency changes, scanner API, live BLAZE-6 values, profile assessment, tests, regressions, security/invariants, and known limitations.
