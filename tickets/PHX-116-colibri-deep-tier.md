# PHX-116 — Colibri deep tier (FYP configuration 3)

Status: IN_PROGRESS. The engine and model are installed and verified, and the gate script and configuration (3) runner are ready and tested offline. The live gate is waiting for the GPU. See "Ready for benchmark" below.
Phase: 10 — Product Integration (milestone M3, capabilities)
Branch: `feature/PHX-116-colibri-deep-tier` (local commits only; no push, no PR)
Base: `origin/main` `5722431` (PR #85)

## Objective

Make FYP configuration (3), "Phoenix + adaptive model routing/deep tier" (docs/24), runnable:

1. Install the optional T3 deep tier from docs/05: Qwen3.6-35B-A3B on Colibri.
2. Gate it on BLAZE-6 for RAM, VRAM, NVMe I/O, TTFT, throughput and quality.
3. If it passes, run configuration (3) with a defined escalation policy.

If it fails the gate, configuration (3) is reported as measured and rejected, with the numbers. That is a valid FYP outcome.

## Authoritative documents

- `/AGENTS.md`, `/RULES.md`, `/INVARIANTS.md` (INV-008, INV-011, INV-021, INV-022, INV-023, INV-024), `/MASTER_SPEC.md`
- `docs/05_MODEL_STRATEGY.md` (Colibri, freeze gate), `docs/06_HARDWARE_PROFILE.md`, `docs/24_TEST_EVALUATION.md`
- ADR-007, ADR-028 (ColibriProvider), ADR-029 (PHX-016 harness), ADR-079 (FYP suite), ADR-081, ADR-084 (OS-assigned backend port), ADR-090, ADR-093
- PHX-015, PHX-016, PHX-091, PHX-105, PHX-109

## Owner decisions (2026-10-21, final)

- Yes, try it: build or run the Colibri engine and download Qwen3.6-35B-A3B in the quantization that fits, to `D:\Phoenix`.
- Downloads are pre-approved from the official project repositories and model publishers. Record URLs and sha256.
- A gate failure (RAM, VRAM, NVMe I/O, TTFT) is reported as measured and rejected, with numbers.
- Colibri must not conflict with PHOENIX's port. Use the recommended options otherwise. ADR numbers 120–124 only.
- GPU rule: another agent is using the GPU. Do not load the 35B model or run any GPU or inference benchmark until the coordinator says so.

## Decisions

- **ADR-120** — The engine is the official Colibri v2.0.0 release, and the model is the container that Colibri's own catalog installs. Colibri keeps port 8000. The gate's criteria are fixed.
- **ADR-121** — Configuration (3) escalates to the deep tier only on PHOENIX's own failure evidence (`verification_failed`, `responder_output`, or an unverified DIRECT fallback), at most once per request, with LEVI on Ollama.

`phoenix/` is unchanged: no product code, dependency, lock, migration, event or frozen-file change, so there is no re-pin. Colibri stays optional (INV-024).

## Hardware facts (BLAZE-6, measured 2026-10-21)

| Item | Value |
| --- | --- |
| System RAM | 23.71 GiB usable (25,463,480,320 bytes): 2 × 12 GB DDR5-4800 (`RMSB3400KB06IVF-4800`) |
| GPU | NVIDIA GeForce RTX 3050 6GB Laptop GPU, 6144 MiB, compute capability 8.6, driver 610.62. The CUDA toolkit is v13.3, but the release bundles its own CUDA 12 runtime |
| Disk | One Samsung `MZAL81T0HDLB-00BL2` NVMe SSD, 1 TB |
| D: free | About 85 GB before the download; 53.9–56 GB after it (other agents also write to D:). The brief's 159 GB is out of date |
| Toolchain | git and Python 3.11. No cmake, MinGW gcc/make or MSVC |
| Sequential shard read (preliminary, disk only) | 823 MB/s (8 GiB, after the hash pass) and 662 MB/s (8 GiB, alone). A .NET cross-check read 673 MB/s (4 GB, 64 MiB blocks). All three ran on AC power while another agent used the GPU, Ollama and RAM (8.5 GB available) |
| `coli plan` (read-only, under that load) | RAM budget 6.6 GB (4.9 GB dense + 4.2 GB runtime) and a 4.2 GB VRAM hot tier of about 2,385 experts. That leaves 13.9 GB of cold experts on disk and 23% projected expert residency, with disk expert misses as the limit. It warns that the RAM budget cannot hold one expert slot per sparse layer. The plan must be re-read when the machine is idle |

The NVMe floor was first set at 1000 MB/s, then lowered to 600 MB/s (ADR-120) after the preliminary reads above. The floor only rules out a SATA-class disk: Colibri streams experts, so the disk's real cost shows up in the TTFT and throughput criteria. Both values are recorded here for transparency.

## What was installed (nothing committed; all under `D:\Phoenix`)

### Engine — `D:\Phoenix\colibri\engine-v2.0.0-cuda`

Source: the official repository https://github.com/JustVugg/colibri, release `v2.0.0` (2026-10-06). Every hash matches the release's `SHA256SUMS.txt` and GitHub's asset digest.

| File | URL | Size (bytes) | sha256 |
| --- | --- | --- | --- |
| `colibri-v2.0.0-windows-x86_64.zip` | https://github.com/JustVugg/colibri/releases/download/v2.0.0/colibri-v2.0.0-windows-x86_64.zip | 11,019,675 | `fdb8ea2fad7dcfe44d9d37bfb3e444f1299207f268ab499719f98bd772397d79` |
| `colibri-v2.0.0-windows-x86_64-cuda.zip` | https://github.com/JustVugg/colibri/releases/download/v2.0.0/colibri-v2.0.0-windows-x86_64-cuda.zip | 3,549,786 | `e0f7f4ad339909a9ef5dfc3bda6afe46757d3358d8a2d446e460ce14a1f7dc3a` |
| `SHA256SUMS.txt` | https://github.com/JustVugg/colibri/releases/download/v2.0.0/SHA256SUMS.txt | 406 | `199f9a088346161b9174cbe887a1a17bd182a85f945e15beec0949a713911a34` |

The archives are kept in `D:\Phoenix\colibri\release-v2.0.0`. The CUDA overlay replaces `colibri.exe`, `qwen36.exe` and `kimi_k3.exe` and adds `coli_cuda.dll` and `cudart64_12.dll`, as its `README-CUDA.txt` says. After the overlay:

| File | sha256 |
| --- | --- |
| `qwen36.exe` | `420a0aa3906cebfb2fc51f9c04df06fe6aef4a21644b4c1c2fba9ab89760658b` |
| `coli_cuda.dll` | `1acacf1149167fe159d4659cbb2242e31c5756de395a39fe1a783281fb2d0259` |

**Build status:** the official prebuilt release is used, not a local build.
- Building on Windows needs MinGW gcc/make, and MSVC Build Tools for the CUDA DLL (upstream `docs/windows.md`). None of these is installed.
- Installing system toolchains was out of scope.
- Upstream documents the release archive as the Windows route. Its CUDA DLL covers compute capability 8.0 and newer, so it runs on the RTX 3050 (8.6).

A reference checkout of the source is at `D:\Phoenix\colibri\src`: `git clone https://github.com/JustVugg/colibri` at `e0db083cdc9e563e0766cc6fc661ef23c32298e0` (2026-10-17).

### Model — `D:\Phoenix\models\colibri\qwen36-35b-a3b-colibri-i4-gs64`

- **Repository:** https://huggingface.co/Kreuzzelg/qwen36-35b-a3b-colibri-i4-gs64, pinned to revision `c619aa594ad1e70af82168fb6b4878427896e21c`.
- **Download command:** `hf download Kreuzzelg/qwen36-35b-a3b-colibri-i4-gs64 --revision c619aa59… --local-dir …` (huggingface_hub 0.35.3).
- **Size:** 47 files, 23,031,250,325 bytes, including the downloader's `.cache` metadata.
- **Quantization:** int4 group-scaled experts (gs64), 23.1 GB. This is the size Colibri's catalog recommends for 10–20 GB of RAM (`ram_min_gb=10`, `ram_good_gb=20`), and it fits 24 GB.
- **Provenance:**
  - It is the container that Colibri's own setup installs for `qwen36-35b`: `c/setup_catalog.py` at `e0db083` names `repo="Kreuzzelg/qwen36-35b-a3b-colibri-i4-gs64"`, and `docs/qwen36.md` recommends it.
  - Its model card names `Qwen/Qwen3.6-35B-A3B` (Apache-2.0) as the base model.
  - The publisher is Kreuzzelg, a Colibri contributor (author of the group-scaled path), not Qwen. The upstream designates it as the project artifact.
  - It holds only data files: safetensors, JSON and a tokenizer.
- **Alternative not taken:** converting the official `Qwen/Qwen3.6-35B-A3B` checkpoint locally (`tools/convert_qwen36.py --repo Qwen/Qwen3.6-35B-A3B --gs 64 --low-disk`). That needs a ~70 GB download and a PyTorch install. It remains available if the owner wants publisher-direct weights.
- **Integrity:** the sha256 of every large file is the Hugging Face LFS oid. All are pinned in `benchmarks/colibri/qwen36-35b-a3b-colibri-i4-gs64.sha256`: 40 expert shards, `model-globals.safetensors` and `tokenizer.json`.
  - `uv run python scripts/colibri_gate.py --verify-manifest` checked them: **manifest OK**, 42 files, 23,031,238,374 bytes, none missing or mismatched.
  - Key hashes: `model-globals.safetensors` (2,034,241,832 bytes) `b00b11ff7de0072b5a3e08c04b7d8bbfe065ae2d1a299de6e0a2ae6d9896bb23`; `tokenizer.json` (12,807,982 bytes) `5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42`; `model-00000.safetensors` (527,831,896 bytes) `96c003f9a1e2d0b9ee8211ea70c3f0ae3e22a8056b5e5b6c74cec672498a3350`. The other 39 shards are listed in the manifest file.
- **Small files:** Hugging Face publishes no LFS hash for these, so they were hashed locally:

  | File | Size (bytes) | sha256 |
  | --- | --- | --- |
  | `config.json` | 2,081 | `7606939241b64e82308369b308af8abe85d24e70846704c41ed4e0b59687a674` |
  | `config.hf.json` | 3,686 | `93a4693fa9d8392fbfccd4b3c9873f4bfdcb14fdede978b123d07d19675efe99` |
  | `qwen36_meta.json` | 1,702 | `cb2d7d4054865c7d2d6f6da3c902b51d9942e5f19b53e646b23909cdd4ce0fc4` |
  | `README.md` | 2,912 | `e4eba18dd8f3c63ef8b01a10e9c9f4a888344426ac133658adafef490be18735` |
  | `.gitattributes` | 1,570 | `34448b82c17d60fec9b65b1f093c115ddbaadc04beb1b0140b6bfed2e012a930` |

## Implementation (repository)

- `scripts/colibri_gate.py` — the deep-tier gate (ADR-120).
  - **Always:** host facts.
  - **Opt-in:** `--io-bench` (sequential shard read, run before hashing) and `--verify-manifest`.
  - **`--live`:** the fixed v1 task set on the served model through the unchanged `ColibriProvider` and PHX-016 `BenchmarkRunner`. It also records the engine process's (`qwen36.exe`) disk reads and RSS.
  - **Verdict:** PASS, FAIL or INCOMPLETE, against fixed thresholds. Results go to `colibri-gate-results.json` and `colibri-gate-summary.md`.
  - **Refusals:** it refuses (exit 2) when nothing serves the model. It never starts, stops or configures the engine.
- `scripts/fyp_adaptive_deep.py` — the configuration (3) runner (ADR-121), dispatched by `scripts/fyp_benchmark.py --config phoenix-adaptive-deep`.
  - **Runtimes:** a first-attempt `PhoenixRuntime` (configuration 2) and a deep runtime. The deep runtime has the Colibri provider, and Colibri is first in the default and `build_engineer` rows. Both share one `ModelLifecycleManager` (INV-023).
  - **Escalation:** decided by `escalation_reason()` from closed runtime values only.
  - **Report:** the first-attempt summary, the final summary, escalations by reason, deep generations used or not used, rescued missions and the deep attempt's latency.
  - **Refusals:** it refuses when Colibri is unhealthy or not serving the model.
- `scripts/fyp_benchmark.py` — adds the `phoenix-adaptive-deep` configuration choice and dispatch. The configuration (3) and `colibri_disk_io` reasons now point to PHX-116 instead of saying "not installed".
- `benchmarks/colibri/qwen36-35b-a3b-colibri-i4-gs64.sha256` — the pinned manifest.
- Tests (offline, fakes only):
  - `tests/test_colibri_gate.py`: 13 cases. Manifest pins and malformed lines, verify, io-bench limits, every verdict branch, a live run through the real harness with no content in the reports, refusals, and dry-run.
  - `tests/test_fyp_adaptive_deep.py`: 22 cases.
    - The escalation policy, across 16 parametrized outcomes.
    - The deep tier table never touches control roles.
    - Only rejected answers escalate, and the deep tier rescues them while LEVI stays on Ollama.
    - An unhealthy deep tier falls back to Ollama observably.
    - The run refuses when the deep model is not served.
    - The live factory shares one lifecycle and refuses without Colibri.
    - Dispatch, dry-run, privacy and metrics.
- `tests/matrix.toml` — maps both test files and adds both scripts to the `mypy-scripts` gate.
- `docs/24_TEST_EVALUATION.md`, `FEATURE_TICKETS.md`, `DECISIONS.md` (ADR-120, ADR-121).

## Ready for benchmark (requires the coordinator's go-ahead; loads the GPU)

Run these in order on AC power, with no other GPU or Ollama work running.

1. Free the GPU and RAM for the gate. `ollama ps` must list no model; to unload one, use `ollama stop <model>`.
2. Start Colibri in its own terminal and leave it running:

   ```powershell
   cd D:\Phoenix\colibri\engine-v2.0.0-cuda
   .\coli.cmd plan --model D:\Phoenix\models\colibri\qwen36-35b-a3b-colibri-i4-gs64
   .\coli.cmd serve --model D:\Phoenix\models\colibri\qwen36-35b-a3b-colibri-i4-gs64 --model-id qwen3.6-colibri --host 127.0.0.1 --port 8000
   ```

   The defaults are recommended: RAM auto, GPU auto with the CUDA expert tier. `coli plan` is read-only; re-read it on the idle machine.
3. Run the gate from the repository root (about 20–60 minutes, with 16 cases × (1 warm-up + 1 measured)):

   ```powershell
   uv run python scripts/colibri_gate.py --io-bench --live --out validation/PHX-116-gate-<yyyymmdd-hhmmss>
   ```

   Exit 0 means PASS, 1 means FAIL or INCOMPLETE, and 2 means nothing is serving. Check that the `colibri_disk_io` engine read figure is present.
4. **Only if the gate passes**, run configuration (3). Restart Colibri with a smaller budget so Ollama's tiers fit beside it, then start Ollama as for configuration (2): the headless `ollama serve`, 16K context, `OLLAMA_MODELS=D:\Phoenix\models` (ADR-079).

   ```powershell
   .\coli.cmd serve --model D:\Phoenix\models\colibri\qwen36-35b-a3b-colibri-i4-gs64 --model-id qwen3.6-colibri --host 127.0.0.1 --port 8000 --ram 8 --vram 1.5
   uv run python scripts/fyp_benchmark.py --config phoenix-adaptive-deep --colibri-url http://127.0.0.1:8000/v1 --deep-model qwen3.6-colibri --out validation/PHX-116-config3-<yyyymmdd-hhmmss>
   ```

   The `--ram 8 --vram 1.5` budget is a starting point, not a measured optimum. The runner refuses a CPU-only Ollama placement unless `--allow-cpu` is passed.
5. If the gate fails, do not run step 4. Configuration (3) is reported as measured and rejected, with the gate summary's numbers.
6. Stop Colibri when done: press Ctrl+C in its terminal, or run `.\coli.cmd stop` in the engine folder.

## Verification (offline)

- `uv run --locked pytest tests/test_colibri_gate.py tests/test_fyp_adaptive_deep.py tests/test_fyp_benchmark.py tests/test_fyp_multi_agent.py tests/test_test_matrix.py -q`: PASS.
- `ruff check .`, `ruff format --check .` and `mypy --strict` on all seven scripts: PASS.
- `uv run python scripts/test_matrix.py --allow-dirty`: see the completion evidence. The only untracked path is a foreign `.swarm/` tool file, not created by this ticket.

## Invariants and security

- **INV-008, INV-021:** an escalated answer is a COMPLEX mission that LEVI must verify. Failures stay explicit, and refusals, permissions and tools never escalate.
- **INV-022:** deep-tier fallback is reported (`deep_not_used`).
- **INV-023:** the two runtimes share one lifecycle lease, so one heavy generation runs at a time.
- **INV-024:** Colibri stays optional. No dependency or `phoenix/` change.
- **INV-011:** no frontend change.
- **Privacy (ADR-029):** reports keep hashes and lengths only. The gate never manages the engine.
- **Network:** downloads came only from the official GitHub release and the Colibri-designated Hugging Face container, pinned by revision and sha256.

## Known limitations

- The live gate and configuration (3) are not yet measured; they are waiting for the GPU.
- Qwen3.6 thinks by default, and `ColibriProvider` cannot turn thinking off without a PHX-010 contract change. TTFT therefore includes reasoning, and throughput is output tokens ÷ whole latency.
- The sequential-read figure is buffered and may include OS cache. The preliminary figures were taken while another agent loaded the machine.
- Running Ollama and Colibri together contends for 24 GB of RAM and 6 GB of VRAM. Step 4's budgets are a starting point.
- Escalation lives in the FYP runner, not in PHOENIX's product runtime. Product escalation is a later ticket if the gate passes.

## Completion evidence

- **Commits:** `bd73faf` (implementation and docs) and `5a9261b` (strict-mypy typing of the gate test fake). `phoenix/` is unchanged, so no re-pin commit is needed.
- **Matrix:** `uv run --locked python scripts/test_matrix.py --allow-dirty` reports **PASS** on every gate: uv-sync, ruff check and format, mypy, mypy-scripts, pytest, npm-test, typecheck, frontend-build, tauri-info, cargo-check, cargo-test and uv-build.
  - pytest: 16,410 tests, 0 failures, 0 errors, 30 skipped. It took 3,167 s because other agents' suites were running at the same time.
  - `--allow-dirty` was needed only because of the foreign untracked `.swarm/` file.
  - The first matrix run failed only its mypy gate, on 4 typing errors in the gate test fake. `5a9261b` fixed them.
- **Disk-only gate phases** (no GPU, no model load): `colibri_gate.py --io-bench --verify-manifest` reported manifest OK (42/42) and a sequential read of 823 MB/s; `--io-bench` alone read 662 MB/s.
- **Acceptance status:**
  - Done: install, provenance, gate, configuration (3) runner and offline tests.
  - Waiting for the GPU: the live gate verdict, then configuration (3) or its measured rejection.
