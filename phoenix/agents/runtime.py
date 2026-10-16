"""PHOENIX's model-backed mission runtime over the frozen orchestration contracts.

PhoenixRuntime is PHOENIX's own runtime (INV-001), not a second orchestrator: it
composes PhoenixOrchestrator, AIZEN, the router, task graph, handoffs and LEVI
without changing them. Steps are text-only unless the caller supplies a
FilesystemTool: then PHX-046 document steps may make one read-only P1 call through
ToolRegistry and PermissionEngine, with caller grants only (see tool_steps). No
events, persistence, recovery or fail-safe behavior exist here. Every failure is
explicit (INV-021); COMPLETED requires every required LEVI verification to pass
(INV-008). WAITING_PERMISSION is a non-terminal outcome: the task stays there,
and a rerun with a grant is a new mission.

Task state is task-level: intermediate step verification runs while RUNNING, and
VERIFYING is the final-answer verification phase. Callers refresh capability
records; provider health is probed once per mission. No resource monitor exists
yet, so every registered candidate is treated as resource eligible.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    model_validator,
)

from phoenix.agents import prompts
from phoenix.agents.handoff import HandoffBuilder
from phoenix.agents.orchestrator import (
    CancellationResult,
    CancellationStatus,
    OrchestrationPath,
    PhoenixOrchestrator,
    PhoenixRequest,
    PhoenixResultKind,
)
from phoenix.agents.planner import AizenPlanner, PlanningRequest
from phoenix.agents.registry import AgentRegistry, build_default_agent_registry
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
    document_grants,
)

# Re-exported for callers that build document-step grants (PHX-046).
from phoenix.agents.tool_steps import DOCUMENT_TOOL_REASON as DOCUMENT_TOOL_REASON
from phoenix.agents.verifier import (
    LeviVerifier,
    VerificationResponder,
    VerificationVerdict,
    verification_requirement_satisfied,
)
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


class StepVerification(_Value):
    step_id: Identifier
    agent_id: Identifier
    verification_id: Identifier
    verdict: VerificationVerdict


class MissionTrace(_Value):
    """Observable evidence without prompts or outputs (hashes/counts only)."""

    prompt_set_version: Identifier
    path: OrchestrationPath
    task_id: Identifier | None = None
    states: tuple[TaskState, ...] = ()
    provider_health: tuple[ProviderRouteHealth, ...] = ()
    generations: tuple[GenerationRecord, ...] = ()
    verifications: tuple[StepVerification, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()


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
    path: OrchestrationPath
    machine: TaskStateMachine | None = None
    health: tuple[ProviderRouteHealth, ...] = ()
    generations: list[GenerationRecord] = field(default_factory=list)
    verifications: list[StepVerification] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)

    def settle(self, state: TaskState) -> None:
        # An illegal edge here is a runtime defect: raise rather than skip it.
        if self.machine is not None:
            self.machine.transition(state)

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
            ),
        )


class PhoenixRuntime:
    """One process/event loop, one active mission per caller task, no workers.

    The caller supplies the path (DIRECT or COMPLEX). Cancellation is cooperative:
    a runtime cancel() yields a CANCELLED result; outside cancellation propagates.
    Callers may add their own deadlines on top of RuntimeLimits.

    structured_providers optionally serve the structured roles (PHOENIX direct
    and assignment, AIZEN, LEVI, and the PHX-047 document_specialist and
    document_answer turns), e.g. adapters constructed with thinking off
    (ADR-080); text specialists and the final answer always use providers.
    Each structured provider must share an ID with a default provider, whose
    health stands for the backend.

    filesystem_tool optionally enables read-only document steps under its
    explicit workspace root. Their grants come only from run()'s caller.
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
        self._active: dict[str, _Mission] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def orchestrator_id(self) -> Literal["phoenix"]:
        return "phoenix"

    @property
    def active_request_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    def _bind_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise PhoenixRuntimeError("Runtime belongs to another event loop")
        self._loop = loop

    async def run(
        self,
        request: PhoenixRequest,
        path: OrchestrationPath,
        *,
        grants: Sequence[PermissionGrant] = (),
        session_id: str | None = None,
    ) -> MissionResult:
        """grants and session_id are caller (user) evidence for document steps."""
        self._bind_loop()
        if type(request) is not PhoenixRequest:
            raise PhoenixRuntimeError("Expected a PhoenixRequest")
        request = PhoenixRequest.model_validate(request)
        directive = _path.validate_python(path)
        try:
            accepted = document_grants(grants)
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
        try:
            async with asyncio.timeout(self._limits.mission_timeout_seconds):
                if directive == OrchestrationPath.DIRECT:
                    text = await self._direct(request, evidence)
                else:
                    text = await self._complex(request, evidence, accepted, session)
            return evidence.result(request, MissionKind.COMPLETED, text=text)
        except asyncio.CancelledError:
            evidence.settle(TaskState.CANCELLED)
            if mission.cancellation_requested and task.uncancel() == 0:
                return evidence.result(request, MissionKind.CANCELLED)
            raise
        except ResponderOutputError as error:
            return self._failed(
                request,
                evidence,
                MissionFailureCode.RESPONDER_OUTPUT,
                responder_error=error.code,
            )
        except ToolPermissionRequired as pending:
            # Non-terminal: the task waits; a rerun with a grant is a new mission.
            evidence.settle(TaskState.WAITING_PERMISSION)
            return evidence.result(
                request, MissionKind.WAITING_PERMISSION, pending=pending.request
            )
        except Exception as error:
            code = _failure_code(error)
            if code is None:
                raise
            return self._failed(request, evidence, code)
        finally:
            del self._active[request.request_id]

    @staticmethod
    def _failed(
        request: PhoenixRequest,
        evidence: _Evidence,
        code: MissionFailureCode,
        *,
        responder_error: ResponderErrorCode | None = None,
    ) -> MissionResult:
        evidence.settle(TaskState.FAILED)
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
        if ProviderHealthStatus.HEALTHY not in statuses.values():
            raise ProviderUnavailableError("No healthy model provider")
        return statuses

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
        documents = domain == RoutingDomain.DOCUMENTS
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

    async def _direct(self, request: PhoenixRequest, evidence: _Evidence) -> str:
        health = await self._health(evidence)
        responder = ModelDirectResponder(self._control("phoenix", health, evidence))
        result = await self._orchestrator.execute(
            request, OrchestrationPath.DIRECT, direct_responder=responder
        )
        assert result.text is not None  # COMPLETED is the only direct outcome.
        return result.text

    async def _complex(
        self,
        request: PhoenixRequest,
        evidence: _Evidence,
        grants: tuple[PermissionGrant, ...],
        session_id: str | None,
    ) -> str:
        limits = self._limits
        handover = await self._orchestrator.execute(request, OrchestrationPath.COMPLEX)
        if handover.kind != PhoenixResultKind.PLANNING_REQUIRED:
            raise PhoenixRuntimeError("Complex path did not require planning")
        machine = TaskStateMachine(str(uuid4()))
        evidence.machine = machine
        machine.transition(TaskState.PLANNING)
        health = await self._health(evidence)

        async with asyncio.timeout(limits.planning_timeout_seconds):
            plan = await self._planner.plan(
                PlanningRequest(request_id=request.request_id, objective=request.text),
                responder=ModelPlanningResponder(
                    self._control("strategist", health, evidence),
                    max_steps=limits.max_plan_steps,
                ),
            )
            assignments = await ModelStepAssignmentResponder(
                self._control("phoenix", health, evidence, tier="assignment"),
                self._guard,
                allowed_domains=self._domains,
            ).assign(plan)

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
        steps = [
            TaskStepDefinition(
                step_id=step.step_id,
                owner_agent_id=routes[step.step_id].agent_id,
                objective=step.objective,
                depends_on=plan_ids[index - 1 : index],
                input_refs=plan_ids[index - 1 : index],
                expected_output=prompts.DOCUMENT_STEP_EXPECTED_OUTPUT
                if step.step_id in documents
                else prompts.STEP_EXPECTED_OUTPUT,
                timeout_seconds=limits.step_timeout_seconds,
                completion_rule=prompts.DOCUMENT_STEP_COMPLETION_RULE
                if step.step_id in documents
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
        machine.transition(TaskState.READY)
        machine.transition(TaskState.RUNNING)

        outputs: dict[str, str] = {}
        for step_id in graph.topological_step_ids:
            step = graph.step(step_id)
            assert step is not None
            final = step_id == FINAL_STEP_ID
            decision = routes[step_id]
            producer = ModelCandidateRef(
                provider_id=decision.provider_id, model_id=decision.model_id
            )
            handoff = self._handoffs.build(
                graph=graph,
                step_id=step_id,
                source_agent_id="phoenix",
                expected_output_schema=step.expected_output,
                verification_required=True,
                permission_scope=DOCUMENT_TOOL_IDS if step_id in documents else (),
            )
            # PHX-047: both document turns run on the structured providers.
            binding = self._binding(
                decision.agent_id, producer, evidence, structured=step_id in documents
            )
            prior = tuple((ref, outputs[ref]) for ref in step.input_refs)
            tool_evidence: ToolEvidence | None = None
            async with asyncio.timeout(step.timeout_seconds):
                if step_id in documents:
                    assert self._documents is not None  # allowlisted only with a tool
                    candidate, tool_evidence = await self._documents.perform(
                        binding,
                        handoff,
                        user_request=request.text,
                        evidence=prior,
                        grants=grants,
                        session_id=session_id,
                        record=evidence.tool_calls.append,
                    )
                else:
                    candidate = await ModelSpecialistResponder(
                        binding, self._guard, final=final
                    ).perform(handoff, user_request=request.text, evidence=prior)
            if final:
                machine.transition(TaskState.VERIFYING)
            verifier = self._control("verifier", health, evidence, avoid=producer)
            responder: VerificationResponder
            if final:
                # PHX-047: LEVI's model sees the verified step outputs it judges.
                responder = FinalVerificationResponder(
                    verifier,
                    self._guard,
                    original_request=request.text,
                    evidence=prior,
                )
            elif tool_evidence is None:
                # PHX-047: grounded in the earlier outputs this step saw, if any.
                responder = StepVerificationResponder(
                    verifier,
                    self._guard,
                    original_request=request.text,
                    evidence=prior,
                )
            else:
                # PHX-047: LEVI's model sees the guarded tool output it verifies.
                responder = ToolEvidenceVerificationResponder(
                    DocumentVerificationResponder(
                        verifier,
                        self._guard,
                        original_request=request.text,
                        evidence=tool_evidence,
                    ),
                    tool_evidence,
                    grants,
                )
            async with asyncio.timeout(limits.verification_timeout_seconds):
                verification = await self._verifier.verify(
                    graph=graph,
                    handoff=handoff,
                    candidate_output=candidate,
                    responder=responder,
                )
            evidence.verifications.append(
                StepVerification(
                    step_id=step_id,
                    agent_id=decision.agent_id,
                    verification_id=verification.verification_id,
                    verdict=verification.verdict,
                )
            )
            if not verification_requirement_satisfied(handoff, verification):
                raise _VerificationFailed
            outputs[step_id] = candidate
        machine.transition(TaskState.COMPLETED)
        return outputs[FINAL_STEP_ID]


def _by_id(providers: Sequence[ModelProvider]) -> dict[str, ModelProvider]:
    found: dict[str, ModelProvider] = {}
    for provider in providers:
        if not isinstance(provider, ModelProvider):
            raise PhoenixRuntimeError("Expected ModelProvider instances")
        if provider.provider_id in found:
            raise PhoenixRuntimeError("Duplicate model provider ID")
        found[provider.provider_id] = provider
    return found


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
    if isinstance(error, ToolStepFailed):
        return MissionFailureCode.TOOL_FAILED
    return None
