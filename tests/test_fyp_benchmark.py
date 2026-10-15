"""PHX-091: FYP benchmark suite runner tests (fake provider only; no network/models)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any, Self

import pytest

from phoenix.models.benchmark import (
    GpuReading,
    ResourceSnapshot,
    ScoringMode,
)
from phoenix.models.provider import (
    CancellationResult,
    CancellationStatus,
    GenerationChunk,
    GenerationRequest,
    GenerationResponse,
    ModelCapabilities,
    ModelProvider,
    ProviderHealth,
    ProviderHealthStatus,
    TokenUsage,
)

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phoenix_fyp_benchmark", ROOT / "scripts" / "fyp_benchmark.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fyp = _load()
TASKS = ROOT / "benchmarks" / "fyp" / "tasks-v1.toml"


class AnswerProvider(ModelProvider):
    """Answers each prompt from a fixed table; unknown prompts get 'unknown'."""

    def __init__(self, answers: dict[str, str], healthy: bool = True) -> None:
        super().__init__("fixture")
        self.answers = answers
        self.healthy = healthy
        self.models: list[str] = []

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
        status = (
            ProviderHealthStatus.HEALTHY
            if self.healthy
            else ProviderHealthStatus.UNAVAILABLE
        )
        return ProviderHealth(provider_id=self.provider_id, status=status)

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        self.models.append(model_id)
        return ModelCapabilities(
            provider_id=self.provider_id,
            model_id=model_id,
            supports_streaming=True,
            supports_structured_output=False,
            supports_tool_use=False,
            supports_vision=False,
            supports_cancellation=True,
        )

    def _answer(self, request: GenerationRequest) -> str:
        return self.answers.get(request.messages[-1].text, "unknown")

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        return GenerationResponse(
            provider_id=self.provider_id,
            model_id=request.model_id,
            operation_id=request.operation_id,
            text=self._answer(request),
        )

    async def stream(
        self, request: GenerationRequest
    ) -> AsyncIterator[GenerationChunk]:
        yield GenerationChunk(
            provider_id=self.provider_id,
            model_id=request.model_id,
            operation_id=request.operation_id,
            text=self._answer(request),
            is_final=True,
            usage=TokenUsage(input_tokens=10, output_tokens=4),
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
            system_ram_used_bytes=8_000_000_000,
            gpus=(
                GpuReading(index=0, name="fixture-gpu", used_mib=3000, total_mib=6144),
            ),
        )


def _correct_answers() -> dict[str, str]:
    suite, _ = fyp.load_task_set(TASKS)
    return {
        case.messages[-1].text: case.scoring.expected
        for case in suite.cases
        if case.scoring.expected is not None
    }


def _runner(manager: Any) -> Any:
    return fyp.BenchmarkRunner(manager, resource_probe=FixedProbe())


def test_task_set_is_fixed_versioned_and_deterministic() -> None:
    suite, categories = fyp.load_task_set(TASKS)
    assert (suite.suite_id, suite.version) == ("phoenix-fyp", "1.0.0")
    assert len(suite.cases) == 16 and len(categories) == 16
    assert set(categories.values()) == {
        "instruction",
        "reasoning",
        "structured_output",
        "coding",
        "extraction",
        "untrusted_content",
        "planning",
    }
    modes = {case.scoring.mode for case in suite.cases}
    assert modes <= {ScoringMode.EXACT, ScoringMode.CONTAINS, ScoringMode.UNSCORED}
    unscored = [c for c in suite.cases if c.scoring.mode is ScoringMode.UNSCORED]
    assert {categories[c.case_id] for c in unscored} == {"planning"}


def test_task_set_file_name_matches_version() -> None:
    suite, _ = fyp.load_task_set(TASKS)
    assert re.fullmatch(r"tasks-v(\d+)\.toml", TASKS.name)
    assert suite.version.split(".")[0] == TASKS.stem.removeprefix("tasks-v")


def test_invalid_task_sets_rejected(tmp_path: Path) -> None:
    head = 'suite_id = "s"\nversion = "1.0.0"\n'
    case = '[[case]]\nid = "a"\ncategory = "c"\nprompt = "p"\n'
    bad_unscored = tmp_path / "u.toml"
    bad_unscored.write_text(head + case + 'scoring = "unscored"\nexpected = "x"\n')
    with pytest.raises(ValueError, match="unscored"):
        fyp.load_task_set(bad_unscored)
    missing_expected = tmp_path / "m.toml"
    missing_expected.write_text(head + case + 'scoring = "exact"\n')
    with pytest.raises(ValueError):
        fyp.load_task_set(missing_expected)
    duplicate = tmp_path / "d.toml"
    dup_case = case + 'scoring = "contains"\nexpected = "x"\n'
    duplicate.write_text(head + dup_case + dup_case)
    with pytest.raises(ValueError):
        fyp.load_task_set(duplicate)
    bad_mode = tmp_path / "b.toml"
    bad_mode.write_text(head + case + 'scoring = "llm_judge"\nexpected = "x"\n')
    with pytest.raises(ValueError):
        fyp.load_task_set(bad_mode)


def test_parse_models_defaults_and_overrides() -> None:
    assert fyp.parse_models([]) == fyp.DEFAULT_TIERS
    assert [m for _, m in fyp.DEFAULT_TIERS] == [
        "qwen3.5:2b",
        "qwen3.5:4b",
        "qwen3.5:9b",
        "qwen2.5-coder:7b",
    ]
    assert fyp.parse_models(["T1=qwen3.5:4b", "gemma3:4b"]) == (
        ("T1", "qwen3.5:4b"),
        ("custom", "gemma3:4b"),
    )


def test_every_docs_24_metric_reported_measured_or_not_runnable() -> None:
    doc = (ROOT / "docs" / "24_TEST_EVALUATION.md").read_text(encoding="utf-8")
    section = doc.split("## Metrics")[1].split("##")[0]
    documented = [
        line[2:].strip() for line in section.splitlines() if line.startswith("- ")
    ]
    rows = fyp.metric_rows()
    assert len(rows) == len(documented) == 12
    for row in rows:
        assert row["status"] in {"MEASURED", "NOT_RUNNABLE"}
        assert (row.get("source") or row.get("reason") or "").strip()
    measured = {r["id"] for r in rows if r["status"] == "MEASURED"}
    assert measured == {"task_success", "ttft_latency", "ram_vram", "token_usage"}


def test_full_run_with_fake_provider(tmp_path: Path) -> None:
    provider = AnswerProvider(_correct_answers())
    rows, reports = fyp.asyncio.run(
        fyp.run_suite(
            *fyp.load_task_set(TASKS),
            (("T1", "fixture-model"),),
            fyp.BenchmarkConfig(warmup_runs=0, measured_runs=1),
            lambda: provider,
            _runner,
        )
    )
    (row,) = rows
    assert row["fyp_configuration"] == "1" and row["status"] == "success"
    assert row["summary"]["score_accuracy"] == 1.0
    assert row["summary"]["scorable_trials"] == 14  # 16 cases - 2 unscored planning
    assert set(row["category_accuracy"]) == {
        "instruction",
        "reasoning",
        "structured_output",
        "coding",
        "extraction",
        "untrusted_content",
    }
    assert row["max_ram_used_bytes"] == 8_000_000_000
    assert row["max_gpu_used_mib"] == 3000
    assert (row["input_tokens"], row["output_tokens"]) == (160, 64)
    assert provider.models == ["fixture-model"]
    # Privacy (ADR-029): reports carry hashes/lengths, never the answer text.
    dumped = json.dumps(reports)
    assert "lab-7731@phoenix.example" not in dumped and "PHOENIX_READY" not in dumped


def test_wrong_answers_lower_accuracy_per_category() -> None:
    answers = _correct_answers()
    answers = {k: ("wrong" if v == "PHOENIX_READY" else v) for k, v in answers.items()}
    rows, _ = fyp.asyncio.run(
        fyp.run_suite(
            *fyp.load_task_set(TASKS),
            (("T0", "m"),),
            fyp.BenchmarkConfig(warmup_runs=0, measured_runs=1),
            lambda: AnswerProvider(answers),
            _runner,
        )
    )
    instruction = rows[0]["category_accuracy"]["instruction"]
    assert (instruction["passed"], instruction["n"]) == (2, 3)
    assert rows[0]["summary"]["score_accuracy"] == pytest.approx(13 / 14)


def test_cli_writes_results_with_not_runnable_configs(tmp_path: Path) -> None:
    out = tmp_path / "out"
    code = fyp.main(
        [
            "--out",
            str(out),
            "--model",
            "T1=fixture-model",
            "--warmup",
            "0",
            "--runs",
            "1",
        ],
        provider_factory=lambda: AnswerProvider(_correct_answers()),
    )
    assert code == 0
    results = json.loads(
        (out / "fyp-benchmark-results.json").read_text(encoding="utf-8")
    )
    assert results["schema_version"] == 1 and results["suite"]["version"] == "1.0.0"
    assert len(results["suite"]["sha256"]) == 64
    assert [
        c["fyp_configuration"] for c in results["configurations"]["not_runnable"]
    ] == [
        "2",
        "3",
    ]
    assert all(c["reason"] for c in results["configurations"]["not_runnable"])
    summary = (out / "fyp-benchmark-summary.md").read_text(encoding="utf-8")
    assert "NOT RUNNABLE" in summary and "fixture-model" in summary


@pytest.mark.parametrize(
    ("argv", "mode", "think"),
    [
        ([], "default", None),
        (["--think", "off"], "off", False),
        (["--think", "on"], "on", True),
    ],
)
def test_cli_records_think_mode(
    tmp_path: Path, argv: list[str], mode: str, think: bool | None
) -> None:
    out = tmp_path / "out"
    code = fyp.main(
        ["--out", str(out), "--model", "T1=fixture-model", "--warmup", "0"]
        + ["--runs", "1", *argv],
        provider_factory=lambda: AnswerProvider(_correct_answers()),
    )
    assert code == 0
    results = json.loads(
        (out / "fyp-benchmark-results.json").read_text(encoding="utf-8")
    )
    assert results["provider_options"] == {"think_mode": mode, "think": think}
    summary = (out / "fyp-benchmark-summary.md").read_text(encoding="utf-8")
    assert f"Thinking: {mode}." in summary


@pytest.mark.parametrize(
    ("mode", "think"), [("default", None), ("off", False), ("on", True)]
)
def test_ollama_factory_passes_think_without_io(mode: str, think: bool | None) -> None:
    async def run() -> None:
        provider = fyp._ollama_factory(fyp.THINK[mode])
        assert provider._think is think
        await provider.aclose()

    asyncio.run(run())


def test_cli_rejects_unknown_think_mode() -> None:
    with pytest.raises(SystemExit):
        fyp.main(["--dry-run", "--think", "maybe"])


def test_cli_unavailable_provider_fails(tmp_path: Path) -> None:
    code = fyp.main(
        [
            "--out",
            str(tmp_path / "o"),
            "--model",
            "T1=m",
            "--warmup",
            "0",
            "--runs",
            "1",
        ],
        provider_factory=lambda: AnswerProvider({}, healthy=False),
    )
    assert code == 1
    results = json.loads(
        (tmp_path / "o" / "fyp-benchmark-results.json").read_text(encoding="utf-8")
    )
    assert results["configurations"]["baseline"][0]["status"] == "unavailable"


def test_dry_run_needs_no_provider(capsys: pytest.CaptureFixture[str]) -> None:
    assert fyp.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "phoenix-fyp 1.0.0: 16 cases" in out and "T2=qwen3.5:9b" in out


@pytest.mark.parametrize(
    ("size", "vram", "device", "fraction"),
    [
        (100, 100, "gpu", 1.0),
        (100, 60, "partial", 0.6),
        (100, 0, "cpu", 0.0),
        (0, 0, "cpu", 0.0),
    ],
)
def test_compute_from_ps_classifies_placement(
    size: int, vram: int, device: str, fraction: float
) -> None:
    result = fyp.compute_from_ps({"name": "m", "size": size, "size_vram": vram})
    assert (result["device"], result["gpu_offload_fraction"]) == (device, fraction)


def _cli(tmp_path: Path, probe: Any, *extra: str) -> tuple[int, Path]:
    out = tmp_path / "out"
    code = fyp.main(
        ["--out", str(out), "--model", "T1=fixture-model", "--warmup", "0"]
        + ["--runs", "1", *extra],
        provider_factory=lambda: AnswerProvider(_correct_answers()),
        compute_probe=probe,
    )
    return code, out


def _placement(device: str) -> dict[str, Any]:
    vram = {"gpu": 100, "partial": 50, "cpu": 0}[device]
    placement: dict[str, Any] = fyp.compute_from_ps({"size": 100, "size_vram": vram})
    return placement


def test_gpu_placement_recorded_in_results_and_summary(tmp_path: Path) -> None:
    code, out = _cli(tmp_path, lambda model: _placement("gpu"))
    assert code == 0
    results = json.loads((out / "fyp-benchmark-results.json").read_text("utf-8"))
    row = results["configurations"]["baseline"][0]
    assert row["compute"]["before"]["device"] == "gpu"
    assert row["compute"]["after"]["device"] == "gpu"
    assert results["compute_policy"] == {"allow_cpu": False, "probe": True}
    summary = (out / "fyp-benchmark-summary.md").read_text("utf-8")
    assert "| T1 | fixture-model | gpu 100% | success |" in summary


def test_cpu_only_is_refused_before_any_benchmark_work(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []
    provider = AnswerProvider(_correct_answers())

    def cpu_probe(model: str) -> dict[str, Any]:
        calls.append(model)
        return _placement("cpu")

    code = fyp.main(
        [
            "--out",
            str(tmp_path / "o"),
            "--model",
            "T1=m",
            "--warmup",
            "0",
            "--runs",
            "1",
        ],
        provider_factory=lambda: provider,
        compute_probe=cpu_probe,
    )
    assert code == 2 and calls == ["m"]
    assert provider.models == []  # the harness never started (no capabilities call)
    assert not (tmp_path / "o" / "fyp-benchmark-results.json").exists()
    assert "--allow-cpu" in capsys.readouterr().err


def test_allow_cpu_records_cpu_rows_deliberately(tmp_path: Path) -> None:
    code, out = _cli(tmp_path, lambda model: _placement("cpu"), "--allow-cpu")
    assert code == 0
    results = json.loads((out / "fyp-benchmark-results.json").read_text("utf-8"))
    assert results["compute_policy"]["allow_cpu"] is True
    assert results["configurations"]["baseline"][0]["compute"]["before"]["device"] == (
        "cpu"
    )


def test_gpu_lost_mid_run_invalidates_row(tmp_path: Path) -> None:
    placements = iter(["gpu", "cpu"])
    code, out = _cli(tmp_path, lambda model: _placement(next(placements)))
    assert code == 1
    results = json.loads((out / "fyp-benchmark-results.json").read_text("utf-8"))
    assert results["configurations"]["baseline"][0]["status"] == "invalid_cpu_fallback"
    summary = (out / "fyp-benchmark-summary.md").read_text("utf-8")
    assert "gpu 100% → cpu" in summary


def test_unloaded_model_counts_as_cpu(tmp_path: Path) -> None:
    code, _ = _cli(tmp_path, lambda model: None)
    assert code == 2


def test_injected_provider_without_probe_is_not_probed(tmp_path: Path) -> None:
    code, out = _cli(tmp_path, None)
    assert code == 0
    results = json.loads((out / "fyp-benchmark-results.json").read_text("utf-8"))
    assert results["compute_policy"]["probe"] is False
    assert results["configurations"]["baseline"][0]["compute"]["before"] == {
        "device": "not_probed"
    }
