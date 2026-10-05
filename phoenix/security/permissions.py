"""Exact permission evidence and pure ToolRegistry-derived P0-P5 decisions.

Trusted callers supply user decisions/grants; this boundary neither authenticates
their origin nor manufactures them. Request and metadata text remain inert.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self, TypeVar, cast

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    ModelWrapValidatorHandler,
    StringConstraints,
    field_validator,
    model_validator,
)

from phoenix.tools.registry import ToolRegistry, ToolRiskLevel

_Id = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
_Model = TypeVar("_Model", bound=BaseModel)


class PermissionStatus(StrEnum):
    ALLOWED = "allowed"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"
    BLOCKED = "blocked"


class UserDecision(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"


class GrantDuration(StrEnum):
    ONCE = "once"
    TASK = "task"
    SESSION = "session"
    PERSISTENT = "persistent"


class DecisionReason(StrEnum):
    P0_INTERNAL = "p0_internal"
    EXPLICIT_USER_APPROVAL = "explicit_user_approval"
    EXPLICIT_USER_DENIAL = "explicit_user_denial"
    MATCHING_GRANT = "matching_grant"
    MATCHING_POLICY = "matching_policy"
    P1_SCOPE_REQUIRED = "p1_scope_required"
    P2_APPROVAL_REQUIRED = "p2_approval_required"
    P3_APPROVAL_REQUIRED = "p3_approval_required"
    P4_EXPLICIT_APPROVAL_REQUIRED = "p4_explicit_approval_required"
    P5_BLOCKED = "p5_blocked"


class PermissionInputError(ValueError):
    """Invalid evidence/correlation, without request or private scope details."""


def _vocabulary(value: object, vocabulary: type[StrEnum]) -> object:
    # Pydantic trusts enum-class instances; require a declared member identity.
    if type(value) is not str and not any(value is member for member in vocabulary):
        raise ValueError("Expected exact vocabulary text or a declared enum member")
    return value


def _snapshot(value: object, contract: type[BaseModel]) -> dict[str, object]:
    if type(value) is not contract or not isinstance(value, BaseModel):
        raise ValueError("Expected an exact permission contract")
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
    def stored_values(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        return handler(_snapshot(value, cls) if isinstance(value, BaseModel) else value)

    @field_validator("reason", check_fields=False)
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Reason must contain meaningful text")
        return value

    @field_validator(
        "requested_at",
        "decided_at",
        "issued_at",
        "expires_at",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def ordinary_timestamp(cls, value: object) -> object:
        if value is not None and type(value) not in (datetime, str):
            raise ValueError("Expected an aware timestamp or ISO representation")
        return value

    @field_validator(
        "requested_at", "decided_at", "issued_at", "expires_at", check_fields=False
    )
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    @field_validator("risk_level", mode="before", check_fields=False)
    @classmethod
    def exact_risk(cls, value: object) -> object:
        return _vocabulary(value, ToolRiskLevel)


class PermissionRequest(_Contract):
    request_id: _Id
    actor_id: _Id
    tool_id: _Id
    action: _Id
    resource: _Id
    reason: _Text
    requested_at: AwareDatetime
    task_id: _Id | None = None
    session_id: _Id | None = None


class UserPermissionDecision(_Contract):
    decision_id: _Id
    request_id: _Id
    decision: UserDecision
    decided_at: AwareDatetime

    @field_validator("decision", mode="before")
    @classmethod
    def exact_decision(cls, value: object) -> object:
        return _vocabulary(value, UserDecision)


class PermissionGrant(_Contract):
    grant_id: _Id
    actor_id: _Id
    tool_id: _Id
    action: _Id
    resource: _Id
    reason: _Text
    duration: GrantDuration
    risk_level: ToolRiskLevel
    issued_at: AwareDatetime
    expires_at: AwareDatetime | None = None
    request_id: _Id | None = None
    task_id: _Id | None = None
    session_id: _Id | None = None
    user_decision: Literal["approved"]

    @field_validator("duration", mode="before")
    @classmethod
    def exact_duration(cls, value: object) -> object:
        return _vocabulary(value, GrantDuration)

    @field_validator("user_decision", mode="before")
    @classmethod
    def literal_approval(cls, value: object) -> object:
        if type(value) is not str:
            raise ValueError("Grant approval must be literal approved")
        return value

    @model_validator(mode="after")
    def grant_shape(self) -> Self:
        if self.risk_level not in (
            ToolRiskLevel.P1,
            ToolRiskLevel.P2,
            ToolRiskLevel.P3,
        ):
            raise ValueError("Grants support only P1/P2/P3")
        expected = {
            GrantDuration.ONCE: (True, False, False),
            GrantDuration.TASK: (False, True, False),
            GrantDuration.SESSION: (False, False, True),
            GrantDuration.PERSISTENT: (False, False, False),
        }[self.duration]
        if (
            tuple(
                x is not None for x in (self.request_id, self.task_id, self.session_id)
            )
            != expected
        ):
            raise ValueError("Grant duration and correlation fields disagree")
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("Grant expiry must follow issuance")
        return self


class PermissionPolicyRule(_Contract):
    rule_id: _Id
    actor_id: _Id
    tool_id: _Id
    action: _Id
    resource: _Id
    reason: _Text
    risk_level: ToolRiskLevel

    @model_validator(mode="after")
    def policy_risk(self) -> Self:
        if self.risk_level not in (ToolRiskLevel.P2, ToolRiskLevel.P3):
            raise ValueError("Policy rules support only P2/P3")
        return self


def _scope(
    value: PermissionRequest | PermissionGrant | PermissionPolicyRule,
) -> tuple[str, ...]:
    return (value.actor_id, value.tool_id, value.action, value.resource, value.reason)


class PermissionPolicy(_Contract):
    rules: tuple[PermissionPolicyRule, ...] = ()

    @field_validator("rules", mode="before")
    @classmethod
    def exact_rules(cls, value: object) -> tuple[PermissionPolicyRule, ...]:
        if type(value) not in (tuple, list):
            raise ValueError("Policy requires ordered exact rule contracts")
        return tuple(
            _validated(rule, PermissionPolicyRule)
            for rule in cast(Sequence[object], value)
        )

    @model_validator(mode="after")
    def unique_rules(self) -> Self:
        ids = [rule.rule_id for rule in self.rules]
        scopes = [(*_scope(rule), rule.risk_level) for rule in self.rules]
        if len(set(ids)) != len(ids) or len(set(scopes)) != len(scopes):
            raise ValueError("Duplicate policy ID or effective rule")
        return self


class PermissionDecision(_Contract):
    request_id: _Id
    actor_id: _Id
    tool_id: _Id
    risk_level: ToolRiskLevel
    permission_requirement: _Id
    status: PermissionStatus
    reason_code: DecisionReason
    matched_grant_id: _Id | None = None
    matched_policy_rule_id: _Id | None = None
    user_decision_id: _Id | None = None

    @field_validator("status", mode="before")
    @classmethod
    def exact_status(cls, value: object) -> object:
        return _vocabulary(value, PermissionStatus)

    @field_validator("reason_code", mode="before")
    @classmethod
    def exact_reason(cls, value: object) -> object:
        return _vocabulary(value, DecisionReason)

    @model_validator(mode="after")
    def consistent_evidence(self) -> Self:
        reason = self.reason_code
        risk = self.risk_level
        approval_reasons = {
            ToolRiskLevel.P1: DecisionReason.P1_SCOPE_REQUIRED,
            ToolRiskLevel.P2: DecisionReason.P2_APPROVAL_REQUIRED,
            ToolRiskLevel.P3: DecisionReason.P3_APPROVAL_REQUIRED,
            ToolRiskLevel.P4: DecisionReason.P4_EXPLICIT_APPROVAL_REQUIRED,
        }
        allowed_risks = {
            DecisionReason.P0_INTERNAL: (ToolRiskLevel.P0,),
            DecisionReason.EXPLICIT_USER_APPROVAL: tuple(approval_reasons),
            DecisionReason.EXPLICIT_USER_DENIAL: (ToolRiskLevel.P0, *approval_reasons),
            DecisionReason.MATCHING_GRANT: (
                ToolRiskLevel.P1,
                ToolRiskLevel.P2,
                ToolRiskLevel.P3,
            ),
            DecisionReason.MATCHING_POLICY: (ToolRiskLevel.P2, ToolRiskLevel.P3),
            DecisionReason.P5_BLOCKED: (ToolRiskLevel.P5,),
        }
        expected_status = (
            PermissionStatus.DENIED
            if reason == DecisionReason.EXPLICIT_USER_DENIAL
            else PermissionStatus.BLOCKED
            if reason == DecisionReason.P5_BLOCKED
            else PermissionStatus.APPROVAL_REQUIRED
            if reason in approval_reasons.values()
            else PermissionStatus.ALLOWED
        )
        if (
            self.status != expected_status
            or (
                reason in approval_reasons.values()
                and approval_reasons.get(risk) != reason
            )
            or (reason in allowed_risks and risk not in allowed_risks[reason])
        ):
            raise ValueError("Decision status, reason and risk disagree")
        expected_ids = (
            reason == DecisionReason.MATCHING_GRANT,
            reason == DecisionReason.MATCHING_POLICY,
            reason
            in (
                DecisionReason.EXPLICIT_USER_APPROVAL,
                DecisionReason.EXPLICIT_USER_DENIAL,
            ),
        )
        if (
            tuple(
                x is not None
                for x in (
                    self.matched_grant_id,
                    self.matched_policy_rule_id,
                    self.user_decision_id,
                )
            )
            != expected_ids
        ):
            raise ValueError("Decision evidence identifiers disagree")
        return self


def _grants(value: object) -> tuple[PermissionGrant, ...]:
    if type(value) not in (tuple, list):
        raise ValueError("Grants require an ordered exact contract sequence")
    grants = tuple(
        _validated(x, PermissionGrant) for x in cast(Sequence[object], value)
    )
    ids = [x.grant_id for x in grants]
    scopes = [
        (*_scope(x), x.risk_level, x.duration, x.request_id, x.task_id, x.session_id)
        for x in grants
    ]
    if len(set(ids)) != len(ids) or len(set(scopes)) != len(scopes):
        raise ValueError("Duplicate grant ID or effective scope")
    return grants


def _matches(
    grant: PermissionGrant, request: PermissionRequest, risk: ToolRiskLevel
) -> bool:
    if grant.risk_level != risk or _scope(grant) != _scope(request):
        return False
    if grant.issued_at > request.requested_at or (
        grant.expires_at is not None and grant.expires_at <= request.requested_at
    ):
        return False
    if risk == ToolRiskLevel.P3 and grant.duration not in (
        GrantDuration.TASK,
        GrantDuration.SESSION,
    ):
        return False
    return {
        GrantDuration.ONCE: grant.request_id == request.request_id,
        GrantDuration.TASK: grant.task_id == request.task_id,
        GrantDuration.SESSION: grant.session_id == request.session_id,
        GrantDuration.PERSISTENT: True,
    }[grant.duration]


@dataclass(frozen=True, slots=True, init=False)
class PermissionEngine:
    """Immutable authority snapshot. evaluate returns evidence, never execution."""

    _registry: ToolRegistry
    _policy: PermissionPolicy

    def __init__(
        self, registry: ToolRegistry, policy: PermissionPolicy | None = None
    ) -> None:
        if hasattr(self, "_registry"):
            raise PermissionInputError("Engine is already initialized")
        try:
            if type(registry) is not ToolRegistry:
                raise ValueError("Expected exact ToolRegistry")
            snapshot = ToolRegistry(registry.list_tools())
            for tool in snapshot.list_tools():
                _vocabulary(tool.risk_level, ToolRiskLevel)
            validated_policy = (
                PermissionPolicy()
                if policy is None
                else _validated(policy, PermissionPolicy)
            )
        except ValueError:
            raise PermissionInputError(
                "Invalid permission authority evidence"
            ) from None
        object.__setattr__(self, "_registry", snapshot)
        object.__setattr__(self, "_policy", validated_policy)

    @property
    def policy(self) -> PermissionPolicy:
        return self._policy

    def evaluate(
        self,
        request: PermissionRequest,
        *,
        grants: Sequence[PermissionGrant] = (),
        user_decision: UserPermissionDecision | None = None,
    ) -> PermissionDecision:
        try:
            request = _validated(request, PermissionRequest)
        except ValueError:
            raise PermissionInputError("Invalid permission request") from None
        tool = self._registry.require(request.tool_id)
        risk = tool.risk_level
        try:
            evidence = _grants(grants)
            user = (
                None
                if user_decision is None
                else _validated(user_decision, UserPermissionDecision)
            )
            if user is not None and (
                user.request_id != request.request_id
                or user.decided_at < request.requested_at
            ):
                raise ValueError("User decision does not correlate to request")
        except ValueError:
            raise PermissionInputError(
                "Invalid permission evidence or correlation"
            ) from None

        def result(
            status: PermissionStatus,
            reason: DecisionReason,
            *,
            grant_id: str | None = None,
            rule_id: str | None = None,
            user_id: str | None = None,
        ) -> PermissionDecision:
            return PermissionDecision(
                request_id=request.request_id,
                actor_id=request.actor_id,
                tool_id=tool.tool_id,
                risk_level=risk,
                permission_requirement=tool.permission_requirement,
                status=status,
                reason_code=reason,
                matched_grant_id=grant_id,
                matched_policy_rule_id=rule_id,
                user_decision_id=user_id,
            )

        if risk == ToolRiskLevel.P5:
            return result(PermissionStatus.BLOCKED, DecisionReason.P5_BLOCKED)
        if user is not None and user.decision == UserDecision.DENIED:
            return result(
                PermissionStatus.DENIED,
                DecisionReason.EXPLICIT_USER_DENIAL,
                user_id=user.decision_id,
            )
        if risk == ToolRiskLevel.P0:
            return result(PermissionStatus.ALLOWED, DecisionReason.P0_INTERNAL)
        if user is not None:
            return result(
                PermissionStatus.ALLOWED,
                DecisionReason.EXPLICIT_USER_APPROVAL,
                user_id=user.decision_id,
            )
        if risk in (ToolRiskLevel.P2, ToolRiskLevel.P3):
            for rule in self._policy.rules:
                if rule.risk_level == risk and _scope(rule) == _scope(request):
                    return result(
                        PermissionStatus.ALLOWED,
                        DecisionReason.MATCHING_POLICY,
                        rule_id=rule.rule_id,
                    )
        if risk in (ToolRiskLevel.P1, ToolRiskLevel.P2, ToolRiskLevel.P3):
            for grant in evidence:
                if _matches(grant, request, risk):
                    return result(
                        PermissionStatus.ALLOWED,
                        DecisionReason.MATCHING_GRANT,
                        grant_id=grant.grant_id,
                    )
        reason = {
            ToolRiskLevel.P1: DecisionReason.P1_SCOPE_REQUIRED,
            ToolRiskLevel.P2: DecisionReason.P2_APPROVAL_REQUIRED,
            ToolRiskLevel.P3: DecisionReason.P3_APPROVAL_REQUIRED,
            ToolRiskLevel.P4: DecisionReason.P4_EXPLICIT_APPROVAL_REQUIRED,
        }[risk]
        return result(PermissionStatus.APPROVAL_REQUIRED, reason)
