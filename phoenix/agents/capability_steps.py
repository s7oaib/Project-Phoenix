"""PHX-113 permissioned media and document steps for the PHOENIX mission runtime.

An opt-in extension of PHX-046's document step (ADR-140). With capability tools
the runtime gives a documents step (``document_analyst``) PHX-046's read-only
filesystem operations plus ``read_document`` (P1: parse a workspace PDF, DOCX,
Markdown or CSV file) and ``create_document`` (P2: exclusively create a new
Markdown, DOCX or CSV file; never overwrite), and a media step (``media``)
Spotify and YouTube search (P1) and play, pause and open (P2). Each step makes
exactly one tool call between two generations, as in PHX-046:

1. The specialist only proposes one ``{"tool_request": ...}`` (structured).
2. PHOENIX validates the proposal against a closed allowlist and builds the
   call and ``PermissionRequest`` itself. The tool decides through
   PermissionEngine; nothing runs unless it returns ALLOWED. Grants come only
   from the runtime caller (INV-003): P1 SESSION or PERSISTENT, P2 SESSION
   only, each for one exact resource. APPROVAL_REQUIRED ends the mission as
   ``waiting_permission`` with the pending request and no effect.
3. The answer turn sees the result only as guarded ``TOOL_OUTPUT`` data
   (INV-017): parsed document text and third-party media metadata are
   untrusted. LEVI checks the tool evidence deterministically first (call,
   request, grant, ALLOWED, success, effect) and only then asks its model,
   grounded in the same guarded item (INV-008).

Filesystem operations keep PHX-046's evidence, records and LEVI responders.
The prompt text is part of ``phoenix-prompts-v5`` (``prompts``; PHX-120).
Traces hold IDs, statuses, hashes and lengths only. No retries (PHX-109 R10).
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated, Literal, cast
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from phoenix.agents import prompts
from phoenix.agents.handoff import HandoffEnvelope
from phoenix.agents.mission_events import MissionEvents, elapsed_ms
from phoenix.agents.responders import (
    ModelBinding,
    ModelVerificationResponder,
    _generate,
    parse_json_object,
)
from phoenix.agents.router import RoutingDomain
from phoenix.agents.tool_steps import (
    DOCUMENT_ACTOR_ID,
    DOCUMENT_OPERATIONS,
    DOCUMENT_TOOL_IDS,
    DOCUMENT_TOOL_REASON,
    MAX_LIST_ENTRIES,
    DocumentVerificationResponder,
    ToolCallRecord,
    ToolEvidence,
    ToolEvidenceVerificationResponder,
    ToolPermissionRequired,
    ToolStepFailed,
    ToolStepRejected,
    _guarded,
    _Item,
    _model_checks,
    _rendered,
    _tool_output_item,
    document_grants,
)
from phoenix.agents.verifier import (
    VerificationCheck,
    VerificationDraft,
    VerificationRequest,
    VerificationResponder,
)
from phoenix.core.events import EventType
from phoenix.models.provider import Identifier, NonnegativeInt
from phoenix.security.permissions import (
    DecisionReason,
    GrantDuration,
    PermissionDecision,
    PermissionGrant,
    PermissionRequest,
    PermissionStatus,
)
from phoenix.security.prompt_injection import (
    PromptInjectionGuard,
    UntrustedContentOrigin,
)
from phoenix.tools.document_files import (
    DocumentFileCall,
    DocumentFileInputError,
    DocumentFileOperation,
    DocumentFileResult,
    DocumentFileTool,
    DocumentSectionSpec,
)
from phoenix.tools.filesystem import (
    FilesystemCall,
    FilesystemInputError,
    FilesystemOperation,
    FilesystemTool,
    FilesystemToolResult,
)
from phoenix.tools.registry import ToolResultStatus, ToolRiskLevel
from phoenix.tools.spotify import (
    SpotifyCall,
    SpotifyError,
    SpotifyOperation,
    SpotifyTool,
    SpotifyToolResult,
)
from phoenix.tools.youtube import (
    YouTubeCall,
    YouTubeError,
    YouTubeOperation,
    YouTubeTool,
    YouTubeToolResult,
)

MEDIA_ACTOR_ID = "media"
# Fixed so caller grants can match exactly; never derived from model text.
CAPABILITY_DOCUMENT_REASON = "PHOENIX document step: workspace document read or create"
MEDIA_TOOL_REASON = "PHOENIX media step: Spotify or YouTube control"

# tool_id -> (actor, reason, risk). Closed: nothing else is reachable.
CAPABILITY_TOOLS: dict[str, tuple[str, str, ToolRiskLevel]] = {
    "document.read": (DOCUMENT_ACTOR_ID, CAPABILITY_DOCUMENT_REASON, ToolRiskLevel.P1),
    "document.create": (
        DOCUMENT_ACTOR_ID,
        CAPABILITY_DOCUMENT_REASON,
        ToolRiskLevel.P2,
    ),
    "spotify.search": (MEDIA_ACTOR_ID, MEDIA_TOOL_REASON, ToolRiskLevel.P1),
    "spotify.play": (MEDIA_ACTOR_ID, MEDIA_TOOL_REASON, ToolRiskLevel.P2),
    "spotify.pause": (MEDIA_ACTOR_ID, MEDIA_TOOL_REASON, ToolRiskLevel.P2),
    "youtube.search": (MEDIA_ACTOR_ID, MEDIA_TOOL_REASON, ToolRiskLevel.P1),
    "youtube.cue": (MEDIA_ACTOR_ID, MEDIA_TOOL_REASON, ToolRiskLevel.P2),
    "youtube.play": (MEDIA_ACTOR_ID, MEDIA_TOOL_REASON, ToolRiskLevel.P2),
    "youtube.pause": (MEDIA_ACTOR_ID, MEDIA_TOOL_REASON, ToolRiskLevel.P2),
}
CAPABILITY_TOOL_IDS: tuple[str, ...] = tuple(CAPABILITY_TOOLS)
# Event "operation" values: the tools' own actions (mission_events mirrors it).
CAPABILITY_ACTIONS: frozenset[str] = frozenset(
    tool_id.split(".", 1)[1] for tool_id in CAPABILITY_TOOLS
)
_SPOTIFY = {
    "search": SpotifyOperation.SEARCH,
    "play": SpotifyOperation.PLAY,
    "pause": SpotifyOperation.PAUSE,
}
_YOUTUBE = {
    "search": YouTubeOperation.SEARCH,
    "open": YouTubeOperation.CUE,
    "play": YouTubeOperation.PLAY,
    "pause": YouTubeOperation.PAUSE,
}


def capability_grants(grants: object) -> tuple[PermissionGrant, ...]:
    """Caller grants for PHX-046 and PHX-113 steps, exact and unique.

    A PHX-046 document grant passes unchanged. A PHX-113 grant names a closed
    tool, its own action, actor, fixed reason and risk; P1 may be SESSION or
    PERSISTENT, P2 only SESSION (ADR-141).
    """
    if type(grants) not in (tuple, list):
        raise ValueError("Grants require an ordered sequence")
    accepted: list[PermissionGrant] = []
    for grant in cast(Sequence[object], grants):
        if type(grant) is not PermissionGrant:
            raise ValueError("Expected PermissionGrant instances")
        grant = PermissionGrant.model_validate(grant)
        if grant.tool_id in DOCUMENT_TOOL_IDS:
            accepted.extend(document_grants([grant]))
            continue
        scope = CAPABILITY_TOOLS.get(grant.tool_id)
        if scope is None:
            raise ValueError("Grant is outside the capability step scope")
        actor, reason, risk = scope
        durations = (
            (GrantDuration.SESSION, GrantDuration.PERSISTENT)
            if risk == ToolRiskLevel.P1
            else (GrantDuration.SESSION,)
        )
        if (
            grant.actor_id != actor
            or grant.reason != reason
            or grant.risk_level != risk
            or grant.duration not in durations
            or grant.action != grant.tool_id.split(".", 1)[1]
        ):
            raise ValueError("Grant is outside the capability step scope")
        accepted.append(grant)
    ids = [grant.grant_id for grant in accepted]
    scopes = [
        (grant.tool_id, grant.action, grant.resource, grant.duration, grant.session_id)
        for grant in accepted
    ]
    if len(set(ids)) != len(ids) or len(set(scopes)) != len(scopes):
        raise ValueError("Duplicate grant ID or effective scope")
    return tuple(accepted)


@dataclass(frozen=True)
class CapabilityTools:
    """The caller's tool instances; any may be absent (then unreachable)."""

    documents: DocumentFileTool | None = None
    spotify: SpotifyTool | None = None
    youtube: YouTubeTool | None = None

    def __post_init__(self) -> None:
        for value, kind in (
            (self.documents, DocumentFileTool),
            (self.spotify, SpotifyTool),
            (self.youtube, YouTubeTool),
        ):
            if value is not None and type(value) is not kind:
                raise TypeError("Expected the exact capability tool type")
        if self.documents is None and self.spotify is None and self.youtube is None:
            raise ValueError("At least one capability tool is required")


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


class CapabilityCallRecord(_Value):
    """Privacy-preserving evidence of one PHX-113 call: IDs, statuses, hashes."""

    step_id: Identifier
    call_id: Identifier
    request_id: Identifier
    tool_id: Identifier
    decision_status: PermissionStatus
    tool_status: ToolResultStatus
    attempted: bool = Field(strict=True)
    effect_applied: bool = Field(strict=True)
    output_chars: NonnegativeInt | None = None
    output_sha256: str | None = Field(
        default=None, strict=True, pattern=r"^[0-9a-f]{64}$"
    )

    @field_validator("tool_id")
    @classmethod
    def closed_tool(cls, value: str) -> str:
        if value not in CAPABILITY_TOOLS:
            raise ValueError("Not a PHX-113 capability tool")
        return value


CapabilityResult = DocumentFileResult | SpotifyToolResult | YouTubeToolResult


@dataclass(frozen=True)
class CapabilityEvidence:
    """What PHOENIX issued and what the tool returned, for LEVI's correlation."""

    call_id: str
    tool_id: str
    request: PermissionRequest
    result: CapabilityResult
    relative_path: str | None = None


class _WireModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


_Short = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]


class _SectionWire(_WireModel):
    heading: _Short
    paragraphs: list[str] = Field(min_length=1, max_length=50)


class _DocumentRequestWire(_WireModel):
    operation: _Short
    relative_path: _Short
    title: _Short | None = None
    sections: list[_SectionWire] | None = Field(default=None, max_length=20)
    rows: list[list[str]] | None = Field(default=None, max_length=1000)


class _DocumentEnvelope(_WireModel):
    tool_request: _DocumentRequestWire


class _MediaRequestWire(_WireModel):
    service: Literal["spotify", "youtube"]
    operation: _Short
    query: _Short | None = None
    media_uri: _Short | None = None
    video_ref: _Short | None = None


class _MediaEnvelope(_WireModel):
    tool_request: _MediaRequestWire


@dataclass(frozen=True)
class _Prepared:
    """One PHOENIX-built call, before PermissionEngine sees it."""

    call_id: str
    request_id: str
    tool_id: str
    action: str
    resource: str
    risk: ToolRiskLevel
    actor_id: str
    reason: str
    run: Callable[[PermissionRequest, tuple[PermissionGrant, ...]], Awaitable[object]]
    relative_path: str | None = None
    filesystem: FilesystemCall | None = None


LeviFactory = Callable[[ModelBinding], VerificationResponder]


class CapabilityStepRunner:
    """One document or media step: propose, permission-check and run, answer."""

    def __init__(
        self,
        tools: CapabilityTools,
        guard: PromptInjectionGuard,
        *,
        filesystem: FilesystemTool | None,
        max_read_bytes: int,
    ) -> None:
        if type(tools) is not CapabilityTools:
            raise TypeError("Expected CapabilityTools")
        if filesystem is not None and type(filesystem) is not FilesystemTool:
            raise TypeError("Expected a FilesystemTool")
        self._tools = tools
        self._guard = guard
        self._filesystem = filesystem
        self._max_read_bytes = max_read_bytes

    @property
    def domains(self) -> tuple[RoutingDomain, ...]:
        """Documents with the document tool; media with a media tool.

        The assignment prompt must describe every allowed domain, so MEDIA is
        offered only when ``prompts.DOMAIN_DESCRIPTIONS`` describes it.
        """
        found: list[RoutingDomain] = []
        if self._tools.documents is not None:
            found.append(RoutingDomain.DOCUMENTS)
        if (
            self._tools.spotify is not None or self._tools.youtube is not None
        ) and RoutingDomain.MEDIA.value in prompts.DOMAIN_DESCRIPTIONS:
            found.append(RoutingDomain.MEDIA)
        return tuple(found)

    def handles(self, domain: RoutingDomain) -> bool:
        return domain in self.domains

    def _document_operations(self) -> tuple[str, ...]:
        found = (
            tuple(operation.value for operation in DOCUMENT_OPERATIONS)
            if self._filesystem is not None
            else ()
        )
        return (*found, "read_document", "create_document")

    def tool_ids(self, domain: RoutingDomain) -> tuple[str, ...]:
        """The step's handoff permission scope (descriptive; never a grant)."""
        if domain == RoutingDomain.DOCUMENTS:
            return (
                *(DOCUMENT_TOOL_IDS if self._filesystem is not None else ()),
                "document.read",
                "document.create",
            )
        return tuple(
            tool_id
            for tool_id in CAPABILITY_TOOL_IDS
            if (tool_id.startswith("spotify.") and self._tools.spotify is not None)
            or (tool_id.startswith("youtube.") and self._tools.youtube is not None)
        )

    @staticmethod
    def role(domain: RoutingDomain) -> str:
        return "document" if domain == RoutingDomain.DOCUMENTS else "media"

    @staticmethod
    def expected_output(domain: RoutingDomain) -> str:
        return (
            prompts.DOCUMENT_CAPABILITY_EXPECTED_OUTPUT
            if domain == RoutingDomain.DOCUMENTS
            else prompts.MEDIA_EXPECTED_OUTPUT
        )

    @staticmethod
    def completion_rule(domain: RoutingDomain) -> str:
        return (
            prompts.DOCUMENT_CAPABILITY_COMPLETION_RULE
            if domain == RoutingDomain.DOCUMENTS
            else prompts.MEDIA_COMPLETION_RULE
        )

    def _system(self, domain: RoutingDomain) -> tuple[str, str, str]:
        if domain == RoutingDomain.DOCUMENTS:
            lines = "\n".join(
                prompts.DOCUMENT_OPERATION_LINES[operation]
                for operation in self._document_operations()
            )
            return (
                "document_capability",
                prompts.DOCUMENT_CAPABILITY_SYSTEM.replace(
                    prompts.OPERATIONS_PLACEHOLDER, lines
                ),
                prompts.DOCUMENT_CAPABILITY_TASK,
            )
        services = [
            prompts.MEDIA_SERVICE_LINES[name]
            for name, tool in (
                ("spotify", self._tools.spotify),
                ("youtube", self._tools.youtube),
            )
            if tool is not None
        ]
        return (
            "media_specialist",
            prompts.MEDIA_SPECIALIST_SYSTEM.replace(
                prompts.SERVICES_PLACEHOLDER, "\n".join(services)
            ),
            prompts.MEDIA_TASK,
        )

    # ---- PHOENIX builds the call; a proposal outside the allowlist runs nothing.

    def _document_call(self, wire: _DocumentRequestWire) -> _Prepared:
        operation = wire.operation
        if operation not in self._document_operations():
            raise ToolStepRejected("Operation is not on the document allowlist")
        if operation in ("read_document", "create_document"):
            tool = self._tools.documents
            assert tool is not None  # documents domain requires the tool
            create = operation == "create_document"
            if not create and (wire.title, wire.sections, wire.rows) != (
                None,
                None,
                None,
            ):
                raise ToolStepRejected("A read takes a path only")
            try:
                call = DocumentFileCall(
                    operation=DocumentFileOperation.CREATE
                    if create
                    else DocumentFileOperation.READ,
                    relative_path=wire.relative_path,
                    title=wire.title,
                    sections=None
                    if wire.sections is None
                    else tuple(
                        DocumentSectionSpec(
                            heading=section.heading,
                            paragraphs=tuple(section.paragraphs),
                        )
                        for section in wire.sections
                    ),
                    rows=None
                    if wire.rows is None
                    else tuple(tuple(row) for row in wire.rows),
                )
                intent = tool.describe(call)
            except (ValidationError, ValueError):
                raise ToolStepRejected("Invalid workspace document request") from None
            expected = "document.create" if create else "document.read"
            if intent.tool_id != expected:
                raise ToolStepRejected("Operation does not match its tool")
            actor, reason, risk = CAPABILITY_TOOLS[expected]
            if intent.risk_level != risk:
                raise ToolStepRejected("Operation risk disagrees with its scope")

            def run(
                request: PermissionRequest, grants: tuple[PermissionGrant, ...]
            ) -> Awaitable[object]:
                return tool.execute(call, request, grants=grants)

            return _Prepared(
                call_id=call.call_id,
                request_id=str(uuid4()),
                tool_id=intent.tool_id,
                action=intent.action,
                resource=intent.resource,
                risk=intent.risk_level,
                actor_id=actor,
                reason=reason,
                run=run,
                relative_path=call.relative_path,
            )
        # PHX-046's read-only filesystem operations, built exactly as there.
        filesystem = self._filesystem
        assert filesystem is not None
        if (wire.title, wire.sections, wire.rows) != (None, None, None):
            raise ToolStepRejected("Filesystem reads take a path only")
        fields: dict[str, object] = {
            "operation": FilesystemOperation(operation),
            "relative_path": wire.relative_path,
        }
        if operation == FilesystemOperation.READ_TEXT.value:
            fields["max_bytes"] = self._max_read_bytes
        elif operation == FilesystemOperation.LIST_DIRECTORY.value:
            fields["max_entries"] = MAX_LIST_ENTRIES
        try:
            fs_call = FilesystemCall.model_validate(fields)
            fs_intent = filesystem.describe(fs_call)
        except (ValidationError, FilesystemInputError):
            raise ToolStepRejected("Invalid workspace request") from None
        if (
            fs_intent.risk_level != ToolRiskLevel.P1
            or fs_intent.tool_id not in DOCUMENT_TOOL_IDS
        ):
            raise ToolStepRejected("Operation is not read-only P1")

        def run_filesystem(
            request: PermissionRequest, grants: tuple[PermissionGrant, ...]
        ) -> Awaitable[object]:
            # Synchronous, bounded and read-only, as in PHX-046.
            return asyncio.to_thread(
                filesystem.execute, fs_call, request, grants=grants
            )

        return _Prepared(
            call_id=fs_call.call_id,
            request_id=str(uuid4()),
            tool_id=fs_intent.tool_id,
            action=fs_intent.action,
            resource=fs_intent.resource,
            risk=fs_intent.risk_level,
            actor_id=DOCUMENT_ACTOR_ID,
            reason=DOCUMENT_TOOL_REASON,
            run=run_filesystem,
            filesystem=fs_call,
        )

    def _media_call(self, wire: _MediaRequestWire) -> _Prepared:
        # Spotify and YouTube require the request ID to equal the call ID.
        identity = str(uuid4())
        run: Callable[
            [PermissionRequest, tuple[PermissionGrant, ...]], Awaitable[object]
        ]
        try:
            if wire.service == "spotify":
                spotify = self._tools.spotify
                operation = _SPOTIFY.get(wire.operation)
                if spotify is None or operation is None or wire.video_ref is not None:
                    raise ToolStepRejected("Not an allowed Spotify request")
                spotify_call = SpotifyCall(
                    call_id=identity,
                    operation=operation,
                    query=wire.query,
                    media_uri=wire.media_uri,
                )
                intent = spotify.describe(spotify_call)
                described = (
                    intent.tool_id,
                    intent.action,
                    intent.resource,
                    intent.risk_level,
                )

                def run_spotify(
                    request: PermissionRequest, grants: tuple[PermissionGrant, ...]
                ) -> Awaitable[object]:
                    assert spotify is not None
                    return spotify.execute(spotify_call, request, grants=grants)

                run = run_spotify
            else:
                youtube = self._tools.youtube
                yt_operation = _YOUTUBE.get(wire.operation)
                if (
                    youtube is None
                    or yt_operation is None
                    or wire.media_uri is not None
                ):
                    raise ToolStepRejected("Not an allowed YouTube request")
                youtube_call = YouTubeCall(
                    call_id=identity,
                    operation=yt_operation,
                    query=wire.query,
                    video_ref=wire.video_ref,
                )
                yt_intent = youtube.describe(youtube_call)
                described = (
                    yt_intent.tool_id,
                    yt_intent.action,
                    yt_intent.resource,
                    yt_intent.risk_level,
                )

                def run_youtube(
                    request: PermissionRequest, grants: tuple[PermissionGrant, ...]
                ) -> Awaitable[object]:
                    assert youtube is not None
                    return youtube.execute(youtube_call, request, grants=grants)

                run = run_youtube
        except (SpotifyError, YouTubeError, ValidationError, ValueError):
            raise ToolStepRejected("Invalid media request") from None
        tool_id, action, resource, risk = described
        scope = CAPABILITY_TOOLS.get(tool_id)
        if scope is None or risk != scope[2]:
            raise ToolStepRejected("Media operation is outside its scope")
        return _Prepared(
            call_id=identity,
            request_id=identity,
            tool_id=tool_id,
            action=action,
            resource=resource,
            risk=risk,
            actor_id=scope[0],
            reason=scope[1],
            run=run,
        )

    async def perform(
        self,
        binding: ModelBinding,
        handoff: HandoffEnvelope,
        domain: RoutingDomain,
        *,
        user_request: str,
        evidence: tuple[tuple[str, str], ...],
        grants: tuple[PermissionGrant, ...],
        session_id: str | None,
        record_tool: Callable[[ToolCallRecord], None],
        record_capability: Callable[[CapabilityCallRecord], None],
        events: MissionEvents | None = None,
    ) -> tuple[str, LeviFactory]:
        """Returns the step candidate and LEVI's responder factory for it."""
        if not self.handles(domain):
            raise ToolStepRejected("Domain is not a capability domain")
        model = UntrustedContentOrigin.MODEL_OUTPUT
        step_id = handoff.step_id
        actor = (
            DOCUMENT_ACTOR_ID if domain == RoutingDomain.DOCUMENTS else (MEDIA_ACTOR_ID)
        )

        async def emit(event_type: EventType, **payload: str | int) -> None:
            if events is not None:
                await events.emit(
                    event_type, agent_id=actor, step_id=step_id, **payload
                )

        objective: _Item = (
            prompts.OBJECTIVE_ITEM,
            model,
            prompts.OBJECTIVE_ITEM,
            "output",
            handoff.objective,
        )
        prior = tuple(
            (identity, model, identity, "output", text) for identity, text in evidence
        )
        fixed = (
            f"Original request:\n{user_request}",
            f"Expected output:\n{handoff.expected_output_schema}",
        )
        boundary = f"{handoff.task_id}:{handoff.step_id}"
        role, system, task = self._system(domain)
        proposal = await _generate(
            binding,
            role,
            system,
            "\n\n".join(
                (
                    *fixed,
                    _guarded(
                        self._guard, f"{boundary}:tool", task, (objective, *prior)
                    ),
                )
            ),
        )
        try:
            if domain == RoutingDomain.DOCUMENTS:
                prepared = self._document_call(
                    parse_json_object(proposal, _DocumentEnvelope).tool_request
                )
            else:
                prepared = self._media_call(
                    parse_json_object(proposal, _MediaEnvelope).tool_request
                )
        except ToolStepRejected:
            await emit(EventType.TOOL_FAILED, code="tool_rejected")
            raise
        request = PermissionRequest(
            request_id=prepared.request_id,
            actor_id=prepared.actor_id,
            tool_id=prepared.tool_id,
            action=prepared.action,
            resource=prepared.resource,
            reason=prepared.reason,
            requested_at=datetime.now(UTC),
            task_id=handoff.task_id,
            session_id=session_id,
        )
        ids = {"tool_id": prepared.tool_id, "call_id": prepared.call_id}
        await emit(
            EventType.TOOL_REQUESTED,
            **ids,
            operation=prepared.action,
            permission_request_id=request.request_id,
        )
        await emit(EventType.TOOL_STARTED, **ids)
        clock = time.monotonic()
        try:
            raw = await prepared.run(request, grants)
        except BaseException as interrupted:
            cancelled = isinstance(interrupted, asyncio.CancelledError)
            await emit(
                EventType.TOOL_FAILED,
                **ids,
                code="cancelled" if cancelled else "tool_failed",
                duration_ms=elapsed_ms(clock),
            )
            if cancelled or not isinstance(
                interrupted,
                (SpotifyError, YouTubeError, DocumentFileInputError),
            ):
                raise
            # A tool refusing PHOENIX's own evidence ran nothing: explicit failure.
            raise ToolStepFailed("Capability tool refused the request") from None
        fs_result: FilesystemToolResult | None = None
        capability: CapabilityResult | None = None
        if prepared.filesystem is not None:
            fs_result = FilesystemToolResult.model_validate(raw)
            decision = fs_result.permission_decision
            status, attempted, effect = (
                fs_result.status,
                fs_result.attempted,
                fs_result.effect_applied,
            )
        else:
            capability = _validated_result(raw)
            decision = capability.permission_decision
            status, attempted, effect = _shape(capability)
        allowed = decision.status
        content: tuple[str, str] | None = None
        if allowed == PermissionStatus.ALLOWED and status == ToolResultStatus.SUCCESS:
            if fs_result is not None:
                content = _rendered(fs_result) if fs_result.output is not None else None
            elif capability is not None:
                content = _render(capability, prepared.relative_path)
        digest = (
            None if content is None else sha256(content[1].encode("utf-8")).hexdigest()
        )
        if fs_result is not None:
            assert prepared.filesystem is not None
            record_tool(
                ToolCallRecord(
                    step_id=step_id,
                    call_id=prepared.call_id,
                    request_id=request.request_id,
                    tool_id=prepared.tool_id,
                    operation=prepared.filesystem.operation,
                    decision_status=allowed,
                    tool_status=status,
                    attempted=attempted,
                    effect_applied=effect,
                    output_chars=None if content is None else len(content[1]),
                    output_sha256=digest,
                )
            )
        else:
            record_capability(
                CapabilityCallRecord(
                    step_id=step_id,
                    call_id=prepared.call_id,
                    request_id=request.request_id,
                    tool_id=prepared.tool_id,
                    decision_status=allowed,
                    tool_status=status,
                    attempted=attempted,
                    effect_applied=effect,
                    output_chars=None if content is None else len(content[1]),
                    output_sha256=digest,
                )
            )
        ended = {**ids, "tool_status": status.value}
        if allowed == PermissionStatus.APPROVAL_REQUIRED:
            await emit(
                EventType.PERMISSION_REQUESTED,
                **ids,
                permission_request_id=request.request_id,
                risk_level=prepared.risk.value,
            )
            raise ToolPermissionRequired(request)
        failure: tuple[str, Exception] | None = None
        if allowed != PermissionStatus.ALLOWED:
            failure = "tool_rejected", ToolStepRejected("Permission was not granted")
        elif content is None:
            failure = "tool_failed", ToolStepFailed("Capability request failed")
        elif len(content[1]) > binding.max_output_chars:
            failure = (
                "tool_failed",
                ToolStepFailed("Tool output exceeds the configured limit"),
            )
        if failure is not None:
            code, error = failure
            await emit(
                EventType.TOOL_FAILED,
                **ended,
                code=code,
                decision=allowed.value,
                duration_ms=elapsed_ms(clock),
            )
            raise error
        assert content is not None
        field, text = content
        await emit(EventType.TOOL_COMPLETED, **ended, duration_ms=elapsed_ms(clock))
        if fs_result is not None:
            assert prepared.filesystem is not None
            item = _tool_output_item(prepared.filesystem, field, text)
        else:
            item = (
                prompts.TOOL_OUTPUT_ITEM,
                UntrustedContentOrigin.TOOL_OUTPUT,
                prepared.call_id,
                field,
                text,
            )
        answer_role, answer_system = (
            (
                "document_capability_answer",
                prompts.DOCUMENT_CAPABILITY_ANSWER_SYSTEM,
            )
            if domain == RoutingDomain.DOCUMENTS
            else ("media_answer", prompts.MEDIA_ANSWER_SYSTEM)
        )
        answer = await _generate(
            binding,
            answer_role,
            answer_system,
            "\n\n".join(
                (
                    *fixed,
                    _guarded(
                        self._guard,
                        boundary,
                        prompts.CAPABILITY_ANSWER_TASK,
                        (objective, item, *prior),
                    ),
                )
            ),
        )
        guard = self._guard
        if fs_result is not None:
            assert prepared.filesystem is not None
            fs_evidence = ToolEvidence(
                call=prepared.filesystem, request=request, result=fs_result
            )

            def filesystem_levi(verifier: ModelBinding) -> VerificationResponder:
                # PHX-046/047: unchanged evidence check and grounded verifier.
                return ToolEvidenceVerificationResponder(
                    DocumentVerificationResponder(
                        verifier,
                        guard,
                        original_request=user_request,
                        evidence=fs_evidence,
                    ),
                    fs_evidence,
                    grants,
                )

            return answer, filesystem_levi
        assert capability is not None
        issued = CapabilityEvidence(
            call_id=prepared.call_id,
            tool_id=prepared.tool_id,
            request=request,
            result=capability,
            relative_path=prepared.relative_path,
        )

        def capability_levi(verifier: ModelBinding) -> VerificationResponder:
            return CapabilityEvidenceVerificationResponder(
                CapabilityVerificationResponder(
                    verifier, guard, original_request=user_request, evidence=issued
                ),
                issued,
                grants,
            )

        return answer, capability_levi


def _validated_result(raw: object) -> CapabilityResult:
    for kind in (DocumentFileResult, SpotifyToolResult, YouTubeToolResult):
        if type(raw) is kind:
            return kind.model_validate(raw)
    raise ToolStepFailed("Unexpected capability tool result")


def _shape(result: CapabilityResult) -> tuple[ToolResultStatus, bool, bool]:
    """(status, attempted, effect_applied) from any capability result."""
    if isinstance(result, DocumentFileResult):
        return result.status, result.attempted, result.effect_applied
    # Media commands act outside PHOENIX; acceptance is reported, not an effect.
    return result.status, result.attempted, False


def _render(result: CapabilityResult, relative_path: str | None) -> tuple[str, str]:
    """The tool output as (field name, text); content is never interpreted."""
    if isinstance(result, DocumentFileResult):
        output = result.output
        if output is None:
            raise ToolStepFailed("Workspace document request failed")
        if result.operation == DocumentFileOperation.READ:
            return "content", str(output["text"])
        return "output", json.dumps(
            {"relative_path": relative_path, **dict(output)},
            ensure_ascii=False,
            sort_keys=True,
        )
    if result.response is None:
        raise ToolStepFailed("Media request failed")
    return "output", json.dumps(
        result.response.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )


def _issued_item(evidence: CapabilityEvidence) -> _Item:
    field, text = _render(evidence.result, evidence.relative_path)
    return (
        prompts.TOOL_OUTPUT_ITEM,
        UntrustedContentOrigin.TOOL_OUTPUT,
        evidence.call_id,
        field,
        text,
    )


def _correlates(evidence: CapabilityEvidence, grant_ids: frozenset[str]) -> bool:
    """Deterministic: the result answers PHOENIX's own call, allowed by a grant."""
    request, result = evidence.request, evidence.result
    scope = CAPABILITY_TOOLS.get(evidence.tool_id)
    if scope is None:
        return False
    actor, _, risk = scope
    decision: PermissionDecision = result.permission_decision
    common = (
        decision.request_id == request.request_id
        and decision.actor_id == request.actor_id == actor
        and decision.tool_id == request.tool_id == evidence.tool_id
        and decision.risk_level == risk
        and decision.status == PermissionStatus.ALLOWED
        and decision.reason_code == DecisionReason.MATCHING_GRANT
        and decision.matched_grant_id in grant_ids
        and result.attempted is True
        and result.status == ToolResultStatus.SUCCESS
    )
    if not common:
        return False
    if isinstance(result, DocumentFileResult):
        create = result.operation == DocumentFileOperation.CREATE
        return (
            result.call_id == evidence.call_id
            and result.tool_id == evidence.tool_id
            and result.resource == request.resource
            and result.effect_applied is create
            and result.output is not None
            and (not create or result.output.get("verified") is True)
        )
    intent, response = result.intent, result.response
    if (
        response is None
        or intent.call_id != evidence.call_id
        or intent.tool_id != evidence.tool_id
        or intent.resource != request.resource
        or result.permission_scope.request_id != request.request_id
        or response.operation is not intent.operation
    ):
        return False
    if risk == ToolRiskLevel.P1:
        return True
    if isinstance(result, SpotifyToolResult):
        return result.response is not None and result.response.command_accepted
    player = result.response.player if result.response is not None else None
    return player is not None and player.command_accepted


class CapabilityVerificationResponder(ModelVerificationResponder):
    """LEVI's model verifier for one PHX-113 step, grounded in its tool output.

    The deterministic plain-text and size checks run first and the model is
    skipped if any fails. The model then sees the step's tool output only as
    the guarded TOOL_OUTPUT item the answer turn saw.
    """

    def __init__(
        self,
        binding: ModelBinding,
        guard: PromptInjectionGuard,
        *,
        original_request: str,
        evidence: CapabilityEvidence,
        max_checks: int = 6,
    ) -> None:
        super().__init__(
            binding,
            guard,
            original_request=original_request,
            plain_text_required=True,
            max_checks=max_checks,
        )
        self._evidence = evidence

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        checks = self._deterministic(request.candidate_output)
        if not all(check.passed for check in checks):
            return VerificationDraft(
                verification_id=request.verification_id, checks=checks
            )
        model = UntrustedContentOrigin.MODEL_OUTPUT
        system = prompts.CAPABILITY_VERIFICATION_SYSTEM.format(
            max_checks=self._max_checks
        )
        user = "\n\n".join(
            (
                f"Original request:\n{self._original_request}",
                f"Expected output:\n{request.expected_output_schema}",
                f"Completion rule:\n{request.completion_rule}",
                _guarded(
                    self._guard,
                    request.verification_id,
                    prompts.CAPABILITY_CANDIDATE_TASK,
                    (
                        (
                            prompts.OBJECTIVE_ITEM,
                            model,
                            prompts.OBJECTIVE_ITEM,
                            "output",
                            request.objective,
                        ),
                        _issued_item(self._evidence),
                        (
                            prompts.CANDIDATE_ITEM,
                            model,
                            prompts.CANDIDATE_ITEM,
                            "output",
                            request.candidate_output,
                        ),
                    ),
                ),
            )
        )
        return _model_checks(
            request,
            checks,
            await _generate(self._binding, "capability_verifier", system, user),
            self._max_checks,
        )


class CapabilityEvidenceVerificationResponder:
    """LEVI responder for PHX-113 steps: tool evidence first, then the model.

    A failed deterministic tool-evidence check is final: the model verifier is
    not consulted and cannot override it.
    """

    def __init__(
        self,
        inner: ModelVerificationResponder,
        evidence: CapabilityEvidence,
        grants: tuple[PermissionGrant, ...],
    ) -> None:
        self._inner = inner
        self._evidence = evidence
        self._grant_ids = frozenset(grant.grant_id for grant in grants)

    def _check(self) -> VerificationCheck:
        passed = _correlates(self._evidence, self._grant_ids)
        return VerificationCheck(
            check_id="deterministic.tool_evidence",
            criterion="Tool evidence correlates with the PHOENIX-issued call",
            passed=passed,
            evidence_summary="Allowed by a caller grant; the result correlates"
            if passed
            else "Tool evidence does not correlate or is not an allowed success",
        )

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        check = self._check()
        if not check.passed:
            return VerificationDraft(
                verification_id=request.verification_id, checks=(check,)
            )
        draft = await self._inner.verify(request)
        return VerificationDraft(
            verification_id=draft.verification_id, checks=(check, *draft.checks)
        )
