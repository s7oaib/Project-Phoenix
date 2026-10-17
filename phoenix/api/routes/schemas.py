"""PHX-104 request and response schemas (ADR-089).

Every schema forbids extra fields. Responses carry only the user's message
text, PHOENIX's final answer text, IDs, statuses, codes and timestamps, and a
pending permission's tool, action, resource path and risk so the user can
decide on it. Raw model output, tool output and file content are never part of
any schema.

Request bodies hold only what the user chooses. An approval names a duration;
the tool, action, resource and actor always come from the stored request
(INV-003), and a body that tries to set them is refused (``extra="forbid"``).
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import StringConstraints

from phoenix.agents.task_graph import TaskState
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


# ---- chat and missions


class ChatRequest(Contract):
    text: Annotated[
        str,
        StringConstraints(
            strict=True, min_length=1, max_length=MAX_CHAT_CHARS, pattern=r"\S"
        ),
    ]
    conversation_id: Uuid | None = None
    # Until PHX-105 triage decides, the caller chooses; "complex" plans steps
    # (including document reads when a workspace is set).
    path: Literal["direct", "complex"] = "direct"


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
    path: Literal["direct", "complex"]
    failure_code: str | None
    responder_error: str | None
    rerun_of: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class MissionDetail(MissionSummary):
    request_text: str | None
    answer_text: str | None
    rerun_id: str | None
    task_id: str | None
    task_states: tuple[TaskState, ...]
    permission_request: PermissionRequestOut | None


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
    # Read-only in v1.
    model_tiers: dict[str, tuple[ModelRef, ...]]
    default_tier: tuple[ModelRef, ...]
    retention_days: int
    api: ApiAddress


class SettingsPatch(Contract):
    workspace_root: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=MAX_WORKSPACE_CHARS),
    ]
