"""PHX-091 configuration (2) runner tests (scripted model only; no network/models).

Every PHOENIX collaborator is real; the model is a scripted provider answering
by the system prompt's role marker, as in tests/test_phoenix_runtime.py.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace, TracebackType
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
    ProviderHealth,
    ProviderHealthStatus,
    TokenUsage,
)
from phoenix.models.registry import ModelCapabilityRegistry

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "benchmarks" / "fyp" / "tasks-v1.toml"
MODELS = ("qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b", "qwen2.5-coder:7b")
PASS = '{"checks":[{"criterion":"Correct","passed":true,"evidence_summary":"Fine."}]}'
FAIL = '{"checks":[{"criterion":"Correct","passed":false,"evidence_summary":"No."}]}'


def _load(name: str, file: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


fyp = _load("phoenix_fyp_benchmark_c2", "fyp_benchmark.py")
multi = _load("phoenix_fyp_multi_agent", "fyp_multi_agent.py")


def _cases() -> list[tuple[str, str]]:
    """(prompt, correct answer) in suite order; unscored cases get free text."""
    suite, _ = fyp.load_task_set(TASKS)
    return [
        (case.messages[-1].text, case.scoring.expected or "A short plan.")
        for case in suite.cases
    ]


class ScriptedProvider(ModelProvider):
    """Answers PHOENIX roles by marker; final answers follow the suite order.

    stream() answers the raw task prompt, so the same fake also drives the
    configuration (1) harness for --compare tests.
    """

    def __init__(
        self,
        wrong: frozenset[int] = frozenset(),
        direct: frozenset[int] = frozenset(),
    ) -> None:
        super().__init__("ollama")
        self.cases = _cases()
        self.by_prompt = dict(self.cases)
        self.wrong = wrong
        self.direct = direct  # case indices triage sends DIRECT; others COMPLEX
        self.mission = -1
        self.replies: dict[str, str] = {
            "planner": json.dumps({"steps": [{"objective": "Answer it."}]}),
            "assignment": json.dumps(
                {"assignments": [{"step_id": "s1", "domain": "intelligence"}]}
            ),
            "specialist": "Step answer.",
            "verifier": PASS,
            "step_verifier": PASS,
            "final_verifier": PASS,
        }
        self.roles: list[str] = []

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
        return ProviderHealth(
            provider_id=self.provider_id, status=ProviderHealthStatus.HEALTHY
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        return ModelCapabilities(
            provider_id=self.provider_id,
            model_id=model_id,
            supports_streaming=True,
            supports_structured_output=False,
            supports_tool_use=True,
            supports_vision=False,
            supports_cancellation=True,
            context_limit_tokens=16384,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        role = request.messages[0].text.splitlines()[0].removeprefix("PHOENIX ROLE: ")
        previous = self.roles[-1] if self.roles else None
        self.roles.append(role)
        # Missions run in suite order; each starts with triage, or with planner
        # or direct when the path is forced.
        if role == "triage" or (role in ("planner", "direct") and previous != "triage"):
            self.mission += 1
        index = self.mission % len(self.cases)
        answer = "wrong" if index in self.wrong else self.cases[index][1]
        if role == "triage":
            text = json.dumps({"path": "direct" if index in self.direct else "complex"})
        elif role in ("final_answer", "direct"):
            text = answer
        else:
            text = self.replies[role]
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
        yield GenerationChunk(
            provider_id=self.provider_id,
            model_id=request.model_id,
            operation_id=request.operation_id,
            text=self.by_prompt.get(request.messages[-1].text, "unknown"),
            is_final=True,
            usage=TokenUsage(input_tokens=10, output_tokens=4),
        )

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            provider_id=self.provider_id,
            operation_id=operation_id,
            status=CancellationStatus.NOT_FOUND,
        )


def scripted_factory(provider: ScriptedProvider) -> Any:
    @asynccontextmanager
    async def factory(limits: RuntimeLimits) -> AsyncIterator[Any]:
        store = ModelCapabilityRegistry()
        for model in MODELS:
            store.register(await provider.capabilities(model))
        yield multi.LiveRuntime(
            PhoenixRuntime(
                providers=(provider,),
                capability_registry=store,
                lifecycle=ModelLifecycleManager(),
                limits=limits,
            ),
            MODELS,
        )

    return factory


class FixedProbe:
    def sample(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            system_ram_used_bytes=8_000_000_000,
            gpus=(GpuReading(index=0, name="fixture", used_mib=4000, total_mib=6144),),
        )


def _run(
    tmp_path: Path, provider: ScriptedProvider, *extra: str
) -> tuple[int, dict[str, Any], Path]:
    out = tmp_path / "out"
    code = multi.main(
        ["--out", str(out), *extra],
        runtime_factory=scripted_factory(provider),
        resource_probe=FixedProbe(),
    )
    path = out / multi.RESULTS_NAME
    results = json.loads(path.read_text("utf-8")) if path.exists() else {}
    return code, results, out


def test_forced_complex_runs_full_pipeline_for_every_case(tmp_path: Path) -> None:
    provider = ScriptedProvider()
    code, results, _ = _run(tmp_path, provider, "--path", "complex")
    assert code == 0 and "triage" not in provider.roles
    s = results["summary"]
    assert s["measured_missions"] == 16
    assert s["outcomes"] == {
        "completed": 16,
        "failed": 0,
        "cancelled": 0,
        "waiting_permission": 0,
        "error": 0,
    }
    assert (s["score_accuracy"], s["success_rate"], s["scorable_missions"]) == (
        1.0,
        1.0,
        14,
    )
    # One-step plan: planner, assignment, specialist, verifier, final, final verifier.
    assert provider.roles[:6] == [
        "planner",
        "assignment",
        "specialist",
        "verifier",
        "final_answer",
        "final_verifier",
    ]
    gens = s["generations_per_mission"]
    assert (gens["median"], gens["total"]) == (6, 96)
    assert (s["input_tokens"], s["output_tokens"]) == (16 * 30, 16 * 18)
    assert (s["levi"]["passed"], s["levi"]["failed"]) == (32, 0)
    assert s["prompt_set_versions"] == [multi.prompts.PROMPT_SET_VERSION]
    assert s["roles"]["planner"]["thinking"] == "off"
    assert s["roles"]["specialist"]["thinking"] == "model_default"
    assert s["roles"]["planner"]["models"] == {"qwen3.5:9b": 16}
    assert s["mission_latency_ms"]["count"] == 16
    assert (s["max_ram_used_bytes"], s["max_gpu_used_mib"]) == (8_000_000_000, 4000)
    assert results["path"] == {"requested": "complex", "used": "complex"}
    assert (s["paths"], s["triage_outcomes"]) == ({"complex": 16}, {"client": 16})
    # Every COMPLEX completion here reached COMPLETED after LEVI: verified.
    assert all(t["verified"] for t in results["trials_detail"])
    assert not any(t["direct_fallback"] for t in results["trials_detail"])
    assert (s["levi_verified_passes"], s["direct_fallbacks"]) == (14, 0)
    assert s["success_rate_excl_fallbacks"] == s["success_rate"] == 1.0
    # PHX-109 traces record recoveries; this clean run needed none.
    assert all(t["recoveries"]["count"] == 0 for t in results["trials_detail"])
    assert not any(t["recoveries"]["direct_fallback"] for t in results["trials_detail"])
    assert results["trials"] == {"warmup": 0, "measured": 1}


def test_default_triage_records_decided_path_and_outcome(tmp_path: Path) -> None:
    # Triage sends the three instruction cases DIRECT and the rest COMPLEX.
    provider = ScriptedProvider(direct=frozenset({0, 1, 2}))
    code, results, out = _run(tmp_path, provider)
    assert code == 0
    assert results["path"] == {"requested": "triage", "used": "triage"}
    s = results["summary"]
    assert s["paths"] == {"complex": 13, "direct": 3}
    assert s["triage_outcomes"] == {"decided": 16}
    assert s["score_accuracy"] == 1.0 and s["outcomes"]["completed"] == 16
    first = results["trials_detail"][0]
    assert (first["path"], first["triage_outcome"], first["triage_reason"]) == (
        "direct",
        "decided",
        None,
    )
    assert first["generations"] == 2 and first["levi_passed"] == 0  # triage, direct
    assert (first["verified"], first["direct_fallback"]) == (False, False)
    assert s["by_path"]["direct"] == {
        "missions": 3,
        "completed": 3,
        "scorable": 3,
        "passed": 3,
        "verification": "unverified by design",
        "pass_rate": 1.0,
    }
    assert s["by_path"]["complex"]["levi_verified"] == 13
    assert s["levi_verified_passes"] == 11  # 2 of the 13 are unscored planning
    assert results["trials_detail"][3]["generations"] == 7
    assert provider.roles[:3] == ["triage", "direct", "triage"]
    assert s["roles"]["triage"] == {
        "thinking": "off",
        "generations": 16,
        "models": {"qwen3.5:4b": 16},
    }
    summary = (out / multi.SUMMARY_NAME).read_text("utf-8")
    assert "missions by path complex 13, direct 3" in summary
    assert "triage outcomes decided 16" in summary


def test_failures_feed_codes_levi_counts_and_category_accuracy(
    tmp_path: Path,
) -> None:
    provider = ScriptedProvider(wrong=frozenset({0}))  # instruction-exact-token
    code, results, _ = _run(tmp_path, provider, "--warmup", "1")
    assert code == 0
    s = results["summary"]
    assert s["measured_missions"] == 16 and len(results["trials_detail"]) == 32
    assert s["passed_scores"] == 13 and s["score_accuracy"] == pytest.approx(13 / 14)
    assert s["category_accuracy"]["instruction"] == {
        "passed": 2,
        "n": 3,
        "accuracy": pytest.approx(2 / 3),
    }
    assert s["failure_codes"] == {}

    failing = ScriptedProvider()
    failing.replies["final_verifier"] = FAIL
    _, failed, _ = _run(tmp_path / "f", failing)
    fs = failed["summary"]
    assert fs["outcomes"]["failed"] == 16 and fs["outcomes"]["completed"] == 0
    assert fs["failure_codes"] == {"verification_failed": 16}
    assert (fs["levi"]["passed"], fs["levi"]["failed"]) == (16, 16)
    assert fs["levi"]["missions_with_failed_check"] == 16
    assert fs["score_accuracy"] == 0.0 and fs["mission_latency_ms"]["count"] == 0


def test_runtime_defect_is_recorded_and_fails_the_run(tmp_path: Path) -> None:
    @asynccontextmanager
    async def broken(limits: RuntimeLimits) -> AsyncIterator[Any]:
        class Boom:
            async def run(self, *args: Any) -> Any:
                raise RuntimeError("secret text must not be recorded")

        yield multi.LiveRuntime(Boom(), MODELS)

    out = tmp_path / "o"
    code = multi.main(["--out", str(out)], runtime_factory=broken)
    assert code == 1
    text = (out / multi.RESULTS_NAME).read_text("utf-8")
    results = json.loads(text)
    assert results["summary"]["outcomes"]["error"] == 16
    assert results["summary"]["failure_codes"] == {"runtime_defect": 16}
    assert results["trials_detail"][0]["error_type"] == "RuntimeError"
    assert "secret text" not in text


def test_output_schema_privacy_and_metric_coverage(tmp_path: Path) -> None:
    code, results, out = _run(tmp_path, ScriptedProvider())
    assert code == 0
    assert results["schema_version"] == 1 and results["tool"] == multi.TOOL
    assert results["fyp_configuration"] == "2"
    assert results["suite"]["version"] == "1.0.0"
    assert len(results["suite"]["sha256"]) == 64
    assert results["model_policy"]["models_registered"] == list(MODELS)
    assert results["model_policy"]["structured_roles_think"] is False
    assert "not one model" in results["model_policy"]["note"]
    assert results["not_runnable"][0]["fyp_configuration"] == "3"
    assert results["not_runnable"][0]["reason"]
    trial = results["trials_detail"][0]
    assert trial["answer_chars"] == len("PHOENIX_READY")
    assert len(trial["answer_sha256"]) == 64
    # Privacy (ADR-029): no answers or prompts verbatim, anywhere.
    dumped = (out / multi.RESULTS_NAME).read_text("utf-8")
    dumped += (out / multi.SUMMARY_NAME).read_text("utf-8")
    # Random ids and digests can contain a short numeric answer by chance
    # ("...-2397-..."); drop them, then look for the answer as a whole token.
    uuid = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    scrubbed = re.sub(r"[0-9a-fA-F]{16,}", "", re.sub(uuid, "", dumped))
    for prompt, answer in _cases():
        assert prompt not in dumped
        assert len(answer) < 4 or not re.search(
            rf"(?<![0-9A-Za-z]){re.escape(answer)}(?![0-9A-Za-z])", scrubbed
        )
    # Every docs/24 metric: measured or NOT_RUNNABLE with a reason.
    doc = (ROOT / "docs" / "24_TEST_EVALUATION.md").read_text(encoding="utf-8")
    section = doc.split("## Metrics")[1].split("##")[0]
    documented = [
        line[2:].strip().replace("/", "_").replace(" ", "_").lower()
        for line in section.splitlines()
        if line.startswith("- ")
    ]
    rows = {row["id"]: row for row in results["metrics"]}
    assert len(rows) == len(documented) == 12
    for row in rows.values():
        assert row["status"] in {"MEASURED", "NOT_RUNNABLE"}
        assert (row.get("source") or row.get("reason") or "").strip()
    assert rows["routing_accuracy"]["status"] == "NOT_RUNNABLE"
    assert "PHX-105" in rows["routing_accuracy"]["reason"]
    assert "fault-injected" in rows["verification_catch_rate"]["reason"]
    assert "scripts/metric_benchmark.py" in rows["verification_catch_rate"]["reason"]
    for metric, ticket in (
        ("memory_retrieval_accuracy", "PHX-110"),
        ("research_evidence_quality", "PHX-111"),
        ("computer_control_completion", "PHX-112"),
    ):
        assert ticket in rows[metric]["reason"]
        assert "benchmarks/metrics/" in rows[metric]["reason"]
    measured = {k for k, r in rows.items() if r["status"] == "MEASURED"}
    # recovery_success is MEASURED now that PHX-109 traces carry recoveries;
    # PHX-117 measures permission prompts from the mission outcomes.
    assert measured == {
        "task_success",
        "ttft_latency",
        "ram_vram",
        "token_usage",
        "recovery_success",
        "user_intervention_count",
    }
    assert results["summary"]["interventions"] == {
        "permission_prompts": 0,
        "per_mission": 0.0,
    }
    summary = (out / multi.SUMMARY_NAME).read_text("utf-8")
    assert "configuration (2)" in summary and "NOT RUNNABLE" in summary


def _config1(tmp_path: Path, think: str) -> Path:
    out = tmp_path / f"c1-{think}"
    code = fyp.main(
        ["--out", str(out), "--model", "T1=qwen3.5:4b", "--model", "T0=qwen3.5:2b"]
        + ["--warmup", "0", "--runs", "1", "--think", think],
        provider_factory=lambda: ScriptedProvider(),
    )
    assert code == 0
    return out / "fyp-benchmark-results.json"


def test_compare_builds_side_by_side_table(tmp_path: Path) -> None:
    off, on = _config1(tmp_path, "off"), _config1(tmp_path, "default")
    code, results, out = _run(
        tmp_path, ScriptedProvider(), "--compare", str(off), "--compare", str(on)
    )
    assert code == 0
    rows = results["comparison"]
    assert [r["fyp_configuration"] for r in rows] == ["1", "1", "1", "1", "2"]
    assert [r["thinking"] for r in rows] == [
        "off",
        "off",
        "model default",
        "model default",
        "mixed by role",
    ]
    assert sum(r["best"] for r in rows) == 2 and rows[0]["best"]
    assert all(r["same_task_set"] for r in rows)
    assert rows[0]["output_tokens_per_task"] == 4.0
    assert rows[-1]["output_tokens_per_task"] == 21.0  # triage + 6 COMPLEX turns
    assert rows[-1]["score_accuracy"] == 1.0
    table = (out / multi.COMPARISON_NAME).read_text("utf-8")
    assert "| (2) | PHOENIX multi-agent (triage) | mixed by role | 100% |" in table
    assert "(best)" in table and "not one model" in table
    assert "success excludes 0 unverified DIRECT fallback(s)" in table
    assert "completed, incl. unverified fallbacks: 100%" in table
    assert "| yes | c1-off |" in table and "| yes | c1-default |" in table
    assert "Comparison with configuration (1)" in (out / multi.SUMMARY_NAME).read_text(
        "utf-8"
    )

    # Re-render later from saved results, without running missions again. An
    # older file (no triage or verification fields per trial) is re-derived
    # from its trials, and the source file is never overwritten.
    saved = json.loads((out / multi.RESULTS_NAME).read_text("utf-8"))
    for trial in saved["trials_detail"]:
        for key in ("triage_outcome", "triage_reason", "recoveries", "verified"):
            del trial[key]
        del trial["direct_fallback"]
    saved["summary"] = {"stale": True}
    older_dir = tmp_path / "older"
    older_dir.mkdir()
    older = older_dir / multi.RESULTS_NAME
    older.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(SystemExit):
        multi.main(["--out", str(older_dir), "--from-results", str(older)])
    again = tmp_path / "again"
    code = multi.main(
        ["--out", str(again), "--from-results", str(older), "--compare", str(off)]
    )
    assert code == 0
    assert "| (2) |" in (again / multi.COMPARISON_NAME).read_text("utf-8")
    rendered = json.loads((again / multi.RESULTS_NAME).read_text("utf-8"))
    assert rendered["summary"]["success_rate_excl_fallbacks"] == 1.0
    assert rendered["rerendered"]["from"] == str(older)
    assert json.loads(older.read_text("utf-8"))["summary"] == {"stale": True}
    assert "missions by path complex 16" in (again / multi.SUMMARY_NAME).read_text(
        "utf-8"
    )


def test_compare_rejects_wrong_file_before_running(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"tool": "other"}), encoding="utf-8")
    provider = ScriptedProvider()
    with pytest.raises(SystemExit):
        _run(tmp_path, provider, "--compare", str(bad))
    assert provider.roles == []


def test_dispatch_flags_and_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert fyp.main(["--config", "phoenix-multi-agent", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "16 cases x (0 warm-up + 1 measured) = 16 missions on path triage" in out
    assert multi.prompts.PROMPT_SET_VERSION in out and "qwen3.5:9b" in out
    assert (
        fyp.main(
            ["--config", "phoenix-multi-agent", "--dry-run", "--path"]
            + ["complex", "--warmup", "1", "--runs", "2"]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "48 missions on path complex;" in out
    assert fyp.main(["--dry-run"]) == 0  # the baseline is unchanged
    assert "tiers: T0=qwen3.5:2b" in capsys.readouterr().out
    for argv in (
        ["--config", "nope", "--dry-run"],
        ["--config", "phoenix-multi-agent", "--path", "other", "--dry-run"],
        ["--config", "phoenix-multi-agent", "--runs", "0", "--dry-run"],
        ["--config", "phoenix-multi-agent", "--think", "off", "--dry-run"],
    ):
        with pytest.raises(SystemExit):
            fyp.main(argv)


@pytest.mark.parametrize(
    ("path", "first", "decided", "outcome"),
    [
        ("direct", "direct", "direct", "client"),
        ("complex", "planner", "complex", "client"),
        ("triage", "triage", "complex", "decided"),
    ],
)
def test_path_flag_selects_and_records_path(
    tmp_path: Path, path: str, first: str, decided: str, outcome: str
) -> None:
    provider = ScriptedProvider()
    code, results, _ = _run(tmp_path, provider, "--path", path)
    assert code == 0 and provider.roles[0] == first
    assert results["path"] == {"requested": path, "used": path}
    assert {t["path"] for t in results["trials_detail"]} == {decided}
    assert {t["triage_outcome"] for t in results["trials_detail"]} == {outcome}
    assert results["summary"]["score_accuracy"] == 1.0


def test_cpu_only_placement_refused_before_any_mission(tmp_path: Path) -> None:
    provider = ScriptedProvider()
    code = multi.main(
        ["--out", str(tmp_path / "o")],
        runtime_factory=scripted_factory(provider),
        compute_probe=lambda model: fyp.compute_from_ps({"size": 1, "size_vram": 0}),
    )
    assert code == 2 and provider.roles == []
    code, results, _ = _run(tmp_path, provider, "--allow-cpu")
    assert code == 0 and results["compute"]["allow_cpu"] is True


def test_preflight_models_follow_the_runtime_tier_table() -> None:
    assert multi.tier_models() == (
        "qwen3.5:4b",
        "qwen3.5:2b",
        "qwen3.5:9b",
        "qwen2.5-coder:7b",
    )
    assert multi.tier_table_json()["verifier"][0] == "qwen3.5:9b"


def _trial(
    path: str | None,
    kind: str,
    states: list[str],
    score: str | None,
    recoveries: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A stored trial record as the runner writes it (closed values only)."""
    return {
        "case_id": "c",
        "category": "reasoning",
        "kind": "measured",
        "run_index": 0,
        "scoring_mode": "contains",
        "mission_kind": kind,
        "failure_code": None if kind == "completed" else "responder_output",
        "responder_error": None if kind == "completed" else "schema",
        "score": score,
        "mission_ms": 10.0,
        "prompt_set_version": "phoenix-prompts-v4",
        "path": path,
        "triage_outcome": "decided",
        "states": states,
        "generations": 1,
        "input_tokens": 1,
        "output_tokens": 1,
        "generations_without_usage": 0,
        "levi_passed": 0,
        "levi_failed": 0,
        "generation_records": [],
        "resources": {"before": None, "after": None},
        "recoveries": recoveries,
    }


DONE = ["created", "planning", "ready", "running", "verifying", "completed"]
PLAN_FAILED = ["created", "planning", "failed"]


def test_completed_mission_with_failed_task_is_a_direct_fallback() -> None:
    fallback = _trial("complex", "completed", PLAN_FAILED, "pass")
    assert multi.mission_class(fallback) == "direct_fallback"
    verified = _trial("complex", "completed", DONE, "pass")
    assert multi.mission_class(verified) == "levi_verified"
    direct = _trial("direct", "completed", [], "pass")
    assert multi.mission_class(direct) == "unverified_direct"
    failed = _trial("complex", "failed", PLAN_FAILED, None)
    assert multi.mission_class(failed) == "not_completed"
    # PHX-109 traces flag the fallback in their recoveries as well.
    flagged = _trial("complex", "completed", [], "pass", {"direct_fallback": True})
    assert multi.mission_class(flagged) == "direct_fallback"


def test_summary_reports_success_with_and_without_fallbacks() -> None:
    trials = [
        _trial("complex", "completed", DONE, "pass"),  # LEVI-verified
        _trial("complex", "completed", PLAN_FAILED, "pass"),  # DIRECT fallback
        _trial("complex", "failed", PLAN_FAILED, None),
        _trial("direct", "completed", [], "pass"),  # unverified by design
        _trial("direct", "completed", [], "fail"),
    ]
    s = multi.summarize(trials)
    assert s["success_rate"] == pytest.approx(4 / 5)
    assert s["success_rate_label"] == "completed, incl. unverified fallbacks"
    assert s["success_rate_excl_fallbacks"] == pytest.approx(3 / 5)
    assert s["score_accuracy"] == pytest.approx(3 / 5)
    assert s["score_accuracy_excl_fallbacks"] == pytest.approx(2 / 5)
    assert (s["direct_fallbacks"], s["levi_verified_passes"]) == (1, 1)
    assert s["by_path"]["complex"] == {
        "missions": 3,
        "completed": 2,
        "scorable": 3,
        "passed": 2,
        "levi_verified": 1,
        "direct_fallbacks": 1,
        "failed": 1,
        "pass_rate": pytest.approx(2 / 3),
    }
    assert s["by_path"]["direct"]["verification"] == "unverified by design"
    assert s["recoveries"] is None
    rows = {row["id"]: row for row in multi.metric_rows(s)}
    assert rows["recovery_success"]["status"] == "NOT_RUNNABLE"
    assert "PHX-109" in rows["recovery_success"]["reason"]
    assert "no retries" not in rows["recovery_success"]["reason"]
    lines = "\n".join(multi._verification_lines(s))
    assert "Success excluding unverified DIRECT fallbacks: 60%" in lines
    assert "completed, incl. unverified fallbacks: 80% (1 fallback(s))" in lines


def test_recoveries_are_counted_by_closed_value_and_measured() -> None:
    record = SimpleNamespace(
        recovery_class=SimpleNamespace(value="responder_output"),
        action=SimpleNamespace(value="switch_agent"),
        outcome=SimpleNamespace(value="recovered"),
        direct_fallback=True,
    )
    exhausted = SimpleNamespace(
        recovery_class=SimpleNamespace(value="provider_error"),
        action=None,
        outcome=SimpleNamespace(value="exhausted"),
        direct_fallback=False,
    )
    counts = multi.recovery_counts(SimpleNamespace(recoveries=(record, exhausted)))
    assert counts == {
        "count": 2,
        "by_class": {"provider_error": 1, "responder_output": 1},
        "by_action": {"none": 1, "switch_agent": 1},
        "by_outcome": {"exhausted": 1, "recovered": 1},
        "direct_fallback": True,
    }
    assert multi.recovery_counts(SimpleNamespace()) is None  # pre-PHX-109 trace
    s = multi.summarize([_trial("complex", "completed", [], "pass", counts)])
    assert s["direct_fallbacks"] == 1
    assert s["recoveries"]["actions"] == 1 and s["recoveries"]["recovered"] == 1
    assert s["recoveries"]["recovery_success"] == 1.0
    rows = {row["id"]: row for row in multi.metric_rows(s)}
    assert rows["recovery_success"]["status"] == "MEASURED"
