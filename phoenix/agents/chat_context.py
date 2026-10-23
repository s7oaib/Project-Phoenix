"""PHX-140 (ADR-264, ADR-265): what a DIRECT or KAIDO reply may see.

The last turns of the same conversation (user and PHOENIX message text,
newest kept) and the user's best saved memories reach the model only as
untrusted guarded data, never as instructions. With nothing to add the
request text is unchanged. Any failure leaves the context out.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from phoenix.agents.tool_steps import _guarded
from phoenix.security.prompt_injection import (
    PromptInjectionGuard,
    UntrustedContentOrigin,
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
    """The request with its context as guarded evidence, or text unchanged."""
    if source is None:
        return text
    try:
        context = await source()
        items = []
        if context.history:
            items.append(
                (
                    "conversation",
                    UntrustedContentOrigin.MODEL_OUTPUT,
                    "conversation",
                    "recent_turns",
                    context.history,
                )
            )
        items.extend(
            (
                f"memory{index}",
                UntrustedContentOrigin.EXTERNAL_TEXT,
                "memory",
                "saved_memory",
                memory,
            )
            for index, memory in enumerate(context.memories[:MAX_MEMORIES], 1)
        )
        if not items:
            return text
        return _guarded(guard, str(uuid4()), text, items)
    except Exception:
        return text
