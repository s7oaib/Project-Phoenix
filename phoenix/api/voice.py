"""PHX-114 backend voice service: the live pipeline behind ``/voice`` (ADR-108).

``VoiceService`` owns at most one ``VoicePipeline`` run. Voice starts only on
request (``POST /voice/start``, or ``PHOENIX_VOICE_AUTOSTART=1`` at launch);
models load in worker threads while the state is ``starting``. A spoken request
becomes a normal chat mission through ``MissionService.submit``, exactly what
``POST /chat`` does, so PHOENIX triages it and every tool call still passes
PermissionEngine; voice adds no path and no authority. The answer appears in
the conversation like any other, and a short spoken rendering is played.

Voice failures are isolated (INV-015, INV-016): ``start``/``stop``/``status``
never raise, a crashed run becomes state ``unavailable`` with a closed code,
and text chat is untouched. State changes are published as allowlisted
``voice.*`` events (IDs and closed values only) and never block or fail.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol
from uuid import uuid4

from phoenix.agents.mission_events import EventPublisher, EventSink
from phoenix.agents.registry import AgentRegistry
from phoenix.api.missions import MissionService, NotFoundError
from phoenix.core.events import EventType
from phoenix.persistence.missions import MissionRecord, MissionStatus
from phoenix.tools.registry import ToolRegistry
from phoenix.voice.conversation import (
    VoiceResponder,
    VoiceResponseDraft,
    VoiceResponseRequest,
)
from phoenix.voice.engines.models import (
    DEFAULT_WAKE_MODEL,
    PIPER_VOICE,
    WHISPER_MODELS,
    default_models_dir,
    missing_files,
    selected_stt_model,
)
from phoenix.voice.engines.pipeline import (
    VoiceComponents,
    VoiceFailureCode,
    VoicePipeline,
    VoicePipelineConfig,
    VoicePipelineState,
    VoiceStateChange,
    speakable_text,
    strip_wake_phrase,
)
from phoenix.voice.engines.sound import MicrophoneError
from phoenix.voice.engines.whisper import VoiceModelMissingError
from phoenix.voice.startup import (
    StartupCheckResult,
    StartupCheckStatus,
    StartupHealthProbe,
    StartupSubsystem,
)

__all__ = (
    "VOICE_COMPONENT_IDS",
    "ComponentStatus",
    "VoiceStatus",
    "ComponentFactory",
    "local_components",
    "MissionVoiceResponder",
    "VoiceHealth",
    "health_probes",
    "VoiceService",
)

AUTOSTART_ENV: Final = "PHOENIX_VOICE_AUTOSTART"
# Mirrors the desktop's VoicePort component IDs (apps/desktop/src/App.tsx).
VOICE_COMPONENT_IDS: Final = (
    "wake_word",
    "vad",
    "stt",
    "tts",
    "conversation",
    "audio_output",
)
_POLL_SECONDS: Final = 0.25
MISSION_WAIT_SECONDS: Final = 600.0
_SETTLED: Final = frozenset(
    {
        MissionStatus.COMPLETED,
        MissionStatus.FAILED,
        MissionStatus.CANCELLED,
        MissionStatus.WAITING_PERMISSION,
        MissionStatus.SUPERSEDED,
    }
)

ComponentStatus = str  # "available" | "degraded" | "unavailable"


@dataclass(frozen=True)
class VoiceStatus:
    state: VoicePipelineState
    code: VoiceFailureCode | None
    session_id: str | None
    conversation_id: str | None
    mission_id: str | None
    components: tuple[tuple[str, ComponentStatus], ...]


ComponentFactory = Callable[[], Awaitable[VoiceComponents]]


def _load_local_components(root: Path, stt_model: str) -> VoiceComponents:
    """Blocking: build and load the local engines (never downloads)."""
    from phoenix.voice.engines.piper import PRIMARY_PROFILE_ID, PiperTtsProvider
    from phoenix.voice.engines.sound import (
        SoundDeviceAudioOutput,
        SoundDeviceMicrophone,
    )
    from phoenix.voice.engines.whisper import (
        FasterWhisperSttProvider,
        FasterWhisperWakeWordProvider,
        WhisperEngine,
    )

    wake_engine = WhisperEngine(
        WHISPER_MODELS[DEFAULT_WAKE_MODEL].path(root), cpu_threads=4, beam_size=1
    )
    stt_engine = WhisperEngine(
        WHISPER_MODELS[stt_model].path(root), cpu_threads=8, beam_size=5
    )
    tts = PiperTtsProvider(PIPER_VOICE.path(root) / PIPER_VOICE.files[0].relative_path)
    wake_engine.load()
    stt_engine.load()
    tts.load()
    return VoiceComponents(
        wake=FasterWhisperWakeWordProvider(wake_engine),
        stt=FasterWhisperSttProvider(stt_engine),
        tts=tts,
        microphone=SoundDeviceMicrophone(),
        output=SoundDeviceAudioOutput(),
        voice_profile_id=PRIMARY_PROFILE_ID,
    )


def local_components(models_dir: Path | None = None) -> ComponentFactory:
    async def build() -> VoiceComponents:
        root = models_dir or default_models_dir()
        stt_model = selected_stt_model()
        for model in (
            WHISPER_MODELS[DEFAULT_WAKE_MODEL],
            WHISPER_MODELS[stt_model],
            PIPER_VOICE,
        ):
            if missing_files(root, model):
                raise VoiceModelMissingError("voice model files are missing")
        return await asyncio.to_thread(_load_local_components, root, stt_model)

    return build


def models_installed(models_dir: Path | None = None) -> dict[str, bool]:
    root = models_dir or default_models_dir()
    return {
        "wake_word": not missing_files(root, WHISPER_MODELS[DEFAULT_WAKE_MODEL]),
        "stt": not missing_files(root, WHISPER_MODELS[selected_stt_model()]),
        "tts": not missing_files(root, PIPER_VOICE),
    }


class MissionVoiceResponder(VoiceResponder):
    """Submits a spoken request as a chat mission and waits for its answer.

    The same MissionService calls as ``POST /chat``: one conversation per voice
    run (created like a chat without ``conversation_id``), ``submit`` with no
    path so PHOENIX triages it. The responder only reads the settled mission;
    it never approves permissions (INV-003, INV-005): a mission waiting for
    approval is announced and left for the user in the desktop app.
    """

    def __init__(
        self,
        missions: MissionService,
        *,
        on_mission: Callable[[str, str], Awaitable[None]] | None = None,
        wait_seconds: float = MISSION_WAIT_SECONDS,
    ) -> None:
        super().__init__("voice_mission_responder")
        self._missions = missions
        self._on_mission = on_mission
        self._wait = wait_seconds
        self.conversation_id: str | None = None
        self.mission_id: str | None = None

    async def _submit(self, text: str) -> str:
        if self.conversation_id is not None:
            try:
                return await self._missions.submit(self.conversation_id, text, None)
            except NotFoundError:
                self.conversation_id = None  # deleted from the chat meanwhile
        conversation_id = await self._missions.create_conversation()
        try:
            mission_id = await self._missions.submit(conversation_id, text, None)
        except Exception:
            with contextlib.suppress(Exception):
                await self._missions.delete_conversation(conversation_id)
            raise
        self.conversation_id = conversation_id
        return mission_id

    async def _settled(self, mission_id: str) -> MissionRecord | None:
        try:
            async with asyncio.timeout(self._wait):
                while True:
                    record = await self._missions.get_mission(mission_id)
                    if record.status in _SETTLED:
                        return record
                    await asyncio.sleep(_POLL_SECONDS)
        except TimeoutError:
            return None

    async def _answer(self, record: MissionRecord) -> str | None:
        if record.answer_message_id is None:
            return None
        for message in await self._missions.list_messages(record.conversation_id):
            if message.message_id == record.answer_message_id:
                return message.text
        return None

    async def respond(self, request: VoiceResponseRequest) -> VoiceResponseDraft:
        accepted = VoiceResponseRequest.model_validate(request)
        text = strip_wake_phrase(accepted.transcript_text).strip()
        if not text:
            spoken = "I'm listening."
        else:
            mission_id = await self._submit(text[:16000])
            self.mission_id = mission_id
            if self._on_mission is not None:
                with contextlib.suppress(Exception):
                    await self._on_mission(accepted.turn_id, mission_id)
            record = await self._settled(mission_id)
            spoken = _spoken_outcome(
                record, await self._answer(record) if record else None
            )
        return VoiceResponseDraft(
            session_id=accepted.session_id,
            turn_id=accepted.turn_id,
            response_text=spoken,
        )


def _spoken_outcome(record: MissionRecord | None, answer: str | None) -> str:
    if record is None:
        return "This is taking longer than expected. The answer will appear on screen."
    if record.status is MissionStatus.COMPLETED and answer:
        return speakable_text(answer)
    if record.status is MissionStatus.WAITING_PERMISSION:
        return (
            "I need your permission for that. "
            "Please review the request in the Phoenix window."
        )
    if record.status is MissionStatus.CANCELLED:
        return "That mission was cancelled."
    return "Sorry, I couldn't complete that mission. The details are on screen."


class VoiceHealth(Protocol):
    async def database_ok(self) -> bool: ...
    async def providers_ok(self) -> bool: ...


class _Probe(StartupHealthProbe):
    def __init__(
        self,
        subsystem: StartupSubsystem,
        check: Callable[[], Awaitable[StartupCheckStatus]],
    ) -> None:
        super().__init__(subsystem)
        self._check = check

    async def check(self, startup_id: str) -> StartupCheckResult:
        try:
            status = await self._check()
        except Exception:
            status = StartupCheckStatus.UNAVAILABLE
        return StartupCheckResult(
            startup_id=startup_id, subsystem=self.subsystem, status=status
        )


def _status(ok: bool) -> StartupCheckStatus:
    return StartupCheckStatus.HEALTHY if ok else StartupCheckStatus.UNAVAILABLE


def health_probes(
    health: VoiceHealth, tools: ToolRegistry, agents: AgentRegistry
) -> tuple[StartupHealthProbe, ...]:
    """The six PHX-065 checks, each a real, bounded check of this backend."""

    async def persistence() -> StartupCheckStatus:
        return _status(await health.database_ok())

    async def hardware() -> StartupCheckStatus:
        import psutil

        memory = await asyncio.to_thread(psutil.virtual_memory)
        return (
            StartupCheckStatus.HEALTHY
            if memory.available >= 1 << 30
            else StartupCheckStatus.DEGRADED
        )

    async def model_provider() -> StartupCheckStatus:
        return _status(await health.providers_ok())

    async def tool_registry() -> StartupCheckStatus:
        return _status(bool(tools.list_tools()))

    async def voice() -> StartupCheckStatus:
        return StartupCheckStatus.HEALTHY  # this run just heard the wake phrase

    async def agent_registry() -> StartupCheckStatus:
        return _status(agents.contains("phoenix") and agents.contains("voice"))

    checks = {
        StartupSubsystem.PERSISTENCE: persistence,
        StartupSubsystem.HARDWARE: hardware,
        StartupSubsystem.MODEL_PROVIDER: model_provider,
        StartupSubsystem.TOOLS: tool_registry,
        StartupSubsystem.VOICE: voice,
        StartupSubsystem.AGENTS: agent_registry,
    }
    return tuple(_Probe(subsystem, checks[subsystem]) for subsystem in StartupSubsystem)


def _failure_code(error: BaseException) -> VoiceFailureCode:
    if isinstance(error, VoiceModelMissingError):
        return VoiceFailureCode.MODELS_MISSING
    if isinstance(error, MicrophoneError):
        return VoiceFailureCode.MICROPHONE_UNAVAILABLE
    if isinstance(error, ImportError | OSError):
        return VoiceFailureCode.DEPENDENCY_UNAVAILABLE
    return VoiceFailureCode.VOICE_FAILURE


class VoiceService:
    """At most one pipeline run; every public method is failure-isolated."""

    def __init__(
        self,
        missions: MissionService,
        events: EventSink | None,
        *,
        probes: Sequence[StartupHealthProbe] | None = None,
        factory: ComponentFactory | None = None,
        config: VoicePipelineConfig | None = None,
        models_dir: Path | None = None,
    ) -> None:
        self._missions = missions
        self._publisher = EventPublisher(events)
        self._probes = tuple(probes) if probes is not None else None
        self._factory = factory or local_components(models_dir)
        self._config = config
        self._models_dir = models_dir
        self._task: asyncio.Task[None] | None = None
        self._pipeline: VoicePipeline | None = None
        self._responder: MissionVoiceResponder | None = None
        self._state = VoicePipelineState.OFF
        self._code: VoiceFailureCode | None = None
        self._session: str | None = None
        self._mission: str | None = None
        self._lock = asyncio.Lock()

    @property
    def dropped_events(self) -> int:
        return self._publisher.dropped

    @property
    def pipeline(self) -> VoicePipeline | None:
        return self._pipeline

    def status(self) -> VoiceStatus:
        installed = models_installed(self._models_dir)
        state, code = self._state, self._code
        failed = state is VoicePipelineState.UNAVAILABLE

        def engine(name: str) -> ComponentStatus:
            if not installed[name] or (
                failed and code is VoiceFailureCode.MODELS_MISSING
            ):
                return "unavailable"
            return "available"

        output = (
            "degraded"
            if state is VoicePipelineState.DEGRADED_VOICE
            else "unavailable"
            if failed and code is VoiceFailureCode.MICROPHONE_UNAVAILABLE
            else "available"
        )
        components = (
            ("wake_word", engine("wake_word")),
            ("vad", "available"),
            (
                "stt",
                "degraded" if state is VoicePipelineState.TEXT_ONLY else engine("stt"),
            ),
            ("tts", engine("tts")),
            ("conversation", "unavailable" if failed else "available"),
            ("audio_output", output),
        )
        return VoiceStatus(
            state=state,
            code=code,
            session_id=self._session,
            conversation_id=None
            if self._responder is None
            else self._responder.conversation_id,
            mission_id=self._mission,
            components=components,
        )

    async def _report(self, change: VoiceStateChange) -> None:
        self._state = change.state
        if change.code is not None:
            self._code = change.code
        if change.mission_id is not None:
            self._mission = change.mission_id
        payload: dict[str, str] = {"state": change.state.value}
        if change.turn_id is not None:
            payload["turn_id"] = change.turn_id
        if change.mission_id is not None:
            payload["mission_id"] = change.mission_id
        if change.code is not None:
            payload["code"] = change.code.value
        if change.session_id is not None:
            payload["session_id"] = change.session_id
        event_type = (
            EventType.VOICE_SPEAKING
            if change.state is VoicePipelineState.SPEAKING
            else EventType.VOICE_INTERRUPTED
            if change.interrupted
            else EventType.VOICE_LISTENING
        )
        await self._publisher.publish(
            event_type,
            payload,
            correlation_id=change.session_id or self._session or "",
            agent_id="voice",
        )

    async def _note_mission(self, turn_id: str, mission_id: str) -> None:
        pipeline = self._pipeline
        if pipeline is not None:
            await pipeline.note_mission(turn_id, mission_id)

    async def _run(self) -> None:
        session = self._session
        assert session is not None
        components = None
        try:
            components = await self._factory()
            responder = MissionVoiceResponder(
                self._missions, on_mission=self._note_mission
            )
            self._responder = responder
            pipeline = VoicePipeline(
                components,
                responder,
                on_state=self._report,
                startup_probes=self._probes,
                config=self._config,
                session_id=session,
            )
            self._pipeline = pipeline
            await pipeline.run()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._report(
                VoiceStateChange(
                    state=VoicePipelineState.UNAVAILABLE,
                    session_id=session,
                    code=_failure_code(error),
                )
            )
        finally:
            self._pipeline = None
            if components is not None:
                await components.aclose()

    async def start(self) -> VoiceStatus:
        """Begin listening for the wake phrase; returns at once (``starting``)."""
        async with self._lock:
            if self._task is not None and not self._task.done():
                return self.status()
            self._session = str(uuid4())
            self._code = None
            self._mission = None
            await self._report(
                VoiceStateChange(
                    state=VoicePipelineState.STARTING, session_id=self._session
                )
            )
            self._task = asyncio.create_task(self._run())
            return self.status()

    async def stop(self) -> VoiceStatus:
        """Stop listening and speaking; the microphone is released."""
        async with self._lock:
            task, self._task = self._task, None
            if task is not None and not task.done():
                task.cancel()
                await asyncio.wait({task})  # its outcome is reported, not raised
            session = self._session
            if self._state is not VoicePipelineState.OFF:
                await self._report(
                    VoiceStateChange(state=VoicePipelineState.OFF, session_id=session)
                )
            self._code = None
            return self.status()

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self.stop()
