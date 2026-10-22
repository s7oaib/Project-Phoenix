"""PHX-136 play on YouTube and open anything (ADR-250..254).

Router, website and shortcut validation, the Start-menu scan, the YouTube
results parser and fetch, and the runner under PermissionEngine with the two
wide scopes. Launchers, the video finder and httpx are fakes: nothing opens,
nothing is fetched.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest

from phoenix.agents.quick_commands import (
    MUSIC_QUERIES,
    QUICK_ACTOR_ID,
    QUICK_SCOPES,
    QUICK_WIDE_SCOPES,
    YOUTUBE_WATCH,
    QuickCommandRunner,
    QuickKind,
    best_shortcut,
    find_youtube_video,
    match_quick_command,
    quick_grants,
    video_ids,
)
from phoenix.security.permissions import (
    GrantDuration,
    PermissionGrant,
    PermissionPolicy,
    PermissionRequest,
    PermissionStatus,
)
from phoenix.tools.open_app import (
    OPEN_APP_TOOL_ID,
    SHORTCUT_RESOURCE,
    AppName,
    OpenAppInputError,
    OpenAppTool,
    open_app_tool_definitions,
    scan_shortcuts,
)
from phoenix.tools.open_link import (
    OPEN_LINK_TOOL_ID,
    SITE_RESOURCE,
    OpenLinkInputError,
    OpenLinkTool,
    open_link_tool_definitions,
    site_root_url,
)
from phoenix.tools.registry import ToolRegistry, ToolResultStatus, ToolRiskLevel

SHORTCUTS = {
    "google chrome": r"C:\ProgramData\Start\Google Chrome.lnk",
    "chrome remote desktop": r"C:\ProgramData\Start\Chrome Remote Desktop.lnk",
    "word": r"C:\ProgramData\Start\Word.lnk",
    "spotify": r"C:\Users\u\Start\Spotify.lnk",
    "netflix": r"C:\Users\u\Start\Netflix.lnk",
    "foo one": r"C:\Start\Foo One.lnk",
    "foo two": r"C:\Start\Foo Two.lnk",
}
NAMES = frozenset(SHORTCUTS)
RESULTS = "https://www.youtube.com/results?search_query="


# ---- router


@pytest.mark.parametrize(
    ("text", "kind", "url", "shortcut"),
    [
        (
            "play Bohemian Rhapsody on YouTube",
            QuickKind.PLAY_YOUTUBE,
            RESULTS + "Bohemian+Rhapsody",
            None,
        ),
        (
            "play despacito song",
            QuickKind.PLAY_YOUTUBE,
            RESULTS + "despacito+song",
            None,
        ),
        ("play cat videos", QuickKind.PLAY_YOUTUBE, RESULTS + "cat+videos", None),
        ("open reddit.com", QuickKind.OPEN_SITE, "https://reddit.com/", None),
        ("go to Amazon.in please", QuickKind.OPEN_SITE, "https://amazon.in/", None),
        ("open amazon", QuickKind.OPEN_SITE, "https://www.amazon.com/", None),
        ("open twitter", QuickKind.OPEN_SITE, "https://x.com/", None),
        ("open x", QuickKind.OPEN_SITE, "https://x.com/", None),
        ("open spotify web", QuickKind.OPEN_SITE, "https://open.spotify.com/", None),
        ("open chatgpt", QuickKind.OPEN_SITE, "https://chatgpt.com/", None),
        ("open netflix.com", QuickKind.OPEN_SITE, "https://netflix.com/", None),
        ("open netflix", QuickKind.OPEN_SHORTCUT, None, "netflix"),  # app wins
        ("open chrome", QuickKind.OPEN_SHORTCUT, None, "google chrome"),
        ("launch the Word app", QuickKind.OPEN_SHORTCUT, None, "word"),
        ("open spotfy", QuickKind.OPEN_SHORTCUT, None, "spotify"),
        ("open youtube", QuickKind.OPEN_YOUTUBE, "https://www.youtube.com/", None),
        ("open notepad", QuickKind.OPEN_NOTEPAD, None, None),
    ],
)
def test_router_matches_play_and_open(
    text: str, kind: QuickKind, url: str | None, shortcut: str | None
) -> None:
    command = match_quick_command(text, shortcuts=NAMES)
    assert command is not None
    assert (command.kind, command.url, command.shortcut) == (kind, url, shortcut)
    if kind == QuickKind.OPEN_SITE:
        assert site_root_url(command.url or "") == command.url


@pytest.mark.parametrize(
    "text",
    [
        "play chess",
        "play",
        "play song; rm -rf /",
        "play https://evil.com/video",
        "open foo",  # tie between "foo one" and "foo two"
        "open notes.md",
        "open notepad.exe",
        "open report.docx",
        "open 127.0.0.1",
        "open 10.0.0.1",
        "open evil.local",
        "open printer.lan",
        "open localhost.com",
        "open evil.com/login",
        "open evil.com:8080",
        "open https://evil.com",
        "open user@evil.com",
        r"open C:\Windows\System32\cmd.exe",
        "open regedit",
        "open youtube and delete my files",
        "open " + "a" * 70,
    ],
)
def test_router_leaves_the_rest_to_the_pipeline(text: str) -> None:
    assert match_quick_command(text, shortcuts=NAMES) is None


def test_without_an_index_known_names_are_websites() -> None:
    command = match_quick_command("open netflix")
    assert command is not None and command.url == "https://www.netflix.com/"
    assert match_quick_command("open chrome") is None


def test_play_music_picks_a_fixed_query(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "phoenix.agents.quick_commands.random.choice", lambda seq: seq[2]
    )
    for text in ("play some music", "play music on youtube", "play random music"):
        command = match_quick_command(text)
        assert command is not None and command.kind == QuickKind.PLAY_MUSIC
        assert command.query == MUSIC_QUERIES[2] == "bollywood hits"
        assert command.url == RESULTS + "bollywood+hits"


def test_search_youtube_still_opens_results() -> None:
    command = match_quick_command("search youtube for lofi")
    assert command is not None and command.kind == QuickKind.SEARCH_YOUTUBE
    assert command.url == RESULTS + "lofi"


def test_best_shortcut_ranks_whole_words_then_spelling() -> None:
    assert best_shortcut("chrome", NAMES) == "google chrome"
    assert best_shortcut("Word", NAMES) == "word"
    assert best_shortcut("w", NAMES) is None
    assert best_shortcut("excel", NAMES) is None


# ---- website validation


@pytest.mark.parametrize(
    "url",
    [
        "http://reddit.com/",
        "https://reddit.com",
        "https://reddit.com/r/x",
        "https://reddit.com/?q=1",
        "https://reddit.com/#x",
        "https://user@reddit.com/",
        "https://reddit.com:443/",
        "https://Reddit.com/",
        "https://10.0.0.1/",
        "https://[::1]/",
        "https://localhost/",
        "https://intranet.corp/",
        "https://box.internal/",
        "https://reddit/",
        "https://-bad.com/",
        "",
    ],
)
def test_site_root_url_refuses_anything_but_a_public_home_page(url: str) -> None:
    with pytest.raises(OpenLinkInputError):
        site_root_url(url)


def test_site_root_url_accepts_a_public_home_page() -> None:
    assert site_root_url("https://amazon.in/") == "https://amazon.in/"


# ---- YouTube results


def test_video_ids_are_distinct_valid_and_capped() -> None:
    page = (
        '"videoId":"abcdefghijk" "videoId":"abcdefghijk" "videoId":"short" '
        '"videoId":"ABC_def-123" "videoId":"zzzzzzzzzzz"'
    )
    assert video_ids(page, 5) == ("abcdefghijk", "ABC_def-123", "zzzzzzzzzzz")
    assert video_ids(page, 1) == ("abcdefghijk",)
    assert video_ids("no ids", 5) == ()


def test_find_video_fetches_the_results_page(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        calls.append((url, kwargs))
        return httpx.Response(200, text='"videoId":"abcdefghijk"')

    monkeypatch.setattr("phoenix.agents.quick_commands.httpx.get", fake_get)
    assert find_youtube_video("lofi beats", False) == YOUTUBE_WATCH + "abcdefghijk"
    ((url, kwargs),) = calls
    assert url == RESULTS + "lofi+beats"
    assert kwargs["timeout"] == 5.0 and kwargs["follow_redirects"] is False
    assert "Mozilla" in kwargs["headers"]["User-Agent"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"location": "https://consent.youtube.com/"}),
        httpx.Response(200, text="nothing"),
        httpx.ConnectError("down"),
    ],
)
def test_find_video_failure_is_none(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response | Exception
) -> None:
    def fake_get(url: str, **kwargs: Any) -> httpx.Response:
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("phoenix.agents.quick_commands.httpx.get", fake_get)
    assert find_youtube_video("x", True) is None


def test_play_music_picks_among_the_top_five(monkeypatch: pytest.MonkeyPatch) -> None:
    page = "".join(f'"videoId":"{c * 11}"' for c in "abcdefg")
    monkeypatch.setattr(
        "phoenix.agents.quick_commands.httpx.get",
        lambda url, **_: httpx.Response(200, text=page),
    )
    seen: list[tuple[str, ...]] = []

    def pick(seq: tuple[str, ...]) -> str:
        seen.append(tuple(seq))
        return seq[-1]

    monkeypatch.setattr("phoenix.agents.quick_commands.random.choice", pick)
    assert find_youtube_video("x", True) == YOUTUBE_WATCH + "e" * 11
    assert seen == [tuple(c * 11 for c in "abcde")]


# ---- Start-menu index


def test_scan_shortcuts_indexes_lnk_files_only(tmp_path: Path) -> None:
    (tmp_path / "Tools").mkdir()
    for name in ("Word.lnk", "Tools/Google  Chrome.lnk", "Uninstall Foo.lnk"):
        (tmp_path / name).write_text("x")
    (tmp_path / "readme.txt").write_text("x")
    found = scan_shortcuts([tmp_path, tmp_path / "missing", Path("relative")])
    assert dict(found) == {
        "word": str(tmp_path / "Word.lnk"),
        "google chrome": str(tmp_path / "Tools" / "Google  Chrome.lnk"),
    }


# ---- tools and runner


def registry() -> ToolRegistry:
    return ToolRegistry((*open_link_tool_definitions(), *open_app_tool_definitions()))


def grant(tool_id: str, scope: tuple[str, str, str]) -> PermissionGrant:
    action, resource, reason = scope
    return PermissionGrant(
        grant_id=str(uuid4()),
        actor_id=QUICK_ACTOR_ID,
        tool_id=tool_id,
        action=action,
        resource=resource,
        reason=reason,
        duration=GrantDuration.PERSISTENT,
        risk_level=ToolRiskLevel.P1,
        issued_at=datetime.now(UTC),
        user_decision="approved",
    )


LINK = grant(OPEN_LINK_TOOL_ID, QUICK_SCOPES[OPEN_LINK_TOOL_ID])
SITE = grant(OPEN_LINK_TOOL_ID, QUICK_WIDE_SCOPES[OPEN_LINK_TOOL_ID])
APP = grant(OPEN_APP_TOOL_ID, QUICK_SCOPES[OPEN_APP_TOOL_ID])
SHORTCUT = grant(OPEN_APP_TOOL_ID, QUICK_WIDE_SCOPES[OPEN_APP_TOOL_ID])


class Fixture:
    def __init__(self, video: str | None | Exception = None) -> None:
        self.opened: list[str] = []
        self.started: list[str] = []
        self.searched: list[tuple[str, bool]] = []
        self.video = video
        tools = registry()
        self.runner = QuickCommandRunner(
            OpenLinkTool(tools, PermissionPolicy(), launcher=self.opened.append),
            OpenAppTool(
                tools,
                PermissionPolicy(),
                launcher=self.started.append,
                paths={},
                shortcuts=SHORTCUTS,
            ),
            find_video=self.find,
        )

    def find(self, query: str, pick_random: bool) -> str | None:
        self.searched.append((query, pick_random))
        if isinstance(self.video, Exception):
            raise self.video
        return self.video

    def run(self, text: str, *grants: PermissionGrant) -> Any:
        command = self.runner.match(text)
        assert command is not None
        return asyncio.run(self.runner.run("m1", command, grants))


def test_play_without_a_grant_waits_and_fetches_nothing() -> None:
    fx = Fixture(YOUTUBE_WATCH + "abcdefghijk")
    outcome = fx.run("play lofi music on youtube")
    assert outcome.pending is not None and outcome.answer is None
    assert outcome.pending.resource == QUICK_SCOPES[OPEN_LINK_TOOL_ID][1]
    assert fx.searched == [] and fx.opened == []


def test_play_opens_the_watch_page_under_the_link_grant() -> None:
    fx = Fixture(YOUTUBE_WATCH + "abcdefghijk")
    outcome = fx.run("play lofi music on youtube", LINK)
    assert fx.searched == [("lofi music", False)]
    assert fx.opened == [YOUTUBE_WATCH + "abcdefghijk"]
    assert outcome.answer == "Playing lofi music on YouTube."
    assert outcome.record.verified and outcome.record.kind == QuickKind.PLAY_YOUTUBE


@pytest.mark.parametrize(
    "video", [None, "https://evil.com/watch?v=abcdefghijk", RuntimeError("x")]
)
def test_play_falls_back_to_the_results_page(video: str | None | Exception) -> None:
    fx = Fixture(video)
    outcome = fx.run("play lofi music on youtube", LINK)
    assert fx.opened == [RESULTS + "lofi+music"]
    assert outcome.answer == "Opened YouTube search for lofi music."


def test_play_music_asks_for_a_random_pick() -> None:
    fx = Fixture(YOUTUBE_WATCH + "abcdefghijk")
    fx.run("play some music", LINK)
    ((query, pick),) = fx.searched
    assert query in MUSIC_QUERIES and pick is True


def test_site_needs_the_website_scope() -> None:
    fx = Fixture()
    waiting = fx.run("open reddit", LINK)  # the fixed link grant does not cover it
    assert waiting.pending is not None
    assert waiting.pending.resource == SITE_RESOURCE and fx.opened == []
    done = fx.run("open reddit", SITE)
    assert fx.opened == ["https://www.reddit.com/"]
    assert done.answer == "Opened Reddit." and done.record.verified
    assert done.record.decision_status == PermissionStatus.ALLOWED


def test_shortcut_needs_the_shortcut_scope() -> None:
    fx = Fixture()
    waiting = fx.run("open chrome", APP)
    assert waiting.pending is not None
    assert waiting.pending.resource == SHORTCUT_RESOURCE and fx.started == []
    done = fx.run("open chrome", SHORTCUT)
    assert fx.started == [SHORTCUTS["google chrome"]]
    assert done.answer == "Opened Google Chrome." and done.record.verified
    assert done.record.tool_id == OPEN_APP_TOOL_ID


def test_wide_grants_are_quick_grants_and_scopes_do_not_cross() -> None:
    assert quick_grants([LINK, SITE, APP, SHORTCUT]) == (LINK, SITE, APP, SHORTCUT)
    crossed = grant(OPEN_APP_TOOL_ID, QUICK_WIDE_SCOPES[OPEN_LINK_TOOL_ID])
    with pytest.raises(ValueError):
        quick_grants([crossed])


def request(tool_id: str, resource: str) -> PermissionRequest:
    action, _, reason = (
        QUICK_WIDE_SCOPES[tool_id]
        if resource in (SITE_RESOURCE, SHORTCUT_RESOURCE)
        else QUICK_SCOPES[tool_id]
    )
    return PermissionRequest(
        request_id=str(uuid4()),
        actor_id=QUICK_ACTOR_ID,
        tool_id=tool_id,
        action=action,
        resource=resource,
        reason=reason,
        requested_at=datetime.now(UTC),
    )


def test_tools_bind_each_scope_to_its_validation() -> None:
    opened: list[str] = []
    started: list[str] = []
    tools = registry()
    links = OpenLinkTool(tools, launcher=opened.append)
    apps = OpenAppTool(tools, launcher=started.append, paths={}, shortcuts=SHORTCUTS)
    link_resource = QUICK_SCOPES[OPEN_LINK_TOOL_ID][1]
    app_resource = QUICK_SCOPES[OPEN_APP_TOOL_ID][1]
    with pytest.raises(OpenLinkInputError):  # a website under the fixed scope
        links.open("https://reddit.com/", request(OPEN_LINK_TOOL_ID, link_resource))
    with pytest.raises(OpenLinkInputError):  # a website is never resolved
        links.open(
            "https://reddit.com/",
            request(OPEN_LINK_TOOL_ID, SITE_RESOURCE),
            (SITE,),
            resolve=lambda: None,
        )
    with pytest.raises(OpenAppInputError):
        apps.open_shortcut("excel", request(OPEN_APP_TOOL_ID, SHORTCUT_RESOURCE))
    with pytest.raises(OpenAppInputError):
        apps.open_shortcut("word", request(OPEN_APP_TOOL_ID, app_resource))
    with pytest.raises(OpenAppInputError):
        apps.open(AppName.NOTEPAD, request(OPEN_APP_TOOL_ID, SHORTCUT_RESOURCE))
    result = apps.open_shortcut(
        "word", request(OPEN_APP_TOOL_ID, SHORTCUT_RESOURCE), (SHORTCUT,)
    )
    assert result.status == ToolResultStatus.SUCCESS and started == [SHORTCUTS["word"]]
    assert opened == []


@pytest.mark.parametrize(
    "shortcuts",
    [
        {"word": "Word.lnk"},
        {"word": r"C:\Start\Word.exe"},
        {"Word": r"C:\Start\Word.lnk"},
        {"": r"C:\Start\Word.lnk"},
    ],
)
def test_shortcut_index_is_validated(shortcuts: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        OpenAppTool(registry(), paths={}, shortcuts=shortcuts)


def test_runner_match_uses_the_tool_index() -> None:
    fx = Fixture()
    command = fx.runner.match("open chrome")
    assert command is not None and command.shortcut == "google chrome"
    assert fx.runner.match("open excel") is None
