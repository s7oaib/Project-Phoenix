"""PHX-130 ``desktop.open_link``: hand one allowlisted https URL to the OS browser.

The tool opens a link in the user's normal default browser and nothing else:
PHOENIX does not drive, read or close the page (owner decision 2026-10-23). The
URL must be ``https`` on a host in ``ALLOWED_HOSTS`` (exactly the quick-command
template hosts; PHX-133 added four) with no user info, port or fragment;
anything else is refused before PermissionEngine is asked. PermissionEngine
decides every call (P1, ADR-220); only ALLOWED launches. The launch is
``os.startfile`` on Windows (no shell, no arguments) and ``webbrowser.open``
elsewhere. P1 writes no durable audit row (INV-007 covers P3/P4); the mission
trace records the call.

PHX-136 (ADR-251, ADR-252): under its own scope (``SITE_RESOURCE``) the tool
also opens a public website's home page, ``https://<domain>/`` only
(``site_root_url``); and a caller may pass ``resolve``, run only after ALLOWED,
whose allowlisted URL replaces the link (e.g. a YouTube watch page), the link
itself staying the fallback.
"""

import ipaddress
import os
import re
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
# PHX-133 (ADR-235): plus Wikipedia, GitHub, Gmail and Google News.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "en.wikipedia.org",
        "github.com",
        "mail.google.com",
        "news.google.com",
        "www.google.com",
        "www.youtube.com",
    }
)
MAX_URL_CHARS = 2048
Launcher = Callable[[str], None]
# PHX-136 (ADR-251): the website scope's resource; any public https home page.
SITE_RESOURCE = "sites:public-https-home-pages"
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
# Never a public website: local, private or reserved names.
_PRIVATE_SUFFIXES = (
    "localhost",
    "local",
    "internal",
    "intranet",
    "lan",
    "home",
    "corp",
    "private",
    "test",
    "invalid",
    "example",
    "arpa",
    "onion",
)


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


def site_root_url(url: str) -> str:
    """Return url if it is exactly ``https://<public domain>/``, else raise."""
    if type(url) is not str or not url or len(url) > 300:
        raise OpenLinkInputError("Invalid link")
    parts = urlsplit(url)
    host = parts.netloc
    labels = host.split(".")
    if (
        parts.scheme != "https"
        or url != f"https://{host}/"
        or host != host.lower()
        or len(host) > 253
        or len(labels) < 2
        or not all(_LABEL.fullmatch(label) for label in labels)
        or not labels[-1].isalpha()
        or labels[-1] in _PRIVATE_SUFFIXES
        or labels[0] == "localhost"
    ):
        raise OpenLinkInputError("Not a public website home page")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return url
    raise OpenLinkInputError("Not a public website home page")


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
                "Open one allowlisted https link (Google, YouTube, Wikipedia, "
                "GitHub, Gmail, Google Maps or News), or (PHX-136) a public "
                "website's https home page, in the user's default browser; no "
                "page control."
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
    url: str | None = None  # PHX-136: the link actually opened


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
        *,
        resolve: Callable[[], str | None] | None = None,
    ) -> OpenLinkResult:
        """Open url; a website home page only under ``SITE_RESOURCE`` (PHX-136).

        ``resolve`` runs only once ALLOWED; an allowlisted URL it returns is
        opened instead of url, anything else (or an error) keeps url.
        """
        site = request.resource == SITE_RESOURCE
        url = site_root_url(url) if site else allowed_url(url)
        if request.tool_id != OPEN_LINK_TOOL_ID:
            raise OpenLinkInputError("Request is for another tool")
        if site and resolve is not None:
            raise OpenLinkInputError("A website link is opened as given")
        decision = self._engine.evaluate(request, grants=grants)
        if decision.status != PermissionStatus.ALLOWED:
            return OpenLinkResult(decision, ToolResultStatus.DENIED, attempted=False)
        if resolve is not None:
            try:
                url = allowed_url(resolve() or url)
            except Exception:
                pass  # keep the allowlisted fallback link
        try:
            self._launch(url)
        except Exception:
            return OpenLinkResult(decision, ToolResultStatus.FAILURE, True, url)
        return OpenLinkResult(decision, ToolResultStatus.SUCCESS, True, url)
