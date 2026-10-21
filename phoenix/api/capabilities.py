"""PHX-108 capability flags (ADR-112): which mission capabilities are enabled.

The desktop's Computer Control, Research and Media views (and the Settings
voice and memory sections) read these flags through ``GET /capabilities`` and
show an honest "not yet enabled" state until the owning ticket lands. Each
ticket enables its capability by flipping ``enabled`` here, in the same change
that wires the capability into missions; nothing else in the API or the
desktop has to change.

A flag is a fact about this build, not a permission: an enabled capability
still runs only inside a mission, through ToolRegistry and PermissionEngine
(INV-004), and the flag grants nothing. The table is fixed at import time and
is not writable over the API.

PHX-120 (ADR-161) enables memory, web research, media and document generation.
Web research also stays opt-in per launch: ``GET /capabilities`` reports it
enabled only while ``PHOENIX_WEB_RESEARCH_ENABLED`` is true (searches leave the
machine), so the Research view never claims a tool the backend does not hold.
"""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class CapabilityId(StrEnum):
    WORKSPACE_DOCUMENTS = "workspace_documents"
    MEMORY = "memory"
    WEB_RESEARCH = "web_research"
    COMPUTER_CONTROL = "computer_control"
    MEDIA = "media"
    DOCUMENT_GENERATION = "document_generation"
    VOICE = "voice"


@dataclass(frozen=True)
class CapabilityFlag:
    enabled: bool
    ticket: str  # the ticket that owns (or delivered) the capability
    agent_id: str  # the specialist agent that carries it


CAPABILITY_FLAGS = MappingProxyType(
    {
        # PHX-046/047: planned document steps read the workspace folder.
        CapabilityId.WORKSPACE_DOCUMENTS: CapabilityFlag(
            enabled=True, ticket="PHX-046", agent_id="document_analyst"
        ),
        # PHX-120 (ADR-161): enabled with the capability integration.
        CapabilityId.MEMORY: CapabilityFlag(
            enabled=True, ticket="PHX-110", agent_id="memory"
        ),
        CapabilityId.WEB_RESEARCH: CapabilityFlag(
            enabled=True, ticket="PHX-111", agent_id="web_research"
        ),
        CapabilityId.COMPUTER_CONTROL: CapabilityFlag(
            enabled=False, ticket="PHX-112", agent_id="computer_control"
        ),
        CapabilityId.MEDIA: CapabilityFlag(
            enabled=True, ticket="PHX-113", agent_id="media"
        ),
        CapabilityId.DOCUMENT_GENERATION: CapabilityFlag(
            enabled=True, ticket="PHX-113", agent_id="document_analyst"
        ),
        CapabilityId.VOICE: CapabilityFlag(
            enabled=False, ticket="PHX-114", agent_id="voice"
        ),
    }
)
