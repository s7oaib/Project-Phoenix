"""PHX-109 bounded MAHORAGA recovery in PHOENIX missions, offline only.

Every collaborator is real except the model: a scripted ModelProvider answers
by the system prompt's role marker, from a per-role queue (the last reply
repeats). No network, Ollama or GPU is used.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Coroutine
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from phoenix.agents import mission_events
from phoenix.agents.mission_events import EventPayloadError, check_event, check_payload
from phoenix.agents.mission_recovery import (
    MAX_RECOVERY_ACTIONS,
    RETRYABLE_OUTPUT,
    RecoveryClass,
    RecoveryOutcome,
    RecoveryRecord,
    RecoveryRole,
    classify,
    retryable,
)
from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest
from phoenix.agents.recovery import RecoveryAction
from phoenix.agents.responders import ResponderErrorCode, ResponderOutputError
from phoenix.agents.runtime import (
    MissionFailureCode,
    MissionKind,
    MissionResult,
    MissionTrace,
    PhoenixRuntime,
    RuntimeLimits,
    _VerificationFailed,
)
from phoenix.agents.task_graph import TaskState
from phoenix.agents.tool_steps import (
    ToolPermissionRequired,
    ToolStepFailed,
    ToolStepRejected,
)
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
    ProviderUnavailableError,
    TokenUsage,
)
from phoenix.models.registry import ModelCapabilityRegistry
from phoenix.security.permissions import PermissionRequest
from phoenix.security.prompt_injection import (
    PromptInjectionError,
    PromptInjectionErrorCode,
)

MODELS = ("qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b", "qwen2.5-coder:7b")
PASS = '{"checks":[{"criterion":"Correct","passed":true,"evidence_summary":"Fine."}]}'
FAIL = '{"checks":[{"criterion":"Correct","passed":false,"evidence_summary":"No."}]}'
SENTINEL = "SENTINEL-7f3a-PRIVATE"
MISSION = "0b6f5f6e-6a43-4a3e-9d8b-4b0f8e9c1d2a"
# Fast backoff for tests; the default (2 s) is asserted separately.
FAST = RuntimeLimits(recovery_backoff_seconds=0.0)
# A reply: text with its finish reason, or an exception to raise.
Reply = tuple[str, str] | Exception


def role_of(request: GenerationRequest) -> str:
    return request.messages[0].text.splitlines()[0].removeprefix("PHOENIX ROLE: ")


def ok(text: str) -> Reply:
    return (text, "stop")


TRUNCATED: Reply = (f"cut {SENTINEL}", "length")
NOT_JSON: Reply = ok(f"no json here {SENTINEL}")
# PHX-125: an assignment to a domain this mission did not offer.
DISALLOWED: Reply = ok(
    '{"assignments": [{"step_id": "s1", "domain": "documents"},'
    ' {"step_id": "s2", "domain": "build_engineering"}]}'
)
# PHX-125: a repeated key is a schema error, not "the last value wins".
DUPLICATE_KEY: Reply = ok(
    '{"steps": [{"objective": "Recall."}, {"objective": "Code."}],'
    ' "steps": [{"objective": "Recall."}]}'
)


class QueueProvider(ModelProvider):
    """Per-role reply queues; the last reply of a queue repeats."""

    def __init__(self) -> None:
        super().__init__("ollama")
        self.replies: dict[str, list[Reply]] = {
            "direct": [ok("Hello.")],
            "planner": [
                ok(
                    json.dumps(
                        {"steps": [{"objective": "Recall."}, {"objective": "Code."}]}
                    )
                )
            ],
            "assignment": [
                ok(
                    json.dumps(
                        {
                            "assignments": [
                                {"step_id": "s1", "domain": "intelligence"},
                                {"step_id": "s2", "domain": "build_engineering"},
                            ]
                        }
                    )
                )
            ],
            "specialist": [ok("Step answer.")],
            "final_answer": [ok("100")],
            "verifier": [ok(PASS)],
            "step_verifier": [ok(PASS)],
            "final_verifier": [ok(PASS)],
        }
        self.health_replies: list[ProviderHealthStatus] = [ProviderHealthStatus.HEALTHY]
        self.health_calls = 0
        self.hang: set[str] = set()
        self.requests: list[GenerationRequest] = []
        self.cancelled: list[str] = []
        self.entered = asyncio.Event()
        self.running = 0
        self.max_running = 0

    def script(self, role: str, *replies: Reply) -> None:
        self.replies[role] = [*replies, *self.replies[role][-1:]]

    async def health(self) -> ProviderHealth:
        self.health_calls += 1
        status = self.health_replies[0]
        if len(self.health_replies) > 1:
            self.health_replies.pop(0)
        return ProviderHealth(provider_id=self.provider_id, status=status)

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        return capabilities(model_id)

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        role = role_of(request)
        self.requests.append(request)
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            self.entered.set()
            if role in self.hang:
                await asyncio.Event().wait()
            await asyncio.sleep(0)
            queue = self.replies[role]
            reply = queue.pop(0) if len(queue) > 1 else queue[0]
        finally:
            self.running -= 1
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
        self.cancelled.append(operation_id)
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.ACCEPTED,
        )

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


def registry() -> ModelCapabilityRegistry:
    store = ModelCapabilityRegistry()
    for model in MODELS:
        store.register(capabilities(model))
    return store


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.events.append(event)


def build(provider: QueueProvider, **options: Any) -> PhoenixRuntime:
    options.setdefault("limits", FAST)
    options.setdefault("lifecycle", ModelLifecycleManager())
    return PhoenixRuntime(
        providers=(provider,), capability_registry=registry(), **options
    )


def run(coroutine: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(asyncio.wait_for(coroutine, timeout=20))


def mission(
    provider: QueueProvider,
    path: OrchestrationPath = OrchestrationPath.COMPLEX,
    text: str = "What is water's boiling point?",
    **options: Any,
) -> MissionResult:
    async def exercise() -> MissionResult:
        runtime = build(provider, **options)
        result: MissionResult = await runtime.run(
            PhoenixRequest(request_id=MISSION, text=text), path
        )
        assert runtime.active_request_ids == ()
        assert runtime.dropped_events == 0  # every recovery event passed
        return result

    result: MissionResult = run(exercise())
    return result


def recoveries(result: MissionResult) -> list[tuple[str, ...]]:
    return [
        (
            item.role.value,
            item.recovery_class.value,
            "" if item.responder_error is None else item.responder_error.value,
            "" if item.action is None else item.action.value,
            item.outcome.value,
        )
        for item in result.trace.recoveries
    ]


def states(result: MissionResult) -> list[str]:
    return [state.value for state in result.trace.states]


# ---- Each retryable class recovers on the second try.


@pytest.mark.parametrize(
    ("role", "reply", "recovery_role", "recovery_class", "error"),
    [
        ("planner", NOT_JSON, "planner", "responder_output", "not_json"),
        ("planner", TRUNCATED, "planner", "responder_output", "truncated"),
        ("planner", DUPLICATE_KEY, "planner", "responder_output", "schema"),
        (
            "assignment",
            DISALLOWED,
            "assignment",
            "responder_output",
            "disallowed_domain",
        ),
        (
            "assignment",
            ok('{"assignments": []}'),
            "assignment",
            "responder_output",
            "schema",
        ),
        ("specialist", TRUNCATED, "specialist", "responder_output", "truncated"),
        ("verifier", NOT_JSON, "verification", "responder_output", "not_json"),
        (
            "final_verifier",
            ok('{"checks": []}'),
            "verification",
            "responder_output",
            "schema",
        ),
        ("final_answer", TRUNCATED, "final_answer", "responder_output", "truncated"),
        (
            "specialist",
            ProviderUnavailableError(f"down {SENTINEL}"),
            "specialist",
            "provider_unavailable",
            "",
        ),
        (
            "final_answer",
            ModelProviderError(f"bad {SENTINEL}"),
            "final_answer",
            "provider_error",
            "",
        ),
    ],
)
def test_each_retryable_class_recovers_on_the_second_try(
    role: str, reply: Reply, recovery_role: str, recovery_class: str, error: str
) -> None:
    provider = QueueProvider()
    provider.script(role, reply)
    result = mission(provider)
    assert (result.kind, result.text) == (MissionKind.COMPLETED, "100")
    assert recoveries(result) == [
        (recovery_role, recovery_class, error, "retry_same", "recovered")
    ]
    # One more generation than a clean run (where s1 and s2 both specialize).
    assert provider.roles().count(role) == (3 if role == "specialist" else 2)
    # LEVI still verified every step, the retried one included (INV-008).
    assert [item.verdict.value for item in result.trace.verifications] == ["passed"] * 3
    assert states(result)[-1] == "completed"
    item = result.trace.recoveries[0]
    assert (item.agent_id, item.step_id) == {
        "planner": ("strategist", None),
        "assignment": ("phoenix", None),
        "specialist": ("intelligence", "s1"),
        "verifier": ("verifier", "s1"),
        "final_verifier": ("verifier", "final"),
        "final_answer": ("user_interaction", "final"),
    }[role]


def test_direct_path_generation_recovers() -> None:
    provider = QueueProvider()
    provider.script("direct", TRUNCATED)
    result = mission(provider, OrchestrationPath.DIRECT)
    assert (result.kind, result.text) == (MissionKind.COMPLETED, "Hello.")
    assert recoveries(result) == [
        ("direct", "responder_output", "truncated", "retry_same", "recovered")
    ]
    assert result.trace.recoveries[0].agent_id == "phoenix"
    assert result.trace.states == ()


def test_provider_retry_waits_and_rechecks_health() -> None:
    provider = QueueProvider()
    provider.script("specialist", ProviderUnavailableError("down"))
    clock = time.monotonic()
    result = mission(provider, limits=RuntimeLimits(recovery_backoff_seconds=0.2))
    assert time.monotonic() - clock >= 0.2
    assert result.kind == MissionKind.COMPLETED
    assert provider.health_calls == 2  # the mission's probe, then the re-check
    assert RuntimeLimits().recovery_backoff_seconds == 2.0
    assert RuntimeLimits().max_recovery_actions == MAX_RECOVERY_ACTIONS == 3


# ---- Provider down: backoff, health re-check, then an explicit failure.


@pytest.mark.parametrize(
    "error",
    [ProviderUnavailableError("down"), ModelProviderError("bad")],
)
def test_provider_still_down_fails_explicitly_without_a_retry(
    error: Exception,
) -> None:
    provider = QueueProvider()
    provider.script("specialist", error)
    provider.health_replies = [
        ProviderHealthStatus.HEALTHY,
        ProviderHealthStatus.UNAVAILABLE,
    ]
    result = mission(provider)
    code = classify(error)[0].value
    assert (result.kind, result.failure_code) == (
        MissionKind.FAILED,
        MissionFailureCode(code),
    )
    assert recoveries(result) == [("specialist", code, "", "retry_same", "unavailable")]
    assert provider.roles().count("specialist") == 1
    assert states(result)[-2:] == ["running", "failed"]


def test_provider_failing_again_after_a_healthy_recheck_keeps_its_code() -> None:
    provider = QueueProvider()
    provider.script(
        "specialist", ProviderUnavailableError("down"), ModelProviderError("bad")
    )
    result = mission(provider)
    assert result.failure_code == MissionFailureCode.PROVIDER_UNAVAILABLE
    assert recoveries(result) == [
        ("specialist", "provider_unavailable", "", "retry_same", "failed")
    ]


# ---- Budget: one retry per generation, three actions per mission.


def test_a_retry_failing_again_keeps_the_original_code() -> None:
    provider = QueueProvider()
    provider.script("verifier", NOT_JSON, TRUNCATED)
    result = mission(provider)
    assert (result.failure_code, result.responder_error) == (
        MissionFailureCode.RESPONDER_OUTPUT,
        ResponderErrorCode.NOT_JSON,
    )
    assert recoveries(result) == [
        ("verification", "responder_output", "not_json", "retry_same", "failed")
    ]
    assert provider.roles().count("verifier") == 2  # never a third attempt


def test_exhausted_budget_fails_with_the_original_code() -> None:
    provider = QueueProvider()
    sink = RecordingSink()
    provider.script("planner", NOT_JSON)
    provider.script("assignment", NOT_JSON)
    provider.script("specialist", TRUNCATED)  # s1 recovers on try 2
    provider.script("step_verifier", ok('{"checks": []}'))
    result = mission(provider, events=sink)
    assert (result.kind, result.failure_code, result.responder_error) == (
        MissionKind.FAILED,
        MissionFailureCode.RESPONDER_OUTPUT,
        ResponderErrorCode.SCHEMA,
    )
    assert recoveries(result) == [
        ("planner", "responder_output", "not_json", "retry_same", "recovered"),
        ("assignment", "responder_output", "not_json", "retry_same", "recovered"),
        ("specialist", "responder_output", "truncated", "retry_same", "recovered"),
        ("verification", "responder_output", "schema", "", "exhausted"),
    ]
    assert provider.roles().count("step_verifier") == 1
    exhausted = [e for e in sink.events if e.event_type == EventType.RECOVERY_EXHAUSTED]
    assert len(exhausted) == 1 and exhausted[0].agent_id == "adaptive_recovery"
    assert dict(exhausted[0].payload) == {
        "recovery_id": result.trace.recoveries[-1].recovery_id,
        "role": "verification",
        "step_id": "s2",
        "recovery_class": "responder_output",
        "responder_error": "schema",
    }


def test_zero_budget_disables_recovery() -> None:
    provider = QueueProvider()
    provider.script("specialist", TRUNCATED)
    result = mission(provider, limits=RuntimeLimits(max_recovery_actions=0))
    assert result.responder_error == ResponderErrorCode.TRUNCATED
    assert result.trace.recoveries == ()
    assert provider.roles().count("specialist") == 1
    with pytest.raises(ValidationError):
        RuntimeLimits(max_recovery_actions=4)
    with pytest.raises(ValidationError):
        RuntimeLimits(recovery_backoff_seconds=31)


# ---- LEVI's rejection stands.


@pytest.mark.parametrize("role", ["verifier", "step_verifier", "final_verifier"])
def test_verification_failed_is_never_retried_or_success(role: str) -> None:
    provider = QueueProvider()
    provider.script("planner", NOT_JSON)  # a recovery earlier in the mission
    provider.replies[role] = [ok(FAIL)]
    result = mission(provider)
    assert (result.kind, result.text, result.failure_code) == (
        MissionKind.FAILED,
        None,
        MissionFailureCode.VERIFICATION_FAILED,
    )
    assert provider.roles().count(role) == 1
    assert [item.role for item in result.trace.recoveries] == [RecoveryRole.PLANNER]
    assert result.trace.verifications[-1].verdict.value == "failed"
    assert "completed" not in states(result)


def test_non_transient_failures_are_never_retried() -> None:
    for error in (
        TimeoutError(),
        asyncio.CancelledError(),
        ToolPermissionRequired(_permission()),
        ToolStepRejected(),
        ToolStepFailed(),
        PromptInjectionError(PromptInjectionErrorCode.INVALID_INPUT),
        _VerificationFailed(),
        *(
            ResponderOutputError(code)
            for code in ResponderErrorCode
            if code not in RETRYABLE_OUTPUT
        ),
    ):
        assert not retryable(error)
        with pytest.raises(LookupError):
            classify(error)
    assert {item.value for item in RecoveryClass} <= {
        item.value for item in MissionFailureCode
    }
    assert RETRYABLE_OUTPUT == {
        ResponderErrorCode.NOT_JSON,
        ResponderErrorCode.SCHEMA,
        ResponderErrorCode.TRUNCATED,
        ResponderErrorCode.DISALLOWED_DOMAIN,  # PHX-125 (ADR-197)
    }


def test_disallowed_domain_retries_once_then_keeps_its_code() -> None:
    # PHX-125 (ADR-197): one retry within the existing budgets, never a third.
    provider = QueueProvider()
    provider.script("assignment", DISALLOWED, DISALLOWED)
    result = mission(provider)
    assert (result.kind, result.failure_code, result.responder_error) == (
        MissionKind.FAILED,
        MissionFailureCode.RESPONDER_OUTPUT,
        ResponderErrorCode.DISALLOWED_DOMAIN,
    )
    assert recoveries(result) == [
        ("assignment", "responder_output", "disallowed_domain", "retry_same", "failed")
    ]
    assert provider.roles().count("assignment") == 2
    assert "specialist" not in provider.roles()


def _permission() -> PermissionRequest:
    return PermissionRequest(
        request_id=MISSION,
        actor_id="librarian",
        tool_id="filesystem.read_text",
        action="read_text",
        resource="notes.txt",
        reason="Read one file named by the user.",
        requested_at=datetime.now(UTC),
    )


def test_step_timeout_and_cancellation_are_not_retried() -> None:
    provider = QueueProvider()
    provider.hang.add("specialist")
    result = mission(provider, limits=RuntimeLimits(step_timeout_seconds=0.05))
    assert result.failure_code == MissionFailureCode.TIMEOUT
    assert result.trace.recoveries == ()
    assert provider.roles().count("specialist") == 1

    provider = QueueProvider()
    provider.hang.add("planner")

    async def cancel() -> MissionResult:
        runtime = build(provider)
        task = asyncio.create_task(
            runtime.run(
                PhoenixRequest(request_id=MISSION, text="Plan."),
                OrchestrationPath.COMPLEX,
            )
        )
        await provider.entered.wait()
        await runtime.cancel(MISSION)
        result: MissionResult = await task
        return result

    result = run(cancel())
    assert result.kind == MissionKind.CANCELLED
    assert result.trace.recoveries == ()
    assert provider.roles() == ["planner"]


def test_cancellation_during_backoff_is_recorded_and_wins() -> None:
    provider = QueueProvider()
    provider.script("specialist", ProviderUnavailableError("down"))
    sink = RecordingSink()

    async def scenario() -> MissionResult:
        runtime = build(
            provider, limits=RuntimeLimits(recovery_backoff_seconds=5), events=sink
        )
        task = asyncio.create_task(
            runtime.run(
                PhoenixRequest(request_id=MISSION, text="Plan."),
                OrchestrationPath.COMPLEX,
            )
        )
        while not any(e.event_type == EventType.RECOVERY_STARTED for e in sink.events):
            await asyncio.sleep(0.005)
        await runtime.cancel(MISSION)
        result: MissionResult = await task
        return result

    result = run(scenario())
    assert result.kind == MissionKind.CANCELLED
    assert recoveries(result) == [
        ("specialist", "provider_unavailable", "", "retry_same", "cancelled")
    ]
    assert provider.roles().count("specialist") == 1
    completed = [e for e in sink.events if e.event_type == EventType.RECOVERY_COMPLETED]
    assert [e.payload["outcome"] for e in completed] == ["cancelled"]


# ---- A failed plan falls back to the DIRECT path, observably.


def test_empty_plan_retries_then_falls_back_to_direct() -> None:
    provider = QueueProvider()
    provider.replies["planner"] = [ok('{"steps": []}')]
    sink = RecordingSink()
    result = mission(provider, events=sink)
    assert (result.kind, result.text) == (MissionKind.COMPLETED, "Hello.")
    assert provider.roles() == ["planner", "planner", "direct"]
    assert recoveries(result) == [
        ("planner", "responder_output", "schema", "retry_same", "failed"),
        ("planner", "responder_output", "schema", "switch_agent", "recovered"),
    ]
    trace = result.trace
    # Never COMPLEX success: the path stays the decided one, the task FAILED,
    # and nothing was verified.
    assert trace.path == OrchestrationPath.COMPLEX
    assert states(result) == ["created", "planning", "failed"]
    assert trace.verifications == ()
    assert [item.direct_fallback for item in trace.recoveries] == [False, True]
    kinds = [
        (e.event_type.value, e.payload.get("role") or e.payload.get("to_state"))
        for e in sink.events
    ]
    assert kinds[2:] == [
        ("agent.started", "planner"),
        ("recovery.started", "planner"),
        ("recovery.completed", "planner"),
        ("agent.failed", "planner"),
        ("recovery.started", "planner"),
        ("task.state_changed", "failed"),
        ("agent.started", "direct"),
        ("agent.completed", "direct"),
        ("recovery.completed", "planner"),
    ]
    failed = sink.events[7].payload
    assert (failed["failure_code"], failed["responder_error"]) == (
        "responder_output",
        "schema",
    )
    assert sink.events[6].payload["action"] == "switch_agent"
    assert sink.events[-1].payload["outcome"] == "recovered"
    # A forged trace cannot claim a completed complex task after a fallback.
    with pytest.raises(ValidationError):
        MissionTrace.model_validate(
            {
                **trace.model_dump(),
                "states": (*trace.states[:-1], TaskState.READY, TaskState.COMPLETED),
            }
        )


def test_fallback_that_fails_keeps_the_plans_code() -> None:
    provider = QueueProvider()
    provider.replies["planner"] = [ok('{"steps": []}')]
    provider.replies["direct"] = [TRUNCATED]
    result = mission(provider)
    assert (result.kind, result.failure_code, result.responder_error) == (
        MissionKind.FAILED,
        MissionFailureCode.RESPONDER_OUTPUT,
        ResponderErrorCode.SCHEMA,
    )
    # The direct retry is the third and last action; the task failed once.
    assert recoveries(result) == [
        ("planner", "responder_output", "schema", "retry_same", "failed"),
        ("planner", "responder_output", "schema", "switch_agent", "failed"),
        ("direct", "responder_output", "truncated", "retry_same", "failed"),
    ]
    assert states(result) == ["created", "planning", "failed"]
    assert provider.roles() == ["planner", "planner", "direct", "direct"]


def test_not_json_plan_does_not_fall_back() -> None:
    provider = QueueProvider()
    provider.replies["planner"] = [NOT_JSON]
    result = mission(provider)
    assert result.responder_error == ResponderErrorCode.NOT_JSON
    assert recoveries(result) == [
        ("planner", "responder_output", "not_json", "retry_same", "failed")
    ]
    assert "direct" not in provider.roles()


# ---- Truncated thinking output retries on the think:false provider.


def test_truncated_thinking_retries_on_the_structured_provider() -> None:
    thinking, structured = QueueProvider(), QueueProvider()
    thinking.script("final_answer", TRUNCATED)
    structured.replies["final_answer"] = [ok("100")]
    result = mission(thinking, structured_providers=(structured,))
    assert result.kind == MissionKind.COMPLETED
    assert recoveries(result) == [
        (
            "final_answer",
            "responder_output",
            "truncated",
            "adjust_parameters",
            "recovered",
        )
    ]
    assert thinking.roles().count("final_answer") == 1
    assert structured.roles().count("final_answer") == 1
    # The retried answer still had to pass LEVI's final verification.
    assert structured.roles()[-1] == "final_verifier"
    assert result.trace.verifications[-1].verdict.value == "passed"


def test_structured_role_truncation_retries_the_same_provider() -> None:
    thinking, structured = QueueProvider(), QueueProvider()
    structured.script("planner", TRUNCATED)
    result = mission(thinking, structured_providers=(structured,))
    assert result.kind == MissionKind.COMPLETED
    assert recoveries(result)[0][3] == "retry_same"
    assert structured.roles().count("planner") == 2


# ---- Observability: allowlisted closed values, no text, one lease at a time.


def test_recovery_events_are_allowlisted_and_content_free() -> None:
    provider = QueueProvider()
    provider.script("planner", NOT_JSON)
    provider.script("specialist", ProviderUnavailableError(f"down {SENTINEL}"))
    sink = RecordingSink()
    lifecycle = ModelLifecycleManager()
    result = mission(
        provider, text=f"Question {SENTINEL}?", events=sink, lifecycle=lifecycle
    )
    assert result.kind == MissionKind.COMPLETED
    recovery_events = [
        e for e in sink.events if e.event_type.value.startswith("recovery.")
    ]
    assert [e.event_type.value for e in recovery_events] == [
        "recovery.started",
        "recovery.completed",
    ] * 2
    for event in recovery_events:
        check_event(event)
        assert event.agent_id == "adaptive_recovery"
        assert event.correlation_id == MISSION
    assert recovery_events[2].payload["step_id"] == "s1"
    assert recovery_events[3].payload["recovery_id"] == (
        result.trace.recoveries[1].recovery_id
    )
    dumped = json.dumps([e.payload for e in sink.events], default=str)
    dumped += result.trace.model_dump_json()
    assert SENTINEL not in dumped and "no json here" not in dumped
    assert "Question" not in dumped
    # INV-023: retries never overlap generations or hold two leases.
    assert provider.max_running == 1 and lifecycle.active_count == 0


@pytest.mark.parametrize(
    ("event_type", "field", "value"),
    [
        (EventType.RECOVERY_STARTED, "action", "replan_subgraph"),
        (EventType.RECOVERY_STARTED, "role", "triage"),
        (EventType.RECOVERY_COMPLETED, "outcome", "exhausted"),
        (EventType.RECOVERY_COMPLETED, "outcome", SENTINEL),
        (EventType.RECOVERY_EXHAUSTED, "recovery_class", "verification_failed"),
        (EventType.RECOVERY_EXHAUSTED, "responder_error", "oversized"),
        (EventType.RECOVERY_EXHAUSTED, "summary", "responder_output"),
        (EventType.RECOVERY_EXHAUSTED, "recovery_id", SENTINEL),
    ],
)
def test_recovery_allowlist_takes_only_closed_values(
    event_type: EventType, field: str, value: str
) -> None:
    good = {
        "recovery_id": MISSION,
        "role": "specialist",
        "step_id": "s1",
        "recovery_class": "responder_output",
        "responder_error": "truncated",
    }
    if event_type != EventType.RECOVERY_EXHAUSTED:
        good["action"] = "retry_same"
    check_payload(event_type, good)
    with pytest.raises(EventPayloadError):
        check_payload(event_type, good | {field: value})
    assert mission_events.RECOVERY_OUTCOMES == {
        "recovered",
        "failed",
        "unavailable",
        "cancelled",
    }
    assert len(EventType) == 32


def test_recovery_records_are_consistent() -> None:
    base = {
        "recovery_id": MISSION,
        "role": "specialist",
        "agent_id": "intelligence",
        "step_id": "s1",
        "recovery_class": "responder_output",
        "responder_error": "truncated",
        "action": "retry_same",
        "outcome": "recovered",
    }
    RecoveryRecord.model_validate(base)
    for change in (
        {"responder_error": None},
        {"responder_error": "oversized"},
        {"recovery_class": "provider_error"},
        {"action": None},
        {"outcome": "exhausted"},
        {"action": "switch_agent"},
        {"outcome": "unavailable"},
        {"text": "anything"},
    ):
        with pytest.raises(ValidationError):
            RecoveryRecord.model_validate(base | change)
    assert (
        RecoveryRecord.model_validate(
            base | {"action": None, "outcome": RecoveryOutcome.EXHAUSTED}
        ).direct_fallback
        is False
    )
    assert RecoveryRecord.model_validate(
        base | {"role": "planner", "action": RecoveryAction.SWITCH_AGENT}
    ).direct_fallback


def test_traces_stored_before_phx_109_still_load() -> None:
    provider = QueueProvider()
    trace = mission(provider).trace.model_dump(mode="json")
    del trace["recoveries"]
    assert MissionTrace.model_validate(trace).recoveries == ()
