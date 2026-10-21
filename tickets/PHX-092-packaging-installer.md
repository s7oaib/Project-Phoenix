# PHX-092 — Packaging and installer

## Status
DONE (owner decisions of 2026-10-21 applied; local commits only, no push, no PR)

Phase: 9 — Validation & Release
Branch: `feature/PHX-092-packaging-installer`
Base: `origin/main` `5722431` (PR #85)

## Objective

Ship PHOENIX as a Windows installer that runs on a machine without the repository:
the Tauri shell plus a self-contained Python backend, installed per user, with
first-run prerequisite checks exposed by the backend and a clean uninstall.

## Authoritative documents
- `/AGENTS.md`, `/RULES.md`, `/MENTAL_MODEL.md`, `/INVARIANTS.md`, `/MASTER_SPEC.md`
- `docs/06_HARDWARE_PROFILE.md` (preferred `D:\Phoenix\`), `docs/13_API_CONTRACTS.md`,
  `docs/21_FRONTEND.md`, `docs/25_DEPENDENCIES.md`
- ADR-091 (desktop sidecar), PHX-106; new ADR-125 to ADR-129

## Owner decisions (final, 2026-10-21)

NSIS installer through the Tauri bundler; backend = official embeddable Python 3.11
plus the locked wheels from `uv.lock`, launched by the Rust shell as the sidecar;
unsigned (SmartScreen documented); data under the user's chosen or default location
(follow the docs; record an ADR); first-run prerequisite checks (Ollama present and
running, models present) exposed as backend readiness, UI owned by PHX-108; clean
uninstall that keeps user data unless the user opts in; a release-build CI job on
`windows-latest` uploading the installer. Embeddable Python download pre-approved
(sha256 verified). Wheel bundling must be generic so later native dependencies are
picked up from `uv.lock` automatically. ADR numbers 125–129.

## Implementation

- **Build script** `scripts/build_installer.py` (ADR-125): verified, cached
  python.org `python-3.11.9-embed-amd64.zip` (SHA-256
  `009d6bf7…cfd3b`, MD5 matches python.org); `python311._pth` adds
  `Lib\site-packages`, `..\app` and `import site`; `uv export --frozen --no-dev
  --no-emit-project` → `uv pip install --target … --python-platform
  x86_64-pc-windows-msvc --python-version 3.11 --only-binary :all: --no-deps
  --require-hashes --no-config`; `app/` = PHOENIX's own wheel contents plus tracked
  `alembic/` and `alembic.ini`; smoke import under `-I`; `runtime-manifest.json`;
  then `tauri build --bundles nsis --config packaging/windows/tauri.installer.conf.json`.
  `--stage-only` stops after staging.
- **Bundle config** `packaging/windows/tauri.installer.conf.json` (ADR-128): merged
  only for the installer build: NSIS, `installMode: currentUser`, the staged runtime
  mapped to `backend/`, the hooks file. `tauri.conf.json`, its capability and CSP, and
  the exe-only `tauri:build` script are unchanged.
- **Uninstall hook** `packaging/windows/installer-hooks.nsh` (ADR-128): removes
  `$INSTDIR\backend` (run-time bytecode caches; also on update) and the empty folder;
  removes the default `%USERPROFILE%\.phoenix\data` only when the user ticks "Delete
  the application data" (never silent or update).
- **Shell** `apps/desktop/src-tauri/src/backend.rs` (ADR-125, ADR-126):
  `LaunchConfig::resolve` → run-time `PHOENIX_BACKEND_ROOT` override; else installed
  mode when `<exe dir>/backend` exists (`backend/app`, `backend/python/python.exe`,
  started with `-I`; an incomplete install is `backend_not_found` /
  `python_not_found`, never a checkout fallback); else PHX-106 dev mode. Data:
  `PHOENIX_DATA_DIR`, else `~/.phoenix/data`.
- **Readiness** `phoenix/api/server.py` (ADR-127): `GET /ready` adds
  `prerequisites: {ollama: running | not_running | not_found, missing_models: [...]}`;
  `null` before services exist. No path or URL is echoed; nothing is installed or pulled.
- **CI** `.github/workflows/release-installer.yml` (ADR-129): `windows-latest`,
  dispatch / main / `v*` tags / relevant PRs; uploads `phoenix-installer-windows-x64`
  (installer + runtime manifest). `contents: read`, no secrets, no release.
- **Data location** (ADR-126): `D:\Phoenix\` in docs/06 is a BLAZE-6 preference, so
  the default stays `~/.phoenix/data`; the user chooses another location with
  `PHOENIX_DATA_DIR` (BLAZE-6: `D:\Phoenix\data`) and may install the program to any
  folder (for example `D:\Phoenix\app`).

## Guards

- `backend.rs` (previously guarded at `origin/main`) and `phoenix/api/server.py`
  (previously pinned at PHX-104) move to `PHX_092_PATHS` in
  `tests/test_prompt_injection_defenses.py`, pinned at the implementation commit
  `43c9c398c5c0cbc5cae2bcd6ced92df977027fc9` in a separate pin commit (PHX-088/106
  precedent). After merging PHX-108 (which also changed `server.py`; ADR-110/113),
  `server.py` left `PHX_108_PATHS` and both PHX-092 paths were re-pinned to the merge
  resolution commit `9d50200e39cee06cd045371149731c39b49a9909`; the `/ready`
  prerequisites now reuse PHX-108's `onboarding.runtime_installed`. The PR must be merged with a merge
  commit so the pinned SHA stays reachable. The two `git diff origin/main` guards
  already exclude both files by name; no exclusion was added or removed.
- `tests/workspace.test.mjs`: the installer config only bundles (exact object), the
  app's bundle stays inactive, `tauri:build` stays exe-only, the shell's bundled
  directory and `-I` isolation. The PHX-106 capability/CSP test and byte pins are
  unchanged (`tauri.conf.json`, `Cargo.toml`, `Cargo.lock`, `package.json` untouched).
- `tests/matrix.toml`: `tests/test_build_installer.py` (unit, path_traversal) and
  `scripts/build_installer.py` in the strict `mypy-scripts` gate.

## Verification

- `cargo test` (20: +4 resolution tests — installed mode, incomplete install without
  fallback, overrides, dev checkout / missing home — and `-I` in the launch arguments),
  `cargo clippy --all-targets`, `cargo fmt --check`.
- `pytest tests/test_build_installer.py` (18, fakes only: digest pin and cache,
  mismatch leaves nothing, `_pth`, unsafe zip members, missing `_pth`, generic pins
  incl. a future native wheel, exact uv flags, layout and manifest, smoke rejections,
  NSIS command, installer config, uninstall hook, ignored outputs, non-Windows exit).
- `pytest tests/test_api_server.py` (+4: exact `/ready` with prerequisites,
  `not_running` / `not_found`, degraded counts as running, executable detection).
- Full matrix `uv run python scripts/test_matrix.py`: see completion evidence.

## Completion evidence (2026-10-21, BLAZE-6)

- Build: `uv run --locked python scripts/build_installer.py` → 38 locked packages
  staged; smoke `{"phoenix": "0.1.0", "python": "3.11.9", "user_site": false,
  "isolated": true}`; `PHOENIX_0.1.0_x64-setup.exe`, 59,103,295 bytes (56.4 MiB),
  SHA-256 `bec587235f604c552580615f083f4542da6a6db5abac1cb47de1b0b3cf149655`
  (local build; not committed). The first bundle run lets the Tauri CLI fetch its NSIS
  toolchain and `nsis_tauri_utils.dll` (hash-validated by the CLI).
- Install: `setup.exe /S /D=%TEMP%\phx092-install\app` → exit 0; `phoenix-desktop.exe`,
  `uninstall.exe`, `backend\` (3,668 files, 221.9 MB); HKCU uninstall entry
  `PHOENIX 0.1.0`; Start-menu and desktop shortcuts.
- Launch with `PHOENIX_DATA_DIR=%TEMP%\phx092-install\data` (not `~/.phoenix/data`):
  the shell started `backend\python\python.exe -I -m phoenix.api --host 127.0.0.1
  --port 0 --data-dir …\data` as its child; migrations 0001→0008 ran in the temp data
  directory; the backend listened on `127.0.0.1` only; "Application startup complete";
  the shell's event bridge connected (`WebSocket /events [accepted]`), which happens
  only after the shell's state is `ready`. No mission was run; the only Ollama calls
  were the startup/readiness probes (`/api/version`, `/api/show`), no generation.
- Close: closing the window ended the app and the backend process.
- Uninstall: `uninstall.exe /S` → exit 0; install folder (including `backend\`),
  uninstall entry and both shortcuts removed; the temp data directory kept
  (`phoenix.db`, log, locks). Tauri kept `HKCU\Software\PHOENIX\PHOENIX` (remembered
  install folder; removed only with the opt-in box), which the test then deleted
  with the temp folders.

- Matrix on the clean pin commit `b1283fc`: `uv run python scripts/test_matrix.py` →
  PASS, all 13 gates (pytest 16,367 passed / 30 skipped; npm 196 passed; cargo check and test,
  typecheck, frontend build, tauri info, uv build). The first run
  failed once in `tests/test_fyp_multi_agent.py::test_output_schema_privacy_and_metric_coverage`,
  a pre-existing random flake unrelated to PHX-092 (a 4-digit scripted answer, `2397`,
  appeared inside a random UUID in the results file); it passed 3/3 alone and the
  whole matrix passed on rerun.

## Acceptance

- Installer builds locally and in CI: PASS locally; CI job added (runs after push).
- Install → launch → backend ready → uninstall on a per-user temp location: PASS.
- Prerequisites exposed by `/ready`: PASS (tests); UI is PHX-108.
- User data kept by default on uninstall: PASS (silent uninstall).

## Limitations

- Unsigned: SmartScreen warns on first run until a certificate is added.
- Windows x64 only; the build script refuses other platforms.
- The opt-in data removal deletes only the default data directory; a custom
  `PHOENIX_DATA_DIR` must be removed by hand. The interactive uninstaller page (opt-in
  box) was not exercised; only silent mode was run.
- No automatic update channel; installing a newer version over an older one uses
  Tauri's update path (old uninstaller with `/UPDATE`, `backend\` replaced).
- The CI job has not run yet (no push from this ticket).
