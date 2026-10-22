"""PHX-130 quick commands: deterministic link opening before triage (ADR-220..222).

A small closed set of requests is recognised by strict patterns, with no model
call, and opens one link in the user's default browser through ToolRegistry's
``desktop.open_link`` and PermissionEngine (INV-004):

- "open (the/my/a) (web) browser" -> https://www.google.com/
- "open youtube" / "open google"
- "search youtube for X", "search for X on youtube", "play X on youtube"
- "search google for X", "search for X on google", "google X"

Polite prefixes ("please", "can you", "Phoenix,", "rise, phoenix", ...) and a
trailing "please" or punctuation are ignored. Anything else, including a query
holding a URL scheme, "www." or shell punctuation, is not a quick command and
takes the normal pipeline unchanged. URLs are built only from the templates
below; the query is length-capped and URL-encoded, so no text can change the
host (no arbitrary URL opening).

Permission: one fixed scope (actor, action, resource naming the allowlist, and
reason), so the user's "Always allow" (a P1 PERSISTENT grant) covers every
quick command and is revocable like any grant. No grant: the mission waits for
the user's decision. LEVI's check is deterministic (ADR-221): the answer is
reported only when the call was ALLOWED, attempted and succeeded.
"""

import asyncio
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import cast
from urllib.parse import quote_plus
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from phoenix.agents.mission_events import EventPublisher, MissionEvents, elapsed_ms
from phoenix.core.events import EventType
from phoenix.security.permissions import (
    GrantDuration,
    PermissionGrant,
    PermissionRequest,
    PermissionStatus,
)
from phoenix.tools.open_link import ALLOWED_HOSTS, OPEN_LINK_TOOL_ID, OpenLinkTool
from phoenix.tools.registry import ToolResultStatus, ToolRiskLevel

QUICK_ACTOR_ID = "phoenix"
QUICK_ACTION = "open_link"
QUICK_RESOURCE = "allowlist:" + ",".join(sorted(ALLOWED_HOSTS))
QUICK_REASON = "PHOENIX quick command: open an allowlisted link in the default browser"
QUICK_STEP_ID = "s1"
MAX_QUERY_CHARS = 200
MAX_TEXT_CHARS = 300

GOOGLE_HOME = "https://www.google.com/"
YOUTUBE_HOME = "https://www.youtube.com/"
_YOUTUBE_SEARCH = "https://www.youtube.com/results?search_query="
_GOOGLE_SEARCH = "https://www.google.com/search?q="


class QuickKind(StrEnum):
    OPEN_BROWSER = "open_browser"
    OPEN_YOUTUBE = "open_youtube"
    OPEN_GOOGLE = "open_google"
    SEARCH_YOUTUBE = "search_youtube"
    SEARCH_GOOGLE = "search_google"


@dataclass(frozen=True)
class QuickCommand:
    kind: QuickKind
    url: str
    answer: str
    query: str | None = None


_PREFIX = re.compile(
    r"^(?:(?:hey|ok|okay|rise)[\s,]+)?(?:phoenix\b[\s,:]*)?"
    r"(?:(?:please|kindly|can you|could you|would you|will you|just)\b[\s,]*)*",
    re.IGNORECASE,
)
_SUFFIX = re.compile(r"(?:[\s,]+(?:please|for me|now))*[\s.!?]*$", re.IGNORECASE)
_OPEN = r"(?:open(?:\s+up)?|launch|start|go\s+to)"
_EXACT: tuple[tuple[re.Pattern[str], QuickKind], ...] = (
    (
        re.compile(
            rf"{_OPEN}\s+(?:(?:the|my|a)\s+)?(?:web\s+|internet\s+)?browser",
            re.IGNORECASE,
        ),
        QuickKind.OPEN_BROWSER,
    ),
    (re.compile(rf"{_OPEN}\s+youtube", re.IGNORECASE), QuickKind.OPEN_YOUTUBE),
    (re.compile(rf"{_OPEN}\s+google", re.IGNORECASE), QuickKind.OPEN_GOOGLE),
)
_Q = r"(?P<q>.+?)"
_SEARCHES: tuple[tuple[re.Pattern[str], QuickKind], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), kind)
    for kind, site in (
        (QuickKind.SEARCH_YOUTUBE, "youtube"),
        (QuickKind.SEARCH_GOOGLE, "google"),
    )
    for pattern in (
        rf"search\s+(?:on\s+)?{site}\s+for\s+{_Q}",
        rf"search\s+for\s+{_Q}\s+on\s+{site}",
        rf"(?:play|find|watch)\s+{_Q}\s+on\s+{site}"
        if site == "youtube"
        else rf"{site}\s+{_Q}",
    )
)
# A query is plain search text: no link, no shell or markup punctuation.
_UNSAFE_QUERY = re.compile(r"://|\bwww\.|[;|&<>`$\\{}\[\]]|[\x00-\x1f\x7f]")


def match_quick_command(text: str) -> QuickCommand | None:
    """The quick command text asks for, or None (then the normal pipeline)."""
    if type(text) is not str or len(text) > MAX_TEXT_CHARS:
        return None
    body = " ".join(text.split())
    body = _PREFIX.sub("", body, count=1)
    body = _SUFFIX.sub("", body, count=1).strip()
    if not body:
        return None
    for pattern, kind in _EXACT:
        if pattern.fullmatch(body):
            url, answer = {
                QuickKind.OPEN_BROWSER: (GOOGLE_HOME, "Opened your browser."),
                QuickKind.OPEN_YOUTUBE: (YOUTUBE_HOME, "Opened YouTube."),
                QuickKind.OPEN_GOOGLE: (GOOGLE_HOME, "Opened Google."),
            }[kind]
            return QuickCommand(kind, url, answer)
    for pattern, kind in _SEARCHES:
        found = pattern.fullmatch(body)
        if found is None:
            continue
        query = found.group("q").strip(" \"'")
        if not query or len(query) > MAX_QUERY_CHARS or _UNSAFE_QUERY.search(query):
            return None
        youtube = kind == QuickKind.SEARCH_YOUTUBE
        base = _YOUTUBE_SEARCH if youtube else _GOOGLE_SEARCH
        site = "YouTube" if youtube else "Google"
        return QuickCommand(
            kind,
            base + quote_plus(query, safe=""),
            f"Opened {site} search for {query}.",
            query,
        )
    return None


def quick_grants(grants: object) -> tuple[PermissionGrant, ...]:
    """Caller grants for quick commands: the one fixed P1 scope, SESSION or
    PERSISTENT; anything else raises ValueError."""
    if type(grants) not in (tuple, list):
        raise ValueError("Grants require an ordered sequence")
    accepted: list[PermissionGrant] = []
    for grant in cast(Sequence[object], grants):
        if type(grant) is not PermissionGrant:
            raise ValueError("Expected PermissionGrant instances")
        grant = PermissionGrant.model_validate(grant)
        if (
            grant.tool_id != OPEN_LINK_TOOL_ID
            or grant.actor_id != QUICK_ACTOR_ID
            or grant.action != QUICK_ACTION
            or grant.resource != QUICK_RESOURCE
            or grant.reason != QUICK_REASON
            or grant.risk_level != ToolRiskLevel.P1
            or grant.duration not in (GrantDuration.SESSION, GrantDuration.PERSISTENT)
        ):
            raise ValueError("Grant is outside the quick command scope")
        accepted.append(grant)
    if len({grant.grant_id for grant in accepted}) != len(accepted):
        raise ValueError("Duplicate grant ID")
    return tuple(accepted)


def usable_quick_grants(
    grants: Sequence[PermissionGrant],
) -> tuple[PermissionGrant, ...]:
    """The stored grants that are quick-command grants; the rest are ignored."""
    usable: list[PermissionGrant] = []
    for grant in grants:
        try:
            usable.extend(quick_grants([*usable, grant])[len(usable) :])
        except ValueError:
            continue
    return tuple(usable)


class QuickCallRecord(BaseModel):
    """Trace evidence of one quick command: IDs, closed values and a hash only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    call_id: str
    request_id: str
    tool_id: str = OPEN_LINK_TOOL_ID
    kind: QuickKind
    decision_status: PermissionStatus
    matched_grant_id: str | None = None
    tool_status: ToolResultStatus
    attempted: bool = Field(strict=True)
    # LEVI's deterministic check (ADR-221): ALLOWED, attempted and success.
    verified: bool = Field(strict=True)
    query_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    duration_ms: int = Field(ge=0)


@dataclass(frozen=True)
class QuickOutcome:
    record: QuickCallRecord
    answer: str | None  # set only when verified
    pending: PermissionRequest | None  # set only when approval is required


class QuickCommandRunner:
    """Runs one matched quick command through ``desktop.open_link``."""

    def __init__(self, tool: OpenLinkTool, events: EventPublisher | None = None):
        if type(tool) is not OpenLinkTool:
            raise TypeError("Expected an OpenLinkTool")
        self._tool = tool
        self._events = events if events is not None else EventPublisher(None)

    async def run(
        self,
        mission_id: str,
        command: QuickCommand,
        grants: Sequence[PermissionGrant],
        *,
        session_id: str | None = None,
    ) -> QuickOutcome:
        started = time.monotonic()
        events = MissionEvents(self._events, mission_id)
        call_id = str(uuid4())
        request = PermissionRequest(
            request_id=str(uuid4()),
            actor_id=QUICK_ACTOR_ID,
            tool_id=OPEN_LINK_TOOL_ID,
            action=QUICK_ACTION,
            resource=QUICK_RESOURCE,
            reason=QUICK_REASON,
            requested_at=datetime.now(UTC),
            task_id=mission_id,
            session_id=session_id,
        )
        tool = {"step_id": QUICK_STEP_ID, "tool_id": OPEN_LINK_TOOL_ID}
        await events.emit(
            EventType.TOOL_REQUESTED, **tool, call_id=call_id, operation=QUICK_ACTION
        )
        result = await asyncio.to_thread(
            self._tool.open, command.url, request, usable_quick_grants(grants)
        )
        decision = result.decision.status
        verified = (
            decision == PermissionStatus.ALLOWED
            and result.attempted
            and result.status == ToolResultStatus.SUCCESS
        )
        duration = elapsed_ms(started)
        record = QuickCallRecord(
            call_id=call_id,
            request_id=request.request_id,
            kind=command.kind,
            decision_status=decision,
            matched_grant_id=result.decision.matched_grant_id,
            tool_status=result.status,
            attempted=result.attempted,
            verified=verified,
            query_sha256=None
            if command.query is None
            else sha256(command.query.encode("utf-8")).hexdigest(),
            duration_ms=duration,
        )
        if decision == PermissionStatus.APPROVAL_REQUIRED:
            await events.emit(
                EventType.PERMISSION_REQUESTED,
                **tool,
                call_id=call_id,
                permission_request_id=request.request_id,
                risk_level=ToolRiskLevel.P1.value,
            )
            return QuickOutcome(record, None, request)
        if verified:
            await events.emit(
                EventType.TOOL_COMPLETED,
                **tool,
                call_id=call_id,
                tool_status=result.status.value,
                duration_ms=duration,
            )
            return QuickOutcome(record, command.answer, None)
        await events.emit(
            EventType.TOOL_FAILED,
            **tool,
            call_id=call_id,
            code="tool_failed",
            decision=decision.value,
            tool_status=result.status.value,
            duration_ms=duration,
        )
        return QuickOutcome(record, None, None)
