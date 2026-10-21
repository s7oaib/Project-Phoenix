"""PHX-104 request and response schemas (ADR-089).

Every schema forbids extra fields. Responses carry only the user's message
text, PHOENIX's final answer text, IDs, statuses, codes and timestamps, and a
pending permission's tool, action, resource path and risk so the user can
decide on it. Raw model output, tool output and file content are never part of
any schema. PHX-110 (ADR-131): a mission also lists the sources its answer
cites (marker, provenance and file name; never the source text).

Request bodies hold only what the user chooses. An approval names a duration;
the tool, action, resource and actor always come from the stored request
(INV-003), and a body that tries to set them is refused (``extra="forbid"``).
"""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import StringConstraints, model_validator

from phoenix.agents.task_graph import TaskState
from phoenix.api.capabilities import CapabilityId
from phoenix.api.onboarding import RuntimeState
from phoenix.api.routes.common import UUID_PATTERN, Contract
from phoenix.api.workspace import MAX_WORKSPACE_CHARS, WorkspaceStatus
from phoenix.models.provider import ProviderHealthStatus
from phoenix.persistence.missions import (
    GrantStatus,
    MessageRole,
    MissionStatus,
    RequestStatus,
)

MAX_CHAT_CHARS = 16_000

Uuid = Annotated[str, StringConstraints(strict=True, pattern=UUID_PATTERN)]
GrantChoice = Literal["session", "always"]
# PHX-108 (ADR-111): the saved default; "auto" means PHOENIX triages.
MissionPathChoice = Literal["auto", "direct", "complex"]


# ---- chat and missions


class ChatRequest(Contract):
    text: Annotated[
        str,
        StringConstraints(
            strict=True, min_length=1, max_length=MAX_CHAT_CHARS, pattern=r"\S"
        ),
    ]
    conversation_id: Uuid | None = None
    # PHX-105: omitted, PHOENIX triages the request; given, it is the client's
    # choice and is used as is. "complex" plans steps (including document reads
    # when a workspace is set).
    path: Literal["direct", "complex"] | None = None


class MissionAccepted(Contract):
    mission_id: str
    conversation_id: str
    status: Literal["queued"] = "queued"
    rerun_of: str | None = None


class PermissionRequestOut(Contract):
    request_id: str
    mission_id: str
    status: RequestStatus
    tool_id: str
    action: str
    resource: str
    risk_level: str | None
    requested_at: datetime
    decided_at: datetime | None


class MissionSummary(Contract):
    mission_id: str
    conversation_id: str
    status: MissionStatus
    # PHX-105: null until triage decides; triage_outcome says who decided.
    path: Literal["direct", "complex"] | None
    triage_outcome: Literal["decided", "fallback", "client"] | None
    failure_code: str | None
    responder_error: str | None
    rerun_of: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class SourceOut(Contract):
    """PHX-110: one cited source; ``marker`` is the "[n]" used in the answer."""

    marker: str
    kind: Literal["memory", "document_chunk"]
    source_id: str
    document_id: str | None
    title: str | None
    page: int | None
    section: str | None
    chunk_index: int | None
    memory_type: str | None
    label: str


class MissionDetail(MissionSummary):
    request_text: str | None
    answer_text: str | None
    rerun_id: str | None
    task_id: str | None
    task_states: tuple[TaskState, ...]
    permission_request: PermissionRequestOut | None
    # PHX-110: the sources behind the answer, in citation order.
    sources: tuple[SourceOut, ...] = ()


class CancelResult(Contract):
    mission_id: str
    outcome: Literal["cancelled", "cancelling"]


# ---- permissions and grants


class ApproveRequest(Contract):
    duration: GrantChoice


class DenyRequest(Contract):
    """Deny takes no fields; any field is refused."""


class ApproveResult(Contract):
    request_id: str
    duration: GrantChoice
    superseded_mission_id: str
    mission_id: str  # the queued linked re-run


class DenyResult(Contract):
    request_id: str
    mission_id: str
    status: Literal["failed"] = "failed"
    failure_code: Literal["permission_denied"] = "permission_denied"


class GrantOut(Contract):
    grant_id: str
    tool_id: str
    action: str
    resource: str
    risk_level: str
    duration: GrantChoice
    status: GrantStatus
    issued_at: datetime
    revoked_at: datetime | None


class RevokeResult(Contract):
    grant_id: str
    status: Literal["revoked"] = "revoked"


# ---- conversations


class ConversationOut(Contract):
    conversation_id: str
    created_at: datetime
    updated_at: datetime


class MessageOut(Contract):
    message_id: str
    role: MessageRole
    text: str
    created_at: datetime


# ---- agents, models and settings


class AgentOut(Contract):
    agent_id: str
    alias: str | None
    role: str


class ModelOut(Contract):
    provider_id: str
    model_id: str
    roles: tuple[str, ...]  # tier-table placement: agent IDs, plus "default"
    registered: bool  # installed and registered at startup
    provider_status: ProviderHealthStatus
    generating: bool  # holds the heavy-generation lease now
    context_limit_tokens: int | None


class ModelRef(Contract):
    provider_id: str
    model_id: str


class WorkspaceOut(Contract):
    root: str | None
    status: WorkspaceStatus
    updated_at: datetime | None


class ApiAddress(Contract):
    host: str
    port: int


class SettingsOut(Contract):
    workspace: WorkspaceOut
    # PHX-108 (ADR-111): writable; the desktop applies it to new missions.
    default_mission_path: MissionPathChoice
    # Read-only in v1.
    model_tiers: dict[str, tuple[ModelRef, ...]]
    default_tier: tuple[ModelRef, ...]
    retention_days: int
    api: ApiAddress


class SettingsPatch(Contract):
    """At least one writable setting; anything else is refused."""

    workspace_root: (
        Annotated[
            str,
            StringConstraints(
                strict=True, min_length=1, max_length=MAX_WORKSPACE_CHARS
            ),
        ]
        | None
    ) = None
    default_mission_path: MissionPathChoice | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if self.workspace_root is None and self.default_mission_path is None:
            raise ValueError("no setting given")
        return self


# ---- PHX-108 onboarding and capability flags


class RuntimeCheck(Contract):
    provider_id: str
    display_name: str
    state: RuntimeState


class ModelCheck(Contract):
    provider_id: str
    model_id: str
    required: bool
    registered: bool
    install_command: str | None  # only while the model is not registered


class StorageCheck(Contract):
    data_dir: str
    free_bytes: int | None
    database_ready: bool


class OnboardingOut(Contract):
    complete: bool
    runtimes: tuple[RuntimeCheck, ...]
    models: tuple[ModelCheck, ...]
    storage: StorageCheck
    workspace: WorkspaceOut


class CapabilityOut(Contract):
    capability_id: CapabilityId
    enabled: bool
    ticket: str
    agent_id: str


class CapabilitiesOut(Contract):
    capabilities: tuple[CapabilityOut, ...]
