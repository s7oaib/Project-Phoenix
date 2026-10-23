"""PHX-138 ``web.youtube_transcript``: read one public YouTube video's captions.

P1 network read of youtube.com (ADR-258). The video is named only by an
11-character ID taken from a youtube.com / youtu.be URL; PermissionEngine
decides every call under one fixed scope, and the URL is resolved (e.g. a
search) only once ALLOWED. yt-dlp is used as a library to read subtitle or
auto-caption metadata only: no download, no cookies, no login, 15 s socket
timeout; English is preferred, json3 or vtt. Timing is stripped and the text
capped. The text is untrusted data; callers only summarise it (ADR-259).
"""

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlsplit

from phoenix.security.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionGrant,
    PermissionPolicy,
    PermissionRequest,
    PermissionStatus,
)
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResultStatus,
    ToolRiskLevel,
)

YOUTUBE_TRANSCRIPT_TOOL_ID = "web.youtube_transcript"
TRANSCRIPT_ACTION = "read_transcript"
TRANSCRIPT_RESOURCE = "youtube:public-video-captions"
CAPTION_TIMEOUT = 15.0
MAX_TRANSCRIPT_CHARS = 12000
MAX_CAPTION_BYTES = 2_000_000
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com"})
_TAG = re.compile(r"<[^>]*>")
CaptionFetcher = Callable[[str], str | None]


def youtube_video_id(url: str) -> str | None:
    """The video ID of a youtube.com watch/shorts or youtu.be URL, else None."""
    if type(url) is not str or len(url) > 300:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or parts.port or parts.username:
        return None
    host = (parts.hostname or "").lower()
    path = parts.path.split("/")
    if host == "youtu.be":
        found = path[1] if len(path) == 2 else None
    elif host in _YOUTUBE_HOSTS and parts.path == "/watch":
        found = parse_qs(parts.query).get("v", [""])[0]
    elif host in _YOUTUBE_HOSTS and len(path) == 3 and path[1] == "shorts":
        found = path[2]
    else:
        found = None
    return found if found is not None and _VIDEO_ID.fullmatch(found) else None


def _english(tracks: object) -> list[dict[str, Any]]:
    if not isinstance(tracks, dict):
        return []
    keys = [k for k in ("en", "en-orig") if k in tracks]
    keys += sorted(k for k in tracks if k.startswith("en") and k not in keys)
    return tracks[keys[0]] if keys and isinstance(tracks[keys[0]], list) else []


def caption_text(raw: str, ext: str) -> str:
    """Plain caption text: json3 segments or vtt cue lines, timing dropped."""
    if ext == "json3":
        events = json.loads(raw).get("events") or []
        pieces = [
            seg.get("utf8", "") for event in events for seg in (event.get("segs") or [])
        ]
    else:
        pieces = []
        for line in raw.splitlines():
            line = _TAG.sub("", line).strip()
            if not line or "-->" in line or line.isdigit() or line == "WEBVTT":
                continue
            if line.startswith(("Kind:", "Language:", "NOTE")):
                continue
            if not pieces or pieces[-1] != line:  # auto captions repeat lines
                pieces.append(line)
    return " ".join(" ".join(pieces).split())[:MAX_TRANSCRIPT_CHARS]


def fetch_captions(video_id: str) -> str | None:
    """English captions of one video as plain text; None when it has none."""
    import yt_dlp  # type: ignore[import-untyped]  # lazy: only after ALLOWED

    options = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": CAPTION_TIMEOUT,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(
            "https://www.youtube.com/watch?v=" + video_id, download=False
        )
        tracks = _english(info.get("subtitles")) or _english(
            info.get("automatic_captions")
        )
        track = next((t for t in tracks if t.get("ext") == "json3"), None) or next(
            (t for t in tracks if t.get("ext") == "vtt"), None
        )
        if track is None:
            return None
        host = (urlsplit(str(track.get("url"))).hostname or "").lower()
        if not (host == "youtube.com" or host.endswith(".youtube.com")):
            raise ValueError("Caption URL is not on youtube.com")
        with ydl.urlopen(track["url"]) as response:
            raw = response.read(MAX_CAPTION_BYTES).decode("utf-8", "replace")
    return caption_text(raw, str(track["ext"])) or None


def youtube_transcript_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            tool_id=YOUTUBE_TRANSCRIPT_TOOL_ID,
            description=(
                "Read the English captions of one public YouTube video as "
                "plain text (no download, no login); untrusted data."
            ),
            input_schema={
                "type": "object",
                "properties": {"video_id": {"type": "string"}},
                "required": ["video_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"status": {"type": "string"}},
            },
            risk_level=ToolRiskLevel.P1,
            permission_requirement="youtube_caption_read",
            timeout_seconds=CAPTION_TIMEOUT,
            cancellation_supported=False,
            audit_behavior="no mandatory durable audit",
            supported_platforms=("win32", "linux", "darwin"),
            supported_environments=("local",),
        ),
    )


@dataclass(frozen=True)
class TranscriptResult:
    decision: PermissionDecision
    status: ToolResultStatus
    attempted: bool
    text: str | None = None  # None with SUCCESS: the video has no captions


class YouTubeTranscriptTool:
    """Asks PermissionEngine, then resolves the URL and reads the captions."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicy | None = None,
        fetch: CaptionFetcher = fetch_captions,
    ) -> None:
        registry.require(YOUTUBE_TRANSCRIPT_TOOL_ID)
        self._engine = PermissionEngine(registry, policy)
        self._fetch = fetch

    def read(
        self,
        locate: Callable[[], str | None],
        request: PermissionRequest,
        grants: Sequence[PermissionGrant] = (),
    ) -> TranscriptResult:
        """``locate`` (a URL or a search) runs only once ALLOWED."""
        if (
            request.tool_id != YOUTUBE_TRANSCRIPT_TOOL_ID
            or request.action != TRANSCRIPT_ACTION
            or request.resource != TRANSCRIPT_RESOURCE
        ):
            raise ValueError("Request is outside the transcript scope")
        decision = self._engine.evaluate(request, grants=grants)
        if decision.status != PermissionStatus.ALLOWED:
            return TranscriptResult(decision, ToolResultStatus.DENIED, False)
        try:
            video_id = youtube_video_id(locate() or "")
            if video_id is None:
                return TranscriptResult(decision, ToolResultStatus.FAILURE, True)
            text = self._fetch(video_id)
        except Exception:
            return TranscriptResult(decision, ToolResultStatus.FAILURE, True)
        return TranscriptResult(decision, ToolResultStatus.SUCCESS, True, text)
