# PHX-085 — Memory / Files Workspace

Status: DONE
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

## Completion evidence — 2026-10-09

PHX-085 replaces exactly Memory and Files / Documents with read-only, caller-owned
presentation ports. Default App has neither port and reports no source connected.
Connected means supplied evidence only, not a live backend or verified storage.
Unavailable/degraded sources hide all rows and stale details after full admission;
malformed snapshots are rejected whole with generic wording. No records are invented.

The user explicitly confirmed these exact DTO keys and caps. MemoryPort is readonly
{status, records}; FilePort is readonly {status, items}. Status is exactly connected,
unavailable or degraded. Neither port has callbacks. Memory records require id and
type (working, episodic, semantic, user, project, agent); optional keys are content,
sourceType, sourceRef, scope, confidence, importance, createdAt, updatedAt,
lastAccessedAt, expiresAt, embeddingRef. Files require id, kind, name; kind is exactly
source_document or generated_artifact. Optional keys are format, mediaType,
sourceType, sourceRef, contentHash, timestamp, sizeBytes. Raw objects, arbitrary
metadata, document content, binary bytes, actions and storage claims are rejected.

Both lists admit 0..64 unique opaque IDs. UTF16 bounds: IDs and embedding refs 128,
memory content 4000, source refs 512, scope/name 256, other text 128, UTC timestamps
64. Text must be meaningful; IDs and memory provenance/scope/embedding references
must already be trimmed, consistent with PHX-030. Content and file text formatting
are preserved. Confidence/importance are finite numbers 0..1; sizeBytes is a safe
nonnegative integer. UTC timestamps use valid calendar dates and Z or +00:00 only,
with no local clock or expiry inference. If creation and another memory timestamp
are both supplied, updated/accessed/expiry cannot precede creation; partial DTOs
never fabricate missing timestamps. Missing optional fields say Not reported;
numeric zero remains visible. These DTOs do not replace the Python domain schemas.

Admission uses exact own data descriptors and detached frozen primitive records,
arrays and snapshots. Unexpected string/symbol keys, missing fields, accessors,
sparse/extended arrays, duplicates, oversized/invalid values and reflection failures
reject the whole snapshot. Getters and arbitrary coercion are avoided. Same-realm
Proxy traps can execute: these checks are neither a sandbox nor authentication.
All external content, names, references and hashes render as escaped inert text,
with no HTML/Markdown execution, links, clipboard, logging or source dereferencing.

Case-insensitive local search covers all admitted strings and numeric evidence,
is capped at 80 UTF16 units, preserves source order, and provides match counts,
no-match feedback and Clear search. Native selection buttons choose only admitted
IDs. App-owned query/selection survives navigation, resets to first/none on source
loss or removed IDs, and explicitly labels selection outside the current filter.
Memory and Files state remains independent of each other and older workspaces.

Semantic headings, labelled inputs, native buttons, aria-pressed/aria-controls and
polite atomic counts/selection feedback preserve keyboard operation. Existing
visible focus, reduced motion, cinder/amber palette and system fonts remain. Wrapped
details and bounded scrollable lists stack at narrow widths; search does not steal
focus. All 14 navigation IDs, branding, skip link, landmarks, default/fallback,
focus wiring and PHX-081/082/083/084 logic are preserved. Eight other destinations
remain honest placeholders. The entire prior logic prefix and three original
foundation test bodies are checked byte for byte.

PUDDING owns memory, ROBIN document generation and VEGAPUNK indexing. Supplied
content/provenance does not prove authoritative memory, source truth, persistence
or retrieval. File metadata does not prove existence, readability, upload or
indexing; supplied hashes are not independently verified. Generated artifacts are
visibly distinct and may exist only in memory, never claimed saved to disk.
Existing PHX-031 secret-rejection policy remains the future trusted adapter's
responsibility; display validation cannot establish that supplied content is safe.
PermissionEngine remains the sole authorization authority; all ten PHX-041 evidence
combinations and PHX-084 intent semantics remain unchanged.

No live API/network/HTTP/WebSocket, Tauri IPC/plugin, filesystem/file-open, upload,
download/save/export/delete, memory mutation/forget, parsing/indexing/persistence,
polling/timers/background work, privileged action or new dependency was added.
Only GET /health exists; no memory/file route is added or called. Native sources,
configuration, CSP/capabilities, manifests/locks, backend and historical tests stay
unchanged. Ports need future trusted adapters; transport remains NOT IMPLEMENTED.
Manual native WebView2 acceptance remains PENDING: SSR/hooks/AST/build evidence
does not establish end-to-end GUI acceptance. Phase 8 is INCOMPLETE; PHX-086 and
PHX-087 remain TODO. PHX-086 was NOT STARTED.

Preparation verified: 134debe3e9ae6e9a01cf80430a6f268a05549926; unchanged main: 5594229ace471804e4571fc40bc159d4b020862e.
User explicitly authorized the clean branch switch from PHX-084 and confirmed
the DTO keys/caps before implementation. Exactly seven allowed paths changed.

Every required command below exited zero. Build overrides change only external
output paths, never runtime security. CARGO_TARGET_DIR points to the external
validation directory; Tauri --config merges only build.beforeBuildCommand and
build.frontendDist. No tracked build configuration was changed.

| Command | Exit |
| --- | --- |
| npm ci --ignore-scripts | 0; 28 packages, zero vulnerabilities |
| uv run --locked pytest tests/test_screenshot_vision_loop.py::test_no_action_persistence_or_dependency_change -q --junitxml=[external validation directory]\historical-screenshot_vision_loop-exact-1.xml | 0 |
| uv run --locked pytest tests/test_screenshot_vision_loop.py::test_no_action_persistence_or_dependency_change -q --junitxml=[external validation directory]\historical-screenshot_vision_loop-exact-2.xml | 0 |
| uv run --locked pytest tests/test_screenshot_vision_loop.py::test_no_action_persistence_or_dependency_change -q --junitxml=[external validation directory]\historical-screenshot_vision_loop-exact-3.xml | 0 |
| uv run --locked pytest tests/test_windows_ui_automation.py::test_source_architecture_and_immutability -q --junitxml=[external validation directory]\historical-windows_ui_automation-exact-1.xml | 0 |
| uv run --locked pytest tests/test_windows_ui_automation.py::test_source_architecture_and_immutability -q --junitxml=[external validation directory]\historical-windows_ui_automation-exact-2.xml | 0 |
| uv run --locked pytest tests/test_windows_ui_automation.py::test_source_architecture_and_immutability -q --junitxml=[external validation directory]\historical-windows_ui_automation-exact-3.xml | 0 |
| uv run --locked pytest tests/test_screenshot_vision_loop.py -q --junitxml=[external validation directory]\historical-screenshot_vision_loop.xml | 0 |
| uv run --locked pytest tests/test_windows_ui_automation.py -q --junitxml=[external validation directory]\historical-windows_ui_automation.xml | 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary -q --junitxml=[external validation directory]\guards-exact-1.xml | 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary -q --junitxml=[external validation directory]\guards-exact-2.xml | 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py::test_protected_existing_sources_dependencies_schema_and_event_vocabulary -q --junitxml=[external validation directory]\guards-exact-3.xml | 0 |
| uv run --locked pytest tests/test_prompt_injection_defenses.py -q --junitxml=[external validation directory]\guards-suite-1.xml | 0 |
| uv run --locked pytest tests/test_api.py -q --junitxml=[external validation directory]\regressions-api.xml | 0 |
| uv run --locked pytest tests/test_bootstrap.py -q --junitxml=[external validation directory]\regressions-bootstrap.xml | 0 |
| uv run --locked pytest tests/test_agent_registry.py -q --junitxml=[external validation directory]\regressions-agent_registry.xml | 0 |
| uv run --locked pytest tests/test_agent_model_router.py -q --junitxml=[external validation directory]\regressions-agent_model_router.xml | 0 |
| uv run --locked pytest tests/test_tool_registry.py -q --junitxml=[external validation directory]\regressions-tool_registry.xml | 0 |
| uv run --locked pytest tests/test_permission_engine.py -q --junitxml=[external validation directory]\regressions-permission_engine.xml | 0 |
| uv run --locked pytest tests/test_audit_trail.py -q --junitxml=[external validation directory]\regressions-audit_trail.xml | 0 |
| uv run --locked pytest tests/test_secrets_boundary.py -q --junitxml=[external validation directory]\regressions-secrets_boundary.xml | 0 |
| uv run --locked pytest tests/test_spotify_integration.py -q --junitxml=[external validation directory]\regressions-spotify_integration.xml | 0 |
| uv run --locked pytest tests/test_youtube_integration.py -q --junitxml=[external validation directory]\regressions-youtube_integration.xml | 0 |
| uv run --locked pytest tests/test_document_generation.py -q --junitxml=[external validation directory]\regressions-document_generation.xml | 0 |
| uv run --locked pytest tests/test_upload_parser_routing.py -q --junitxml=[external validation directory]\regressions-upload_parser_routing.xml | 0 |
| uv run --locked pytest tests/test_rise_phoenix_startup.py -q --junitxml=[external validation directory]\regressions-rise_phoenix_startup.xml | 0 |
| uv run --locked pytest tests/test_voice_conversation.py -q --junitxml=[external validation directory]\regressions-voice_conversation.xml | 0 |
| uv run --locked pytest tests/test_tts_provider.py -q --junitxml=[external validation directory]\regressions-tts_provider.xml | 0 |
| uv run --locked pytest tests/test_stt_provider.py -q --junitxml=[external validation directory]\regressions-stt_provider.xml | 0 |
| uv run --locked pytest tests/test_vad.py -q --junitxml=[external validation directory]\regressions-vad.xml | 0 |
| uv run --locked pytest tests/test_wake_word_provider.py -q --junitxml=[external validation directory]\regressions-wake_word_provider.xml | 0 |
| uv run --locked pytest tests/test_task_graph_state_machine.py -q --junitxml=[external validation directory]\regressions-task_graph_state_machine.xml | 0 |
| uv run --locked pytest --junitxml=[external validation directory]\full-run-1.xml | 0 |
| uv run --locked pytest --junitxml=[external validation directory]\full-run-2.xml | 0 |
| uv run --locked pytest --junitxml=[external validation directory]\full-run-3.xml | 0 |
| uv sync --locked | 0 |
| uv run --locked ruff check . | 0 |
| uv run --locked ruff format --check . | 0 |
| uv run --locked mypy | 0 |
| uv build --out-dir [external validation directory]\dist | 0 |
| npm.cmd test | 0 |
| npm.cmd test | 0 |
| npm.cmd test | 0 |
| npm.cmd run typecheck --workspace @phoenix/desktop | 0 |
| npm.cmd run build --workspace @phoenix/desktop -- --outDir [external validation directory]\frontend-dist | 0 |
| npm.cmd run tauri:info --workspace @phoenix/desktop | 0 |
| cargo check --manifest-path apps/desktop/src-tauri/Cargo.toml | 0 |
| cargo test --manifest-path apps/desktop/src-tauri/Cargo.toml | 0 |
| npm.cmd run tauri:build --workspace @phoenix/desktop -- --config [external validation directory]\tauri-build-paths.json | 0 |
| uv run --locked pytest tests/test_memory_schema.py -q --junitxml=[external validation directory]\impacted-memory_schema.xml | 0 |
| uv run --locked pytest tests/test_pudding_memory_service.py -q --junitxml=[external validation directory]\impacted-pudding_memory_service.xml | 0 |
| uv run --locked pytest tests/test_document_ingestion.py -q --junitxml=[external validation directory]\impacted-document_ingestion.xml | 0 |
| uv run --locked pytest tests/test_vector_store_sqlite.py -q --junitxml=[external validation directory]\impacted-vector_store_sqlite.xml | 0 |
| uv run --locked pytest tests/test_retrieval_context.py -q --junitxml=[external validation directory]\impacted-retrieval_context.xml | 0 |
| uv run --locked pytest tests/test_provenance_citations.py -q --junitxml=[external validation directory]\impacted-provenance_citations.xml | 0 |
| uv run --locked pytest tests/test_filesystem_tool.py -q --junitxml=[external validation directory]\impacted-filesystem_tool.xml | 0 |

Three final npm runs: 115 passed, zero failed/skipped each (87 prior + 28 new).
TypeScript, Vite, Tauri info, Cargo check/test and Tauri release build passed.
Cargo test contains zero Rust tests; it is build/link evidence only. All three
historical exact guards passed three times each. Full prompt/screenshot/Windows
security suites passed 604/397/493, with historical files/assertions unchanged.

Focused baseline counts: API 8; bootstrap 11; registry 96; router 213;
ToolRegistry 204; PermissionEngine 926; AuditTrail 302; SecretStore 402;
Spotify 345; YouTube 358; document generation 284; upload/parser 336;
startup 509; conversation 950; TTS 602; STT 414; VAD 403; wake 537;
task graph/state machine 348. All 19 individual invocations passed.

Three complete final Python runs: 15,721 passed each, with exactly the same
16 existing skip identities/reasons and same existing Starlette/httpx warning
as accepted PHX-084 baseline. No new skips, filters, retries or warning suppression.
The hidden external one-shot runner was launched through the Windows process
service, independent of tool-host lifetime, with no OS-setting/permission changes.
No interrupted run was counted as a pass.

Locked sync passed, Ruff lint passed, format check covered 133 unchanged Python
files, and mypy passed 127 source files. Wheel/sdist built outside the repository;
all 77 Python sources match working bytes and Git-normalized main, direct runtime
dependencies equal main and Requires-Python remains <3.12,>=3.11. API eight tests
prove exact repeated GET /health HTTP 200/application-json, status ok,
service phoenix-api, installed package version, unknown route 404 and factory
isolation. No new endpoints.

Additional impacted subsystem suites passed:

- memory_schema: 244 passed, exit 0.
- pudding_memory_service: 167 passed, exit 0.
- document_ingestion: 300 passed, exit 0.
- vector_store_sqlite: 273 passed, exit 0.
- retrieval_context: 367 passed, exit 0.
- provenance_citations: 173 passed, exit 0.
- filesystem_tool: 649 passed, 16 existing skips (exact baseline identities), exit 0.

Bounded repair: initial npm test exited 1 (113 passed, two failed). Both new
ID-bound tests supplied undefined through a default parameter, substituting a
valid fixture ID. Explicit id override corrected the fixture while preserving
undefined rejection. No production validation or historical assertion was weakened.
All three complete final npm/Python sequences follow the last product/test edit.

Complete implementation diff was staged/reviewed after all deterministic gates
and before these four completion docs/status edits. Added-line credential scans,
private-path/debug/action/artifact review and exact scope checks passed. Backend,
native/CSP/capabilities, manifests/locks, historical tests and earlier tickets are
unchanged; original foundation bodies and prior workspace logic byte-equal.

| Invariant | Individual evidence |
| --- | --- |
| INV-001 | Exact 30-entry frontend/document/Python ordered parity and sole phoenix Supreme orchestrator checked by npm; full orchestrator tests pass, production unchanged. |
| INV-002 | strategist/AIZEN retains Chief strategist/master planner metadata and subordinate designation; no executable authority; registry 96/router 213 and full planner tests pass. |
| INV-003 | No LLM output, request reason or display status grants authority; no direct PermissionEngine call or locally created decision. Explicit native button callback delivers intent only; PermissionEngine 926 unchanged and passed. |
| INV-004 | App has no tool/IPC imports; ToolRegistry 204 and PermissionEngine 926 passed. |
| INV-005 | P4 review requires an admitted exact P4/p4_explicit_approval_required evaluation and explicit native click. Callback sends only a request ID and human intent; trusted identity correlation and authorization remain PermissionEngine 926, unchanged. |
| INV-006 | P5/p5_blocked is the only admitted P5 evaluation; no Approve/Deny controls or callback eligibility. Exhaustive 1,920 combination tests and unchanged PermissionEngine 926/SecretStore 402 pass. |
| INV-007 | AuditTrail 302 passed; audit/persistence source unchanged; no UI privileged actions. |
| INV-008 | All 48 required and seven additional impacted commands plus npm ci exit zero before DONE. Initial ID-fixture default-parameter mistake repaired without product or historical assertion weakening; three final npm/full sequences follow last test edit. Live memory/file integration and manual native acceptance remain pending. |
| INV-009 | Recovery source unchanged; test_mahoraga_recovery.py::test_explicit_positive_budgets and ::test_bounded_replan_membership_failed_step_and_order are included in all three full pytest runs. |
| INV-010 | No recovery mode controls; test_kaido_failsafe.py::test_decision_is_degraded_only is included in all three full pytest runs; source unchanged. |
| INV-011 | Exact npm AST call allowlist permits React/local helpers and one supplied sendText call only inside sendDraft and one supplied submitDecision call only inside deliverPermissionIntent; provider/network/native/media/storage APIs banned; CSP connect-src none and empty capabilities unchanged. |
| INV-012 | No provider or agent production edits; registry 96 and router 213 passed. |
| INV-013 | No tool/policy production edits; ToolRegistry 204 passed, inert permission/audit metadata protected. |
| INV-014 | Only exact canonical IDs select agent details/status; permission IDs remain opaque exact request IDs, never grant authority; every alias lookup rejected by npm; Python alias-removal/collision registry tests pass; 14 routes unchanged. |
| INV-015 | Native controlled local textarea remains editable while disconnected; named form Send and all 14 nav buttons work without a microphone; disabled voice explained. |
| INV-016 | No voice initialization in shell; startup 509, conversation 950, TTS 602, STT 414, VAD 403, wake 537 passed. |
| INV-017 | PHX-085 exact whole-snapshot own-descriptor admission, 64 unique records per port, six memory classes/two file kinds, confirmed primitive/UTC bounds, safe size integers, detached frozen evidence, generic failures and React escaping verified by 28 new tests. Content/source references remain inert; no links, bytes, metadata or instructions executed. Proxy reflection is not a sandbox. Snapshot roles limited to user/assistant, exact data fields, count 64, content 4000, ID 128, unique IDs; malformed/oversize entries omitted with notice, complete accepted text React-escaped; no parsing or external resources. Task whole-graph admission checks 1..40 steps, <=120 edges, exact known owners, 11 canonical task states, strict text/array/timeout bounds, no duplicate/unknown/self dependencies or cycles, source-priority traversal, inert JSX and generic errors. Agent snapshots admit at most 30 unique known IDs, 4 closed states, own data fields only, task<=240/provider<=120 UTF16; invalid whole snapshot rejected with generic notice; JSX escaping tested. Permission snapshots enforce <=64 unique requests, exact own data fields/correlation, UTF16 bounds/strict UTC, ten exact evidence combinations, generic reflection failure, detached frozen primitives and inert JSX; model-like approval text never changes eligibility. Prompt 604, upload 336 passed. |
| INV-018 | No PHX-085 storage, logging, clipboard, callbacks, transport or admission policy. Display validation does not guarantee private data contains no secrets; trusted future adapters must enforce existing PHX-031 mandatory secret rejection. SecretStore 402 and impacted memory service tests pass; memory/security production unchanged. |
| INV-019 | Caller-triggered one-flight send exposes pending state and Cancel waiting. Local cancellation retains draft even after late ack, explicitly cannot abort delivery, and duplicates remain blocked until adapter settles; no timer/polling/hidden worker. Permission intents expose global pending state, prevent all duplicates until settlement, retain pending across navigation/source loss and explain that UI cannot cancel adapter delivery; no cancellation feature promised. Hook/helper tests pass. |
| INV-020 | No migration changes; historical guard preserves sole Alembic head 0005_audit_events and EventType 32, exact test x3 passed. |
| INV-021 | Only connected supplied Promise<void> ack can clear matching draft; synchronous/thenable/malformed result and exceptions fail safely, retain draft, never append transcript; Permission intent requires native Promise resolving undefined; sync/thenables/malformed acknowledgements/throws/rejections fail safely with deliberate retry and unchanged evaluation. ACK blocks repeat ID until valid evidence changes away or ID disappears; never reports authorization. All provider/tool regressions pass. |
| INV-022 | Memory/file absent, empty, malformed, unavailable and degraded states are explicit, hidden records suppressed after full validation; selection resets on loss, persists on navigation, off-filter selected evidence labelled. Missing optional fields show Not reported; zero preserved. Unknown view falls back Home; unknown/alias agent selection retains last valid identity, no identity invention; missing evidence Status not reported, disconnected/degraded no active claims; off-list selection explicitly labelled; task selection resets across task IDs/missing steps/disconnect, default no graph; Permission default source disconnected; unavailable/degraded/malformed evidence suppresses stale details, missing evaluation never infers risk/status/actions, removed selection resets to first/none and off-filter notice explicit. Chat/voice fallback preserved. |
| INV-023 | No model generation path; phoenix/models/lifecycle.py default capacity one remains unchanged; test_model_lifecycle.py::test_global_capacity_blocks_different_providers_and_models and invalid/positive capacity cases pass in all three full suites. |
| INV-024 | All npm/Python manifests and locks unchanged; existing-dependency UI only; package dependency metadata equals main. |

Receipts, logs, JSON/XML, build outputs, wheel/sdist and full staged-diff review are
outside the repository at C:/Users/shoai/.codex/validation/PHX-085-20261009.
Final scope/delivery receipts will capture the exact completion commit, normal
feature-only push, matching local/remote HEAD, 0 behind / 2 ahead of unchanged main
and a clean tree before the final report. Completion SHA is reported after commit rather than embedded
in itself. Frozen ticket body preserved except Status and this appended receipt.

LIVE MEMORY/FILE TRANSPORT: NOT IMPLEMENTED.
MEMORY MUTATION/DELETE/FORGET: NOT IMPLEMENTED.
FILE OPEN/READ/UPLOAD/DOWNLOAD/SAVE/EXPORT/DELETE/PARSE/INDEX: NOT IMPLEMENTED.
MANUAL NATIVE WEBVIEW2 ACCEPTANCE: PENDING.
PHASE 8: INCOMPLETE. PHX-086/087: TODO. PHX-086: NOT STARTED.
