# 25 — Dependency Classification

## Required V1 runtime/platform

### Ollama
Default local model runtime.

### Tauri / React / FastAPI / SQLite ecosystem
Core application platform.

### Playwright (Phase 5)

Required Python runtime dependency for PHX-050 direct DOM/browser control:
`playwright>=1,<2`, locked to **1.63.0** with its required **greenlet 3.5.6** and
**pyee 13.0.1** transitive dependencies. Promotion is accepted in ADR-051.

PHX-050 uses synchronous, isolated headless Chromium behind ToolRegistry,
PermissionEngine and P4 audit. Chromium binaries are a separate operational
prerequisite; import, construction, tests, package installation and application
startup never install/download them. Explicit authorized session opening returns
safe unavailable-dependency evidence if the executable is missing. Deterministic
tests use a private fake runtime and require neither browser binaries nor public
internet. No existing user profile, cookies, credentials or persistent context
is imported. See https://playwright.dev/python/docs/browsers.

### pywinauto (Phase 5, Windows only)

PHX-051 authorizes `pywinauto>=0.6.9,<0.7; sys_platform == "win32"`, locked
to **0.6.9** with **pywin32 312**, **comtypes 1.4.17** and **six 1.17.0**.
Promotion is accepted in ADR-052.
Only the UIA backend is used, behind ToolRegistry, PermissionEngine and durable
P4 audit. Imports and desktop access occur only during explicit allowed execution;
non-Windows execution returns safe unavailable-dependency evidence.

Native accessibility calls require a Windows desktop and applications exposing
the relevant UIA patterns. Deterministic tests use a private fake and never access
the real desktop. Native COM calls are synchronous and non-preemptible; no hard
process-wide timeout is claimed. Descendant admission is capped at 500, with
overflow failing before interaction; native FindAll may materialize the tree
before that admission check. No physical input or process-launch fallback exists.
No Pillow, screenshot/OCR/vision, hooks or alternate automation framework is added.
See https://pywinauto.readthedocs.io/en/latest/code/pywinauto.uia_element_info.html.

### Document parsers (Phase 7)

ADR-065 promotes exactly `pypdf>=6.19,<7` (locked **6.19.0**) and
`python-docx>=1.2,<2` (locked **1.2.0**) as required Python runtime dependencies
for PHX-072 in-memory text-layer PDF and top-level DOCX paragraph/table parsing.
**lxml 6.1.3** is the only new transitive identity, required by python-docx;
typing-extensions was already compatible. lxml is not a Phoenix direct requirement.
All 42 existing non-Phoenix lock records/versions/hashes remain exact; the Phoenix
record changes only for those two direct references. No extras/OCR package added.

Readers receive BytesIO, never a filename/path. Source/page/stream/text/segment and
ZIP entry/member/total/ratio bounds are caller supplied. DOCX original archive names
are checked before host normalization, and bounded XML rejects DTD/entities before
python-docx. No extraction, external entity resolution or native OCR executable.
Public pypdf decoder limits are scoped to synchronous calls, external JBIG2 decoder
disabled, diagnostics redacted and configuration restored. Third-party synchronous
parsing is not hard-preemptible and cannot guarantee a strict process-memory ceiling.
pypdf is not OCR; image-only PDFs fail explicitly. DOCX does not claim full Word
semantic fidelity. Images require an explicit borrowed adapter; none is instantiated.
See https://pypdf.readthedocs.io/en/stable/user/extract-text.html and
https://python-docx.readthedocs.io/en/latest/api/document.html.

### Document generation (Phase 7)

ADR-066 promotes exactly `reportlab>=5.0.1,<6`, locked **5.0.1**, for PHX-073
in-memory Platypus PDF rendering. **Pillow 12.3.0** and **charset-normalizer 3.5.2**
are the only new transitive package identities; neither is a Phoenix direct
requirement. All 45 previous non-Phoenix records/versions/hashes remain exact.
Python remains >=3.11,<3.12. No extras, types-reportlab or fourth package.

Reuse python-docx **1.2.0** and pypdf **6.19.0** for DOCX generation and structural
verification. Markdown uses stdlib only. Base-14 PDF fonts require exact cp1252
rendered text; no font/template/image file lookup, download, OCR or native program.
DOCX uses an in-memory code-owned OOXML seed and ordinary BytesIO serialization;
post-save output limits bound accepted artifacts, not streaming process memory.
Native rendering/ZIP/extraction remain synchronous and cannot be hard-preempted.
Pillow is installed for ReportLab's declared dependency, not promoted to an OCR,
vision or image-generation feature. Strict mypy has only a narrowly scoped external
`reportlab`/`reportlab.*` ignore_missing_imports override; no local typing bypass.

### WebSockets (Phase 10)

ADR-088 promotes `websockets>=17.2,<18`, locked **17.2** (BSD-3-Clause), as a
required Python runtime dependency for PHX-103 `WS /events`. It is uvicorn's
WebSocket backend: with it installed, uvicorn's automatic selection uses its
sans-I/O implementation. It has no transitive packages; every other lock record
is unchanged. It is a direct requirement, not the `uvicorn[standard]` extra, and
tests use its synchronous client against a real loopback server.
See https://websockets.readthedocs.io/.

### Desktop shell HTTP / WebSocket clients (Phase 10)

Owner-approved on 2026-10-18 (PHX-106, ADR-091) as required Rust dependencies of
the Tauri shell, which proxies the webview's traffic to the loopback backend and
holds the per-launch token:

- `ureq` **3.4.2** (MIT OR Apache-2.0), with `ureq-proto` 0.6.5: the blocking
  HTTP/1.1 client behind the `api_request` command. Default features are off,
  so it is TLS-free (no rustls, webpki roots or gzip); the environment proxy is
  explicitly disabled and redirects are off.
- `tungstenite` **0.28.0** (MIT OR Apache-2.0), `handshake` feature only: the
  WebSocket client for `WS /events`. TLS-free (no `native-tls` or `rustls`
  features); it pulls `sha1`, `data-encoding`, `rand` 0.9 and `utf-8`.

Both connect only to `127.0.0.1`. `base64`, `getrandom`, `serde`, `serde_json`
and `windows-sys` were already locked through Tauri and are now direct. The
webview gains `@tauri-apps/api` **2.12.1** (Apache-2.0 OR MIT), matching the
`tauri` crate, for `invoke` and `listen` only.

### Local voice (Phase 10)

Owner-approved on 2026-10-21 (PHX-114) as required Python runtime
dependencies. Everything runs offline on the CPU.

- **faster-whisper.** `faster-whisper>=1.2.1,<2`, locked **1.2.1** (MIT;
  ADR-105). It brings CTranslate2 4.8.3, ONNX Runtime 1.31.0, PyAV 18.1.0,
  tokenizers 0.23.3, huggingface-hub, tqdm and PyYAML. huggingface-hub is
  constrained to `<1` (`[tool.uv] constraint-dependencies`; locked 0.36.2 with
  requests and urllib3), because 2.x pulls in `httpx2`, which replaces
  Starlette's TestClient transport. faster-whisper never downloads: models
  load from a local directory with `local_files_only=True`.
- **piper-tts.** `piper-tts>=1.8,<2`, locked **1.8.0** (**GPL-3.0-or-later**;
  ADR-106; review before any distribution), with pathvalidate.
- **sounddevice.** `sounddevice>=0.5.6,<0.6`, locked **0.5.6** (MIT; bundled
  PortAudio on Windows; ADR-107), with cffi and pycparser.
- **numpy.** `numpy>=2,<3`, locked **2.4.6**, now used directly.

The lock gains exactly 21 package records; no existing record changes except
`phoenix`. Engines are imported lazily (`importlib`), only when voice starts.
Models are pinned files (Hugging Face commit, size, SHA-256) that the explicit
`scripts/voice_setup.py` fetches into `D:\Phoenix\models\voice` (ADR-109).
They are never auto-downloaded. Coqui XTTS remains an optional candidate.
### Installer runtime (Phase 9)

Owner-approved on 2026-10-21 (PHX-092, ADR-125): the Windows installer ships the
python.org **embeddable CPython 3.11.9** (amd64, PSF license; SHA-256
`009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b`) with every
runtime package of `uv.lock`, unchanged and hash-checked. No package is added to
`pyproject.toml`, `uv.lock` or `Cargo.toml`: the build uses `uv` (already
required) and the Tauri CLI's NSIS bundler. Ollama and its models stay
operational prerequisites that neither the installer nor the app downloads
(ADR-127); WebView2's bootstrapper runs only when WebView2 is missing.

## Optional V1 runtime

### Colibri
https://github.com/JustVugg/colibri

Optional deep MoE inference backend using storage/RAM/VRAM tiering.

### Coqui TTS
https://github.com/coqui-ai/TTS

Candidate local TTS backend behind the voice-provider abstraction.

## Experimental/reference inference

### AirLLM
https://github.com/lyogavin/airllm

Large-model inference/training under constrained VRAM; experimental provider/reference.

## Development/training

### Soup
https://github.com/MakazhanAlpamys/Soup

Fine-tuning/post-training/export experiments. Not required runtime.

### RTK
https://github.com/rtk-ai/rtk

Optional compact terminal-output tooling for coding agents.

### Nightshift
https://github.com/marcus/nightshift

Optional development maintenance automation.

## Design references

### ECC
https://github.com/affaan-m/ECC

Reference for coordinated engineering agents, skills, memory, review, and verification. Phoenix must not become an ECC wrapper.

### Karpathy-inspired skills
https://github.com/multica-ai/andrej-karpathy-skills

Reference for think-before-coding, simplicity, surgical changes, and goal-driven verification.

### Ponytail
https://github.com/DietrichGebert/ponytail

Reference for avoiding unnecessary implementation complexity.

## Rule

Promoting an optional/reference dependency to required runtime requires an accepted decision.
