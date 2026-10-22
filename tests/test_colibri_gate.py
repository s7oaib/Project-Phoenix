"""PHX-116 Colibri deep-tier gate tests (fakes only; no engine, model or GPU)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any, Self

import pytest

from phoenix.models.benchmark import GpuReading, ResourceSnapshot
from phoenix.models.provider import (
    CancellationResult,
    CancellationStatus,
    GenerationChunk,
    GenerationRequest,
    GenerationResponse,
    ModelCapabilities,
    ModelProvider,
    ModelProviderError,
    ProviderHealth,
    ProviderHealthStatus,
    TokenUsage,
)

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "benchmarks" / "fyp" / "tasks-v1.toml"


def _load(name: str, file: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load("phoenix_colibri_gate", "colibri_gate.py")
fyp = _load("phoenix_fyp_benchmark_gate", "fyp_benchmark.py")
ANSWERS = {
    case.messages[-1].text: case.scoring.expected or "A short plan."
    for case in fyp.load_task_set(TASKS)[0].cases
}


class FakeColibri(ModelProvider):
    """Streams each task's expected answer with explicit usage."""

    def __init__(
        self,
        status: ProviderHealthStatus = ProviderHealthStatus.HEALTHY,
        served: bool = True,
        usage: bool = True,
    ) -> None:
        super().__init__("colibri")
        self.status, self.served, self.usage = status, served, usage

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def health(self) -> ProviderHealth:
        return ProviderHealth(provider_id=self.provider_id, status=self.status)

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        if not self.served:
            raise ModelProviderError("not served")
        return ModelCapabilities(
            provider_id=self.provider_id,
            model_id=model_id,
            supports_streaming=True,
            supports_structured_output=False,
            supports_tool_use=False,
            supports_vision=False,
            supports_cancellation=True,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        raise AssertionError("the harness streams")

    async def stream(
        self, request: GenerationRequest
    ) -> AsyncIterator[GenerationChunk]:
        yield GenerationChunk(
            provider_id=self.provider_id,
            model_id=request.model_id,
            operation_id=request.operation_id,
            text=ANSWERS[request.messages[-1].text],
        )
        yield GenerationChunk(
            provider_id=self.provider_id,
            model_id=request.model_id,
            operation_id=request.operation_id,
            text="",
            is_final=True,
            finish_reason="stop",
            # Live Colibri v2.0.0 reports no usage at all (PHX-124).
            usage=TokenUsage(input_tokens=10, output_tokens=40) if self.usage else None,
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            provider_id=self.provider_id,
            operation_id=operation_id,
            status=CancellationStatus.NOT_FOUND,
        )


class FixedProbe:
    def sample(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            system_ram_used_bytes=12 * gate.GIB,
            system_disk_read_bytes=0,
            gpus=(GpuReading(index=0, name="fixture", used_mib=5000, total_mib=6144),),
        )


def _engine_io() -> Any:
    reads = iter((100 * gate.MIB, 612 * gate.MIB))
    return lambda: {
        "read_bytes": next(reads),
        "rss_bytes": 9 * gate.GIB,
        "processes": 1,
    }


def _live(tmp_path: Path, provider: FakeColibri, *extra: str) -> tuple[int, Path]:
    out = tmp_path / "out"
    code = gate.main(
        ["--live", "--out", str(out), "--model-dir", str(tmp_path / "none"), *extra],
        provider_factory=lambda: provider,
        process_io=_engine_io(),
        resource_probe=FixedProbe(),
    )
    return code, out


def test_repository_manifest_pins_the_catalog_container() -> None:
    manifest = gate.load_manifest(gate.DEFAULT_MANIFEST)
    assert len(manifest) == 42  # 40 expert shards, globals, tokenizer
    assert manifest["model-globals.safetensors"] == (
        "b00b11ff7de0072b5a3e08c04b7d8bbfe065ae2d1a299de6e0a2ae6d9896bb23"
    )
    assert sum(name.startswith("model-000") for name in manifest) == 40
    assert "tokenizer.json" in manifest


@pytest.mark.parametrize(
    "text",
    [
        "",
        "# only a note\n",
        "abc  file\n",
        "a" * 64 + "  ../escape\n",
        "a" * 64 + " x\n",
    ],
)
def test_manifest_rejects_malformed_lines(tmp_path: Path, text: str) -> None:
    path = tmp_path / "m.sha256"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        gate.load_manifest(path)


def test_verify_manifest_reports_missing_and_mismatched(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"alpha")
    (tmp_path / "b.bin").write_bytes(b"beta")
    good = {
        "a.bin": hashlib.sha256(b"alpha").hexdigest(),
        "b.bin": hashlib.sha256(b"beta").hexdigest(),
    }
    result = gate.verify_manifest(tmp_path, good)
    assert result["ok"] and result["bytes"] == 9 and result["files"] == 2
    bad = {**good, "b.bin": "0" * 64, "c.bin": "1" * 64}
    result = gate.verify_manifest(tmp_path, bad)
    assert not result["ok"]
    assert (result["missing"], result["mismatched"]) == (["c.bin"], ["b.bin"])


def test_io_bench_reads_shards_up_to_the_limit(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"model-0000{index}.safetensors").write_bytes(b"x" * 3000)
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    ticks = iter((10.0, 12.0))
    result = gate.io_bench(
        gate.shard_files(tmp_path),
        limit_bytes=5000,
        chunk=1024,
        clock=lambda: next(ticks),
    )
    assert result["bytes_read"] == 5000 and result["seconds"] == 2.0
    assert result["files"] == ["model-00000.safetensors", "model-00001.safetensors"]
    assert result["mb_per_s"] == round(5000 / gate.MIB / 2, 1)


def _passing() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    facts = {"ram_total_bytes": 24 * gate.GIB}
    live = {
        "success_rate": 1.0,
        "score_accuracy": 0.8,
        "ttft_ms_median": 20_000.0,
        "tokens_per_second_median": 6.0,
        "max_ram_used_bytes": 20 * gate.GIB,
        "max_gpu_used_mib": 5800,
        "gpu_total_mib": 6144,
    }
    return facts, live, {"mb_per_s": 2500.0}


def test_verdict_passes_only_when_every_criterion_passes() -> None:
    facts, live, io = _passing()
    verdict, criteria = gate.evaluate(facts, live, io, gate.Thresholds())
    assert verdict == "PASS" and {c["status"] for c in criteria} == {"PASS"}
    assert [c["id"] for c in criteria] == [
        "success_rate",
        "score_accuracy",
        "ttft_ms_median",
        "tokens_per_second_median",
        "ram_headroom_bytes",
        "vram_fraction",
        "nvme_read_mb_per_s",
    ]
    for key, value in (
        ("ttft_ms_median", 61_000.0),
        ("tokens_per_second_median", 1.5),
        ("max_ram_used_bytes", 23 * gate.GIB),
        ("max_gpu_used_mib", 6144),
        ("success_rate", 0.5),
    ):
        verdict, criteria = gate.evaluate(
            facts, {**live, key: value}, io, gate.Thresholds()
        )
        assert verdict == "FAIL"
        assert sum(c["status"] == "FAIL" for c in criteria) == 1
    assert gate.Thresholds().min_read_mb_per_s == 1000  # pre-registered (ADR-120)
    verdict, _ = gate.evaluate(facts, live, {"mb_per_s": 823.0}, gate.Thresholds())
    assert verdict == "FAIL"
    verdict, criteria = gate.evaluate(facts, None, io, gate.Thresholds())
    assert verdict == "INCOMPLETE"
    assert {c["status"] for c in criteria} == {"NOT_MEASURED", "PASS"}
    # A failure is final even when other criteria were not measured.
    verdict, _ = gate.evaluate(facts, None, {"mb_per_s": 1.0}, gate.Thresholds())
    assert verdict == "FAIL"


def test_live_run_measures_the_served_model_without_content(tmp_path: Path) -> None:
    code, out = _live(tmp_path, FakeColibri())
    assert code == 1  # no --io-bench: NVMe not measured, so INCOMPLETE
    text = (out / gate.RESULTS_NAME).read_text("utf-8")
    results = json.loads(text)
    assert results["verdict"] == "INCOMPLETE" and results["tool"] == gate.TOOL
    live = results["live"]
    assert (live["success_rate"], live["score_accuracy"]) == (1.0, 1.0)
    assert live["measured_trials"] == 16
    assert live["max_gpu_used_mib"] == 5000 and live["gpu_total_mib"] == 6144
    assert live["tokens_per_second_median"] > 0
    engine = results["live_endpoint"]["engine"]
    assert engine["read_bytes"] == 512 * gate.MIB and engine["processes"] == 1
    statuses = {c["id"]: c["status"] for c in results["criteria"]}
    assert statuses["nvme_read_mb_per_s"] == "NOT_MEASURED"
    assert statuses["success_rate"] == statuses["vram_fraction"] == "PASS"
    for prompt, answer in ANSWERS.items():
        assert prompt not in text
    summary = (out / gate.SUMMARY_NAME).read_text("utf-8")
    assert "Colibri deep-tier gate — INCOMPLETE" in summary
    assert "Engine disk reads during the run: 0.50 GiB" in summary


def _word_tokenizer(model_dir: Path) -> None:
    """A tiny local tokenizer.json: one token per whitespace-separated word."""
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace

    tokenizer = Tokenizer(WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = Whitespace()
    model_dir.mkdir()
    tokenizer.save(str(model_dir / "tokenizer.json"))


def test_missing_usage_without_tokenizer_is_never_pass(tmp_path: Path) -> None:
    code, out = _live(tmp_path, FakeColibri(usage=False))
    results = json.loads((out / gate.RESULTS_NAME).read_text("utf-8"))
    statuses = {c["id"]: c["status"] for c in results["criteria"]}
    assert statuses["tokens_per_second_median"] == "NOT_MEASURED"
    assert code == 1 and results["verdict"] == "INCOMPLETE"
    assert results["live"]["throughput_method"] is None
    summary = (out / gate.SUMMARY_NAME).read_text("utf-8")
    assert "Throughput method: NOT MEASURED (0 trials" in summary


def test_missing_usage_is_measured_by_the_model_tokenizer(tmp_path: Path) -> None:
    model = tmp_path / "model"
    _word_tokenizer(model)
    out = tmp_path / "out"
    code = gate.main(
        ["--live", "--out", str(out), "--model-dir", str(model)],
        provider_factory=lambda: FakeColibri(usage=False),
        process_io=_engine_io(),
        resource_probe=FixedProbe(),
    )
    text = (out / gate.RESULTS_NAME).read_text("utf-8")
    results = json.loads(text)
    live = results["live"]
    assert live["throughput_method"] == "tokenizer_recount"
    assert live["throughput_trials"] == 16 and live["tokens_per_second_median"] > 0
    statuses = {c["id"]: c["status"] for c in results["criteria"]}
    assert statuses["tokens_per_second_median"] != "NOT_MEASURED"
    assert code == 1 and results["verdict"] == "INCOMPLETE"  # NVMe not measured
    long = [a for a in ANSWERS.values() if len(a) >= 12]
    assert long and not any(a in text for a in long)  # counts only, never text


def test_counting_provider_counts_streamed_text_per_operation() -> None:
    import asyncio

    async def run() -> dict[str, int]:
        provider = gate.CountingProvider(FakeColibri(), lambda s: len(s.split()))
        prompt, answer = next(iter(ANSWERS.items()))
        request = GenerationRequest.model_validate(
            {
                "operation_id": "Op.A",
                "model_id": "m",
                "messages": [{"role": "user", "text": prompt}],
            }
        )
        chunks = [chunk async for chunk in provider.stream(request)]
        assert "".join(c.text for c in chunks) == answer and chunks[-1].is_final
        assert provider.provider_id == "colibri"
        counts: dict[str, int] = provider.output_tokens
        return counts

    counts = asyncio.run(run())
    assert counts == {"Op.A": len(next(iter(ANSWERS.values())).split())}


def test_load_token_counter_is_local_and_optional(tmp_path: Path) -> None:
    assert gate.load_token_counter(tmp_path / "absent") is None
    model = tmp_path / "model"
    _word_tokenizer(model)
    counter = gate.load_token_counter(model)
    assert counter is not None and counter("one two three") == 3


def test_summary_reports_a_zero_vram_reading_explicitly(tmp_path: Path) -> None:
    class IdleGpu(FixedProbe):
        def sample(self) -> ResourceSnapshot:
            return ResourceSnapshot(
                system_ram_used_bytes=12 * gate.GIB,
                gpus=(GpuReading(index=0, name="fixture", used_mib=0, total_mib=6144),),
            )

    out = tmp_path / "out"
    gate.main(
        ["--live", "--out", str(out), "--model-dir", str(tmp_path / "none")],
        provider_factory=lambda: FakeColibri(),
        process_io=_engine_io(),
        resource_probe=IdleGpu(),
    )
    live = json.loads((out / gate.RESULTS_NAME).read_text("utf-8"))["live"]
    assert (live["max_gpu_used_mib"], live["gpu_samples"]) == (0, 32)
    assert live["gpu_samples_expected"] == 32
    summary = (out / gate.SUMMARY_NAME).read_text("utf-8")
    assert "device-wide peak 0 MiB in 32/32" in summary
    assert "engine held no GPU memory (CPU-only run)" in summary


@pytest.mark.parametrize(
    "provider",
    [FakeColibri(ProviderHealthStatus.UNAVAILABLE), FakeColibri(served=False)],
)
def test_live_run_refuses_without_a_served_model(
    tmp_path: Path, provider: FakeColibri
) -> None:
    code, out = _live(tmp_path, provider)
    assert code == 2 and not (out / gate.RESULTS_NAME).exists()


def test_manifest_failure_rejects_and_dry_run_plans(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    model = tmp_path / "model"
    model.mkdir()
    manifest = tmp_path / "m.sha256"
    manifest.write_text("0" * 64 + "  model-globals.safetensors\n", encoding="utf-8")
    out = tmp_path / "out"
    code = gate.main(
        ["--verify-manifest", "--manifest", str(manifest), "--model-dir", str(model)]
        + ["--out", str(out)],
        resource_probe=FixedProbe(),
    )
    results = json.loads((out / gate.RESULTS_NAME).read_text("utf-8"))
    assert code == 1 and results["verdict"] == "FAIL"
    assert results["manifest"]["missing"] == ["model-globals.safetensors"]
    assert results["host_facts"]["gpus"][0]["total_mib"] == 6144
    assert gate.main(["--dry-run", "--live", "--io-bench"]) == 0
    printed = capsys.readouterr().out
    assert "phases: facts, io-bench, live" in printed
    assert "16 cases x (1 warm-up + 1 measured)" in printed
    with pytest.raises(SystemExit):
        gate.main(["--dry-run", "--runs", "0"])
