"""PHX-016 timing, privacy, resource, lifecycle and cleanup evidence."""

import ast
import asyncio
import hashlib
import inspect
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from typing import Self
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from phoenix.models import benchmark
from phoenix.models.benchmark import (
    PHASE1_SUITE,
    BenchmarkCase,
    BenchmarkConfig,
    BenchmarkError,
    BenchmarkReport,
    BenchmarkRunner,
    BenchmarkSuite,
    FailureCode,
    GpuReading,
    PreflightStatus,
    ReportStatus,
    ResourceSnapshot,
    ScoreOutcome,
    ScoringMode,
    ScoringRule,
    SystemResourceProbe,
    TrialKind,
    TrialStatus,
)
from phoenix.models.lifecycle import ModelLifecycleManager
from phoenix.models.provider import (
    CancellationResult,
    CancellationStatus,
    GenerationChunk,
    GenerationMetrics,
    GenerationRequest,
    GenerationResponse,
    MessageRole,
    ModelCapabilities,
    ModelMessage,
    ModelProvider,
    ModelProviderError,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderUnavailableError,
    TokenUsage,
)


def run(coroutine: Awaitable[None]) -> None:
    async def bounded() -> None:
        await asyncio.wait_for(coroutine, 5)

    asyncio.run(bounded())


def suite(
    mode: ScoringMode = ScoringMode.EXACT, expected: str = "éOK"
) -> BenchmarkSuite:
    return BenchmarkSuite(
        suite_id="fixture-suite",
        version="1",
        cases=(
            BenchmarkCase(
                case_id="case",
                category="fixture",
                messages=(
                    ModelMessage(role=MessageRole.SYSTEM, text="system"),
                    ModelMessage(role=MessageRole.USER, text="private prompt"),
                ),
                scoring=ScoringRule(
                    mode=mode,
                    expected=None if mode is ScoringMode.UNSCORED else expected,
                ),
            ),
        ),
    )


class Probe:
    def __init__(self, values: tuple[ResourceSnapshot, ...] = ()) -> None:
        self.values = iter(values)
        self.calls = 0

    def sample(self) -> ResourceSnapshot:
        self.calls += 1
        return next(self.values, ResourceSnapshot())


class Clock:
    def __init__(
        self, values: tuple[int, ...] = (0, 1_000_000, 10_000_000, 20_000_000)
    ) -> None:
        self.values = iter(values)

    def __call__(self) -> int:
        return next(self.values)


class FakeProvider(ModelProvider):
    def __init__(self, *, streaming: bool = True) -> None:
        super().__init__("fixture")
        self.streaming = streaming
        self.health_status = ProviderHealthStatus.HEALTHY
        self.calls: list[str] = []
        self.requests: list[GenerationRequest] = []
        self.active: set[str] = set()
        self.cancelled: list[str] = []
        self.texts: tuple[str, ...] = ("", "éOK", "")
        self.usage: TokenUsage | None = TokenUsage(input_tokens=3, output_tokens=2)
        self.failures: list[Exception | None] = []
        self.manager: ModelLifecycleManager | None = None
        self.entered = asyncio.Event()
        self.hang = False
        self.final_count = 1
        self.after_final = False
        self.wrong_identity = False
        self.forged = False
        self.closed = 0

    async def health(self) -> ProviderHealth:
        self.calls.append("health")
        return ProviderHealth(
            provider_id=self.provider_id,
            status=self.health_status,
            detail="private provider key body",
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        self.calls.append("capabilities")
        return ModelCapabilities(
            provider_id=self.provider_id,
            model_id=model_id,
            supports_streaming=self.streaming,
            supports_cancellation=True,
            supports_tool_use=True,
            supports_structured_output=False,
            supports_vision=True,
        )

    def begin(self, request: GenerationRequest) -> None:
        if self.manager:
            assert self.manager.is_active(request.operation_id)
        self.requests.append(request)
        self.active.add(request.operation_id)
        self.entered.set()

    async def work(self) -> None:
        if self.hang:
            await asyncio.Event().wait()
        if self.failures:
            failure = self.failures.pop(0)
            if failure:
                raise failure

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.calls.append("generate")
        self.begin(request)
        try:
            await self.work()
            return GenerationResponse(
                provider_id=self.provider_id,
                model_id=request.model_id,
                operation_id=request.operation_id,
                text="".join(self.texts),
                usage=self.usage,
                metrics=GenerationMetrics(ttft_ms=99999, duration_ms=99999),
            )
        finally:
            self.active.remove(request.operation_id)

    async def stream(
        self, request: GenerationRequest
    ) -> AsyncIterator[GenerationChunk]:
        self.calls.append("stream")
        self.begin(request)
        try:
            await self.work()
            for index, text in enumerate(self.texts):
                item = GenerationChunk(
                    provider_id=self.provider_id,
                    model_id="other" if self.wrong_identity else request.model_id,
                    operation_id=request.operation_id,
                    text=text,
                    is_final=index == len(self.texts) - 1 and self.final_count > 0,
                    usage=self.usage if index == len(self.texts) - 1 else None,
                )
                if self.forged:
                    item = item.model_copy(update={"is_final": "yes"})
                yield item
            if self.final_count > 1 or self.after_final:
                yield GenerationChunk(
                    provider_id=self.provider_id,
                    model_id=request.model_id,
                    operation_id=request.operation_id,
                    text="private",
                    is_final=self.final_count > 1,
                )
        finally:
            self.active.remove(request.operation_id)
            self.closed += 1

    async def cancel(self, operation_id: str) -> CancellationResult:
        self.cancelled.append(operation_id)
        return CancellationResult(
            provider_id=self.provider_id,
            operation_id=operation_id,
            status=CancellationStatus.NOT_FOUND,
        )


def test_suite_identity_order_scope_and_freezing() -> None:
    assert (
        PHASE1_SUITE.suite_id == "phoenix-phase1-text"
        and PHASE1_SUITE.version == "1.0.0"
    )
    assert tuple(case.case_id for case in PHASE1_SUITE.cases) == (
        "instruction",
        "arithmetic",
        "format",
        "code",
    )
    assert len(PHASE1_SUITE.cases) == 4
    for value in (
        PHASE1_SUITE,
        PHASE1_SUITE.cases[0],
        PHASE1_SUITE.cases[0].scoring,
        BenchmarkConfig(),
        ResourceSnapshot(),
        GpuReading(index=0, name="gpu", used_mib=0, total_mib=10),
    ):
        field = next(iter(type(value).model_fields))
        with pytest.raises(ValidationError):
            setattr(value, field, getattr(value, field))
        with pytest.raises(ValidationError):
            type(value).model_validate({**value.model_dump(), "extra": "private"})
    assert (
        BenchmarkSuite.model_validate_json(PHASE1_SUITE.model_dump_json())
        == PHASE1_SUITE
    )
    with pytest.raises(ValidationError):
        BenchmarkSuite(suite_id="s", version="1", cases=())
    with pytest.raises(ValidationError):
        BenchmarkSuite(suite_id="s", version="1", cases=PHASE1_SUITE.cases * 2)


@pytest.mark.parametrize("field", ["warmup_runs", "measured_runs", "max_output_tokens"])
@pytest.mark.parametrize("value", [-1, True, None, "1", 1.5])
def test_invalid_integer_config(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        BenchmarkConfig.model_validate({field: value})


@pytest.mark.parametrize(
    "field",
    [
        "per_trial_timeout_seconds",
        "preflight_timeout_seconds",
        "cleanup_timeout_seconds",
        "temperature",
    ],
)
@pytest.mark.parametrize("value", [-1, True, None, "1", float("inf"), float("nan")])
def test_invalid_float_config(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        BenchmarkConfig.model_validate({field: value})


@pytest.mark.parametrize(
    "field",
    [
        "measured_runs",
        "max_output_tokens",
        "per_trial_timeout_seconds",
        "preflight_timeout_seconds",
        "cleanup_timeout_seconds",
    ],
)
def test_invalid_zero_config(field: str) -> None:
    with pytest.raises(ValidationError):
        BenchmarkConfig.model_validate({field: 0})
    assert BenchmarkConfig(warmup_runs=0, temperature=0).warmup_runs == 0


@pytest.mark.parametrize(
    "mode,expected,text,outcome",
    [
        (ScoringMode.EXACT, "OK", "OK", ScoreOutcome.PASS),
        (ScoringMode.EXACT, "OK", "OK\n", ScoreOutcome.FAIL),
        (ScoringMode.CONTAINS, "OK", "before OK after", ScoreOutcome.PASS),
        (ScoringMode.CONTAINS, "OK", "ok", ScoreOutcome.FAIL),
        (ScoringMode.UNSCORED, None, "anything", ScoreOutcome.UNSCORED),
        (ScoringMode.EXACT, "", "", ScoreOutcome.PASS),
    ],
)
def test_scoring(
    mode: ScoringMode, expected: str | None, text: str, outcome: ScoreOutcome
) -> None:
    assert ScoringRule(mode=mode, expected=expected).score(text) is outcome


@pytest.mark.parametrize(
    "value",
    [
        {"mode": "exact"},
        {"mode": "contains", "expected": ""},
        {"mode": "unscored", "expected": "private"},
        {"mode": "other"},
    ],
)
def test_invalid_score_rules(value: object) -> None:
    with pytest.raises(ValidationError):
        ScoringRule.model_validate(value)


def test_stream_timing_usage_privacy_resources_json_and_lifecycle() -> None:
    async def exercise() -> None:
        provider = FakeProvider()
        manager = ModelLifecycleManager()
        provider.manager = manager
        gpu_a = GpuReading(index=0, name="a", used_mib=100, total_mib=1000)
        gpu_b = GpuReading(index=1, name="b", used_mib=200, total_mib=2000)
        probe = Probe(
            (
                ResourceSnapshot(
                    system_ram_used_bytes=100,
                    system_disk_read_bytes=10,
                    system_disk_write_bytes=20,
                    gpus=(gpu_a, gpu_b),
                ),
                ResourceSnapshot(
                    system_ram_used_bytes=200,
                    system_disk_read_bytes=30,
                    system_disk_write_bytes=50,
                    gpus=(gpu_a.model_copy(update={"used_mib": 150}), gpu_b),
                ),
            )
        )
        report = await BenchmarkRunner(
            manager, resource_probe=probe, clock_ns=Clock()
        ).run(
            provider,
            "explicit-model",
            suite=suite(),
            config=BenchmarkConfig(warmup_runs=0, measured_runs=1),
        )
        assert (
            report.status is ReportStatus.SUCCESS
            and report.preflight is PreflightStatus.READY
        )
        assert provider.calls == ["health", "capabilities", "stream"]
        assert report.capabilities and report.capabilities.supports_vision
        assert "vision" in report.not_assessed and "tool_use" in report.not_assessed
        trial = report.trials[0]
        assert (
            trial.ttft_ms == 10
            and trial.latency_ms == 20
            and trial.tokens_per_second == 200
        )
        assert (
            trial.usage == provider.usage
            and trial.usage
            and trial.usage.total_tokens is None
        )
        assert trial.output_character_count == 3
        assert trial.output_sha256 == hashlib.sha256("éOK".encode()).hexdigest()
        assert trial.score is ScoreOutcome.PASS and trial.failure is None
        assert trial.provider_id == "fixture" and trial.model_id == "explicit-model"
        assert trial.operation_id == provider.requests[0].operation_id
        assert provider.requests[0].messages == suite().cases[0].messages
        assert (
            provider.requests[0].temperature == 0
            and provider.requests[0].max_output_tokens == 64
        )
        assert trial.started_at.tzinfo == UTC and trial.finished_at.tzinfo == UTC
        resources = trial.resources
        assert probe.calls == 2 and resources.sample_count == 2
        assert resources.max_observed_ram_used_bytes == 200
        assert (
            resources.system_disk_read_bytes_delta == 20
            and resources.system_disk_write_bytes_delta == 30
        )
        assert resources.gpus and [
            gpu.max_observed_used_mib for gpu in resources.gpus
        ] == [150, 200]
        assert report.summary.success_rate == 1 and report.summary.score_accuracy == 1
        assert (
            manager.active_count == 0
            and manager.available_count == 1
            and not provider.active
        )
        encoded = report.model_dump_json()
        assert all(
            text not in encoded
            for text in ("private prompt", "private provider", "éOK", "99999")
        )
        assert BenchmarkReport.model_validate_json(encoded) == report
        for value in (report, report.summary, trial, resources):
            with pytest.raises(ValidationError):
                type(value).model_validate(
                    {**value.model_dump(), "raw_payload": {"key": "private"}}
                )
            field = next(iter(type(value).model_fields))
            with pytest.raises(ValidationError):
                setattr(value, field, getattr(value, field))

    run(exercise())


@pytest.mark.parametrize(
    "streaming,texts,ticks,usage,ttft,rate",
    [
        (
            False,
            ("éOK",),
            (0, 20_000_000),
            TokenUsage(output_tokens=2, total_tokens=5),
            None,
            None,
        ),
        (True, ("", "", ""), (0, 1, 2, 3), None, None, None),
        (True, ("", "éOK", ""), (0, 1, 10, 20), None, 0.00001, None),
        (True, ("éOK",), (0, 10), TokenUsage(output_tokens=2), 0.00001, None),
        (True, ("éOK", ""), (0, 10, 10), TokenUsage(output_tokens=2), 0.00001, None),
        (True, ("éOK", ""), (0, 10, 9), TokenUsage(output_tokens=2), 0.00001, None),
        (True, ("éOK", ""), (0, 10, 20), TokenUsage(output_tokens=0), 0.00001, 0),
    ],
)
def test_timing_missing_evidence_and_nonstream(
    streaming: bool,
    texts: tuple[str, ...],
    ticks: tuple[int, ...],
    usage: TokenUsage | None,
    ttft: float | None,
    rate: float | None,
) -> None:
    async def exercise() -> None:
        provider = FakeProvider(streaming=streaming)
        provider.texts, provider.usage = texts, usage
        report = await BenchmarkRunner(
            ModelLifecycleManager(), resource_probe=Probe(), clock_ns=Clock(ticks)
        ).run(
            provider,
            "model",
            suite=suite(),
            config=BenchmarkConfig(warmup_runs=0, measured_runs=1),
        )
        trial = report.trials[0]
        assert (
            trial.ttft_ms == ttft
            and trial.tokens_per_second == rate
            and trial.usage == usage
        )
        assert trial.status is TrialStatus.SUCCESS
        assert provider.calls[-1] == ("stream" if streaming else "generate")

    run(exercise())


@pytest.mark.parametrize(
    "health,expected",
    [
        (ProviderHealthStatus.UNAVAILABLE, PreflightStatus.UNAVAILABLE),
        (ProviderHealthStatus.DEGRADED, PreflightStatus.FAILED),
    ],
)
def test_health_non_success_no_fabricated_trials(
    health: ProviderHealthStatus, expected: PreflightStatus
) -> None:
    async def exercise() -> None:
        provider, probe = FakeProvider(), Probe()
        provider.health_status = health
        report = await BenchmarkRunner(
            ModelLifecycleManager(), resource_probe=probe
        ).run(provider, "model")
        assert report.preflight is expected and provider.calls == ["health"]
        assert (
            report.trials == ()
            and report.summary.success_rate is None
            and report.capabilities is None
        )
        assert report.status is not ReportStatus.SUCCESS and not probe.calls
        assert report.health and report.health.detail is None

    run(exercise())


@pytest.mark.parametrize("stage", ["health", "capabilities"])
@pytest.mark.parametrize("action", ["timeout", "error", "identity", "forged"])
def test_preflight_errors_identity_and_bounds(stage: str, action: str) -> None:
    class Invalid(FakeProvider):
        async def health(self) -> ProviderHealth:
            value = await super().health()
            if stage == "health":
                if action == "timeout":
                    await asyncio.Event().wait()
                if action == "error":
                    raise ModelProviderError("private")
                value = value.model_copy(
                    update={"provider_id": "other"}
                    if action == "identity"
                    else {"status": "invalid"}
                )
            return value

        async def capabilities(self, model_id: str) -> ModelCapabilities:
            value = await super().capabilities(model_id)
            if stage == "capabilities":
                if action == "timeout":
                    await asyncio.Event().wait()
                if action == "error":
                    raise ModelProviderError("private")
                value = value.model_copy(
                    update={"model_id": "other"}
                    if action == "identity"
                    else {"supports_streaming": "yes"}
                )
            return value

    async def exercise() -> None:
        report = await BenchmarkRunner(
            ModelLifecycleManager(), resource_probe=Probe()
        ).run(
            Invalid(), "model", config=BenchmarkConfig(preflight_timeout_seconds=0.02)
        )
        assert report.preflight is (
            PreflightStatus.TIMEOUT if action == "timeout" else PreflightStatus.FAILED
        )
        assert not report.trials and "private" not in report.model_dump_json()

    run(exercise())


@pytest.mark.parametrize(
    "action", ["missing-final", "double-final", "after-final", "wrong-id", "forged"]
)
def test_neutral_stream_violations_fail_and_cleanup(action: str) -> None:
    async def exercise() -> None:
        provider, manager = FakeProvider(), ModelLifecycleManager()
        provider.final_count = (
            0 if action == "missing-final" else 2 if action == "double-final" else 1
        )
        provider.after_final = action == "after-final"
        provider.wrong_identity = action == "wrong-id"
        provider.forged = action == "forged"
        report = await BenchmarkRunner(manager, resource_probe=Probe()).run(
            provider,
            "model",
            suite=suite(),
            config=BenchmarkConfig(warmup_runs=0, measured_runs=1),
        )
        assert (
            report.trials[0].status is TrialStatus.FAILED
            and report.trials[0].output_sha256 is None
        )
        assert (
            provider.closed == 1 and not provider.active and manager.active_count == 0
        )
        assert provider.cancelled == [report.trials[0].operation_id]

    run(exercise())


def test_warmup_exclusion_failure_denominators_and_deterministic_statistics() -> None:
    async def exercise() -> None:
        provider = FakeProvider(streaming=False)
        provider.failures = [None, None, ModelProviderError("private"), None, None]
        measured_suite = suite()
        report = await BenchmarkRunner(
            ModelLifecycleManager(),
            resource_probe=Probe(),
            clock_ns=Clock(
                (0, 999_000_000, 0, 10_000_000, 0, 0, 20_000_000, 0, 30_000_000)
            ),
        ).run(
            provider,
            "model",
            suite=measured_suite,
            config=BenchmarkConfig(warmup_runs=1, measured_runs=4),
        )
        summary = report.summary
        assert summary.measured_trials == 4 and summary.successful_trials == 3
        assert summary.success_rate == 0.75 and summary.score_accuracy == 0.75
        assert summary.scorable_trials == 4 and summary.passed_scores == 3
        assert summary.latency_ms.model_dump() == {
            "count": 3,
            "minimum": 10.0,
            "median": 20.0,
            "maximum": 30.0,
        }
        assert summary.ttft_ms.count == 0 and report.status is ReportStatus.PARTIAL
        assert report.trials[0].kind is TrialKind.WARMUP
        assert [trial.repetition_index for trial in report.trials[1:]] == [0, 1, 2, 3]
        assert len({trial.operation_id for trial in report.trials}) == 5
        assert not provider.active

    run(exercise())


def test_unscored_denominator_and_unavailable_skips_following_slots() -> None:
    async def exercise() -> None:
        provider = FakeProvider()
        provider.failures = [ProviderUnavailableError("private")]
        report = await BenchmarkRunner(
            ModelLifecycleManager(), resource_probe=Probe()
        ).run(
            provider,
            "model",
            suite=suite(ScoringMode.UNSCORED),
            config=BenchmarkConfig(warmup_runs=0, measured_runs=3),
        )
        assert [trial.status for trial in report.trials] == [
            TrialStatus.FAILED,
            TrialStatus.SKIPPED,
            TrialStatus.SKIPPED,
        ]
        assert (
            report.summary.success_rate == 0 and report.summary.score_accuracy is None
        )
        assert len(provider.requests) == 1 and all(
            trial.score is ScoreOutcome.UNSCORED for trial in report.trials
        )

    run(exercise())


def test_successful_unscored_case_does_not_hide_failed_scored_attempt() -> None:
    async def exercise() -> None:
        provider = FakeProvider()
        provider.failures = [ModelProviderError("private"), None]
        mixed = BenchmarkSuite(
            suite_id="mixed",
            version="1",
            cases=(
                suite().cases[0],
                suite(ScoringMode.UNSCORED)
                .cases[0]
                .model_copy(update={"case_id": "unscored"}),
            ),
        )
        report = await BenchmarkRunner(
            ModelLifecycleManager(), resource_probe=Probe()
        ).run(
            provider,
            "model",
            suite=mixed,
            config=BenchmarkConfig(warmup_runs=0, measured_runs=1),
        )
        assert report.summary.success_rate == 0.5
        assert report.summary.scorable_trials == 1
        assert report.summary.score_accuracy == 0
        assert report.summary.latency_ms.count == 1

    run(exercise())


@pytest.mark.parametrize("slow_sample", [1, 2])
def test_slow_optional_probe_does_not_consume_generation_deadline(
    slow_sample: int,
) -> None:
    class SlowProbe(Probe):
        def sample(self) -> ResourceSnapshot:
            result = super().sample()
            if self.calls == slow_sample:
                time.sleep(0.025)
            return result

    async def exercise() -> None:
        provider, manager, probe = (
            FakeProvider(streaming=False),
            ModelLifecycleManager(),
            SlowProbe(),
        )
        report = await BenchmarkRunner(manager, resource_probe=probe).run(
            provider,
            "model",
            suite=suite(),
            config=BenchmarkConfig(
                warmup_runs=0, measured_runs=1, per_trial_timeout_seconds=0.01
            ),
        )
        assert report.trials[0].status is TrialStatus.SUCCESS
        assert report.trials[0].output_sha256 is not None
        assert report.summary.success_rate == 1
        assert probe.calls == 2 and len(provider.requests) == 1
        assert manager.active_count == 0 and not provider.active

    run(exercise())


def test_generation_that_blocks_past_deadline_remains_timeout() -> None:
    class BlockingProvider(FakeProvider):
        async def generate(self, request: GenerationRequest) -> GenerationResponse:
            time.sleep(0.025)
            return await super().generate(request)

    async def exercise() -> None:
        provider, manager = BlockingProvider(streaming=False), ModelLifecycleManager()
        report = await BenchmarkRunner(manager, resource_probe=Probe()).run(
            provider,
            "model",
            suite=suite(),
            config=BenchmarkConfig(
                warmup_runs=0, measured_runs=1, per_trial_timeout_seconds=0.01
            ),
        )
        assert report.trials[0].status is TrialStatus.TIMEOUT
        assert report.trials[0].output_sha256 is None
        assert report.summary.success_rate == 0
        assert len(provider.cancelled) == 1
        assert manager.active_count == 0 and not provider.active

    run(exercise())


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("action", ["timeout", "caller-cancel"])
def test_inflight_timeout_and_caller_cancel_cleanup(
    streaming: bool, action: str
) -> None:
    async def exercise() -> None:
        provider, manager, probe = (
            FakeProvider(streaming=streaming),
            ModelLifecycleManager(),
            Probe(),
        )
        provider.hang = True
        provider.manager = manager
        task = asyncio.create_task(
            BenchmarkRunner(manager, resource_probe=probe).run(
                provider,
                "model",
                suite=suite(),
                config=BenchmarkConfig(
                    warmup_runs=0, measured_runs=1, per_trial_timeout_seconds=0.03
                ),
            )
        )
        await provider.entered.wait()
        assert manager.active_count == 1
        if action == "caller-cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            report = await task
            assert report.trials[0].status is TrialStatus.TIMEOUT
            assert (
                report.trials[0].failure is FailureCode.TIMEOUT
                and report.summary.success_rate == 0
            )
        assert (
            manager.active_count == 0
            and manager.available_count == 1
            and not provider.active
        )
        assert len(provider.cancelled) == 1 and probe.calls == 2
        provider.hang = False
        assert (
            await BenchmarkRunner(manager, resource_probe=Probe()).run(
                provider,
                "model",
                suite=suite(),
                config=BenchmarkConfig(warmup_runs=0, measured_runs=1),
            )
        ).status is ReportStatus.SUCCESS

    run(exercise())


@pytest.mark.parametrize("action", ["release", "timeout", "cancel"])
def test_shared_global_capacity_waits_without_provider_io(action: str) -> None:
    async def exercise() -> None:
        provider, manager, probe = FakeProvider(), ModelLifecycleManager(), Probe()
        lease = await manager.acquire_heavy(
            provider_id="other", model_id="other", operation_id="held"
        )
        task = asyncio.create_task(
            BenchmarkRunner(manager, resource_probe=probe).run(
                provider,
                "model",
                suite=suite(),
                config=BenchmarkConfig(
                    warmup_runs=0,
                    measured_runs=1,
                    per_trial_timeout_seconds=0.03 if action == "timeout" else 2,
                ),
            )
        )
        # Preflight completes synchronously; yield one checkpoint to reach lease wait.
        await asyncio.sleep(0)
        assert (
            provider.calls == ["health", "capabilities"]
            and not task.done()
            and not probe.calls
        )
        if action == "release":
            lease.release()
            assert (await task).status is ReportStatus.SUCCESS
        elif action == "timeout":
            report = await task
            assert (
                report.trials[0].status is TrialStatus.TIMEOUT
                and report.trials[0].latency_ms is None
            )
            assert report.trials[0].resources.sample_count == 0
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not provider.active and not provider.cancelled
        lease.release()
        assert manager.active_count == 0 and manager.available_count == 1

    run(exercise())


@pytest.mark.parametrize(
    "before,after,expected",
    [(10, 20, 10), (10, 10, 0), (20, 10, None), (None, 20, None), (10, None, None)],
)
def test_disk_counter_resets_missing_and_zero(
    before: int | None, after: int | None, expected: int | None
) -> None:
    result = benchmark._resources(
        ResourceSnapshot(system_disk_read_bytes=before),
        ResourceSnapshot(system_disk_read_bytes=after),
    )
    assert (
        result.system_disk_read_bytes_delta == expected
        and result.max_observed_ram_used_bytes is None
    )
    assert result.gpus is None


@pytest.mark.parametrize(
    "output,expected",
    [
        ("", ()),
        ("0, GPU A, 0, 100\n", (0,)),
        ('1, "GPU, B", 20, 200\n0, GPU A, 10, 100\n', (1, 0)),
        ("0, GPU A, -1, 100", None),
        ("0, GPU A, N/A, 100", None),
        ("0, GPU A, 10.5, 100", None),
        ("0, GPU A, 101, 100", None),
        ("0, GPU A, 10, 0", None),
        ("0, GPU A, 10", None),
        ("0, GPU A, 10, 100\n0, GPU A, 11, 100", None),
        ("０, GPU A, 10, 100", None),
        ('0,"bad,10,100', None),
    ],
)
def test_system_probe_fixed_command_strict_gpu_parsing(
    output: str, expected: tuple[int, ...] | None
) -> None:
    with (
        patch("psutil.virtual_memory", return_value=SimpleNamespace(used=123)),
        patch(
            "psutil.disk_io_counters",
            return_value=SimpleNamespace(read_bytes=10, write_bytes=20),
        ),
        patch(
            "phoenix.models.benchmark.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, output, "private"),
        ) as command,
    ):
        sample = SystemResourceProbe().sample()
    assert sample.system_ram_used_bytes == 123 and sample.system_disk_read_bytes == 10
    assert sample.system_disk_write_bytes == 20
    assert (
        tuple(gpu.index for gpu in sample.gpus) if sample.gpus is not None else None
    ) == expected
    assert command.call_args.args[0] == [
        "nvidia-smi",
        "--query-gpu=index,name,memory.used,memory.total",
        "--format=csv,noheader,nounits",
    ]
    assert command.call_args.kwargs == {
        "shell": False,
        "capture_output": True,
        "text": True,
        "timeout": 1,
        "check": False,
    }


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("private"),
        subprocess.TimeoutExpired("nvidia-smi", 1),
        OSError("private"),
        UnicodeError("private"),
    ],
)
def test_missing_failed_telemetry_is_unknown(error: Exception) -> None:
    with (
        patch("psutil.virtual_memory", side_effect=error),
        patch("psutil.disk_io_counters", side_effect=error),
        patch("phoenix.models.benchmark.subprocess.run", side_effect=error),
    ):
        assert SystemResourceProbe().sample() == ResourceSnapshot()
    with patch(
        "phoenix.models.benchmark.subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, "private", "private"),
    ):
        assert SystemResourceProbe().sample().gpus is None


def test_optional_probe_failure_never_changes_generation_success() -> None:
    class FailingProbe:
        def sample(self) -> ResourceSnapshot:
            raise RuntimeError("private")

    async def exercise() -> None:
        report = await BenchmarkRunner(
            ModelLifecycleManager(), resource_probe=FailingProbe()
        ).run(
            FakeProvider(),
            "model",
            suite=suite(),
            config=BenchmarkConfig(warmup_runs=0, measured_runs=1),
        )
        assert (
            report.status is ReportStatus.SUCCESS
            and report.trials[0].resources.sample_count == 2
        )
        assert (
            report.trials[0].resources.gpus is None
            and "private" not in report.model_dump_json()
        )

    run(exercise())


@pytest.mark.parametrize("model_id", ["", " ", 1, None, True])
def test_invalid_run_target_before_io(model_id: object) -> None:
    async def exercise() -> None:
        provider = FakeProvider()
        with pytest.raises(ValidationError):
            await BenchmarkRunner(ModelLifecycleManager()).run(provider, model_id)  # type: ignore[arg-type]
        assert not provider.calls

    run(exercise())


def test_dependency_and_scope_boundaries() -> None:
    source = inspect.getsource(benchmark)
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("phoenix")
        ):
            assert node.module in {
                "phoenix.models.provider",
                "phoenix.models.lifecycle",
            }
    assert not any(
        value in source
        for value in (
            "/api/tags",
            "/v1/models",
            "EventBus",
            "Semaphore(",
            "shell=True",
            "sqlite",
            ".write_text(",
            ".write_bytes(",
            "ollama pull",
        )
    )


@pytest.mark.parametrize("streaming", [True, False])
def test_invalid_unicode_output_is_explicit_protocol_failure(streaming: bool) -> None:
    async def exercise() -> None:
        provider = FakeProvider(streaming=streaming)
        provider.texts = ("\ud800", "")
        manager = ModelLifecycleManager()
        report = await BenchmarkRunner(manager, resource_probe=Probe()).run(
            provider,
            "model",
            suite=suite(),
            config=BenchmarkConfig(warmup_runs=0, measured_runs=1),
        )
        assert report.trials[0].status is TrialStatus.FAILED
        assert report.trials[0].failure is FailureCode.PROTOCOL
        assert report.trials[0].output_sha256 is None
        assert manager.active_count == 0 and not provider.active

    run(exercise())


def test_overflow_throughput_stays_unknown_without_losing_explicit_usage() -> None:
    async def exercise() -> None:
        provider = FakeProvider()
        provider.usage = TokenUsage(output_tokens=10**400)
        report = await BenchmarkRunner(
            ModelLifecycleManager(), resource_probe=Probe(), clock_ns=Clock()
        ).run(
            provider,
            "model",
            suite=suite(),
            config=BenchmarkConfig(warmup_runs=0, measured_runs=1),
        )
        assert report.trials[0].tokens_per_second is None
        assert (
            report.trials[0].usage == provider.usage
            and report.status is ReportStatus.SUCCESS
        )

    run(exercise())


@pytest.mark.parametrize(
    "action", ["cancel-timeout", "cancel-error", "close-timeout", "close-error"]
)
def test_cleanup_failure_aborts_safely_and_releases_lease(action: str) -> None:
    class InvalidCleanup(FakeProvider):
        async def cancel(self, operation_id: str) -> CancellationResult:
            if action == "cancel-timeout":
                await asyncio.Event().wait()
            if action == "cancel-error":
                raise ModelProviderError("private key prompt")
            return await super().cancel(operation_id)

        def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationChunk]:
            underlying = super().stream(request)

            class Iterator:
                def __aiter__(self) -> Self:
                    return self

                async def __anext__(self) -> GenerationChunk:
                    return await anext(underlying)

                async def aclose(self) -> None:
                    close = getattr(underlying, "aclose")
                    await close()
                    if action == "close-timeout":
                        await asyncio.Event().wait()
                    if action == "close-error":
                        raise ModelProviderError("private key prompt")

            return Iterator()

    async def exercise() -> None:
        provider, manager = InvalidCleanup(), ModelLifecycleManager()
        if action.startswith("cancel"):
            provider.failures = [ModelProviderError("private")]
        with pytest.raises(BenchmarkError) as failure:
            await BenchmarkRunner(manager, resource_probe=Probe()).run(
                provider,
                "model",
                suite=suite(),
                config=BenchmarkConfig(
                    warmup_runs=0, measured_runs=1, cleanup_timeout_seconds=0.02
                ),
            )
        assert "private" not in str(failure.value)
        assert not provider.active and manager.active_count == 0
        assert len(asyncio.all_tasks()) == 2  # outer bounded caller plus exercise

    run(exercise())


def test_explicit_usage_in_separate_chunks_is_retained() -> None:
    class UsageFrames(FakeProvider):
        async def stream(
            self, request: GenerationRequest
        ) -> AsyncIterator[GenerationChunk]:
            for text, usage, final in (
                ("", TokenUsage(input_tokens=5), False),
                ("éOK", TokenUsage(output_tokens=2), False),
                ("", TokenUsage(total_tokens=7), True),
            ):
                yield GenerationChunk(
                    provider_id=self.provider_id,
                    model_id=request.model_id,
                    operation_id=request.operation_id,
                    text=text,
                    usage=usage,
                    is_final=final,
                )

    async def exercise() -> None:
        report = await BenchmarkRunner(
            ModelLifecycleManager(), resource_probe=Probe(), clock_ns=Clock()
        ).run(
            UsageFrames(),
            "model",
            suite=suite(),
            config=BenchmarkConfig(warmup_runs=0, measured_runs=1),
        )
        assert report.trials[0].usage == TokenUsage(
            input_tokens=5, output_tokens=2, total_tokens=7
        )
        assert report.trials[0].ttft_ms == 10

    run(exercise())


def test_two_runners_share_global_heavy_capacity() -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        first, second = FakeProvider(), FakeProvider()
        first.hang = True
        first.manager = second.manager = manager
        config = BenchmarkConfig(warmup_runs=0, measured_runs=1)
        one = asyncio.create_task(
            BenchmarkRunner(manager, resource_probe=Probe()).run(
                first, "a", suite=suite(), config=config
            )
        )
        await first.entered.wait()
        two = asyncio.create_task(
            BenchmarkRunner(manager, resource_probe=Probe()).run(
                second, "b", suite=suite(), config=config
            )
        )
        await asyncio.sleep(0)
        assert not second.requests and manager.active_count == 1
        one.cancel()
        with pytest.raises(asyncio.CancelledError):
            await one
        assert (await two).status is ReportStatus.SUCCESS
        assert not first.active and not second.active and manager.active_count == 0

    run(exercise())


def test_forged_suite_config_and_invalid_resources_are_revalidated() -> None:
    async def exercise() -> None:
        provider = FakeProvider()
        runner = BenchmarkRunner(ModelLifecycleManager(), resource_probe=Probe())
        bad_case = suite().cases[0].model_copy(update={"messages": ()})
        for bad_suite, bad_config in (
            (suite().model_copy(update={"cases": (bad_case,)}), BenchmarkConfig()),
            (suite(), BenchmarkConfig().model_copy(update={"measured_runs": True})),
        ):
            with pytest.raises(ValidationError):
                await runner.run(provider, "model", suite=bad_suite, config=bad_config)
        assert not provider.calls
        invalid = ResourceSnapshot().model_copy(update={"system_ram_used_bytes": -1})
        report = await BenchmarkRunner(
            ModelLifecycleManager(), resource_probe=Probe((invalid, invalid))
        ).run(
            provider,
            "model",
            suite=suite(),
            config=BenchmarkConfig(warmup_runs=0, measured_runs=1),
        )
        assert report.trials[0].resources.max_observed_ram_used_bytes is None
        assert report.status is ReportStatus.SUCCESS

    run(exercise())


def test_import_and_construction_are_isolated(tmp_path: Path) -> None:
    script = """
import asyncio, importlib.abc, os, socket, sqlite3, subprocess, sys, threading
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected benchmark activity")
def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("psutil", "httpx", "phoenix.models.ollama", "phoenix.models.colibri",
                 "phoenix.models.embeddings", "phoenix.models.registry", "phoenix.core",
                 "phoenix.api", "phoenix.persistence", "phoenix.agents",
                 "phoenix.tools", "phoenix.permissions", "phoenix.voice")
        if any(fullname == root or fullname.startswith(root + ".") for root in roots):
            forbidden()
sys.dont_write_bytecode = True
sys.meta_path.insert(0, BlockImports())
sys.addaudithook(audit)
os.mkdir = forbidden
socket.socket = forbidden
socket.create_connection = forbidden
sqlite3.connect = forbidden
subprocess.Popen = forbidden
threading.Thread.start = forbidden
asyncio.create_task = forbidden
from phoenix.models.benchmark import (
    BenchmarkConfig, BenchmarkRunner, PHASE1_SUITE, SystemResourceProbe
)
from phoenix.models.lifecycle import ModelLifecycleManager
BenchmarkRunner(ModelLifecycleManager())
SystemResourceProbe()
BenchmarkConfig()
assert PHASE1_SUITE.model_dump_json()
assert not list(Path.cwd().iterdir())
print("Benchmark isolation: PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "Benchmark isolation: PASS"
