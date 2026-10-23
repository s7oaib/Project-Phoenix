"""PHX-139 (ADR-261, ADR-262): Ollama auto-start and a faster video summary.

Nothing is launched, fetched or generated for real.
"""

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from phoenix.agents.answer_stream import PartialAnswers
from phoenix.agents.mission_events import EventPublisher
from phoenix.agents.quick_commands import (
    SUMMARY_INPUT_CHARS,
    SUMMARY_MAX_TOKENS,
    QuickCommandRunner,
    VideoSummarizer,
    match_quick_command,
    trim_transcript,
)
from phoenix.agents.registry import build_default_agent_registry
from phoenix.api import server
from phoenix.models.provider import GenerationChunk, GenerationRequest
from phoenix.security.prompt_injection import PromptInjectionGuard
from phoenix.tools.open_app import OpenAppTool, open_app_tool_definitions
from phoenix.tools.open_link import OpenLinkTool, open_link_tool_definitions
from phoenix.tools.registry import ToolRegistry
from phoenix.tools.youtube_transcript import (
    YouTubeTranscriptTool,
    youtube_transcript_tool_definitions,
)

VID = "dQw4w9WgXcQ"
WATCH = "https://www.youtube.com/watch?v=" + VID


def test_trim_keeps_head_and_tail() -> None:
    assert trim_transcript("short") == "short"
    text = "a" * 3000 + "b" * 3000
    trimmed = trim_transcript(text)
    assert trimmed.startswith("a" * 2500) and trimmed.endswith("b" * 1500)
    assert "[…]" in trimmed and len(trimmed) < SUMMARY_INPUT_CHARS + 10


class StreamProvider:
    provider_id = "ollama"

    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    async def stream(self, request: GenerationRequest) -> AsyncIterator[Any]:
        self.requests.append(request)
        ids: dict[str, Any] = {
            "operation_id": request.operation_id,
            "provider_id": "ollama",
            "model_id": request.model_id,
        }
        for piece in ("A short ", "summary."):
            yield GenerationChunk(**ids, text=piece)
        yield GenerationChunk(**ids, text="", is_final=True)


class Sink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def _runner(
    provider: StreamProvider, fetched: list[str], partials: PartialAnswers, sink: Sink
) -> QuickCommandRunner:
    tools = ToolRegistry(
        (
            *open_link_tool_definitions(),
            *open_app_tool_definitions(),
            *youtube_transcript_tool_definitions(),
        )
    )

    def fetch(video_id: str) -> str:
        fetched.append(video_id)
        return "x" * 10000

    return QuickCommandRunner(
        OpenLinkTool(tools, launcher=lambda url: None),
        OpenAppTool(tools),
        EventPublisher(sink),
        summarizer=VideoSummarizer(
            YouTubeTranscriptTool(tools, fetch=fetch),
            provider,  # type: ignore[arg-type]
            PromptInjectionGuard(build_default_agent_registry()),
            partials=partials,
        ),
    )


def test_streams_caps_and_caches() -> None:
    from tests.test_youtube_summary import _grant

    fetched: list[str] = []
    provider, partials, sink = StreamProvider(), PartialAnswers(), Sink()
    runner = _runner(provider, fetched, partials, sink)
    command = match_quick_command(f"summarize {WATCH}")
    assert command is not None
    mission = "00000000-0000-4000-8000-000000000001"
    for _ in range(2):
        done = asyncio.run(runner.run(mission, command, (_grant(),)))
        assert done.answer == "A short summary."
    assert fetched == [VID]  # the second read came from the cache
    assert partials.text(mission) == "A short summary."
    assert provider.requests[0].max_output_tokens == SUMMARY_MAX_TOKENS
    assert len(provider.requests[0].messages[1].text) < 10000
    deltas = [
        e.payload["answer_delta"]
        for e in sink.events
        if "answer_delta" in getattr(e, "payload", {})
    ]
    assert deltas[:2] == ["A short ", "summary."]


def test_launch_only_fixed_path_local_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launched: list[object] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(os, "startfile", launched.append, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv(server.AUTOSTART_ENV, raising=False)
    assert not server.launch_ollama_app(server.OLLAMA_LOCAL_URL)  # not installed
    app = tmp_path / "Programs" / "Ollama" / "ollama app.exe"
    app.parent.mkdir(parents=True)
    app.write_bytes(b"")
    assert not server.launch_ollama_app("http://10.0.0.2:11434")
    assert server.launch_ollama_app(server.OLLAMA_LOCAL_URL + "/")
    assert launched == [app]
    monkeypatch.setenv(server.AUTOSTART_ENV, "0")
    assert not server.launch_ollama_app(server.OLLAMA_LOCAL_URL)
    assert launched == [app]


def test_warm_when_up_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    results = iter((False, False, True))
    calls: list[int] = []

    async def warm() -> bool:
        calls.append(1)
        return next(results)

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    assert asyncio.run(server.warm_when_up(warm, 5)) and len(calls) == 3
    assert not asyncio.run(server.warm_when_up(lambda: _false(), 1))


async def _false() -> bool:
    return False
