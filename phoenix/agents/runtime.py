"""PHOENIX's model-backed mission runtime over the frozen orchestration contracts.

PhoenixRuntime is PHOENIX's own runtime (INV-001), not a second orchestrator: it
composes PhoenixOrchestrator, AIZEN, the router, task graph, handoffs and LEVI
without changing them. Steps are text-only unless the caller supplies a
FilesystemTool: then PHX-046 document steps may make one read-only P1 call through
ToolRegistry and PermissionEngine, with caller grants only (see tool_steps). No
persistence or fail-safe (KAIDO) behavior exists here. Every failure is
explicit (INV-021); COMPLETED requires every required LEVI verification to pass
(INV-008). WAITING_PERMISSION is a non-terminal outcome: the task stays there,
and a rerun with a grant is a new mission.

PHX-103 (ADR-087): with an optional event sink the runtime publishes live,
content-free progress (task, step, agent, tool, permission and verification
events; see mission_events), correlated by the mission's request ID and task
ID. Events carry IDs, states, codes and timings only. Publishing never blocks
or fails a mission: refusals are counted, never reported in the result.

PHX-105 (ADR-090): a request run without a path is triaged first (see triage):
one structured generation on the triage tier proposes DIRECT or COMPLEX, and
any triage failure except cancellation falls back to COMPLEX. MissionTrace
records who decided (``triage_outcome``) and, on fallback, the reason code;
PHOENIX's triage agent events carry the same closed values.

PHX-109 (ADR-093): a generation that fails transiently or structurally is
retried once, only with MAHORAGA's approval within its finite mission budget
(INV-009; see mission_recovery), and an AIZEN plan that still fails its schema
falls back to the DIRECT path. LEVI's rejection is never retried (INV-008).
MissionTrace records every recovery decision and recovery.* events publish them,
with closed values only (INV-022); exhaustion keeps the original failure code.

PHX-111 (ADR-135..139): with a WebResearchTool, web research steps search and
read pages only under the user's grant for the search provider; LEVI checks
their citations deterministically, and the final answer carries a Sources list.

Task state is task-level: intermediate step verification runs while RUNNING, and
VERIFYING is the final-answer verification phase. Callers refresh capability
records; provider health is probed once per mission. No resource monitor exists
yet, so every registered candidate is treated as resource eligible.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Self, TypeVar
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_validator,
)

from phoenix.agents import prompts
from phoenix.agents.handoff import HandoffBuilder, HandoffEnvelope
from phoenix.agents.mission_events import (
    EventPublisher,
    EventSink,
    MissionEvents,
    Phase,
    elapsed_ms,
)
from phoenix.agents.mission_recovery import (
    MAX_RECOVERY_ACTIONS,
    RECOVERY_AGENT_ID,
    PolicyResponder,
    RecoveryClass,
    RecoveryOutcome,
    RecoveryRecord,
    RecoveryRole,
    classify,
    recovery_subject,
    retryable,
    summary,
    trigger,
)
from phoenix.agents.orchestrator import (
    CancellationResult,
    CancellationStatus,
    OrchestrationPath,
    PhoenixOrchestrator,
    PhoenixRequest,
    PhoenixResultKind,
)
from phoenix.agents.planner import AizenPlan, AizenPlanner, PlanningRequest
from phoenix.agents.recovery import (
    MahoragaRecovery,
    MahoragaRecoveryError,
    RecoveryAction,
    RecoveryStatus,
)
from phoenix.agents.registry import AgentRegistry, build_default_agent_registry
from phoenix.agents.research_steps import (
    RESEARCH_ROLE,
    RESEARCH_TOOL_IDS,
    ResearchCallRecord,
    ResearchEvidence,
    ResearchPlanningResponder,
    ResearchRateLimited,
    ResearchStepRunner,
    ResearchVerificationResponder,
    mission_grants,
    sources_footer,
)
from phoenix.agents.responders import (
    GenerationRecord,
    ModelBinding,
    ModelDirectResponder,
    ModelPlanningResponder,
    ModelSpecialistResponder,
    ModelStepAssignmentResponder,
    ResponderErrorCode,
    ResponderOutputError,
)
from phoenix.agents.router import (
    DOMAIN_AGENT_IDS,
    AgentModelRouter,
    AgentModelRoutingError,
    ModelRouteCandidate,
    NoEligibleModelError,
    PermissionRisk,
    ProviderRouteHealth,
    RouteDecision,
    RoutingDomain,
    RoutingRequest,
)
from phoenix.agents.task_graph import (
    TaskGraph,
    TaskState,
    TaskStateMachine,
    TaskStepDefinition,
    TaskTransition,
)
from phoenix.agents.tool_steps import (
    DOCUMENT_TOOL_IDS,
    DocumentStepRunner,
    DocumentVerificationResponder,
    FinalVerificationResponder,
    StepVerificationResponder,
    ToolCallRecord,
    ToolEvidence,
    ToolEvidenceVerificationResponder,
    ToolPermissionRequired,
    ToolStepFailed,
    ToolStepRejected,
)

# Re-exported for callers that build document-step grants (PHX-046).
from phoenix.agents.tool_steps import DOCUMENT_TOOL_REASON as DOCUMENT_TOOL_REASON
from phoenix.agents.triage import (
    TRIAGE_ROLE,
    ModelTriageResponder,
    TriageFallbackReason,
    TriageOutcome,
    fallback_reason,
)
from phoenix.agents.verifier import (
    LeviVerifier,
    VerificationResponder,
    VerificationResult,
    VerificationVerdict,
    verification_requirement_satisfied,
)
from phoenix.core.events import EventType
from phoenix.models.lifecycle import ModelLifecycleManager
from phoenix.models.provider import (
    Identifier,
    ModelProvider,
    ModelProviderError,
    ProviderHealthStatus,
    ProviderUnavailableError,
)
from phoenix.models.registry import CapabilityRequirements, ModelCapabilityRegistry
from phoenix.security.permissions import PermissionGrant, PermissionRequest
from phoenix.security.prompt_injection import (
    PromptInjectionError,
    PromptInjectionGuard,
)
from phoenix.tools.filesystem import FilesystemTool
from phoenix.tools.web_research import WebResearchTool

# Text-only specialist domains (PHX-029). PHX-046 adds DOCUMENTS only when the
# runtime holds a FilesystemTool.
TEXT_DOMAINS: tuple[RoutingDomain, ...] = (
    RoutingDomain.INTELLIGENCE,
    RoutingDomain.SCENARIO_ANALYSIS,
    RoutingDomain.BUILD_ENGINEERING,
)
FINAL_STEP_ID = "final"
_path = TypeAdapter(OrchestrationPath)
_identifier = TypeAdapter(Identifier)
_T = TypeVar("_T")


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


class ModelCandidateRef(_Value):
    provider_id: Identifier
    model_id: Identifier


@dataclass(frozen=True)
class ModelTierTable:
    """Provisional ordered candidates per agent internal ID (docs/05 freeze gate)."""

    roles: Mapping[str, tuple[ModelCandidateRef, ...]]
    default: tuple[ModelCandidateRef, ...]

    def candidates(self, agent_id: str) -> tuple[ModelCandidateRef, ...]:
        return self.roles.get(agent_id, self.default)


def default_tier_table() -> ModelTierTable:
    """MASTER_SPEC BLAZE-6 tiers; T0 is always the last fallback."""

    def refs(*models: str) -> tuple[ModelCandidateRef, ...]:
        return tuple(
            ModelCandidateRef(provider_id="ollama", model_id=model) for model in models
        )

    t0, t1, t2, code = "qwen3.5:2b", "qwen3.5:4b", "qwen3.5:9b", "qwen2.5-coder:7b"
    return ModelTierTable(
        roles=MappingProxyType(
            {
                "phoenix": refs(t1, t0),
                # PHX-047 (ADR-083): PHOENIX's step assignment runs on T2.
                "assignment": refs(t2, t1, t0),
                # PHX-105 (ADR-090): PHOENIX's request triage, thinking off.
                TRIAGE_ROLE: refs(t1, t0),
                "strategist": refs(t2, t1, t0),
                "verifier": refs(t2, t1, t0),
                "build_engineer": refs(code, t2, t1, t0),
            }
        ),
        default=refs(t1, t0),
    )


class RuntimeLimits(_Value):
    max_plan_steps: int = Field(default=4, strict=True, ge=1, le=8)
    # Structured roles (thinking off when structured providers are supplied).
    max_output_tokens: int = Field(default=2048, strict=True, gt=0)
    # PHX-047 (ADR-083): roles on the default providers think before answering
    # (text specialists and the final answer), so their cap leaves room for both.
    max_thinking_output_tokens: int = Field(default=4096, strict=True, gt=0)
    # UntrustedContentItem accepts at most 50,000 characters per item.
    max_output_chars: int = Field(default=20000, strict=True, gt=0, le=50000)
    mission_timeout_seconds: float = Field(default=1800.0, gt=0, allow_inf_nan=False)
    planning_timeout_seconds: float = Field(default=300.0, gt=0, allow_inf_nan=False)
    step_timeout_seconds: float = Field(default=300.0, gt=0, allow_inf_nan=False)
    verification_timeout_seconds: float = Field(
        default=300.0, gt=0, allow_inf_nan=False
    )
    # PHX-105: health probe, model selection and the one triage generation.
    triage_timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    # PHX-109: MAHORAGA's mission budget (0 disables recovery) and the wait
    # before a provider failure's health re-check and retry.
    max_recovery_actions: int = Field(
        default=MAX_RECOVERY_ACTIONS, strict=True, ge=0, le=MAX_RECOVERY_ACTIONS
    )
    recovery_backoff_seconds: float = Field(
        default=2.0, ge=0, le=30, allow_inf_nan=False
    )


class ControlRoleModelSelector:
    """The router's eligibility rules for control roles the router excludes.

    First candidate in caller order whose provider is HEALTHY and whose pair
    matches the capability requirements. Selection grants no authority.
    """

    def __init__(self, capability_registry: ModelCapabilityRegistry) -> None:
        self._capabilities = capability_registry

    def select(
        self,
        candidates: Sequence[ModelCandidateRef],
        health: Mapping[str, ProviderHealthStatus],
        requirements: CapabilityRequirements,
    ) -> ModelCandidateRef:
        matching = {
            (record.provider_id, record.model_id)
            for record in self._capabilities.matching(requirements)
        }
        for candidate in candidates:
            if (
                health.get(candidate.provider_id) == ProviderHealthStatus.HEALTHY
                and (candidate.provider_id, candidate.model_id) in matching
            ):
                return candidate
        raise NoEligibleModelError("No eligible model candidate")


class MissionKind(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    WAITING_PERMISSION = "waiting_permission"


class MissionFailureCode(StrEnum):
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_ERROR = "provider_error"
    RESPONDER_OUTPUT = "responder_output"
    PROMPT_BOUNDARY = "prompt_boundary"
    NO_ELIGIBLE_MODEL = "no_eligible_model"
    VERIFICATION_FAILED = "verification_failed"
    TIMEOUT = "timeout"
    TOOL_REJECTED = "tool_rejected"
    TOOL_FAILED = "tool_failed"
    # PHX-111 (ADR-138): the search provider throttled PHOENIX; never retried.
    RATE_LIMITED = "rate_limited"


class StepVerification(_Value):
    step_id: Identifier
    agent_id: Identifier
    verification_id: Identifier
    verdict: VerificationVerdict


class MissionTrace(_Value):
    """Observable evidence without prompts or outputs (hashes/counts only).

    PHX-105: ``triage_outcome`` says who decided ``path``. It is None in traces
    stored before PHX-105, and for a mission that ended before triage decided
    (cancelled, or the mission deadline), whose ``path`` is None too.
    ``triage_reason`` is a fallback's closed code, set only by the triage that
    fell back (a re-run that reuses that decision carries none).

    PHX-109: ``recoveries`` lists MAHORAGA's decisions in order. A recovered
    ``switch_agent`` means PHOENIX answered on the DIRECT path after AIZEN's
    plan failed: ``path`` stays the decided COMPLEX and the task never
    COMPLETED, so it never counts as COMPLEX success.
    """

    prompt_set_version: Identifier
    path: OrchestrationPath | None
    task_id: Identifier | None = None
    states: tuple[TaskState, ...] = ()
    provider_health: tuple[ProviderRouteHealth, ...] = ()
    generations: tuple[GenerationRecord, ...] = ()
    verifications: tuple[StepVerification, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    triage_outcome: TriageOutcome | None = None
    triage_reason: TriageFallbackReason | None = None
    recoveries: tuple[RecoveryRecord, ...] = ()
    # PHX-111: one record per research call (IDs, statuses, counts, hashes).
    research_calls: tuple[ResearchCallRecord, ...] = ()

    @model_validator(mode="after")
    def consistent_triage(self) -> Self:
        if self.triage_reason is not None and (
            self.triage_outcome != TriageOutcome.FALLBACK
        ):
            raise ValueError("Only a triage fallback carries a reason")
        if self.triage_outcome is not None and self.path is None:
            raise ValueError("A triage outcome requires a path")
        if (
            self.triage_outcome == TriageOutcome.FALLBACK
            and self.path != OrchestrationPath.COMPLEX
        ):
            raise ValueError("A triage fallback takes the complex path")
        if any(record.direct_fallback for record in self.recoveries) and (
            self.path != OrchestrationPath.COMPLEX or TaskState.COMPLETED in self.states
        ):
            raise ValueError("A direct fallback never completes the complex task")
        return self


class MissionResult(_Value):
    """PHOENIX's user-facing outcome; only COMPLETED carries answer text.

    WAITING_PERMISSION carries the unattempted PermissionRequest (fixed reason,
    absolute resource) for the caller to decide on; never file content.
    """

    request_id: Identifier
    orchestrator_id: Literal["phoenix"] = "phoenix"
    kind: MissionKind
    text: str | None = Field(default=None, strict=True)
    failure_code: MissionFailureCode | None = None
    responder_error: ResponderErrorCode | None = None
    pending_permission: PermissionRequest | None = None
    trace: MissionTrace

    @model_validator(mode="after")
    def consistent_outcome(self) -> Self:
        if (self.kind == MissionKind.COMPLETED) != (self.text is not None):
            raise ValueError("Only completed missions carry text")
        if (self.kind == MissionKind.FAILED) != (self.failure_code is not None):
            raise ValueError("Only failed missions carry a failure code")
        if (self.failure_code == MissionFailureCode.RESPONDER_OUTPUT) != (
            self.responder_error is not None
        ):
            raise ValueError("Responder error requires a responder_output failure")
        if (self.kind == MissionKind.WAITING_PERMISSION) != (
            self.pending_permission is not None
        ):
            raise ValueError("Only waiting missions carry a pending permission")
        return self


class PhoenixRuntimeError(Exception):
    """A runtime contract or mission lifetime requirement was violated."""


class DuplicateMissionError(PhoenixRuntimeError):
    """The same request ID is already an active mission on this runtime."""


class _VerificationFailed(Exception):
    pass


@dataclass
class _Mission:
    task: asyncio.Task[object]
    cancellation_requested: bool = False


@dataclass
class _Evidence:
    path: OrchestrationPath | None
    triage_outcome: TriageOutcome | None = None
    triage_reason: TriageFallbackReason | None = None
    machine: TaskStateMachine | None = None
    health: tuple[ProviderRouteHealth, ...] = ()
    generations: list[GenerationRecord] = field(default_factory=list)
    verifications: list[StepVerification] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    recoveries: list[RecoveryRecord] = field(default_factory=list)
    research_calls: list[ResearchCallRecord] = field(default_factory=list)
    # PHX-109: a direct fallback already settled the failed task as FAILED.
    task_failed: bool = False

    @property
    def recovery_actions(self) -> int:
        return sum(record.action is not None for record in self.recoveries)

    def settle(self, state: TaskState) -> TaskTransition | None:
        # An illegal edge here is a runtime defect: raise rather than skip it.
        if self.machine is not None:
            if self.task_failed and state == TaskState.FAILED:
                return None
            return self.machine.transition(state)
        return None

    def result(
        self,
        request: PhoenixRequest,
        kind: MissionKind,
        *,
        text: str | None = None,
        failure: MissionFailureCode | None = None,
        responder_error: ResponderErrorCode | None = None,
        pending: PermissionRequest | None = None,
    ) -> MissionResult:
        machine = self.machine
        return MissionResult(
            request_id=request.request_id,
            kind=kind,
            text=text,
            failure_code=failure,
            responder_error=responder_error,
            pending_permission=pending,
            trace=MissionTrace(
                prompt_set_version=prompts.PROMPT_SET_VERSION,
                path=self.path,
                task_id=None if machine is None else machine.task_id,
                states=()
                if machine is None
                else (
                    TaskState.CREATED,
                    *(record.to_state for record in machine.history),
                ),
                provider_health=self.health,
                generations=tuple(self.generations),
                verifications=tuple(self.verifications),
                tool_calls=tuple(self.tool_calls),
                triage_outcome=self.triage_outcome,
                triage_reason=self.triage_reason,
                recoveries=tuple(self.recoveries),
                research_calls=tuple(self.research_calls),
            ),
        )


class PhoenixRuntime:
    """One process/event loop, one active mission per caller task, no workers.

    The caller supplies the path (DIRECT or COMPLEX), or none and PHOENIX
    triages the request (PHX-105). Cancellation is cooperative: a runtime
    cancel() yields a CANCELLED result; outside cancellation propagates.
    Callers may add their own deadlines on top of RuntimeLimits.

    structured_providers optionally serve the structured roles (PHOENIX direct
    and assignment, AIZEN, LEVI, and the PHX-047 document_specialist and
    document_answer turns), e.g. adapters constructed with thinking off
    (ADR-080); text specialists and the final answer always use providers.
    Each structured provider must share an ID with a default provider, whose
    health stands for the backend.

    filesystem_tool optionally enables read-only document steps under its
    explicit workspace root. Their grants come only from run()'s caller.
    research_tool optionally enables PHX-111 web research steps (see
    research_steps), with the same caller-only grants.

    events optionally receives PHX-103 progress events (e.g. an EventBus). It
    must not block; anything it raises is swallowed and counted in
    dropped_events, so a mission never fails or waits because of it.
    """

    def __init__(
        self,
        *,
        providers: Sequence[ModelProvider],
        structured_providers: Sequence[ModelProvider] = (),
        capability_registry: ModelCapabilityRegistry,
        lifecycle: ModelLifecycleManager,
        agent_registry: AgentRegistry | None = None,
        tier_table: ModelTierTable | None = None,
        limits: RuntimeLimits | None = None,
        filesystem_tool: FilesystemTool | None = None,
        events: EventSink | None = None,
        research_tool: WebResearchTool | None = None,
    ) -> None:
        registry = (
            build_default_agent_registry() if agent_registry is None else agent_registry
        )
        self._agents = AgentRegistry.model_validate(registry.model_dump())
        self._providers = _by_id(providers)
        if not self._providers:
            raise PhoenixRuntimeError("At least one model provider is required")
        structured = _by_id(structured_providers)
        if not set(structured) <= set(self._providers):
            raise PhoenixRuntimeError("Structured provider has no default provider")
        self._structured = {**self._providers, **structured}
        self._lifecycle = lifecycle
        self._tiers = default_tier_table() if tier_table is None else tier_table
        self._limits = RuntimeLimits() if limits is None else limits
        self._orchestrator = PhoenixOrchestrator(self._agents)
        self._planner = AizenPlanner(self._agents)
        self._router = AgentModelRouter(self._agents, capability_registry)
        self._selector = ControlRoleModelSelector(capability_registry)
        self._handoffs = HandoffBuilder(self._agents)
        self._verifier = LeviVerifier(self._agents)
        self._guard = PromptInjectionGuard(self._agents)
        self._documents: DocumentStepRunner | None = None
        self._domains = TEXT_DOMAINS
        if filesystem_tool is not None:
            if type(filesystem_tool) is not FilesystemTool:
                raise PhoenixRuntimeError("Expected a FilesystemTool")
            self._documents = DocumentStepRunner(
                filesystem_tool,
                self._guard,
                max_read_bytes=self._limits.max_output_chars,
            )
            self._domains = (*TEXT_DOMAINS, RoutingDomain.DOCUMENTS)
        # PHX-111: web research steps only when the caller supplies the tool.
        self._research: ResearchStepRunner | None = None
        if research_tool is not None:
            if type(research_tool) is not WebResearchTool:
                raise PhoenixRuntimeError("Expected a WebResearchTool")
            self._research = ResearchStepRunner(research_tool, self._guard)
            self._domains = (*self._domains, RoutingDomain.WEB_RESEARCH)
        try:
            self._recovery = MahoragaRecovery(self._agents)
        except MahoragaRecoveryError:
            raise PhoenixRuntimeError("MAHORAGA is absent from the registry") from None
        self._active: dict[str, _Mission] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._events = EventPublisher(events)

    @property
    def orchestrator_id(self) -> Literal["phoenix"]:
        return "phoenix"

    @property
    def active_request_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    @property
    def dropped_events(self) -> int:
        """Events the sink refused or that failed the allowlist (INV-022)."""
        return self._events.dropped

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise PhoenixRuntimeError("Runtime belongs to another event loop")
        self._loop = loop

    async def run(
        self,
        request: PhoenixRequest,
        path: OrchestrationPath | None = None,
        *,
        grants: Sequence[PermissionGrant] = (),
        session_id: str | None = None,
        decided_by: TriageOutcome = TriageOutcome.CLIENT,
    ) -> MissionResult:
        """grants and session_id are caller (user) evidence for document steps.

        Without a path PHOENIX triages the request (PHX-105). With one,
        decided_by records who chose it: the caller (default), or an earlier
        triage whose decision a re-run reuses (DECIDED, or FALLBACK for
        COMPLEX). It is recorded only and changes nothing else.
        """
        self._bind_loop()
        if type(request) is not PhoenixRequest:
            raise PhoenixRuntimeError("Expected a PhoenixRequest")
        request = PhoenixRequest.model_validate(request)
        directive = None if path is None else _path.validate_python(path)
        try:
            decider = TriageOutcome(decided_by)
        except ValueError:
            raise PhoenixRuntimeError("Invalid path decision") from None
        if (directive is None and decider != TriageOutcome.CLIENT) or (
            decider == TriageOutcome.FALLBACK and directive != OrchestrationPath.COMPLEX
        ):
            raise PhoenixRuntimeError("Inconsistent path decision")
        try:
            accepted = mission_grants(grants)
            session = (
                None if session_id is None else _identifier.validate_python(session_id)
            )
        except ValueError:
            raise PhoenixRuntimeError("Invalid caller permission evidence") from None
        if request.request_id in self._active:
            raise DuplicateMissionError("Request ID is already an active mission")
        task = asyncio.current_task()
        if task is None:
            raise PhoenixRuntimeError("A mission requires a caller task")
        if any(active.task is task for active in self._active.values()):
            raise PhoenixRuntimeError("Caller task already owns an active mission")
        mission = _Mission(task)
        self._active[request.request_id] = mission
        evidence = _Evidence(directive)
        if directive is not None:
            evidence.triage_outcome = decider
        events = MissionEvents(self._events, request.request_id)
        try:
            async with asyncio.timeout(self._limits.mission_timeout_seconds):
                if directive is None:
                    directive = await self._triage(request, evidence, events)
                if directive == OrchestrationPath.DIRECT:
                    text = await self._direct(request, evidence, events)
                else:
                    text = await self._complex(
                        request, evidence, events, accepted, session
                    )
            return evidence.result(request, MissionKind.COMPLETED, text=text)
        except asyncio.CancelledError:
            await _state_changed(events, evidence.settle(TaskState.CANCELLED))
            if mission.cancellation_requested and task.uncancel() == 0:
                return evidence.result(request, MissionKind.CANCELLED)
            raise
        except ResponderOutputError as error:
            return await self._failed(
                request,
                evidence,
                events,
                MissionFailureCode.RESPONDER_OUTPUT,
                responder_error=error.code,
            )
        except ToolPermissionRequired as pending:
            # Non-terminal: the task waits; a rerun with a grant is a new mission.
            await _state_changed(events, evidence.settle(TaskState.WAITING_PERMISSION))
            return evidence.result(
                request, MissionKind.WAITING_PERMISSION, pending=pending.request
            )
        except Exception as error:
            code = _failure_code(error)
            if code is None:
                raise
            return await self._failed(request, evidence, events, code)
        finally:
            del self._active[request.request_id]

    @staticmethod
    async def _failed(
        request: PhoenixRequest,
        evidence: _Evidence,
        events: MissionEvents,
        code: MissionFailureCode,
        *,
        responder_error: ResponderErrorCode | None = None,
    ) -> MissionResult:
        codes: dict[str, JsonValue] = {"failure_code": code.value}
        if responder_error is not None:
            codes["responder_error"] = responder_error.value
        await _state_changed(events, evidence.settle(TaskState.FAILED), **codes)
        return evidence.result(
            request, MissionKind.FAILED, failure=code, responder_error=responder_error
        )

    async def cancel(self, request_id: str) -> CancellationResult:
        self._bind_loop()
        identity = _identifier.validate_python(request_id)
        mission = self._active.get(identity)
        if mission is None:
            return CancellationResult(
                request_id=identity, status=CancellationStatus.NOT_FOUND
            )
        if not mission.cancellation_requested:
            mission.cancellation_requested = True
            mission.task.cancel()
        return CancellationResult(
            request_id=identity, status=CancellationStatus.ACCEPTED
        )

    async def _health(self, evidence: _Evidence) -> dict[str, ProviderHealthStatus]:
        if evidence.health:
            # Probed once per mission: triage's probe also serves its path.
            return _healthy({item.provider_id: item.status for item in evidence.health})
        statuses: dict[str, ProviderHealthStatus] = {}
        for identity, provider in self._providers.items():
            try:
                report = await provider.health()
                status = (
                    report.status
                    if report.provider_id == identity
                    else ProviderHealthStatus.UNAVAILABLE
                )
            except ModelProviderError:
                status = ProviderHealthStatus.UNAVAILABLE
            statuses[identity] = status
        evidence.health = tuple(
            ProviderRouteHealth(provider_id=identity, status=status)
            for identity, status in statuses.items()
        )
        return _healthy(statuses)

    def _binding(
        self,
        agent_id: str,
        ref: ModelCandidateRef,
        evidence: _Evidence,
        *,
        structured: bool = False,
    ) -> ModelBinding:
        providers = self._structured if structured else self._providers
        limits = self._limits
        return ModelBinding(
            agent_id=agent_id,
            provider=providers[ref.provider_id],
            model_id=ref.model_id,
            lifecycle=self._lifecycle,
            # max_output_chars still bounds the visible answer either way.
            max_output_tokens=limits.max_output_tokens
            if structured
            else limits.max_thinking_output_tokens,
            max_output_chars=self._limits.max_output_chars,
            record=evidence.generations.append,
        )

    def _control(
        self,
        agent_id: str,
        health: Mapping[str, ProviderHealthStatus],
        evidence: _Evidence,
        *,
        avoid: ModelCandidateRef | None = None,
        tier: str | None = None,
    ) -> ModelBinding:
        candidates = self._tiers.candidates(agent_id)
        if tier is not None:
            # A role-specific row, falling back to the agent's own row.
            candidates = self._tiers.roles.get(tier, candidates)
        if avoid is not None:
            # Prefer a model other than the producer's, keeping it as a fallback.
            candidates = (
                *(item for item in candidates if item != avoid),
                *(item for item in candidates if item == avoid),
            )
        ref = self._selector.select(candidates, health, CapabilityRequirements())
        return self._binding(agent_id, ref, evidence, structured=True)

    def _route(
        self, domain: RoutingDomain, health: Mapping[str, ProviderHealthStatus]
    ) -> RouteDecision:
        documents = domain in (RoutingDomain.DOCUMENTS, RoutingDomain.WEB_RESEARCH)
        return self._router.route(
            RoutingRequest(
                domain=domain,
                model_candidates=tuple(
                    ModelRouteCandidate(
                        provider_id=ref.provider_id,
                        model_id=ref.model_id,
                        resource_eligible=True,
                    )
                    for ref in self._tiers.candidates(DOMAIN_AGENT_IDS[domain])
                ),
                provider_health=tuple(
                    ProviderRouteHealth(provider_id=identity, status=status)
                    for identity, status in health.items()
                ),
                requirements=CapabilityRequirements(),
                permission_risk=PermissionRisk.P1 if documents else PermissionRisk.P0,
            )
        )

    async def _recovering(
        self,
        request: PhoenixRequest,
        evidence: _Evidence,
        events: MissionEvents,
        *,
        role: RecoveryRole,
        binding: ModelBinding,
        attempt: Callable[[ModelBinding], Awaitable[_T]],
        step_id: str | None = None,
        thinking: bool = False,
    ) -> _T:
        """One generation, retried at most once with MAHORAGA's approval (PHX-109).

        A provider failure waits ``recovery_backoff_seconds`` and retries only
        if that provider's health re-check passes. A thinking role's truncated
        output retries on the structured (think:false) provider when one is
        configured. The retry runs under the caller's deadlines, and LEVI still
        verifies whatever it produces.
        """
        try:
            return await attempt(binding)
        except Exception as error:
            if not retryable(error):
                raise
            failure = error
        recovery_class, responder_error = classify(failure)
        retry, action = binding, RecoveryAction.RETRY_SAME
        if thinking and responder_error == ResponderErrorCode.TRUNCATED:
            structured = self._structured[binding.provider.provider_id]
            if structured is not binding.provider:
                retry = replace(binding, provider=structured)
                action = RecoveryAction.ADJUST_PARAMETERS
        async with self._recovery_action(
            request,
            evidence,
            events,
            failure,
            role=role,
            agent_id=binding.agent_id,
            action=action,
            step_id=step_id,
        ) as outcome:
            if recovery_class != RecoveryClass.RESPONDER_OUTPUT:
                await asyncio.sleep(self._limits.recovery_backoff_seconds)
                if not await self._provider_healthy(binding.provider.provider_id):
                    outcome[0] = RecoveryOutcome.UNAVAILABLE
                    raise failure
            return await attempt(retry)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _direct_fallback(
        self,
        request: PhoenixRequest,
        evidence: _Evidence,
        events: MissionEvents,
        failure: ResponderOutputError,
    ) -> str:
        """PHOENIX answers directly after AIZEN's plan failed (PHX-109).

        The COMPLEX task ends FAILED with the plan's codes; the answer is
        PHOENIX's DIRECT output, never a verified COMPLEX result.
        """
        machine = evidence.machine
        assert machine is not None  # the complex task exists before planning
        async with self._recovery_action(
            request,
            evidence,
            events,
            failure,
            role=RecoveryRole.PLANNER,
            agent_id="strategist",
            action=RecoveryAction.SWITCH_AGENT,
        ):
            await _state_changed(
                events,
                machine.transition(TaskState.FAILED),
                failure_code=MissionFailureCode.RESPONDER_OUTPUT.value,
                responder_error=failure.code.value,
            )
            evidence.task_failed = True
            return await self._direct(request, evidence, events)
        raise AssertionError("unreachable")  # pragma: no cover

    @asynccontextmanager
    async def _recovery_action(
        self,
        request: PhoenixRequest,
        evidence: _Evidence,
        events: MissionEvents,
        failure: Exception,
        *,
        role: RecoveryRole,
        agent_id: str,
        action: RecoveryAction,
        step_id: str | None = None,
    ) -> AsyncIterator[list[RecoveryOutcome]]:
        """MAHORAGA approves one action or reports exhaustion; then it is recorded.

        Exhaustion (or a zero budget) re-raises the original failure. A body
        that fails again with a retryable failure also re-raises the original,
        so the mission keeps its first failure code; any other error propagates.
        """
        limit = self._limits.max_recovery_actions
        if limit == 0:
            raise failure
        recovery_class, responder_error = classify(failure)
        machine = evidence.machine
        subject = role.value if step_id is None else f"{step_id}-{role.value}"
        graph, handoff = recovery_subject(
            self._agents,
            self._handoffs,
            task_id=request.request_id if machine is None else machine.task_id,
            subject_id=subject,
            agent_id=agent_id,
            timeout_seconds=self._limits.step_timeout_seconds,
        )
        decision = await self._recovery.recover(
            graph=graph,
            handoff=handoff,
            trigger=trigger(recovery_class),
            failure_summary=summary(recovery_class, responder_error),
            attempts_used=evidence.recovery_actions,
            max_attempts=limit,
            max_replan_steps=1,
            responder=PolicyResponder(action),
        )
        fields: dict[str, JsonValue] = {
            "recovery_id": decision.recovery_id,
            "role": role.value,
            "recovery_class": recovery_class.value,
        }
        if step_id is not None:
            fields["step_id"] = step_id
        if responder_error is not None:
            fields["responder_error"] = responder_error.value
        record = {
            "recovery_id": decision.recovery_id,
            "role": role,
            "agent_id": agent_id,
            "step_id": step_id,
            "recovery_class": recovery_class,
            "responder_error": responder_error,
        }
        if decision.status == RecoveryStatus.EXHAUSTED:
            evidence.recoveries.append(
                RecoveryRecord.model_validate(
                    {**record, "outcome": RecoveryOutcome.EXHAUSTED}
                )
            )
            await events.emit(
                EventType.RECOVERY_EXHAUSTED, agent_id=RECOVERY_AGENT_ID, **fields
            )
            raise failure
        assert decision.proposal is not None
        fields["action"] = decision.proposal.action.value
        record["action"] = decision.proposal.action
        # Recorded when approved, so a nested recovery (the direct fallback's
        # own retry) sees this action spent; the outcome is set on exit.
        index = len(evidence.recoveries)
        outcome = [RecoveryOutcome.FAILED]
        evidence.recoveries.append(
            RecoveryRecord.model_validate({**record, "outcome": outcome[0]})
        )
        await events.emit(
            EventType.RECOVERY_STARTED, agent_id=RECOVERY_AGENT_ID, **fields
        )
        clock = time.monotonic()
        try:
            yield outcome
            outcome[0] = RecoveryOutcome.RECOVERED
        except asyncio.CancelledError:
            outcome[0] = RecoveryOutcome.CANCELLED
            raise
        except Exception as error:
            if retryable(error):
                raise failure from None
            raise
        finally:
            evidence.recoveries[index] = RecoveryRecord.model_validate(
                {**record, "outcome": outcome[0]}
            )
            await events.emit(
                EventType.RECOVERY_COMPLETED,
                agent_id=RECOVERY_AGENT_ID,
                **fields,
                outcome=outcome[0].value,
                duration_ms=elapsed_ms(clock),
            )

    async def _provider_healthy(self, provider_id: str) -> bool:
        """The default instance's health stands for the backend (ADR-081)."""
        try:
            report = await self._providers[provider_id].health()
        except ModelProviderError:
            return False
        return (report.provider_id, report.status) == (
            provider_id,
            ProviderHealthStatus.HEALTHY,
        )

    async def _triage(
        self, request: PhoenixRequest, evidence: _Evidence, events: MissionEvents
    ) -> OrchestrationPath:
        """PHOENIX's path for a request without one (PHX-105, ADR-090).

        Any failure but cancellation falls back to COMPLEX: recorded in the
        trace and published as agent.failed (role triage) with the reason.
        """
        async with _agent_phase(events, "phoenix", TRIAGE_ROLE) as phase:
            try:
                async with asyncio.timeout(self._limits.triage_timeout_seconds):
                    health = await self._health(evidence)
                    responder = ModelTriageResponder(
                        self._control("phoenix", health, evidence, tier=TRIAGE_ROLE)
                    )
                    path = await responder.classify(request)
            except Exception as error:
                reason = fallback_reason(error)
                path = OrchestrationPath.COMPLEX
                evidence.triage_outcome = TriageOutcome.FALLBACK
                evidence.triage_reason = reason
                phase.done = EventType.AGENT_FAILED
                phase.result = {
                    "code": _event_code(error),
                    "triage_outcome": TriageOutcome.FALLBACK.value,
                    "triage_reason": reason.value,
                }
            else:
                evidence.triage_outcome = TriageOutcome.DECIDED
                phase.result["triage_outcome"] = TriageOutcome.DECIDED.value
            phase.result["path"] = path.value
            evidence.path = path
        return path

    async def _direct(
        self, request: PhoenixRequest, evidence: _Evidence, events: MissionEvents
    ) -> str:
        async with _agent_phase(events, "phoenix", "direct"):
            health = await self._health(evidence)
            result = await self._recovering(
                request,
                evidence,
                events,
                role=RecoveryRole.DIRECT,
                binding=self._control("phoenix", health, evidence),
                attempt=lambda binding: self._orchestrator.execute(
                    request,
                    OrchestrationPath.DIRECT,
                    direct_responder=ModelDirectResponder(binding),
                ),
            )
        assert result.text is not None  # COMPLETED is the only direct outcome.
        return result.text

    async def _complex(
        self,
        request: PhoenixRequest,
        evidence: _Evidence,
        events: MissionEvents,
        grants: tuple[PermissionGrant, ...],
        session_id: str | None,
    ) -> str:
        limits = self._limits
        handover = await self._orchestrator.execute(request, OrchestrationPath.COMPLEX)
        if handover.kind != PhoenixResultKind.PLANNING_REQUIRED:
            raise PhoenixRuntimeError("Complex path did not require planning")
        machine = TaskStateMachine(str(uuid4()))
        evidence.machine = machine
        events.task_id = machine.task_id
        await events.emit(EventType.TASK_CREATED, path=OrchestrationPath.COMPLEX.value)
        await _transition(events, machine, TaskState.PLANNING)
        health = await self._health(evidence)

        planning = PlanningRequest(
            request_id=request.request_id, objective=request.text
        )
        plan: AizenPlan | None = None
        try:
            async with asyncio.timeout(limits.planning_timeout_seconds):
                async with _agent_phase(events, "strategist", "planner"):
                    plan = await self._recovering(
                        request,
                        evidence,
                        events,
                        role=RecoveryRole.PLANNER,
                        binding=self._control("strategist", health, evidence),
                        attempt=lambda binding: self._planner.plan(
                            planning,
                            # PHX-111: research_planner only with the tool.
                            responder=(
                                ModelPlanningResponder
                                if self._research is None
                                else ResearchPlanningResponder
                            )(binding, max_steps=limits.max_plan_steps),
                        ),
                    )
                async with _agent_phase(events, "phoenix", "assignment"):
                    planned = plan
                    assignments = await self._recovering(
                        request,
                        evidence,
                        events,
                        role=RecoveryRole.ASSIGNMENT,
                        binding=self._control(
                            "phoenix", health, evidence, tier="assignment"
                        ),
                        attempt=lambda binding: ModelStepAssignmentResponder(
                            binding, self._guard, allowed_domains=self._domains
                        ).assign(planned),
                    )
        except ResponderOutputError as error:
            if plan is not None or error.code != ResponderErrorCode.SCHEMA:
                raise
            # PHX-109: the plan failed its schema even after its retry.
            return await self._direct_fallback(request, evidence, events, error)

        routes = {
            item.step_id: self._route(item.domain, health)
            for item in assignments.assignments
        }
        routes[FINAL_STEP_ID] = self._route(RoutingDomain.USER_INTERACTION, health)
        plan_ids = tuple(step.step_id for step in plan.steps)
        documents = {
            step_id
            for step_id, route in routes.items()
            if route.domain == RoutingDomain.DOCUMENTS
        }
        research = {
            step_id
            for step_id, route in routes.items()
            if route.domain == RoutingDomain.WEB_RESEARCH
        }
        steps = [
            TaskStepDefinition(
                step_id=step.step_id,
                owner_agent_id=routes[step.step_id].agent_id,
                objective=step.objective,
                depends_on=plan_ids[index - 1 : index],
                input_refs=plan_ids[index - 1 : index],
                expected_output=prompts.DOCUMENT_STEP_EXPECTED_OUTPUT
                if step.step_id in documents
                else prompts.RESEARCH_STEP_EXPECTED_OUTPUT
                if step.step_id in research
                else prompts.STEP_EXPECTED_OUTPUT,
                timeout_seconds=limits.step_timeout_seconds,
                completion_rule=prompts.DOCUMENT_STEP_COMPLETION_RULE
                if step.step_id in documents
                else prompts.RESEARCH_STEP_COMPLETION_RULE
                if step.step_id in research
                else prompts.STEP_COMPLETION_RULE,
            )
            for index, step in enumerate(plan.steps)
        ]
        steps.append(
            TaskStepDefinition(
                step_id=FINAL_STEP_ID,
                owner_agent_id=routes[FINAL_STEP_ID].agent_id,
                objective=prompts.FINAL_OBJECTIVE,
                depends_on=plan_ids[-1:],
                input_refs=plan_ids,
                expected_output=prompts.FINAL_EXPECTED_OUTPUT,
                timeout_seconds=limits.step_timeout_seconds,
                completion_rule=prompts.FINAL_COMPLETION_RULE,
            )
        )
        graph = TaskGraph.model_validate(
            {"task_id": machine.task_id, "steps": steps},
            context={"agent_registry": self._agents},
        )
        await _transition(events, machine, TaskState.READY)
        await _transition(events, machine, TaskState.RUNNING)

        outputs: dict[str, str] = {}
        cited: list[ResearchEvidence] = []
        for step_id in graph.topological_step_ids:
            step = graph.step(step_id)
            assert step is not None
            final = step_id == FINAL_STEP_ID
            decision = routes[step_id]
            step_clock = time.monotonic()
            await events.emit(
                EventType.STEP_STARTED,
                agent_id=decision.agent_id,
                step_id=step_id,
                domain=decision.domain.value,
                final=final,
            )
            producer = ModelCandidateRef(
                provider_id=decision.provider_id, model_id=decision.model_id
            )
            handoff = self._handoffs.build(
                graph=graph,
                step_id=step_id,
                source_agent_id="phoenix",
                expected_output_schema=step.expected_output,
                verification_required=True,
                permission_scope=DOCUMENT_TOOL_IDS
                if step_id in documents
                else RESEARCH_TOOL_IDS
                if step_id in research
                else (),
            )
            # PHX-047: both document turns run on the structured providers;
            # PHX-111: so do both research turns.
            binding = self._binding(
                decision.agent_id,
                producer,
                evidence,
                structured=step_id in documents or step_id in research,
            )
            prior = tuple((ref, outputs[ref]) for ref in step.input_refs)
            tool_evidence: ToolEvidence | None = None
            research_evidence: ResearchEvidence | None = None
            role = (
                "final_answer"
                if final
                else "document"
                if step_id in documents
                else RESEARCH_ROLE
                if step_id in research
                else "specialist"
            )
            # Outside the step deadline, so a step timeout reports "timeout".
            async with _agent_phase(events, decision.agent_id, role, step_id):
                async with asyncio.timeout(step.timeout_seconds):
                    if step_id in documents:
                        assert self._documents is not None  # allowlisted with a tool
                        candidate, tool_evidence = await self._documents.perform(
                            binding,
                            handoff,
                            user_request=request.text,
                            evidence=prior,
                            grants=grants,
                            session_id=session_id,
                            record=evidence.tool_calls.append,
                            events=events,
                        )
                    elif step_id in research:
                        assert self._research is not None  # allowlisted with a tool
                        candidate, research_evidence = await self._research.perform(
                            binding,
                            handoff,
                            user_request=request.text,
                            evidence=prior,
                            grants=grants,
                            session_id=session_id,
                            record=evidence.research_calls.append,
                            events=events,
                        )
                    else:
                        candidate = await self._recovering(
                            request,
                            evidence,
                            events,
                            role=RecoveryRole.FINAL_ANSWER
                            if final
                            else RecoveryRole.SPECIALIST,
                            binding=binding,
                            attempt=_specialist(
                                self._guard, handoff, request.text, prior, final
                            ),
                            step_id=step_id,
                            thinking=True,
                        )
            if final:
                await _transition(events, machine, TaskState.VERIFYING)
            async with events.phase(
                EventType.VERIFICATION_STARTED,
                EventType.VERIFICATION_PASSED,
                agent_id="verifier",
                failed=_verification_failed,
                step_id=step_id,
            ) as check:
                verifier = self._control("verifier", health, evidence, avoid=producer)
                levi = _levi_responder(
                    self._guard,
                    request.text,
                    prior,
                    final,
                    tool_evidence,
                    grants,
                    research_evidence,
                )
                async with asyncio.timeout(limits.verification_timeout_seconds):
                    # PHX-109: only LEVI's failed generation is retried, never
                    # its verdict: a FAILED result returns from here as is.
                    verification = await self._recovering(
                        request,
                        evidence,
                        events,
                        role=RecoveryRole.VERIFICATION,
                        binding=verifier,
                        attempt=_verification(
                            self._verifier, graph, handoff, candidate, levi
                        ),
                        step_id=step_id,
                    )
                satisfied = verification_requirement_satisfied(handoff, verification)
                check.result = {
                    "verification_id": verification.verification_id,
                    "verdict": verification.verdict.value,
                }
                if not satisfied:
                    check.done = EventType.VERIFICATION_FAILED
                    check.result["code"] = MissionFailureCode.VERIFICATION_FAILED.value
            evidence.verifications.append(
                StepVerification(
                    step_id=step_id,
                    agent_id=decision.agent_id,
                    verification_id=verification.verification_id,
                    verdict=verification.verdict,
                )
            )
            if not satisfied:
                raise _VerificationFailed
            outputs[step_id] = candidate
            if research_evidence is not None:
                cited.append(research_evidence)
            await events.emit(
                EventType.STEP_COMPLETED,
                agent_id=decision.agent_id,
                step_id=step_id,
                duration_ms=elapsed_ms(step_clock),
            )
        await _transition(events, machine, TaskState.COMPLETED)
        # PHX-111 (ADR-139): the answer carries the LEVI-verified citations.
        footer = sources_footer(cited)
        if footer:
            return f"{outputs[FINAL_STEP_ID]}\n\n{footer}"
        return outputs[FINAL_STEP_ID]


def _specialist(
    guard: PromptInjectionGuard,
    handoff: HandoffEnvelope,
    user_request: str,
    prior: tuple[tuple[str, str], ...],
    final: bool,
) -> Callable[[ModelBinding], Awaitable[str]]:
    """One text step's generation on a given binding (PHX-109 retries it)."""

    def perform(binding: ModelBinding) -> Awaitable[str]:
        return ModelSpecialistResponder(binding, guard, final=final).perform(
            handoff, user_request=user_request, evidence=prior
        )

    return perform


def _levi_responder(
    guard: PromptInjectionGuard,
    user_request: str,
    prior: tuple[tuple[str, str], ...],
    final: bool,
    tool_evidence: ToolEvidence | None,
    grants: tuple[PermissionGrant, ...],
    research_evidence: ResearchEvidence | None = None,
) -> Callable[[ModelBinding], VerificationResponder]:
    """LEVI's responder for a step on a given binding."""

    def responder(verifier: ModelBinding) -> VerificationResponder:
        if research_evidence is not None:
            # PHX-111 (ADR-139): deterministic citation checks, no generation.
            return ResearchVerificationResponder(research_evidence, grants)
        if final:
            # PHX-047: LEVI's model sees the verified step outputs it judges.
            return FinalVerificationResponder(
                verifier, guard, original_request=user_request, evidence=prior
            )
        if tool_evidence is None:
            # PHX-047: grounded in the earlier outputs this step saw, if any.
            return StepVerificationResponder(
                verifier, guard, original_request=user_request, evidence=prior
            )
        # PHX-047: LEVI's model sees the guarded tool output it verifies.
        return ToolEvidenceVerificationResponder(
            DocumentVerificationResponder(
                verifier,
                guard,
                original_request=user_request,
                evidence=tool_evidence,
            ),
            tool_evidence,
            grants,
        )

    return responder


def _verification(
    verifier: LeviVerifier,
    graph: TaskGraph,
    handoff: HandoffEnvelope,
    candidate: str,
    levi: Callable[[ModelBinding], VerificationResponder],
) -> Callable[[ModelBinding], Awaitable[VerificationResult]]:
    """One LEVI verification on a given binding (PHX-109 retries it)."""

    def verify(binding: ModelBinding) -> Awaitable[VerificationResult]:
        return verifier.verify(
            graph=graph,
            handoff=handoff,
            candidate_output=candidate,
            responder=levi(binding),
        )

    return verify


def _healthy(
    statuses: dict[str, ProviderHealthStatus],
) -> dict[str, ProviderHealthStatus]:
    if ProviderHealthStatus.HEALTHY not in statuses.values():
        raise ProviderUnavailableError("No healthy model provider")
    return statuses


def _by_id(providers: Sequence[ModelProvider]) -> dict[str, ModelProvider]:
    found: dict[str, ModelProvider] = {}
    for provider in providers:
        if not isinstance(provider, ModelProvider):
            raise PhoenixRuntimeError("Expected ModelProvider instances")
        if provider.provider_id in found:
            raise PhoenixRuntimeError("Duplicate model provider ID")
        found[provider.provider_id] = provider
    return found


async def _state_changed(
    events: MissionEvents, transition: TaskTransition | None, **codes: JsonValue
) -> None:
    if transition is not None:
        await events.emit(
            EventType.TASK_STATE_CHANGED,
            agent_id=None,
            scope="task",
            from_state=transition.from_state.value,
            to_state=transition.to_state.value,
            **codes,
        )


async def _transition(
    events: MissionEvents, machine: TaskStateMachine, state: TaskState
) -> None:
    await _state_changed(events, machine.transition(state))


def _agent_phase(
    events: MissionEvents, agent_id: str, role: str, step_id: str | None = None
) -> Phase:
    """agent.started, then agent.completed or agent.failed (PHX-103)."""
    fields: dict[str, JsonValue] = {"role": role}
    if step_id is not None:
        fields["step_id"] = step_id
    phase = events.phase(
        EventType.AGENT_STARTED,
        EventType.AGENT_COMPLETED,
        agent_id=agent_id,
        failed=_agent_ended,
        **fields,
    )
    phase.result["outcome"] = "completed"
    return phase


def _agent_ended(error: BaseException) -> tuple[EventType, dict[str, JsonValue]]:
    if isinstance(error, ToolPermissionRequired):
        # The agent did its part; the step now waits for the user's decision.
        return EventType.AGENT_COMPLETED, {"outcome": "waiting_permission"}
    return EventType.AGENT_FAILED, {"code": _event_code(error)}


def _verification_failed(
    error: BaseException,
) -> tuple[EventType, dict[str, JsonValue]]:
    return EventType.VERIFICATION_FAILED, {"code": _event_code(error)}


def _event_code(error: BaseException) -> str:
    """A closed code for an event; the mission's own result is unaffected.

    Cancellation, including a mission or planning deadline cutting a phase
    short, is "cancelled"; the task's terminal event carries the final code.
    """
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if isinstance(error, ResponderOutputError):
        return MissionFailureCode.RESPONDER_OUTPUT.value
    code = _failure_code(error) if isinstance(error, Exception) else None
    return "internal_error" if code is None else code.value


def _failure_code(error: Exception) -> MissionFailureCode | None:
    """Known explicit failures only; anything else is a defect and propagates."""
    if isinstance(error, TimeoutError):
        return MissionFailureCode.TIMEOUT
    if isinstance(error, ProviderUnavailableError):
        return MissionFailureCode.PROVIDER_UNAVAILABLE
    if isinstance(error, ModelProviderError):
        return MissionFailureCode.PROVIDER_ERROR
    if isinstance(error, PromptInjectionError):
        return MissionFailureCode.PROMPT_BOUNDARY
    if isinstance(error, AgentModelRoutingError):
        return MissionFailureCode.NO_ELIGIBLE_MODEL
    if isinstance(error, _VerificationFailed):
        return MissionFailureCode.VERIFICATION_FAILED
    if isinstance(error, ToolStepRejected):
        return MissionFailureCode.TOOL_REJECTED
    if isinstance(error, ResearchRateLimited):
        return MissionFailureCode.RATE_LIMITED
    if isinstance(error, ToolStepFailed):
        return MissionFailureCode.TOOL_FAILED
    return None
