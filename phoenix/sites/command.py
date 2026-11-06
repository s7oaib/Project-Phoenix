# ruff: noqa: E501
"""Rule-based intent router for the command bar and voice transcripts.

"site" means this module handled it; "passthrough" means the existing mission
and chat path runs unchanged. No model is involved, so routing is instant.
"""

import re
from dataclasses import dataclass
from typing import Literal

from phoenix.security.trust import TRUST
from phoenix.sites.editor import COLORS
from phoenix.sites.service import SiteBusy, SitesService, get_default
from phoenix.sites.spec import SiteError

SITE_WORD = r"(?:web ?site|site|landing(?: page)?|portfolio)"
CREATE = re.compile(
    rf"^(?:please |can you |could you )?(?:design|build|make|create|generate)\s+(?:me\s+)?"
    rf"(?:a|an|new|another|my|our)\b.*\b{SITE_WORD}\b",
    re.I,
)
OPEN = re.compile(
    rf"^(?:open|show|load|switch to)\s+(?:me\s+)?(?:my|the)?\s*(?P<x>.+?)\s+{SITE_WORD}$",
    re.I,
)
UNDO = re.compile(
    r"^(?:please )?(?:undo|revert|go back)\b(?: that| the last (?:change|edit))?$", re.I
)
REDO = re.compile(r"^(?:please )?redo\b(?: that)?$", re.I)
EDIT_VERB = re.compile(
    r"^(?:please |can you |could you )?(?:change|make|set|add|remove|delete|drop|hide|move|rename|"
    r"replace|update|swap|use|put|turn|rewrite|shorten|lengthen|switch)\b",
    re.I,
)
EDIT_REF = re.compile(
    r"\b(?:section|theme|colou?r|font|hero|footer|pricing|testimonials?|gallery|features|contact|"
    r"title|heading|headline|background|layout|dark mode|light mode)\b",
    re.I,
)
NEW = re.compile(r"\b(?:a|an|new|another)\b", re.I)
# A bare edit verb is not enough ("make a coffee"): it must also name the site or a look.
EDIT_OBJ = re.compile(
    rf"\b(?:it|site|page|website|dark|light|serif|sans|mono|display|{'|'.join(COLORS)})\b",
    re.I,
)


@dataclass(frozen=True)
class CommandOutcome:
    kind: Literal["site", "passthrough"]
    site_id: str | None
    message: str


def _pass() -> CommandOutcome:
    return CommandOutcome("passthrough", None, "")


# Voice-first: the user switches Trust mode by speaking, never by clicking.
TRUST_CMD = re.compile(
    r"(?:please )?(?:(?:turn|switch|set) )?(?:(?:on|off) )?(?:enable |disable |activate |deactivate )?"
    r"(?:the )?trust mode(?: (?:on|off))?",
    re.I,
)


async def handle_command(
    text: str, active_site_id: str | None = None, service: SitesService | None = None
) -> CommandOutcome:
    service = service or get_default()
    text = " ".join(text.split())
    if TRUST_CMD.fullmatch(text.strip(" .!?,")):
        if re.search(r"\b(?:off|disable|deactivate)\b", text, re.I):
            on = False
        elif re.search(r"\b(?:on|enable|activate)\b", text, re.I):
            on = True
        else:
            return _pass()  # "trust mode" alone changes nothing
        TRUST.set(on)
        return CommandOutcome(
            "site", None, "Trust mode on. I won't ask again, boss." if on else "Trust mode off."
        )
    if service is None or not text:
        return _pass()
    try:
        if CREATE.search(text) and not (
            active_site_id
            and EDIT_REF.search(text)
            and not re.search(r"\b(?:a|an|new|another)\b", text, re.I)
        ):
            view = service.start_build(text)
            return CommandOutcome("site", view["id"], "On it. Building your site.")
        found = OPEN.match(text)
        if found:
            slug = service.find(found["x"])
            if slug is None:
                return _pass()
            return CommandOutcome("site", slug, f"Opened {service.get(slug)['name']}.")
        if not active_site_id:
            return _pass()
        if UNDO.match(text) or REDO.match(text):
            view = service.move(active_site_id, -1 if UNDO.match(text) else 1)
            return CommandOutcome("site", active_site_id, view["message"])
        if EDIT_REF.search(text) or (EDIT_VERB.match(text) and EDIT_OBJ.search(text)):
            service.start_edit(active_site_id, text)
            return CommandOutcome("site", active_site_id, "On it. Updating your site.")
    except SiteBusy as exc:
        return CommandOutcome("site", active_site_id, str(exc))
    except SiteError as exc:
        return CommandOutcome("site", active_site_id, str(exc))
    return _pass()
