"""Explicit admitted storage only; memory evidence grants no execution authority.

Callers own schema setup, dependencies and authorization. Secret classification
belongs to the required trusted admission policy, not to text heuristics here.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from typing import Annotated, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from phoenix.agents.registry import (
    AgentDefinition,
    AgentRegistry,
    build_default_agent_registry,
)
from phoenix.core.event_bus import EventBus, EventBusFullError
from phoenix.core.events import EventEnvelope, EventType
from phoenix.memory.schema import MemoryRecord, MemoryType
from phoenix.persistence.memory import MemoryRow

_Identifier = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_identifier = TypeAdapter(_Identifier)


class MemoryServiceError(ValueError):
    """Safe service failure; private inputs and underlying SQL are omitted."""


class MemoryInputError(MemoryServiceError):
    """Invalid exact input contract."""


class MemoryAdmissionError(MemoryServiceError):
    """Policy failure, invalid decision or ID mismatch."""


class MemorySecretRejectedError(MemoryAdmissionError):
    """Secret-bearing memory was rejected before database access."""


class MemoryPolicyRejectedError(MemoryAdmissionError):
    """Memory policy rejected persistence before database access."""


class MemoryConflictError(MemoryServiceError):
    """Identity or replacement timestamp conflict; original remains unchanged."""


class MemoryNotFoundError(MemoryServiceError):
    """Explicit replacement target does not exist."""


class MemoryPersistenceError(MemoryServiceError):
    """Database operation failed without silent repair or retry."""


class MemoryDataError(MemoryPersistenceError):
    """Stored row cannot satisfy the domain contract."""


class MemoryEventError(MemoryServiceError):
    """Publication failed after durable commit; the row remains authoritative."""


def _snapshot(value: object, contract: type[BaseModel]) -> dict[str, object]:
    if type(value) is not contract or not isinstance(value, BaseModel):
        raise MemoryInputError("Expected an exact memory contract")
    return {**vars(value), **(value.__pydantic_extra__ or {})}


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

    @field_validator(
        "verdict", "event_delivery", "type", mode="before", check_fields=False
    )
    @classmethod
    def textual_enum(cls, value: object) -> object:
        if value is not None and not isinstance(value, str):
            raise ValueError("Expected a textual vocabulary value")
        return value


class MemoryAdmissionVerdict(StrEnum):
    ACCEPTED = "accepted"
    REJECTED_SECRET = "rejected_secret"
    REJECTED_POLICY = "rejected_policy"


class MemoryAdmissionDecision(_Contract):
    memory_id: _Identifier
    verdict: MemoryAdmissionVerdict


class MemoryAdmissionPolicy(Protocol):
    def evaluate(self, record: MemoryRecord) -> MemoryAdmissionDecision:
        """Classify this exact record; only accepted permits persistence."""
        ...


class MemoryListQuery(_Contract):
    type: MemoryType | None = None
    scope: _Identifier | None = None
    source_type: _Identifier | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=500)] = 100
    offset: Annotated[int, Field(strict=True, ge=0)] = 0


class MemoryEventDelivery(StrEnum):
    PUBLISHED = "published"
    REJECTED_FULL = "rejected_full"


class MemoryCreateResult(_Contract):
    record: MemoryRecord
    event_delivery: MemoryEventDelivery

    @field_validator("record", mode="before")
    @classmethod
    def exact_record(cls, value: object) -> MemoryRecord:
        return MemoryRecord.model_validate(_snapshot(value, MemoryRecord))


def _record(value: object) -> MemoryRecord:
    try:
        return MemoryRecord.model_validate(_snapshot(value, MemoryRecord))
    except (ValueError, TypeError, RecursionError):
        raise MemoryInputError("Invalid memory record") from None


def _id(value: object) -> str:
    try:
        return _identifier.validate_python(value)
    except ValidationError:
        raise MemoryInputError("Invalid memory ID") from None


def _row_values(record: MemoryRecord) -> dict[str, object]:
    values = record.model_dump()
    values["memory_type"] = values.pop("type")
    values["metadata_json"] = values.pop("metadata")
    return values


def _from_row(row: MemoryRow) -> MemoryRecord:
    try:
        return MemoryRecord.model_validate(
            {
                "id": row.id,
                "type": row.memory_type,
                "content": row.content,
                "source_type": row.source_type,
                "source_ref": row.source_ref,
                "scope": row.scope,
                "confidence": row.confidence,
                "importance": row.importance,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "last_accessed_at": row.last_accessed_at,
                "expires_at": row.expires_at,
                "embedding_ref": row.embedding_ref,
                "metadata": row.metadata_json,
            }
        )
    except (ValueError, TypeError, RecursionError):
        raise MemoryDataError("Invalid stored memory record") from None


class PuddingMemoryService:
    """Borrowed synchronous sessions; only post-commit create publication awaits."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        event_bus: EventBus,
        admission_policy: MemoryAdmissionPolicy,
        registry: AgentRegistry | None = None,
    ) -> None:
        if not isinstance(sessions, sessionmaker) or not isinstance(
            event_bus, EventBus
        ):
            raise MemoryInputError("Expected a session factory and EventBus")
        if not callable(getattr(admission_policy, "evaluate", None)):
            raise MemoryAdmissionError("An explicit admission policy is required")
        selected = build_default_agent_registry() if registry is None else registry
        try:
            fields = _snapshot(selected, AgentRegistry)
            fields["agents"] = tuple(
                AgentDefinition.model_validate(_snapshot(agent, AgentDefinition))
                for agent in selected.agents
            )
            AgentRegistry.model_validate(fields).require("memory")
        except (ValueError, LookupError, TypeError, AttributeError):
            raise MemoryInputError("Registry requires the memory internal ID") from None
        self._sessions = sessions
        self._event_bus = event_bus
        self._admission_policy = admission_policy

    @property
    def agent_id(self) -> str:
        return "memory"

    def _admit(self, value: MemoryRecord) -> MemoryRecord:
        record = _record(value)
        try:
            # Keep persistence evidence separate even from a policy that abuses
            # object.__setattr__ to mutate the frozen snapshot it receives.
            supplied = self._admission_policy.evaluate(_record(record))
            decision = MemoryAdmissionDecision.model_validate(
                _snapshot(supplied, MemoryAdmissionDecision)
            )
        except Exception:
            raise MemoryAdmissionError("Memory admission failed") from None
        if decision.memory_id != record.id:
            raise MemoryAdmissionError("Admission memory ID mismatch")
        if decision.verdict is MemoryAdmissionVerdict.REJECTED_SECRET:
            raise MemorySecretRejectedError("Secret-bearing memory rejected")
        if decision.verdict is MemoryAdmissionVerdict.REJECTED_POLICY:
            raise MemoryPolicyRejectedError("Memory policy rejected persistence")
        return record

    @contextmanager
    def _session(
        self, *, write: bool = False, creating: bool = False
    ) -> Iterator[Session]:
        try:
            with self._sessions() as session:
                with session.begin():
                    yield session
                    if write:
                        session.commit()
        except IntegrityError as error:
            if creating and getattr(error.orig, "sqlite_errorname", None) == (
                "SQLITE_CONSTRAINT_PRIMARYKEY"
            ):
                raise MemoryConflictError("Memory ID already exists") from None
            raise MemoryPersistenceError("Memory database operation failed") from None
        except SQLAlchemyError:
            raise MemoryPersistenceError("Memory database operation failed") from None
        except MemoryServiceError:
            raise
        except (ValueError, TypeError, RecursionError):
            # SQLite JSON/datetime result decoding can fail before _from_row.
            raise MemoryDataError("Invalid stored memory data") from None

    async def create(self, record: MemoryRecord) -> MemoryCreateResult:
        accepted = self._admit(record)
        with self._session(write=True, creating=True) as session:
            if session.get(MemoryRow, accepted.id) is not None:
                raise MemoryConflictError("Memory ID already exists")
            session.add(MemoryRow(**_row_values(accepted)))
        event = EventEnvelope(
            event_type=EventType.MEMORY_CREATED,
            agent_id="memory",
            correlation_id=accepted.id,
            payload={"memory_id": accepted.id, "memory_type": accepted.type.value},
        )
        try:
            await self._event_bus.publish(event)
        except EventBusFullError:
            delivery = MemoryEventDelivery.REJECTED_FULL
        except Exception:
            raise MemoryEventError(
                "Memory committed; event publication failed"
            ) from None
        else:
            delivery = MemoryEventDelivery.PUBLISHED
        return MemoryCreateResult(record=accepted, event_delivery=delivery)

    def get(self, memory_id: str) -> MemoryRecord | None:
        identity = _id(memory_id)
        with self._session() as session:
            row = session.get(MemoryRow, identity)
            return _from_row(row) if row is not None else None

    def list_records(
        self, query: MemoryListQuery | None = None
    ) -> tuple[MemoryRecord, ...]:
        try:
            selected = (
                MemoryListQuery()
                if query is None
                else MemoryListQuery.model_validate(_snapshot(query, MemoryListQuery))
            )
        except (ValueError, TypeError):
            raise MemoryInputError("Invalid memory list query") from None
        statement = select(MemoryRow)
        if selected.type is not None:
            statement = statement.where(MemoryRow.memory_type == selected.type.value)
        if selected.scope is not None:
            statement = statement.where(MemoryRow.scope == selected.scope)
        if selected.source_type is not None:
            statement = statement.where(MemoryRow.source_type == selected.source_type)
        statement = (
            statement.order_by(MemoryRow.created_at.asc(), MemoryRow.id.asc())
            .limit(selected.limit)
            .offset(selected.offset)
        )
        with self._session() as session:
            return tuple(_from_row(row) for row in session.scalars(statement))

    def replace(self, record: MemoryRecord) -> MemoryRecord:
        accepted = self._admit(record)
        with self._session(write=True) as session:
            row = session.get(MemoryRow, accepted.id)
            if row is None:
                raise MemoryNotFoundError("Memory replacement target not found")
            stored = _from_row(row)
            if accepted.created_at != stored.created_at:
                raise MemoryConflictError("Memory creation timestamp cannot change")
            if accepted.updated_at < stored.updated_at:
                raise MemoryConflictError("Memory update timestamp cannot move earlier")
            for name, value in _row_values(accepted).items():
                setattr(row, name, value)
        return _record(accepted)

    def delete(self, memory_id: str) -> bool:
        identity = _id(memory_id)
        with self._session(write=True) as session:
            row = session.get(MemoryRow, identity)
            if row is None:
                return False
            session.delete(row)
        return True
