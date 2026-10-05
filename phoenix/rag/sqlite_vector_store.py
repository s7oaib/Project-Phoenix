"""Borrowed synchronous SQLite storage with bounded exact cosine evidence."""

from math import fsum, hypot, isfinite

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from phoenix.persistence.embeddings import EmbeddingRecordRow
from phoenix.rag.vector_store import (
    VectorBatch,
    VectorConflictError,
    VectorCorruptionError,
    VectorDimensionError,
    VectorInputError,
    VectorMatch,
    VectorPersistenceError,
    VectorRecord,
    VectorSearchLimitError,
    VectorSearchQuery,
    VectorStore,
    VectorStoreError,
    _identifier,
    _revalidate,
)


def _identity(value: str) -> str:
    try:
        return _identifier.validate_python(value)
    except ValueError:
        raise VectorInputError("Invalid vector record ID") from None


def _record(row: EmbeddingRecordRow) -> VectorRecord:
    try:
        return VectorRecord.model_validate(
            {field: getattr(row, field) for field in VectorRecord.model_fields}
        )
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise VectorCorruptionError("Invalid stored vector evidence") from None


def _unit(vector: tuple[float, ...]) -> tuple[float, ...]:
    # Scale temporary arithmetic, never stored evidence; handle extreme finite values.
    scale = max(abs(component) for component in vector)
    scaled = tuple(component / scale for component in vector)
    norm = hypot(*scaled)
    return tuple(component / norm for component in scaled)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    score = fsum(a * b for a, b in zip(left, _unit(right), strict=True))
    if not isfinite(score) or abs(score) > 1 + 1e-12:
        raise VectorCorruptionError("Invalid similarity evidence")
    return max(-1.0, min(1.0, score))


class SqliteVectorStore(VectorStore):
    """Caller owns schema, factory/engine lifetimes and serialized source policy.

    Explicit writes reserve SQLite's transaction before dimension checks. Search
    is bounded exact arithmetic, not source retrieval or an ANN/relevance engine.
    """

    def __init__(
        self, sessions: sessionmaker[Session], *, max_search_candidates: int
    ) -> None:
        if not isinstance(sessions, sessionmaker):
            raise VectorInputError("Expected an explicit session factory")
        if type(max_search_candidates) is not int or max_search_candidates < 1:
            raise VectorInputError("Candidate bound must be a positive integer")
        self._sessions = sessions
        self._max_search_candidates = max_search_candidates

    @property
    def max_search_candidates(self) -> int:
        return self._max_search_candidates

    @staticmethod
    def _space_dimension(session: Session, provider: str, model: str) -> int | None:
        dimensions = session.scalars(
            select(EmbeddingRecordRow.dimensions)
            .where(
                EmbeddingRecordRow.provider_id == provider,
                EmbeddingRecordRow.model_id == model,
            )
            .distinct()
            .limit(2)
        ).all()
        if not dimensions:
            return None
        if len(dimensions) != 1 or type(dimensions[0]) is not int or dimensions[0] < 1:
            raise VectorCorruptionError(
                "Inconsistent stored embedding-space dimensions"
            )
        return dimensions[0]

    def add(self, record: VectorRecord) -> VectorRecord:
        return self.add_many((record,))[0]

    def add_many(self, records: tuple[VectorRecord, ...]) -> tuple[VectorRecord, ...]:
        try:
            batch = VectorBatch(records=records)
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise VectorInputError("Invalid vector batch evidence") from None
        try:
            with self._sessions() as session:
                with session.begin():
                    # Reserve the writer before reads: differing dimensions cannot race.
                    session.execute(text("BEGIN IMMEDIATE"))
                    spaces: dict[tuple[str, str], int] = {}
                    for record in batch.records:
                        conflict = session.scalar(
                            select(EmbeddingRecordRow.id)
                            .where(
                                (EmbeddingRecordRow.id == record.id)
                                | (
                                    (
                                        EmbeddingRecordRow.source_kind
                                        == record.source_kind
                                    )
                                    & (EmbeddingRecordRow.source_id == record.source_id)
                                    & (
                                        EmbeddingRecordRow.provider_id
                                        == record.provider_id
                                    )
                                    & (EmbeddingRecordRow.model_id == record.model_id)
                                )
                            )
                            .limit(1)
                        )
                        if conflict is not None:
                            raise VectorConflictError("Vector identity already exists")
                        space = (record.provider_id, record.model_id)
                        if space not in spaces:
                            dimension = self._space_dimension(session, *space)
                            if dimension is not None and dimension != record.dimensions:
                                raise VectorDimensionError(
                                    "Stored embedding-space dimension mismatch"
                                )
                            spaces[space] = record.dimensions
                    session.add_all(
                        EmbeddingRecordRow(**record.model_dump())
                        for record in batch.records
                    )
                    session.commit()
        except IntegrityError as error:
            if getattr(error.orig, "sqlite_errorname", None) in (
                "SQLITE_CONSTRAINT_PRIMARYKEY",
                "SQLITE_CONSTRAINT_UNIQUE",
            ):
                raise VectorConflictError("Vector identity already exists") from None
            raise VectorPersistenceError("Vector database operation failed") from None
        except SQLAlchemyError:
            raise VectorPersistenceError("Vector database operation failed") from None
        return batch.records

    def get(self, record_id: str) -> VectorRecord | None:
        identity = _identity(record_id)
        try:
            with self._sessions() as session:
                row = session.get(EmbeddingRecordRow, identity)
                return _record(row) if row is not None else None
        except VectorStoreError:
            raise
        except SQLAlchemyError:
            raise VectorPersistenceError("Vector database operation failed") from None
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise VectorCorruptionError("Invalid stored vector evidence") from None

    def delete(self, record_id: str) -> bool:
        identity = _identity(record_id)
        try:
            with self._sessions() as session:
                with session.begin():
                    result = session.connection().execute(
                        delete(EmbeddingRecordRow)
                        .where(EmbeddingRecordRow.id == identity)
                        .execution_options(synchronize_session=False)
                    )
                    deleted = result.rowcount > 0
                    session.commit()
                    return deleted
        except SQLAlchemyError:
            raise VectorPersistenceError("Vector database operation failed") from None

    def search(self, query: VectorSearchQuery) -> tuple[VectorMatch, ...]:
        try:
            query = _revalidate(query, VectorSearchQuery)
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise VectorInputError("Invalid vector query evidence") from None
        try:
            with self._sessions() as session:
                with session.begin():
                    # SQLite's legacy SELECT mode needs an explicit read snapshot.
                    session.execute(text("BEGIN"))
                    dimension = self._space_dimension(
                        session, query.provider_id, query.model_id
                    )
                    if dimension is None:
                        return ()
                    filters = [
                        EmbeddingRecordRow.provider_id == query.provider_id,
                        EmbeddingRecordRow.model_id == query.model_id,
                        EmbeddingRecordRow.dimensions == dimension,
                    ]
                    if query.source_kind is not None:
                        filters.append(
                            EmbeddingRecordRow.source_kind == query.source_kind
                        )
                    if query.source_ids is not None:
                        filters.append(
                            EmbeddingRecordRow.source_id.in_(query.source_ids)
                        )
                    count = session.scalar(
                        select(func.count())
                        .select_from(EmbeddingRecordRow)
                        .where(*filters)
                    )
                    if count == 0:
                        return ()
                    if len(query.vector) != dimension:
                        raise VectorDimensionError(
                            "Query embedding-space dimension mismatch"
                        )
                    if count is None or count > self._max_search_candidates:
                        raise VectorSearchLimitError(
                            "Exact candidate set exceeds configured bound"
                        )
                    # Bound the fetch independently as well as the preceding count.
                    rows = session.scalars(
                        select(EmbeddingRecordRow).where(*filters).limit(count + 1)
                    ).all()
                    if len(rows) > self._max_search_candidates:
                        raise VectorSearchLimitError(
                            "Exact candidate set exceeds configured bound"
                        )
                    records = tuple(_record(row) for row in rows)
        except VectorStoreError:
            raise
        except SQLAlchemyError:
            raise VectorPersistenceError("Vector database operation failed") from None
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise VectorCorruptionError("Invalid stored vector evidence") from None
        unit_query = _unit(query.vector)
        matches = tuple(
            VectorMatch(
                record_id=record.id,
                source_kind=record.source_kind,
                source_id=record.source_id,
                source_hash=record.source_hash,
                provider_id=record.provider_id,
                model_id=record.model_id,
                score=_cosine(unit_query, record.vector),
            )
            for record in records
        )
        return tuple(
            sorted(matches, key=lambda match: (-match.score, match.record_id))[
                : query.limit
            ]
        )
