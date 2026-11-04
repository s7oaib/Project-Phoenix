"""PHX-132 (ADR-230 to ADR-234): streaming DIRECT answers, with fakes only."""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from phoenix.agents.answer_stream import (
    MAX_DELTA_CHARS,
    PartialAnswers,
    StreamingProvider,
    split_delta,
)
from phoenix.agents.mission_events import (
    EventPayloadError,
    check_event,
    is_answer_delta,
    mission_event,
)
from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest
from phoenix.agents.runtime import PhoenixRuntime
from phoenix.api.observability import MetricsCollector
from phoenix.core.events import EventEnvelope, EventType
from phoenix.models.lifecycle import ModelLifecycleManager
from phoenix.models.ollama import OllamaProvider
from phoenix.models.provider import (
    CancellationResult,
    CancellationStatus,
    GenerationChunk,
    GenerationRequest,
    GenerationResponse,
    MessageRole,
    ModelCapabilities,
    ModelMessage,
    ModelProvider,
    ProviderHealth,
    ProviderHealthStatus,
    TokenUsage,
)
from phoenix.models.registry import ModelCapabilityRegistry
from phoenix.voice.engines.pipeline import first_spoken_sentence, speakable_text

MODELS = ("qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b", "qwen2.5-coder:7b")
MISSION = "5f0c1d2e-3a4b-4c5d-8e6f-7a8b9c0d1e2f"
ANSWER = "Paris is the capital of France. It sits on the Seine."
PIECES = ("Paris is", " the capital", " of France.", " It sits on", " the Seine.")


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


class FakeProvider(ModelProvider):
    """generate returns ANSWER whole; stream yields PIECES then completion."""

    def __init__(self) -> None:
        super().__init__("ollama")
        self.streams = 0
        self.closed = 0

    async def health(self) -> ProviderHealth:
        return ProviderHealth(provider_id="ollama", status=ProviderHealthStatus.HEALTHY)

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        return capabilities(model_id)

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id="ollama",
            model_id=request.model_id,
            text=ANSWER,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=5, output_tokens=9),
        )

    async def _chunks(
        self, request: GenerationRequest
    ) -> AsyncIterator[GenerationChunk]:
        self.streams += 1
        try:
            for piece in PIECES:
                await asyncio.sleep(0)
                yield GenerationChunk(
                    operation_id=request.operation_id,
                    provider_id="ollama",
                    model_id=request.model_id,
                    text=piece,
                )
            yield GenerationChunk(
                operation_id=request.operation_id,
                provider_id="ollama",
                model_id=request.model_id,
                text="",
                finish_reason="stop",
                usage=TokenUsage(input_tokens=5, output_tokens=9),
                is_final=True,
            )
        finally:
            self.closed += 1

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        return self._chunks(request)

    async def cancel(self, operation_id: str) -> CancellationResult:
        return CancellationResult(
            operation_id=operation_id,
            provider_id="ollama",
            status=CancellationStatus.ACCEPTED,
        )


class Sink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        check_event(event)
        self.events.append(event)


def request() -> GenerationRequest:
    return GenerationRequest(
        model_id="qwen3.5:4b",
        messages=(ModelMessage(role=MessageRole.USER, text="Capital of France?"),),
    )


def runtime(
    provider: ModelProvider, sink: Sink | None, partials: PartialAnswers | None
) -> PhoenixRuntime:
    registry = ModelCapabilityRegistry()
    for model in MODELS:
        registry.register(capabilities(model))
    return PhoenixRuntime(
        providers=(provider,),
        capability_registry=registry,
        lifecycle=ModelLifecycleManager(),
        events=sink,
        partial_answers=partials,
    )


def direct(rt: PhoenixRuntime) -> Any:
    async def go() -> Any:
        return await rt.run(
            PhoenixRequest(request_id=MISSION, text="Capital of France?"),
            OrchestrationPath.DIRECT,
        )

    return asyncio.run(go())


# ---- provider


def test_ollama_streaming_parse_with_a_fake_transport() -> None:
    lines = [
        {
            "model": "qwen3.5:4b",
            "message": {"role": "assistant", "content": p},
            "done": False,
        }
        for p in PIECES
    ] + [
        {
            "model": "qwen3.5:4b",
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
            "eval_count": 9,
        }
    ]
    sent: list[dict[str, Any]] = []

    def handler(http: httpx.Request) -> httpx.Response:
        sent.append(json.loads(http.content))
        body = "\n".join(json.dumps(line) for line in lines) + "\n"
        return httpx.Response(200, content=body.encode())

    async def go() -> tuple[GenerationResponse, list[str]]:
        deltas: list[str] = []

        async def on_delta(text: str) -> None:
            deltas.append(text)

        async with OllamaProvider(transport=httpx.MockTransport(handler)) as ollama:
            response = await StreamingProvider(ollama, on_delta).generate(request())
        return response, deltas

    response, deltas = asyncio.run(go())
    assert sent[0]["stream"] is True
    assert deltas == list(PIECES)
    assert response.text == ANSWER and response.finish_reason == "stop"


def test_streaming_generate_equals_generate_and_closes_the_stream() -> None:
    provider = FakeProvider()

    async def go() -> tuple[GenerationResponse, GenerationResponse, list[str]]:
        deltas: list[str] = []

        async def on_delta(text: str) -> None:
            deltas.append(text)

        req = request()
        streamed = await StreamingProvider(provider, on_delta).generate(req)
        plain = await provider.generate(req)
        return streamed, plain, deltas

    streamed, plain, deltas = asyncio.run(go())
    assert streamed == plain and "".join(deltas) == ANSWER
    assert provider.closed == 1


def test_long_chunks_split_into_bounded_deltas() -> None:
    text = "x" * (MAX_DELTA_CHARS * 2 + 5)
    parts = split_delta(text)
    assert "".join(parts) == text and max(map(len, parts)) == MAX_DELTA_CHARS


# ---- runtime


def test_runtime_emits_deltas_then_final_answer_identical_to_non_streaming() -> None:
    plain = direct(runtime(FakeProvider(), Sink(), None))
    sink, partials, provider = Sink(), PartialAnswers(), FakeProvider()
    streamed = direct(runtime(provider, sink, partials))
    assert provider.streams == 1
    assert streamed.text == plain.text == ANSWER
    assert streamed.kind == plain.kind
    deltas = [e for e in sink.events if is_answer_delta(e)]
    assert "".join(str(e.payload["answer_delta"]) for e in deltas) == ANSWER
    assert all(dict(e.payload).keys() == {"mission_id", "answer_delta"} for e in deltas)
    assert all(e.correlation_id == MISSION and e.agent_id == "phoenix" for e in deltas)
    kinds = [e.event_type for e in sink.events if not is_answer_delta(e)]
    last_delta = max(i for i, e in enumerate(sink.events) if is_answer_delta(e))
    completed = [
        i
        for i, e in enumerate(sink.events)
        if e.event_type == EventType.AGENT_COMPLETED
    ]
    assert completed and completed[-1] > last_delta and kinds
    assert partials.text(MISSION) == ANSWER


def test_without_partial_answers_nothing_streams() -> None:
    sink, provider = Sink(), FakeProvider()
    direct(runtime(provider, sink, None))
    assert provider.streams == 0 and not any(is_answer_delta(e) for e in sink.events)


# ---- allowlist and observers


@pytest.mark.parametrize(
    "payload",
    [
        {"answer_delta": "hi"},
        {"mission_id": MISSION, "answer_delta": "hi", "role": "direct"},
        {"mission_id": "6a1b2c3d-4e5f-4a6b-9c7d-8e9f0a1b2c3d", "answer_delta": "hi"},
        {"mission_id": MISSION, "answer_delta": ""},
        {"mission_id": MISSION, "answer_delta": "x" * (MAX_DELTA_CHARS + 1)},
    ],
)
def test_malformed_answer_deltas_are_refused(payload: dict[str, Any]) -> None:
    with pytest.raises(EventPayloadError):
        mission_event(
            EventType.AGENT_STARTED, payload, correlation_id=MISSION, agent_id="phoenix"
        )


def test_answer_delta_only_on_agent_started_from_phoenix() -> None:
    good = {"mission_id": MISSION, "answer_delta": "hi"}
    mission_event(
        EventType.AGENT_STARTED, good, correlation_id=MISSION, agent_id="phoenix"
    )
    with pytest.raises(EventPayloadError):
        mission_event(
            EventType.AGENT_STARTED, good, correlation_id=MISSION, agent_id="verifier"
        )
    with pytest.raises(EventPayloadError):
        mission_event(
            EventType.STEP_STARTED, good, correlation_id=MISSION, agent_id="phoenix"
        )
    assert len(EventType) == 32


def test_metrics_never_log_answer_text(caplog: pytest.LogCaptureFixture) -> None:
    sink = Sink()
    collector = MetricsCollector(sink)
    event = mission_event(
        EventType.AGENT_STARTED,
        {"mission_id": MISSION, "answer_delta": "SECRET-ANSWER-42"},
        correlation_id=MISSION,
        agent_id="phoenix",
    )
    with caplog.at_level(logging.DEBUG):
        asyncio.run(collector.publish(event))
    assert sink.events == [event]
    assert "SECRET-ANSWER-42" not in caplog.text
    assert all("SECRET-ANSWER-42" not in str(log) for log in collector._logs)


# ---- voice


def test_first_spoken_sentence_waits_for_a_complete_sentence() -> None:
    assert first_spoken_sentence("Paris is the capital") is None
    assert first_spoken_sentence("Paris is the capital of France.") is None
    first = first_spoken_sentence("Paris is the **capital** of France. It")
    assert first == "Paris is the capital of France."
    assert speakable_text(ANSWER).startswith(first_spoken_sentence(ANSWER + " ") or "?")
    assert first_spoken_sentence("Run `ls -la`. Then") is None


def test_partial_answers_are_bounded_and_reset_per_attempt() -> None:
    partials = PartialAnswers()
    partials.feed(MISSION, "a")
    partials.start(MISSION)
    partials.feed(MISSION, "b")
    assert partials.text(MISSION) == "b"
    for index in range(20):
        partials.feed(f"m{index}", "x")
    assert partials.text(MISSION) == ""
    partials.discard("m19")
    assert partials.text("m19") == ""


def test_voice_responder_speaks_the_first_sentence_before_the_mission_settles() -> None:
    from types import SimpleNamespace

    from phoenix.api.voice import MissionVoiceResponder
    from phoenix.persistence.missions import MissionStatus
    from phoenix.voice.conversation import VoiceResponseRequest

    partials = PartialAnswers()
    order: list[str] = []
    state = {"status": MissionStatus.RUNNING}

    class Missions:
        async def create_conversation(self) -> str:
            return "c1"

        async def submit(self, conversation: str, text: str, path: object) -> str:
            partials.start(MISSION)
            partials.feed(MISSION, "Paris is the capital of France. It sits")
            return MISSION

        async def get_mission(self, mission_id: str) -> Any:
            return SimpleNamespace(
                status=state["status"], answer_message_id="a1", conversation_id="c1"
            )

        async def list_messages(self, conversation: str) -> Any:
            return [SimpleNamespace(message_id="a1", text=ANSWER)]

    async def preview(text: str) -> bool:
        order.append(f"preview:{text}")
        state["status"] = MissionStatus.COMPLETED
        return True

    async def go() -> Any:
        responder = MissionVoiceResponder(Missions(), partial_answers=partials)  # type: ignore[arg-type]
        ask = VoiceResponseRequest(
            session_id="11111111-1111-4111-8111-111111111111",
            turn_id="22222222-2222-4222-8222-222222222222",
            transcript_text="Capital of France?",
        )
        return await responder.respond_with_preview(ask, preview)

    draft = asyncio.run(go())
    assert order == ["preview:Paris is the capital of France."]
    assert draft.response_text == speakable_text(ANSWER)
    assert draft.response_text.startswith("Paris is the capital of France.")
    assert partials.text(MISSION) == ""  # discarded once settled


def test_voice_responder_stops_after_a_barge_in_during_the_preview() -> None:
    from types import SimpleNamespace

    from phoenix.api.voice import MissionVoiceResponder
    from phoenix.persistence.missions import MissionStatus
    from phoenix.voice.conversation import VoiceResponseRequest

    partials = PartialAnswers()

    class Missions:
        async def create_conversation(self) -> str:
            return "c1"

        async def submit(self, conversation: str, text: str, path: object) -> str:
            partials.feed(MISSION, "Sure. More")
            return MISSION

        async def get_mission(self, mission_id: str) -> Any:
            return SimpleNamespace(status=MissionStatus.RUNNING)

    async def preview(text: str) -> bool:
        return False

    async def go() -> Any:
        responder = MissionVoiceResponder(Missions(), partial_answers=partials)  # type: ignore[arg-type]
        ask = VoiceResponseRequest(
            session_id="11111111-1111-4111-8111-111111111111",
            turn_id="22222222-2222-4222-8222-222222222222",
            transcript_text="Hi",
        )
        return await asyncio.wait_for(responder.respond_with_preview(ask, preview), 5)

    assert asyncio.run(go()).response_text == "Sure."
