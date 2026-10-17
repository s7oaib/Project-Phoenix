# PHX-107 — Frontend port adapters

Status: IN_PROGRESS (implemented with PHX-106 for the 2026-10-21 review demo; recommendations R1–R11 await owner confirmation)
Phase: 10 — Product Integration (milestone M2, desktop)
Branch: `feature/PHX-106-107-desktop-connected` (local commits only; no push, no PR)
Base: `origin/main` `f8aaef0`

## Objective

Back the existing App ports with the live backend through the PHX-106 native proxy, so a user can chat, watch step-by-step progress, decide permissions and see the linked re-run's answer, without changing App.tsx and without any network code in the webview.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/INVARIANTS.md` (INV-003, INV-005, INV-011, INV-015, INV-019, INV-021), `/MASTER_SPEC.md`
- `docs/13_API_CONTRACTS.md`, `docs/14_EVENT_SYSTEM.md`, `docs/20_UI_UX_SPEC.md`, `docs/21_FRONTEND.md`
- ADR-068 to ADR-074, ADR-087, ADR-089, ADR-091

## Recommendations (R1–R11)

- **R1 — Layout.** `src/adapters/core.ts` (the store and mappers; no IPC), `src/adapters/tauri.ts` (the only importer of `@tauri-apps/api`, and only `invoke`, `listen` and `isTauri`), `src/adapters/ConnectedApp.tsx` (`useSyncExternalStore`) and `connected.css`. `main.tsx` renders `ConnectedApp` inside the native shell and the unconnected App in a plain browser. `@tauri-apps/api` is pinned to 2.12.1, matching the `tauri` crate.
- **R2 — App.tsx unchanged.** Every port is a fresh plain snapshot shaped exactly as App's readers admit it (tested with those readers). Memory, Files and Voice stay unconnected (no backend API yet; PHX-110/PHX-114).
- **R3 — REST is the source of truth.** Events only schedule a coalesced refetch (120 ms); backend `ready`, any stream (re)connection or close (including 1013) reloads settings, models, permissions, the tracked mission and the transcript. While the tracked mission is queued or running, a 3 s fallback poll covers dropped events. Nothing is fetched while the backend is not ready.
- **R4 — Chat.** `sendText` posts `POST /chat` and resolves only on 202; the conversation is kept, and at start the latest conversation and its latest mission are resumed. The transcript is the latest 64 entries of `GET /conversations/{id}/messages` (`phoenix` shown as assistant); an answer longer than 4,000 characters becomes ordered parts `<id>:1`, `<id>:2`, so nothing is omitted or clipped. Until PHX-105 triage, a "Mission path" selector sends no `path` (server default), `direct` or `complex` (planned steps that may read workspace documents).
- **R5 — Permissions.** `GET /permissions` pending requests become PermissionEntries: the actor is the agent from `permission.requested` (else `phoenix`), the reason names the mission and tool, and the evaluation is `approval_required` with the PHX-041 reason code for P1–P4 only, so a missing, P0 or P5 risk shows no decision controls. Approve sends `{"duration": "session"}` (`always` is not offered), Deny sends `{}`; any other status rejects, so App keeps the request retryable. After approval the store follows the linked re-run (`mission_id`, or `rerun_of` in events, or `rerun_id` on a superseded mission). A request whose field exceeds App's limits is counted in the connection bar, not shown.
- **R6 — Task Graph from content-free events.** Steps come from `step.*`, `agent.*`, `tool.*`, `permission.requested` and `verification.*` with their canonical owner and domain; the live status (running, calling a tool, waiting for permission, LEVI verifying, completed with duration, failed with code) is stated in the objective text, because objectives are not published. Dependencies are not published, so none are drawn; the timeout shown is the runtime default (300 s). The task state is the mission detail's last task state.
- **R7 — Agent Network.** `agent.started/completed/failed` and mission status set per-agent `working`, `waiting_permission` or `idle` with a short current task (mission prefix, role, step). Unknown agent IDs are dropped.
- **R8 — Models.** `GET /models`: provider, model, provider status and context limit.
- **R9 — Observability.** The latest 64 events as traces (kind by event family, failures as `failure`, summary of IDs/codes/timings only) and metrics for step latency, verification results and tool results; no logs are collected.
- **R10 — Minimal PHX-108: connection bar.** Above App: backend state with a plain explanation of each degraded code, live-stream state, the document workspace (root and status) with a folder field calling `PATCH /settings` (each `workspace_*` code explained, the path never echoed), the mission path selector, and the tracked mission's status with Cancel (queued/running, INV-019) and Run again (failed `interrupted`).
- **R11 — Known copy gap.** App's panel captions still say "caller-supplied … live transport is not implemented" and the header "backend integration pending"; App.tsx is deliberately unchanged here. Updating that copy (and its historical byte checks) is proposed for PHX-108.

## Verification

- `npm test` (192 tests, including 11 PHX-106/107 tests in `tests/desktop_adapters.mjs`: App readers admit every live port; `submitDecision` mapping, re-run tracking and refusals; `sendText` body and path; event/stream refetch coalescing and no fetch while down; status mapping; long-answer parts; risk-free requests have no controls; workspace errors; exact tauri commands/events; no credential, address or network primitive in webview code; connected SSR render without inline styles).
- `npm run typecheck`, frontend build, native release build and the PHX-106 smoke.

## Acceptance

- Adapters, mapping and guards: PASS (automated).
- Native end-to-end chat → progress → approval → re-run answer with live models: PENDING (main session demo).
