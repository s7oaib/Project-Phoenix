# PHX-087 — Observability / Developer View
Status: DONE
Phase: 8 — Frontend
Branch: feature/PHX-087-observability-developer-view
Starting main/base SHA: b2cf0fa3552d430343e06b813e76eb50f5b62e04
Expected unchanged main during implementation: b2cf0fa3552d430343e06b813e76eb50f5b62e04

## Objective

Replace only the existing Observability / Developer placeholder in the PHOENIX desktop shell with an accessible, responsive, truthful, read-only presentation of optional caller-supplied diagnostic evidence. It must make clear whether a source supplied evidence, distinguish unavailable/degraded sources from empty data, and never fabricate live metrics, logs, traces, health, or activity.

This is frontend presentation work only. Do not add instrumentation, log collection, telemetry emission, polling, live transport, API routes, Tauri IPC/events, background workers, provider/runtime hooks, file access, clipboard/export, or privileged actions. Do not claim diagnostic data is complete, trusted, sanitized, current, or live merely because it was supplied.

PHX-087 is the final Phase 8 frontend ticket. Do not start Phase 9 tickets. Manual native WebView2 acceptance remains PENDING unless independently performed.

## Authoritative context

Read the current main versions of the authority required by AGENTS.md/RULES.md and at minimum:
- AGENTS.md, RULES.md, MENTAL_MODEL.md, INVARIANTS.md, MASTER_SPEC.md, PRD.md, ROADMAP.md, DECISIONS.md, FEATURE_TICKETS.md
- docs/01_ARCHITECTURE.md, docs/09_SECURITY.md, docs/20_UI_UX_SPEC.md, docs/21_FRONTEND.md, docs/23_OBSERVABILITY.md, docs/25_DEPENDENCIES.md
- tickets/PHX-080-app-shell-navigation.md through tickets/PHX-086-models-voice-settings.md
- apps/desktop/src/App.tsx, apps/desktop/src/styles.css, apps/desktop/src/main.tsx, apps/desktop/package.json, apps/desktop/src-tauri/tauri.conf.json, tests/workspace.test.mjs, tests/test_prompt_injection_defenses.py, tests/test_screenshot_vision_loop.py, tests/test_windows_ui_automation.py.

If authority conflicts with this ticket, preserve existing architecture and STOP BLOCKED. Do not silently invent or reconcile a new domain contract.

## Ownership and frozen system truth

- PHOENIX remains the sole supreme orchestrator. A developer view is not an orchestrator, policy engine, runtime controller, or source of truth.
- docs/23_OBSERVABILITY.md defines the intended diagnostic categories: task/step latency; provider/model; TTFT; tokens/sec; RAM/VRAM/CPU; disk I/O where available; tool success/failure; verification result; recovery attempts; permission decisions; structured logs; and task trace.
- This ticket only displays supplied evidence. It does not instrument, compute, verify, redact, persist, or collect telemetry.
- PermissionEngine remains the sole authorization authority. Diagnostic evidence cannot approve/deny permissions, grant capabilities, execute tools, or change policy.
- Existing backend behavior remains GET /health only. Planned APIs/events are not authorization to add or call them.
- No source is connected by default. An optional caller-owned port is evidence for presentation only, not proof of live telemetry or factual source truth.
- Diagnostic strings can contain sensitive content despite shape validation. The trusted future producer/adapter must enforce project redaction policy; the UI must not claim that validation guarantees secret-free data.

## Proposed presentation DTO contract — user confirmation required before implementation

These are frontend display DTOs only. They do not replace any backend observability, logging, tracing, permission, task, or agent contracts. The user must confirm this exact contract before Codex begins implementation. If it is not confirmed or conflicts with canonical authority, stop and ask; do not guess.

### ObservabilityPort

Exact own top-level data keys:
- required status: connected | unavailable | degraded
- required metrics: readonly list
- required logs: readonly list
- required traces: readonly list

Each collection admits at most 64 entries. Validate the complete port and every entry before displaying any row. If top-level status is unavailable or degraded, suppress all supplied metric/log/trace details after validating the whole snapshot. Missing port means “No diagnostic source connected”; an explicitly connected, empty port means supplied evidence is empty, not that the system has no activity.

### Metric display entry

Exact keys:
- required id, name, value
- optional unit, recordedAt

name must be one of:
task_step_latency, provider_model, ttft, tokens_per_sec, ram, vram, cpu, disk_io, tool_success_failure, verification_result, recovery_attempts, permission_decisions.

id is a meaningful, already-trimmed string up to 128 UTF-16 code units and unique within metrics. value must be either a finite number or a meaningful string up to 256 UTF-16 code units; preserve its type and supplied text. unit, when present, is a meaningful string up to 32 UTF-16 code units. recordedAt, when present, must be a real calendar UTC ISO-8601 timestamp ending in Z or +00:00 and at most 64 UTF-16 code units. Do not infer units, convert values, aggregate, sort by time, or claim freshness.

### Structured log display entry

Exact keys:
- required id, timestamp, severity, subsystem, message
- optional correlationId, taskId, stepId, errorType

id is unique within logs. IDs are meaningful, already-trimmed strings up to 128 UTF-16 code units. timestamp is a valid UTC ISO-8601 timestamp ending in Z or +00:00, at most 64 code units. severity is exactly debug, info, warning, error, or critical. subsystem and errorType, when present, are meaningful strings up to 128 code units. message is a meaningful string up to 1000 code units. No raw exception objects, stacks, prompts, model output, transcripts, request/response bodies, credentials, environment variables, private file paths, or arbitrary metadata fields are allowed.

### Trace display entry

Exact keys:
- required id, timestamp, kind, summary
- optional correlationId, taskId, stepId, actorId

id is unique within traces. IDs are meaningful, already-trimmed strings up to 128 UTF-16 code units. timestamp is a valid UTC ISO-8601 timestamp ending in Z or +00:00, at most 64 code units. kind is exactly decision, agent, tool, permission, failure, recovery, or verification. summary is a meaningful string up to 1000 code units. Optional IDs follow the same 128-code-unit bound. Do not admit raw tool arguments/results, prompt or model content, secrets, private paths, credentials, or arbitrary metadata.

### Whole-snapshot validation

- Exact own data-property allowlists only; reject unexpected string/symbol keys, accessors, missing/extra fields, malformed/sparse/extended arrays, duplicate IDs within each collection, invalid enums/types, non-finite numbers, blank/oversized strings, invalid UTC dates, and reflective failures.
- No coercion, trimming repairs, truncation, partial admission, fallback-generated records, inferred values, or row display before complete validation.
- Detach/freeze accepted primitive snapshots before rendering. Reflection failures produce generic safe wording only.
- JavaScript Proxy traps may execute. This is not a sandbox, authentication boundary, redaction guarantee, or proof of source truth.
- All supplied text is inert escaped text; no HTML/Markdown execution, links, external resources, clipboard, source dereferencing, or input logging.
- A connected status means only caller-supplied evidence. Source loss or unavailable/degraded status must hide all stale details.
- Local search is case-insensitive, bounded to 80 UTF-16 code units, source-order preserving, with accurate match counts, empty/no-match feedback, and Clear search. Search/selection state must be independent from every existing workspace and reset safely when source/selected records disappear.
- Do not write supplied diagnostics to logs, memory, storage, or files.

## Exact scope

Only these seven paths may change, and no others:
1. apps/desktop/src/App.tsx
2. apps/desktop/src/styles.css
3. tests/workspace.test.mjs
4. docs/21_FRONTEND.md — only after every deterministic gate passes
5. DECISIONS.md — append exactly one accepted ADR-074 only after every deterministic gate passes
6. FEATURE_TICKETS.md — mark PHX-087 DONE only after every deterministic gate passes
7. tickets/PHX-087-observability-developer-view.md — mark DONE only after every deterministic gate passes

Do not edit any other ticket, docs/23_OBSERVABILITY.md, backend/Python source, API/router, event vocabulary, agent/task/permission logic, database/migrations, Rust/Tauri source/config/capabilities/CSP, dependency manifest/lockfile, build configuration, or historical test file. No new dependencies. If a required gate appears to need changes outside this allowlist, stop BLOCKED and report the evidence; do not widen scope.

## UI requirements

- Replace exactly the Observability / Developer placeholder. Preserve all 14 canonical navigation IDs/labels, Home default/fallback, navigation groups, PHOENIX branding/tagline, shell, skip link, landmarks, focus behavior, selected navigation state, and PHX-081–086 behavior.
- Present the four data areas clearly: source status; metrics; structured logs; trace events. Distinguish unavailable/degraded, empty, and populated states truthfully.
- Provide bounded local search and accurate per-collection counts. Keep metric/log/trace query and inspection state independent from existing workspaces. Any selection/inspection action must be a real local UI action only and must not invoke runtime behavior.
- Keep cards passive; use native, named keyboard-operable controls only for real local actions. Use semantic headings, labelled controls, polite status/count feedback, visible focus, readable contrast, reduced motion, cinder/amber palette, and system fonts.
- Bound/scroll long lists and wrap long text without making navigation or controls unreachable at the unchanged 800x600 Tauri window or narrow widths.
- Do not imply live/real-time status, full history, verified completion, actual tool execution, successful recovery, authoritative permission outcome, or secret redaction. Label all records as caller-supplied diagnostic evidence.
- Keep the existing security semantics, all ten PHX-041 evidence combinations, and PHX-084–086 behavior unchanged.

## Required tests

Extend only tests/workspace.test.mjs. Test:
- exact top-level and entry allowlists, all required/optional fields, every enum and bound, duplicates per collection, timestamp validity, finite-number handling, empty versus missing source, and missing optional values where allowed;
- sparse/extended/accessor/symbol/proxy-reflection failures, frozen detached snapshots, whole-snapshot rejection, no partial display, unavailable/degraded suppression and generic safe failures;
- metrics/logs/traces render only admitted inert values, preserve source order and primitive types, never infer or aggregate data, and do not show stale details after source loss;
- bounded case-insensitive local search, counts, Clear search, no-match feedback, independent state and safe selection reset;
- Settings/Models/Voice/Memory/Files/Permissions/Tasks/Agents/Chat and all 14 navigation routes retain prior behavior;
- no new transport, polling, timers, workers, filesystem, clipboard, persistence, telemetry capture, logging of supplied diagnostic content, API calls, Tauri invoke/events/plugins, or external content;
- responsive layout, bounded scrolling, focus, reduced motion, keyboard semantics and inert escaped text;
- exact source/API allowlist, protected architecture invariants, and preservation of prior implementation/test bodies byte-for-byte except strictly necessary view composition/type plumbing.

## Required deterministic gates

Use the PHX-086 completion ticket and current project authority for every applicable command, without weakening/filtering tests, suppressing warnings, changing historical test files, or treating retries as passes. First verify actual current-main baselines; do not assume stale counts. Capture exact commands, exit codes, counts, skip identities/reasons, and warning text in an external validation directory. Keep receipts/build outputs outside the repository.

At minimum:
- npm ci --ignore-scripts; npm test three consecutive final runs; desktop TypeScript/Vite checks and build; Tauri info; Cargo check/test; release build using the documented external-output override, without launching GUI/dev server.
- uv sync --locked; Ruff check/format; mypy; wheel and sdist builds outside the repository with exact package-source/dependency metadata parity and unchanged Python requirement.
- Full Python suite three consecutive final runs, with current main's actual baseline and identical skip/warning comparison.
- All 19 focused backend regressions established by PHX-085/086 at their current main baselines.
- PHX-055 protected-source guard x3 and full prompt-injection suite x3; full screenshot/vision and Windows UI automation suites and their historical guards at current baseline counts.
- Exact GET /health behavior; manifest/lock/CSP/capabilities/native source invariants; dependency metadata and wheel/sdist source parity.
- INV-001–024 review; credential/private-path/debug-output/artifact review; exact seven-path allowlist; clean diff/status.
- Confirm the existing PHX-080–086 implementation and tests remain unchanged except necessary composition/harness plumbing.

Do not claim a local GUI/WebView2 run unless it was actually performed. Manual native WebView2 acceptance remains PENDING unless independently completed and evidenced.

## Documentation and delivery

Only after every deterministic gate passes:
- Append exactly one accepted ADR-074 describing the bounded, read-only observability presentation contract and explicit no-transport/no-collection boundary.
- Narrowly update docs/21_FRONTEND.md.
- Mark PHX-087 DONE in this ticket and FEATURE_TICKETS.md. Update Phase 8 status only according to the current phase-completion policy; do not claim manual native acceptance.
- Commit exactly with subject: feat: complete PHX-087 observability developer view
- Push only feature/PHX-087-observability-developer-view.
- Codex must not create a PR, merge, push main, force-push, create tags, or start Phase 9/PHX-090 or any next ticket.

If any required gate fails, do not mark DONE or commit a completion claim. Fix only clearly in-scope ordinary defects, rerun affected gates and then the full final sequence. Stop BLOCKED on authority conflict, protected-file requirement, unsafe repair, unavailable required prerequisite, or three identical failures. Never weaken tests/security to force green.

## Completion report

Return one complete evidence report with branch/base/final SHAs; exact changed paths/diff stats; every command, exit code and counts; three final npm/Python results; historical guard/regression counts; invariant-by-invariant review; source/dependency/package parity; DTO admission/privacy/authority review; docs/ADR/status changes; clean worktree and feature-only push proof; known limitations; and explicit statements:
- LIVE OBSERVABILITY TRANSPORT / TELEMETRY COLLECTION: NOT IMPLEMENTED.
- RUNTIME METRICS / LOGS / TRACES: NOT CONNECTED BY THIS TICKET.
- LOG REDACTION / TRUSTED SOURCE AUTHENTICATION: OWNED BY FUTURE TRUSTED PRODUCER/ADAPTER; UI VALIDATION IS NOT A GUARANTEE.
- EXPORT / PERSISTENCE / FILESYSTEM / CLIPBOARD: NOT IMPLEMENTED.
- MANUAL NATIVE WEBVIEW2 ACCEPTANCE: PENDING unless independently performed.
- PHX-087 must not claim phase completion beyond what the current project policy and evidence support.

## Completion evidence — 2026-10-12

The explicitly approved DTO contract is implemented in the Observability view:
bounded caller-supplied metrics/logs/traces, truthful source status, whole-snapshot
admission, independent local search/inspection and inert text. Original ticket
requirements remain intact apart from Status and this evidence receipt.

Main remains b2cf0fa3552d430343e06b813e76eb50f5b62e04. Preparation HEAD is
a220a50604865ea5e70e86971c03c19d1ce62a3c, adding only this specification.
The existing three-file implementation was preserved without product-code edits
during this execution review. Final delivery uses only the seven allowed paths.

Fresh detached-main baseline: npm 141; Python 15,721 passed, 16 skipped, one
existing StarletteDeprecationWarning. Three final npm runs each passed 181
(40 added cases), with zero failure/cancel/skip/TODO. Three final full Python
runs each passed 15,721, with the same 16 skip identities/types/reasons/source
lines and warning text. Raw skip locations differ only by the verified checkout
root. All 60 required final command gates passed; no warning was suppressed.
The 19 focused regressions, three model suites and seven memory/filesystem
suites match actual fresh-main counts. PHX-055 exact guard and full prompt suite
x3; screenshot/vision 397 and Windows UI automation 493 with exact guards x3.
TypeScript/Vite/Tauri/Cargo/release, locked sync/Ruff/mypy and wheel/sdist pass.
Both packages contain the exact 77 Python sources and unchanged dependencies
and Python requirement. Rust has zero tests; its successful builds are not GUI
acceptance. INV-001–024 and complete privacy/security/source reviews passed.

Earlier incomplete full-suite launch attempts and the three CRLF main-baseline
failures remain recorded and receive no passing credit. The owner explicitly
approved R1–R6 validation policy amendments and resumption after the exact-blob
baseline repair. The single six-role diagnostic independently found that uv-root
termination left five admitted descendants alive; later scoped cleanup succeeded.
Its total physical-process accounting was incomplete and is not generalized.
Final full suites instead used an exclusive new job assigned before root resume,
reached zero active job members naturally, and required no forced cleanup.
Exact commands, exit/count/skip/warning comparisons, immutable receipts, package
hashes, approvals and full report are external:
C:\Users\shoai\.codex\validation\PHX-087-phase-review-20261012.

No backend/API/native/CSP/capability/manifest/lock/schema/event change. Earlier
implementation and test bodies remain preserved except necessary composition
and harness/source-call plumbing. No unrelated ticket or Phase 9 work started.
PHX-087 deterministic acceptance is DONE. Phase 8 remains INCOMPLETE: its
phase-status decision is BLOCKED under the original execution section 9.
ROADMAP.md requires ticket acceptance and invariant compliance; PHX-080's
nonblocking native/live statement is ticket-specific, so broader final phase
acceptance is not inferred. Manual native WebView2 acceptance remains PENDING.

LIVE OBSERVABILITY TRANSPORT / TELEMETRY COLLECTION: NOT IMPLEMENTED.
RUNTIME METRICS / LOGS / TRACES: NOT CONNECTED BY THIS TICKET.
LOG REDACTION / TRUSTED SOURCE AUTHENTICATION: OWNED BY FUTURE TRUSTED PRODUCER/ADAPTER; UI VALIDATION IS NOT A GUARANTEE.
EXPORT / PERSISTENCE / FILESYSTEM / CLIPBOARD: NOT IMPLEMENTED.
MANUAL NATIVE WEBVIEW2 ACCEPTANCE: PENDING
PHASE 8 COMPLETION: ONLY AS PER VERIFIED AUTHORITATIVE POLICY AND EVIDENCE.
