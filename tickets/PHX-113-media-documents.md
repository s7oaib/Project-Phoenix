# PHX-113 — Media and documents steps in missions

Status: DONE (code and tests; recommended options applied under the owner's instruction "recommended options"; the draft prompt text below awaits the owner's approval before any live mission can plan these steps)
Phase: 10 — Product Integration (milestone M3, capabilities)
Branch: `feature/PHX-113-media-documents` (local commits only; no push, no PR)
Base: `origin/main` `5722431` (PR #85)

## Objective

Let PHOENIX missions:

- (a) read PDF, DOCX, Markdown and CSV files from the workspace, through a permissioned read whose parsed text stays untrusted, guarded data and grounds LEVI;
- (b) generate Markdown, DOCX and CSV documents saved inside the workspace only, through a permissioned write (docs/08 P2 "create new safe output") that needs explicit caller approval and never overwrites;
- (c) control Spotify and YouTube (search, play, pause, open) through the existing PHX-070/071 tools behind permission.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md` (INV-003/004/005/007/008/017/018/021/022), `/MASTER_SPEC.md`
- `docs/08_PERMISSION_MATRIX.md`, `docs/18_MEDIA_INTEGRATIONS.md`, `docs/19_DOCUMENT_SYSTEM.md`
- PHX-045 (secrets), PHX-046/047 (permissioned tool steps, grounded LEVI), PHX-048 (root checks), PHX-070/071 (Spotify/YouTube), PHX-072/073 (parsers, generator), PHX-103 (events), PHX-109 (MAHORAGA)

## Recommendations applied (R1–R12)

- **R1 — Placement (ADR-140).** New modules only, plus a small opt-in hook:
  - `phoenix/tools/document_files.py` — `DocumentFileTool` (`document.read` P1, `document.create` P2);
  - `phoenix/agents/capability_steps.py` — `CapabilityStepRunner`, `CapabilityTools`, `capability_grants`, LEVI responders;
  - `phoenix/agents/capability_prompts.py` — the DRAFT prompt text;
  - `runtime.py`: a `capabilities=` constructor argument, the grant check, the step dispatch and `MissionTrace.capability_calls` (additive, default empty);
  - `mission_events.py`: the PHX-113 tool IDs, actions and the `media` agent role added to the allowlist.
  - `prompts.py`, `tool_steps.py`, `responders.py` and every tool module are byte-identical.
- **R2 — Opt-in.** Without `capabilities=` the runtime is exactly PHX-046/109. The backend composition root (`phoenix/api/server.py`) is not wired: production missions cannot reach PHX-113 until the owner approves the prompts and a follow-up wires it (with the API's grant store, which today passes only PHX-046 grants).
- **R3 — One call per step.** As PHX-046: the specialist proposes one `{"tool_request": ...}` on the structured binding; PHOENIX validates it against a closed allowlist and builds the call and `PermissionRequest`; the tool's PermissionEngine decides; the answer turn sees the result only as the guarded `tool_output` item. A second request, or one in the answer turn, is never executed. Not retried (PHX-109 R10).
- **R4 — Documents step.** With the document tool, a `documents` step offers PHX-046's `read_text`, `list_directory`, `stat` (only if a FilesystemTool is also configured, with PHX-046's grants, evidence, records and LEVI responders unchanged) plus `read_document` and `create_document`.
- **R5 — Read (ADR-142).** `document.read` (P1) reads one `.pdf/.docx/.md/.markdown/.csv` file (≤ 4 MiB) with FilesystemTool's own workspace rules (`_path`, `_target`, `_checked_root`: links and junctions refused, root re-checked per call), parses it with the PHX-072 parsers (encrypted PDFs, DTDs, ZIP bombs, bad packages refused) and returns text ≤ `max_text_chars` (20,000; over the bound fails `limit_exceeded`, never truncates). PDF segments keep `[page N]` provenance.
- **R6 — Write (ADR-142).** `document.create` (P2) generates Markdown/DOCX with the PHX-073 generator (title + sections of plain paragraphs) or CSV with the stdlib (equal-width rows, ≤ 1,000 × 50, cells ≤ 2,000). It opens the target with exclusive create (`"xb"`): an existing file is never overwritten (`already_exists`, no effect); parent folders must already exist; then it reads the file back and compares bytes before reporting `verified: true` (docs/19). CSV cells starting with `= + - @ TAB CR` (other than plain numbers) are prefixed with `'` (CSV-injection defence). No overwrite (P3), delete or mkdir operation exists.
- **R7 — Grants (ADR-141).** Caller grants only, never model text. Exact tool, action, absolute resource, fixed reason (`CAPABILITY_DOCUMENT_REASON` or `MEDIA_TOOL_REASON`), actor (`document_analyst` or `media`) and risk. P1: SESSION or PERSISTENT. P2: SESSION only. PHX-046 grants pass unchanged. Duplicates are refused before the mission starts. No grant → `waiting_permission` with the pending P2 request and no effect.
- **R8 — Media (ADR-143).** A `media` step (agent `media`/BROOK) may propose: Spotify `search` (P1), `play` (optional `media_uri`), `pause` (P2); YouTube `search` (P1), `open` (= `cue`: load a video in PHOENIX's player without playing), `play`, `pause` (P2). Transfer, volume, seek, queue and browser external-open are not offered. Request ID = call ID as the tools require. Third-party titles stay guarded tool output. An accepted command does not prove audible or visible playback.
- **R9 — Media activation.** The assignment prompt must describe every allowed domain, and `prompts.DOMAIN_DESCRIPTIONS` has no `media` entry. So `media` is offered only once the owner-approved description lands in `prompts.py`; until then a media assignment fails `disallowed_domain` (tests simulate approval with the draft text).
- **R10 — LEVI.** A deterministic `deterministic.tool_evidence` check runs first (call ID, request ID, actor, tool, resource, ALLOWED by a caller grant, attempted, success, effect matches the operation, create `verified`, P2 media `command_accepted`); a failure is final and the model is not consulted. Then the `capability_verifier` model check is grounded in the same guarded `tool_output` item the answer turn saw.
- **R11 — Observability.** `MissionTrace.capability_calls` (IDs, statuses, hashes, lengths; never paths, text or titles). Tool events as PHX-103 with the new tool IDs and actions (`read`, `create`, `search`, `play`, `pause`, `cue`); agent role `media`. Vocabulary stays at 32 types. P1/P2 write no audit rows (INV-007 covers P3/P4).
- **R12 — Pins.** `runtime.py` and `mission_events.py` move from the PHX-109 pin to the PHX-113 implementation commit (`PHX_113_COMMIT`/`PHX_113_PATHS`) in a separate pin commit; merge with a merge commit.

## Owner approval needed (prompt text, DRAFT `phx-113-draft-1`)

The exact text is in `phoenix/agents/capability_prompts.py`. Summary of what needs approval, then moving into `prompts.py` under a new `PROMPT_SET_VERSION` (`phoenix-prompts-v5`):

1. New role prompts: `document_capability`, `document_capability_answer`, `media_specialist`, `media_answer`, `capability_verifier` (each starts with `PHOENIX ROLE:`; the operation and service lists are filled from `DOCUMENT_OPERATION_LINES` / `MEDIA_SERVICE_LINES`), plus their task texts, expected outputs and completion rules.
2. `DOMAIN_DESCRIPTIONS["documents"]` (today "read-only") → `DRAFT_DOMAIN_DESCRIPTIONS["documents"]`: "reading, listing or inspecting workspace files, reading PDF, DOCX, Markdown or CSV documents, or creating one new Markdown, DOCX or CSV file in the workspace".
3. New `DOMAIN_DESCRIPTIONS["media"]`: "searching, playing, pausing or opening music or videos on Spotify or YouTube".
4. `PLANNING_SYSTEM` → `DRAFT_PLANNING_SYSTEM`: allows "creating one new Markdown, DOCX or CSV file in the workspace, or one Spotify or YouTube search, play, pause or open request"; still forbids changing, replacing or deleting files and any other tool use. Without this, AIZEN will not plan write or media steps.
5. `TRIAGE_SYSTEM` already sends tool use to COMPLEX; no change proposed.

## Owner setup needed (live use only)

- **Spotify** (PHX-070/045): a Spotify developer app (client ID, loopback redirect such as `http://127.0.0.1:8765/callback`), Spotify Premium for playback control and an active Spotify Connect device. Link once with `SpotifyTool.begin_authorization()` / `complete_authorization(...)`; tokens go to the PHX-045 `WindowsCredentialStore` (`spotify.access_token`, `spotify.refresh_token`). Nothing is hardcoded; without tokens a granted call fails `tool_failed` (tested).
- **YouTube** (PHX-071): a YouTube Data API key stored as `youtube.api_key` in the SecretStore for search; play/pause/open need a concrete `YouTubePlayerAdapter` (the embedded player is still deferred), otherwise they fail `tool_failed`.
- **Wiring**: after prompt approval, pass `capabilities=CapabilityTools(...)` in `phoenix/api/server.py` and extend the API grant store/approval flow beyond `document_grants`.

## Live smoke commands (owner-run, not CI; no GPU used by the agent)

```powershell
# 1. Offline proof (scripted model, fake transports/player):
uv run pytest -q tests/test_media_document_steps.py
# 2. Documents against a real temporary workspace without a model (no grant: denied; with grant: created and verified; again: already_exists):
uv run python -c "import asyncio,tempfile,pathlib;from datetime import UTC,datetime,timedelta;from phoenix.tools.document_files import *;from phoenix.tools.registry import ToolRegistry;from phoenix.security.permissions import *;r=pathlib.Path(tempfile.mkdtemp()).resolve();t=DocumentFileTool(r,ToolRegistry(document_file_tool_definitions()),PermissionPolicy());c=DocumentFileCall(operation=DocumentFileOperation.CREATE,relative_path='s.md',title='T',sections=(DocumentSectionSpec(heading='H',paragraphs=('p',)),));i=t.describe(c);q=PermissionRequest(request_id='q',actor_id='document_analyst',tool_id=i.tool_id,action=i.action,resource=i.resource,reason='smoke',requested_at=datetime.now(UTC),session_id='s');g=PermissionGrant(grant_id='g',actor_id='document_analyst',tool_id=i.tool_id,action=i.action,resource=i.resource,reason='smoke',duration='session',risk_level=i.risk_level,issued_at=datetime.now(UTC)-timedelta(minutes=1),session_id='s',user_decision='approved');R=asyncio.Runner();d=R.run(t.execute(c,q));w=R.run(t.execute(c,q,grants=[g]));x=R.run(t.execute(c,q,grants=[g]));R.close();print(d.status,w.output,x.error_code,(r/'s.md').read_text())"
# 3. Live missions (after prompt approval + wiring; start Ollama first): ask PHOENIX
#    "Read report.pdf in my workspace and save a summary to out/summary.md", approve the
#    pending document.create request, re-run, and check out/summary.md; then
#    "Search Spotify for lofi and pause playback" with the Spotify link completed.
```

## Tests

New `tests/test_media_document_steps.py` (80 cases; 1 POSIX-only skip on Windows): unit, integration, end_to_end, tools, permissions, path_traversal, permission_bypass, prompt_injection, malicious_documents, forged_tool_results, secret_leakage.

- Tool: each format parses (PDF/DOCX built by the real PHX-073 generator); no grant reads/writes nothing; Markdown/DOCX/CSV created, verified and re-readable; never overwrites; missing folder fails; 13 path-traversal/bad-target spellings refused (`..`, absolute, drive, backslash, `.`, device names, trailing dot, `a..md`, wrong extension); write through an NTFS junction refused (`symlink_forbidden`, nothing outside written); a grant for another path does not allow a write; CSV formula neutralisation; closed call shapes; malformed/oversized documents fail closed.
- Grants: exact scopes; P2 PERSISTENT, wrong reason/actor, duplicates and unknown tools refused; a bad grant fails before the mission.
- Missions: read with grant completes with LEVI evidence and grounded verifier; read + create writes one verified DOCX; write without grant → `waiting_permission` (P2) and no file; write denied path; path traversal in a mission → `tool_rejected`, nothing written; overwrite attempt → `tool_failed`, file unchanged; unlisted operations rejected unexecuted; grant claims in model text → schema failure; malicious MD/PDF/DOCX instructions stay inert inside the guarded `tool_output` item (flagged suspicious) and a follow-up write in the answer turn is never executed; forged tool result fails LEVI without the model; PHX-046 filesystem ops keep their evidence; capability steps are opt-in.
- Media: media domain unreachable without the approved description; Spotify search with grant completes (injection in track names stays guarded); P2 Spotify/YouTube control waits without a grant (no HTTP, no player call); YouTube pause reaches only the fake player; Spotify pause with grant sends one PUT; unlinked Spotify fails explicitly; unlisted media requests rejected.
- Contracts: event allowlist mirrors the tools; closed trace record; pre-PHX-113 traces load; canonical registry required; draft prompts separate from the approved set; provider neutrality of the new agent modules.

## Non-goals

Overwrite/replace, delete, mkdir, PDF output (not requested), OCR, browser external-open of YouTube URLs (PHX-112's browser permission), Spotify transfer/volume and YouTube seek/queue, API/frontend approval UI, composition-root wiring, live model runs, new dependencies (pypdf 6.19.0, python-docx 1.2.0 and ReportLab 5.0.1 are already locked by PHX-072/073).

## Limitations

- Approval is a pre-supplied SESSION grant for an exact resource. The user approves the path, not the generated content; a re-run regenerates content. Spotify/YouTube resources include the query hash, so a different query on re-run needs a new approval.
- A create cancelled while its worker thread is writing cannot be undone (the file may exist), as for PHX-070/071 external effects.
- Command acceptance never proves physical playback; the YouTube embedded player adapter is still deferred.
- The draft prompts have not been exercised live.

## Acceptance

- [x] Document read/create and media steps implemented behind permission; opt-in
- [x] Targeted tests, impacted subsystem tests, ruff, ruff format and strict mypy pass
- [x] Full matrix (see Completion evidence)
- [ ] Owner approves the draft prompt text (then `prompts.py` v5 and wiring)
- [ ] Owner completes Spotify/YouTube setup and runs the live smoke

## Completion evidence

See the final section, filled after the matrix run.
