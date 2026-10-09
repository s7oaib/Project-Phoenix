# PHX-085 — Memory / Files Workspace

Status: IN_PROGRESS (ticket preparation only)
Phase: 8 — Frontend
Branch: feature/PHX-085-memory-files-workspace
Starting main/base SHA: 5594229ace471804e4571fc40bc159d4b020862e
Expected unchanged main during implementation: 5594229ace471804e4571fc40bc159d4b020862e

## Objective

Replace only the existing Memory and Files / Documents placeholders in the PHOENIX desktop shell with two professional, accessible, responsive, read-only workspaces for reviewing caller-supplied memory records and document/artifact metadata.

This is a frontend presentation boundary. It does not add live transport, API routes, filesystem access, Tauri IPC, persistence, upload, parsing, indexing, memory mutation/deletion, artifact save/open/download, or any privileged action. Default App must remain disconnected and must not fabricate memory records, files, generated artifacts, paths, or success.

PHX-085 completes neither Phase 8 nor backend integration. PHX-086 and PHX-087 remain TODO. Native WebView2 acceptance must remain explicitly PENDING unless a real manual acceptance is independently performed.

## Authority and mandatory reading

Before editing, read the current main versions of all project authority and these specifically relevant sources in their authority order:

- AGENTS.md, RULES.md, MENTAL_MODEL.md, INVARIANTS.md, MASTER_SPEC.md, PRD.md, ROADMAP.md, DECISIONS.md, FEATURE_TICKETS.md
- docs/01_ARCHITECTURE.md
- docs/03_AGENT_ROSTER.md
- docs/09_SECURITY.md
- docs/10_MEMORY_SYSTEM.md
- docs/11_RAG_SYSTEM.md
- docs/12_DATABASE_SCHEMA.md
- docs/13_API_CONTRACTS.md
- docs/19_DOCUMENT_SYSTEM.md
- docs/20_UI_UX_SPEC.md
- docs/21_FRONTEND.md
- docs/25_DEPENDENCIES.md
- tickets/PHX-030-memory-schema.md
- tickets/PHX-031-pudding-memory-service.md
- tickets/PHX-032-document-ingestion.md
- tickets/PHX-033-vectorstore-sqlite-adapter.md
- tickets/PHX-034-retrieval-context-assembly.md
- tickets/PHX-035-provenance-citations.md
- tickets/PHX-043-filesystem-tool.md
- tickets/PHX-072-upload-parser-routing.md
- tickets/PHX-073-document-generation.md
- tickets/PHX-080-app-shell-navigation.md
- tickets/PHX-081-chat-voice-ui.md
- tickets/PHX-082-agent-network-ui.md
- tickets/PHX-083-task-graph-ui.md
- tickets/PHX-084-permission-center.md
- apps/desktop/src/App.tsx
- apps/desktop/src/styles.css
- apps/desktop/src/main.tsx
- apps/desktop/package.json
- apps/desktop/src-tauri/tauri.conf.json
- tests/workspace.test.mjs
- tests/test_prompt_injection_defenses.py
- tests/test_screenshot_vision_loop.py
- tests/test_windows_ui_automation.py

If an authoritative conflict exists, preserve the current work and STOP BLOCKED. Do not invent a new resolution or expand scope.

## Frozen system truth and ownership

- PUDDING (internal ID `memory`) owns memory behavior. PHX-030/031 own the canonical memory schema and durable service.
- VEGAPUNK (internal ID `knowledge_rag`) owns indexing/RAG. ROBIN (`document_analyst`) owns document analysis/generation. Aliases are display-only.
- PHX-032 owns ingestion; PHX-033 vector storage; PHX-034 retrieval/context assembly; PHX-035 provenance/citations; PHX-072 upload/parser routing; PHX-073 in-memory artifact generation.
- PHX-043's filesystem tool remains a narrow permission-gated text capability. PHX-073 artifacts may be in-memory and do not prove a file was saved to disk.
- The documented REST routes are planned contracts, not proof of implementation. The current API exposes only GET /health. Do not add or call memory/files endpoints.
- The current desktop has no memory/file transport. An optional port is caller-supplied evidence, not proof of a live backend, authoritative state, persistence, or successful filesystem access.
- Do not introduce a second permission, audit, memory, RAG, filesystem, or document-generation authority.

## Exact scope

Only these seven paths may change, and no others:

1. `apps/desktop/src/App.tsx`
2. `apps/desktop/src/styles.css`
3. `tests/workspace.test.mjs`
4. `docs/21_FRONTEND.md` — only after all gates pass
5. `DECISIONS.md` — add exactly one accepted ADR-072 only after all gates pass
6. `FEATURE_TICKETS.md` — only after all gates pass
7. `tickets/PHX-085-memory-files-workspace.md` — update to DONE only after all gates pass

No backend/Python files, database/migrations, API/router, persistence, EventType, registry, permission/audit, parser, document generation, Rust/Tauri config/capabilities/CSP, dependencies, package manifests, lockfiles, build config, or historical test files may change. No new dependency.

## Memory workspace requirements

Implement a typed optional, caller-owned, readonly MemoryPort with exact top-level status `connected | unavailable | degraded` and a readonly list of memory presentation records. It must have no mutating callbacks. Default App has no port and shows an honest disconnected state.

The display DTO is a frontend adapter/presentation contract, not a replacement for PHX-030 MemoryRecord. It may expose the corresponding bounded fields: ID, one of the six exact memory classes (`working`, `episodic`, `semantic`, `user`, `project`, `agent`), content, source type/reference, scope, confidence, importance, created/updated/last-accessed/expiry timestamps, and optional embedding reference. Map from the canonical record at a future trusted adapter; do not change the Python schema. If a field is not supplied, show “Not reported”; never infer it. Do not render arbitrary metadata as trusted instructions or executable content.

Provide bounded case-insensitive local search across admitted display fields, source-order results, match counts, Clear search, native keyboard-operable selection controls, a selected-record details region, and an explicit off-filter-selection notice. Selection should survive navigation and reset safely if its record disappears or the port becomes unavailable/degraded. Preserve the whole-snapshot admission rule: malformed, duplicate, oversized, sparse, extended, accessor-bearing or otherwise invalid input is rejected as a whole; do not partially render or silently clip a record.

Use strict primitive/type checks, own data-property descriptors, finite numeric confidence/importance where canonical constraints allow them, and strict bounded timestamp/text validation consistent with PHX-030. Validate exact known memory classes; do not invent a `secret` class. Catch reflective failures and display only a generic safe error. Detach and freeze accepted primitive snapshots before render. JavaScript Proxy traps may execute; do not claim this is a sandbox or authentication boundary.

Render content and source references as inert escaped text. Do not turn paths/URLs into links, dereference sources, execute Markdown/HTML, expose raw objects/exceptions, or claim source truth. Memory may contain private information: do not log, copy to clipboard, persist, or send displayed content. No memory create/replace/delete/forget controls in this ticket.

## Files / Documents workspace requirements

Implement a typed optional, caller-owned, readonly FilePort (or equivalently explicit name) with exact top-level status `connected | unavailable | degraded` and a readonly list of bounded display metadata records. It has no actions. Default App has no port and shows an honest disconnected state.

The UI DTO is metadata for display only, not a new filesystem/document domain contract. Each admitted item should have a unique bounded opaque ID, a kind distinguishing `source_document` from `generated_artifact`, a bounded display name/title, and optional bounded plain-text format/media type, source type/reference, content hash, timestamp and nonnegative finite size where supplied. Do not infer a disk path, URL, file existence, readability, upload completion, indexing completion, or persistence from these fields. Missing evidence is shown as “Not reported.” Keep generated artifacts visibly distinct from source documents; an in-memory generated artifact must never be labelled as saved.

Provide bounded local case-insensitive search, source-order results, match counts, Clear search, native keyboard-operable selection, selected-item metadata/provenance detail, and explicit off-filter-selection feedback. Preserve selection across navigation and safely reset it when the item disappears or the port disconnects/degrades. Apply whole-snapshot rejection for malformed, duplicate, accessor-bearing, sparse/extended, oversized or invalid records; never partially display a malformed snapshot.

All file names, source references, formats, hashes and other external strings are untrusted plain text. Do not create clickable paths/URLs, open/read/write/delete files, upload/download/save/export, parse bytes, index content, invoke filesystem tools, access a user home directory, or imply successful storage. No arbitrary document content or binary bytes should be added to this UI DTO.

## Shared UI, accessibility, and preservation

- Keep all 14 canonical navigation IDs/labels, home default/fallback, groups, PHOENIX branding/tagline, shell, skip link, landmarks, selected navigation state, focus behavior, and PHX-081/082/083/084 behavior.
- Replace exactly the two existing placeholders, Memory and Files / Documents. Other unimplemented views remain honest placeholders.
- Use the existing cinder/amber palette, system fonts, visible focus, readable contrast, reduced-motion support, and responsive behavior for the unchanged 800x600 Tauri window and narrow widths. No new icon/image/font/framework dependency.
- Use semantic headings, labelled search inputs, native buttons, `aria-pressed` / `aria-controls` where appropriate, polite match/selection/status feedback, and no focus theft during search.
- Keep details text wrapped and result lists bounded/scrollable without making the sidebar or actions unreachable.
- Search and navigation must never invoke a callback, access disk/network, mutate caller data, or persist state.
- Keep PHX-084 Permission Center security semantics and all ten PHX-041 evidence combinations unchanged.
- Preserve the PHX-081 chat, PHX-082 roster/status, PHX-083 task graph, and PHX-084 permission logic blocks byte-for-byte except strictly necessary view composition/type plumbing. Preserve the three original foundation test bodies unchanged. Do not weaken or rewrite historical assertions.

## Frontend admission and error semantics

- Use exact own data fields; reject unexpected string/symbol keys, accessors, malformed arrays, duplicate IDs, invalid types, out-of-bounds strings/numbers, and reflective failures. Do not coerce arbitrary values.
- Validate each complete snapshot before any record becomes visible. Never partially show valid rows from a rejected snapshot.
- Unavailable/degraded ports must not expose supplied rows. Connected means only “caller-supplied evidence,” not verified live connectivity.
- Do not auto-refresh, poll, use timers, fetch, invoke IPC, read storage, or make background calls.
- Keep generic safe failure wording; do not surface stack traces, exception text, private file paths, or record contents in diagnostics.
- Tests must prove untrusted content remains inert and no hidden transport/action is performed.

## Required deterministic validation gates

Work only on `feature/PHX-085-memory-files-workspace`, based on main `5594229ace471804e4571fc40bc159d4b020862e`. Fetch and verify branch, base, and working-tree state before edits. If main has moved or branch has unexpected commits/files, stop and report the exact mismatch rather than rebasing/resetting or widening scope.

Run and record the complete command, exit status, and relevant counts for every gate. A test gate is passing only when the exact command exits zero and expected assertions are present.

### Desktop/native gates

1. `npm ci --ignore-scripts`
2. `npm test` three consecutive final runs; PHX-084 baseline is 87 passing tests per run, so the new final count must be greater than 87 and identical across all three runs.
3. Desktop TypeScript typecheck using the existing repository script.
4. Vite production build using the existing repository script.
5. Existing Tauri info command.
6. `cargo check` for the existing Tauri crate.
7. `cargo test` for the existing Tauri crate; report zero Rust tests honestly if none exist.
8. Existing Tauri release build.

### Historical and UI security guards

- Exact PHX-055 guard: three consecutive passing runs.
- Exact PHX-052 guard: three consecutive passing runs.
- Exact PHX-051 guard: three consecutive passing runs.
- Full `tests/test_prompt_injection_defenses.py`: 604 baseline tests, three consecutive passes if the ticket's prescribed runner calls for repetition.
- Full `tests/test_screenshot_vision_loop.py`: 397 baseline tests.
- Full `tests/test_windows_ui_automation.py`: 493 baseline tests.
- Preserve all historical test files and assertion bodies. Do not change the frozen test baselines or reinterpret a failure as unrelated without exact evidence.

### Backend and Python regressions

Run the established repository focused regression commands for all 19 suites and preserve the current baseline counts:

- API: 8
- bootstrap: 11
- registry: 96
- router: 213
- ToolRegistry: 204
- PermissionEngine: 926
- AuditTrail: 302
- SecretStore: 402
- Spotify: 345
- YouTube: 358
- document generation: 284
- upload/parser: 336
- startup: 509
- conversation: 950
- TTS: 602
- STT: 414
- VAD: 403
- wake word: 537
- task graph/state machine: 348

Run the complete Python suite three consecutive times after the final product/test edit. Baseline per run: 15,721 passed, 16 existing skips, one existing warning. Compare skip identities/reasons and warning text exactly to the pre-change baseline. Do not treat host-terminated/incomplete runs as passes; do not modify tests merely to normalize a mismatch.

Run locked dependency sync, Ruff lint and format checks, mypy, wheel/sdist build outside the repository, 77-source package parity, direct-dependency metadata and Python requirement checks. Verify GET /health remains HTTP 200 with JSON status `ok`, service `phoenix-api`, and installed package version.

### Security and scope gates

- Review staged diff and run whitespace/error checks.
- Confirm exactly the seven authorized paths above changed.
- Scan changed files and staged diff for credentials, tokens, private content, accidental generated artifacts, path leakage and debug output.
- Individually review INV-001 through INV-024 with evidence. Explicitly preserve PermissionEngine as sole authorization authority and the existing PHX-084 boundaries.
- Confirm package manifests/locks, backend/native sources, Tauri CSP/capabilities and historical test files are unchanged.
- Confirm no network/HTTP, IPC, filesystem, persistence, upload, deletion, export, file-open or background behavior was added.
- Keep any build artifacts, receipts and temporary comparison files outside the repository.
- Do not claim GUI acceptance from SSR, DOM/static tests or builds. Manual native WebView2 acceptance remains PENDING.

## Completion and delivery protocol

Only after every required deterministic gate passes and the complete staged diff has been reviewed:

1. Add exactly one accepted ADR-072 to DECISIONS.md explaining the read-only memory/files presentation boundary and explicitly preserving all authority/transport limitations.
2. Update docs/21_FRONTEND.md with the actual implemented behavior and limitations.
3. Update FEATURE_TICKETS.md: PHX-085 DONE; PHX-086 and PHX-087 remain TODO; Phase 8 remains INCOMPLETE.
4. Update this ticket to DONE and record exact evidence, commands, counts, limitations and manual native acceptance still pending.
5. Re-run any gate affected by documentation/status changes as the repository workflow requires.
6. Review `git diff --check`, staged diff, exact seven-path allowlist, and clean final status.
7. Create exactly one completion commit with the exact message: `feat: complete PHX-085 memory files workspace`.
8. Push only `feature/PHX-085-memory-files-workspace` normally.

Expected delivery state: branch 0 behind / 2 ahead of the unchanged main SHA above, local and remote HEAD identical, clean working tree. If any gate fails, do not mark DONE, do not create a completion commit, and do not push a completion. Stop on a genuine hard blocker or after three identical failures, reporting exact evidence and the smallest safe next action.

## Forbidden actions

- No reset, clean, rebase, amend, force-push, merge, PR creation, main push, next-ticket work or unrelated cleanup.
- Do not run a gate in a way that mutates historical files; restore only your own proven changes and report evidence.
- Do not add dependencies or alter manifests/locks.
- Do not add backend routes, transport, IPC, file access, persistence, uploads, deletes, saves, exports or live status.
- Do not claim that caller-supplied records prove authoritative memory, live files, storage, indexing, permission, or execution.
- Do not start PHX-086.

## Required final report

Return a complete evidence-based report with:

1. Status: DONE or BLOCKED.
2. Repository path, exact feature branch, starting base SHA, expected unchanged main SHA, actual main SHA, completion SHA if any.
3. Exact changed paths and staged/final diff summary.
4. Implemented memory and file workspace behavior, schemas/bounds, accessibility and preservation evidence.
5. Every required command, exit status, test count and all three final npm/Python run results.
6. Regression/guard results and exact baseline comparisons.
7. Package/API, INV-001–024 security, dependency and artifact-scan evidence.
8. ADR/docs/ticket status changes.
9. Commit/push, branch ahead/behind, clean-tree evidence.
10. Explicitly unimplemented transport/actions and pending manual WebView2 acceptance.
11. Confirmation PHX-086 was NOT STARTED.

Do not report DONE unless every deterministic gate passes and the exact feature-only delivery state is proven.
