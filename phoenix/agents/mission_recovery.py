"""PHX-109 bounded MAHORAGA recovery for PHOENIX missions (ADR-093).

The runtime retries a failed model generation only for transient or structural
failures, and only with MAHORAGA's approval (``MahoragaRecovery``, PHX-027):

- responder output that is not JSON, fails its schema, or is truncated: the
  same generation once (a thinking role's truncation retries on the structured
  think:false provider when one is configured);
- provider unavailable or provider error: once, after a bounded backoff and a
  passing health re-check of that provider;
- an AIZEN plan that still fails its schema after its retry: PHOENIX answers on
  the DIRECT path instead (MAHORAGA's ``switch_agent``), an observable fallback
  that never counts as COMPLEX success (the task itself ends FAILED).

Nothing else is retried: LEVI's rejection stands (INV-008), and timeouts,
cancellation, permission decisions, tool outcomes, prompt-boundary refusals and
routing failures stay explicit (INV-021). Every generation retries at most once
and MAHORAGA's finite mission budget (INV-009) bounds the recovery actions;
when it is exhausted, or a retry fails again, the mission fails with the
original failure code. Records hold closed values only (no prompt or output
text), and the runtime publishes them as ``recovery.*`` events (INV-022).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from phoenix.agents.handoff import HandoffBuilder, HandoffEnvelope
from phoenix.agents.recovery import (
    RecoveryAction,
    RecoveryDraft,
    RecoveryRequest,
    RecoveryTrigger,
)
from phoenix.agents.registry import AgentRegistry
from phoenix.agents.responders import ResponderErrorCode, ResponderOutputError
from phoenix.agents.task_graph import TaskGraph
from phoenix.models.provider import (
    Identifier,
    ModelProviderError,
    ProviderUnavailableError,
)

RECOVERY_AGENT_ID = "adaptive_recovery"  # MAHORAGA's internal ID
# The owner's caps (2026-10-18): one retry per generation, three per mission.
MAX_RECOVERY_ACTIONS = 3
THINKING_OFF = "thinking_off"  # the one parameter adjustment PHOENIX makes
_RATIONALE = "PHX-109 deterministic mission recovery policy"
_SUBJECT_OBJECTIVE = "Produce the failed generation again."
_SUBJECT_OUTPUT = "The same output contract as the failed generation."
_SUBJECT_RULE = "The retried output passes every check the original had to pass."


class RecoveryClass(StrEnum):
    """The retryable failures; values equal their MissionFailureCode values."""

    RESPONDER_OUTPUT = "responder_output"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_ERROR = "provider_error"


# Structural output failures only; oversized, invalid text, unknown step and
# disallowed domain are not transient and are never retried.
RETRYABLE_OUTPUT: frozenset[ResponderErrorCode] = frozenset(
    {
        ResponderErrorCode.NOT_JSON,
        ResponderErrorCode.SCHEMA,
        ResponderErrorCode.TRUNCATED,
    }
)


class RecoveryRole(StrEnum):
    """The generation that failed (LEVI's own generations are ``verification``)."""

    DIRECT = "direct"
    PLANNER = "planner"
    ASSIGNMENT = "assignment"
    SPECIALIST = "specialist"
    FINAL_ANSWER = "final_answer"
    VERIFICATION = "verification"


class RecoveryOutcome(StrEnum):
    RECOVERED = "recovered"  # the retry or fallback produced its output
    FAILED = "failed"  # it failed again; the mission keeps the original code
    UNAVAILABLE = "unavailable"  # the health re-check failed: no retry was made
    CANCELLED = "cancelled"  # cancellation or a deadline interrupted it
    EXHAUSTED = "exhausted"  # MAHORAGA's budget was spent: no action was taken


class RecoveryRecord(BaseModel):
    """One recovery decision in MissionTrace: closed values and IDs only."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )

    recovery_id: Identifier
    role: RecoveryRole
    agent_id: Identifier
    step_id: Identifier | None = None
    recovery_class: RecoveryClass
    responder_error: ResponderErrorCode | None = None
    action: RecoveryAction | None = None
    outcome: RecoveryOutcome

    @model_validator(mode="after")
    def consistent_record(self) -> "RecoveryRecord":
        if (self.recovery_class == RecoveryClass.RESPONDER_OUTPUT) != (
            self.responder_error in RETRYABLE_OUTPUT
        ):
            raise ValueError("Only a responder_output recovery carries its error")
        if (self.outcome == RecoveryOutcome.EXHAUSTED) != (self.action is None):
            raise ValueError("Exactly an exhausted recovery takes no action")
        if self.action == RecoveryAction.SWITCH_AGENT and (
            self.role != RecoveryRole.PLANNER
        ):
            raise ValueError("Only a failed plan falls back to the direct path")
        if self.outcome == RecoveryOutcome.UNAVAILABLE and (
            self.recovery_class == RecoveryClass.RESPONDER_OUTPUT
        ):
            raise ValueError("Only provider failures re-check provider health")
        return self

    @property
    def direct_fallback(self) -> bool:
        """PHOENIX answered on the DIRECT path after AIZEN's plan failed."""
        return (
            self.action == RecoveryAction.SWITCH_AGENT
            and self.outcome == RecoveryOutcome.RECOVERED
        )


def classify(error: BaseException) -> tuple[RecoveryClass, ResponderErrorCode | None]:
    """The retryable class of a failure, or raise LookupError if it is final."""
    if isinstance(error, ResponderOutputError):
        if error.code in RETRYABLE_OUTPUT:
            return RecoveryClass.RESPONDER_OUTPUT, error.code
    elif isinstance(error, ProviderUnavailableError):
        return RecoveryClass.PROVIDER_UNAVAILABLE, None
    elif isinstance(error, ModelProviderError):
        return RecoveryClass.PROVIDER_ERROR, None
    raise LookupError("Not a retryable failure")


def retryable(error: BaseException) -> bool:
    try:
        classify(error)
    except LookupError:
        return False
    return True


def trigger(recovery_class: RecoveryClass) -> RecoveryTrigger:
    if recovery_class == RecoveryClass.RESPONDER_OUTPUT:
        return RecoveryTrigger.EXECUTION_FAILURE
    return RecoveryTrigger.RESOURCE_UNAVAILABLE


def summary(
    recovery_class: RecoveryClass, responder_error: ResponderErrorCode | None
) -> str:
    """MAHORAGA's failure summary: closed codes, never model or user text."""
    if responder_error is None:
        return recovery_class.value
    return f"{recovery_class.value}:{responder_error.value}"


class PolicyResponder:
    """MAHORAGA's responder for missions: PHOENIX's fixed policy, no model.

    The runtime chooses the action deterministically; MAHORAGA validates it
    against the failure evidence and the mission's remaining budget.
    """

    def __init__(self, action: RecoveryAction) -> None:
        self._action = action

    async def propose(self, request: RecoveryRequest) -> RecoveryDraft:
        fields: dict[str, object] = {}
        if self._action == RecoveryAction.ADJUST_PARAMETERS:
            fields["parameter_adjustments"] = (THINKING_OFF,)
        elif self._action == RecoveryAction.SWITCH_AGENT:
            fields["replacement_agent_id"] = "phoenix"
        return RecoveryDraft.model_validate(
            {
                "recovery_id": request.recovery_id,
                "action": self._action,
                "rationale": _RATIONALE,
                **fields,
            }
        )


def recovery_subject(
    agents: AgentRegistry,
    handoffs: HandoffBuilder,
    *,
    task_id: str,
    subject_id: str,
    agent_id: str,
    timeout_seconds: float,
) -> tuple[TaskGraph, HandoffEnvelope]:
    """The failed generation as MAHORAGA's one-step evidence graph.

    MAHORAGA correlates a failure with a task graph step and its handoff. A
    generation outside the mission graph (direct, planning, assignment) or
    LEVI's own generation has no such step, so each recovery describes its
    subject as one fixed-text step owned by the failed agent, handed to it by
    MAHORAGA. It is evidence only, never a mission graph or a grant.
    """
    graph = TaskGraph.model_validate(
        {
            "task_id": task_id,
            "steps": (
                {
                    "step_id": subject_id,
                    "owner_agent_id": agent_id,
                    "objective": _SUBJECT_OBJECTIVE,
                    "expected_output": _SUBJECT_OUTPUT,
                    "timeout_seconds": timeout_seconds,
                    "completion_rule": _SUBJECT_RULE,
                },
            ),
        },
        context={"agent_registry": agents},
    )
    handoff = handoffs.build(
        graph=graph,
        step_id=subject_id,
        source_agent_id=RECOVERY_AGENT_ID,
        expected_output_schema=_SUBJECT_OUTPUT,
        verification_required=False,
    )
    return graph, handoff
