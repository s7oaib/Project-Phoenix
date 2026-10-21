"""PHX-104 chat, mission and conversation routes (ADR-089).

``POST /chat`` is asynchronous: it queues a mission through
``MissionService.submit`` and returns 202 with the mission and conversation IDs
at once. Progress arrives over ``WS /events``; the answer comes from
``GET /missions/{id}``. No request is held open for a mission. Without a
``path`` PHOENIX triages the request (PHX-105); missions report the path and
who decided it (``triage_outcome``). PHX-110: a completed mission's detail
lists the sources its answer cites (``sources``), read from its trace.
"""

import contextlib

from fastapi import APIRouter, Request, Response

from phoenix.agents.retrieval_steps import MissionCitation
from phoenix.agents.control_steps import ControlStepRunner
from phoenix.api.missions import CancelOutcome, MissionService
from phoenix.api.routes.common import (
    DEFAULT_PAGE,
    ApiError,
    Limit,
    Offset,
    Page,
    Uuid,
    UuidQuery,
    page,
    services,
)
from phoenix.api.routes.schemas import (
    ApprovalSummaryOut,
    CancelResult,
    ChatRequest,
    ConversationOut,
    KaidoOut,
    MessageOut,
    MissionAccepted,
    MissionDetail,
    MissionSummary,
    PermissionRequestOut,
    SourceOut,
)
from phoenix.persistence.missions import MissionRecord, PermissionRequestRecord
from phoenix.tools.registry import ToolRegistry

router = APIRouter()


def permission_out(
    record: PermissionRequestRecord,
    tools: ToolRegistry,
    control: ControlStepRunner | None = None,
) -> PermissionRequestOut:
    request = record.request
    tool = tools.get(request.tool_id)
    # PHX-112 (ADR-151): display only; approve and deny use the stored request.
    found = None if control is None else control.approval_summary(request.request_id)
    summary = (
        None
        if found is None or found.tool_id != request.tool_id
        else ApprovalSummaryOut.model_validate(
            found.model_dump(
                mode="json", exclude={"request_id", "tool_id", "risk_level"}
            )
        )
    )
    return PermissionRequestOut(
        request_id=request.request_id,
        mission_id=record.mission_id,
        status=record.status,
        tool_id=request.tool_id,
        action=request.action,
        resource=request.resource,
        risk_level=None if tool is None else tool.risk_level.value,
        requested_at=request.requested_at,
        decided_at=record.decided_at,
        summary=summary,
    )


def _summary_fields(record: MissionRecord) -> dict[str, object]:
    return {
        "mission_id": record.mission_id,
        "conversation_id": record.conversation_id,
        "status": record.status,
        "path": record.path,
        "triage_outcome": record.triage_outcome,
        "failure_code": record.failure_code,
        "responder_error": record.responder_error,
        "rerun_of": record.rerun_of,
        "created_at": record.created_at,
        "started_at": record.started_at,
        "finished_at": record.finished_at,
    }


def summary(record: MissionRecord) -> MissionSummary:
    return MissionSummary.model_validate(_summary_fields(record))


async def _detail(
    missions: MissionService,
    record: MissionRecord,
    tools: ToolRegistry,
    control: ControlStepRunner | None,
) -> MissionDetail:
    messages = {
        message.message_id: message.text
        for message in await missions.list_messages(record.conversation_id)
    }
    siblings = await missions.list_missions(record.conversation_id)
    rerun = next(
        (m.mission_id for m in siblings if m.rerun_of == record.mission_id), None
    )
    trace = await missions.get_trace(record.mission_id)
    pending = record.permission_request
    return MissionDetail.model_validate(
        {
            **_summary_fields(record),
            "request_text": messages.get(record.message_id),
            "answer_text": None
            if record.answer_message_id is None
            else messages.get(record.answer_message_id),
            "rerun_id": rerun,
            "task_id": None if trace is None else trace.task_id,
            "task_states": () if trace is None else trace.states,
            "permission_request": None
            if pending is None
            else permission_out(pending, tools, control),
            "kaido": None
            if trace is None or trace.kaido is None
            else KaidoOut.model_validate(
                trace.kaido.model_dump(
                    mode="json",
                    include={
                        "failsafe_id",
                        "trigger",
                        "scope",
                        "outcome",
                        "verified",
                        "recovery_id",
                    },
                )
            ),
            # PHX-110: only an answer has sources to show.
            "sources": ()
            if trace is None or record.answer_message_id is None
            else tuple(source_out(citation) for citation in trace.citations),
        }
    )


def source_out(citation: MissionCitation) -> SourceOut:
    return SourceOut(
        marker=citation.marker,
        kind=citation.source_kind,
        source_id=citation.source_id,
        document_id=citation.document_id,
        title=citation.title,
        page=citation.page,
        section=citation.section,
        chunk_index=citation.chunk_index,
        memory_type=None
        if citation.memory_type is None
        else citation.memory_type.value,
        label=citation.label,
    )


@router.post("/chat", status_code=202, response_model=MissionAccepted)
async def chat(body: ChatRequest, request: Request) -> MissionAccepted:
    missions = services(request).missions
    if body.conversation_id is not None:
        mission_id = await missions.submit(body.conversation_id, body.text, body.path)
        return MissionAccepted(
            mission_id=mission_id, conversation_id=body.conversation_id
        )
    conversation_id = await missions.create_conversation()
    try:
        mission_id = await missions.submit(conversation_id, body.text, body.path)
    except Exception:
        # Leave no empty conversation behind (best effort; the error stands).
        with contextlib.suppress(Exception):
            await missions.delete_conversation(conversation_id)
        raise
    return MissionAccepted(mission_id=mission_id, conversation_id=conversation_id)


@router.get("/missions", response_model=Page[MissionSummary])
async def list_missions(
    request: Request,
    conversation_id: UuidQuery = None,
    limit: Limit = DEFAULT_PAGE,
    offset: Offset = 0,
) -> Page[MissionSummary]:
    records = await services(request).missions.list_missions(conversation_id)
    return page([summary(record) for record in records], limit, offset)


@router.get("/missions/{mission_id}", response_model=MissionDetail)
async def get_mission(mission_id: Uuid, request: Request) -> MissionDetail:
    backend = services(request)
    record = await backend.missions.get_mission(mission_id)
    return await _detail(
        backend.missions, record, backend.tool_registry, backend.control_steps
    )


@router.post(
    "/missions/{mission_id}/cancel", status_code=202, response_model=CancelResult
)
async def cancel_mission(mission_id: Uuid, request: Request) -> CancelResult:
    outcome = await services(request).missions.cancel(mission_id)
    if outcome == CancelOutcome.NOT_CANCELLABLE:
        raise ApiError(409, "invalid_state")
    cancelled = outcome == CancelOutcome.CANCELLED
    return CancelResult(
        mission_id=mission_id, outcome="cancelled" if cancelled else "cancelling"
    )


@router.post(
    "/missions/{mission_id}/run-again", status_code=202, response_model=MissionAccepted
)
async def run_again(mission_id: Uuid, request: Request) -> MissionAccepted:
    missions = services(request).missions
    original = await missions.get_mission(mission_id)
    new_id = await missions.run_again(mission_id)
    return MissionAccepted(
        mission_id=new_id,
        conversation_id=original.conversation_id,
        rerun_of=mission_id,
    )


@router.get("/conversations", response_model=Page[ConversationOut])
async def list_conversations(
    request: Request, limit: Limit = DEFAULT_PAGE, offset: Offset = 0
) -> Page[ConversationOut]:
    records = await services(request).missions.list_conversations()
    items = [
        ConversationOut(
            conversation_id=record.conversation_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        for record in records
    ]
    return page(items, limit, offset)


@router.get(
    "/conversations/{conversation_id}/messages", response_model=Page[MessageOut]
)
async def list_messages(
    conversation_id: Uuid,
    request: Request,
    limit: Limit = DEFAULT_PAGE,
    offset: Offset = 0,
) -> Page[MessageOut]:
    records = await services(request).missions.list_messages(conversation_id)
    items = [
        MessageOut(
            message_id=record.message_id,
            role=record.role,
            text=record.text,
            created_at=record.created_at,
        )
        for record in records
    ]
    return page(items, limit, offset)


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: Uuid, request: Request) -> Response:
    await services(request).missions.delete_conversation(conversation_id)
    return Response(status_code=204)


@router.delete("/conversations", status_code=204)
async def clear_conversations(request: Request) -> Response:
    """Delete every conversation, message, mission and trace; grants stay."""
    await services(request).missions.clear_all()
    return Response(status_code=204)
