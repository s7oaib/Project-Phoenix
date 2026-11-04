"""POST /command: the single entry for the command bar.

Site intents (build, edit, undo, open) are handled by phoenix.sites.command;
everything else goes to the existing chat/mission path unchanged.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Request
from pydantic import StringConstraints

from phoenix.api.routes.common import Contract
from phoenix.api.routes.missions import chat
from phoenix.api.routes.schemas import ChatRequest
from phoenix.api.routes.sites import sites
from phoenix.sites.command import handle_command

router = APIRouter()


class CommandRequest(Contract):
    text: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=16_000, pattern=r"\S"),
    ]
    active_site_id: (
        Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")] | None
    ) = None


class CommandResult(Contract):
    kind: Literal["site", "mission"]
    site_id: str | None = None
    mission_id: str | None = None
    message: str


@router.post("/command", response_model=CommandResult)
async def command(body: CommandRequest, request: Request) -> CommandResult:
    outcome = await handle_command(body.text, body.active_site_id, sites(request))
    if outcome.kind == "site":
        return CommandResult(
            kind="site", site_id=outcome.site_id, message=outcome.message
        )
    accepted = await chat(ChatRequest(text=body.text), request)
    return CommandResult(kind="mission", mission_id=accepted.mission_id, message="")
