"""Explicit degraded capability policy only; no execution or success authority.

Triggers are caller evidence, never inferred from reason text or health probes.
Capability categories are descriptive: even safe limited tool routing grants no
permission. Later PHOENIX integration owns execution, state and event production.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from phoenix.agents.recovery import RecoveryResult, RecoveryStatus
from phoenix.agents.registry import (
    AgentDefinition,
    AgentRegistry,
    UnknownAgentError,
    build_default_agent_registry,
)

_Identifier = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Text = Annotated[str, StringConstraints(strict=True, min_length=1)]


class KaidoFailSafeError(ValueError):
    """Fail-safe identity, request or degraded evidence is invalid."""


def _snapshot(value: object, contract: type[BaseModel]) -> dict[str, object]:
    if not isinstance(value, BaseModel) or type(value) is not contract:
        raise KaidoFailSafeError("Expected an exact fail-safe input contract")
    # model_dump can omit fields injected with model_copy(update=...).
    return {**vars(value), **(value.__pydantic_extra__ or {})}


class FailSafeTrigger(StrEnum):
    PRIMARY_MODEL_PROVIDER_UNAVAILABLE = "primary_model_provider_unavailable"
    RESOURCE_PRESSURE = "resource_pressure"
    DEEP_PROVIDER_UNAVAILABLE = "deep_provider_unavailable"
    CRITICAL_SUBSYSTEM_DEGRADATION = "critical_subsystem_degradation"
    RECOVERY_EXHAUSTED = "recovery_exhausted"


class FailSafeCapability(StrEnum):
    BASIC_CONVERSATION = "basic_conversation"
    DIAGNOSTICS = "diagnostics"
    RECOVERY_GUIDANCE = "recovery_guidance"
    SAFE_LIMITED_TOOL_ROUTING = "safe_limited_tool_routing"


_Capabilities = Annotated[tuple[FailSafeCapability, ...], Field(min_length=1)]


class _Policy(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    trigger: FailSafeTrigger
    reason: _Text

    @model_validator(mode="wrap")
    @classmethod
    def snapshot_contract(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        if isinstance(value, BaseModel):
            value = _snapshot(value, cls)
        return handler(value)

    @field_validator("trigger", mode="before")
    @classmethod
    def textual_trigger(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("Trigger requires a string or its enum member")
        return value

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Reason must contain non-whitespace content")
        return value

    @field_validator(
        "requested_capabilities",
        "enabled_capabilities",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def ordered_capabilities(cls, value: object) -> object:
        if not isinstance(value, (tuple, list)):
            raise ValueError("Capabilities require an ordered tuple or list")
        if any(not isinstance(item, str) for item in value):
            raise ValueError("Capability requires a string or its enum member")
        return tuple(value)

    @field_validator(
        "requested_capabilities", "enabled_capabilities", check_fields=False
    )
    @classmethod
    def unique_capabilities(
        cls, value: tuple[FailSafeCapability, ...]
    ) -> tuple[FailSafeCapability, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Duplicate capability")
        return value


class FailSafeRequest(_Policy):
    """Untrusted caller evidence; capabilities do not authorize any action."""

    failsafe_id: _Identifier = Field(default_factory=lambda: str(uuid4()))
    requested_capabilities: _Capabilities
    recovery_result: RecoveryResult | None = None

    @field_validator("recovery_result", mode="before")
    @classmethod
    def snapshot_recovery(cls, value: object) -> RecoveryResult | None:
        if value is None:
            return None
        return RecoveryResult.model_validate(_snapshot(value, RecoveryResult))

    @model_validator(mode="after")
    def trigger_evidence(self) -> Self:
        if self.trigger == FailSafeTrigger.RECOVERY_EXHAUSTED:
            if (
                self.recovery_result is None
                or self.recovery_result.status != RecoveryStatus.EXHAUSTED
            ):
                raise ValueError("Recovery exhaustion requires exhausted evidence")
        elif self.recovery_result is not None:
            raise ValueError("Recovery evidence is irrelevant to this trigger")
        return self


class FailSafeDecision(_Policy):
    """Observable degraded policy, never task completion or tool permission."""

    failsafe_id: _Identifier
    failsafe_agent_id: Literal["failsafe"] = "failsafe"
    mode: Literal["degraded"] = "degraded"
    enabled_capabilities: _Capabilities
    recovery_id: _Identifier | None = None

    @model_validator(mode="after")
    def correlated_recovery_reference(self) -> Self:
        if (self.trigger == FailSafeTrigger.RECOVERY_EXHAUSTED) != (
            self.recovery_id is not None
        ):
            raise ValueError("Recovery reference contradicts trigger")
        return self


class KaidoFailSafe:
    """Pure synchronous policy selection; registry identity is authoritative."""

    def __init__(self, agent_registry: AgentRegistry | None = None) -> None:
        registry = (
            build_default_agent_registry() if agent_registry is None else agent_registry
        )
        try:
            data = _snapshot(registry, AgentRegistry)
            agents = data.get("agents", ())
            if not isinstance(agents, (tuple, list)):
                raise KaidoFailSafeError("Registry requires ordered definitions")
            data["agents"] = tuple(_snapshot(item, AgentDefinition) for item in agents)
            self._agents = AgentRegistry.model_validate(data)
            self._agents.require("failsafe")
        except (ValidationError, UnknownAgentError):
            raise KaidoFailSafeError("Invalid fail-safe registry identity") from None

    @property
    def failsafe_agent_id(self) -> Literal["failsafe"]:
        return "failsafe"

    def activate(self, request: FailSafeRequest) -> FailSafeDecision:
        """Select precisely the requested categories from exact validated evidence."""
        try:
            request = FailSafeRequest.model_validate(
                _snapshot(request, FailSafeRequest)
            )
        except ValidationError:
            raise KaidoFailSafeError("Invalid fail-safe request evidence") from None
        self._agents.require("failsafe")
        return FailSafeDecision(
            failsafe_id=request.failsafe_id,
            trigger=request.trigger,
            reason=request.reason,
            enabled_capabilities=request.requested_capabilities,
            recovery_id=(
                request.recovery_result.recovery_id
                if request.recovery_result is not None
                else None
            ),
        )
