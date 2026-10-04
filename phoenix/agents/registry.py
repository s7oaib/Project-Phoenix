"""Immutable logical identities only; aliases never select execution authority.

Roles describe the frozen roster. They are not prompts, permission grants,
provider bindings or runtime implementations. Construction and lookup do no I/O.
"""

from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

# Match the neutral strict, trimmed, nonempty string contract without coupling
# agent identity metadata to the model subsystem.
_Text = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_internal_id = TypeAdapter(_Text)


class AgentDefinition(BaseModel):
    """Display alias may be removed without changing the authoritative ID/role."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    internal_id: _Text
    alias: _Text | None = None
    professional_role: _Text


class UnknownAgentError(LookupError):
    """A valid authoritative internal ID is absent from the registry."""


class AgentRegistry(BaseModel):
    """Frozen ordered snapshots; no alias fallback, mutation or runtime state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agents: tuple[AgentDefinition, ...] = ()

    @model_validator(mode="after")
    def unique_internal_ids(self) -> Self:
        if len({agent.internal_id for agent in self.agents}) != len(self.agents):
            raise ValueError("Duplicate agent internal ID")
        return self

    def get(self, internal_id: str) -> AgentDefinition | None:
        """Strict ID lookup, trimming outer whitespace without case/alias mapping."""
        identity = _internal_id.validate_python(internal_id)
        return next(
            (agent for agent in self.agents if agent.internal_id == identity), None
        )

    def require(self, internal_id: str) -> AgentDefinition:
        agent = self.get(internal_id)
        if agent is None:
            raise UnknownAgentError("Unknown agent internal ID")
        return agent

    def contains(self, internal_id: str) -> bool:
        return self.get(internal_id) is not None

    def list_agents(self) -> tuple[AgentDefinition, ...]:
        return self.agents


# Order, IDs, aliases and role text are frozen by docs/03_AGENT_ROSTER.md.
DEFAULT_AGENT_DEFINITIONS: tuple[AgentDefinition, ...] = tuple(
    AgentDefinition(internal_id=identity, alias=alias, professional_role=role)
    for identity, alias, role in (
        ("phoenix", "PHOENIX", "Supreme orchestrator"),
        ("strategist", "AIZEN", "Chief strategist/master planner"),
        ("intelligence", "ARMIN", "Intelligence/reporting"),
        ("automation", "LAW", "Automation/workflows"),
        ("mission_coordinator", "ERWIN", "Mission/task coordination"),
        ("project_manager", "NAMI", "Project management"),
        ("build_engineer", "FRANKY", "Build/engineering"),
        ("document_analyst", "ROBIN", "Documents/knowledge extraction"),
        ("system_intelligence", "LELOUCH", "Active system intelligence/monitoring"),
        ("diagnostics", "CHOPPER", "Diagnostics"),
        ("perimeter_security", "GOJO", "Security/perimeter"),
        ("health_biometrics", "MIGHT GUY", "Health/biometrics"),
        ("ethics_compliance", "FUJITORA", "Legal/ethics/compliance"),
        ("media", "BROOK", "Spotify/YouTube/media"),
        ("failsafe", "KAIDO", "Emergency degraded fail-safe"),
        ("infrastructure", "WHITEBEARD", "Infrastructure/stability"),
        ("knowledge_rag", "VEGAPUNK", "Knowledge indexing/RAG"),
        ("background_optimizer", "SHIKAMARU", "Background optimization"),
        ("scenario_analysis", "LIGHT", "Scenario/trade-off analysis"),
        ("heavy_compute", "GARP", "Heavy compute/batch work"),
        ("threat_recon", "SASUKE", "Threat reconnaissance"),
        ("priority_executor", "THORFINN", "High-priority execution"),
        ("user_interaction", "NARUTO", "User interaction support"),
        ("silent_monitor", "ITACHI", "Logs/anomaly monitoring"),
        ("adaptive_recovery", "MAHORAGA", "Adaptive recovery/self-healing"),
        ("memory", "PUDDING", "Long-term memory"),
        ("web_research", "URAHARA", "Real-time web research"),
        ("voice", "UTA", "Voice/wake interface"),
        ("computer_control", "KAKASHI", "Computer control"),
        ("verifier", "LEVI", "Verification/QA"),
    )
)


def build_default_agent_registry() -> AgentRegistry:
    """Build a fresh in-memory snapshot; no global mutable registry instance."""
    return AgentRegistry(agents=DEFAULT_AGENT_DEFINITIONS)
