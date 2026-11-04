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

PHX-135 (ADR-245, owner override of ADR-108's "voice never approves"): when a
spoken request's mission waits for permission, PHOENIX asks aloud ("I need
permission to <action>. Say granted or denied.") and binds that ONE request.
For 15 s after the question has been spoken, the exact replies "granted",
"approve" or "yes, granted" approve it and "denied", "deny" or "no" deny it,
through the same ``MissionService.approve``/``deny`` path as the desktop
(PermissionEngine, grants, audit and re-run unchanged; P1/P2 "session", P3
"session", P4 one-time; P5 never waits). Any other reply, a late one, or a
request no longer pending decides nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol
from uuid import uuid4

from phoenix.agents.answer_stream import PartialAnswers
from phoenix.agents.mission_events import EventPublisher, EventSink
from phoenix.agents.registry import AgentRegistry
from phoenix.api.missions import MissionService, NotFoundError
from phoenix.core.events import EventType
from phoenix.persistence.missions import (
    MissionRecord,
    MissionStatus,
    PermissionRequestRecord,
    RequestStatus,
)
from phoenix.security.permissions import GrantDuration, PermissionRequest
from phoenix.tools.registry import ToolRegistry
from phoenix.voice.ack import AckClips, default_cache_dir
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
    first_spoken_sentence,
    speakable_text,
    strip_wake_phrase,
)
from phoenix.voice.engines.sound import MicrophoneError
from phoenix.voice.engines.whisper import VoiceModelMissingError
from phoenix.voice.persona import site_failed_line, site_pending_line, site_ready_line
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
Preview = Callable[[str], Awaitable[bool]]
# PHX-135 (ADR-245, ADR-246): spoken approval of one pending request.
VoiceApprovalCheck = Callable[[PermissionRequestRecord], bool]
VOICE_APPROVAL_SECONDS: Final = 15.0
GRANT_REPLIES: Final = frozenset({"granted", "approve", "yes granted"})
DENY_REPLIES: Final = frozenset({"denied", "deny", "no"})
# What the user is asked to allow, by tool family; never the resource text.
_ACTION_SUMMARIES: Final = {
    "desktop.open_link": "open a link in your browser",
    "desktop.open_app": "open an app",
    "screen.capture_window": "look at the window on your screen",
    "web.research": "search the web",
    "document.read": "read a document in your workspace",
    "document.create": "create a document in your workspace",
}
_FAMILY_SUMMARIES: Final = {
    "filesystem": "read files in your workspace",
    "spotify": "control Spotify",
    "youtube": "use YouTube",
    "browser": "control your browser",
    "windows": "control a window on your computer",
    "terminal": "run a terminal command",
}


def spoken_reply(text: str) -> str:
    """A transcript reduced to lower-case words: "Yes, granted." -> "yes granted"."""
    words = re.sub(r"[^\w\s]", " ", strip_wake_phrase(text).lower()).split()
    return " ".join(words)


def action_summary(request: PermissionRequest) -> str:
    """A short, fixed description of what a request would do (ADR-246)."""
    family = request.tool_id.split(".", 1)[0]
    return _ACTION_SUMMARIES.get(request.tool_id) or _FAMILY_SUMMARIES.get(
        family, "use " + family.replace("_", " ")
    )


@dataclass
class _SpokenApproval:
    """PHX-135: the one request a spoken reply may decide."""

    request_id: str
    mission_id: str
    deadline: float | None = None  # set once the question has been spoken


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


log = logging.getLogger("phoenix.voice.timing")
SITE_WAIT_SECONDS: Final = 120.0


class SiteOutcome(Protocol):
    kind: str  # "site" | "passthrough"
    site_id: str | None


class SiteCommand(Protocol):
    """phoenix.sites.command.handle_command (agent A)."""

    async def __call__(self, text: str, active_site_id: str | None) -> SiteOutcome: ...


# {"status": "building"|"ready"|"failed", "sections": int | list, ...}
SiteStatusReader = Callable[[str], Awaitable[Mapping[str, object]]]


def default_site_command() -> SiteCommand | None:
    try:
        module = importlib.import_module("phoenix.sites.command")
    except ImportError:  # agent A's module has not landed
        return None
    handle: SiteCommand = module.handle_command
    return handle


def _section_count(status: Mapping[str, object]) -> int | None:
    spec = status.get("spec")
    for value in (
        status.get("sections"),
        spec.get("sections") if isinstance(spec, Mapping) else None,
    ):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, list):
            return len(value)
    return None


class TurnTimer:
    """Per-stage latency logs: turn ids and milliseconds only, never text."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._stt_done: dict[str, float] = {}

    def stage(self, name: str, turn_id: str, ms: float, kind: str = "") -> None:
        log.info(
            "voice_timing stage=%s%s ms=%d",
            name,
            f" kind={kind}" if kind else "",
            ms,
            extra={"correlation_id": turn_id},
        )

    def stt_done(self, turn_id: str, took: float) -> None:
        self._stt_done[turn_id] = self._clock()
        if len(self._stt_done) > 16:
            self._stt_done.pop(next(iter(self._stt_done)))
        self.stage("stt", turn_id, took * 1000)

    def since_stt(self, name: str, turn_id: str, kind: str = "") -> None:
        start = self._stt_done.get(turn_id)
        if start is not None:
            self.stage(name, turn_id, (self._clock() - start) * 1000, kind)


def instrument(components: VoiceComponents, timer: TurnTimer) -> None:
    """Time STT and the answer's first audio by wrapping the two bound methods."""
    stt, output = components.stt, components.output
    transcribe, play = stt.transcribe, output.play

    async def timed_transcribe(request):  # type: ignore[no-untyped-def]
        start = time.monotonic()
        try:
            return await transcribe(request)
        finally:
            timer.stt_done(request.operation_id, time.monotonic() - start)

    async def timed_play(request):  # type: ignore[no-untyped-def]
        timer.since_stt("first_audio", request.playback_id, "answer")
        return await play(request)

    with contextlib.suppress(AttributeError, TypeError):
        stt.transcribe = timed_transcribe  # type: ignore[method-assign]
        output.play = timed_play  # type: ignore[method-assign]


class MissionVoiceResponder(VoiceResponder):
    """Submits a spoken request as a chat mission and waits for its answer.

    The same MissionService calls as ``POST /chat``: one conversation per voice
    run (created like a chat without ``conversation_id``), ``submit`` with no
    path so PHOENIX triages it. Without ``may_approve`` it never decides a
    permission: a mission waiting for approval is announced and left for the
    user in the desktop app. PHX-135 (ADR-245): with it, the user's exact
    spoken reply decides that one request, as the user's own decision, through
    MissionService (INV-003, INV-005 keep holding: the user decides).
    """

    def __init__(
        self,
        missions: MissionService,
        *,
        on_mission: Callable[[str, str], Awaitable[None]] | None = None,
        wait_seconds: float = MISSION_WAIT_SECONDS,
        partial_answers: PartialAnswers | None = None,
        may_approve: VoiceApprovalCheck | None = None,
        approval_seconds: float = VOICE_APPROVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        acknowledge: Callable[[str], Awaitable[object]] | None = None,
        site_command: SiteCommand | None = None,
        site_status: SiteStatusReader | None = None,
        site_wait_seconds: float = SITE_WAIT_SECONDS,
        timer: TurnTimer | None = None,
    ) -> None:
        super().__init__("voice_mission_responder")
        self._missions = missions
        self._partials = partial_answers
        self._on_mission = on_mission
        self._wait = wait_seconds
        self._may_approve = may_approve
        self._approval_seconds = approval_seconds
        self._clock = clock
        self._approval: _SpokenApproval | None = None
        self._acknowledge = acknowledge
        self._site_command = site_command
        self._site_status = site_status
        self._site_wait = site_wait_seconds
        self._timer = timer or TurnTimer()
        self.conversation_id: str | None = None
        self.mission_id: str | None = None
        self.active_site_id: str | None = None

    @property
    def awaiting_approval(self) -> str | None:
        """The request ID a spoken reply may still decide, if any."""
        approval = self._approval
        if approval is None or (
            approval.deadline is not None and self._clock() > approval.deadline
        ):
            return None
        return approval.request_id

    def open_approval_window(self) -> None:
        """The question has been spoken: the 15 s reply window starts now."""
        approval = self._approval
        if approval is not None and approval.deadline is None:
            approval.deadline = self._clock() + self._approval_seconds

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

    async def _settled(
        self, mission_id: str, preview: Preview | None = None
    ) -> MissionRecord | str | None:
        """The settled record, None on timeout, or the previewed sentence when
        the user barged in during it (PHX-132: the turn ends at once)."""
        try:
            async with asyncio.timeout(self._wait):
                while True:
                    record = await self._missions.get_mission(mission_id)
                    if record.status in _SETTLED:
                        return record
                    if preview is not None and self._partials is not None:
                        sentence = first_spoken_sentence(
                            self._partials.text(mission_id)
                        )
                        if sentence is not None:
                            go_on, preview = await preview(sentence), None
                            if not go_on:
                                return sentence
                            continue
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

    async def _site_line(self, site_id: str | None) -> str:
        if self._site_status is None or site_id is None:
            return site_pending_line()
        try:
            async with asyncio.timeout(self._site_wait):
                while True:
                    status = await self._site_status(site_id)
                    if status.get("status") == "ready":
                        return site_ready_line(_section_count(status))
                    if status.get("status") == "failed":
                        return site_failed_line()
                    await asyncio.sleep(_POLL_SECONDS)
        except Exception:  # includes the wait timeout
            return site_pending_line()

    async def _site(self, text: str) -> str | None:
        """A spoken completion line when handle_command took the request."""
        if self._site_command is None:
            return None
        try:
            outcome = await self._site_command(text, self.active_site_id)
        except Exception:
            return None  # fail soft: the normal mission path still answers
        if outcome.kind != "site":
            return None
        if outcome.site_id is not None:
            self.active_site_id = outcome.site_id
        return await self._site_line(outcome.site_id)

    async def _mission(
        self, text: str, turn_id: str, preview: Preview | None = None
    ) -> str:
        # PHX-135: any non-empty reply ends the approval binding (one shot).
        approval, self._approval = self._approval, None
        reply = spoken_reply(text)
        if approval is not None and reply in GRANT_REPLIES | DENY_REPLIES:
            return await self._decide(approval, reply in GRANT_REPLIES, turn_id)
        mission_id = await self._submit(text[:16000])
        try:
            spoken = await self._follow(mission_id, turn_id, preview)
        except asyncio.CancelledError:  # voice stopped or turn cancelled
            with contextlib.suppress(Exception):
                await asyncio.shield(self._missions.cancel(mission_id))
            raise
        self._timer.since_stt("first_token", turn_id)
        return spoken

    async def respond(self, request: VoiceResponseRequest) -> VoiceResponseDraft:
        return await self._respond(request, None)

    async def respond_with_preview(
        self, request: VoiceResponseRequest, preview: Preview
    ) -> VoiceResponseDraft:
        """PHX-132 (ADR-233): a DIRECT answer's first sentence is spoken while
        the rest is still being generated."""
        return await self._respond(request, preview)

    async def _respond(
        self, request: VoiceResponseRequest, preview: Preview | None
    ) -> VoiceResponseDraft:
        accepted = VoiceResponseRequest.model_validate(request)
        text = strip_wake_phrase(accepted.transcript_text).strip()
        if not text:
            spoken = "I'm listening."
        else:
            turn_id = accepted.turn_id
            ack = None
            if self._acknowledge is not None:
                self._timer.since_stt("first_audio", turn_id, "ack")
                ack = asyncio.ensure_future(self._acknowledge(turn_id))
            try:
                spoken = await self._site(text) or await self._mission(
                    text, turn_id, preview
                )
            except BaseException:
                if ack is not None:
                    ack.cancel()  # flushes the clip via the output's cancel path
                raise
            if ack is not None:
                with contextlib.suppress(Exception):
                    await ack  # the output is free before the answer plays
        return VoiceResponseDraft(
            session_id=accepted.session_id,
            turn_id=accepted.turn_id,
            response_text=spoken,
        )

    async def _follow(
        self, mission_id: str, turn_id: str, preview: Preview | None = None
    ) -> str:
        """What to say about this mission once it settles."""
        self.mission_id = mission_id
        if self._on_mission is not None:
            with contextlib.suppress(Exception):
                await self._on_mission(turn_id, mission_id)
        try:
            settled = await self._settled(mission_id, preview)
        finally:
            if self._partials is not None:
                self._partials.discard(mission_id)
        if isinstance(settled, str):
            return settled
        if (
            settled is not None
            and settled.status is MissionStatus.WAITING_PERMISSION
            and self._may_approve is not None
        ):
            question = await self._ask(settled)
            if question is not None:
                return question
        return _spoken_outcome(
            settled, await self._answer(settled) if settled else None
        )

    async def _ask(self, record: MissionRecord) -> str | None:
        """PHX-135 (ADR-245): bind the mission's one pending request and ask."""
        try:
            pending = [
                item
                for item in await self._missions.list_pending_permissions()
                if item.mission_id == record.mission_id
            ]
        except Exception:
            return None
        if len(pending) != 1:
            return None  # none, or ambiguous: the desktop decides
        request = pending[0].request
        self._approval = _SpokenApproval(request.request_id, record.mission_id)
        return f"I need permission to {action_summary(request)}. Say granted or denied."

    async def _decide(
        self, approval: _SpokenApproval, approve: bool, turn_id: str
    ) -> str:
        """The user's spoken decision on the one bound request (ADR-245/246)."""
        if approval.deadline is None or self._clock() > approval.deadline:
            return "That approval window has closed. Please use the Phoenix window."
        try:
            record = await self._missions.get_permission_request(approval.request_id)
            if (
                record.mission_id != approval.mission_id
                or record.status is not RequestStatus.PENDING
            ):
                return "That request is no longer waiting."
            if not approve:
                await self._missions.deny(approval.request_id)
                return "Denied. I won't do that."
            assert self._may_approve is not None
            if not self._may_approve(record):
                return (
                    "I can't approve that one by voice. Please use the Phoenix window."
                )
            # Session for P1/P2/P3; MissionService makes P4 one-time (ADR-151).
            rerun = await self._missions.approve(
                approval.request_id, GrantDuration.SESSION
            )
        except Exception:
            return "I couldn't record that. Please use the Phoenix window."
        return await self._follow(rerun, turn_id)


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
        partial_answers: PartialAnswers | None = None,
        may_approve: VoiceApprovalCheck | None = None,
        site_command: SiteCommand | None = None,
        site_status: SiteStatusReader | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        self._missions = missions
        self._partials = partial_answers
        self._may_approve = may_approve  # PHX-135: None = voice never decides
        self._site_command = site_command or default_site_command()
        self._site_status = site_status
        self._cache_dir = cache_dir
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
        responder = self._responder
        if change.state is VoicePipelineState.LISTENING and responder is not None:
            # PHX-135: the question has been spoken (PHOENIX is silent now).
            responder.open_approval_window()
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
            timer = TurnTimer()
            instrument(components, timer)
            clips = AckClips(self._cache_dir or default_cache_dir())
            await clips.prepare(components.tts, components.voice_profile_id)

            async def acknowledge(turn_id: str) -> bool:
                return await clips.play(components.output, session, turn_id)

            responder = MissionVoiceResponder(
                self._missions,
                on_mission=self._note_mission,
                partial_answers=self._partials,
                may_approve=self._may_approve,
                acknowledge=acknowledge,
                site_command=self._site_command,
                site_status=self._site_status,
                timer=timer,
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
