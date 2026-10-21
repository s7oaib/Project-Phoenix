"""PHX-116 configuration (3) runner tests (scripted models only; no network).

Every PHOENIX collaborator is real (triage, AIZEN, router, LEVI, MAHORAGA); the
Ollama tiers and the Colibri deep tier are scripted providers that answer by
the system prompt's role marker and the task prompt found in the request.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, TracebackType
from typing import Any, Self

import pytest

from phoenix.agents.runtime import PhoenixRuntime, RuntimeLimits
from phoenix.models.benchmark import GpuReading, ResourceSnapshot
from phoenix.models.lifecycle import ModelLifecycleManager
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
from phoenix.models.registry import ModelCapabilityRegistry

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "benchmarks" / "fyp" / "tasks-v1.toml"
MODELS = ("qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b", "qwen2.5-coder:7b")
DEEP = "qwen3.6-colibri"
WRONG = "WRONG-ANSWER-MARKER"
PASS = '{"checks":[{"criterion":"Correct","passed":true,"evidence_summary":"Fine."}]}'
FAIL = '{"checks":[{"criterion":"Correct","passed":false,"evidence_summary":"No."}]}'


def _load(name: str, file: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fyp = _load("phoenix_fyp_benchmark_c3", "fyp_benchmark.py")
deep = _load("phoenix_fyp_adaptive_deep", "fyp_adaptive_deep.py")
CASES = [
    (case.messages[-1].text, case.scoring.expected or "A short plan.")
    for case in fyp.load_task_set(TASKS)[0].cases
]


class Scripted(ModelProvider):
    """Answers by role marker; the task is found by its prompt in the request."""

    def __init__(
        self,
        provider_id: str = "ollama",
        *,
        wrong: frozenset[int] = frozenset(),
        direct: frozenset[int] = frozenset(),
        health: ProviderHealthStatus = ProviderHealthStatus.HEALTHY,
        log: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__(provider_id)
        self.wrong, self.direct, self.status = wrong, direct, health
        self.log = log if log is not None else []

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
        return ModelCapabilities(
            provider_id=self.provider_id,
            model_id=model_id,
            supports_streaming=True,
            supports_structured_output=False,
            supports_tool_use=False,
            supports_vision=False,
            supports_cancellation=True,
        )

    def _index(self, text: str) -> int:
        found = [i for i, (prompt, _) in enumerate(CASES) if prompt in text]
        assert found, "request carries no known task prompt"
        return max(found, key=lambda i: len(CASES[i][0]))

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        role = request.messages[0].text.splitlines()[0].removeprefix("PHOENIX ROLE: ")
        self.log.append((self.provider_id, role))
        joined = "\n".join(m.text for m in request.messages)
        if role == "triage":
            index = self._index(joined)
            text = json.dumps({"path": "direct" if index in self.direct else "complex"})
        elif role in ("final_answer", "direct"):
            index = self._index(joined)
            text = WRONG if index in self.wrong else CASES[index][1]
        elif role == "planner":
            text = json.dumps({"steps": [{"objective": "Answer it."}]})
        elif role == "assignment":
            text = json.dumps(
                {"assignments": [{"step_id": "s1", "domain": "intelligence"}]}
            )
        elif role == "specialist":
            text = "Step answer."
        elif role == "final_verifier":
            text = FAIL if WRONG in joined else PASS
        else:
            text = PASS
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text=text,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=5, output_tokens=3),
        )

    async def stream(
        self, request: GenerationRequest
    ) -> AsyncIterator[GenerationChunk]:
        raise AssertionError("missions do not stream")
        yield  # pragma: no cover

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            provider_id=self.provider_id,
            operation_id=operation_id,
            status=CancellationStatus.NOT_FOUND,
        )


def factory(ollama: Scripted, colibri: Scripted) -> Any:
    @asynccontextmanager
    async def build(
        limits: RuntimeLimits, deep_limits: RuntimeLimits
    ) -> AsyncIterator[Any]:
        store = ModelCapabilityRegistry()
        for model in MODELS:
            store.register(await ollama.capabilities(model))
        store.register(await colibri.capabilities(DEEP))
        lifecycle = ModelLifecycleManager()
        yield deep.AdaptiveRuntimes(
            first=PhoenixRuntime(
                providers=(ollama,),
                capability_registry=store,
                lifecycle=lifecycle,
                limits=limits,
            ),
            deep=PhoenixRuntime(
                providers=(ollama, colibri),
                capability_registry=store,
                lifecycle=lifecycle,
                tier_table=deep.deep_tier_table(DEEP),
                limits=deep_limits,
            ),
            deep_model=DEEP,
            models_registered=(DEEP, *MODELS),
        )

    return build


class FixedProbe:
    def sample(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            system_ram_used_bytes=9_000_000_000,
            gpus=(GpuReading(index=0, name="fixture", used_mib=5000, total_mib=6144),),
        )


def _run(
    tmp_path: Path, ollama: Scripted, colibri: Scripted, *extra: str
) -> tuple[int, dict[str, Any], Path]:
    out = tmp_path / "out"
    code = deep.main(
        ["--out", str(out), *extra],
        runtime_factory=factory(ollama, colibri),
        resource_probe=FixedProbe(),
    )
    path = out / deep.RESULTS_NAME
    return code, json.loads(path.read_text("utf-8")) if path.exists() else {}, out


def _record(kind: str, code: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"mission_kind": kind, "failure_code": code, **extra}


@pytest.mark.parametrize(
    ("record", "reason"),
    [
        (_record("completed", path="complex", states=["completed"]), None),
        (_record("completed", path="direct", states=["completed"]), None),
        # The score is never an escalation signal, even a failing one.
        (_record("completed", path="direct", states=[], score="fail"), None),
        (
            _record("completed", path="complex", states=["running", "failed"]),
            "unverified_fallback",
        ),
        (_record("failed", "verification_failed"), "verification_failed"),
        (_record("failed", "responder_output"), "responder_output"),
        (_record("failed", "prompt_boundary"), None),
        (_record("failed", "timeout"), None),
        (_record("failed", "provider_unavailable"), None),
        (_record("failed", "provider_error"), None),
        (_record("failed", "no_eligible_model"), None),
        (_record("failed", "tool_rejected"), None),
        (_record("failed", "tool_failed"), None),
        (_record("waiting_permission"), None),
        (_record("cancelled"), None),
        (_record("error", "runtime_defect"), None),
    ],
)
def test_escalation_policy_uses_closed_runtime_evidence_only(
    record: dict[str, Any], reason: str | None
) -> None:
    assert deep.escalation_reason(record) == reason


def test_deep_tier_serves_text_answers_and_never_control_roles() -> None:
    table = deep.deep_tier_table(DEEP)
    base = deep.default_tier_table()
    assert table.default[0].provider_id == "colibri"
    assert table.default[0].model_id == DEEP
    assert table.default[1:] == base.default
    assert table.roles["build_engineer"][1:] == base.roles["build_engineer"]
    assert table.roles["build_engineer"][0] == table.default[0]
    for role in ("phoenix", "assignment", "triage", "strategist", "verifier"):
        assert table.roles[role] == base.roles[role]
        assert all(ref.provider_id == "ollama" for ref in table.roles[role])


def test_only_rejected_answers_escalate_and_the_deep_tier_rescues_them(
    tmp_path: Path,
) -> None:
    log: list[tuple[str, str]] = []
    ollama = Scripted(wrong=frozenset({0, 4}), direct=frozenset({1}), log=log)
    colibri = Scripted("colibri", log=log)
    code, results, out = _run(tmp_path, ollama, colibri)
    assert code == 0
    first = results["first_attempt_summary"]
    assert first["failure_codes"] == {"verification_failed": 2}
    assert first["outcomes"]["completed"] == 14
    s = results["summary"]
    esc = s["escalation"]
    assert (esc["escalated"], esc["deep_used"], esc["deep_not_used"]) == (2, 2, 0)
    assert esc["by_reason"] == {"verification_failed": 2}
    assert (esc["rescued_completed"], esc["rescued_levi_verified"]) == (2, 2)
    assert esc["rescued_passes"] == 2
    assert esc["escalation_rate"] == pytest.approx(2 / 16)
    assert s["outcomes"]["completed"] == 16 and s["score_accuracy"] == 1.0
    assert first["score_accuracy"] == pytest.approx(12 / 14)
    # Colibri only produced text specialist and final answers; LEVI and every
    # control role stayed on Ollama, and the DIRECT case never escalated.
    assert {role for provider, role in log if provider == "colibri"} == {
        "specialist",
        "final_answer",
    }
    trials = results["trials_detail"]
    escalated = [t for t in trials if t["escalation"]["escalated"]]
    assert [t["case_id"] for t in escalated] == [
        trials[0]["case_id"],
        trials[4]["case_id"],
    ]
    for trial in escalated:
        brief = trial["escalation"]["first_attempt"]
        assert brief["failure_code"] == "verification_failed"
        assert trial["path"] == "complex" and trial["verified"] is True
        assert trial["mission_ms"] == pytest.approx(
            brief["mission_ms"] + trial["deep_attempt_ms"], abs=0.01
        )
        assert trial["escalation"]["deep_generations"] == 2
    assert trials[1]["path"] == "direct" and not trials[1]["escalation"]["escalated"]
    assert results["deep_tier"]["escalate_failures"] == [
        "responder_output",
        "verification_failed",
    ]
    summary = (out / deep.SUMMARY_NAME).read_text("utf-8")
    assert "Escalated 2 of 16" in summary and "Rescued: 2 completed" in summary
    assert "First attempt = configuration (2), same run" in summary


def test_unhealthy_deep_tier_falls_back_observably(tmp_path: Path) -> None:
    ollama = Scripted(wrong=frozenset({0}))
    colibri = Scripted("colibri", health=ProviderHealthStatus.UNAVAILABLE)
    code, results, _ = _run(tmp_path, ollama, colibri)
    assert code == 0
    esc = results["summary"]["escalation"]
    assert (esc["escalated"], esc["deep_used"], esc["deep_not_used"]) == (1, 0, 1)
    assert esc["rescued_completed"] == 0
    assert results["summary"]["failure_codes"] == {"verification_failed": 1}


def test_refuses_when_the_deep_tier_is_not_served(tmp_path: Path) -> None:
    @asynccontextmanager
    async def missing(
        limits: RuntimeLimits, deep_limits: RuntimeLimits
    ) -> AsyncIterator[Any]:
        raise deep.DeepTierUnavailableError("qwen3.6-colibri is not served")
        yield  # pragma: no cover

    out = tmp_path / "o"
    assert deep.main(["--out", str(out)], runtime_factory=missing) == 2
    assert not (out / deep.RESULTS_NAME).exists()


def test_live_runtimes_share_one_lifecycle_and_refuse_without_colibri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    import phoenix.models.colibri as colibri_module
    import phoenix.models.ollama as ollama_module

    class FakeOllama(Scripted):
        def __init__(self, base_url: str, think: bool | None = None) -> None:
            super().__init__("ollama")
            self.think = think

        async def capabilities(self, model_id: str) -> ModelCapabilities:
            if model_id == "qwen2.5-coder:7b":
                raise ModelProviderError("not installed")
            return await super().capabilities(model_id)

    status = {"value": ProviderHealthStatus.HEALTHY}

    class FakeColibri(Scripted):
        def __init__(self, base_url: str) -> None:
            super().__init__("colibri", health=status["value"])

    monkeypatch.setattr(ollama_module, "OllamaProvider", FakeOllama)
    monkeypatch.setattr(colibri_module, "ColibriProvider", FakeColibri)
    limits = RuntimeLimits()

    async def build() -> Any:
        async with deep.live_runtimes(
            limits, limits, "http://o", "http://c/v1", DEEP
        ) as live:
            return live

    live = asyncio.run(build())
    assert live.first._lifecycle is live.deep._lifecycle  # INV-023
    assert live.models_registered == (DEEP, "qwen3.5:4b", "qwen3.5:2b", "qwen3.5:9b")
    assert set(live.deep._providers) == {"ollama", "colibri"}
    assert set(live.first._providers) == {"ollama"}
    status["value"] = ProviderHealthStatus.UNAVAILABLE
    with pytest.raises(deep.DeepTierUnavailableError):
        asyncio.run(build())


def test_dispatch_dry_run_privacy_and_metrics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert fyp.main(["--config", "phoenix-adaptive-deep", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "16 cases x (0 warm-up + 1 measured) on path triage" in out
    assert "qwen3.6-colibri at http://127.0.0.1:8000/v1" in out
    with pytest.raises(SystemExit):
        fyp.main(["--config", "phoenix-adaptive-deep", "--runs", "0", "--dry-run"])
    code, results, out_dir = _run(
        tmp_path, Scripted(wrong=frozenset({2})), Scripted("colibri")
    )
    assert code == 0
    text = (out_dir / deep.RESULTS_NAME).read_text("utf-8")
    for prompt, answer in CASES:
        assert prompt not in text
    assert WRONG not in text
    metrics = {m["id"]: m for m in results["metrics"]}
    assert "colibri_gate.py" in metrics["colibri_disk_io"]["reason"]
    assert metrics["task_success"]["status"] == "MEASURED"
    assert results["fyp_configuration"] == "3"
    baseline_metrics = {m["id"]: m for m in fyp.metric_rows()}
    assert "colibri_gate.py" in baseline_metrics["colibri_disk_io"]["reason"]
    assert "phoenix-adaptive-deep" in fyp.CONFIG3_REASON
