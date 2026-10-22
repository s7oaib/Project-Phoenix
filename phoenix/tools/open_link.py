"""PHX-130 ``desktop.open_link``: hand one allowlisted https URL to the OS browser.

The tool opens a link in the user's normal default browser and nothing else:
PHOENIX does not drive, read or close the page (owner decision 2026-10-23). The
URL must be ``https`` on a host in ``ALLOWED_HOSTS`` (exactly the quick-command
template hosts) with no user info, port or fragment; anything else is refused
before PermissionEngine is asked. PermissionEngine decides every call (P1,
ADR-220); only ALLOWED launches. The launch is ``os.startfile`` on Windows (no
shell, no arguments) and ``webbrowser.open`` elsewhere. P1 writes no durable
audit row (INV-007 covers P3/P4); the mission trace records the call.
"""

import os
import sys
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

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

OPEN_LINK_TOOL_ID = "desktop.open_link"
ALLOWED_HOSTS: frozenset[str] = frozenset({"www.google.com", "www.youtube.com"})
MAX_URL_CHARS = 2048
Launcher = Callable[[str], None]


class OpenLinkInputError(ValueError):
    """The URL is not an allowlisted https link; nothing was asked or opened."""


def allowed_url(url: str) -> str:
    """Return url unchanged if it is an allowlisted https link, else raise."""
    if type(url) is not str or not url or len(url) > MAX_URL_CHARS:
        raise OpenLinkInputError("Invalid link")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        raise OpenLinkInputError("Invalid link")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc not in ALLOWED_HOSTS
        or parts.hostname not in ALLOWED_HOSTS
        or parts.fragment
        or not url.startswith("https://" + parts.netloc + "/")
    ):
        raise OpenLinkInputError("Link is outside the allowlist")
    return url


def default_launcher(url: str) -> None:
    """Open url in the default browser; raises if the OS refuses."""
    if sys.platform == "win32":
        os.startfile(url)
        return
    if not webbrowser.open(url):
        raise OSError("No default browser")


def open_link_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            tool_id=OPEN_LINK_TOOL_ID,
            description=(
                "Open one allowlisted https link (Google or YouTube) in the "
                "user's default browser; no page control."
            ),
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"status": {"type": "string"}},
            },
            risk_level=ToolRiskLevel.P1,
            permission_requirement="allowlisted_link_open",
            timeout_seconds=5.0,
            cancellation_supported=False,
            audit_behavior="no mandatory durable audit",
            supported_platforms=("win32", "linux", "darwin"),
            supported_environments=("local",),
        ),
    )


@dataclass(frozen=True)
class OpenLinkResult:
    decision: PermissionDecision
    status: ToolResultStatus
    attempted: bool


class OpenLinkTool:
    """Validates the link, asks PermissionEngine, launches only when ALLOWED."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PermissionPolicy | None = None,
        launcher: Launcher = default_launcher,
    ) -> None:
        registry.require(OPEN_LINK_TOOL_ID)
        self._engine = PermissionEngine(registry, policy)
        self._launch = launcher

    def open(
        self,
        url: str,
        request: PermissionRequest,
        grants: Sequence[PermissionGrant] = (),
    ) -> OpenLinkResult:
        url = allowed_url(url)
        if request.tool_id != OPEN_LINK_TOOL_ID:
            raise OpenLinkInputError("Request is for another tool")
        decision = self._engine.evaluate(request, grants=grants)
        if decision.status != PermissionStatus.ALLOWED:
            return OpenLinkResult(decision, ToolResultStatus.DENIED, attempted=False)
        try:
            self._launch(url)
        except Exception:
            return OpenLinkResult(decision, ToolResultStatus.FAILURE, attempted=True)
        return OpenLinkResult(decision, ToolResultStatus.SUCCESS, attempted=True)
