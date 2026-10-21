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
        CapabilityId.MEMORY: CapabilityFlag(
            enabled=False, ticket="PHX-110", agent_id="memory"
        ),
        CapabilityId.WEB_RESEARCH: CapabilityFlag(
            enabled=False, ticket="PHX-111", agent_id="web_research"
        ),
        CapabilityId.COMPUTER_CONTROL: CapabilityFlag(
            enabled=False, ticket="PHX-112", agent_id="computer_control"
        ),
        CapabilityId.MEDIA: CapabilityFlag(
            enabled=False, ticket="PHX-113", agent_id="media"
        ),
        CapabilityId.DOCUMENT_GENERATION: CapabilityFlag(
            enabled=False, ticket="PHX-113", agent_id="document_analyst"
        ),
        # PHX-114 (ADR-108): this build has the voice pipeline. Whether it is
        # running or its models and devices are available right now is
        # runtime state, reported by GET /voice/status, not by this flag.
        CapabilityId.VOICE: CapabilityFlag(
            enabled=True, ticket="PHX-114", agent_id="voice"
        ),
    }
)
