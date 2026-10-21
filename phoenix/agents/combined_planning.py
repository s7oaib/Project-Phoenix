"""PHX-121 (ADR-166): AIZEN's planner for any mix of enabled capability families.

A mission whose allowed domains include more than one capability family
(documents, knowledge_rag, web_research, media, computer_control) plans with
the v6 COMBINED_PLANNING_SYSTEM, whose step list holds one line per enabled
capability and nothing about disabled ones. A mission with one family or none
keeps its v5 or control planner unchanged. The reply contract is the planner's:
the responder, not the model, assigns step IDs. The prompt confers no
authority; PHOENIX, ToolRegistry and PermissionEngine decide every action.
"""

from collections.abc import Iterable

from phoenix.agents import prompts
from phoenix.agents.research_steps import ResearchPlanningResponder
from phoenix.agents.responders import ModelBinding
from phoenix.agents.router import RoutingDomain

PLANNING_FAMILIES: frozenset[RoutingDomain] = frozenset(
    {
        RoutingDomain.DOCUMENTS,
        RoutingDomain.KNOWLEDGE_RAG,
        RoutingDomain.WEB_RESEARCH,
        RoutingDomain.MEDIA,
        RoutingDomain.COMPUTER_CONTROL,
    }
)


def combined(domains: Iterable[RoutingDomain]) -> bool:
    """Whether a mission's allowed domains span more than one family."""
    return len(PLANNING_FAMILIES.intersection(domains)) > 1


def capability_lines(keys: Iterable[str]) -> str:
    """The enabled capabilities' lines, in COMBINED_PLANNING_LINES order."""
    enabled = set(keys)
    unknown = enabled - set(prompts.COMBINED_PLANNING_LINES)
    if unknown or not enabled:
        raise ValueError("Expected known combined planning capabilities")
    return "\n".join(
        line for key, line in prompts.COMBINED_PLANNING_LINES.items() if key in enabled
    )


class CombinedPlanningResponder(ResearchPlanningResponder):
    """The v6 combined planner (role marker ``planner``)."""

    ROLE = "planner"

    def __init__(
        self, binding: ModelBinding, *, max_steps: int, capabilities: str
    ) -> None:
        super().__init__(binding, max_steps=max_steps)
        # Filled before the planner's own format(); PHOENIX's lines hold no braces.
        if "{" in capabilities or "}" in capabilities:
            raise ValueError("Capability lines must not contain braces")
        self.SYSTEM = prompts.COMBINED_PLANNING_SYSTEM.replace(
            "{capabilities}", capabilities
        )
