"""PHX-119 KAIDO fail-safe in PHOENIX missions, offline only (ADR-155..158).

A scripted ModelProvider answers by the system prompt's role marker, from a
per-role queue (the last reply repeats); health and resource samples are
scripted too. No network, Ollama or GPU is used.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Coroutine
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from phoenix.agents import mission_events
from phoenix.agents.failsafe import FailSafeCapability, FailSafeTrigger
from phoenix.agents.kaido import (
    KAIDO_ANSWER_LABEL,
    KAIDO_CAPABILITIES,
    KAIDO_MAX_INPUT_CHARS,
    KAIDO_MAX_OUTPUT_TOKENS,
    KAIDO_SYSTEM,
    KaidoMonitor,
    KaidoPolicy,
    KaidoRecord,
    KaidoStatus,
    ResourceSample,
    within_budget,
)
from phoenix.agents.mission_events import EventPayloadError, check_event, check_payload
from phoenix.agents.mission_recovery import RecoveryOutcome, RecoveryRecord
from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest
from phoenix.agents.responders import ResponderErrorCode
from phoenix.agents.runtime import (
    MissionFailureCode,
    MissionKind,
    MissionResult,
    MissionTrace,
    PhoenixRuntime,
    PhoenixRuntimeError,
    RuntimeLimits,
    default_tier_table,
)
from phoenix.agents.task_graph import TaskState
from phoenix.api.routes.schemas import MAX_CHAT_CHARS
from phoenix.api.security import LocalApiSecurity
from phoenix.api.server import ProviderSet, ServerConfig, build_server_app
from phoenix.core.config import AppSettings
from phoenix.core.events import EventEnvelope, EventType
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

MODELS = ("qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b", "qwen2.5-coder:7b")
PASS = '{"checks":[{"criterion":"Correct","passed":true,"evidence_summary":"Fine."}]}'
FAIL = '{"checks":[{"criterion":"Correct","passed":false,"evidence_summary":"No."}]}'
SENTINEL = "SENTINEL-kaido-4e1d-PRIVATE"
MISSION = "0b6f5f6e-6a43-4a3e-9d8b-4b0f8e9c1d2a"
FAST = RuntimeLimits(recovery_backoff_seconds=0.0)
HEALTHY = ProviderHealthStatus.HEALTHY
DEGRADED = ProviderHealthStatus.DEGRADED
UNAVAILABLE = ProviderHealthStatus.UNAVAILABLE
Reply = tuple[str, str] | Exception
PLAN = json.dumps({"steps": [{"objective": "Recall."}]})
ASSIGN = json.dumps({"assignments": [{"step_id": "s1", "domain": "intelligence"}]})


def role_of(request: GenerationRequest) -> str:
    return request.messages[0].text.splitlines()[0].removeprefix("PHOENIX ROLE: ")


def ok(text: str) -> Reply:
    return (text, "stop")


NOT_JSON: Reply = ok(f"no json {SENTINEL}")


class QueueProvider(ModelProvider):
    """Per-role reply queues; the last reply of a queue repeats."""

    def __init__(self, name: str = "default") -> None:
        super().__init__("ollama")
        self.name = name
        self.replies: dict[str, list[Reply]] = {
            "triage": [ok('{"path": "direct"}')],
            "direct": [ok("Hello.")],
            "planner": [ok(PLAN)],
            "assignment": [ok(ASSIGN)],
            "specialist": [ok("Step answer.")],
            "final_answer": [ok("100")],
            "verifier": [ok(PASS)],
            "step_verifier": [ok(PASS)],
            "final_verifier": [ok(PASS)],
            "kaido": [ok(f"Degraded hello {SENTINEL}")],
        }
        self.health_replies: list[ProviderHealthStatus] = [HEALTHY]
        self.requests: list[GenerationRequest] = []
        self.hang: set[str] = set()
        self.entered = asyncio.Event()

    def script(self, role: str, *replies: Reply) -> None:
        self.replies[role] = [*replies, *self.replies[role][-1:]]

    async def health(self) -> ProviderHealth:
        status = self.health_replies[0]
        if len(self.health_replies) > 1:
            self.health_replies.pop(0)
        return ProviderHealth(provider_id=self.provider_id, status=status)

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        if model_id not in MODELS:
            raise ModelProviderError("not installed")
        return capabilities(model_id)

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        role = role_of(request)
        self.requests.append(request)
        self.entered.set()
        if role in self.hang:
            await asyncio.Event().wait()
        await asyncio.sleep(0)
        queue = self.replies[role]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        text, finish = reply
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text=text,
            finish_reason=finish,
            usage=TokenUsage(input_tokens=5, output_tokens=3),
        )

    async def stream(
        self, request: GenerationRequest
    ) -> AsyncIterator[GenerationChunk]:
        raise ModelProviderError("streaming is not scripted")
        yield  # pragma: no cover

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.ACCEPTED,
        )

    async def aclose(self) -> None:
        pass

    def roles(self) -> list[str]:
        return [role_of(request) for request in self.requests]


def capabilities(model_id: str) -> ModelCapabilities:
    return ModelCapabilities(
        provider_id="ollama",
        model_id=model_id,
        supports_streaming=True,
        supports_structured_output=False,
        supports_tool_use=True,
        supports_vision=False,
        supports_cancellation=True,
        context_limit_tokens=16384,
    )


def registry(models: tuple[str, ...] = MODELS) -> ModelCapabilityRegistry:
    store = ModelCapabilityRegistry()
    for model in models:
        store.register(capabilities(model))
    return store


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        check_event(event)  # every published event passes the allowlist
        self.events.append(event)

    def types(self) -> list[str]:
        return [event.event_type.value for event in self.events]


class Resources:
    """A scripted resource probe; an Exception entry raises."""

    def __init__(self, *samples: ResourceSample | Exception) -> None:
        self.samples = list(samples) or [ResourceSample(free_ram_mib=16384)]

    def __call__(self) -> ResourceSample:
        sample = self.samples[0]
        if len(self.samples) > 1:
            self.samples.pop(0)
        if isinstance(sample, Exception):
            raise sample
        return sample


PRESSURE = ResourceSample(free_ram_mib=1024)
PLENTY = ResourceSample(free_ram_mib=16384)


def monitor(*samples: ResourceSample | Exception, **options: Any) -> KaidoMonitor:
    return KaidoMonitor(resource_probe=Resources(*samples), **options)


def run(coroutine: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(asyncio.wait_for(coroutine, timeout=20))


def build(
    provider: QueueProvider,
    structured: QueueProvider | None = None,
    **options: Any,
) -> PhoenixRuntime:
    options.setdefault("limits", FAST)
    options.setdefault("lifecycle", ModelLifecycleManager())
    options.setdefault("capability_registry", registry())
    return PhoenixRuntime(
        providers=(provider,),
        structured_providers=() if structured is None else (structured,),
        **options,
    )


def mission(
    provider: QueueProvider,
    path: OrchestrationPath | None = OrchestrationPath.COMPLEX,
    text: str = f"What is water's boiling point? {SENTINEL}",
    **options: Any,
) -> MissionResult:
    async def exercise() -> MissionResult:
        runtime = build(provider, **options)
        result: MissionResult = await runtime.run(
            PhoenixRequest(request_id=MISSION, text=text), path
        )
        assert runtime.active_request_ids == ()
        assert runtime.dropped_events == 0
        return result

    result: MissionResult = run(exercise())
    return result


def states(result: MissionResult) -> list[str]:
    return [state.value for state in result.trace.states]


def no_secret(result: MissionResult, sink: RecordingSink | None = None) -> None:
    assert SENTINEL not in result.trace.model_dump_json()
    if sink is not None:
        for event in sink.events:
            assert SENTINEL not in event.model_dump_json()


# ---- KaidoMonitor: engage, hysteresis, release


def test_monitor_engages_on_provider_down_and_publishes_degraded() -> None:
    sink = RecordingSink()
    kaido = monitor(events=sink)

    async def exercise() -> KaidoStatus:
        return await kaido.observe({"ollama": UNAVAILABLE})

    status = run(exercise())
    assert status.engaged
    assert status.trigger == FailSafeTrigger.PRIMARY_MODEL_PROVIDER_UNAVAILABLE
    assert status.capabilities == KAIDO_CAPABILITIES
    assert FailSafeCapability.SAFE_LIMITED_TOOL_ROUTING not in status.capabilities
    decision = kaido.decision
    assert decision is not None and decision.mode == "degraded"
    assert decision.failsafe_agent_id == "failsafe"
    assert sink.types() == ["phoenix.degraded"]
    event = sink.events[0]
    assert event.agent_id == "failsafe"
    assert event.correlation_id == decision.failsafe_id
    assert dict(event.payload) == {
        "failsafe_id": decision.failsafe_id,
        "kaido_scope": "process",
        "trigger": "primary_model_provider_unavailable",
    }
    assert event.severity.value == "warning"


@pytest.mark.parametrize("status", [DEGRADED, UNAVAILABLE])
def test_no_healthy_provider_engages(status: ProviderHealthStatus) -> None:
    kaido = monitor()
    result = run(kaido.observe({"ollama": status}))
    assert result.engaged
    assert result.trigger == FailSafeTrigger.PRIMARY_MODEL_PROVIDER_UNAVAILABLE
    assert run(kaido.observe({})).engaged  # no provider at all


def test_hysteresis_releases_only_after_consecutive_clear_observations() -> None:
    sink = RecordingSink()
    kaido = monitor(events=sink)

    async def exercise() -> list[KaidoStatus]:
        seen = [await kaido.observe({"ollama": UNAVAILABLE})]
        seen.append(await kaido.observe({"ollama": HEALTHY}))  # clear 1
        seen.append(await kaido.observe({"ollama": UNAVAILABLE}))  # resets
        seen.append(await kaido.observe({"ollama": HEALTHY}))  # clear 1
        seen.append(await kaido.observe({"ollama": HEALTHY}))  # clear 2: released
        seen.append(await kaido.observe({"ollama": HEALTHY}))
        return seen

    seen = run(exercise())
    assert [s.engaged for s in seen] == [True, True, True, True, False, False]
    assert [s.clear_observations for s in seen] == [0, 1, 0, 1, 0, 0]
    # One engagement (no re-publication while engaged), one release.
    assert sink.types() == ["phoenix.degraded", "phoenix.ready"]
    engaged, released = sink.events
    assert released.correlation_id == engaged.correlation_id
    assert dict(released.payload) == {
        "failsafe_id": engaged.payload["failsafe_id"],
        "kaido_scope": "process",
    }
    assert kaido.decision is None


def test_release_after_is_configurable() -> None:
    kaido = monitor(policy=KaidoPolicy(release_after=1))
    run(kaido.observe({"ollama": UNAVAILABLE}))
    assert not run(kaido.observe({"ollama": HEALTHY})).engaged


def test_resource_pressure_engages_with_a_release_band() -> None:
    band = ResourceSample(free_ram_mib=2500)  # between engage 2048 and release 3072
    kaido = monitor(PRESSURE, band, band, PLENTY, PLENTY, band)

    async def exercise() -> list[KaidoStatus]:
        return [await kaido.observe({"ollama": HEALTHY}) for _ in range(6)]

    seen = run(exercise())
    assert seen[0].trigger == FailSafeTrigger.RESOURCE_PRESSURE
    # In the band nothing counts as clear; only two samples above release do.
    assert [s.engaged for s in seen] == [True, True, True, True, False, False]
    # Released, the band alone does not engage again.
    assert not seen[-1].engaged


def test_vram_pressure_engages() -> None:
    kaido = monitor(ResourceSample(free_ram_mib=16384, free_vram_mib=100))
    status = run(kaido.observe({"ollama": HEALTHY}))
    assert status.trigger == FailSafeTrigger.RESOURCE_PRESSURE


def test_provider_down_takes_precedence_over_pressure() -> None:
    kaido = monitor(PRESSURE)
    status = run(kaido.observe({"ollama": UNAVAILABLE}))
    assert status.trigger == FailSafeTrigger.PRIMARY_MODEL_PROVIDER_UNAVAILABLE


def test_failing_resource_probe_is_no_evidence() -> None:
    kaido = monitor(RuntimeError("probe broke"))
    assert not run(kaido.observe({"ollama": HEALTHY})).engaged
    run(kaido.observe({"ollama": UNAVAILABLE}))
    # Engaged, a failing probe never counts as clear.
    for _ in range(3):
        assert run(kaido.observe({"ollama": HEALTHY})).engaged


def test_no_resource_probe_counts_provider_health_only() -> None:
    kaido = KaidoMonitor()
    run(kaido.observe({"ollama": UNAVAILABLE}))
    run(kaido.observe({"ollama": HEALTHY}))
    assert not run(kaido.observe({"ollama": HEALTHY})).engaged


def test_policy_and_status_contracts() -> None:
    with pytest.raises(ValidationError):
        KaidoPolicy(engage_free_ram_mib=4096, release_free_ram_mib=1024)
    with pytest.raises(ValidationError):
        KaidoPolicy(release_after=0)
    with pytest.raises(ValidationError):
        KaidoStatus(engaged=True, release_after=2)
    with pytest.raises(ValidationError):
        ResourceSample(free_ram_mib=-1)


def test_kaido_record_contract() -> None:
    record = {
        "failsafe_id": MISSION,
        "trigger": FailSafeTrigger.RESOURCE_PRESSURE,
        "scope": "process",
        "outcome": "answered",
    }
    assert KaidoRecord.model_validate(record).verified is False
    for change in (
        {"capabilities": (FailSafeCapability.SAFE_LIMITED_TOOL_ROUTING,)},
        {"trigger": FailSafeTrigger.DEEP_PROVIDER_UNAVAILABLE},
        {"trigger": FailSafeTrigger.RECOVERY_EXHAUSTED},  # no recovery ID
        {"scope": "mission"},  # only exhaustion is mission-scoped
        {"recovery_id": MISSION},
        {"verified": True},
        {"outcome": "completed"},
        {"extra": 1},
    ):
        with pytest.raises(ValidationError):
            KaidoRecord.model_validate({**record, **change})


def test_context_budget_fits_every_api_chat_request() -> None:
    assert KAIDO_MAX_OUTPUT_TOKENS == 1024
    assert within_budget("x" * MAX_CHAT_CHARS)
    assert not within_budget("x" * (KAIDO_MAX_INPUT_CHARS - len(KAIDO_SYSTEM) + 1))
    assert KAIDO_SYSTEM.splitlines()[0] == "PHOENIX ROLE: kaido"


def test_tier_table_puts_kaido_on_t0_only() -> None:
    table = default_tier_table()
    assert [ref.model_id for ref in table.roles["failsafe"]] == ["qwen3.5:2b"]


# ---- Process-wide KAIDO in missions


@pytest.mark.parametrize(
    "path", [None, OrchestrationPath.DIRECT, OrchestrationPath.COMPLEX]
)
def test_engaged_mission_is_one_labelled_t0_reply(
    path: OrchestrationPath | None,
) -> None:
    provider, structured, sink = (
        QueueProvider(),
        QueueProvider("structured"),
        RecordingSink(),
    )
    result = mission(
        provider,
        path,
        structured=structured,
        events=sink,
        kaido=monitor(PRESSURE),
    )
    assert result.kind == MissionKind.COMPLETED
    assert result.text == f"{KAIDO_ANSWER_LABEL}\n\nDegraded hello {SENTINEL}"
    # One generation, on the structured (thinking-off) T0, nothing else.
    assert provider.requests == []
    assert structured.roles() == ["kaido"]
    request = structured.requests[0]
    assert request.model_id == "qwen3.5:2b"
    assert request.max_output_tokens == KAIDO_MAX_OUTPUT_TOKENS
    trace = result.trace
    assert trace.kaido is not None
    assert (trace.kaido.trigger, trace.kaido.scope, trace.kaido.outcome) == (
        FailSafeTrigger.RESOURCE_PRESSURE,
        "process",
        "answered",
    )
    assert trace.kaido.verified is False
    assert trace.kaido.capabilities == KAIDO_CAPABILITIES
    # No task, plan, LEVI verification, tool call or recovery.
    assert (trace.task_id, trace.states, trace.verifications) == (None, (), ())
    assert (trace.tool_calls, trace.recoveries) == ((), ())
    assert trace.path == path
    assert [g.role for g in trace.generations] == ["kaido"]
    assert sink.types() == ["phoenix.degraded", "agent.started", "agent.completed"]
    degraded, started, completed = sink.events
    assert degraded.correlation_id == MISSION
    assert dict(degraded.payload) == {
        "failsafe_id": trace.kaido.failsafe_id,
        "kaido_scope": "process",
        "trigger": "resource_pressure",
    }
    assert started.agent_id == completed.agent_id == "failsafe"
    assert started.payload["role"] == "kaido"
    no_secret(result, sink)


def test_kaido_answers_on_a_degraded_provider() -> None:
    provider = QueueProvider()
    provider.health_replies = [DEGRADED]
    result = mission(provider, kaido=monitor())
    assert result.kind == MissionKind.COMPLETED
    assert result.trace.kaido is not None
    assert result.trace.kaido.trigger == (
        FailSafeTrigger.PRIMARY_MODEL_PROVIDER_UNAVAILABLE
    )
    assert provider.roles() == ["kaido"]


def test_unavailable_provider_fails_explicitly_without_generation() -> None:
    provider, sink = QueueProvider(), RecordingSink()
    provider.health_replies = [UNAVAILABLE]
    result = mission(provider, events=sink, kaido=monitor())
    assert (result.kind, result.failure_code) == (
        MissionKind.FAILED,
        MissionFailureCode.PROVIDER_UNAVAILABLE,
    )
    assert result.trace.kaido is not None
    assert result.trace.kaido.outcome == "unavailable"
    assert provider.requests == []
    assert sink.types() == ["phoenix.degraded"]


def test_unregistered_t0_is_no_eligible_model() -> None:
    provider = QueueProvider()
    result = mission(
        provider,
        kaido=monitor(PRESSURE),
        capability_registry=registry(("qwen3.5:4b",)),
    )
    assert (result.kind, result.failure_code) == (
        MissionKind.FAILED,
        MissionFailureCode.NO_ELIGIBLE_MODEL,
    )
    assert result.trace.kaido is not None
    assert result.trace.kaido.outcome == "unavailable"
    assert provider.requests == []


def test_request_beyond_the_8k_budget_is_declined() -> None:
    provider = QueueProvider()
    result = mission(
        provider, text="x" * KAIDO_MAX_INPUT_CHARS, kaido=monitor(PRESSURE)
    )
    assert (result.kind, result.failure_code) == (
        MissionKind.FAILED,
        MissionFailureCode.NO_ELIGIBLE_MODEL,
    )
    assert result.trace.kaido is not None
    assert result.trace.kaido.outcome == "declined"
    assert provider.requests == []


def test_kaido_reply_is_single_and_its_failure_is_explicit() -> None:
    provider, sink = QueueProvider(), RecordingSink()
    provider.script("kaido", (f"cut {SENTINEL}", "length"))
    result = mission(provider, events=sink, kaido=monitor(PRESSURE))
    assert (result.kind, result.failure_code, result.responder_error) == (
        MissionKind.FAILED,
        MissionFailureCode.RESPONDER_OUTPUT,
        ResponderErrorCode.TRUNCATED,
    )
    assert provider.roles() == ["kaido"]  # never retried
    assert result.trace.kaido is not None
    assert result.trace.kaido.outcome == "failed"
    assert sink.types()[-1] == "agent.failed"
    no_secret(result, sink)


def test_released_kaido_returns_missions_to_normal() -> None:
    provider = QueueProvider()
    kaido = monitor(PRESSURE, PLENTY)

    async def exercise() -> list[MissionResult]:
        runtime = build(provider, kaido=kaido)
        results = []
        for index in range(3):
            request = PhoenixRequest(
                request_id=f"0b6f5f6e-6a43-4a3e-9d8b-4b0f8e9c1d2{index}", text="Hi."
            )
            results.append(await runtime.run(request, OrchestrationPath.DIRECT))
        return results

    first, second, third = run(exercise())
    # Engaged, still engaged after one clear observation, then released.
    assert first.trace.kaido is not None and second.trace.kaido is not None
    assert third.trace.kaido is None and third.text == "Hello."
    assert provider.roles() == ["kaido", "kaido", "direct"]


def test_without_a_monitor_nothing_changes() -> None:
    provider = QueueProvider()
    provider.health_replies = [UNAVAILABLE]
    result = mission(provider)
    assert (result.kind, result.failure_code) == (
        MissionKind.FAILED,
        MissionFailureCode.PROVIDER_UNAVAILABLE,
    )
    assert result.trace.kaido is None
    assert states(result) == ["created", "planning", "failed"]


def test_runtime_requires_an_exact_monitor() -> None:
    with pytest.raises(PhoenixRuntimeError):
        build(QueueProvider(), kaido=object())


def test_cancellation_during_kaido_wins() -> None:
    provider = QueueProvider()
    provider.hang.add("kaido")

    async def exercise() -> MissionResult:
        runtime = build(provider, kaido=monitor(PRESSURE))
        task = asyncio.create_task(
            runtime.run(PhoenixRequest(request_id=MISSION, text="Hi."))
        )
        await provider.entered.wait()
        await runtime.cancel(MISSION)
        result: MissionResult = await task
        return result

    result = run(exercise())
    assert result.kind == MissionKind.CANCELLED
    assert result.trace.kaido is not None
    assert result.trace.kaido.outcome == "failed"


# ---- KAIDO after MAHORAGA's mission budget is exhausted


def test_exhausted_budget_ends_degraded_with_a_kaido_answer() -> None:
    provider, sink = QueueProvider(), RecordingSink()
    provider.script("planner", NOT_JSON)  # recovered: the one action is spent
    provider.script("assignment", NOT_JSON)  # exhausted
    result = mission(
        provider,
        events=sink,
        kaido=monitor(),
        limits=RuntimeLimits(recovery_backoff_seconds=0.0, max_recovery_actions=1),
    )
    assert result.kind == MissionKind.COMPLETED
    assert result.text is not None and result.text.startswith(KAIDO_ANSWER_LABEL)
    trace = result.trace
    assert [(r.role.value, r.outcome.value) for r in trace.recoveries] == [
        ("planner", "recovered"),
        ("assignment", "exhausted"),
    ]
    assert trace.kaido is not None
    assert (trace.kaido.trigger, trace.kaido.scope, trace.kaido.outcome) == (
        FailSafeTrigger.RECOVERY_EXHAUSTED,
        "mission",
        "answered",
    )
    assert trace.kaido.recovery_id == trace.recoveries[-1].recovery_id
    assert states(result) == ["created", "planning", "failed", "degraded"]
    assert TaskState.COMPLETED not in trace.states
    changes = [
        dict(e.payload)
        for e in sink.events
        if e.event_type.value == "task.state_changed"
    ]
    assert changes[-2] == {
        "scope": "task",
        "from_state": "planning",
        "to_state": "failed",
        "failure_code": "responder_output",
        "responder_error": "not_json",
    }
    assert changes[-1] == {
        "scope": "task",
        "from_state": "failed",
        "to_state": "degraded",
    }
    degraded = [e for e in sink.events if e.event_type.value == "phoenix.degraded"]
    assert len(degraded) == 1
    assert dict(degraded[0].payload)["kaido_scope"] == "mission"
    assert provider.roles()[-1] == "kaido"
    no_secret(result, sink)


def test_exhausted_provider_failure_gets_a_kaido_answer() -> None:
    provider = QueueProvider()
    provider.script("planner", NOT_JSON)  # recovered: the one action is spent
    provider.script("assignment", ModelProviderError(f"bad {SENTINEL}"))
    result = mission(
        provider,
        kaido=monitor(),
        limits=RuntimeLimits(recovery_backoff_seconds=0.0, max_recovery_actions=1),
    )
    assert result.kind == MissionKind.COMPLETED
    assert result.trace.recoveries[-1].recovery_class.value == "provider_error"
    assert result.trace.kaido is not None
    assert result.trace.kaido.outcome == "answered"
    assert states(result) == ["created", "planning", "failed", "degraded"]
    no_secret(result)


def test_zero_budget_is_not_exhaustion() -> None:
    provider = QueueProvider()
    provider.script("planner", NOT_JSON)
    result = mission(
        provider,
        kaido=monitor(),
        limits=RuntimeLimits(recovery_backoff_seconds=0.0, max_recovery_actions=0),
    )
    # Recovery is off: no MAHORAGA evidence, so no KAIDO answer.
    assert (result.kind, result.failure_code) == (
        MissionKind.FAILED,
        MissionFailureCode.RESPONDER_OUTPUT,
    )
    assert result.trace.kaido is None
    assert "kaido" not in provider.roles()


def test_exhausted_levi_generation_never_gets_a_kaido_answer() -> None:
    provider = QueueProvider()
    provider.script("planner", NOT_JSON)
    provider.script("final_verifier", NOT_JSON)
    result = mission(
        provider,
        kaido=monitor(),
        limits=RuntimeLimits(recovery_backoff_seconds=0.0, max_recovery_actions=1),
    )
    assert (result.kind, result.failure_code) == (
        MissionKind.FAILED,
        MissionFailureCode.RESPONDER_OUTPUT,
    )
    assert result.trace.recoveries[-1].outcome == RecoveryOutcome.EXHAUSTED
    assert result.trace.kaido is None
    assert "kaido" not in provider.roles()


def test_levi_rejection_never_gets_a_kaido_answer() -> None:
    provider = QueueProvider()
    provider.script("final_verifier", ok(FAIL))
    result = mission(provider, kaido=monitor())
    assert (result.kind, result.failure_code) == (
        MissionKind.FAILED,
        MissionFailureCode.VERIFICATION_FAILED,
    )
    assert result.trace.kaido is None
    assert "kaido" not in provider.roles()


def test_kaido_failing_after_exhaustion_keeps_the_original_failure() -> None:
    provider = QueueProvider()
    provider.script("planner", NOT_JSON)
    provider.script("assignment", NOT_JSON)
    provider.script("kaido", ModelProviderError("down"))
    result = mission(
        provider,
        kaido=monitor(),
        limits=RuntimeLimits(recovery_backoff_seconds=0.0, max_recovery_actions=1),
    )
    assert (result.kind, result.failure_code, result.responder_error) == (
        MissionKind.FAILED,
        MissionFailureCode.RESPONDER_OUTPUT,
        ResponderErrorCode.NOT_JSON,
    )
    assert states(result) == ["created", "planning", "failed"]
    assert result.trace.kaido is not None
    assert result.trace.kaido.outcome == "failed"


def test_without_a_monitor_exhaustion_fails_as_before() -> None:
    provider = QueueProvider()
    provider.script("planner", NOT_JSON)
    provider.script("assignment", NOT_JSON)
    result = mission(
        provider,
        limits=RuntimeLimits(recovery_backoff_seconds=0.0, max_recovery_actions=1),
    )
    assert result.kind == MissionKind.FAILED and result.trace.kaido is None
    assert states(result) == ["created", "planning", "failed"]


# ---- Trace and allowlist contracts


def _trace(**changes: Any) -> dict[str, Any]:
    return {"prompt_set_version": "v", "path": "complex", **changes}


def test_trace_rejects_a_kaido_answer_that_completes_or_lacks_evidence() -> None:
    answered = {
        "failsafe_id": MISSION,
        "trigger": "resource_pressure",
        "scope": "process",
        "outcome": "answered",
    }
    MissionTrace.model_validate(_trace(kaido=answered))
    with pytest.raises(ValidationError):
        MissionTrace.model_validate(
            _trace(kaido=answered, states=("created", "planning", "completed"))
        )
    with pytest.raises(ValidationError):
        MissionTrace.model_validate(
            _trace(kaido=answered, states=("created", "planning", "failed"))
        )
    exhausted = {
        **answered,
        "trigger": "recovery_exhausted",
        "scope": "mission",
        "recovery_id": MISSION,
    }
    with pytest.raises(ValidationError):  # no exhausted record with that ID
        MissionTrace.model_validate(_trace(kaido=exhausted))
    record = RecoveryRecord.model_validate(
        {
            "recovery_id": MISSION,
            "role": "assignment",
            "agent_id": "phoenix",
            "recovery_class": "responder_output",
            "responder_error": "not_json",
            "outcome": "exhausted",
        }
    )
    MissionTrace.model_validate(
        _trace(
            kaido=exhausted,
            recoveries=(record,),
            states=("created", "planning", "failed", "degraded"),
        )
    )


def test_traces_stored_before_phx_119_still_load() -> None:
    trace = MissionTrace.model_validate(_trace())
    assert trace.kaido is None


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        (EventType.PHOENIX_DEGRADED, {"trigger": f"x {SENTINEL}"}),
        (EventType.PHOENIX_DEGRADED, {"trigger": "deep_provider_unavailable"}),
        (EventType.PHOENIX_DEGRADED, {"failsafe_id": SENTINEL}),
        (EventType.PHOENIX_DEGRADED, {"kaido_scope": "everything"}),
        (EventType.PHOENIX_READY, {"trigger": "resource_pressure"}),
        (EventType.PHOENIX_READY, {"reason": "text"}),
    ],
)
def test_kaido_allowlist_takes_only_closed_values(
    event_type: EventType, payload: dict[str, object]
) -> None:
    with pytest.raises(EventPayloadError):
        check_payload(event_type, payload)


def test_kaido_allowlist_accepts_its_closed_values() -> None:
    for trigger in mission_events.KAIDO_TRIGGERS:
        check_payload(
            EventType.PHOENIX_DEGRADED,
            {"failsafe_id": MISSION, "kaido_scope": "mission", "trigger": trigger},
        )
    check_payload(
        EventType.PHOENIX_READY, {"failsafe_id": MISSION, "kaido_scope": "process"}
    )
    assert "kaido" in mission_events.AGENT_ROLES
    assert len(EventType) == 32


# ---- Backend: /ready and the mission API


TOKEN = "kaido-test-token-0123456789abcdef0123456789"
PORT = 48766
SECURITY = LocalApiSecurity(TOKEN, host="127.0.0.1", port=PORT)


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def refuse_send(*args: object, **kwargs: object) -> None:
        raise AssertionError("httpx network send attempted")

    monkeypatch.setattr(httpx.AsyncClient, "send", refuse_send)


def backend_client(
    tmp_path: Path, provider: QueueProvider, **config: Any
) -> TestClient:
    settings = AppSettings(data_dir=tmp_path / "data", _env_file=None)  # type: ignore[call-arg]
    structured = QueueProvider("structured")
    app = build_server_app(
        settings,
        security=SECURITY,
        config=ServerConfig(
            probe_timeout_seconds=0.5, startup_timeout_seconds=5, **config
        ),
        provider_factory=lambda _: ProviderSet(
            providers=(provider,), structured=(structured,)
        ),
    )
    return TestClient(
        app,
        base_url=f"http://127.0.0.1:{PORT}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def test_ready_reports_kaido_and_releases_with_hysteresis(tmp_path: Path) -> None:
    provider = QueueProvider()
    # Healthy for startup registration, down twice, then healthy again.
    provider.health_replies = [HEALTHY, UNAVAILABLE, UNAVAILABLE, HEALTHY]
    with backend_client(tmp_path, provider, kaido_ram_probe=False) as client:
        first = client.get("/ready").json()
        assert first["status"] == "degraded"
        assert first["kaido"]["engaged"] is True
        assert first["kaido"]["trigger"] == "primary_model_provider_unavailable"
        assert first["kaido"]["capabilities"] == [
            "basic_conversation",
            "diagnostics",
            "recovery_guidance",
        ]
        assert client.get("/ready").json()["kaido"]["engaged"] is True
        recovering = client.get("/ready").json()  # healthy: clear 1 of 2
        assert recovering["status"] == "degraded"
        assert recovering["kaido"]["clear_observations"] == 1
        released = client.get("/ready").json()
        assert released["status"] == "ready"
        assert released["kaido"] == {
            "engaged": False,
            "failsafe_id": None,
            "trigger": None,
            "capabilities": [],
            "clear_observations": 0,
            "release_after": 2,
        }


def test_mission_api_shows_a_kaido_answer(tmp_path: Path) -> None:
    provider = QueueProvider()
    # Thresholds no machine meets: the RAM sample always reports pressure.
    policy = KaidoPolicy(engage_free_ram_mib=2**40, release_free_ram_mib=2**40)
    with backend_client(tmp_path, provider, kaido_policy=policy) as client:
        response = client.post("/chat", json={"text": f"Hi {SENTINEL}"})
        assert response.status_code == 202, response.text
        mission_id = response.json()["mission_id"]
        deadline = time.monotonic() + 20
        while True:
            body = client.get(f"/missions/{mission_id}").json()
            if body["status"] in {"completed", "failed", "cancelled"}:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        assert body["status"] == "completed"
        assert body["answer_text"].startswith(KAIDO_ANSWER_LABEL)
        kaido = body["kaido"]
        assert kaido["trigger"] == "resource_pressure"
        assert (kaido["scope"], kaido["outcome"], kaido["verified"]) == (
            "process",
            "answered",
            False,
        )
        assert kaido["recovery_id"] is None
        assert client.get("/ready").json()["kaido"]["engaged"] is True
