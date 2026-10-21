"""PHX-110 knowledge routes: uploads, memories and search (ADR-132, ADR-133).

Every route sits behind the PHX-101 middleware. The routes are thin;
``KnowledgeService`` owns the behaviour and its write policy.

- ``POST /files/upload?filename=...`` takes the raw file as the body (no
  multipart parser, so no new dependency), up to the path's own body cap, and
  returns 201 with the indexed document. ``GET /files`` lists uploads;
  ``DELETE /files/{document_id}`` removes one with its chunks and vectors.
- ``POST /memory`` stores one user note; ``GET /memory`` lists every memory
  (user-visible) and ``DELETE /memory/{memory_id}`` removes one with its vector.
- ``GET /memory/search?q=...`` searches memories and uploads and returns the
  matching text (the user's own data) with provenance.

Refusals use the error envelope with a closed code: ``knowledge_unavailable``
(503, no embedding backend or it failed), ``secret_rejected`` and
``invalid_upload`` (422), ``unsupported_format``/``media_type_mismatch`` (415),
``payload_too_large`` (413), ``knowledge_full`` (409) and ``not_found`` (404).
Nothing from the request is echoed.
"""

from collections.abc import Awaitable
from datetime import datetime
from typing import Annotated, Literal, TypeVar

from fastapi import APIRouter, Query, Request, Response
from pydantic import StringConstraints

from phoenix.api.knowledge import (
    MAX_MEMORY_CHARS,
    DocumentInfo,
    KnowledgeError,
    KnowledgeService,
    MemoryInfo,
)
from phoenix.api.routes.common import (
    DEFAULT_PAGE,
    ApiError,
    Contract,
    Limit,
    Offset,
    Page,
    Uuid,
    page,
    services,
)

router = APIRouter()
_T = TypeVar("_T")

Filename = Annotated[str, Query(min_length=1, max_length=255)]
SearchQuery = Annotated[str, Query(min_length=1, max_length=2000, pattern=r"\S")]
SearchLimit = Annotated[int, Query(ge=1, le=20)]


class DocumentOut(Contract):
    document_id: str
    filename: str
    media_type: str
    chunk_count: int
    indexed_at: datetime


class MemoryIn(Contract):
    content: Annotated[
        str,
        StringConstraints(
            strict=True, min_length=1, max_length=MAX_MEMORY_CHARS, pattern=r"\S"
        ),
    ]
    memory_type: Literal["user", "project", "semantic"] = "user"


class MemoryOut(Contract):
    memory_id: str
    memory_type: str
    scope: str
    content: str
    created_at: datetime


class SearchHitOut(Contract):
    kind: Literal["memory", "document_chunk"]
    source_id: str
    document_id: str | None
    title: str | None
    text: str
    score: float


class SearchOut(Contract):
    items: tuple[SearchHitOut, ...]


def _knowledge(request: Request) -> KnowledgeService:
    return services(request).knowledge


async def _call(operation: Awaitable[_T]) -> _T:
    try:
        return await operation
    except KnowledgeError as error:
        raise ApiError(error.status, error.code) from None


def _document(info: DocumentInfo) -> DocumentOut:
    return DocumentOut(
        document_id=info.document_id,
        filename=info.filename,
        media_type=info.media_type,
        chunk_count=info.chunk_count,
        indexed_at=info.indexed_at,
    )


def _memory(info: MemoryInfo) -> MemoryOut:
    return MemoryOut(
        memory_id=info.memory_id,
        memory_type=info.memory_type,
        scope=info.scope,
        content=info.content,
        created_at=info.created_at,
    )


@router.post("/files/upload", status_code=201, response_model=DocumentOut)
async def upload_file(filename: Filename, request: Request) -> DocumentOut:
    knowledge = _knowledge(request)
    data = await request.body()
    if not data:
        raise ApiError(422, "invalid_upload")
    declared = request.headers.get("content-type")
    media = None if declared is None else declared.split(";", 1)[0].strip() or None
    return _document(await _call(knowledge.upload(filename, data, media)))


@router.get("/files", response_model=Page[DocumentOut])
async def list_files(
    request: Request, limit: Limit = DEFAULT_PAGE, offset: Offset = 0
) -> Page[DocumentOut]:
    documents = await _knowledge(request).list_documents()
    return page([_document(item) for item in documents], limit, offset)


@router.delete("/files/{document_id}", status_code=204)
async def delete_file(document_id: Uuid, request: Request) -> Response:
    if not await _knowledge(request).delete_document(document_id):
        raise ApiError(404, "not_found")
    return Response(status_code=204)


@router.post("/memory", status_code=201, response_model=MemoryOut)
async def remember(body: MemoryIn, request: Request) -> MemoryOut:
    if not body.content.strip():
        raise ApiError(422, "invalid_request")
    knowledge = _knowledge(request)
    return _memory(await _call(knowledge.remember(body.content, body.memory_type)))


@router.get("/memory", response_model=Page[MemoryOut])
async def list_memory(
    request: Request, limit: Limit = DEFAULT_PAGE, offset: Offset = 0
) -> Page[MemoryOut]:
    memories = await _knowledge(request).list_memories()
    return page([_memory(item) for item in memories], limit, offset)


@router.get("/memory/search", response_model=SearchOut)
async def search_memory(
    q: SearchQuery, request: Request, limit: SearchLimit = 5
) -> SearchOut:
    hits = await _call(_knowledge(request).search(q, limit))
    return SearchOut(
        items=tuple(
            SearchHitOut(
                kind=hit.kind,
                source_id=hit.source_id,
                document_id=hit.document_id,
                title=hit.title,
                text=hit.text,
                score=hit.score,
            )
            for hit in hits
        )
    )


@router.delete("/memory/{memory_id}", status_code=204)
async def forget(memory_id: Uuid, request: Request) -> Response:
    if not await _knowledge(request).delete_memory(memory_id):
        raise ApiError(404, "not_found")
    return Response(status_code=204)
