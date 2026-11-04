"""PHX-140 (ADR-264 to ADR-268): conversation memory, saved memories in chat,
the research rule and the follow-up quick commands."""

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from phoenix.agents.chat_context import (
    MAX_HISTORY_CHARS,
    ChatContext,
    chat_prompt,
    history_text,
    recall_memories,
)
from phoenix.agents.quick_commands import (
    YOUTUBE_WATCH,
    QuickKind,
    match_quick_command,
)
from phoenix.agents.registry import build_default_agent_registry
from phoenix.agents.triage import rule_decides_direct, rule_wants_research
from phoenix.api.missions import MissionService
from phoenix.security.prompt_injection import PromptInjectionGuard


def test_history_keeps_the_last_six_turns_newest_first_under_the_cap() -> None:
    messages = [("user", f"q{i}") for i in range(20)]
    text = history_text(messages)
    assert text.splitlines() == [f"User: q{i}" for i in range(8, 20)]
    long = [("user", "old " * 2000), ("phoenix", "It was made by Ada.")]
    assert history_text(long) == "PHOENIX: It was made by Ada."
    assert len(history_text([("phoenix", "x" * 9000)])) == MAX_HISTORY_CHARS
    assert history_text([]) == ""


@dataclass(frozen=True)
class _Hit:
    kind: str
    text: str


class _Knowledge:
    async def search(self, query: str, limit: int) -> tuple[_Hit, ...]:
        return (
            _Hit("document_chunk", "a chunk"),
            *(_Hit("memory", f"m{i}") for i in range(5)),
        )


def test_recall_keeps_only_the_top_three_memories() -> None:
    found = asyncio.run(recall_memories(_Knowledge(), "who am i"))
    assert found == ("m0", "m1", "m2")


def _guard() -> PromptInjectionGuard:
    return PromptInjectionGuard(build_default_agent_registry())


def test_context_reaches_the_model_only_as_guarded_data() -> None:
    async def source() -> ChatContext:
        return ChatContext("User: who made python?\nPHOENIX: Guido.", ("I like tea",))

    prompt = asyncio.run(chat_prompt(_guard(), "who made it?", source))
    assert prompt.startswith("PHOENIX SECURITY POLICY")
    assert "recent_turns" in prompt and "saved_memory" in prompt
    assert "who made it?" in prompt


def test_no_context_or_a_failure_leaves_the_request_unchanged() -> None:
    async def empty() -> ChatContext:
        return ChatContext()

    async def broken() -> ChatContext:
        raise RuntimeError("store down")

    guard = _guard()
    for source in (None, empty, broken):
        assert asyncio.run(chat_prompt(guard, "hello", source)) == "hello"


@pytest.mark.parametrize(
    "text",
    [
        "what's the latest news",
        "weather in London",
        "who won the match yesterday",
        "bitcoin price",
        "cricket score",
        "best phones of 2026",
    ],
)
def test_current_questions_go_to_research(text: str) -> None:
    assert rule_wants_research(text, year=2026)
    assert not rule_decides_direct(text)


@pytest.mark.parametrize(
    "text",
    [
        "what is python",
        "who made it?",
        "summarise my notes from today",
        "what time is it today",
        "what happened in 1969",
        "open the news",
        "",
    ],
)
def test_other_requests_are_left_to_triage(text: str) -> None:
    assert not rule_wants_research(text, year=2026)


@pytest.mark.parametrize(
    "text", ["play another one", "next song", "Play the next song", "another song"]
)
def test_next_song_plays_music_again(text: str) -> None:
    command = match_quick_command(text)
    assert command is not None and command.kind == QuickKind.PLAY_MUSIC


def test_summarise_that_video_uses_the_conversations_last_video() -> None:
    command = match_quick_command("summarise that video")
    assert command is not None
    assert (command.kind, command.url, command.query) == (
        QuickKind.SUMMARIZE_YOUTUBE,
        None,
        None,
    )
    service = SimpleNamespace(_videos={"c1": "dQw4w9WgXcQ"})
    found = MissionService._last_video(service, command, "c1")  # type: ignore[arg-type]
    assert found is not None and found.url == YOUTUBE_WATCH + "dQw4w9WgXcQ"
    # Without a remembered video the normal pipeline answers.
    assert MissionService._last_video(service, command, "c2") is None  # type: ignore[arg-type]
    assert match_quick_command("summarise it") is None
