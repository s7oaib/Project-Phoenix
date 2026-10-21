"""PHX-117: the metric-dataset runner (scripted models only; no network or GPU).

Real components throughout the missions: PhoenixRuntime (AIZEN, router, task
graph, LEVI and its deterministic checks), a FilesystemTool over a temporary
workspace with PermissionEngine and an alembic-migrated audit DB. Only the
model turns are scripted.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

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


def _load(name: str, file: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mb = _load("phoenix_metric_benchmark", "metric_benchmark.py")
md = mb.md


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx network send attempted")

    def refuse_connect(*args: object, **kwargs: object) -> None:
        raise AssertionError("outbound socket connection attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_send)
    monkeypatch.setattr(httpx.Client, "send", refuse_send)
    monkeypatch.setattr(socket, "create_connection", refuse_connect)


def _results(out: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((out / mb.RESULTS_NAME).read_text("utf-8"))
    return data


def _rows(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in results["metrics"]}


# ---- dry run


def test_dry_run_validates_every_set_without_models(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse(limits: object) -> Any:
        raise AssertionError("no runtime in a dry run")

    assert mb.main(["--dry-run"], bench_factory=refuse) == 0
    out = capsys.readouterr().out
    for line in (
        "phoenix-verification 1.0.0: 25 cases",
        "phoenix-interventions 1.0.0: 8 cases",
        "phoenix-memory 1.0.0: 20 cases",
        "phoenix-research 1.0.0: 8 cases",
        "phoenix-computer-control 1.0.0: 10 cases",
    ):
        assert line in out
    for ticket in ("PHX-110", "PHX-111", "PHX-112"):
        assert ticket in out


def test_dry_run_fails_on_a_calibration_mismatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        mb.md,
        "calibration_failures",
        lambda name: ["broken"] if name == "research" else [],
    )
    assert mb.main(["--dry-run"]) == 1
    assert "CALIBRATION FAILED broken" in capsys.readouterr().err


# ---- scripted CI run


@pytest.fixture(scope="module")
def scripted(tmp_path_factory: pytest.TempPathFactory) -> tuple[int, Path]:
    out = tmp_path_factory.mktemp("scripted")
    code = mb.main(["--scripted", "--out", str(out)])
    return code, out


def test_scripted_run_measures_the_deterministic_floor(
    scripted: tuple[int, Path],
) -> None:
    code, out = scripted
    assert code == 0
    results = _results(out)
    assert results["schema_version"] == 1 and results["tool"] == mb.TOOL
    assert results["mode"] == "scripted" and "not a FYP number" in results["note"]
    assert results["compute"] == {"device": "not_probed"}
    s = results["verification"]["summary"]
    # The always-pass scripted verifier leaves only LEVI's code checks.
    assert s["outcomes"] == {
        "caught": 5,
        "escaped": 15,
        "accepted": 5,
        "false_rejection": 0,
        "inconclusive": 0,
    }
    assert s["catch_rate"] == pytest.approx(5 / 20)
    assert s["false_rejection_rate"] == 0.0
    assert s["by_detector"]["deterministic"]["catch_rate"] == 1.0
    assert s["by_detector"]["model"]["catch_rate"] == 0.0
    cases = {row["id"]: row for row in results["verification"]["cases"]}
    for case_id in ("forged-tool-call-id", "forged-tool-request", "forged-tool-grant"):
        row = cases[case_id]
        assert row["outcome"] == "caught"
        assert (row["failure_code"], row["verdicts"]) == (
            "verification_failed",
            ["failed"],
        )
        assert row["verified_steps"] == ["s1"]  # caught at the document step
    two_step = cases["control-two-step"]
    assert two_step["verified_steps"] == ["s1", "s2", "final"]
    assert set(two_step["verdicts"]) == {"passed"}


def test_scripted_run_counts_permission_prompts(scripted: tuple[int, Path]) -> None:
    _, out = scripted
    results = _results(out)
    s = results["interventions"]["summary"]
    assert (s["missions"], s["completed"], s["interventions"]) == (8, 8, 8)
    assert s["excess_interventions"] == 0 and s["missions_at_minimum"] == 8
    cases = {row["id"]: row for row in results["interventions"]["cases"]}
    assert cases["two-files-room-and-lead"]["prompt_actions"] == ["read_text"] * 2
    assert cases["same-file-twice"]["interventions"] == 1
    assert cases["text-only-arithmetic"]["interventions"] == 0


def test_scripted_metric_table_and_privacy(scripted: tuple[int, Path]) -> None:
    _, out = scripted
    results = _results(out)
    rows = _rows(results)
    assert list(rows) == list(mb.METRIC_ORDER)
    assert rows["verification_catch_rate"]["status"] == "MEASURED"
    assert rows["verification_catch_rate"]["value"] == pytest.approx(0.25)
    assert "not a FYP number" in rows["verification_catch_rate"]["source"]
    assert rows["user_intervention_count"]["status"] == "MEASURED"
    assert rows["user_intervention_count"]["value"] == 8
    assert rows["recovery_success"]["status"] == "NOT_RUNNABLE"
    for metric, ticket in (
        ("memory_retrieval_accuracy", "PHX-110"),
        ("research_evidence_quality", "PHX-111"),
        ("computer_control_completion", "PHX-112"),
    ):
        assert rows[metric]["status"] == "NOT_RUNNABLE"
        assert ticket in rows[metric]["reason"]
    suites = results["suites"]
    assert set(suites) == set(md.SETS)
    assert all(len(item["sha256"]) == 64 for item in suites.values())
    # IDs, codes and counts only: no request, answer or file text (ADR-029).
    dumped = (out / mb.RESULTS_NAME).read_text("utf-8")
    dumped += (out / mb.SUMMARY_NAME).read_text("utf-8")
    verification = md.load_verification_set()
    for case in verification.cases:
        for text in (case.request, case.final, *case.step_outputs, case.document_text):
            if text is not None and len(text) > 3:
                assert text not in dumped, text
    for text in md.load_intervention_set().files.values():
        assert text not in dumped
    summary = (out / mb.SUMMARY_NAME).read_text("utf-8")
    assert "Catch rate:** 25%" in summary and "Permission prompts:** 8" in summary
    assert "NOT RUNNABLE" in summary


# ---- live path: producers scripted, LEVI turns sent to the inner provider


class RejectingVerifier(ModelProvider):
    """A stand-in for the live model: every LEVI check fails."""

    def __init__(self) -> None:
        super().__init__("ollama")
        self.roles: list[str] = []

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id, status=ProviderHealthStatus.HEALTHY
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        found: ModelCapabilities = mb.capabilities_for(model_id)
        return found

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.roles.append(mb.role_of(request))
        verdict = {
            "checks": [
                {"criterion": "Grounded", "passed": False, "evidence_summary": "No."}
            ]
        }
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text=json.dumps(verdict),
            finish_reason="stop",
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )

    async def _no_stream(self) -> AsyncIterator[GenerationChunk]:
        raise ModelProviderError("not scripted")
        yield  # pragma: no cover

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        return self._no_stream()

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.ACCEPTED,
        )


def test_live_mode_sends_only_levi_turns_to_the_model(tmp_path: Path) -> None:
    inner = RejectingVerifier()

    @asynccontextmanager
    async def factory(limits: Any) -> AsyncIterator[Any]:
        store = mb.ModelCapabilityRegistry()
        for model in mb.tier_models():
            store.register(mb.capabilities_for(model))
        outer = mb.FaultInjectionProvider(inner)
        with mb._workspace_tool() as (tool, root):
            yield mb.Bench(
                runtime=mb.PhoenixRuntime(
                    providers=(outer,),
                    capability_registry=store,
                    lifecycle=mb.ModelLifecycleManager(),
                    limits=limits,
                    filesystem_tool=tool,
                ),
                tool=tool,
                root=root,
                providers=(outer,),
                models_registered=mb.tier_models(),
            )

    code = mb.main(
        ["--out", str(tmp_path), "--metric", "verification"], bench_factory=factory
    )
    assert code == 0
    results = _results(tmp_path)
    assert results["mode"] == "live" and results["note"] is None
    assert results["interventions"] is None
    s = results["verification"]["summary"]
    # A verifier that rejects everything catches every fault and every control.
    assert s["outcomes"]["caught"] == 20 and s["outcomes"]["false_rejection"] == 5
    assert s["catch_rate"] == 1.0 and s["false_rejection_rate"] == 1.0
    # Forged evidence is final before the model; every other LEVI turn is live.
    assert set(inner.roles) <= mb.VERIFIER_ROLES and inner.roles
    rows = _rows(results)
    assert rows["user_intervention_count"]["status"] == "NOT_RUNNABLE"
    assert "not a FYP number" not in rows["verification_catch_rate"]["source"]


def test_cpu_only_placement_is_refused_before_any_mission(tmp_path: Path) -> None:
    def refuse(limits: object) -> Any:
        raise AssertionError("no mission on a CPU-only placement")

    code = mb.main(
        ["--out", str(tmp_path)],
        bench_factory=refuse,
        compute_probe=lambda model: {"device": "cpu"},
    )
    assert code == 2 and not (tmp_path / mb.RESULTS_NAME).exists()


# ---- capability gate, recovery wiring, forgery harness


def test_capability_gate_needs_the_ticket_done_and_an_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tickets = tmp_path / "FEATURE_TICKETS.md"
    tickets.write_text(
        "- PHX-110 Memory and RAG in missions — TODO\n"
        "- PHX-111 Web research — DONE (live)\n",
        encoding="utf-8",
    )
    assert mb.ticket_status("PHX-110", tickets) == "TODO"
    assert mb.ticket_status("PHX-111", tickets) == "DONE"
    assert mb.ticket_status("PHX-112", tickets) is None
    memory = mb.capability_reason("memory_retrieval_accuracy", tickets)
    assert memory is not None and "PHX-110 is TODO" in memory
    assert "memory-v1.toml" in memory
    research = mb.capability_reason("research_evidence_quality", tickets)
    assert research is not None and "no PHX-117 adapter" in research
    control = mb.capability_reason("computer_control_completion", tickets)
    assert control is not None and "PHX-112 is missing" in control
    monkeypatch.setitem(mb.ADAPTERS, "research_evidence_quality", lambda: None)
    assert mb.capability_reason("research_evidence_quality", tickets) is None
    # The real ticket list parses (PHX-109 shipped before this ticket).
    assert mb.ticket_status("PHX-109") == "DONE"


def test_recovery_success_is_read_from_configuration_2_traces(tmp_path: Path) -> None:
    assert mb.recovery_row(None)["status"] == "NOT_RUNNABLE"
    old = mb.recovery_row({"summary": {"recoveries": None}})
    assert old["status"] == "NOT_RUNNABLE" and "PHX-109" in old["reason"]
    none_taken = mb.recovery_row(
        {
            "summary": {
                "recoveries": {"actions": 0, "recovered": 0, "recovery_success": None}
            }
        }
    )
    assert none_taken["status"] == "MEASURED" and none_taken["value"] is None
    assert "undefined" in none_taken["detail"]["note"]
    config2 = {
        "tool": "phoenix-fyp-multi-agent",
        "summary": {
            "recoveries": {
                "actions": 4,
                "recovered": 3,
                "recovery_success": 0.75,
                "missions_with_recoveries": 3,
            }
        },
    }
    path = tmp_path / "c2.json"
    path.write_text(json.dumps(config2), encoding="utf-8")
    out = tmp_path / "out"
    code = mb.main(
        [
            "--scripted",
            "--metric",
            "interventions",
            "--out",
            str(out),
            "--config2-results",
            str(path),
        ]
    )
    assert code == 0
    row = _rows(_results(out))["recovery_success"]
    assert (row["status"], row["value"]) == ("MEASURED", 0.75)
    assert row["detail"]["note"] is None
    assert _rows(_results(out))["verification_catch_rate"]["status"] == "NOT_RUNNABLE"
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"tool": "phoenix-routing-benchmark"}), "utf-8")
    assert mb.main(["--scripted", "--config2-results", str(wrong)]) == 2


def test_forgery_harness_is_scoped_to_one_tool_and_restored() -> None:
    original = mb.FilesystemTool.execute
    with mb._workspace_tool() as (tool, root):
        with mb.forged_tool(tool, "call_id"):
            assert mb.FilesystemTool.execute is not original
        assert mb.FilesystemTool.execute is original
        with mb.forged_tool(tool, None):
            assert mb.FilesystemTool.execute is original
        assert root.is_dir()
    with pytest.raises(ValueError):
        mb.forge(object(), "effect_applied")


def test_mission_script_replies_in_step_order() -> None:
    case = next(
        c for c in md.load_intervention_set().cases if c.case_id == "one-file-then-text"
    )
    script = mb.intervention_script(case)
    plan = json.loads(script.reply("planner"))
    assert [step["objective"] for step in plan["steps"]] == list(case.steps)
    assignment = json.loads(script.reply("assignment"))["assignments"]
    assert [a["domain"] for a in assignment] == ["documents", "intelligence"]
    assert json.loads(script.reply("document_specialist")) == {
        "tool_request": {"operation": "read_text", "relative_path": "notes.txt"}
    }
    assert script.reply("document_answer") == case.step_outputs[0]
    assert script.reply("specialist") == case.step_outputs[1]
    assert script.reply("final_answer") == case.final
    with pytest.raises(ModelProviderError):
        script.reply("triage")
