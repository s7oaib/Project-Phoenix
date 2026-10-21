"""PHX-114 voice routes (ADR-108): status, start and stop listening.

Behind the PHX-101 middleware like every route. Voice is opt-in: nothing opens
the microphone until ``POST /voice/start``. Responses hold the pipeline state,
closed codes, IDs and component availability only; spoken requests and answers
live in the conversation (``GET /conversations/{id}/messages``), and live state
changes arrive as ``voice.*`` events on ``WS /events``.
"""

from typing import Literal

from fastapi import APIRouter, Request

from phoenix.api.routes.common import Contract, services
from phoenix.api.voice import VoiceStatus
from phoenix.voice.wake import DEFAULT_WAKE_PHRASE

router = APIRouter()

VoiceState = Literal[
    "off",
    "starting",
    "sleeping",
    "listening",
    "thinking",
    "speaking",
    "degraded_voice",
    "text_only",
    "unavailable",
]
VoiceCode = Literal[
    "models_missing",
    "dependency_unavailable",
    "microphone_unavailable",
    "voice_failure",
]
ComponentId = Literal["wake_word", "vad", "stt", "tts", "conversation", "audio_output"]
Availability = Literal["available", "degraded", "unavailable"]


class VoiceComponentOut(Contract):
    id: ComponentId
    status: Availability


class VoiceStatusOut(Contract):
    state: VoiceState
    code: VoiceCode | None
    wake_phrase: str
    session_id: str | None
    conversation_id: str | None
    mission_id: str | None
    components: tuple[VoiceComponentOut, ...]


def _out(status: VoiceStatus) -> VoiceStatusOut:
    return VoiceStatusOut.model_validate(
        {
            "state": status.state.value,
            "code": None if status.code is None else status.code.value,
            "wake_phrase": DEFAULT_WAKE_PHRASE.text,
            "session_id": status.session_id,
            "conversation_id": status.conversation_id,
            "mission_id": status.mission_id,
            "components": [
                {"id": component, "status": value}
                for component, value in status.components
            ],
        }
    )


@router.get("/voice/status", response_model=VoiceStatusOut)
async def voice_status(request: Request) -> VoiceStatusOut:
    return _out(services(request).voice.status())


@router.post("/voice/start", status_code=202, response_model=VoiceStatusOut)
async def voice_start(request: Request) -> VoiceStatusOut:
    """Start listening for "Rise, Phoenix"; models load in the background."""
    return _out(await services(request).voice.start())


@router.post("/voice/stop", response_model=VoiceStatusOut)
async def voice_stop(request: Request) -> VoiceStatusOut:
    """Stop listening and speaking, and release the microphone."""
    return _out(await services(request).voice.stop())
