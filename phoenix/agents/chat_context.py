"""PHX-140 (ADR-264, ADR-265): what a DIRECT or KAIDO reply may see.

The last turns of the same conversation (user and PHOENIX message text,
newest kept) and the user's best saved memories reach the model as plain
conversation context (owner decision 2026-10-23). With nothing to add the
request text is unchanged. Any failure leaves the context out.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from phoenix.security.prompt_injection import (
    PromptInjectionGuard,
)

MAX_TURNS = 6
MAX_HISTORY_CHARS = 3000
MAX_MEMORIES = 3
_SEARCH_LIMIT = 10  # memories are filtered from mixed memory/document hits
_LABELS = {"user": "User", "phoenix": "PHOENIX"}


@dataclass(frozen=True)
class ChatContext:
    history: str = ""
    memories: tuple[str, ...] = ()


ContextSource = Callable[[], Awaitable[ChatContext]]


class _Hit(Protocol):
    @property
    def kind(self) -> str: ...
    @property
    def text(self) -> str: ...


class MemorySearch(Protocol):
    async def search(self, query: str, limit: int) -> Sequence[_Hit]: ...


def history_text(messages: Sequence[tuple[str, str]]) -> str:
    """(role, text) oldest first, before the current request: the last
    MAX_TURNS turns as labelled lines, capped at MAX_HISTORY_CHARS, newest
    kept."""
    lines = [
        f"{_LABELS.get(role, 'User')}: {' '.join(text.split())}"
        for role, text in messages[-2 * MAX_TURNS :]
    ]
    kept: list[str] = []
    size = 0
    for line in reversed(lines):
        if size + len(line) > MAX_HISTORY_CHARS:
            if not kept:
                kept.append(line[:MAX_HISTORY_CHARS])
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(reversed(kept))


async def recall_memories(knowledge: MemorySearch, text: str) -> tuple[str, ...]:
    """PHX-110 internal retrieval (P0): the top saved memories above the
    store's similarity floor; nothing when nothing is indexed."""
    hits = await knowledge.search(text, _SEARCH_LIMIT)
    return tuple(hit.text for hit in hits if hit.kind == "memory")[:MAX_MEMORIES]


async def chat_prompt(
    guard: PromptInjectionGuard, text: str, source: ContextSource | None
) -> str:
    """The request with its context as plain chat history, or text unchanged.

    Owner decision 2026-10-23 (ADR-264 amended): the earlier turns are the
    user's own messages and PHOENIX's own answers, and saved memories are
    written only by the user, so they go in as ordinary conversation context.
    The prompt-injection wrapper made the small model refuse follow-ups. Web,
    file and screen content keep their guarded paths elsewhere.
    """
    if source is None:
        return text
    try:
        context = await source()
        parts = []
        if context.history:
            parts.append("Earlier in this conversation:\n" + context.history)
        memories = context.memories[:MAX_MEMORIES]
        if memories:
            parts.append(
                "Notes the user saved:\n" + "\n".join(f"- {m}" for m in memories)
            )
        if not parts:
            return text
        return "\n\n".join([*parts, "Current message:\n" + text])
    except Exception:
        return text
