"""Durable permission/outcome evidence only; audit history grants no authority."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Self, TypeVar
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    TypeAdapter,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from phoenix.persistence.audit import AuditEventRow
from phoenix.security.permissions import (
    DecisionReason,
    PermissionDecision,
    PermissionRequest,
    PermissionStatus,
)
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolRegistryError,
    ToolResultStatus,
    ToolRiskLevel,
)

_Id = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Hash = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    ),
]
_identifier = TypeAdapter(_Id)
_Model = TypeVar("_Model", bound=BaseModel)


class AuditEventKind(StrEnum):
    PERMISSION_DECISION = "permission_decision"
    TOOL_OUTCOME = "tool_outcome"


class AuditError(ValueError):
    """Safe audit failure without private inputs, SQL or database location."""


class AuditInputError(AuditError):
    """Invalid exact evidence or request/registry correlation."""


class AuditConflictError(AuditError):
    """Audit identity already exists; no history was overwritten."""


class AuditPersistenceError(AuditError):
    """Explicit database failure without retry or private error details."""


class AuditDataError(AuditPersistenceError):
    """Stored row cannot satisfy the exact domain evidence contract."""


def _vocabulary(value: object, vocabulary: type[StrEnum]) -> object:
    if type(value) is not str and not any(value is member for member in vocabulary):
        raise ValueError("Expected exact vocabulary text or declared enum member")
    return value


def audit_required_for_execution(risk: ToolRiskLevel) -> bool:
    if type(risk) is not ToolRiskLevel or not any(
        risk is member for member in ToolRiskLevel
    ):
        raise AuditInputError("Expected a declared ToolRiskLevel")
    return risk in (ToolRiskLevel.P3, ToolRiskLevel.P4)


def _validated(value: object, contract: type[_Model]) -> _Model:
    if type(value) is not contract or not isinstance(value, BaseModel):
        raise ValueError("Expected an exact audit boundary contract")
    extras = value.__pydantic_extra__
    if extras is not None and type(extras) is not dict:
        raise ValueError("Expected exact stored contract extras")
    return contract.model_validate({**vars(value), **(extras or {})})


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
    )

    @model_validator(mode="wrap")
    @classmethod
    def exact_stored_values(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        if isinstance(value, BaseModel):
            if type(value) is not cls:
                raise ValueError("Expected exact audit contract")
            extras = value.__pydantic_extra__
            if extras is not None and type(extras) is not dict:
                raise ValueError("Expected exact stored contract extras")
            value = {**vars(value), **(extras or {})}
        return handler(value)


class AuditRecord(_Contract):
    audit_id: _Id = Field(default_factory=lambda: str(uuid4()))
    event_kind: AuditEventKind
    occurred_at: AwareDatetime
    request_id: _Id
    actor_id: _Id
    tool_id: _Id
    action: _Id
    resource: _Id
    request_reason_hash: _Hash
    risk_level: ToolRiskLevel
    permission_requirement: _Id
    permission_status: PermissionStatus
    permission_reason_code: DecisionReason
    matched_grant_id: _Id | None = None
    matched_policy_rule_id: _Id | None = None
    user_decision_id: _Id | None = None
    task_id: _Id | None = None
    session_id: _Id | None = None
    correlation_id: _Id | None = None
    tool_result_status: ToolResultStatus | None = None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def timestamp_input(cls, value: object) -> object:
        if type(value) not in (datetime, str):
            raise ValueError("Expected aware timestamp or ISO representation")
        return value

    @field_validator("occurred_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("event_kind", mode="before")
    @classmethod
    def kind(cls, value: object) -> object:
        return _vocabulary(value, AuditEventKind)

    @field_validator("risk_level", mode="before")
    @classmethod
    def risk(cls, value: object) -> object:
        return _vocabulary(value, ToolRiskLevel)

    @field_validator("permission_status", mode="before")
    @classmethod
    def status(cls, value: object) -> object:
        return _vocabulary(value, PermissionStatus)

    @field_validator("permission_reason_code", mode="before")
    @classmethod
    def reason(cls, value: object) -> object:
        return _vocabulary(value, DecisionReason)

    @field_validator("tool_result_status", mode="before")
    @classmethod
    def result(cls, value: object) -> object:
        return _vocabulary(value, ToolResultStatus) if value is not None else None

    @model_validator(mode="after")
    def structural_evidence(self) -> Self:
        # PHX-041 remains the one status/reason/risk/evidence consistency authority.
        PermissionDecision(
            request_id=self.request_id,
            actor_id=self.actor_id,
            tool_id=self.tool_id,
            risk_level=self.risk_level,
            permission_requirement=self.permission_requirement,
            status=self.permission_status,
            reason_code=self.permission_reason_code,
            matched_grant_id=self.matched_grant_id,
            matched_policy_rule_id=self.matched_policy_rule_id,
            user_decision_id=self.user_decision_id,
        )
        if self.event_kind == AuditEventKind.PERMISSION_DECISION:
            if self.tool_result_status is not None:
                raise ValueError("Permission audit cannot carry a tool outcome")
        elif (
            self.tool_result_status is None
            or self.permission_status != PermissionStatus.ALLOWED
            or self.risk_level == ToolRiskLevel.P5
            or self.tool_result_status == ToolResultStatus.DENIED
        ):
            raise ValueError("Tool outcome requires allowed nonblocked evidence")
        return self


class AuditListQuery(_Contract):
    event_kind: AuditEventKind | None = None
    request_id: _Id | None = None
    actor_id: _Id | None = None
    tool_id: _Id | None = None
    risk_level: ToolRiskLevel | None = None
    task_id: _Id | None = None
    session_id: _Id | None = None
    limit: Annotated[int, Field(strict=True, ge=1, le=500)] = 100
    offset: Annotated[int, Field(strict=True, ge=0)] = 0

    @field_validator("event_kind", mode="before")
    @classmethod
    def kind(cls, value: object) -> object:
        return _vocabulary(value, AuditEventKind) if value is not None else None

    @field_validator("risk_level", mode="before")
    @classmethod
    def risk(cls, value: object) -> object:
        return _vocabulary(value, ToolRiskLevel) if value is not None else None


def _from_row(row: AuditEventRow) -> AuditRecord:
    try:
        data = {
            name: getattr(row, name)
            for name in AuditRecord.model_fields
            if name != "audit_id"
        }
        return AuditRecord.model_validate({"audit_id": row.id, **data})
    except (ValueError, TypeError):
        raise AuditDataError("Invalid stored audit evidence") from None


@dataclass(frozen=True, slots=True, init=False)
class AuditTrail:
    """Caller-owned database dependencies; only explicit methods access SQLite."""

    _sessions: sessionmaker[Session]
    _registry: ToolRegistry

    def __init__(
        self, session_factory: sessionmaker[Session], registry: ToolRegistry
    ) -> None:
        if hasattr(self, "_registry"):
            raise AuditInputError("Audit trail is already initialized")
        try:
            if type(registry) is not ToolRegistry:
                raise ValueError("Expected exact ToolRegistry")
            snapshot = ToolRegistry(registry.list_tools())
            for tool in snapshot.list_tools():
                _vocabulary(tool.risk_level, ToolRiskLevel)
        except (ValueError, ToolRegistryError):
            raise AuditInputError("Invalid tool registry evidence") from None
        object.__setattr__(self, "_sessions", session_factory)
        object.__setattr__(self, "_registry", snapshot)

    @contextmanager
    def _session(self, *, write: bool = False) -> Iterator[Session]:
        try:
            with self._sessions() as session:
                with session.begin():
                    yield session
                    if write:
                        session.commit()
        except AuditError:
            raise
        except IntegrityError as error:
            if (
                getattr(error.orig, "sqlite_errorname", None)
                == "SQLITE_CONSTRAINT_PRIMARYKEY"
            ):
                raise AuditConflictError("Audit ID already exists") from None
            raise AuditPersistenceError("Audit database operation failed") from None
        except SQLAlchemyError:
            raise AuditPersistenceError("Audit database operation failed") from None
        except (ValueError, TypeError, OverflowError):
            raise AuditDataError("Invalid stored audit evidence") from None

    def _record(
        self,
        request: PermissionRequest,
        decision: PermissionDecision,
        *,
        occurred_at: datetime,
        audit_id: str | None,
        correlation_id: str | None,
        result_status: ToolResultStatus | None = None,
    ) -> AuditRecord:
        try:
            request = _validated(request, PermissionRequest)
            tool: ToolDefinition = self._registry.require(request.tool_id)
            decision = _validated(decision, PermissionDecision)
            if (
                decision.request_id,
                decision.actor_id,
                decision.tool_id,
                decision.risk_level,
                decision.permission_requirement,
            ) != (
                request.request_id,
                request.actor_id,
                tool.tool_id,
                tool.risk_level,
                tool.permission_requirement,
            ):
                raise ValueError("Permission evidence does not correlate")
            data: dict[str, object] = dict(
                event_kind=AuditEventKind.TOOL_OUTCOME
                if result_status is not None
                else AuditEventKind.PERMISSION_DECISION,
                occurred_at=occurred_at,
                request_id=request.request_id,
                actor_id=request.actor_id,
                tool_id=tool.tool_id,
                action=request.action,
                resource=request.resource,
                request_reason_hash=sha256(request.reason.encode("utf-8")).hexdigest(),
                risk_level=tool.risk_level,
                permission_requirement=tool.permission_requirement,
                permission_status=decision.status,
                permission_reason_code=decision.reason_code,
                matched_grant_id=decision.matched_grant_id,
                matched_policy_rule_id=decision.matched_policy_rule_id,
                user_decision_id=decision.user_decision_id,
                task_id=request.task_id,
                session_id=request.session_id,
                correlation_id=correlation_id,
                tool_result_status=result_status,
            )
            if audit_id is not None:
                data["audit_id"] = audit_id
            record = AuditRecord.model_validate(data)
            if record.occurred_at < request.requested_at:
                raise ValueError("Audit time precedes request")
            return record
        except (ValueError, ToolRegistryError):
            raise AuditInputError("Invalid audit evidence or correlation") from None

    def _append(self, record: AuditRecord) -> AuditRecord:
        data = record.model_dump(mode="python")
        data["id"] = data.pop("audit_id")
        with self._session(write=True) as session:
            if session.get(AuditEventRow, record.audit_id) is not None:
                raise AuditConflictError("Audit ID already exists")
            session.add(AuditEventRow(**data))
        return _validated(record, AuditRecord)

    def record_permission_decision(
        self,
        request: PermissionRequest,
        decision: PermissionDecision,
        *,
        occurred_at: datetime,
        audit_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditRecord:
        return self._append(
            self._record(
                request,
                decision,
                occurred_at=occurred_at,
                audit_id=audit_id,
                correlation_id=correlation_id,
            )
        )

    def record_tool_outcome(
        self,
        request: PermissionRequest,
        decision: PermissionDecision,
        result_status: ToolResultStatus,
        *,
        occurred_at: datetime,
        audit_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuditRecord:
        if type(result_status) is not ToolResultStatus or not any(
            result_status is member for member in ToolResultStatus
        ):
            raise AuditInputError("Expected a declared ToolResultStatus")
        return self._append(
            self._record(
                request,
                decision,
                occurred_at=occurred_at,
                audit_id=audit_id,
                correlation_id=correlation_id,
                result_status=result_status,
            )
        )

    def get(self, audit_id: str) -> AuditRecord | None:
        try:
            identity = _identifier.validate_python(audit_id)
        except ValueError:
            raise AuditInputError("Invalid audit ID") from None
        with self._session() as session:
            row = session.get(AuditEventRow, identity)
            return _from_row(row) if row is not None else None

    def list_records(
        self, query: AuditListQuery | None = None
    ) -> tuple[AuditRecord, ...]:
        try:
            accepted = (
                AuditListQuery() if query is None else _validated(query, AuditListQuery)
            )
        except ValueError:
            raise AuditInputError("Invalid audit list query") from None
        statement = select(AuditEventRow)
        for name in (
            "event_kind",
            "request_id",
            "actor_id",
            "tool_id",
            "risk_level",
            "task_id",
            "session_id",
        ):
            value = getattr(accepted, name)
            if value is not None:
                statement = statement.where(getattr(AuditEventRow, name) == value)
        statement = (
            statement.order_by(AuditEventRow.occurred_at.asc(), AuditEventRow.id.asc())
            .limit(accepted.limit)
            .offset(accepted.offset)
        )
        with self._session() as session:
            return tuple(_from_row(row) for row in session.scalars(statement))
