"""PHX-110 user knowledge: uploads, memories, search and the mission source.

``KnowledgeService`` owns the user's knowledge base (ADR-132, ADR-133):

- **Uploads.** ``upload`` routes the bytes through the PHX-072 parsers,
  ingests them with the PHX-032 ``DocumentIngestionService`` (secret-bearing
  documents are refused), embeds every chunk with the configured embedding
  model and stores the vectors in the PHX-033 ``SqliteVectorStore``. If
  embedding fails, the document is removed again: nothing half-indexed stays.
- **Memory write policy.** Only the user writes memory, through ``remember``:
  missions and models never do. A memory is one short note (at most
  ``MAX_MEMORY_CHARS``) of type ``user``, ``project`` or ``semantic`` in the
  single ``user`` scope, with ``source_type="user_note"``, full confidence and
  no expiry. Text that looks like a credential is refused before it is
  embedded or stored (INV-018). Every memory is listed and deletable; deleting
  removes its vector too.
- **Search and missions.** ``search`` and the mission ``KnowledgeSource``
  (``available``, ``retrieve``, ``titles``) run the PHX-034
  ``RetrievalContextService`` over every indexed upload and the ``user`` memory
  scope, with fixed bounds.

Embeddings run under the model lifecycle's heavy lease, so they never overlap a
generation (INV-023). The ingestion and memory services publish their
``document.indexed`` and ``memory.created`` events to a private bus: the
PHX-103 WebSocket allowlist is unchanged. Writes are serialized by one lock.
"""

import asyncio
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from phoenix.agents.registry import AgentRegistry
from phoenix.api.body_limit import MAX_UPLOAD_BYTES as BODY_UPLOAD_BYTES
from phoenix.core.event_bus import EventBus
from phoenix.documents.parsers import (
    UploadDocument,
    UploadParserError,
    UploadParserErrorCode,
    UploadParserLimits,
    UploadRouter,
)
from phoenix.memory.schema import MemoryRecord, MemoryType
from phoenix.memory.service import (
    MemoryAdmissionDecision,
    MemoryAdmissionVerdict,
    MemorySecretRejectedError,
    MemoryServiceError,
    PuddingMemoryService,
)
from phoenix.models.embeddings import (
    EmbeddingProvider,
    EmbeddingRequest,
    EmbeddingResponse,
)
from phoenix.models.lifecycle import ModelLifecycleManager
from phoenix.persistence.documents import DocumentChunkRow, DocumentRow
from phoenix.persistence.embeddings import EmbeddingRecordRow
from phoenix.persistence.memory import MemoryRow
from phoenix.rag.ingestion import (
    DocumentAdmissionDecision,
    DocumentAdmissionVerdict,
    DocumentChunkRecord,
    DocumentIngestionError,
    DocumentIngestionLimits,
    DocumentIngestionService,
    DocumentLimitError,
    DocumentParserError,
    DocumentRecord,
    DocumentSecretRejectedError,
)
from phoenix.rag.retrieval import (
    ContextBudget,
    MemoryRankingPolicy,
    RetrievalContextResult,
    RetrievalContextService,
    RetrievalRequest,
)
from phoenix.rag.sqlite_vector_store import SqliteVectorStore
from phoenix.rag.vector_store import VectorRecord

EMBEDDING_MODEL_ID: Final = "embeddinggemma:300m"
MEMORY_SCOPE: Final = "user"
MEMORY_SOURCE_TYPE: Final = "user_note"
MEMORY_TYPES: Final = (MemoryType.USER, MemoryType.PROJECT, MemoryType.SEMANTIC)
MAX_MEMORY_CHARS: Final = 2000
MAX_UPLOAD_BYTES: Final = BODY_UPLOAD_BYTES
MAX_DOCUMENTS: Final = 200
MAX_MEMORIES: Final = 2000
# RetrievalContextService refuses more than 2,000 eligible sources per kind.
MAX_CHUNKS: Final = 2000
EMBED_BATCH: Final = 16
SNIPPET_CHARS: Final = 500

# Credential shapes refused from memory and uploads (INV-018). Signature
# fragments are split so this file is not itself a credential-looking artifact.
_SECRET_PATTERNS: Final = tuple(
    re.compile(pattern)
    for pattern in (
        r"-----BEGIN [A-Z ]*PRIVATE" r" KEY-----",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\bgh[pousr]_[A-Za-z0-9]{30,}",
        r"\bgithub_" r"pat_[A-Za-z0-9_]{20,}",
        r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
        r"\bsk-[A-Za-z0-9_-]{20,}",
        r"\bAIza[0-9A-Za-z_-]{35}",
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        r"(?i)\b(?:password|passwd|passphrase|secret|api[_ -]?key|access[_ -]?token"
        r"|auth[_ -]?token|private[_ -]?key|client[_ -]?secret)\b\s*(?:is\s*)?[:=]"
        r"\s*\S{6,}",
    )
)


def contains_secret(text: str) -> bool:
    """A credential-shaped string; a heuristic that can miss or over-match."""
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


class MemoryWritePolicy:
    """PUDDING admission: user notes in the user scope only, never secrets."""

    def evaluate(self, record: MemoryRecord) -> MemoryAdmissionDecision:
        if contains_secret(record.content):
            verdict = MemoryAdmissionVerdict.REJECTED_SECRET
        elif (
            record.scope != MEMORY_SCOPE
            or record.source_type != MEMORY_SOURCE_TYPE
            or record.type not in MEMORY_TYPES
            or len(record.content) > MAX_MEMORY_CHARS
        ):
            verdict = MemoryAdmissionVerdict.REJECTED_POLICY
        else:
            verdict = MemoryAdmissionVerdict.ACCEPTED
        return MemoryAdmissionDecision(memory_id=record.id, verdict=verdict)


class DocumentWritePolicy:
    """Ingestion admission: a document with a credential-shaped chunk is refused."""

    def evaluate(
        self, document: DocumentRecord, chunks: tuple[DocumentChunkRecord, ...]
    ) -> DocumentAdmissionDecision:
        secret = any(contains_secret(chunk.content) for chunk in chunks)
        return DocumentAdmissionDecision(
            document_id=document.id,
            verdict=DocumentAdmissionVerdict.REJECTED_SECRET
            if secret
            else DocumentAdmissionVerdict.ACCEPTED,
        )


class KnowledgeLimits(BaseModel):
    """Fixed bounds; the API exposes none of them as settings in v1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_size_chars: int = Field(default=1000, strict=True, ge=100, le=10_000)
    chunk_overlap_chars: int = Field(default=150, strict=True, ge=0, le=1000)
    max_chunks_per_document: int = Field(default=500, strict=True, ge=1, le=2000)
    max_parsed_chars: int = Field(default=500_000, strict=True, ge=1)
    candidate_limit_per_kind: int = Field(default=16, strict=True, ge=1, le=100)
    max_context_items: int = Field(default=6, strict=True, ge=1, le=20)
    max_context_chars: int = Field(default=6000, strict=True, ge=1, le=40_000)
    min_similarity: float = Field(default=0.35, ge=-1, le=1, allow_inf_nan=False)


class KnowledgeError(Exception):
    """A closed, safe refusal: an HTTP status and code, never input text."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class DocumentInfo:
    document_id: str
    filename: str
    media_type: str
    chunk_count: int
    indexed_at: datetime


@dataclass(frozen=True)
class MemoryInfo:
    memory_id: str
    memory_type: str
    scope: str
    content: str
    created_at: datetime


@dataclass(frozen=True)
class SearchHit:
    kind: Literal["memory", "document_chunk"]
    source_id: str
    document_id: str | None
    title: str | None
    text: str
    score: float


class _LeasedEmbeddings(EmbeddingProvider):
    """The embedding provider under the heavy lease (INV-023)."""

    def __init__(
        self, inner: EmbeddingProvider, lifecycle: ModelLifecycleManager
    ) -> None:
        super().__init__(inner.provider_id)
        self._inner = inner
        self._lifecycle = lifecycle

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        async with await self._lifecycle.acquire_heavy(
            provider_id=self.provider_id,
            model_id=request.model_id,
            operation_id=request.operation_id,
        ):
            return await self._inner.embed(request)


_PARSER_CODES: Final = {
    UploadParserErrorCode.UNSUPPORTED_FORMAT: (415, "unsupported_format"),
    UploadParserErrorCode.MEDIA_TYPE_MISMATCH: (415, "media_type_mismatch"),
    UploadParserErrorCode.SOURCE_TOO_LARGE: (413, "payload_too_large"),
    UploadParserErrorCode.PARSER_UNAVAILABLE: (415, "unsupported_format"),
}


def _filename(row: DocumentRow) -> str:
    name = (row.metadata_json or {}).get("filename")
    return name if isinstance(name, str) and name else row.id


class KnowledgeService:
    """One event loop. Reads run on the loop's thread pool; writes are serialized.

    ``embedding`` None (no embedding backend configured) leaves the service
    read-only: listing and deleting work, uploads, new memories and search
    report ``knowledge_unavailable``, and missions are not offered knowledge.
    """

    def __init__(
        self,
        *,
        sessions: sessionmaker[Session],
        embedding: EmbeddingProvider | None,
        lifecycle: ModelLifecycleManager,
        agent_registry: AgentRegistry | None = None,
        model_id: str = EMBEDDING_MODEL_ID,
        limits: KnowledgeLimits | None = None,
    ) -> None:
        self._sessions = sessions
        self._limits = KnowledgeLimits() if limits is None else limits
        self._model_id = model_id
        self._embedding = (
            None if embedding is None else _LeasedEmbeddings(embedding, lifecycle)
        )
        # A private bus: these events are not part of the PHX-103 stream.
        bus = EventBus(capacity=64)
        self._store = SqliteVectorStore(sessions, max_search_candidates=MAX_CHUNKS)
        self._retrieval = RetrievalContextService(sessions, self._store, agent_registry)
        self._memory = PuddingMemoryService(
            sessions, bus, MemoryWritePolicy(), agent_registry
        )
        limits = self._limits
        self._ingestion = DocumentIngestionService(
            sessions,
            bus,
            DocumentWritePolicy(),
            DocumentIngestionLimits(
                max_source_bytes=MAX_UPLOAD_BYTES,
                max_parsed_chars=limits.max_parsed_chars,
                max_chunks=limits.max_chunks_per_document,
                chunk_size_chars=limits.chunk_size_chars,
                chunk_overlap_chars=limits.chunk_overlap_chars,
            ),
            agent_registry,
        )
        self._router = UploadRouter(
            UploadParserLimits(
                max_source_bytes=MAX_UPLOAD_BYTES,
                max_text_chars=limits.max_parsed_chars,
                max_segments=2000,
                max_pdf_pages=200,
                max_pdf_content_stream_bytes=4_000_000,
                max_docx_entries=500,
                max_docx_member_uncompressed_bytes=8_000_000,
                max_docx_total_uncompressed_bytes=16_000_000,
                max_docx_compression_ratio=200,
                max_csv_rows=20_000,
                max_csv_columns=100,
                max_csv_cell_chars=10_000,
            )
        )
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._embedding is not None

    def _require(self) -> EmbeddingProvider:
        if self._embedding is None:
            raise KnowledgeError(503, "knowledge_unavailable")
        return self._embedding

    # ---- embeddings

    async def _vectors(
        self, texts: tuple[str, ...], operation: str
    ) -> tuple[tuple[float, ...], ...]:
        provider = self._require()
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start : start + EMBED_BATCH]
            request = EmbeddingRequest(
                operation_id=f"{operation}:{start}",
                model_id=self._model_id,
                inputs=batch,
            )
            try:
                response = await provider.embed(request)
                if (
                    response.operation_id != request.operation_id
                    or response.provider_id != provider.provider_id
                    or len(response.embeddings) != len(batch)
                ):
                    raise ValueError("Uncorrelated embedding evidence")
            except asyncio.CancelledError:
                raise
            except Exception:
                raise KnowledgeError(503, "knowledge_unavailable") from None
            vectors.extend(tuple(vector) for vector in response.embeddings)
        return tuple(vectors)

    def _records(
        self,
        kind: str,
        sources: tuple[tuple[str, str], ...],
        vectors: tuple[tuple[float, ...], ...],
    ) -> tuple[VectorRecord, ...]:
        assert self._embedding is not None
        now = datetime.now(UTC)
        return tuple(
            VectorRecord(
                id=str(uuid4()),
                source_kind=kind,
                source_id=source_id,
                source_hash=source_hash,
                provider_id=self._embedding.provider_id,
                model_id=self._model_id,
                dimensions=len(vector),
                vector=vector,
                created_at=now,
            )
            for (source_id, source_hash), vector in zip(sources, vectors, strict=True)
        )

    # ---- documents

    def _document_count(self) -> tuple[int, int]:
        with self._sessions() as session:
            documents = session.scalar(select(func.count()).select_from(DocumentRow))
            chunks = session.scalar(select(func.count()).select_from(DocumentChunkRow))
        return int(documents or 0), int(chunks or 0)

    def _chunks(self, document_id: str) -> tuple[tuple[str, str, str], ...]:
        with self._sessions() as session:
            rows = session.execute(
                select(
                    DocumentChunkRow.id,
                    DocumentChunkRow.content_hash,
                    DocumentChunkRow.content,
                )
                .where(DocumentChunkRow.document_id == document_id)
                .order_by(DocumentChunkRow.chunk_index)
            ).all()
        return tuple((row[0], row[1], row[2]) for row in rows)

    def _remove_document(self, document_id: str) -> bool:
        with self._sessions() as session, session.begin():
            if session.get(DocumentRow, document_id) is None:
                return False
            chunk_ids = select(DocumentChunkRow.id).where(
                DocumentChunkRow.document_id == document_id
            )
            session.execute(
                delete(EmbeddingRecordRow)
                .where(
                    EmbeddingRecordRow.source_kind == "document_chunk",
                    EmbeddingRecordRow.source_id.in_(chunk_ids),
                )
                .execution_options(synchronize_session=False)
            )
            # Foreign-key order; production SQLite does not enforce it (R14).
            session.execute(
                delete(DocumentChunkRow)
                .where(DocumentChunkRow.document_id == document_id)
                .execution_options(synchronize_session=False)
            )
            session.execute(
                delete(DocumentRow)
                .where(DocumentRow.id == document_id)
                .execution_options(synchronize_session=False)
            )
        return True

    async def upload(
        self, filename: str, data: bytes, media_type: str | None = None
    ) -> DocumentInfo:
        """Parse, ingest, embed and index one upload, or leave nothing behind."""
        self._require()
        async with self._lock:
            documents, chunks = await asyncio.to_thread(self._document_count)
            if documents >= MAX_DOCUMENTS or chunks >= MAX_CHUNKS:
                raise KnowledgeError(409, "knowledge_full")
            try:
                route = self._router.resolve(
                    UploadDocument(
                        document_id=str(uuid4()),
                        filename=filename,
                        declared_media_type=media_type,
                        content_bytes=data,
                        source_timestamp=datetime.now(UTC),
                    )
                )
            except UploadParserError as error:
                raise KnowledgeError(
                    *_PARSER_CODES.get(error.code, (422, "invalid_upload"))
                ) from None
            except Exception:
                raise KnowledgeError(422, "invalid_upload") from None
            try:
                result = await self._ingestion.ingest(
                    source=route.source, parser=route.parser
                )
            except DocumentSecretRejectedError:
                raise KnowledgeError(422, "secret_rejected") from None
            except (DocumentLimitError, DocumentParserError):
                raise KnowledgeError(422, "invalid_upload") from None
            except DocumentIngestionError:
                raise KnowledgeError(503, "knowledge_unavailable") from None
            document = result.document
            try:
                if chunks + document.chunk_count > MAX_CHUNKS:
                    raise KnowledgeError(409, "knowledge_full")
                stored = await asyncio.to_thread(self._chunks, document.id)
                vectors = await self._vectors(
                    tuple(content for _, _, content in stored), document.id
                )
                records = self._records(
                    "document_chunk",
                    tuple((identity, digest) for identity, digest, _ in stored),
                    vectors,
                )
                await asyncio.to_thread(self._store.add_many, records)
            except BaseException as error:
                # Nothing half-indexed stays (shielded from a cancel, bounded).
                await asyncio.shield(
                    asyncio.to_thread(self._remove_document, document.id)
                )
                if isinstance(error, KnowledgeError | asyncio.CancelledError):
                    raise
                raise KnowledgeError(503, "knowledge_unavailable") from None
            return DocumentInfo(
                document_id=document.id,
                filename=filename,
                media_type=document.media_type,
                chunk_count=document.chunk_count,
                indexed_at=document.indexed_at,
            )

    def _list_documents(self) -> tuple[DocumentInfo, ...]:
        with self._sessions() as session:
            rows = session.scalars(
                select(DocumentRow).order_by(DocumentRow.indexed_at, DocumentRow.id)
            ).all()
            return tuple(
                DocumentInfo(
                    document_id=row.id,
                    filename=_filename(row),
                    media_type=row.media_type,
                    chunk_count=row.chunk_count,
                    indexed_at=row.indexed_at,
                )
                for row in rows
            )

    async def list_documents(self) -> tuple[DocumentInfo, ...]:
        return await asyncio.to_thread(self._list_documents)

    async def delete_document(self, document_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._remove_document, document_id)

    # ---- memories

    def _memory_count(self) -> int:
        with self._sessions() as session:
            count = session.scalar(
                select(func.count())
                .select_from(MemoryRow)
                .where(MemoryRow.scope == MEMORY_SCOPE)
            )
        return int(count or 0)

    async def remember(
        self, content: str, memory_type: MemoryType | str = MemoryType.USER
    ) -> MemoryInfo:
        """Store one user note under the write policy, embedded and searchable."""
        self._require()
        try:
            kind = MemoryType(memory_type)
        except ValueError:
            raise KnowledgeError(422, "invalid_request") from None
        now = datetime.now(UTC)
        try:
            record = MemoryRecord(
                type=kind,
                content=content,
                source_type=MEMORY_SOURCE_TYPE,
                scope=MEMORY_SCOPE,
                confidence=1.0,
                importance=0.5,
                created_at=now,
                updated_at=now,
            )
        except ValueError:
            raise KnowledgeError(422, "invalid_request") from None
        verdict = MemoryWritePolicy().evaluate(record).verdict
        # Checked before embedding, so a secret never leaves this process.
        if verdict == MemoryAdmissionVerdict.REJECTED_SECRET:
            raise KnowledgeError(422, "secret_rejected")
        if verdict != MemoryAdmissionVerdict.ACCEPTED:
            raise KnowledgeError(422, "invalid_request")
        async with self._lock:
            if await asyncio.to_thread(self._memory_count) >= MAX_MEMORIES:
                raise KnowledgeError(409, "knowledge_full")
            digest = hashlib.sha256(record.content.encode("utf-8")).hexdigest()
            vector = self._records(
                "memory",
                ((record.id, digest),),
                await self._vectors((record.content,), record.id),
            )[0]
            stored = record.model_copy(update={"embedding_ref": vector.id})
            try:
                await self._memory.create(MemoryRecord.model_validate(stored))
            except MemorySecretRejectedError:
                raise KnowledgeError(422, "secret_rejected") from None
            except MemoryServiceError:
                raise KnowledgeError(503, "knowledge_unavailable") from None
            try:
                await asyncio.to_thread(self._store.add, vector)
            except BaseException as error:
                await asyncio.shield(asyncio.to_thread(self._remove_memory, record.id))
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise KnowledgeError(503, "knowledge_unavailable") from None
            return _memory_info(record)

    def _list_memories(self) -> tuple[MemoryInfo, ...]:
        with self._sessions() as session:
            rows = session.scalars(
                select(MemoryRow)
                .where(MemoryRow.scope == MEMORY_SCOPE)
                .order_by(MemoryRow.created_at, MemoryRow.id)
            ).all()
            return tuple(
                MemoryInfo(
                    memory_id=row.id,
                    memory_type=row.memory_type,
                    scope=row.scope,
                    content=row.content,
                    created_at=row.created_at,
                )
                for row in rows
            )

    async def list_memories(self) -> tuple[MemoryInfo, ...]:
        return await asyncio.to_thread(self._list_memories)

    def _remove_memory(self, memory_id: str) -> bool:
        with self._sessions() as session, session.begin():
            row = session.get(MemoryRow, memory_id)
            if row is None or row.scope != MEMORY_SCOPE:
                return False
            session.execute(
                delete(EmbeddingRecordRow)
                .where(
                    EmbeddingRecordRow.source_kind == "memory",
                    EmbeddingRecordRow.source_id == memory_id,
                )
                .execution_options(synchronize_session=False)
            )
            session.delete(row)
        return True

    async def delete_memory(self, memory_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._remove_memory, memory_id)

    # ---- retrieval (search and the mission KnowledgeSource)

    def _document_ids(self) -> tuple[str, ...]:
        with self._sessions() as session:
            return tuple(
                session.scalars(
                    select(DocumentRow.id).order_by(DocumentRow.id).limit(MAX_DOCUMENTS)
                )
            )

    async def available(self) -> bool:
        """KnowledgeSource: something is indexed and embeddings are configured."""
        if self._embedding is None:
            return False
        documents, _ = await asyncio.to_thread(self._document_count)
        return documents > 0 or await asyncio.to_thread(self._memory_count) > 0

    async def retrieve(
        self, *, retrieval_id: str, query_text: str
    ) -> RetrievalContextResult:
        """KnowledgeSource: scoped, bounded context; errors propagate explicitly."""
        provider = self._require()
        limits = self._limits
        documents = await asyncio.to_thread(self._document_ids)
        return await self._retrieval.retrieve(
            RetrievalRequest(
                retrieval_id=retrieval_id,
                query_text=query_text,
                provider_id=provider.provider_id,
                model_id=self._model_id,
                as_of=datetime.now(UTC),
                memory_scopes=(MEMORY_SCOPE,),
                document_ids=documents,
                candidate_limit_per_kind=limits.candidate_limit_per_kind,
                min_similarity=limits.min_similarity,
                ranking_policy=MemoryRankingPolicy(
                    relevance_weight=1,
                    recency_weight=0.1,
                    importance_weight=0.1,
                    recency_half_life_seconds=30 * 24 * 3600,
                ),
                context_budget=ContextBudget(
                    max_items=limits.max_context_items,
                    max_chars=limits.max_context_chars,
                ),
            ),
            embedding_provider=provider,
        )

    def _titles(self, document_ids: tuple[str, ...]) -> dict[str, str]:
        with self._sessions() as session:
            rows = session.scalars(
                select(DocumentRow).where(DocumentRow.id.in_(document_ids))
            ).all()
            return {row.id: _filename(row) for row in rows}

    async def titles(self, document_ids: tuple[str, ...]) -> Mapping[str, str]:
        """KnowledgeSource: upload file names for display."""
        return await asyncio.to_thread(self._titles, document_ids)

    async def search(self, query: str, limit: int) -> tuple[SearchHit, ...]:
        """The user's own search over memories and uploads (``/memory/search``)."""
        self._require()
        if not await self.available():
            return ()
        try:
            result = await self.retrieve(retrieval_id=str(uuid4()), query_text=query)
        except KnowledgeError:
            raise
        except Exception:
            raise KnowledgeError(503, "knowledge_unavailable") from None
        items = result.items[:limit]
        titles = await self.titles(
            tuple({i.document_id for i in items if i.document_id is not None})
        )
        return tuple(
            SearchHit(
                kind=item.source_kind,
                source_id=item.source_id,
                document_id=item.document_id,
                title=None
                if item.document_id is None
                else titles.get(item.document_id),
                text=item.content[:SNIPPET_CHARS],
                score=item.rerank_score,
            )
            for item in items
        )


def _memory_info(record: MemoryRecord) -> MemoryInfo:
    return MemoryInfo(
        memory_id=record.id,
        memory_type=record.type.value,
        scope=record.scope,
        content=record.content,
        created_at=record.created_at,
    )
