"""Pure normal-specialist routing from caller-supplied, neutral evidence.

Candidate order belongs to the caller. Health and resource facts are observations,
not probes or reservations. A decision is data for PHOENIX, never execution or
permission authority. Benchmark recommendations and permanent model preferences
remain outside this foundation.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from phoenix.agents.registry import (
    AgentRegistry,
    UnknownAgentError,
    build_default_agent_registry,
)
from phoenix.models.provider import Identifier, ModelCapabilities, ProviderHealthStatus
from phoenix.models.registry import CapabilityRequirements, ModelCapabilityRegistry


class RoutingDomain(StrEnum):
    """Explicit task domains; no free-text classification or control roles."""

    INTELLIGENCE = "intelligence"
    AUTOMATION = "automation"
    MISSION_COORDINATION = "mission_coordination"
    PROJECT_MANAGEMENT = "project_management"
    BUILD_ENGINEERING = "build_engineering"
    DOCUMENTS = "documents"
    SYSTEM_INTELLIGENCE = "system_intelligence"
    DIAGNOSTICS = "diagnostics"
    SECURITY = "security"
    HEALTH = "health"
    COMPLIANCE = "compliance"
    MEDIA = "media"
    INFRASTRUCTURE = "infrastructure"
    KNOWLEDGE_RAG = "knowledge_rag"
    OPTIMIZATION = "optimization"
    SCENARIO_ANALYSIS = "scenario_analysis"
    HEAVY_COMPUTE = "heavy_compute"
    THREAT_RECON = "threat_recon"
    PRIORITY_EXECUTION = "priority_execution"
    USER_INTERACTION = "user_interaction"
    SILENT_MONITORING = "silent_monitoring"
    MEMORY = "memory"
    WEB_RESEARCH = "web_research"
    VOICE = "voice"
    COMPUTER_CONTROL = "computer_control"


DOMAIN_AGENT_IDS: Mapping[RoutingDomain, str] = MappingProxyType(
    {
        RoutingDomain.INTELLIGENCE: "intelligence",
        RoutingDomain.AUTOMATION: "automation",
        RoutingDomain.MISSION_COORDINATION: "mission_coordinator",
        RoutingDomain.PROJECT_MANAGEMENT: "project_manager",
        RoutingDomain.BUILD_ENGINEERING: "build_engineer",
        RoutingDomain.DOCUMENTS: "document_analyst",
        RoutingDomain.SYSTEM_INTELLIGENCE: "system_intelligence",
        RoutingDomain.DIAGNOSTICS: "diagnostics",
        RoutingDomain.SECURITY: "perimeter_security",
        RoutingDomain.HEALTH: "health_biometrics",
        RoutingDomain.COMPLIANCE: "ethics_compliance",
        RoutingDomain.MEDIA: "media",
        RoutingDomain.INFRASTRUCTURE: "infrastructure",
        RoutingDomain.KNOWLEDGE_RAG: "knowledge_rag",
        RoutingDomain.OPTIMIZATION: "background_optimizer",
        RoutingDomain.SCENARIO_ANALYSIS: "scenario_analysis",
        RoutingDomain.HEAVY_COMPUTE: "heavy_compute",
        RoutingDomain.THREAT_RECON: "threat_recon",
        RoutingDomain.PRIORITY_EXECUTION: "priority_executor",
        RoutingDomain.USER_INTERACTION: "user_interaction",
        RoutingDomain.SILENT_MONITORING: "silent_monitor",
        RoutingDomain.MEMORY: "memory",
        RoutingDomain.WEB_RESEARCH: "web_research",
        RoutingDomain.VOICE: "voice",
        RoutingDomain.COMPUTER_CONTROL: "computer_control",
    }
)


class PermissionRisk(StrEnum):
    """Descriptive P0-P5 classification; never approval, denial, or a grant."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"
    P5 = "P5"


class _RouteModel(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


class ModelRouteCandidate(_RouteModel):
    """An exact pair and an explicit resource eligibility fact, without scores."""

    provider_id: Identifier
    model_id: Identifier
    resource_eligible: bool = Field(strict=True)


class ProviderRouteHealth(_RouteModel):
    """Caller-supplied provider health; freshness belongs to the caller."""

    provider_id: Identifier
    status: ProviderHealthStatus


class RoutingRequest(_RouteModel):
    """One domain and an ordered, immutable snapshot of routing inputs."""

    route_id: Identifier = Field(default_factory=lambda: str(uuid4()))
    domain: RoutingDomain
    model_candidates: tuple[ModelRouteCandidate, ...] = Field(min_length=1)
    provider_health: tuple[ProviderRouteHealth, ...] = ()
    requirements: CapabilityRequirements
    permission_risk: PermissionRisk

    @field_validator("model_candidates", "provider_health", mode="before")
    @classmethod
    def snapshot_entries(cls, value: object) -> object:
        if isinstance(value, (tuple, list)):
            return tuple(
                entry.model_dump(warnings=False)
                if isinstance(entry, BaseModel)
                else entry
                for entry in value
            )
        return value

    @field_validator("requirements", mode="before")
    @classmethod
    def snapshot_requirements(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(warnings=False)
        return value

    @model_validator(mode="after")
    def unique_evidence(self) -> Self:
        pairs = {(item.provider_id, item.model_id) for item in self.model_candidates}
        if len(pairs) != len(self.model_candidates):
            raise ValueError("Duplicate model candidate pair")
        if len({item.provider_id for item in self.provider_health}) != len(
            self.provider_health
        ):
            raise ValueError("Duplicate provider health evidence")
        return self


class RouteDecision(_RouteModel):
    """Detached technical evidence and identities; no runtime or authority."""

    route_id: Identifier
    domain: RoutingDomain
    agent_id: Identifier
    provider_id: Identifier
    model_id: Identifier
    requirements: CapabilityRequirements
    capabilities: ModelCapabilities
    permission_risk: PermissionRisk

    @field_validator("requirements", "capabilities", mode="before")
    @classmethod
    def snapshot_evidence(cls, value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(warnings=False)
        return value

    @model_validator(mode="after")
    def identities_agree(self) -> Self:
        if self.agent_id != DOMAIN_AGENT_IDS[self.domain]:
            raise ValueError("Route agent contradicts its domain")
        if (self.provider_id, self.model_id) != (
            self.capabilities.provider_id,
            self.capabilities.model_id,
        ):
            raise ValueError("Route model contradicts its capability snapshot")
        return self


class AgentModelRoutingError(LookupError):
    """Routing could not produce a normal-specialist decision."""


class MissingRouteAgentError(AgentModelRoutingError):
    """The authoritative specialist ID is absent; no alias/control fallback."""


class NoEligibleModelError(AgentModelRoutingError):
    """No caller candidate satisfied every normal eligibility condition."""


class AgentModelRouter:
    """Synchronous read-only selection; no provider discovery or execution.

    Agent identities are captured at construction. The capability registry stays
    caller-owned: later explicit registrations affect later routes. Like that
    registry, this contract covers one process/thread, without background work.
    """

    def __init__(
        self,
        agent_registry: AgentRegistry | None = None,
        capability_registry: ModelCapabilityRegistry | None = None,
    ) -> None:
        agents = (
            build_default_agent_registry() if agent_registry is None else agent_registry
        )
        self._agents = AgentRegistry.model_validate(agents.model_dump(warnings=False))
        self._capabilities = (
            ModelCapabilityRegistry()
            if capability_registry is None
            else capability_registry
        )

    def route(self, request: RoutingRequest) -> RouteDecision:
        """Select the first eligible candidate in the supplied order, or fail."""
        request = RoutingRequest.model_validate(request.model_dump(warnings=False))
        agent_id = DOMAIN_AGENT_IDS[request.domain]
        try:
            self._agents.require(agent_id)
        except UnknownAgentError:
            raise MissingRouteAgentError("Mapped route agent is absent") from None

        healthy = {
            item.provider_id
            for item in request.provider_health
            if item.status == ProviderHealthStatus.HEALTHY
        }
        matching = {
            (record.provider_id, record.model_id): record.capabilities
            for record in self._capabilities.matching(request.requirements)
        }
        for candidate in request.model_candidates:
            pair = candidate.provider_id, candidate.model_id
            if (
                candidate.resource_eligible
                and candidate.provider_id in healthy
                and pair in matching
            ):
                return RouteDecision(
                    route_id=request.route_id,
                    domain=request.domain,
                    agent_id=agent_id,
                    provider_id=candidate.provider_id,
                    model_id=candidate.model_id,
                    requirements=request.requirements,
                    capabilities=matching[pair],
                    permission_risk=request.permission_risk,
                )
        raise NoEligibleModelError("No eligible model candidate")
