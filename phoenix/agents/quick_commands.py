"""PHX-130 quick commands: deterministic link opening before triage (ADR-220..222).

A small closed set of requests is recognised by strict patterns, with no model
call, and opens one link in the user's default browser through ToolRegistry's
``desktop.open_link`` and PermissionEngine (INV-004):

- "open (the/my/a) (web) browser" -> https://www.google.com/
- "open youtube" / "open google"
- "search youtube for X", "search for X on youtube", "play X on youtube"
- "search google for X", "search for X on google", "google X"

PHX-133 (ADR-235..239) adds:

- "open wikipedia", "search wikipedia for X", "open github", "open gmail",
  "open (google) maps", "search maps for X", "directions to X",
  "open google news" (same tool, four more allowlisted hosts);
- "what time is it", "what's the date", "what day is it": answered from the
  local clock with no tool and no model (``QuickAnswerRecord``);
- "open notepad", "open calculator", "open file explorer" / "open files":
  ``desktop.open_app`` (P1, its own fixed scope) launches the app by its fixed
  system path with no arguments.

Polite prefixes ("please", "can you", "Phoenix,", "rise, phoenix", ...) and a
trailing "please" or punctuation are ignored. Anything else, including a query
holding a URL scheme, "www." or shell punctuation, is not a quick command and
takes the normal pipeline unchanged. URLs are built only from the templates
below; the query is length-capped and URL-encoded, so no text can change the
host (no arbitrary URL opening). App commands carry only an ``AppName``.

Permission: one fixed scope per tool (actor, action, resource naming the
allowlist, and reason), so the user's "Always allow" (a P1 PERSISTENT grant)
covers every link command, and a second one every app command; both are
revocable like any grant. No grant: the mission waits for the user's
decision. LEVI's check is deterministic (ADR-221): the answer is reported only
when the call was ALLOWED, attempted and succeeded.
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
from phoenix.tools.open_app import (
    OPEN_APP_TOOL_ID,
    AppName,
    OpenAppResult,
    OpenAppTool,
)
from phoenix.tools.open_link import (
    ALLOWED_HOSTS,
    OPEN_LINK_TOOL_ID,
    OpenLinkResult,
    OpenLinkTool,
)
from phoenix.tools.registry import ToolResultStatus, ToolRiskLevel

QUICK_ACTOR_ID = "phoenix"
QUICK_ACTION = "open_link"
QUICK_RESOURCE = "allowlist:" + ",".join(sorted(ALLOWED_HOSTS))
QUICK_REASON = "PHOENIX quick command: open an allowlisted link in the default browser"
# PHX-133 (ADR-236): the app scope, one grant for the whole app list.
QUICK_APP_ACTION = "open_app"
QUICK_APP_RESOURCE = "allowlist:" + ",".join(sorted(name.value for name in AppName))
QUICK_APP_REASON = "PHOENIX quick command: open an allowlisted app"
# tool_id -> (action, resource, reason): the only quick-command scopes.
QUICK_SCOPES: dict[str, tuple[str, str, str]] = {
    OPEN_LINK_TOOL_ID: (QUICK_ACTION, QUICK_RESOURCE, QUICK_REASON),
    OPEN_APP_TOOL_ID: (QUICK_APP_ACTION, QUICK_APP_RESOURCE, QUICK_APP_REASON),
}
QUICK_STEP_ID = "s1"
MAX_QUERY_CHARS = 200
MAX_TEXT_CHARS = 300

GOOGLE_HOME = "https://www.google.com/"
YOUTUBE_HOME = "https://www.youtube.com/"
_YOUTUBE_SEARCH = "https://www.youtube.com/results?search_query="
_GOOGLE_SEARCH = "https://www.google.com/search?q="
# PHX-133 (ADR-235)
WIKIPEDIA_HOME = "https://en.wikipedia.org/"
GITHUB_HOME = "https://github.com/"
GMAIL_HOME = "https://mail.google.com/"
MAPS_HOME = "https://www.google.com/maps/"
NEWS_HOME = "https://news.google.com/"
_WIKIPEDIA_SEARCH = "https://en.wikipedia.org/wiki/Special:Search?search="
_MAPS_SEARCH = "https://www.google.com/maps/search/"
_WEEKDAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class QuickKind(StrEnum):
    OPEN_BROWSER = "open_browser"
    OPEN_YOUTUBE = "open_youtube"
    OPEN_GOOGLE = "open_google"
    SEARCH_YOUTUBE = "search_youtube"
    SEARCH_GOOGLE = "search_google"
    # PHX-133
    OPEN_WIKIPEDIA = "open_wikipedia"
    SEARCH_WIKIPEDIA = "search_wikipedia"
    OPEN_GITHUB = "open_github"
    OPEN_GMAIL = "open_gmail"
    OPEN_MAPS = "open_maps"
    SEARCH_MAPS = "search_maps"
    OPEN_NEWS = "open_news"
    TELL_TIME = "tell_time"
    TELL_DATE = "tell_date"
    OPEN_NOTEPAD = "open_notepad"
    OPEN_CALCULATOR = "open_calculator"
    OPEN_FILE_EXPLORER = "open_file_explorer"


# PHX-133: local answers (no tool) and app launches (desktop.open_app).
LOCAL_KINDS = frozenset({QuickKind.TELL_TIME, QuickKind.TELL_DATE})
APP_KINDS: dict[QuickKind, AppName] = {
    QuickKind.OPEN_NOTEPAD: AppName.NOTEPAD,
    QuickKind.OPEN_CALCULATOR: AppName.CALCULATOR,
    QuickKind.OPEN_FILE_EXPLORER: AppName.FILE_EXPLORER,
}


@dataclass(frozen=True)
class QuickCommand:
    kind: QuickKind
    url: str | None  # None for a local answer or an app (PHX-133)
    answer: str
    query: str | None = None

    @property
    def app(self) -> AppName | None:
        return APP_KINDS.get(self.kind)


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
    # PHX-133
    (re.compile(rf"{_OPEN}\s+wikipedia", re.IGNORECASE), QuickKind.OPEN_WIKIPEDIA),
    (re.compile(rf"{_OPEN}\s+github", re.IGNORECASE), QuickKind.OPEN_GITHUB),
    (re.compile(rf"{_OPEN}\s+g-?mail", re.IGNORECASE), QuickKind.OPEN_GMAIL),
    (
        re.compile(rf"{_OPEN}\s+(?:google\s+)?maps", re.IGNORECASE),
        QuickKind.OPEN_MAPS,
    ),
    (re.compile(rf"{_OPEN}\s+google\s+news", re.IGNORECASE), QuickKind.OPEN_NEWS),
    (
        re.compile(
            r"what\s+time\s+is\s+it|what(?:'s|\s+is)\s+the\s+(?:current\s+)?time"
            r"|tell\s+me\s+the\s+time",
            re.IGNORECASE,
        ),
        QuickKind.TELL_TIME,
    ),
    (
        re.compile(
            r"what(?:'s|\s+is)\s+(?:the\s+date|today's\s+date|today)"
            r"|what\s+(?:day|date)\s+is\s+(?:it|today)|tell\s+me\s+the\s+date",
            re.IGNORECASE,
        ),
        QuickKind.TELL_DATE,
    ),
    (
        re.compile(rf"{_OPEN}\s+(?:the\s+)?notepad", re.IGNORECASE),
        QuickKind.OPEN_NOTEPAD,
    ),
    (
        re.compile(rf"{_OPEN}\s+(?:the\s+)?calculator", re.IGNORECASE),
        QuickKind.OPEN_CALCULATOR,
    ),
    (
        re.compile(
            rf"{_OPEN}\s+(?:the\s+)?(?:file\s+explorer|(?:my\s+)?files)",
            re.IGNORECASE,
        ),
        QuickKind.OPEN_FILE_EXPLORER,
    ),
)
_EXACT_TARGETS: dict[QuickKind, tuple[str | None, str]] = {
    QuickKind.OPEN_BROWSER: (GOOGLE_HOME, "Opened your browser."),
    QuickKind.OPEN_YOUTUBE: (YOUTUBE_HOME, "Opened YouTube."),
    QuickKind.OPEN_GOOGLE: (GOOGLE_HOME, "Opened Google."),
    QuickKind.OPEN_WIKIPEDIA: (WIKIPEDIA_HOME, "Opened Wikipedia."),
    QuickKind.OPEN_GITHUB: (GITHUB_HOME, "Opened GitHub."),
    QuickKind.OPEN_GMAIL: (GMAIL_HOME, "Opened Gmail."),
    QuickKind.OPEN_MAPS: (MAPS_HOME, "Opened Google Maps."),
    QuickKind.OPEN_NEWS: (NEWS_HOME, "Opened Google News."),
    QuickKind.OPEN_NOTEPAD: (None, "Opened Notepad."),
    QuickKind.OPEN_CALCULATOR: (None, "Opened Calculator."),
    QuickKind.OPEN_FILE_EXPLORER: (None, "Opened File Explorer."),
}
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
) + tuple(
    # PHX-133: Wikipedia and Google Maps searches.
    (re.compile(pattern, re.IGNORECASE), kind)
    for kind, site in (
        (QuickKind.SEARCH_WIKIPEDIA, "wikipedia"),
        (QuickKind.SEARCH_MAPS, r"(?:google\s+)?maps"),
    )
    for pattern in (
        rf"search\s+(?:on\s+)?{site}\s+for\s+{_Q}",
        rf"search\s+for\s+{_Q}\s+on\s+{site}",
        rf"(?:get\s+|show\s+me\s+)?directions\s+to\s+{_Q}"
        if kind == QuickKind.SEARCH_MAPS
        else rf"look\s+up\s+{_Q}\s+on\s+{site}",
    )
)
_SEARCH_TARGETS: dict[QuickKind, tuple[str, str]] = {
    QuickKind.SEARCH_YOUTUBE: (_YOUTUBE_SEARCH, "YouTube search"),
    QuickKind.SEARCH_GOOGLE: (_GOOGLE_SEARCH, "Google search"),
    QuickKind.SEARCH_WIKIPEDIA: (_WIKIPEDIA_SEARCH, "Wikipedia search"),
    QuickKind.SEARCH_MAPS: (_MAPS_SEARCH, "Google Maps search"),
}
# A query is plain search text: no link, no shell or markup punctuation.
_UNSAFE_QUERY = re.compile(r"://|\bwww\.|[;|&<>`$\\{}\[\]]|[\x00-\x1f\x7f]")


def _clock_answer(kind: QuickKind, now: datetime) -> str:
    """PHX-133 (ADR-237): the local time or date, fixed English wording."""
    if kind == QuickKind.TELL_TIME:
        hour = now.hour % 12 or 12
        half = "AM" if now.hour < 12 else "PM"
        return f"It's {hour}:{now.minute:02d} {half}."
    weekday = _WEEKDAYS[now.weekday()]
    return f"Today is {weekday}, {now.day} {_MONTHS[now.month - 1]} {now.year}."


def match_quick_command(
    text: str, *, now: datetime | None = None
) -> QuickCommand | None:
    """The quick command text asks for, or None (then the normal pipeline).

    ``now`` (tests) replaces the local clock for the time and date answers.
    """
    if type(text) is not str or len(text) > MAX_TEXT_CHARS:
        return None
    body = " ".join(text.replace("’", "'").split())
    body = _PREFIX.sub("", body, count=1)
    body = _SUFFIX.sub("", body, count=1).strip()
    if not body:
        return None
    for pattern, kind in _EXACT:
        if pattern.fullmatch(body):
            if kind in LOCAL_KINDS:
                local = datetime.now().astimezone() if now is None else now
                return QuickCommand(kind, None, _clock_answer(kind, local))
            url, answer = _EXACT_TARGETS[kind]
            return QuickCommand(kind, url, answer)
    for pattern, kind in _SEARCHES:
        found = pattern.fullmatch(body)
        if found is None:
            continue
        query = found.group("q").strip(" \"'")
        if not query or len(query) > MAX_QUERY_CHARS or _UNSAFE_QUERY.search(query):
            return None
        base, site = _SEARCH_TARGETS[kind]
        return QuickCommand(
            kind,
            base + quote_plus(query, safe=""),
            f"Opened {site} for {query}.",
            query,
        )
    return None


def quick_grants(grants: object) -> tuple[PermissionGrant, ...]:
    """Caller grants for quick commands: one of the fixed P1 scopes (links,
    PHX-133 apps), SESSION or PERSISTENT; anything else raises ValueError."""
    if type(grants) not in (tuple, list):
        raise ValueError("Grants require an ordered sequence")
    accepted: list[PermissionGrant] = []
    for grant in cast(Sequence[object], grants):
        if type(grant) is not PermissionGrant:
            raise ValueError("Expected PermissionGrant instances")
        grant = PermissionGrant.model_validate(grant)
        if (
            QUICK_SCOPES.get(grant.tool_id)
            != (grant.action, grant.resource, grant.reason)
            or grant.actor_id != QUICK_ACTOR_ID
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
    tool_id: str = OPEN_LINK_TOOL_ID  # or (PHX-133) desktop.open_app
    kind: QuickKind
    decision_status: PermissionStatus
    matched_grant_id: str | None = None
    tool_status: ToolResultStatus
    attempted: bool = Field(strict=True)
    # LEVI's deterministic check (ADR-221): ALLOWED, attempted and success.
    verified: bool = Field(strict=True)
    query_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    duration_ms: int = Field(ge=0)


class QuickAnswerRecord(BaseModel):
    """PHX-133 (ADR-237): a local-clock answer; no tool, no model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: QuickKind


@dataclass(frozen=True)
class QuickOutcome:
    record: QuickCallRecord | QuickAnswerRecord
    answer: str | None  # set only when verified
    pending: PermissionRequest | None  # set only when approval is required


class QuickCommandRunner:
    """Runs one matched quick command through ``desktop.open_link``, (PHX-133)
    ``desktop.open_app``, or the local clock."""

    def __init__(
        self,
        tool: OpenLinkTool,
        apps: OpenAppTool,
        events: EventPublisher | None = None,
    ):
        if type(tool) is not OpenLinkTool:
            raise TypeError("Expected an OpenLinkTool")
        if type(apps) is not OpenAppTool:
            raise TypeError("Expected an OpenAppTool")
        self._tool = tool
        self._apps = apps
        self._events = events if events is not None else EventPublisher(None)

    async def run(
        self,
        mission_id: str,
        command: QuickCommand,
        grants: Sequence[PermissionGrant],
        *,
        session_id: str | None = None,
    ) -> QuickOutcome:
        if command.kind in LOCAL_KINDS:  # PHX-133: no tool, nothing to verify
            answer = QuickAnswerRecord(kind=command.kind)
            return QuickOutcome(answer, command.answer, None)
        started = time.monotonic()
        events = MissionEvents(self._events, mission_id)
        call_id = str(uuid4())
        app = command.app
        tool_id = OPEN_LINK_TOOL_ID if app is None else OPEN_APP_TOOL_ID
        action, resource, reason = QUICK_SCOPES[tool_id]
        request = PermissionRequest(
            request_id=str(uuid4()),
            actor_id=QUICK_ACTOR_ID,
            tool_id=tool_id,
            action=action,
            resource=resource,
            reason=reason,
            requested_at=datetime.now(UTC),
            task_id=mission_id,
            session_id=session_id,
        )
        tool = {"step_id": QUICK_STEP_ID, "tool_id": tool_id}
        await events.emit(
            EventType.TOOL_REQUESTED, **tool, call_id=call_id, operation=action
        )
        usable = usable_quick_grants(grants)
        result: OpenAppResult | OpenLinkResult
        if app is not None:
            result = await asyncio.to_thread(self._apps.open, app, request, usable)
        else:
            url = cast(str, command.url)
            result = await asyncio.to_thread(self._tool.open, url, request, usable)
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
            tool_id=tool_id,
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
