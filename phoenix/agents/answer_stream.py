"""PHX-132 (ADR-230, ADR-231): DIRECT answers stream while they are generated.

``StreamingProvider`` wraps one provider for one DIRECT attempt: ``generate``
consumes the provider's own ``stream`` (Ollama ``stream: true``), hands each
text delta to a callback and returns the assembled ``GenerationResponse``, so
the unchanged responder (``_generate``) applies the same identity, record,
truncation and size checks: the final answer equals the non-streaming one.
Every other method delegates. Deltas are display text only; they confer no
authority and are never stored. ``PartialAnswers`` keeps the text so far of a
few recent missions in memory for the voice responder (first sentence early).
"""

from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Final

from phoenix.models.provider import (
    CancellationResult,
    GenerationChunk,
    GenerationRequest,
    GenerationResponse,
    ModelCapabilities,
    ModelProvider,
    ModelProviderError,
    ProviderHealth,
)

# One published delta at most; a longer chunk is split (events stay small).
MAX_DELTA_CHARS: Final = 2048
_MAX_MISSIONS: Final = 8
_MAX_PARTIAL_CHARS: Final = 20000

DeltaSink = Callable[[str], Awaitable[None]]


def split_delta(text: str) -> tuple[str, ...]:
    return tuple(
        text[start : start + MAX_DELTA_CHARS]
        for start in range(0, len(text), MAX_DELTA_CHARS)
    )


class StreamingProvider(ModelProvider):
    """``generate`` through ``stream``; deltas go to ``on_delta`` in order."""

    def __init__(self, inner: ModelProvider, on_delta: DeltaSink) -> None:
        super().__init__(inner.provider_id)
        self._inner = inner
        self._on_delta = on_delta

    async def health(self) -> ProviderHealth:
        return await self._inner.health()

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        return await self._inner.capabilities(model_id)

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        return self._inner.stream(request)

    async def cancel(self, operation_id: str) -> CancellationResult:
        return await self._inner.cancel(operation_id)

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        parts: list[str] = []
        final: GenerationChunk | None = None
        chunks = self._inner.stream(request)
        try:
            async for chunk in chunks:
                if final is not None:
                    raise ModelProviderError("Stream continued after completion")
                if chunk.operation_id != request.operation_id:
                    raise ModelProviderError("Provider response identity mismatch")
                if chunk.text:
                    parts.append(chunk.text)
                    for delta in split_delta(chunk.text):
                        await self._on_delta(delta)
                if chunk.is_final:
                    final = chunk
        finally:
            close = getattr(chunks, "aclose", None)
            if close is not None:
                await close()  # an early stop must release the HTTP stream
        if final is None:
            raise ModelProviderError("Stream ended without completion")
        return GenerationResponse(
            operation_id=final.operation_id,
            provider_id=final.provider_id,
            model_id=final.model_id,
            text="".join(parts),
            finish_reason=final.finish_reason,
            usage=final.usage,
            metrics=final.metrics,
        )


class PartialAnswers:
    """The DIRECT answer text so far per mission; bounded, memory only."""

    def __init__(self) -> None:
        self._texts: OrderedDict[str, str] = OrderedDict()

    def start(self, mission_id: str) -> None:
        """A new attempt replaces any earlier partial text."""
        self._texts[mission_id] = ""
        self._texts.move_to_end(mission_id)
        while len(self._texts) > _MAX_MISSIONS:
            self._texts.popitem(last=False)

    def feed(self, mission_id: str, delta: str) -> None:
        if mission_id not in self._texts:
            self.start(mission_id)
        text = self._texts[mission_id]
        self._texts[mission_id] = (text + delta)[:_MAX_PARTIAL_CHARS]

    def text(self, mission_id: str) -> str:
        return self._texts.get(mission_id, "")

    def discard(self, mission_id: str) -> None:
        self._texts.pop(mission_id, None)
