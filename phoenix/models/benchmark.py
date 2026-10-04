"""Explicit Phase-1 text microbenchmarks: in-memory evidence, never routing.

RAM/disk observations are system-wide and may include unrelated activity. Two
boundary samples report observed maxima, not absolute peaks or process usage.
No activity occurs until run()/sample(); caller owns providers and shared leases.
"""

import asyncio
import csv
import hashlib
import io
import math
import statistics
import subprocess
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Protocol, Self

from pydantic import AwareDatetime, Field, TypeAdapter, field_validator, model_validator

from phoenix.models.lifecycle import ModelLifecycleManager
from phoenix.models.provider import (
    GenerationChunk,
    GenerationRequest,
    GenerationResponse,
    Identifier,
    MessageRole,
    ModelCapabilities,
    ModelMessage,
    ModelProvider,
    ModelProviderError,
    NonnegativeFloat,
    NonnegativeInt,
    PositiveInt,
    ProviderHealth,
    ProviderHealthStatus,
    ProviderModel,
    ProviderUnavailableError,
    TokenUsage,
)

_PositiveSeconds = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
_Digest = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]


class ScoringMode(StrEnum):
    EXACT = "exact"
    CONTAINS = "contains"
    UNSCORED = "unscored"


class ScoreOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNSCORED = "unscored"


class ScoringRule(ProviderModel):
    mode: ScoringMode = ScoringMode.UNSCORED
    expected: str | None = Field(default=None, strict=True)

    @model_validator(mode="after")
    def expected_matches_mode(self) -> Self:
        if self.mode is ScoringMode.UNSCORED:
            if self.expected is not None:
                raise ValueError("Unscored rules cannot have expected text")
        elif self.expected is None or (
            self.mode is ScoringMode.CONTAINS and not self.expected
        ):
            raise ValueError("Scored rules require explicit expected text")
        return self

    def score(self, text: str) -> ScoreOutcome:
        if self.mode is ScoringMode.UNSCORED:
            return ScoreOutcome.UNSCORED
        passed = (
            text == self.expected
            if self.mode is ScoringMode.EXACT
            else self.expected is not None and self.expected in text
        )
        return ScoreOutcome.PASS if passed else ScoreOutcome.FAIL


class BenchmarkCase(ProviderModel):
    case_id: Identifier
    category: Identifier
    messages: tuple[ModelMessage, ...] = Field(min_length=1)
    scoring: ScoringRule = Field(default_factory=ScoringRule)


class BenchmarkSuite(ProviderModel):
    suite_id: Identifier
    version: Identifier
    cases: tuple[BenchmarkCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("Benchmark case IDs must be unique")
        return self


PHASE1_SUITE = BenchmarkSuite(
    suite_id="phoenix-phase1-text",
    version="1.0.0",
    cases=tuple(
        BenchmarkCase(
            case_id=case_id,
            category=category,
            messages=(ModelMessage(role=MessageRole.USER, text=prompt),),
            scoring=ScoringRule(mode=ScoringMode.EXACT, expected=expected),
        )
        for case_id, category, prompt, expected in (
            (
                "instruction",
                "instruction",
                "Reply with exactly PHOENIX_OK.",
                "PHOENIX_OK",
            ),
            ("arithmetic", "arithmetic", "What is 17 + 25? Reply with only 42.", "42"),
            ("format", "format", 'Reply with exactly {"ok":true}.', '{"ok":true}'),
            (
                "code",
                "code",
                "Reply with exactly print(2 + 3), no markdown.",
                "print(2 + 3)",
            ),
        )
    ),
)


class BenchmarkConfig(ProviderModel):
    warmup_runs: NonnegativeInt = 1
    measured_runs: PositiveInt = 3
    per_trial_timeout_seconds: _PositiveSeconds = 60
    preflight_timeout_seconds: _PositiveSeconds = 10
    cleanup_timeout_seconds: _PositiveSeconds = 5
    temperature: NonnegativeFloat = 0
    max_output_tokens: PositiveInt = 64


class GpuReading(ProviderModel):
    index: NonnegativeInt
    name: Identifier
    used_mib: NonnegativeInt
    total_mib: PositiveInt

    @model_validator(mode="after")
    def valid_usage(self) -> Self:
        if self.used_mib > self.total_mib:
            raise ValueError("GPU usage exceeds total memory")
        return self


class ResourceSnapshot(ProviderModel):
    system_ram_used_bytes: NonnegativeInt | None = None
    system_disk_read_bytes: NonnegativeInt | None = None
    system_disk_write_bytes: NonnegativeInt | None = None
    gpus: tuple[GpuReading, ...] | None = None

    @model_validator(mode="after")
    def unique_gpus(self) -> Self:
        if self.gpus is not None and len({gpu.index for gpu in self.gpus}) != len(
            self.gpus
        ):
            raise ValueError("GPU indices must be unique")
        return self


class ResourceProbe(Protocol):
    def sample(self) -> ResourceSnapshot:
        """Explicit bounded local observation; no permanent worker."""


class SystemResourceProbe:
    """Two explicit boundary samples; fixed NVIDIA command bounded to one second."""

    def sample(self) -> ResourceSnapshot:
        import psutil

        ram = read = write = None
        gpus: tuple[GpuReading, ...] | None = None
        try:
            ram = TypeAdapter(NonnegativeInt).validate_python(
                psutil.virtual_memory().used
            )
        except Exception:
            pass
        try:
            counters = psutil.disk_io_counters()
            if counters is not None:
                read = TypeAdapter(NonnegativeInt).validate_python(counters.read_bytes)
                write = TypeAdapter(NonnegativeInt).validate_python(
                    counters.write_bytes
                )
        except Exception:
            read = write = None
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                shell=False,
                capture_output=True,
                text=True,
                timeout=1,
                check=False,
            )
            if result.returncode == 0:
                readings: list[GpuReading] = []
                for row in csv.reader(
                    io.StringIO(result.stdout), strict=True, skipinitialspace=True
                ):
                    if not row or all(not item.strip() for item in row):
                        continue
                    if len(row) != 4:
                        raise ValueError
                    index, name, used, total = (item.strip() for item in row)
                    if any(
                        not value.isascii() or not value.isdecimal()
                        for value in (index, used, total)
                    ):
                        raise ValueError
                    readings.append(
                        GpuReading(
                            index=int(index),
                            name=name,
                            used_mib=int(used),
                            total_mib=int(total),
                        )
                    )
                gpus = ResourceSnapshot(gpus=tuple(readings)).gpus
        except Exception:
            pass
        return ResourceSnapshot(
            system_ram_used_bytes=ram,
            system_disk_read_bytes=read,
            system_disk_write_bytes=write,
            gpus=gpus,
        )


class GpuObservation(ProviderModel):
    index: NonnegativeInt
    name: Identifier
    total_mib: PositiveInt
    max_observed_used_mib: NonnegativeInt
    samples: PositiveInt


class ResourceObservation(ProviderModel):
    """System-wide deltas and discrete observed maxima, not model attribution."""

    sample_count: NonnegativeInt
    max_observed_ram_used_bytes: NonnegativeInt | None = None
    system_disk_read_bytes_delta: NonnegativeInt | None = None
    system_disk_write_bytes_delta: NonnegativeInt | None = None
    gpus: tuple[GpuObservation, ...] | None = None


def _resources(
    before: ResourceSnapshot, after: ResourceSnapshot
) -> ResourceObservation:
    ram = [
        sample.system_ram_used_bytes
        for sample in (before, after)
        if sample.system_ram_used_bytes is not None
    ]
    readings: dict[tuple[int, str, int], list[int]] = {}
    for sample in (before, after):
        for gpu in sample.gpus or ():
            readings.setdefault((gpu.index, gpu.name, gpu.total_mib), []).append(
                gpu.used_mib
            )

    def delta(start: int | None, end: int | None) -> int | None:
        return (
            end - start
            if start is not None and end is not None and end >= start
            else None
        )

    return ResourceObservation(
        sample_count=2,
        max_observed_ram_used_bytes=max(ram) if ram else None,
        system_disk_read_bytes_delta=delta(
            before.system_disk_read_bytes, after.system_disk_read_bytes
        ),
        system_disk_write_bytes_delta=delta(
            before.system_disk_write_bytes, after.system_disk_write_bytes
        ),
        gpus=tuple(
            GpuObservation(
                index=key[0],
                name=key[1],
                total_mib=key[2],
                max_observed_used_mib=max(values),
                samples=len(values),
            )
            for key, values in sorted(readings.items())
        )
        if before.gpus is not None or after.gpus is not None
        else None,
    )


class TrialStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class TrialKind(StrEnum):
    WARMUP = "warmup"
    MEASURED = "measured"


class PreflightStatus(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    TIMEOUT = "timeout"


class FailureCode(StrEnum):
    UNAVAILABLE = "provider_unavailable"
    PROVIDER = "provider_failure"
    PROTOCOL = "invalid_neutral_response"
    TIMEOUT = "deadline_exceeded"
    SKIPPED = "provider_became_unavailable"


class _Timestamped(ProviderModel):
    started_at: AwareDatetime
    finished_at: AwareDatetime

    @field_validator("started_at", "finished_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class BenchmarkTrialResult(_Timestamped):
    suite_id: Identifier
    suite_version: Identifier
    case_id: Identifier
    provider_id: Identifier
    model_id: Identifier
    operation_id: Identifier
    repetition_index: NonnegativeInt
    kind: TrialKind
    status: TrialStatus
    scoring_mode: ScoringMode
    score: ScoreOutcome
    output_character_count: NonnegativeInt | None = None
    output_sha256: _Digest | None = None
    ttft_ms: NonnegativeFloat | None = None
    latency_ms: NonnegativeFloat | None = None
    tokens_per_second: NonnegativeFloat | None = None
    usage: TokenUsage | None = None
    resources: ResourceObservation
    failure: FailureCode | None = None


class NumericSummary(ProviderModel):
    count: NonnegativeInt
    minimum: NonnegativeFloat | None = None
    median: NonnegativeFloat | None = None
    maximum: NonnegativeFloat | None = None


class BenchmarkSummary(ProviderModel):
    measured_trials: NonnegativeInt
    successful_trials: NonnegativeInt
    scorable_trials: NonnegativeInt
    passed_scores: NonnegativeInt
    success_rate: (
        Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] | None
    )
    score_accuracy: (
        Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)] | None
    )
    latency_ms: NumericSummary
    ttft_ms: NumericSummary
    tokens_per_second: NumericSummary


def summarize(trials: tuple[BenchmarkTrialResult, ...]) -> BenchmarkSummary:
    measured = [trial for trial in trials if trial.kind is TrialKind.MEASURED]
    successful = [trial for trial in measured if trial.status is TrialStatus.SUCCESS]
    scorable = [
        trial for trial in measured if trial.scoring_mode is not ScoringMode.UNSCORED
    ]
    passed = sum(trial.score is ScoreOutcome.PASS for trial in scorable)

    def numeric(field: str) -> NumericSummary:
        values: list[float] = [
            value
            for trial in successful
            if (value := getattr(trial, field)) is not None
        ]
        return NumericSummary(
            count=len(values),
            minimum=min(values) if values else None,
            median=statistics.median(values) if values else None,
            maximum=max(values) if values else None,
        )

    return BenchmarkSummary(
        measured_trials=len(measured),
        successful_trials=len(successful),
        scorable_trials=len(scorable),
        passed_scores=passed,
        success_rate=len(successful) / len(measured) if measured else None,
        score_accuracy=passed / len(scorable) if scorable else None,
        latency_ms=numeric("latency_ms"),
        ttft_ms=numeric("ttft_ms"),
        tokens_per_second=numeric("tokens_per_second"),
    )


class ReportStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class BenchmarkReport(_Timestamped):
    suite_id: Identifier
    suite_version: Identifier
    provider_id: Identifier
    model_id: Identifier
    preflight: PreflightStatus
    health: ProviderHealth | None
    capabilities: ModelCapabilities | None
    config: BenchmarkConfig
    trials: tuple[BenchmarkTrialResult, ...]
    summary: BenchmarkSummary
    status: ReportStatus
    failure: FailureCode | None = None
    not_assessed: tuple[str, ...] = (
        "tool_use",
        "structured_output",
        "vision",
        "planning_quality",
        "general_coding_quality",
    )


class BenchmarkError(Exception):
    """Cleanup/coordination failure aborts a run rather than fabricating completion."""


@dataclass
class _Generation:
    text: str
    start_ns: int
    completion_ns: int
    first_text_ns: int | None
    usage: TokenUsage | None


class BenchmarkRunner:
    """Explicit serial trials using the caller's shared lifecycle manager.

    Trial deadline includes lease waiting and generation, with separately bounded
    diagnostic probe windows. Generation timing excludes lease wait and probes.
    Ordinary failures continue; provider unavailability skips
    remaining configured slots. Caller cancellation propagates after cleanup.
    Providers must honor the neutral cancellation/bounded-cleanup contract.
    """

    def __init__(
        self,
        lifecycle: ModelLifecycleManager,
        *,
        resource_probe: ResourceProbe | None = None,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self._lifecycle = lifecycle
        self._probe = (
            resource_probe if resource_probe is not None else SystemResourceProbe()
        )
        self._clock = clock_ns

    def _sample(self) -> ResourceSnapshot:
        try:
            result = self._probe.sample()
            return ResourceSnapshot.model_validate(result.model_dump(warnings=False))
        except Exception:
            return ResourceSnapshot()

    async def _generate(
        self,
        provider: ModelProvider,
        request: GenerationRequest,
        streaming: bool,
        cleanup_seconds: float,
    ) -> _Generation:
        def validate(value: GenerationResponse) -> None:
            if (value.provider_id, value.model_id, value.operation_id) != (
                provider.provider_id,
                request.model_id,
                request.operation_id,
            ):
                raise ValueError("Invalid neutral identity")

        start = self._clock()
        completion: int | None
        if not streaming:
            response = await provider.generate(request)
            completion = self._clock()
            response = GenerationResponse.model_validate(
                response.model_dump(warnings=False)
            )
            validate(response)
            response.text.encode("utf-8")
            return _Generation(response.text, start, completion, None, response.usage)
        iterator: AsyncIterator[GenerationChunk] = provider.stream(request)
        text: list[str] = []
        first: int | None = None
        completion = None
        usage: TokenUsage | None = None
        try:
            async for raw in iterator:
                observed = self._clock()
                chunk = GenerationChunk.model_validate(raw.model_dump(warnings=False))
                validate(chunk)
                if completion is not None:
                    raise ValueError("Chunk follows terminal evidence")
                if chunk.text:
                    if first is None:
                        first = observed
                    text.append(chunk.text)
                if chunk.usage is not None:
                    values = usage.model_dump() if usage else {}
                    values.update(
                        {
                            key: value
                            for key, value in chunk.usage.model_dump().items()
                            if value is not None
                        }
                    )
                    usage = TokenUsage.model_validate(values)
                if chunk.is_final:
                    completion = observed
            if completion is None:
                raise ValueError("Missing neutral terminal evidence")
            combined = "".join(text)
            combined.encode("utf-8")
            return _Generation(combined, start, completion, first, usage)
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                try:
                    async with asyncio.timeout(cleanup_seconds):
                        await close()
                except Exception:
                    raise BenchmarkError("Benchmark stream cleanup failed") from None

    async def _cancel(
        self,
        provider: ModelProvider,
        request: GenerationRequest,
        capabilities: ModelCapabilities,
        seconds: float,
    ) -> None:
        if capabilities.supports_cancellation:
            try:
                async with asyncio.timeout(seconds):
                    await provider.cancel(request.operation_id)
            except Exception:
                raise BenchmarkError("Benchmark cancellation cleanup failed") from None

    async def _trial(
        self,
        provider: ModelProvider,
        model_id: str,
        suite: BenchmarkSuite,
        case: BenchmarkCase,
        config: BenchmarkConfig,
        capabilities: ModelCapabilities,
        kind: TrialKind,
        repetition: int,
        skip: bool,
    ) -> BenchmarkTrialResult:
        started = datetime.now(UTC)
        request = GenerationRequest(
            model_id=model_id,
            messages=case.messages,
            temperature=config.temperature,
            max_output_tokens=config.max_output_tokens,
        )
        status = TrialStatus.SKIPPED
        failure: FailureCode | None = FailureCode.SKIPPED
        generation: _Generation | None = None
        before = after = ResourceSnapshot()
        sampled = False
        if not skip:
            deadline = (
                asyncio.get_running_loop().time() + config.per_trial_timeout_seconds
            )
            try:
                async with asyncio.timeout_at(deadline) as trial_timeout:
                    async with await self._lifecycle.acquire_heavy(
                        provider_id=provider.provider_id,
                        model_id=model_id,
                        operation_id=request.operation_id,
                    ):
                        loop = asyncio.get_running_loop()
                        if loop.time() >= deadline:
                            raise TimeoutError
                        # Optional synchronous telemetry has its own bounded
                        # window; its availability cannot decide generation success.
                        trial_timeout.reschedule(None)
                        probe_started = loop.time()
                        before = self._sample()
                        deadline += loop.time() - probe_started
                        trial_timeout.reschedule(deadline)
                        sampled = True
                        try:
                            if asyncio.get_running_loop().time() >= deadline:
                                raise TimeoutError
                            generation = await self._generate(
                                provider,
                                request,
                                capabilities.supports_streaming,
                                config.cleanup_timeout_seconds,
                            )
                            if loop.time() >= deadline:
                                raise TimeoutError
                        except BaseException:
                            # Provider I/O has unwound before scoped cancellation;
                            # no hidden generation task can outlive the lease.
                            await self._cancel(
                                provider,
                                request,
                                capabilities,
                                config.cleanup_timeout_seconds,
                            )
                            raise
                        finally:
                            if not trial_timeout.expired():
                                trial_timeout.reschedule(None)
                            after = self._sample()
                status, failure = TrialStatus.SUCCESS, None
            except BenchmarkError:
                raise
            except TimeoutError:
                status, failure = TrialStatus.TIMEOUT, FailureCode.TIMEOUT
            except ProviderUnavailableError:
                status, failure = TrialStatus.FAILED, FailureCode.UNAVAILABLE
            except ModelProviderError:
                status, failure = TrialStatus.FAILED, FailureCode.PROVIDER
            except (ValueError, AttributeError, TypeError):
                status, failure = TrialStatus.FAILED, FailureCode.PROTOCOL
        outcome = (
            ScoreOutcome.UNSCORED
            if case.scoring.mode is ScoringMode.UNSCORED
            else ScoreOutcome.FAIL
        )
        length = digest = ttft = latency = rate = usage = None
        if generation is not None and status is TrialStatus.SUCCESS:
            if generation.completion_ns < generation.start_ns:
                raise BenchmarkError("Benchmark monotonic clock regressed")
            outcome = case.scoring.score(generation.text)
            length = len(generation.text)
            digest = hashlib.sha256(generation.text.encode("utf-8")).hexdigest()
            latency = (generation.completion_ns - generation.start_ns) / 1_000_000
            usage = generation.usage
            if generation.first_text_ns is not None:
                if generation.first_text_ns < generation.start_ns:
                    raise BenchmarkError("Benchmark monotonic clock regressed")
                ttft = (generation.first_text_ns - generation.start_ns) / 1_000_000
                interval = generation.completion_ns - generation.first_text_ns
                if (
                    usage is not None
                    and usage.output_tokens is not None
                    and interval > 0
                ):
                    try:
                        candidate = usage.output_tokens / (interval / 1_000_000_000)
                        rate = candidate if math.isfinite(candidate) else None
                    except OverflowError:
                        rate = None
        return BenchmarkTrialResult(
            suite_id=suite.suite_id,
            suite_version=suite.version,
            case_id=case.case_id,
            provider_id=provider.provider_id,
            model_id=model_id,
            operation_id=request.operation_id,
            repetition_index=repetition,
            kind=kind,
            started_at=started,
            finished_at=datetime.now(UTC),
            status=status,
            scoring_mode=case.scoring.mode,
            score=outcome,
            output_character_count=length,
            output_sha256=digest,
            ttft_ms=ttft,
            latency_ms=latency,
            tokens_per_second=rate,
            usage=usage,
            resources=_resources(before, after)
            if sampled
            else ResourceObservation(sample_count=0),
            failure=failure,
        )

    async def run(
        self,
        provider: ModelProvider,
        model_id: str,
        *,
        suite: BenchmarkSuite = PHASE1_SUITE,
        config: BenchmarkConfig | None = None,
    ) -> BenchmarkReport:
        model_id = TypeAdapter(Identifier).validate_python(model_id)
        suite = BenchmarkSuite.model_validate(suite.model_dump(warnings=False))
        config = (
            BenchmarkConfig.model_validate(config.model_dump(warnings=False))
            if config is not None
            else BenchmarkConfig()
        )
        started = datetime.now(UTC)
        health: ProviderHealth | None = None
        capabilities: ModelCapabilities | None = None
        preflight, failure = PreflightStatus.READY, None
        try:
            async with asyncio.timeout(config.preflight_timeout_seconds):
                raw_health = await provider.health()
                health = ProviderHealth.model_validate(
                    raw_health.model_dump(warnings=False)
                )
                # Strip provider-controlled detail even on contradictory identity.
                health = health.model_copy(update={"detail": None})
                if health.provider_id != provider.provider_id:
                    health = None
                    raise ValueError
                if health.status is ProviderHealthStatus.UNAVAILABLE:
                    raise ProviderUnavailableError
                if health.status is not ProviderHealthStatus.HEALTHY:
                    raise ModelProviderError
                raw_caps = await provider.capabilities(model_id)
                capabilities = ModelCapabilities.model_validate(
                    raw_caps.model_dump(warnings=False)
                )
                if (capabilities.provider_id, capabilities.model_id) != (
                    provider.provider_id,
                    model_id,
                ):
                    capabilities = None
                    raise ValueError
        except TimeoutError:
            preflight, failure = PreflightStatus.TIMEOUT, FailureCode.TIMEOUT
        except ProviderUnavailableError:
            preflight, failure = PreflightStatus.UNAVAILABLE, FailureCode.UNAVAILABLE
        except (ModelProviderError, ValueError, AttributeError, TypeError):
            preflight, failure = PreflightStatus.FAILED, FailureCode.PROTOCOL
        trials: list[BenchmarkTrialResult] = []
        skip = False
        if preflight is PreflightStatus.READY and capabilities is not None:
            for kind, runs in (
                (TrialKind.WARMUP, config.warmup_runs),
                (TrialKind.MEASURED, config.measured_runs),
            ):
                for repetition in range(runs):
                    for case in suite.cases:
                        trial = await self._trial(
                            provider,
                            model_id,
                            suite,
                            case,
                            config,
                            capabilities,
                            kind,
                            repetition,
                            skip,
                        )
                        trials.append(trial)
                        skip = skip or trial.failure is FailureCode.UNAVAILABLE
        summary = summarize(tuple(trials))
        status = ReportStatus.FAILED
        if preflight is PreflightStatus.UNAVAILABLE:
            status = ReportStatus.UNAVAILABLE
        elif preflight is PreflightStatus.READY:
            status = (
                ReportStatus.SUCCESS
                if summary.success_rate == 1
                else ReportStatus.PARTIAL
                if summary.successful_trials
                else ReportStatus.FAILED
            )
        return BenchmarkReport(
            suite_id=suite.suite_id,
            suite_version=suite.version,
            provider_id=provider.provider_id,
            model_id=model_id,
            preflight=preflight,
            health=health,
            capabilities=capabilities,
            config=config,
            trials=tuple(trials),
            summary=summary,
            status=status,
            failure=failure,
            started_at=started,
            finished_at=datetime.now(UTC),
        )
