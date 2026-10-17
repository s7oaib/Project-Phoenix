"""PHX-105 request triage: PHOENIX's routing hint for a request without a path.

When the caller gives no path, PHOENIX asks one structured (thinking off)
generation on the ``triage`` tier (T1 qwen3.5:4b, then T0 qwen3.5:2b) whether
the request goes DIRECT or COMPLEX. The model sees ``TRIAGE_SYSTEM`` and the
user's request text only, as the user message, exactly as the DIRECT and
planning roles see it (ADR-081: only runtime constants and the user's own
request appear as raw prompt text); the request is not wrapped by
PromptInjectionGuard, whose origins are all untrusted third-party content and
whose blocking signals would turn the user's own wording into a fallback.

The reply must be exactly ``{"path": "direct"}`` or ``{"path": "complex"}``,
parsed by the frozen responder rules. The decision is a routing hint only: it
grants nothing (INV-003) and PHOENIX still runs the chosen path with all its
checks (INV-001). Any failure falls back to COMPLEX, AIZEN planning plus LEVI
verification, observably (INV-022): the runtime records the outcome and reason
code in MissionTrace and publishes them on PHOENIX's agent events.
Cancellation is never a fallback; it propagates.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from phoenix.agents import prompts
from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest
from phoenix.agents.responders import (
    ModelBinding,
    ResponderErrorCode,
    ResponderOutputError,
    _generate,
    parse_json_object,
)
from phoenix.agents.router import AgentModelRoutingError
from phoenix.models.provider import ModelProviderError, ProviderUnavailableError

# The tier-table row, the generation record role and the agent event role.
TRIAGE_ROLE = "triage"


class TriageOutcome(StrEnum):
    """Who decided a mission's path."""

    DECIDED = "decided"  # the triage model chose it
    FALLBACK = "fallback"  # triage failed, so COMPLEX by rule
    CLIENT = "client"  # the caller chose it; triage did not run


class TriageFallbackReason(StrEnum):
    """Why triage fell back to COMPLEX; a closed code, never model text."""

    TRUNCATED = ResponderErrorCode.TRUNCATED.value
    OVERSIZED = ResponderErrorCode.OVERSIZED.value
    NOT_JSON = ResponderErrorCode.NOT_JSON.value
    SCHEMA = ResponderErrorCode.SCHEMA.value
    INVALID_TEXT = ResponderErrorCode.INVALID_TEXT.value
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_ERROR = "provider_error"
    NO_ELIGIBLE_MODEL = "no_eligible_model"
    INTERNAL_ERROR = "internal_error"


class _TriageWire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    path: Literal["direct", "complex"]


class ModelTriageResponder:
    """One structured generation that proposes DIRECT or COMPLEX."""

    def __init__(self, binding: ModelBinding) -> None:
        self._binding = binding

    async def classify(self, request: PhoenixRequest) -> OrchestrationPath:
        text = await _generate(
            self._binding, TRIAGE_ROLE, prompts.TRIAGE_SYSTEM, request.text
        )
        return OrchestrationPath(parse_json_object(text, _TriageWire).path)


def fallback_reason(error: Exception) -> TriageFallbackReason:
    """The closed reason for a triage failure; anything unknown is internal."""
    if isinstance(error, ResponderOutputError):
        try:
            return TriageFallbackReason(error.code.value)
        except ValueError:
            return TriageFallbackReason.SCHEMA
    if isinstance(error, TimeoutError):
        return TriageFallbackReason.TIMEOUT
    if isinstance(error, ProviderUnavailableError):
        return TriageFallbackReason.PROVIDER_UNAVAILABLE
    if isinstance(error, ModelProviderError):
        return TriageFallbackReason.PROVIDER_ERROR
    if isinstance(error, AgentModelRoutingError):
        return TriageFallbackReason.NO_ELIGIBLE_MODEL
    return TriageFallbackReason.INTERNAL_ERROR
