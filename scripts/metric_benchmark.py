"""PHOENIX metric-dataset runner (PHX-117, ADR-115-ADR-118).

Makes the docs/24 metrics that need their own data measurable, each reported
as MEASURED with its value or NOT_RUNNABLE with a reason (ADR-079):

- ``verification_catch_rate``: ``benchmarks/metrics/verification-v1.toml``.
  Each case is a forced-COMPLEX ``PhoenixRuntime`` mission whose producer
  turns are scripted from the case (the injected fault), whose document step
  reads a fixture workspace through the real FilesystemTool and
  PermissionEngine, and whose LEVI verifier turns are the model under test.
  ``--scripted`` (CI) answers LEVI's model turns with an always-pass verifier,
  so its catch rate is the deterministic floor: harness validation, not a FYP
  number. A live run sends LEVI's turns to Ollama.
- ``user_intervention_count``: ``benchmarks/metrics/interventions-v1.toml``.
  Missions start with no grants; each WAITING_PERMISSION is one permission
  prompt, approved for exactly the pending request before the re-run.
- ``recovery_success``: read from configuration (2) traces
  (``--config2-results``, PHX-109 ``MissionTrace.recoveries``).
- ``memory_retrieval_accuracy``, ``research_evidence_quality`` and
  ``computer_control_completion``: datasets and scorers are validated by
  ``--dry-run`` (including fixed calibration vectors); the live metric stays
  NOT_RUNNABLE until its ticket (PHX-110/111/112) is DONE and an adapter is
  registered in ``ADAPTERS``.

Results are schema-versioned JSON plus a Markdown summary in the gitignored
``.metric-bench/``; they hold IDs, closed codes and counts only, never
prompts, answers or file content.

Usage::

    python scripts/metric_benchmark.py --dry-run      # validate every set
    python scripts/metric_benchmark.py --scripted     # CI: scripted models
    python scripts/metric_benchmark.py                # live LEVI / missions
    python scripts/metric_benchmark.py --config2-results \
        .fyp-bench/fyp-multi-agent-results.json      # add recovery_success
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import re
import sys
import tempfile
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
for _path in (ROOT, SCRIPTS):  # allow running from scripts/ or via importlib
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import fyp_benchmark as baseline  # noqa: E402
import metric_datasets as md  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from alembic import command  # noqa: E402
from phoenix.agents import prompts  # noqa: E402
from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest  # noqa: E402
from phoenix.agents.runtime import (  # noqa: E402
    DOCUMENT_TOOL_REASON,
    MissionKind,
    MissionResult,
    PhoenixRuntime,
    RuntimeLimits,
    default_tier_table,
)
from phoenix.models.lifecycle import ModelLifecycleManager  # noqa: E402
from phoenix.models.provider import (  # noqa: E402
    CancellationResult,
    CancellationStatus,
    GenerationChunk,
    GenerationRequest,
    GenerationResponse,
    ModelCapabilities,
    ModelProvider,
    ModelProviderError,
    ProviderHealth,
    ProviderHealthStatus,
    TokenUsage,
)
from phoenix.models.registry import ModelCapabilityRegistry  # noqa: E402
from phoenix.security.audit import AuditTrail  # noqa: E402
from phoenix.security.permissions import (  # noqa: E402
    PermissionGrant,
    PermissionPolicy,
    PermissionRequest,
)
from phoenix.tools.filesystem import (  # noqa: E402
    FilesystemCall,
    FilesystemTool,
    FilesystemToolResult,
    filesystem_tool_definitions,
)
from phoenix.tools.registry import ToolRegistry  # noqa: E402

SCHEMA_VERSION = 1
TOOL = "phoenix-metric-benchmark"
DEFAULT_OUT = ROOT / ".metric-bench"
RESULTS_NAME = "metric-benchmark-results.json"
SUMMARY_NAME = "metric-benchmark-summary.md"
FEATURE_TICKETS = ROOT / "FEATURE_TICKETS.md"
SESSION_ID = "metric-session"
DOCUMENT_ACTOR = "document_analyst"
VERIFIER_ROLES = frozenset(
    {"verifier", "step_verifier", "document_verifier", "final_verifier"}
)
SCRIPTED_PASS = json.dumps(
    {
        "checks": [
            {"criterion": "Correct", "passed": True, "evidence_summary": "Scripted."}
        ]
    }
)
SCRIPTED_NOTE = (
    "Scripted CI run: LEVI's model verifier always passes, so the catch rate is "
    "the deterministic floor (forged tool evidence and forged verdicts only). "
    "It validates the harness and is not a FYP number; run without --scripted "
    "for LEVI's live catch rate."
)
# Capability-gated metrics: (ticket, title). Live metrics need the ticket DONE
# and an adapter registered in ADAPTERS (ADR-117).
CAPABILITIES = {
    "memory_retrieval_accuracy": ("PHX-110", "memory"),
    "research_evidence_quality": ("PHX-111", "research"),
    "computer_control_completion": ("PHX-112", "computer_control"),
}
# metric id -> live adapter; empty until PHX-110/111/112 land.
ADAPTERS: dict[str, Callable[..., Any]] = {}
METRIC_ORDER = (
    "verification_catch_rate",
    "user_intervention_count",
    "recovery_success",
    "memory_retrieval_accuracy",
    "research_evidence_quality",
    "computer_control_completion",
)
_ROLE = re.compile(r"PHOENIX ROLE: ([a-z_]+)")


def role_of(request: GenerationRequest) -> str:
    match = _ROLE.match(request.messages[0].text)
    if match is None:
        raise ModelProviderError("Request carries no PHOENIX role")
    return match.group(1)


def tool_request(relative_path: str) -> str:
    return json.dumps(
        {"tool_request": {"operation": "read_text", "relative_path": relative_path}}
    )


# ---- scripted producer turns


@dataclass
class MissionScript:
    """One mission's producer replies, in step order; reset for each re-run."""

    steps: tuple[str, ...]
    domains: tuple[str, ...]
    documents: tuple[str, ...]
    step_outputs: tuple[str, ...]
    final: str
    _used: dict[str, int] = field(default_factory=dict)

    def reset(self) -> None:
        self._used.clear()

    def _next(self, role: str, items: list[str]) -> str:
        index = self._used.get(role, 0)
        self._used[role] = index + 1
        if not items:
            raise ModelProviderError(f"No scripted reply for {role}")
        return items[min(index, len(items) - 1)]

    def reply(self, role: str) -> str:
        pairs = list(zip(self.domains, self.step_outputs, self.documents, strict=True))
        if role == "planner":
            return json.dumps({"steps": [{"objective": s} for s in self.steps]})
        if role == "assignment":
            return json.dumps(
                {
                    "assignments": [
                        {"step_id": f"s{index}", "domain": domain}
                        for index, domain in enumerate(self.domains, start=1)
                    ]
                }
            )
        if role == "specialist":
            return self._next(role, [o for d, o, _ in pairs if d == "intelligence"])
        if role == "document_specialist":
            files = [tool_request(f) for d, _, f in pairs if d == "documents"]
            return self._next(role, files)
        if role == "document_answer":
            return self._next(role, [o for d, o, _ in pairs if d == "documents"])
        if role in ("final_answer", "direct"):
            return self.final
        raise ModelProviderError(f"No scripted reply for {role}")


def verification_script(case: md.VerificationCase) -> MissionScript:
    documents = tuple(
        case.document or "" if domain == "documents" else "" for domain in case.domains
    )
    return MissionScript(
        steps=case.steps,
        domains=case.domains,
        documents=documents,
        step_outputs=case.step_outputs,
        final=case.final,
    )


def intervention_script(case: md.InterventionCase) -> MissionScript:
    return MissionScript(
        steps=case.steps,
        domains=case.domains,
        documents=case.documents,
        step_outputs=case.step_outputs,
        final=case.final,
    )


def capabilities_for(model_id: str, provider_id: str = "ollama") -> ModelCapabilities:
    return ModelCapabilities(
        provider_id=provider_id,
        model_id=model_id,
        supports_streaming=True,
        supports_structured_output=False,
        supports_tool_use=True,
        supports_vision=False,
        supports_cancellation=True,
        context_limit_tokens=16384,
    )


class FaultInjectionProvider(ModelProvider):
    """Routes each turn by its PHOENIX role (ADR-116).

    With a script loaded, producer turns get the script's reply (the injected
    fault). LEVI's verifier turns, and every turn without a script, go to
    ``inner`` (a live provider); with no inner provider, LEVI gets the
    scripted always-pass verifier.
    """

    def __init__(self, inner: ModelProvider | None = None) -> None:
        super().__init__("ollama" if inner is None else inner.provider_id)
        self.inner = inner
        self.script: MissionScript | None = None
        self.roles: list[str] = []

    async def health(self) -> ProviderHealth:
        if self.inner is not None:
            return await self.inner.health()
        return ProviderHealth(
            provider_id=self.provider_id, status=ProviderHealthStatus.HEALTHY
        )

    async def capabilities(self, model_id: str) -> ModelCapabilities:
        if self.inner is not None:
            return await self.inner.capabilities(model_id)
        return capabilities_for(model_id, self.provider_id)

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        role = role_of(request)
        self.roles.append(role)
        scripted = self.script is not None and role not in VERIFIER_ROLES
        if not scripted and self.inner is not None:
            return await self.inner.generate(request)
        if scripted:
            assert self.script is not None
            text = self.script.reply(role)
        elif role in VERIFIER_ROLES:
            text = SCRIPTED_PASS
        else:
            raise ModelProviderError(f"No scripted reply for {role}")
        return GenerationResponse(
            operation_id=request.operation_id,
            provider_id=self.provider_id,
            model_id=request.model_id,
            text=text,
            finish_reason="stop",
            usage=TokenUsage(input_tokens=0, output_tokens=0),
        )

    async def _no_stream(self) -> AsyncIterator[GenerationChunk]:
        raise ModelProviderError("streaming is not used by missions")
        yield  # pragma: no cover

    def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationChunk]:
        return self._no_stream()

    async def cancel(self, operation_id: str) -> CancellationResult:
        if self.inner is not None:
            return await self.inner.cancel(operation_id)
        return CancellationResult(
            operation_id=operation_id,
            provider_id=self.provider_id,
            status=CancellationStatus.ACCEPTED,
        )


@dataclass
class Bench:
    """One runtime over one fixture workspace, plus the providers it uses."""

    runtime: PhoenixRuntime
    tool: FilesystemTool
    root: Path
    providers: tuple[FaultInjectionProvider, ...]
    models_registered: tuple[str, ...]

    def load(self, script: MissionScript | None) -> None:
        for provider in self.providers:
            provider.script = script

    def write(self, name: str, text: str) -> None:
        (self.root / name).write_text(text, encoding="utf-8", newline="\n")

    def clear(self) -> None:
        for path in self.root.iterdir():
            path.unlink()


BenchFactory = Callable[[RuntimeLimits], AbstractAsyncContextManager[Bench]]


def tier_models() -> tuple[str, ...]:
    table = default_tier_table()
    ordered = [ref.model_id for refs in table.roles.values() for ref in refs]
    ordered += [ref.model_id for ref in table.default]
    return tuple(dict.fromkeys(ordered))


@contextmanager
def _workspace_tool() -> Iterator[tuple[FilesystemTool, Path]]:
    """A FilesystemTool over a temporary workspace with a migrated audit DB."""
    with tempfile.TemporaryDirectory(prefix="phoenix-metric-") as temp:
        # FilesystemTool refuses a root that is not its own real path
        # (ADR-094). On GitHub's Windows runner the temp dir is an 8.3 short
        # name (RUNNER~1), so every document step failed there; use the
        # canonical spelling.
        base = Path(os.path.realpath(temp))
        root = base / "workspace"
        root.mkdir()
        db = base / "audit.db"
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", f"sqlite:///{db}".replace("%", "%%"))
        command.upgrade(config, "head")
        engine = create_engine(f"sqlite:///{db}")
        try:
            tools = ToolRegistry(filesystem_tool_definitions())
            audit = AuditTrail(sessionmaker(bind=engine), tools)
            yield FilesystemTool(root, tools, PermissionPolicy(), audit), root
        finally:
            engine.dispose()


@asynccontextmanager
async def scripted_bench(limits: RuntimeLimits) -> AsyncIterator[Bench]:
    """CI: every model turn scripted; LEVI's model verifier always passes."""
    provider = FaultInjectionProvider()
    store = ModelCapabilityRegistry()
    models = tier_models()
    for model in models:
        store.register(capabilities_for(model))
    with _workspace_tool() as (tool, root):
        yield Bench(
            runtime=PhoenixRuntime(
                providers=(provider,),
                capability_registry=store,
                lifecycle=ModelLifecycleManager(),
                limits=limits,
                filesystem_tool=tool,
            ),
            tool=tool,
            root=root,
            providers=(provider,),
            models_registered=models,
        )


@asynccontextmanager
async def ollama_bench(
    limits: RuntimeLimits, base_url: str = baseline.OLLAMA_URL
) -> AsyncIterator[Bench]:
    """Live: Ollama default + think=False providers behind the fault injector."""
    from phoenix.models.ollama import OllamaProvider

    store = ModelCapabilityRegistry()
    async with (
        OllamaProvider(base_url) as default,
        OllamaProvider(base_url, think=False) as structured,
    ):
        available: list[str] = []
        for model in tier_models():
            try:
                store.register(await default.capabilities(model))
                available.append(model)
            except ModelProviderError:
                continue  # not installed; the tier table falls back to T0
        outer = FaultInjectionProvider(default)
        outer_structured = FaultInjectionProvider(structured)
        with _workspace_tool() as (tool, root):
            yield Bench(
                runtime=PhoenixRuntime(
                    providers=(outer,),
                    structured_providers=(outer_structured,),
                    capability_registry=store,
                    lifecycle=ModelLifecycleManager(),
                    limits=limits,
                    filesystem_tool=tool,
                ),
                tool=tool,
                root=root,
                providers=(outer, outer_structured),
                models_registered=tuple(available),
            )


def read_grant(
    tool: FilesystemTool, relative_path: str, grant_id: str
) -> PermissionGrant:
    """The session grant a user approves for one read_text of ``relative_path``."""
    intent = tool.describe(
        FilesystemCall.model_validate(
            {"operation": "read_text", "relative_path": relative_path}
        )
    )
    return grant_for(
        PermissionRequest(
            request_id=str(uuid4()),
            actor_id=DOCUMENT_ACTOR,
            tool_id=intent.tool_id,
            action=intent.action,
            resource=intent.resource,
            reason=DOCUMENT_TOOL_REASON,
            requested_at=datetime.now(UTC),
            session_id=SESSION_ID,
        ),
        grant_id,
    )


def grant_for(pending: PermissionRequest, grant_id: str) -> PermissionGrant:
    """Approve exactly the pending request for this session (PHX-102 re-run)."""
    return PermissionGrant.model_validate(
        {
            "grant_id": grant_id,
            "actor_id": pending.actor_id,
            "tool_id": pending.tool_id,
            "action": pending.action,
            "resource": pending.resource,
            "reason": pending.reason,
            "duration": "session",
            "session_id": SESSION_ID,
            "risk_level": "P1",
            "issued_at": datetime.now(UTC) - timedelta(seconds=1),
            "user_decision": "approved",
        }
    )


FORGED_GRANT_ID = "forged-grant"


def forge(result: FilesystemToolResult, forgery: str) -> FilesystemToolResult:
    """The harness's tampered tool result for a forged_tool_evidence case."""
    if forgery == "call_id":
        return result.model_copy(update={"call_id": str(uuid4())})
    if forgery == "foreign_request":
        decision = result.permission_decision.model_copy(
            update={"request_id": str(uuid4())}
        )
        return result.model_copy(update={"permission_decision": decision})
    if forgery == "foreign_grant":
        decision = result.permission_decision.model_copy(
            update={"matched_grant_id": FORGED_GRANT_ID}
        )
        return result.model_copy(update={"permission_decision": decision})
    raise ValueError(f"unknown forgery {forgery!r}")


@contextmanager
def forged_tool(tool: FilesystemTool, forgery: str | None) -> Iterator[None]:
    """Tamper with every result ``tool`` returns, for this case only.

    FilesystemTool is slotted and the runtime accepts only that exact type, so
    the harness wraps the class method for the case and restores it after;
    other instances pass through untouched.
    """
    if forgery is None:
        yield
        return
    original = FilesystemTool.execute

    def execute(
        self: FilesystemTool, *args: Any, **kwargs: Any
    ) -> FilesystemToolResult:
        result = original(self, *args, **kwargs)
        return forge(result, forgery) if self is tool else result

    FilesystemTool.execute = execute  # type: ignore[method-assign]
    try:
        yield
    finally:
        FilesystemTool.execute = original  # type: ignore[method-assign]


def _verdicts(result: MissionResult) -> list[str]:
    return [item.verdict.value for item in result.trace.verifications]


def _mission_fields(result: MissionResult) -> dict[str, Any]:
    failure = result.failure_code
    responder = result.responder_error
    return {
        "mission_kind": result.kind.value,
        "failure_code": None if failure is None else failure.value,
        "responder_error": None if responder is None else responder.value,
        "verdicts": _verdicts(result),
        "verified_steps": [item.step_id for item in result.trace.verifications],
        "states": [state.value for state in result.trace.states],
        "recoveries": len(result.trace.recoveries),
        "generations": len(result.trace.generations),
        "prompt_set_version": result.trace.prompt_set_version,
    }


async def run_verification(
    cases: tuple[md.VerificationCase, ...], bench: Bench
) -> list[dict[str, Any]]:
    """One forced-COMPLEX mission per case; closed codes only (ADR-029)."""
    rows: list[dict[str, Any]] = []
    for case in cases:
        bench.clear()
        bench.load(verification_script(case))
        grants: tuple[PermissionGrant, ...] = ()
        if case.document is not None and case.document_text is not None:
            bench.write(case.document, case.document_text)
            grants = (read_grant(bench.tool, case.document, "metric-read"),)
        assert bench.providers[0].script is not None
        bench.providers[0].script.reset()
        base = {
            "id": case.case_id,
            "fault": case.fault,
            "target": case.target,
            "detector": case.detector,
        }
        try:
            with forged_tool(bench.tool, case.forgery):
                result = await bench.runtime.run(
                    PhoenixRequest(text=case.request),
                    OrchestrationPath.COMPLEX,
                    grants=grants,
                    session_id=SESSION_ID,
                )
        except Exception as error:  # a defect: recorded by type, never message
            row = {
                **base,
                "outcome": "inconclusive",
                "error_type": type(error).__name__,
            }
        else:
            fields = _mission_fields(result)
            row = {
                **base,
                **fields,
                "outcome": md.verification_outcome(
                    case,
                    mission_kind=fields["mission_kind"],
                    failure_code=fields["failure_code"],
                    verdicts=fields["verdicts"],
                ),
            }
        rows.append(row)
        print(
            f"metric: verification {case.case_id} ({case.fault}) -> {row['outcome']}",
            flush=True,
        )
    bench.load(None)
    return rows


async def run_interventions(
    interventions: md.InterventionSet, bench: Bench, *, live: bool
) -> list[dict[str, Any]]:
    """Missions from no grants; each permission prompt is approved and re-run."""
    rows: list[dict[str, Any]] = []
    for case in interventions.cases:
        bench.clear()
        for name, text in interventions.files.items():
            bench.write(name, text)
        script = None if live else intervention_script(case)
        bench.load(script)
        grants: list[PermissionGrant] = []
        prompts_seen: list[str] = []
        result: MissionResult | None = None
        error_type: str | None = None
        for round_index in range(interventions.max_rounds):
            if script is not None:
                script.reset()
            try:
                result = await bench.runtime.run(
                    PhoenixRequest(text=case.request),
                    OrchestrationPath.COMPLEX,
                    grants=tuple(grants),
                    session_id=SESSION_ID,
                )
            except Exception as error:  # a defect: recorded by type only
                error_type = type(error).__name__
                break
            if result.kind != MissionKind.WAITING_PERMISSION:
                break
            assert result.pending_permission is not None
            prompts_seen.append(result.pending_permission.action)
            grants.append(grant_for(result.pending_permission, f"grant-{round_index}"))
        completed = result is not None and result.kind == MissionKind.COMPLETED
        row: dict[str, Any] = {
            "id": case.case_id,
            "min_interventions": case.min_interventions,
            "interventions": len(prompts_seen),
            "prompt_actions": prompts_seen,
            "completed": completed,
            "final_kind": None if result is None else result.kind.value,
            "failure_code": None
            if result is None or result.failure_code is None
            else result.failure_code.value,
        }
        if error_type is not None:
            row["error_type"] = error_type
        rows.append(row)
        print(
            f"metric: interventions {case.case_id} -> {row['interventions']} "
            f"prompt(s), {row['final_kind']}",
            flush=True,
        )
    bench.load(None)
    return rows


# ---- capability gate and the metric table


def ticket_status(ticket: str, path: Path = FEATURE_TICKETS) -> str | None:
    """The FEATURE_TICKETS.md status of ``ticket`` (DONE, TODO, ...), or None."""
    pattern = re.compile(rf"^- {re.escape(ticket)} .*? — ([A-Z_]+)\b", re.MULTILINE)
    match = pattern.search(path.read_text(encoding="utf-8"))
    return None if match is None else match.group(1)


def capability_reason(metric: str, path: Path = FEATURE_TICKETS) -> str | None:
    """None when the metric can run live; otherwise why it is NOT_RUNNABLE."""
    ticket, name = CAPABILITIES[metric]
    status = ticket_status(ticket, path)
    dataset = md.SETS[name].relative_to(ROOT).as_posix()
    if status != "DONE":
        return (
            f"{ticket} is {status or 'missing'}: the capability is not built. "
            f"{dataset} and its scorer are validated by --dry-run (calibration "
            "vectors) until it lands."
        )
    if metric not in ADAPTERS:
        return (
            f"{ticket} is DONE but no PHX-117 adapter is registered for {metric} "
            "in scripts/metric_benchmark.py (ADAPTERS)."
        )
    return None


def recovery_row(config2: dict[str, Any] | None) -> dict[str, Any]:
    """recovery_success from configuration (2) traces (PHX-109), consistently."""
    if config2 is None:
        return {
            "status": "NOT_RUNNABLE",
            "reason": "Measured from configuration (2) traces: pass "
            "--config2-results <fyp-multi-agent-results.json> (PHX-109 "
            "MissionTrace.recoveries).",
        }
    recoveries = (config2.get("summary") or {}).get("recoveries")
    if recoveries is None:
        return {
            "status": "NOT_RUNNABLE",
            "reason": "These configuration (2) traces record no MAHORAGA "
            "recoveries: MissionTrace.recoveries needs PHX-109.",
        }
    return {
        "status": "MEASURED",
        "source": "configuration (2) MissionTrace.recoveries: recovered actions "
        "over actions taken (scripts/fyp_multi_agent.py summary.recoveries)",
        "value": recoveries.get("recovery_success"),
        "detail": {
            "actions": recoveries.get("actions"),
            "recovered": recoveries.get("recovered"),
            "missions_with_recoveries": recoveries.get("missions_with_recoveries"),
            "note": None
            if recoveries.get("actions")
            else "No recovery action was taken, so the rate is undefined.",
        },
    }


def metric_rows(
    *,
    verification: dict[str, Any] | None,
    interventions: dict[str, Any] | None,
    config2: dict[str, Any] | None,
    scripted: bool,
    tickets: Path = FEATURE_TICKETS,
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if verification is None:
        rows["verification_catch_rate"] = {
            "status": "NOT_RUNNABLE",
            "reason": "Not run: run without --dry-run (and --metric verification "
            "or all) to measure LEVI on the fault-injected set.",
        }
    else:
        rows["verification_catch_rate"] = {
            "status": "MEASURED",
            "source": "fault-injected verification-v1 missions: caught / (caught + "
            "escaped); false rejections over controls"
            + (" (scripted LEVI floor, not a FYP number)" if scripted else ""),
            "value": verification["catch_rate"],
            "detail": {
                "false_rejection_rate": verification["false_rejection_rate"],
                "outcomes": verification["outcomes"],
            },
        }
    if interventions is None:
        rows["user_intervention_count"] = {
            "status": "NOT_RUNNABLE",
            "reason": "Not run: run without --dry-run (and --metric interventions "
            "or all) to count permission prompts on the interventions set.",
        }
    else:
        rows["user_intervention_count"] = {
            "status": "MEASURED",
            "source": "permission prompts (WAITING_PERMISSION) per mission on "
            "interventions-v1; PHOENIX v1 has no clarification turn"
            + (" (scripted producers)" if scripted else ""),
            "value": interventions["interventions"],
            "detail": {
                "interventions_per_mission": interventions["interventions_per_mission"],
                "excess_interventions": interventions["excess_interventions"],
                "completion_rate": interventions["completion_rate"],
            },
        }
    rows["recovery_success"] = recovery_row(config2)
    for metric in CAPABILITIES:
        reason = capability_reason(metric, tickets)
        rows[metric] = (
            {"status": "NOT_RUNNABLE", "reason": reason}
            if reason is not None
            else {"status": "NOT_RUNNABLE", "reason": "Adapter run not requested."}
        )
    return [{"id": metric, **rows[metric]} for metric in METRIC_ORDER]


# ---- dry run, reports and the entry point


def validate_all() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Load every set; returns per-set facts and calibration failures."""
    sets: dict[str, dict[str, Any]] = {}
    verification = md.load_verification_set()
    sets["verification"] = {
        "header": verification.header,
        "cases": len(verification.cases),
        "composition": md.verification_composition(verification.cases),
    }
    interventions = md.load_intervention_set()
    sets["interventions"] = {
        "header": interventions.header,
        "cases": len(interventions.cases),
        "composition": {"files": len(interventions.files)},
    }
    memory = md.load_memory_set()
    sets["memory"] = {
        "header": memory.header,
        "cases": len(memory.cases),
        "composition": {
            "documents": len(memory.documents),
            "calibration": len(memory.calibration),
        },
    }
    research = md.load_research_set()
    sets["research"] = {
        "header": research.header,
        "cases": len(research.cases),
        "composition": {
            "sources": len(research.sources),
            "calibration": len(research.calibration),
        },
    }
    controls = md.load_control_set()
    sets["computer_control"] = {
        "header": controls.header,
        "cases": len(controls.cases),
        "composition": {
            "fixtures": len(controls.fixtures),
            "calibration": len(controls.calibration),
        },
    }
    failures: list[str] = []
    for name in ("memory", "research", "computer_control"):
        failures += md.calibration_failures(name)
    return sets, failures


def suites_json(sets: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "id": item["header"].suite_id,
            "version": item["header"].version,
            "path": md.SETS[name].relative_to(ROOT).as_posix(),
            "sha256": md.sha256_file(md.SETS[name]),
            "cases": item["cases"],
            "composition": item["composition"],
        }
        for name, item in sets.items()
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def write_summary(results: dict[str, Any], path: Path) -> None:
    lines = [
        "# PHOENIX metric datasets (PHX-117)",
        "",
        f"{results['started_utc']} → {results['finished_utc']} on "
        f"{results['host']['platform']}; mode **{results['mode']}**; prompts "
        f"{results['prompt_set_version']}.",
    ]
    if results["mode"] == "scripted":
        lines += ["", f"> {SCRIPTED_NOTE}"]
    lines += ["", "## Datasets", "", "| Set | Version | Cases | sha256 |"]
    lines.append("| --- | --- | --- | --- |")
    for suite in results["suites"].values():
        lines.append(
            f"| {suite['id']} | {suite['version']} | {suite['cases']} | "
            f"`{suite['sha256'][:16]}…` |"
        )
    verification = results.get("verification")
    if verification is not None:
        s = verification["summary"]
        o = s["outcomes"]
        lines += [
            "",
            "## Verification catch rate",
            "",
            f"- **Catch rate:** {_pct(s['catch_rate'])} ({o['caught']} caught, "
            f"{o['escaped']} escaped, {o['inconclusive']} inconclusive of "
            f"{s['fault_cases']} faults)",
            f"- **False rejections:** {_pct(s['false_rejection_rate'])} "
            f"({o['false_rejection']} of {s['control_cases']} controls)",
            "",
            "| Fault | Caught | Escaped | Inconclusive | Catch rate |",
            "| --- | --- | --- | --- | --- |",
        ]
        for name, item in s["by_fault"].items():
            lines.append(
                f"| {name} | {item['caught']} | {item['escaped']} | "
                f"{item['inconclusive']} | {_pct(item['catch_rate'])} |"
            )
        lines += ["", "| Detector | Caught | Escaped | Catch rate |"]
        lines.append("| --- | --- | --- | --- |")
        for name, item in s["by_detector"].items():
            lines.append(
                f"| {name} | {item['caught']} | {item['escaped']} | "
                f"{_pct(item['catch_rate'])} |"
            )
    interventions = results.get("interventions")
    if interventions is not None:
        s = interventions["summary"]
        lines += [
            "",
            "## User intervention count",
            "",
            f"- **Permission prompts:** {s['interventions']} over {s['missions']} "
            f"missions ({s['interventions_per_mission']:.2f} per mission; minimum "
            f"{s['minimum_interventions']}, excess {s['excess_interventions']})",
            f"- **Completed:** {s['completed']}/{s['missions']}; clarifications: "
            "none (no clarification turn in PHOENIX v1)",
        ]
    lines += [
        "",
        "## docs/24 metrics owned by PHX-117",
        "",
        "| Metric | Status | Value / reason |",
    ]
    lines.append("| --- | --- | --- |")
    for row in results["metrics"]:
        detail = (
            f"{row['value']}" + f" — {row['source']}"
            if row["status"] == "MEASURED"
            else row["reason"]
        )
        status = "MEASURED" if row["status"] == "MEASURED" else "NOT RUNNABLE"
        lines.append(f"| {row['id']} | {status} | {detail} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _measure(
    factory: BenchFactory,
    limits: RuntimeLimits,
    which: str,
    *,
    live: bool,
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None, tuple[str, ...]]:
    verification_rows = intervention_rows = None
    async with factory(limits) as bench:
        if which in ("verification", "all"):
            verification_rows = await run_verification(
                md.load_verification_set().cases, bench
            )
        if which in ("interventions", "all"):
            intervention_rows = await run_interventions(
                md.load_intervention_set(), bench, live=live
            )
        return verification_rows, intervention_rows, bench.models_registered


def main(
    argv: list[str] | None = None,
    bench_factory: BenchFactory | None = None,
    compute_probe: baseline.ComputeProbe | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="PHOENIX metric datasets (PHX-117)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--metric", choices=("verification", "interventions", "all"), default="all"
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--scripted", action="store_true", help="scripted models (CI, no Ollama)"
    )
    parser.add_argument("--config2-results", type=Path, default=None)
    parser.add_argument(
        "--allow-cpu", action="store_true", help="record a CPU-only live run"
    )
    args = parser.parse_args(argv)

    sets, failures = validate_all()
    if args.dry_run:
        for name, item in sets.items():
            header = item["header"]
            print(
                f"metric: {header.suite_id} {header.version}: {item['cases']} cases "
                f"{item['composition']} sha256 {md.sha256_file(md.SETS[name])[:16]}"
            )
        for metric in CAPABILITIES:
            reason = capability_reason(metric)
            print(f"metric: {metric}: {'runnable' if reason is None else reason}")
        for failure in failures:
            print(f"metric: CALIBRATION FAILED {failure}", file=sys.stderr)
        return 1 if failures else 0
    if failures:
        for failure in failures:
            print(f"metric: CALIBRATION FAILED {failure}", file=sys.stderr)
        return 1

    config2: dict[str, Any] | None = None
    if args.config2_results is not None:
        config2 = json.loads(args.config2_results.read_text(encoding="utf-8"))
        if (
            not isinstance(config2, dict)
            or config2.get("tool") != "phoenix-fyp-multi-agent"
        ):
            print(
                "metric: --config2-results is not a configuration (2) file",
                file=sys.stderr,
            )
            return 2

    live = not args.scripted
    limits = RuntimeLimits()
    started = datetime.now(UTC).isoformat()
    compute: dict[str, Any] = {"device": "not_probed"}
    probe = compute_probe or (
        baseline.ollama_compute if live and bench_factory is None else None
    )
    verifier_model = default_tier_table().candidates("verifier")[0].model_id
    if probe is not None:
        compute = probe(verifier_model) or {"device": "cpu", "reason": "not loaded"}
        if compute["device"] == "cpu" and not args.allow_cpu:
            print(
                f"metric: refused - {verifier_model} would run on CPU only "
                f"({compute}); enable the GPU or pass --allow-cpu",
                file=sys.stderr,
            )
            return 2
    factory = bench_factory or (ollama_bench if live else scripted_bench)
    verification_rows, intervention_rows, models = asyncio.run(
        _measure(factory, limits, args.metric, live=live)
    )
    verification: dict[str, Any] | None = (
        None
        if verification_rows is None
        else {
            "summary": md.summarize_verification(verification_rows),
            "cases": verification_rows,
        }
    )
    interventions: dict[str, Any] | None = (
        None
        if intervention_rows is None
        else {
            "summary": md.summarize_interventions(intervention_rows),
            "cases": intervention_rows,
        }
    )
    results: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL,
        "mode": "live" if live else "scripted",
        "note": None if live else SCRIPTED_NOTE,
        "started_utc": started,
        "finished_utc": datetime.now(UTC).isoformat(),
        "suites": suites_json(sets),
        "prompt_set_version": prompts.PROMPT_SET_VERSION,
        "models_registered": list(models),
        "verifier_first_choice": verifier_model,
        "compute": compute,
        "compute_policy": {"allow_cpu": args.allow_cpu, "probe": probe is not None},
        "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "config2_results": None
        if args.config2_results is None
        else str(args.config2_results),
        "verification": verification,
        "interventions": interventions,
        "metrics": metric_rows(
            verification=None if verification is None else verification["summary"],
            interventions=None if interventions is None else interventions["summary"],
            config2=config2,
            scripted=not live,
        ),
    }
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    (out / RESULTS_NAME).write_text(json.dumps(results, indent=2), encoding="utf-8")
    write_summary(results, out / SUMMARY_NAME)
    print(f"metric: done ({results['mode']}); results in {out}")
    # A verification run in which LEVI never decided measured nothing.
    if verification is not None:
        outcomes = verification["summary"]["outcomes"]
        if outcomes["inconclusive"] == verification["summary"]["cases"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
