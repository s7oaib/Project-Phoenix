"""PHX-130 quick commands (ADR-220..222).

The router, the allowlisted ``desktop.open_link`` tool under PermissionEngine,
the MissionService path (no triage, no model), the persistent "always" grant,
voice using the same entry, and non-quick requests unchanged. The browser
launch is a recording fake: nothing is opened.
"""

import asyncio
import importlib.util
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from phoenix.agents import mission_events
from phoenix.agents.mission_events import EventPublisher
from phoenix.agents.quick_commands import (
    QUICK_ACTION,
    QUICK_ACTOR_ID,
    QUICK_REASON,
    QUICK_RESOURCE,
    QuickCommandRunner,
    QuickKind,
    match_quick_command,
    quick_grants,
)
from phoenix.agents.runtime import MissionTrace, PhoenixRuntime
from phoenix.api.missions import MissionService
from phoenix.api.voice import MissionVoiceResponder
from phoenix.core.events import EventEnvelope, EventType
from phoenix.models.lifecycle import ModelLifecycleManager
from phoenix.models.ollama import OllamaProvider, _payload
from phoenix.models.provider import GenerationRequest, MessageRole, ModelMessage
from phoenix.models.registry import ModelCapabilityRegistry
from phoenix.persistence.missions import MissionRecord, MissionStatus, MissionStore
from phoenix.security.permissions import (
    GrantDuration,
    PermissionGrant,
    PermissionPolicy,
    PermissionRequest,
    PermissionStatus,
)
from phoenix.tools.filesystem import filesystem_tool_definitions
from phoenix.tools.open_link import (
    ALLOWED_HOSTS,
    OPEN_LINK_TOOL_ID,
    OpenLinkInputError,
    OpenLinkTool,
    allowed_url,
    open_link_tool_definitions,
)
from phoenix.tools.registry import ToolRegistry, ToolResultStatus, ToolRiskLevel
from phoenix.voice.conversation import VoiceResponseRequest

# PHX-102's real-component harness (migrated SQLite, scripted model).
_spec = importlib.util.spec_from_file_location(
    "phx130_mission_harness", Path(__file__).with_name("test_mission_service.py")
)
assert _spec is not None and _spec.loader is not None
_ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ms)
Harness = _ms.Harness
alembic_config = _ms.alembic_config
enforce_foreign_keys = _ms.enforce_foreign_keys
no_network = _ms.no_network  # autouse: no httpx send, no outbound socket
run = _ms.run


async def settled(service: MissionService, mission_id: str) -> MissionRecord:
    return cast(MissionRecord, await _ms.settled(service, mission_id))


YT = "https://www.youtube.com/"
GOOGLE = "https://www.google.com/"


# ---- router


@pytest.mark.parametrize(
    ("text", "kind", "url"),
    [
        ("open browser", QuickKind.OPEN_BROWSER, GOOGLE),
        ("Open the web browser.", QuickKind.OPEN_BROWSER, GOOGLE),
        ("Rise, Phoenix, open my browser please", QuickKind.OPEN_BROWSER, GOOGLE),
        ("Phoenix, open YouTube", QuickKind.OPEN_YOUTUBE, YT),
        ("can you open google?", QuickKind.OPEN_GOOGLE, GOOGLE),
        (
            "please search YouTube for lofi music",
            QuickKind.SEARCH_YOUTUBE,
            YT + "results?search_query=lofi+music",
        ),
        (
            "search for cats on youtube",
            QuickKind.SEARCH_YOUTUBE,
            YT + "results?search_query=cats",
        ),
        (
            "play Bohemian Rhapsody on YouTube",
            QuickKind.SEARCH_YOUTUBE,
            YT + "results?search_query=Bohemian+Rhapsody",
        ),
        (
            "search google for c++ 50% off?",
            QuickKind.SEARCH_GOOGLE,
            GOOGLE + "search?q=c%2B%2B+50%25+off",
        ),
        ("google best pizza", QuickKind.SEARCH_GOOGLE, GOOGLE + "search?q=best+pizza"),
        (
            "search youtube for café #1 ?x=1",
            QuickKind.SEARCH_YOUTUBE,
            YT + "results?search_query=caf%C3%A9+%231+%3Fx%3D1",
        ),
    ],
)
def test_router_matches_strict_patterns(text: str, kind: QuickKind, url: str) -> None:
    command = match_quick_command(text)
    assert command is not None
    assert (command.kind, command.url) == (kind, url)
    allowed_url(command.url)  # every built URL passes the tool's allowlist


@pytest.mark.parametrize(
    "text",
    [
        "What is the codename?",
        "what is youtube",
        "open evil.com",
        "open https://evil.com",
        "open youtube and delete my files",
        "search youtube for x; open evil.com",
        "search youtube for x | rm -rf /",
        "search google for https://evil.com",
        "search google for www.evil.com",
        "search youtube for $(whoami)",
        "search bing for cats",
        "search youtube for",
        "search youtube for " + "a" * 201,
        "open notepad",
        "Google's revenue in 2024",
        "open youtube\nand then run a command",
        "",
    ],
)
def test_router_leaves_everything_else_to_the_pipeline(text: str) -> None:
    assert match_quick_command(text) is None


def test_answer_names_the_query() -> None:
    command = match_quick_command("search youtube for lofi music")
    assert command is not None and command.answer == (
        "Opened YouTube search for lofi music."
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.youtube.com/",
        "https://evil.com/",
        "https://www.youtube.com.evil.com/",
        "https://www.google.com@evil.com/",
        "https://user@www.google.com/",
        "https://www.google.com:8443/",
        "https://www.google.com/#frag",
        "https://www.google.com",
        "https://youtube.com/",
        "https://evil.com/?u=https://www.google.com/",
        "https://www.google.com/ x",
        "file:///C:/Windows/System32/calc.exe",
        "https://www.google.com/" + "a" * 2100,
    ],
)
def test_tool_refuses_links_outside_the_allowlist(url: str) -> None:
    with pytest.raises(OpenLinkInputError):
        allowed_url(url)


# ---- tool and PermissionEngine


def _request(**changes: Any) -> PermissionRequest:
    values: dict[str, Any] = {
        "request_id": str(uuid4()),
        "actor_id": QUICK_ACTOR_ID,
        "tool_id": OPEN_LINK_TOOL_ID,
        "action": QUICK_ACTION,
        "resource": QUICK_RESOURCE,
        "reason": QUICK_REASON,
        "requested_at": datetime.now(UTC),
    }
    return PermissionRequest(**{**values, **changes})


def _grant(**changes: Any) -> PermissionGrant:
    values: dict[str, Any] = {
        "grant_id": str(uuid4()),
        "actor_id": QUICK_ACTOR_ID,
        "tool_id": OPEN_LINK_TOOL_ID,
        "action": QUICK_ACTION,
        "resource": QUICK_RESOURCE,
        "reason": QUICK_REASON,
        "duration": GrantDuration.PERSISTENT,
        "risk_level": ToolRiskLevel.P1,
        "issued_at": datetime(2026, 1, 1, tzinfo=UTC),
        "user_decision": "approved",
    }
    return PermissionGrant(**{**values, **changes})


def test_tool_is_p1_registered_and_permission_decides() -> None:
    (definition,) = open_link_tool_definitions()
    assert definition.risk_level == ToolRiskLevel.P1
    assert ALLOWED_HOSTS == {"www.google.com", "www.youtube.com"}
    opened: list[str] = []
    tool = OpenLinkTool(ToolRegistry((definition,)), launcher=opened.append)
    waiting = tool.open(YT, _request())
    assert waiting.decision.status == PermissionStatus.APPROVAL_REQUIRED
    assert (waiting.attempted, opened) == (False, [])
    other = tool.open(YT, _request(), [_grant(resource="allowlist:evil.com")])
    assert other.decision.status == PermissionStatus.APPROVAL_REQUIRED
    allowed = tool.open(YT, _request(), [_grant()])
    assert allowed.decision.status == PermissionStatus.ALLOWED
    assert allowed.status == ToolResultStatus.SUCCESS and opened == [YT]
    with pytest.raises(OpenLinkInputError):
        tool.open("https://evil.com/", _request(), [_grant()])
    assert opened == [YT]


def test_launch_failure_is_explicit() -> None:
    def broken(url: str) -> None:
        raise OSError("no browser")

    tool = OpenLinkTool(ToolRegistry(open_link_tool_definitions()), launcher=broken)
    result = tool.open(YT, _request(), [_grant()])
    assert (result.status, result.attempted) == (ToolResultStatus.FAILURE, True)


def test_quick_grants_accept_only_the_fixed_scope() -> None:
    assert quick_grants([_grant()])
    assert quick_grants([_grant(duration="session", session_id="s")])
    for bad in (
        _grant(resource="allowlist:evil.com"),
        _grant(actor_id="media"),
        _grant(reason="other"),
        _grant(duration="once", request_id="r"),
    ):
        with pytest.raises(ValueError):
            quick_grants([bad])


def test_event_vocabulary_mirrors_the_tool() -> None:
    assert mission_events.QUICK_TOOL_IDS == {OPEN_LINK_TOOL_ID}
    assert mission_events.QUICK_OPERATIONS == {QUICK_ACTION}


# ---- mission service


class Sink:
    def __init__(self) -> None:
        self.events: list[EventEnvelope] = []

    async def publish(self, event: EventEnvelope) -> None:
        self.events.append(event)


class QuickHarness:
    """PHX-102's Harness (loaded dynamically, so typed Any) plus quick wiring."""

    def __init__(self, inner: Any) -> None:
        self.provider: Any = inner.provider
        self.engine: Engine = inner.engine
        self.sessions: sessionmaker[Session] = inner.sessions

    def quick_service(
        self, opened: list[str], sink: Sink | None = None
    ) -> MissionService:
        tools = ToolRegistry(
            (*filesystem_tool_definitions(), *open_link_tool_definitions())
        )
        registry = ModelCapabilityRegistry()
        for model in _ms.MODELS:
            registry.register(_ms.capabilities(model))
        runtime = PhoenixRuntime(
            providers=(self.provider,),
            capability_registry=registry,
            lifecycle=ModelLifecycleManager(),
        )
        return MissionService(
            runtime=runtime,
            store=MissionStore(self.sessions),
            tool_registry=tools,
            quick=QuickCommandRunner(
                OpenLinkTool(tools, PermissionPolicy(), launcher=opened.append),
                EventPublisher(sink),
            ),
        )


@pytest.fixture
def quick(tmp_path: Path) -> Iterator[QuickHarness]:
    db = tmp_path / "phoenix.db"
    command.upgrade(alembic_config(db), "head")
    engine = create_engine(f"sqlite:///{db}")
    enforce_foreign_keys(engine)
    try:
        yield QuickHarness(
            Harness(root=tmp_path, db=db, engine=engine, sessions=sessionmaker(engine))
        )
    finally:
        engine.dispose()


def _trace(harness: QuickHarness, mission_id: str) -> MissionTrace:
    with harness.engine.connect() as connection:
        raw: str = connection.execute(
            text("SELECT trace FROM mission_traces WHERE mission_id = :m"),
            {"m": mission_id},
        ).scalar_one()
    return MissionTrace.model_validate(json.loads(raw))


def test_always_grant_once_then_no_prompt_ever(quick: QuickHarness) -> None:
    opened: list[str] = []
    sink = Sink()

    async def first() -> tuple[str, str, str]:
        service = quick.quick_service(opened, sink)
        await service.start()
        conversation = await service.create_conversation()
        waiting_id = await service.submit(conversation, "open youtube")
        waiting = await settled(service, waiting_id)
        assert waiting.status == MissionStatus.WAITING_PERMISSION
        assert waiting.path is None  # no triage ran
        assert waiting.permission_request is not None
        request = waiting.permission_request.request
        assert request.tool_id == OPEN_LINK_TOOL_ID and opened == []
        rerun_id = await service.approve(request.request_id, "persistent")
        rerun = await settled(service, rerun_id)
        assert rerun.status == MissionStatus.COMPLETED
        searched_id = await service.submit(
            conversation, "search youtube for lofi music"
        )
        searched = await settled(service, searched_id)
        assert searched.status == MissionStatus.COMPLETED
        answers = {
            m.message_id: m.text for m in await service.list_messages(conversation)
        }
        await service.stop()
        assert rerun.answer_message_id is not None
        assert searched.answer_message_id is not None
        return (
            answers[rerun.answer_message_id],
            answers[searched.answer_message_id],
            searched_id,
        )

    rerun_answer, searched_answer, searched_id = run(first())
    assert rerun_answer == "Opened YouTube."
    assert searched_answer == "Opened YouTube search for lofi music."
    assert opened == [YT, YT + "results?search_query=lofi+music"]
    assert quick.provider.roles == []  # no model call at all
    (call,) = _trace(quick, searched_id).quick_calls
    assert call.verified and call.decision_status == PermissionStatus.ALLOWED
    assert call.query_sha256 is not None and "lofi" not in call.model_dump_json()
    types = [event.event_type for event in sink.events]
    assert EventType.PERMISSION_REQUESTED in types
    assert types.count(EventType.TOOL_COMPLETED) == 2

    async def after_restart() -> MissionStatus:
        service = quick.quick_service(opened)
        await service.start()
        conversation = await service.create_conversation()
        record = await settled(
            service, await service.submit(conversation, "open google")
        )
        (grant,) = await service.list_grants()
        assert await service.revoke(grant.grant.grant_id)
        revoked = await settled(
            service, await service.submit(conversation, "open google")
        )
        await service.stop()
        assert revoked.status == MissionStatus.WAITING_PERMISSION
        return record.status

    assert run(after_restart()) == MissionStatus.COMPLETED
    assert opened[-1] == GOOGLE and len(opened) == 3


def test_launch_failure_fails_the_mission(quick: QuickHarness) -> None:
    async def scenario() -> MissionStatus:
        tools = ToolRegistry(open_link_tool_definitions())

        def broken(url: str) -> None:
            raise OSError

        service = quick.quick_service([])
        service._quick = QuickCommandRunner(OpenLinkTool(tools, launcher=broken))
        await service.start()
        conversation = await service.create_conversation()
        waiting = await settled(
            service, await service.submit(conversation, "open google")
        )
        assert waiting.permission_request is not None
        rerun = await settled(
            service,
            await service.approve(
                waiting.permission_request.request.request_id, "session"
            ),
        )
        await service.stop()
        assert rerun.failure_code == "tool_failed"
        return rerun.status

    assert run(scenario()) == MissionStatus.FAILED


def test_non_quick_request_takes_the_normal_pipeline(quick: QuickHarness) -> None:
    opened: list[str] = []

    async def scenario() -> MissionStatus:
        service = quick.quick_service(opened)
        await service.start()
        conversation = await service.create_conversation()
        record = await settled(
            service, await service.submit(conversation, "What is the codename?")
        )
        await service.stop()
        assert record.path is not None  # triaged as before
        return record.status

    run(scenario())
    assert opened == [] and quick.provider.roles  # the model answered


def test_voice_transcript_uses_the_same_router(quick: QuickHarness) -> None:
    opened: list[str] = []

    async def scenario() -> tuple[str, str]:
        service = quick.quick_service(opened)
        await service.start()
        conversation = await service.create_conversation()
        waiting = await settled(
            service, await service.submit(conversation, "open google")
        )
        assert waiting.permission_request is not None
        await settled(
            service,
            await service.approve(
                waiting.permission_request.request.request_id, "persistent"
            ),
        )
        responder = MissionVoiceResponder(service)
        draft = await responder.respond(
            VoiceResponseRequest(
                session_id=str(uuid4()),
                turn_id=str(uuid4()),
                transcript_text="Rise, Phoenix. Search YouTube for lofi music.",
            )
        )
        await service.stop()
        return draft.response_text, opened[-1]

    spoken, url = run(scenario())
    assert spoken == "Opened YouTube search for lofi music."
    assert url == YT + "results?search_query=lofi+music"
    assert quick.provider.roles == []


# ---- Ollama keep_alive (ADR-222)


def test_ollama_keep_alive_is_sent_only_when_set() -> None:
    request = GenerationRequest(
        model_id="m", messages=(ModelMessage(role=MessageRole.USER, text="Hi"),)
    )
    assert "keep_alive" not in _payload(request, stream=False, think=None)
    body = _payload(request, stream=False, think=False, keep_alive="30m")
    assert body["keep_alive"] == "30m"
    with pytest.raises(ValueError):
        OllamaProvider(keep_alive="forever")
    assert asyncio.iscoroutinefunction(OllamaProvider.aclose)
