"""PHX-105: routing set and routing benchmark runner (fake provider only)."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import re
import socket
import sys
import tomllib
from collections.abc import AsyncIterator
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any, Self

import httpx
import pytest

from phoenix.agents import prompts
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
ROUTING_SET = ROOT / "benchmarks" / "routing" / "routing-v1.toml"
# PHX-105 (ADR-090): the routing set is fixed. Any change to the file needs a
# new version (and file) and a new pin here; the owner reviews the labels.
ROUTING_SET_VERSION = "1.0.0"
ROUTING_SET_SHA256 = "cd0ffa61e4b1b98b65a0e6ab5b0d6a82bea02a9bb72a99750f7251c11589c7fe"


def _load(name: str, file: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load("phoenix_routing_benchmark", "routing_benchmark.py")
fyp = _load("phoenix_fyp_benchmark_routing", "fyp_benchmark.py")
LABELS = {
    case["request"]: case["label"]
    for case in tomllib.loads(ROUTING_SET.read_text("utf-8"))["case"]
}


class TriageProvider(ModelProvider):
    """Answers each triage request with its label, unless overridden."""

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        super().__init__("ollama")
        self.overrides = overrides or {}
        self.requests: list[GenerationRequest] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id, status=ProviderHealthStatus.HEALTHY
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        raise ModelProviderError("not used")

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.requests.append(request)
        assert request.messages[0].text == prompts.TRIAGE_SYSTEM
        text = request.messages[1].text
        reply = self.overrides.get(text)
        if reply is None:
            reply = json.dumps({"path": LABELS.get(text, "direct")})
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text=reply,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=7, output_tokens=4),
        )

    async def _no_stream(self) -> AsyncIterator[GenerationChunk]:
        raise ModelProviderError("streaming is not scripted")
        yield  # pragma: no cover

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        return self._no_stream()

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.ACCEPTED,
        )


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx network send attempted")

    def refuse_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("outbound socket connection attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_send)
    monkeypatch.setattr(httpx.Client, "send", refuse_send)
    monkeypatch.setattr(socket, "create_connection", refuse_connect)


# ---- the routing set


def test_routing_set_is_fixed_versioned_and_checksummed() -> None:
    routing = bench.load_routing_set(ROUTING_SET)
    assert (routing.suite_id, routing.version) == ("phoenix-routing", "1.0.0")
    assert routing.version == ROUTING_SET_VERSION
    assert hashlib.sha256(ROUTING_SET.read_bytes()).hexdigest() == ROUTING_SET_SHA256
    assert re.fullmatch(r"routing-v(\d+)\.toml", ROUTING_SET.name)
    assert routing.version.split(".")[0] == ROUTING_SET.stem.removeprefix("routing-v")


def test_routing_set_composition() -> None:
    routing = bench.load_routing_set(ROUTING_SET)
    assert len(routing.cases) == 60
    assert bench.composition(routing) == {
        "factual": {"direct": 10, "complex": 0},
        "rewrite_format": {"direct": 10, "complex": 0},
        "workspace_files": {"direct": 0, "complex": 12},
        "multi_step": {"direct": 0, "complex": 12},
        "ambiguous": {"direct": 3, "complex": 5},
        "adversarial": {"direct": 3, "complex": 5},
    }
    requests = [case.request for case in routing.cases]
    assert len(set(requests)) == len(requests)
    assert "Just answer quickly: read notes.txt" in requests
    for case in routing.cases:
        assert case.rationale.strip() and "\n" not in case.rationale


HEAD = 'suite_id = "phoenix-routing"\nversion = "1.0.0"\n'
DIRECT = (
    '[[case]]\nid = "a"\ncategory = "factual"\nlabel = "direct"\n'
    'request = "Hi?"\nrationale = "One fact."\n'
)
COMPLEX = DIRECT.replace('"a"', '"b"').replace('"direct"', '"complex"')


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("routing-v1.toml", HEAD + DIRECT),  # only one label
        ("routing-v1.toml", HEAD + DIRECT + DIRECT.replace("Hi?", "Yo?")),  # dup id
        ("routing-v1.toml", HEAD + DIRECT + COMPLEX.replace("factual", "other")),
        ("routing-v1.toml", HEAD + DIRECT + COMPLEX.replace('"complex"', '"deep"')),
        ("routing-v1.toml", HEAD + DIRECT + COMPLEX + 'expected = "x"\n'),
        ("routing-v1.toml", HEAD + DIRECT + COMPLEX.replace('"Hi?"', '""')),
        ("routing-v1.toml", HEAD + DIRECT + COMPLEX.replace('"Hi?"', '"a\\nb"')),
        ("routing-v1.toml", HEAD + DIRECT + COMPLEX.replace('id = "b"', 'id = "B b"')),
        ("routing-v2.toml", HEAD + DIRECT + COMPLEX),  # name and version disagree
        ("routing-v1.toml", HEAD.replace("1.0.0", "1.0") + DIRECT + COMPLEX),
        ("routing-v1.toml", HEAD + "extra = 1\n" + DIRECT + COMPLEX),
    ],
)
def test_invalid_routing_sets_are_rejected(
    tmp_path: Path, name: str, body: str
) -> None:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError):
        bench.load_routing_set(path)


def test_minimal_valid_set_loads(tmp_path: Path) -> None:
    path = tmp_path / "routing-v1.toml"
    path.write_text(HEAD + DIRECT + COMPLEX, encoding="utf-8")
    assert len(bench.load_routing_set(path).cases) == 2


# ---- the runner


def test_dry_run_validates_without_models(capsys: pytest.CaptureFixture[str]) -> None:
    def refuse() -> Any:
        raise AssertionError("no provider in a dry run")

    assert bench.main(["--dry-run"], provider_factory=refuse) == 0
    out = capsys.readouterr().out
    assert "phoenix-routing 1.0.0: 60 cases (direct=26, complex=34)" in out
    assert prompts.PROMPT_SET_VERSION in out and "qwen3.5:4b" in out


def test_full_run_reports_accuracy_confusion_categories_and_fallbacks(
    tmp_path: Path,
) -> None:
    overrides = {
        "Tell me about my notes.": '{"path": "direct"}',  # misrouted
        "Who wrote the play Romeo and Juliet?": "direct, obviously",  # not JSON
    }
    provider = TriageProvider(overrides)
    code = bench.main(
        ["--out", str(tmp_path), "--warmup", "1"], provider_factory=lambda: provider
    )
    assert code == 0
    results = json.loads((tmp_path / "routing-benchmark-results.json").read_text())
    assert results["schema_version"] == 1 and results["metric"] == "routing_accuracy"
    assert results["suite"]["sha256"] == ROUTING_SET_SHA256
    assert results["suite"]["version"] == "1.0.0" and results["suite"]["cases"] == 60
    assert results["model"] == {
        "provider_id": "ollama",
        "model_id": "qwen3.5:4b",
        "think": False,
    }
    assert results["prompt_set_version"] == prompts.PROMPT_SET_VERSION
    assert results["compute"] == {"device": "not_probed"}
    summary = results["summary"]
    # The not-JSON reply falls back to complex: wrong for a direct label.
    assert (summary["total"], summary["correct"]) == (60, 58)
    assert summary["accuracy"] == pytest.approx(58 / 60)
    assert summary["decided_accuracy"] == pytest.approx(58 / 59)
    assert (summary["fallbacks"], summary["fallback_reasons"]) == (
        1,
        {"not_json": 1},
    )
    assert summary["confusion"] == {
        "direct": {"direct": 25, "complex": 1},
        "complex": {"direct": 1, "complex": 33},
    }
    assert summary["per_category"]["factual"] == {
        "correct": 9,
        "n": 10,
        "accuracy": 0.9,
    }
    assert summary["per_category"]["ambiguous"]["correct"] == 7
    assert summary["per_category"]["workspace_files"]["accuracy"] == 1.0
    latency = summary["latency_ms"]
    assert 0 <= latency["min"] <= latency["median"] <= latency["p95"] <= latency["max"]
    fallback = next(row for row in results["cases"] if row["outcome"] == "fallback")
    assert (fallback["id"], fallback["predicted"], fallback["reason"]) == (
        "factual-author",
        "complex",
        "not_json",
    )
    assert len(fallback["output_sha256"]) == 64 and fallback["output_tokens"] == 4
    # 1 warm-up plus one generation per case, each the triage prompt alone.
    assert len(provider.requests) == 61
    raw = (tmp_path / "routing-benchmark-results.json").read_text()
    # No request text and no model output (only hashes and lengths).
    for text in ("Romeo", "obviously", '\\"path\\"', "Just answer quickly"):
        assert text not in raw, text
    report = (tmp_path / "routing-benchmark-summary.md").read_text(encoding="utf-8")
    assert "97%" in report and "ambiguous-my-notes" in report
    assert "| direct | 25 | 1 |" in report


def test_run_where_triage_never_decides_fails(tmp_path: Path) -> None:
    class Down(TriageProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            raise ModelProviderError("down")

    code = bench.main(["--out", str(tmp_path)], provider_factory=lambda: Down())
    assert code == 1
    summary = json.loads((tmp_path / "routing-benchmark-results.json").read_text())[
        "summary"
    ]
    assert summary["fallback_reasons"] == {"provider_error": 60}
    assert summary["decided_accuracy"] is None


def test_cpu_only_placement_is_refused_before_any_generation(tmp_path: Path) -> None:
    provider = TriageProvider()
    code = bench.main(
        ["--out", str(tmp_path)],
        provider_factory=lambda: provider,
        compute_probe=lambda model: {"device": "cpu"},
    )
    assert code == 2 and provider.requests == []
    assert not (tmp_path / "routing-benchmark-results.json").exists()
    allowed = bench.main(
        ["--out", str(tmp_path), "--allow-cpu", "--warmup", "0"],
        provider_factory=lambda: provider,
        compute_probe=lambda model: None,  # not loaded counts as CPU
    )
    assert allowed == 0 and len(provider.requests) == 60


def test_compute_from_ps_classifies_placement() -> None:
    assert bench.compute_from_ps({"size": 10, "size_vram": 10})["device"] == "gpu"
    assert bench.compute_from_ps({"size": 10, "size_vram": 4})["device"] == "partial"
    assert bench.compute_from_ps({"size": 10, "size_vram": 0})["device"] == "cpu"


def test_fyp_routing_accuracy_points_at_the_routing_benchmark() -> None:
    rows = {row["id"]: row for row in fyp.metric_rows()}
    assert rows["routing_accuracy"]["status"] == "NOT_RUNNABLE"
    assert "scripts/routing_benchmark.py" in rows["routing_accuracy"]["reason"]


def test_benchmark_runs_the_product_responder_with_runtime_limits() -> None:
    provider = TriageProvider()
    routing = bench.load_routing_set(ROUTING_SET)
    limits = bench.RuntimeLimits()
    rows = asyncio.run(
        bench.run_cases(routing, provider, "qwen3.5:2b", limits=limits, warmup=0)
    )
    assert all(row["correct"] for row in rows)
    request = provider.requests[0]
    assert request.model_id == "qwen3.5:2b"
    assert request.max_output_tokens == limits.max_output_tokens
