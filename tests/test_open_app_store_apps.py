"""PHX-137: Store (UWP) apps in the app index; known-site fallback."""

import json

import pytest

from phoenix.agents.quick_commands import QuickKind, match_quick_command
from phoenix.tools.open_app import (
    APPS_FOLDER,
    OPEN_APP_TOOL_ID,
    SHORTCUT_RESOURCE,
    OpenAppTool,
    is_shortcut_target,
    open_app_tool_definitions,
    scan_start_apps,
)
from phoenix.tools.open_link import open_link_tool_definitions
from phoenix.tools.registry import ToolRegistry, ToolResultStatus
from tests.test_quick_commands_play_open import SHORTCUT, request

WHATSAPP_ID = "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App"
WHATSAPP = APPS_FOLDER + WHATSAPP_ID


def registry() -> ToolRegistry:
    return ToolRegistry((*open_link_tool_definitions(), *open_app_tool_definitions()))


def test_scan_start_apps_keeps_store_app_ids_only() -> None:
    listed = [
        {"Name": "WhatsApp", "AppID": WHATSAPP_ID},
        {"Name": "Notepad", "AppID": r"{1AC14E77}\notepad.exe"},
        {"Name": "Evil", "AppID": "x_cv1g1gvanyjgm!App & calc"},
        {"Name": "Uninstall Foo", "AppID": "Foo_abcdefghijklm!App"},
        {"Name": 3, "AppID": WHATSAPP_ID},
        "junk",
    ]
    found = scan_start_apps(lambda: json.dumps(listed))
    assert dict(found) == {"whatsapp": WHATSAPP}


def test_scan_start_apps_single_object_and_failures() -> None:
    one = json.dumps({"Name": "WhatsApp", "AppID": WHATSAPP_ID})
    assert dict(scan_start_apps(lambda: one)) == {"whatsapp": WHATSAPP}
    assert dict(scan_start_apps(lambda: "")) == {}
    assert dict(scan_start_apps(lambda: "not json")) == {}
    assert dict(scan_start_apps(lambda: "42")) == {}


@pytest.mark.parametrize(
    ("target", "ok"),
    [
        (WHATSAPP, True),
        (r"C:\Start\Word.lnk", True),
        (APPS_FOLDER + "notepad.exe", False),
        (APPS_FOLDER + WHATSAPP_ID + " /x", False),
        ("shell:AppsFolder" + WHATSAPP_ID, False),
        (r"C:\Start\Word.exe", False),
        (None, False),
    ],
)
def test_shortcut_targets_are_strict(target: object, ok: bool) -> None:
    assert is_shortcut_target(target) is ok


def test_store_app_launches_through_apps_folder() -> None:
    started: list[str] = []
    apps = OpenAppTool(
        registry(), launcher=started.append, paths={}, shortcuts={"whatsapp": WHATSAPP}
    )
    result = apps.open_shortcut(
        "whatsapp", request(OPEN_APP_TOOL_ID, SHORTCUT_RESOURCE), (SHORTCUT,)
    )
    assert result.status == ToolResultStatus.SUCCESS and started == [WHATSAPP]
    with pytest.raises(ValueError):
        OpenAppTool(registry(), paths={}, shortcuts={"x": APPS_FOLDER + "calc.exe"})


@pytest.mark.parametrize(
    ("text", "url"),
    [
        ("open spotify", "https://open.spotify.com/"),
        ("open whatsapp", "https://web.whatsapp.com/"),
        ("open youtube music", "https://music.youtube.com/"),
        ("open discord", "https://discord.com/"),
    ],
)
def test_known_site_when_no_app_installed(text: str, url: str) -> None:
    command = match_quick_command(text)
    assert command is not None and command.kind == QuickKind.OPEN_SITE
    assert command.url == url


def test_installed_app_beats_known_site() -> None:
    command = match_quick_command("open whatsapp", shortcuts=frozenset({"whatsapp"}))
    assert command is not None and command.shortcut == "whatsapp"
