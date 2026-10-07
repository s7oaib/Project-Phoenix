"""PHOENIX-owned startup, using caller-supplied voice and health evidence.

No default health, device backend, worker, task execution or permission authority.
UTA is interface metadata. Probes/collaborators and native deadlines are borrowed;
same-run structural health evidence is not independent authentication.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence
from enum import StrEnum
from hashlib import sha256
from typing import ClassVar, Self, TypeVar, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    field_validator,
    model_validator,
)

from phoenix.agents.failsafe import (
    FailSafeCapability,
    FailSafeDecision,
    FailSafeRequest,
    FailSafeTrigger,
    KaidoFailSafe,
)
from phoenix.core.event_bus import EventBus
from phoenix.core.events import EventEnvelope, EventType
from phoenix.voice.audio import _identifier, _stored_values
from phoenix.voice.conversation import (
    AudioOutput,
    AudioPlaybackRequest,
    AudioPlaybackResult,
    AudioPlaybackStatus,
    VoiceConversationManager,
    VoiceConversationState,
)
from phoenix.voice.tts import TtsAudio, TtsCapabilities, TtsProvider, TtsRequest
from phoenix.voice.wake import (
    DEFAULT_WAKE_PHRASE,
    WakeWordCapabilities,
    WakeWordDetection,
    WakeWordProvider,
    WakeWordRequest,
)

STARTUP_ACKNOWLEDGEMENT = (
    "Wake command acknowledged. Initializing Phoenix network. "
    "Checking all agents and core systems."
)
STARTUP_HEALTHY_RESPONSE = (
    "All agents online. All core systems operational. What's the mission?"
)
STARTUP_DEGRADED_RESPONSE = (
    "Phoenix initialized in degraded mode. Some core systems are unavailable. "
    "Continuing with available capabilities."
)
_DEGRADED_REASON = "Startup health or voice output is degraded."
_CAPABILITIES = (
    FailSafeCapability.BASIC_CONVERSATION,
    FailSafeCapability.DIAGNOSTICS,
    FailSafeCapability.RECOVERY_GUIDANCE,
)

__all__ = (
    "STARTUP_ACKNOWLEDGEMENT",
    "STARTUP_HEALTHY_RESPONSE",
    "STARTUP_DEGRADED_RESPONSE",
    "StartupSubsystem",
    "StartupCheckStatus",
    "StartupOutcome",
    "StartupVoiceFailureStage",
    "StartupErrorCode",
    "StartupError",
    "StartupCheckResult",
    "StartupHealthProbe",
    "RisePhoenixStartupRequest",
    "RisePhoenixStartupResult",
    "RisePhoenixStartupCoordinator",
)


class StartupSubsystem(StrEnum):
    PERSISTENCE = "persistence"
    HARDWARE = "hardware"
    MODEL_PROVIDER = "model_provider"
    TOOLS = "tools"
    VOICE = "voice"
    AGENTS = "agents"


class StartupCheckStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class StartupOutcome(StrEnum):
    READY = "ready"
    DEGRADED = "degraded"


class StartupVoiceFailureStage(StrEnum):
    TTS = "tts"
    PLAYBACK = "playback"


class StartupErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    INVALID_RESPONSE = "invalid_response"
    BUSY = "busy"
    CLOSED = "closed"
    WRONG_EVENT_LOOP = "wrong_event_loop"
    WAKE_FAILURE = "wake_failure"
    HEALTH_FAILURE = "health_failure"
    TTS_FAILURE = "tts_failure"
    PLAYBACK_FAILURE = "playback_failure"
    CONVERSATION_FAILURE = "conversation_failure"
    EVENT_FAILURE = "event_failure"
    FAILSAFE_FAILURE = "failsafe_failure"


class StartupError(Exception):
    def __init__(self, code: StartupErrorCode) -> None:
        if not any(code is member for member in StartupErrorCode):
            raise ValueError("invalid_input")
        self.code = code
        super().__init__(code.value)


E = TypeVar("E", bound=StrEnum)


def _enum(value: object, cls: type[E]) -> E:
    for member in cls:
        if value is member or (type(value) is str and value == member.value):
            return member
    raise ValueError("invalid_input")


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )
    _code: ClassVar[StartupErrorCode] = StartupErrorCode.INVALID_RESPONSE

    @model_validator(mode="wrap")
    @classmethod
    def exact(cls, value: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        try:
            return handler(_stored_values(value, cls))._integrity()
        except Exception:
            raise StartupError(cls._code) from None

    def _integrity(self) -> Self:
        return self

    @field_validator("startup_id", "session_id", mode="before", check_fields=False)
    @classmethod
    def identifier(cls, value: object) -> str:
        return _identifier(value)


class StartupCheckResult(_Contract):
    startup_id: str
    subsystem: StartupSubsystem
    status: StartupCheckStatus

    @field_validator("subsystem", mode="before")
    @classmethod
    def subsystem_value(cls, value: object) -> StartupSubsystem:
        return _enum(value, StartupSubsystem)

    @field_validator("status", mode="before")
    @classmethod
    def status_value(cls, value: object) -> StartupCheckStatus:
        return _enum(value, StartupCheckStatus)


class StartupHealthProbe(ABC):
    def __init__(self, subsystem: StartupSubsystem) -> None:
        try:
            self._subsystem = _enum(subsystem, StartupSubsystem)
        except Exception:
            raise StartupError(StartupErrorCode.INVALID_INPUT) from None

    @property
    def subsystem(self) -> StartupSubsystem:
        return self._subsystem

    @abstractmethod
    async def check(self, startup_id: str) -> StartupCheckResult:
        """Execute a fresh caller-owned check; no default success exists."""


class RisePhoenixStartupRequest(_Contract):
    _code = StartupErrorCode.INVALID_INPUT
    startup_id: str
    session_id: str
    wake_request: WakeWordRequest = Field(repr=False)
    tts_voice_profile_id: str
    tts_language_hint: str | None = None

    @field_validator("wake_request", mode="before")
    @classmethod
    def wake_snapshot(cls, value: object) -> WakeWordRequest:
        return WakeWordRequest.model_validate(value)

    @field_validator("tts_voice_profile_id", mode="before")
    @classmethod
    def profile(cls, value: object) -> str:
        return _identifier(value, 128)

    @field_validator("tts_language_hint", mode="before")
    @classmethod
    def language(cls, value: object) -> str | None:
        return None if value is None else _identifier(value, 32)

    def _integrity(self) -> Self:
        if self.wake_request.phrase != DEFAULT_WAKE_PHRASE:
            raise ValueError("invalid_input")
        return self


def _decision(value: object, startup_id: str) -> FailSafeDecision:
    # Reject the older policy contract's permissive copied/foreign enums before
    # revalidating it. JSON dictionaries may contain only ordinary exact strings.
    data = _stored_values(value, FailSafeDecision)
    if type(data) is not dict:
        raise ValueError("invalid_response")
    values = cast(dict[str, object], data).copy()
    values["trigger"] = _enum(values.get("trigger"), FailSafeTrigger)
    capabilities = values.get("enabled_capabilities")
    if type(capabilities) not in (list, tuple):
        raise ValueError("invalid_response")
    values["enabled_capabilities"] = tuple(
        _enum(item, FailSafeCapability) for item in cast(Sequence[object], capabilities)
    )
    result = FailSafeDecision.model_validate(values)
    if (
        result.failsafe_id != startup_id
        or result.failsafe_agent_id != "failsafe"
        or result.mode != "degraded"
        or result.trigger is not FailSafeTrigger.CRITICAL_SUBSYSTEM_DEGRADATION
        or result.enabled_capabilities != _CAPABILITIES
        or result.reason != _DEGRADED_REASON
        or result.recovery_id is not None
    ):
        raise ValueError("invalid_response")
    return result


class RisePhoenixStartupResult(_Contract):
    startup_id: str
    session_id: str
    outcome: StartupOutcome
    checks: tuple[StartupCheckResult, ...]
    response_text: str = Field(repr=False)
    voice_failure_stage: StartupVoiceFailureStage | None = None
    failsafe_decision: FailSafeDecision | None = Field(default=None, repr=False)
    final_voice_state: VoiceConversationState

    @field_validator("outcome", mode="before")
    @classmethod
    def outcome_value(cls, value: object) -> StartupOutcome:
        return _enum(value, StartupOutcome)

    @field_validator("voice_failure_stage", mode="before")
    @classmethod
    def failure(cls, value: object) -> StartupVoiceFailureStage | None:
        return None if value is None else _enum(value, StartupVoiceFailureStage)

    @field_validator("final_voice_state", mode="before")
    @classmethod
    def voice_state(cls, value: object) -> VoiceConversationState:
        return _enum(value, VoiceConversationState)

    @field_validator("response_text", mode="before")
    @classmethod
    def exact_text(cls, value: object) -> str:
        if type(value) is not str or value not in (
            STARTUP_HEALTHY_RESPONSE,
            STARTUP_DEGRADED_RESPONSE,
        ):
            raise ValueError("invalid_response")
        return value

    @field_validator("checks", mode="before")
    @classmethod
    def check_snapshots(cls, value: object) -> tuple[StartupCheckResult, ...]:
        if type(value) not in (tuple, list) or len(cast(Sequence[object], value)) != 6:
            raise ValueError("invalid_response")
        return tuple(
            StartupCheckResult.model_validate(item)
            for item in cast(Sequence[object], value)
        )

    @field_validator("failsafe_decision", mode="before")
    @classmethod
    def failsafe_snapshot(cls, value: object) -> FailSafeDecision | None:
        if value is None:
            return None
        data = _stored_values(value, FailSafeDecision)
        if type(data) is not dict:
            raise ValueError("invalid_response")
        return _decision(
            value, _identifier(cast(dict[str, object], data).get("failsafe_id"))
        )

    def _integrity(self) -> Self:
        if (
            tuple(check.subsystem for check in self.checks) != tuple(StartupSubsystem)
            or any(check.startup_id != self.startup_id for check in self.checks)
            or self.final_voice_state is not VoiceConversationState.LISTENING
        ):
            raise ValueError("invalid_response")
        healthy = all(
            check.status is StartupCheckStatus.HEALTHY for check in self.checks
        )
        if self.outcome is StartupOutcome.READY:
            if (
                not healthy
                or self.voice_failure_stage is not None
                or self.failsafe_decision is not None
                or self.response_text != STARTUP_HEALTHY_RESPONSE
            ):
                raise ValueError("invalid_response")
        elif (
            (healthy and self.voice_failure_stage is None)
            or self.failsafe_decision is None
            or self.response_text != STARTUP_DEGRADED_RESPONSE
        ):
            raise ValueError("invalid_response")
        if self.failsafe_decision is not None:
            _decision(self.failsafe_decision, self.startup_id)
        return self


class RisePhoenixStartupCoordinator:
    """One caller-owned sequence, no saved health result or hidden worker.

    Aborts after activation attempt best-effort sleep only the matching session.
    Rollback errors cannot replace the original safe error/cancellation category;
    callers can observe the actual borrowed manager state.
    """

    def __init__(
        self,
        wake_provider: WakeWordProvider,
        probes: Sequence[StartupHealthProbe],
        tts_provider: TtsProvider,
        audio_output: AudioOutput,
        conversation: VoiceConversationManager,
        event_bus: EventBus,
        failsafe: KaidoFailSafe,
    ) -> None:
        try:
            if not (
                isinstance(wake_provider, WakeWordProvider)
                and isinstance(tts_provider, TtsProvider)
                and isinstance(audio_output, AudioOutput)
                and isinstance(conversation, VoiceConversationManager)
                and isinstance(event_bus, EventBus)
                and isinstance(failsafe, KaidoFailSafe)
            ):
                raise ValueError
            if type(probes) not in (tuple, list) or len(probes) != 6:
                raise ValueError
            accepted = tuple(probes)
            if not all(isinstance(probe, StartupHealthProbe) for probe in accepted):
                raise ValueError
            if not all(
                probe.subsystem is subsystem
                for probe, subsystem in zip(accepted, StartupSubsystem)
            ):
                raise ValueError
            _identifier(wake_provider.provider_id)
            _identifier(tts_provider.provider_id)
            _identifier(audio_output.output_id)
            if (
                conversation.owner_id != "voice"
                or failsafe.failsafe_agent_id != "failsafe"
            ):
                raise ValueError
        except Exception:
            raise StartupError(StartupErrorCode.INVALID_INPUT) from None
        self._wake = wake_provider
        self._probes = accepted
        self._tts = tts_provider
        self._output = audio_output
        self._conversation = conversation
        self._bus = event_bus
        self._failsafe = failsafe
        self._active: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    @property
    def owner_id(self) -> str:
        return "phoenix"

    @property
    def active_startup_id(self) -> str | None:
        return self._active

    @property
    def closed(self) -> bool:
        return self._closed

    def _bind(self, *, closing: bool = False) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise StartupError(StartupErrorCode.WRONG_EVENT_LOOP)
        if self._closed and not closing:
            raise StartupError(StartupErrorCode.CLOSED)

    def _check(self) -> None:
        task = asyncio.current_task()
        if self._closed or (task is not None and task.cancelling()):
            raise asyncio.CancelledError

    def _listening(self, request: RisePhoenixStartupRequest) -> bool:
        try:
            return (
                self._conversation.session_id == request.session_id
                and self._conversation.state is VoiceConversationState.LISTENING
                and not self._conversation.closed
            )
        except Exception:
            raise StartupError(StartupErrorCode.CONVERSATION_FAILURE) from None

    async def _publish(
        self,
        kind: EventType,
        request: RisePhoenixStartupRequest,
        result: RisePhoenixStartupResult | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "startup_id": request.startup_id,
            "session_id": request.session_id,
        }
        if result is not None:
            payload.update(
                outcome=result.outcome.value,
                unhealthy_subsystems=[
                    check.subsystem.value
                    for check in result.checks
                    if check.status is not StartupCheckStatus.HEALTHY
                ],
                voice_failure_stage=result.voice_failure_stage.value
                if result.voice_failure_stage is not None
                else None,
            )
        try:
            event = EventEnvelope.model_validate(
                dict(
                    event_type=kind,
                    agent_id="phoenix",
                    correlation_id=request.startup_id,
                    payload=payload,
                )
            )
            await self._bus.publish(event)
            self._check()
        except Exception:
            self._check()
            raise StartupError(StartupErrorCode.EVENT_FAILURE) from None

    async def _speak(
        self, request: RisePhoenixStartupRequest, text: str, phase: str
    ) -> StartupVoiceFailureStage | None:
        operation = (
            "startup_"
            + phase
            + "_"
            + sha256(request.startup_id.encode("utf-8")).hexdigest()
        )
        try:
            raw_cap = await self._tts.capabilities()
            self._check()
            capabilities = TtsCapabilities.model_validate(raw_cap)
            capabilities.validate_provider(self._tts.provider_id)
            outgoing = TtsRequest(
                operation_id=operation,
                text=text,
                voice_profile_id=request.tts_voice_profile_id,
                language_hint=request.tts_language_hint,
            )
            outgoing.validate_capabilities(capabilities)
            raw_audio = await self._tts.synthesize(outgoing)
            self._check()
            audio = TtsAudio.model_validate(raw_audio)
            audio.validate_request(outgoing, capabilities)
        except Exception:
            self._check()
            return StartupVoiceFailureStage.TTS
        try:
            playback = AudioPlaybackRequest(
                playback_id=operation,
                session_id=request.session_id,
                turn_id=operation,
                audio=audio,
            )
            raw_result = await self._output.play(playback)
            self._check()
            result = AudioPlaybackResult.model_validate(raw_result)
            result.validate_request(playback, self._output.output_id)
            if result.status is not AudioPlaybackStatus.COMPLETED:
                raise ValueError
        except Exception:
            self._check()
            return StartupVoiceFailureStage.PLAYBACK
        return None

    def _select_failsafe(self, request: RisePhoenixStartupRequest) -> FailSafeDecision:
        self._check()
        try:
            policy = FailSafeRequest(
                failsafe_id=request.startup_id,
                trigger=FailSafeTrigger.CRITICAL_SUBSYSTEM_DEGRADATION,
                reason=_DEGRADED_REASON,
                requested_capabilities=_CAPABILITIES,
                recovery_result=None,
            )
            raw = self._failsafe.activate(policy)
            self._check()
            if type(raw) is not FailSafeDecision:
                raise ValueError
            return _decision(raw, request.startup_id)
        except Exception:
            self._check()
            raise StartupError(StartupErrorCode.FAILSAFE_FAILURE) from None

    async def _health(self, startup_id: str) -> tuple[StartupCheckResult, ...]:
        checks = []
        for probe, subsystem in zip(self._probes, StartupSubsystem):
            self._check()
            try:
                identity_matches = probe.subsystem is subsystem
            except Exception:
                raise StartupError(StartupErrorCode.HEALTH_FAILURE) from None
            if not identity_matches:
                raise StartupError(StartupErrorCode.HEALTH_FAILURE)
            try:
                raw = await probe.check(startup_id)
                self._check()
            except Exception:
                self._check()
                checks.append(
                    StartupCheckResult(
                        startup_id=startup_id,
                        subsystem=subsystem,
                        status=StartupCheckStatus.UNAVAILABLE,
                    )
                )
                continue
            try:
                result = StartupCheckResult.model_validate(raw)
                if (
                    result.startup_id != startup_id
                    or result.subsystem is not subsystem
                    or probe.subsystem is not subsystem
                ):
                    raise ValueError
            except Exception:
                raise StartupError(StartupErrorCode.HEALTH_FAILURE) from None
            checks.append(result)
        return tuple(checks)

    async def _rollback(self, request: RisePhoenixStartupRequest) -> None:
        try:
            if (
                self._conversation.session_id == request.session_id
                and not self._conversation.closed
            ):
                await self._conversation.sleep()
        except (Exception, asyncio.CancelledError):
            # Preserve the original abort; do not invent rollback success.
            pass

    async def handle_wake(
        self, request: RisePhoenixStartupRequest
    ) -> RisePhoenixStartupResult | None:
        self._bind()
        accepted = RisePhoenixStartupRequest.model_validate(request)
        if self._active is not None:
            raise StartupError(StartupErrorCode.BUSY)
        try:
            sleeping = (
                not self._conversation.closed
                and self._conversation.state is VoiceConversationState.SLEEPING
            )
        except Exception:
            raise StartupError(StartupErrorCode.CONVERSATION_FAILURE) from None
        if not sleeping:
            raise StartupError(StartupErrorCode.INVALID_INPUT)
        self._active = accepted.startup_id
        activation_attempted = False
        try:
            try:
                raw_cap = await self._wake.capabilities()
                self._check()
                capabilities = WakeWordCapabilities.model_validate(raw_cap)
                capabilities.validate_provider(self._wake.provider_id)
                if DEFAULT_WAKE_PHRASE not in capabilities.supported_phrases:
                    raise ValueError
                raw_detection = await self._wake.detect(accepted.wake_request)
                self._check()
                detection = WakeWordDetection.model_validate(raw_detection)
                detection.validate_request(
                    accepted.wake_request, self._wake.provider_id
                )
            except Exception:
                self._check()
                raise StartupError(StartupErrorCode.WAKE_FAILURE) from None
            if not detection.detected:
                return None
            await self._publish(EventType.PHOENIX_STARTED, accepted)
            self._check()
            voice_failure = await self._speak(accepted, STARTUP_ACKNOWLEDGEMENT, "ack")
            self._check()
            checks = await self._health(accepted.startup_id)
            self._check()
            outcome = (
                StartupOutcome.READY
                if voice_failure is None
                and all(check.status is StartupCheckStatus.HEALTHY for check in checks)
                else StartupOutcome.DEGRADED
            )
            decision = (
                self._select_failsafe(accepted)
                if outcome is StartupOutcome.DEGRADED
                else None
            )
            activation_attempted = True
            try:
                await self._conversation.activate(accepted.session_id)
                self._check()
                if not self._listening(accepted):
                    raise ValueError
            except Exception:
                self._check()
                raise StartupError(StartupErrorCode.CONVERSATION_FAILURE) from None
            response = (
                STARTUP_HEALTHY_RESPONSE
                if outcome is StartupOutcome.READY
                else STARTUP_DEGRADED_RESPONSE
            )
            if voice_failure is None:
                voice_failure = await self._speak(accepted, response, "final")
                self._check()
                if voice_failure is not None:
                    outcome = StartupOutcome.DEGRADED
                    response = STARTUP_DEGRADED_RESPONSE
                    if decision is None:
                        decision = self._select_failsafe(accepted)
            if not self._listening(accepted):
                raise StartupError(StartupErrorCode.CONVERSATION_FAILURE)
            result = RisePhoenixStartupResult(
                startup_id=accepted.startup_id,
                session_id=accepted.session_id,
                outcome=outcome,
                checks=checks,
                response_text=response,
                voice_failure_stage=voice_failure,
                failsafe_decision=decision,
                # Just observed/correlated above, with no intervening await.
                final_voice_state=VoiceConversationState.LISTENING,
            )
            await self._publish(
                EventType.PHOENIX_READY
                if outcome is StartupOutcome.READY
                else EventType.PHOENIX_DEGRADED,
                accepted,
                result,
            )
            self._check()
            return result
        except (Exception, asyncio.CancelledError):
            if activation_attempted:
                await self._rollback(accepted)
            raise
        finally:
            self._active = None

    async def aclose(self) -> None:
        self._bind(closing=True)
        self._closed = True
        self._active = None
