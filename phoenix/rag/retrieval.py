"""Explicit read-only context evidence; retrieved text grants no authority.

Callers own admission, authorization, schema, collaborators and deadlines. Source
snapshots do not span embedding and the VectorStore's independent transaction.
"""

import asyncio
import hashlib
import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self, TypeVar, cast
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from phoenix.agents.registry import (
    AgentDefinition,
    AgentRegistry,
    build_default_agent_registry,
)
from phoenix.memory.schema import MemoryRecord, MemoryType
from phoenix.models.embeddings import (
    EmbeddingProvider,
    EmbeddingRequest,
    EmbeddingResponse,
)
from phoenix.persistence.documents import DocumentChunkRow, DocumentRow
from phoenix.persistence.memory import MemoryRow
from phoenix.rag.ingestion import DocumentChunkRecord, DocumentRecord
from phoenix.rag.vector_store import VectorMatch, VectorSearchQuery, VectorStore

_Id = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
_Hash = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    ),
]
_Count = Annotated[int, Field(strict=True, ge=0)]
_Kind = Literal["memory", "document_chunk"]
_Model = TypeVar("_Model", bound=BaseModel)
_identifier = TypeAdapter(_Id)


def _number(value: object) -> float:
    if type(value) not in (int, float):
        raise ValueError("Expected ordinary numeric evidence")
    try:
        number = float(cast(int | float, value))
    except (ValueError, OverflowError):
        raise ValueError("Expected finite numeric evidence") from None
    if not math.isfinite(number):
        raise ValueError("Expected finite numeric evidence")
    return number


_Number = Annotated[float, BeforeValidator(_number), Field(strict=True)]
_Score = Annotated[_Number, Field(ge=0, le=1)]
_Similarity = Annotated[_Number, Field(ge=-1, le=1)]


def _snapshot(value: object, contract: type[BaseModel]) -> dict[str, object]:
    if type(value) is not contract or not isinstance(value, BaseModel):
        raise ValueError("Expected an exact retrieval contract")
    return {**vars(value), **(value.__pydantic_extra__ or {})}


def _validated(value: object, contract: type[_Model]) -> _Model:
    return contract.model_validate(_snapshot(value, contract))


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
    )

    @model_validator(mode="wrap")
    @classmethod
    def exact_snapshot(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        if isinstance(value, BaseModel):
            value = _snapshot(value, cls)
        return handler(value)

    @field_validator("as_of", "source_timestamp", mode="before", check_fields=False)
    @classmethod
    def timestamp_input(cls, value: object) -> object:
        if value is not None and not isinstance(value, (datetime, str)):
            raise ValueError("Expected an aware timestamp or ISO representation")
        return value

    @field_validator("as_of", "source_timestamp", check_fields=False)
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @field_validator("query_text", "content", "path", "section", check_fields=False)
    @classmethod
    def meaningful(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Expected meaningful text")
        return value

    @field_validator("memory_type", "status", mode="before", check_fields=False)
    @classmethod
    def textual_enum(cls, value: object) -> object:
        if value is not None and not isinstance(value, str):
            raise ValueError("Expected a textual vocabulary value")
        return value


class MemoryRankingPolicy(_Contract):
    relevance_weight: Annotated[_Number, Field(gt=0)]
    recency_weight: Annotated[_Number, Field(ge=0)]
    importance_weight: Annotated[_Number, Field(ge=0)]
    recency_half_life_seconds: Annotated[_Number, Field(gt=0)]


class ContextBudget(_Contract):
    max_items: Annotated[int, Field(strict=True, ge=1, le=100)]
    max_chars: Annotated[int, Field(strict=True, ge=1, le=1_000_000)]


class RetrievalRequest(_Contract):
    retrieval_id: _Id = Field(default_factory=lambda: str(uuid4()))
    query_text: _Text = Field(repr=False)
    provider_id: _Id
    model_id: _Id
    as_of: AwareDatetime
    memory_scopes: tuple[_Id, ...] = ()
    memory_types: Annotated[tuple[MemoryType, ...], Field(min_length=1)] | None = None
    document_ids: tuple[_Id, ...] = ()
    authoritative_document_ids: tuple[_Id, ...] = ()
    candidate_limit_per_kind: Annotated[int, Field(strict=True, ge=1, le=100)]
    min_similarity: _Similarity
    ranking_policy: MemoryRankingPolicy
    context_budget: ContextBudget

    @field_validator(
        "memory_scopes",
        "memory_types",
        "document_ids",
        "authoritative_document_ids",
        mode="before",
    )
    @classmethod
    def ordered(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, (tuple, list)):
            raise ValueError("Scope filters require ordered sequences")
        return tuple(value)

    @field_validator("memory_types", mode="before")
    @classmethod
    def textual_types(cls, value: object) -> object:
        if value is not None:
            if not isinstance(value, (tuple, list)) or any(
                not isinstance(item, str) for item in value
            ):
                raise ValueError("Expected existing textual memory types")
        return value

    @model_validator(mode="after")
    def scopes(self) -> Self:
        for values in (
            self.memory_scopes,
            self.memory_types,
            self.document_ids,
            self.authoritative_document_ids,
        ):
            if values is not None and len(set(values)) != len(values):
                raise ValueError("Duplicate scope values")
        if not self.memory_scopes and not self.document_ids:
            raise ValueError("Explicit source scope is required")
        if not set(self.authoritative_document_ids).issubset(self.document_ids):
            raise ValueError("Authoritative documents must be requested")
        return self


class ContextItem(_Contract):
    vector_record_id: _Id
    source_kind: _Kind
    source_id: _Id
    source_hash: _Hash
    content: _Text = Field(repr=False)
    similarity: _Similarity
    rerank_score: _Score
    authoritative: bool = Field(strict=True)
    source_ref: _Id | None = None
    document_id: _Id | None = None
    path: _Text | None = None
    page: Annotated[int, Field(strict=True, ge=1)] | None = None
    section: _Text | None = None
    source_timestamp: AwareDatetime | None = None
    parser_version: _Id | None = None
    chunk_index: _Count | None = None
    memory_type: MemoryType | None = None
    memory_scope: _Id | None = None
    memory_confidence: _Score | None = None

    @model_validator(mode="after")
    def provenance_shape(self) -> Self:
        memory = (self.memory_type, self.memory_scope, self.memory_confidence)
        document = (
            self.document_id,
            self.path,
            self.page,
            self.section,
            self.source_timestamp,
            self.parser_version,
            self.chunk_index,
        )
        if self.source_kind == "memory":
            if (
                self.authoritative
                or any(v is not None for v in document)
                or any(v is None for v in memory)
            ):
                raise ValueError("Contradictory memory provenance")
        elif any(v is not None for v in memory) or any(
            v is None
            for v in (
                self.document_id,
                self.source_ref,
                self.parser_version,
                self.chunk_index,
            )
        ):
            raise ValueError("Contradictory document provenance")
        elif self.rerank_score != (self.similarity + 1) / 2:
            raise ValueError("Document score must be normalized similarity")
        return self


class RetrievalDiagnostics(_Contract):
    vector_matches_received: _Count
    below_similarity_exclusions: _Count
    stale_source_exclusions: _Count
    orphaned_source_exclusions: _Count
    ineligible_source_exclusions: _Count
    eligible_ranked_items: _Count
    budget_exclusions: _Count
    final_context_item_count: _Count

    @model_validator(mode="after")
    def accounting(self) -> Self:
        if self.vector_matches_received != (
            self.below_similarity_exclusions
            + self.stale_source_exclusions
            + self.orphaned_source_exclusions
            + self.ineligible_source_exclusions
            + self.eligible_ranked_items
        ) or self.eligible_ranked_items != (
            self.budget_exclusions + self.final_context_item_count
        ):
            raise ValueError("Contradictory retrieval diagnostics")
        return self


def _order(item: ContextItem) -> tuple[int, float, float, str, str, str]:
    return (
        -int(item.authoritative),
        -item.rerank_score,
        -item.similarity,
        item.source_kind,
        item.source_id,
        item.vector_record_id,
    )


class RetrievalContextResult(_Contract):
    retrieval_id: _Id
    provider_id: _Id
    model_id: _Id
    as_of: AwareDatetime
    items: Annotated[tuple[ContextItem, ...], Field(max_length=100)]
    total_chars: Annotated[int, Field(strict=True, ge=0, le=1_000_000)]
    diagnostics: RetrievalDiagnostics

    @field_validator("items", mode="before")
    @classmethod
    def ordered_items(cls, value: object) -> object:
        if not isinstance(value, (tuple, list)):
            raise ValueError("Context items require an ordered sequence")
        return tuple(value)

    @model_validator(mode="after")
    def consistent_result(self) -> Self:
        if self.total_chars != sum(len(item.content) for item in self.items) or (
            len(self.items) != self.diagnostics.final_context_item_count
        ):
            raise ValueError("Contradictory context totals")
        if len({item.vector_record_id for item in self.items}) != len(self.items):
            raise ValueError("Duplicate context vector identity")
        if tuple(sorted(self.items, key=_order)) != self.items:
            raise ValueError("Context order disagrees with ranking")
        return self


class RetrievalCancellationStatus(StrEnum):
    ACCEPTED = "accepted"
    NOT_FOUND = "not_found"


class RetrievalCancellationResult(_Contract):
    retrieval_id: _Id
    status: RetrievalCancellationStatus


class RetrievalError(ValueError):
    """Safe retrieval failure; never a final-answer or success verdict."""


class RetrievalInputError(RetrievalError):
    """Invalid exact request or collaborator."""


class RetrievalScopeError(RetrievalError):
    """Eligible source IDs exceed the explicit bound."""


class RetrievalEmbeddingError(RetrievalError):
    """Embedding failed or returned malformed/uncorrelated evidence."""


class RetrievalVectorError(RetrievalError):
    """Vector search failed or returned malformed/out-of-scope evidence."""


class RetrievalPersistenceError(RetrievalError):
    """Source database operation failed; no repair or retry."""


class RetrievalSourceError(RetrievalPersistenceError):
    """Stored source evidence violates its existing domain contract."""


class DuplicateActiveRetrievalError(RetrievalError):
    """A retrieval ID already belongs to another caller operation."""


@dataclass
class _Active:
    task: asyncio.Task[object]
    cancellation_requested: bool = False


def _memory_score(
    request: RetrievalRequest, memory: MemoryRecord, score: float
) -> float:
    policy = request.ranking_policy
    age = max(0.0, (request.as_of - memory.updated_at).total_seconds())
    components = (
        (score + 1) / 2,
        2 ** (-age / policy.recency_half_life_seconds),
        memory.importance,
    )
    weights = (policy.relevance_weight, policy.recency_weight, policy.importance_weight)
    # Algebraically the ticket's weighted mean; avoid overflow of finite weights.
    scale = max(weights)
    scaled = tuple(weight / scale for weight in weights)
    result = math.fsum(w * c for w, c in zip(scaled, components, strict=True)) / (
        math.fsum(scaled)
    )
    if not math.isfinite(result) or result < -1e-12 or result > 1 + 1e-12:
        raise RetrievalSourceError("Invalid memory ranking evidence")
    return max(0.0, min(1.0, result))


def _from_row(row: object, contract: type[_Model]) -> _Model:
    aliases = {"type": "memory_type", "metadata": "metadata_json"}
    record = contract.model_validate(
        {
            field: getattr(row, aliases.get(field, field))
            for field in contract.model_fields
        }
    )
    # Revalidation must not normalize corrupted storage into a different binding.
    for field in ("id", "document_id", "scope"):
        if field in contract.model_fields and getattr(row, field) != getattr(
            record, field
        ):
            raise ValueError("Stored source binding is not canonical")
    return record


class RetrievalContextService:
    """One process/event loop; explicit embedding and short read-only sessions."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        vector_store: VectorStore,
        registry: AgentRegistry | None = None,
    ) -> None:
        if not isinstance(sessions, sessionmaker) or not isinstance(
            vector_store, VectorStore
        ):
            raise RetrievalInputError("Expected a session factory and VectorStore")
        try:
            selected = build_default_agent_registry() if registry is None else registry
            fields = _snapshot(selected, AgentRegistry)
            fields["agents"] = tuple(
                _validated(agent, AgentDefinition) for agent in selected.agents
            )
            AgentRegistry.model_validate(fields).require("knowledge_rag")
        except (ValueError, TypeError, LookupError, AttributeError, RecursionError):
            raise RetrievalInputError(
                "Registry requires knowledge_rag internal ID"
            ) from None
        self._sessions = sessions
        self._store = vector_store
        self._active: dict[str, _Active] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def agent_id(self) -> str:
        return "knowledge_rag"

    @property
    def active_retrieval_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise RetrievalError("Retrieval service belongs to another event loop")
        self._loop = loop

    @staticmethod
    def _check(operation: _Active) -> None:
        if operation.cancellation_requested or operation.task.cancelling():
            raise asyncio.CancelledError

    @contextmanager
    def _read(self) -> Iterator[Session]:
        try:
            with self._sessions() as session, session.no_autoflush:
                with session.begin():
                    session.execute(text("BEGIN"))
                    yield session
        except RetrievalError:
            raise
        except SQLAlchemyError:
            raise RetrievalPersistenceError("Source database read failed") from None
        except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
            raise RetrievalSourceError("Invalid stored source evidence") from None

    def _source_ids(
        self, request: RetrievalRequest
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        memories: tuple[str, ...] = ()
        chunks: tuple[str, ...] = ()
        with self._read() as session:
            if request.memory_scopes:
                statement = select(MemoryRow.id).where(
                    MemoryRow.scope.in_(request.memory_scopes),
                    MemoryRow.expires_at.is_(None)
                    | (MemoryRow.expires_at > request.as_of),
                )
                if request.memory_types is not None:
                    statement = statement.where(
                        MemoryRow.memory_type.in_(request.memory_types)
                    )
                memories = tuple(
                    session.scalars(statement.order_by(MemoryRow.id).limit(2001))
                )
            if request.document_ids:
                chunks = tuple(
                    session.scalars(
                        select(DocumentChunkRow.id)
                        .join(
                            DocumentRow, DocumentRow.id == DocumentChunkRow.document_id
                        )
                        .where(DocumentRow.id.in_(request.document_ids))
                        .order_by(DocumentChunkRow.id)
                        .limit(2001)
                    )
                )
            if len(memories) > 2000 or len(chunks) > 2000:
                raise RetrievalScopeError("Eligible source ID bound exceeded")
            # Stored IDs must already be canonical; never repair their binding.
            if any(
                _identifier.validate_python(identity) != identity
                for identity in (*memories, *chunks)
            ):
                raise RetrievalSourceError("Invalid stored source identity")
        return memories, chunks

    def _matches(
        self,
        request: RetrievalRequest,
        vector: tuple[float, ...],
        sources: tuple[tuple[str, ...], tuple[str, ...]],
        operation: _Active,
    ) -> tuple[VectorMatch, ...]:
        matches: list[VectorMatch] = []
        seen: set[str] = set()
        for kind, ids in zip(("memory", "document_chunk"), sources, strict=True):
            if not ids:
                continue
            query = VectorSearchQuery(
                provider_id=request.provider_id,
                model_id=request.model_id,
                vector=vector,
                limit=request.candidate_limit_per_kind,
                source_kind=kind,
                source_ids=ids,
            )
            try:
                supplied = self._store.search(query)
                self._check(operation)
                if (
                    not isinstance(supplied, (tuple, list))
                    or len(supplied) > request.candidate_limit_per_kind
                ):
                    raise ValueError("Invalid vector result sequence")
                allowed = set(ids)
                for supplied_match in supplied:
                    match = _validated(supplied_match, VectorMatch)
                    if (
                        match.provider_id != request.provider_id
                        or match.model_id != request.model_id
                        or match.source_kind != kind
                        or match.source_id not in allowed
                        or match.record_id in seen
                    ):
                        raise ValueError("Invalid vector result correlation")
                    seen.add(match.record_id)
                    matches.append(match)
            except Exception:
                self._check(operation)
                raise RetrievalVectorError("Vector search or evidence failed") from None
        return tuple(matches)

    def _resolve(
        self, request: RetrievalRequest, matches: tuple[VectorMatch, ...]
    ) -> tuple[tuple[ContextItem, ...], dict[str, int]]:
        counts = dict(
            vector_matches_received=len(matches),
            below_similarity_exclusions=0,
            stale_source_exclusions=0,
            orphaned_source_exclusions=0,
            ineligible_source_exclusions=0,
        )
        ranked: list[ContextItem] = []
        with self._read() as session:
            for match in matches:
                if match.score < request.min_similarity:
                    counts["below_similarity_exclusions"] += 1
                    continue
                common = dict(
                    vector_record_id=match.record_id,
                    source_kind=match.source_kind,
                    source_id=match.source_id,
                    source_hash=match.source_hash,
                    similarity=match.score,
                )
                if match.source_kind == "memory":
                    row = session.get(MemoryRow, match.source_id)
                    if row is None:
                        counts["orphaned_source_exclusions"] += 1
                        continue
                    memory = _from_row(row, MemoryRecord)
                    if (
                        memory.scope not in request.memory_scopes
                        or (
                            request.memory_types is not None
                            and memory.type not in request.memory_types
                        )
                        or (
                            memory.expires_at is not None
                            and memory.expires_at <= request.as_of
                        )
                    ):
                        counts["ineligible_source_exclusions"] += 1
                        continue
                    if (
                        hashlib.sha256(memory.content.encode("utf-8")).hexdigest()
                        != match.source_hash
                    ):
                        counts["stale_source_exclusions"] += 1
                        continue
                    ranked.append(
                        ContextItem.model_validate(
                            common
                            | dict(
                                content=memory.content,
                                rerank_score=_memory_score(
                                    request, memory, match.score
                                ),
                                authoritative=False,
                                source_ref=memory.source_ref,
                                memory_type=memory.type,
                                memory_scope=memory.scope,
                                memory_confidence=memory.confidence,
                            )
                        )
                    )
                else:
                    chunk_row = session.get(DocumentChunkRow, match.source_id)
                    parent = (
                        None
                        if chunk_row is None
                        else session.get(DocumentRow, chunk_row.document_id)
                    )
                    if chunk_row is None or parent is None:
                        counts["orphaned_source_exclusions"] += 1
                        continue
                    chunk = _from_row(chunk_row, DocumentChunkRecord)
                    document = _from_row(parent, DocumentRecord)
                    if document.id not in request.document_ids:
                        counts["ineligible_source_exclusions"] += 1
                        continue
                    if chunk.content_hash != match.source_hash:
                        counts["stale_source_exclusions"] += 1
                        continue
                    ranked.append(
                        ContextItem.model_validate(
                            common
                            | dict(
                                content=chunk.content,
                                rerank_score=(match.score + 1) / 2,
                                authoritative=document.id
                                in request.authoritative_document_ids,
                                source_ref=document.source_ref,
                                document_id=document.id,
                                path=chunk.path,
                                page=chunk.page,
                                section=chunk.section,
                                source_timestamp=chunk.source_timestamp,
                                parser_version=chunk.parser_version,
                                chunk_index=chunk.chunk_index,
                            )
                        )
                    )
        return tuple(sorted(ranked, key=_order)), counts

    async def retrieve(
        self, request: RetrievalRequest, *, embedding_provider: EmbeddingProvider
    ) -> RetrievalContextResult:
        try:
            request = _validated(request, RetrievalRequest)
            if (
                not isinstance(embedding_provider, EmbeddingProvider)
                or embedding_provider.provider_id != request.provider_id
            ):
                raise ValueError("Embedding provider identity mismatch")
        except Exception:
            raise RetrievalInputError(
                "Invalid retrieval request or embedding provider"
            ) from None
        self._bind_loop()
        if request.retrieval_id in self._active:
            raise DuplicateActiveRetrievalError("Retrieval ID is already active")
        task = asyncio.current_task()
        if task is None or any(active.task is task for active in self._active.values()):
            raise RetrievalError("Caller task already owns an active retrieval")
        operation = _Active(task)
        self._active[request.retrieval_id] = operation
        try:
            self._check(operation)
            sources = self._source_ids(request)
            try:
                response = await embedding_provider.embed(
                    EmbeddingRequest(
                        operation_id=request.retrieval_id,
                        model_id=request.model_id,
                        inputs=(request.query_text,),
                    )
                )
                self._check(operation)
                fields = _snapshot(response, EmbeddingResponse)
                vectors = fields.get("embeddings")
                if not isinstance(vectors, (tuple, list)) or any(
                    not isinstance(value, (tuple, list)) for value in vectors
                ):
                    raise ValueError("Embedding vectors require ordered evidence")
                response = EmbeddingResponse.model_validate(fields)
                if (
                    response.operation_id != request.retrieval_id
                    or response.provider_id != request.provider_id
                    or response.model_id != request.model_id
                    or len(response.embeddings) != 1
                ):
                    raise ValueError("Embedding response correlation mismatch")
                # The vector boundary also requires ordinary finite/nonzero evidence.
                vector = VectorSearchQuery(
                    provider_id=request.provider_id,
                    model_id=request.model_id,
                    vector=response.embeddings[0],
                    limit=1,
                ).vector
            except Exception:
                self._check(operation)
                raise RetrievalEmbeddingError(
                    "Query embedding or evidence failed"
                ) from None
            matches = self._matches(request, vector, sources, operation)
            self._check(operation)
            ranked, counts = self._resolve(request, matches)
            selected: list[ContextItem] = []
            total = 0
            for item in ranked:
                if (
                    len(selected) < request.context_budget.max_items
                    and total + len(item.content) <= request.context_budget.max_chars
                ):
                    selected.append(item)
                    total += len(item.content)
            self._check(operation)
            return RetrievalContextResult(
                retrieval_id=request.retrieval_id,
                provider_id=request.provider_id,
                model_id=request.model_id,
                as_of=request.as_of,
                items=tuple(selected),
                total_chars=total,
                diagnostics=RetrievalDiagnostics(
                    **counts,
                    eligible_ranked_items=len(ranked),
                    budget_exclusions=len(ranked) - len(selected),
                    final_context_item_count=len(selected),
                ),
            )
        finally:
            del self._active[request.retrieval_id]

    async def cancel(self, retrieval_id: str) -> RetrievalCancellationResult:
        self._bind_loop()
        try:
            identity = _identifier.validate_python(retrieval_id)
        except ValueError:
            raise RetrievalInputError("Invalid retrieval ID") from None
        operation = self._active.get(identity)
        status = RetrievalCancellationStatus.NOT_FOUND
        if operation is not None:
            operation.cancellation_requested = True
            operation.task.cancel()
            status = RetrievalCancellationStatus.ACCEPTED
        return RetrievalCancellationResult(retrieval_id=identity, status=status)
