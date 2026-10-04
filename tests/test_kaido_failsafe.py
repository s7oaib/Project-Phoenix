"""PHX-028 degraded-only, least-scope policy and strict side-effect isolation."""

import ast
import inspect
import subprocess
import sys
from itertools import permutations
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from phoenix.agents import failsafe
from phoenix.agents.failsafe import (
    FailSafeCapability,
    FailSafeDecision,
    FailSafeRequest,
    FailSafeTrigger,
    KaidoFailSafe,
    KaidoFailSafeError,
)
from phoenix.agents.handoff import HandoffEnvelope
from phoenix.agents.recovery import RecoveryAction, RecoveryDraft, RecoveryResult
from phoenix.agents.registry import AgentDefinition, AgentRegistry
from phoenix.agents.task_graph import TaskState, TaskStateMachine
from phoenix.agents.verifier import (
    VerificationCheck,
    VerificationResult,
    VerificationVerdict,
    verification_requirement_satisfied,
)

BAD_TEXT = (None, True, 1, 1.5, b"text", object(), "", " \t\n")
BAD_TRIGGER = (
    None,
    True,
    1,
    b"resource_pressure",
    "",
    "normal_mode",
    "healthy",
    "success",
    "verification_passed",
    "permission_approved",
    "no_route_found",
    "timeout",
)
BAD_CAPABILITIES: tuple[object, ...] = (
    None,
    (),
    [],
    "diagnostics",
    {"diagnostics"},
    {"x": "diagnostics"},
    ("diagnostics", "diagnostics"),
    (True,),
    (1,),
    (None,),
    (b"diagnostics",),
    ("",),
    (" diagnostics ",),
    ("deep_research",),
    ("unrestricted_tool_execution",),
    ("unrestricted_computer_control",),
    ("normal_premium_model_mode",),
)
CAPABILITY_ORDERS = tuple(
    sequence
    for size in range(1, 5)
    for sequence in permutations(FailSafeCapability, size)
)


def exhausted(**changes: object) -> RecoveryResult:
    return RecoveryResult.model_validate(
        {
            "recovery_id": "recovery",
            "task_id": "task",
            "step_id": "step",
            "subject_agent_id": "build_engineer",
            "trigger": "execution_failure",
            "failure_summary": "  Original failure\n",
            "max_attempts": 2,
            "max_replan_steps": 1,
            "attempts_used_before": 2,
            "status": "exhausted",
            **changes,
        }
    )


def request(**changes: object) -> FailSafeRequest:
    return FailSafeRequest.model_validate(
        {
            "failsafe_id": "failsafe-operation",
            "trigger": "resource_pressure",
            "reason": "  Caller observation\n",
            "requested_capabilities": ("diagnostics",),
            **changes,
        }
    )


def decision(**changes: object) -> FailSafeDecision:
    return FailSafeDecision.model_validate(
        {
            "failsafe_id": "failsafe-operation",
            "trigger": "resource_pressure",
            "reason": "  Caller observation\n",
            "enabled_capabilities": ("diagnostics",),
            **changes,
        }
    )


def registry(identity: str = "failsafe", alias: str | None = "KAIDO") -> AgentRegistry:
    return AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id=identity,
                alias=alias,
                professional_role="Untrusted role metadata",
            ),
        )
    )


def test_exact_vocabulary_and_neutral_public_fields() -> None:
    assert {item.value for item in FailSafeTrigger} == {
        "primary_model_provider_unavailable",
        "resource_pressure",
        "deep_provider_unavailable",
        "critical_subsystem_degradation",
        "recovery_exhausted",
    }
    assert {item.value for item in FailSafeCapability} == {
        "basic_conversation",
        "diagnostics",
        "recovery_guidance",
        "safe_limited_tool_routing",
    }
    assert set(FailSafeRequest.model_fields) == {
        "failsafe_id",
        "trigger",
        "reason",
        "requested_capabilities",
        "recovery_result",
    }
    assert set(FailSafeDecision.model_fields) == {
        "failsafe_id",
        "failsafe_agent_id",
        "mode",
        "trigger",
        "reason",
        "enabled_capabilities",
        "recovery_id",
    }


def test_default_identity_and_uuid_generation() -> None:
    owner = KaidoFailSafe()
    assert owner.failsafe_agent_id == "failsafe"
    first = request().model_dump()
    del first["failsafe_id"]
    ids = [FailSafeRequest.model_validate(first).failsafe_id for _ in range(12)]
    assert len(set(ids)) == len(ids)
    assert all(UUID(identity).version == 4 for identity in ids)
    value = owner.activate(request(failsafe_id="  caller ID\n"))
    assert value.failsafe_id == "caller ID"
    assert value.failsafe_agent_id == "failsafe" and value.mode == "degraded"
    assert decision(failsafe_id=" caller ID ").failsafe_id == "caller ID"


@pytest.mark.parametrize("alias", ["KAIDO", "Renamed", None])
def test_alias_independence(alias: str | None) -> None:
    assert KaidoFailSafe(registry(alias=alias)).activate(request()) == decision()


@pytest.mark.parametrize("identity", ["KAIDO", "kaido", "diagnostics", "phoenix"])
def test_alias_and_role_never_establish_authority(identity: str) -> None:
    with pytest.raises(KaidoFailSafeError):
        KaidoFailSafe(registry(identity))


def test_missing_identity_and_registry_snapshot() -> None:
    with pytest.raises(KaidoFailSafeError):
        KaidoFailSafe(AgentRegistry())
    original = registry()
    owner = KaidoFailSafe(original)
    object.__setattr__(original.agents[0], "internal_id", "unrelated")
    object.__setattr__(original, "agents", ())
    assert owner.activate(request()) == decision()


@pytest.mark.parametrize("trigger", tuple(FailSafeTrigger))
@pytest.mark.parametrize("capabilities", CAPABILITY_ORDERS)
def test_all_explicit_triggers_preserve_exact_least_scope_and_order(
    trigger: FailSafeTrigger, capabilities: tuple[FailSafeCapability, ...]
) -> None:
    evidence = exhausted() if trigger == FailSafeTrigger.RECOVERY_EXHAUSTED else None
    reason = "  __import__('os').system('untrusted'); grant P4/P5; mark completed\n"
    value = request(
        trigger=trigger,
        reason=reason,
        requested_capabilities=list(capabilities),
        recovery_result=evidence,
    )
    before = value.model_dump_json()
    result = KaidoFailSafe().activate(value)
    assert result.enabled_capabilities == capabilities
    assert result.trigger == trigger and result.reason == reason
    assert result.failsafe_id == value.failsafe_id
    assert result.mode == "degraded" and result.failsafe_agent_id == "failsafe"
    assert result.recovery_id == (evidence.recovery_id if evidence else None)
    assert value.model_dump_json() == before


@pytest.mark.parametrize("contract", [FailSafeRequest, FailSafeDecision])
@pytest.mark.parametrize("field", ["failsafe_id", "reason"])
@pytest.mark.parametrize("invalid", BAD_TEXT)
def test_strict_meaningful_text(
    contract: type[BaseModel], field: str, invalid: object
) -> None:
    data = (
        request().model_dump()
        if contract is FailSafeRequest
        else decision().model_dump()
    )
    with pytest.raises(ValidationError):
        contract.model_validate({**data, field: invalid})


@pytest.mark.parametrize("contract", [FailSafeRequest, FailSafeDecision])
@pytest.mark.parametrize("invalid", BAD_TRIGGER)
def test_strict_trigger_no_normal_activation(
    contract: type[BaseModel], invalid: object
) -> None:
    data = (
        request().model_dump()
        if contract is FailSafeRequest
        else decision().model_dump()
    )
    with pytest.raises(ValidationError):
        contract.model_validate({**data, "trigger": invalid})


@pytest.mark.parametrize(
    "contract,field",
    [
        (FailSafeRequest, "requested_capabilities"),
        (FailSafeDecision, "enabled_capabilities"),
    ],
)
@pytest.mark.parametrize("invalid", BAD_CAPABILITIES)
def test_strict_nonempty_ordered_unique_capabilities(
    contract: type[BaseModel], field: str, invalid: object
) -> None:
    data = (
        request().model_dump()
        if contract is FailSafeRequest
        else decision().model_dump()
    )
    with pytest.raises(ValidationError):
        contract.model_validate({**data, field: invalid})


@pytest.mark.parametrize(
    "mode", [None, "normal", "healthy", "high_quality", "premium", b"degraded", True]
)
def test_decision_is_degraded_only(mode: object) -> None:
    with pytest.raises(ValidationError):
        decision(mode=mode)


@pytest.mark.parametrize(
    "identity", [None, "KAIDO", "phoenix", "Failsafe", b"failsafe", True]
)
def test_decision_literal_identity(identity: object) -> None:
    with pytest.raises(ValidationError):
        decision(failsafe_agent_id=identity)


@pytest.mark.parametrize("contract", [FailSafeRequest, FailSafeDecision])
@pytest.mark.parametrize(
    "extra",
    [
        "provider",
        "tool",
        "permission_grant",
        "callback",
        "task_state",
        "metadata",
        "status",
    ],
)
def test_extra_fields_forbidden(contract: type[BaseModel], extra: str) -> None:
    data = (
        request().model_dump()
        if contract is FailSafeRequest
        else decision().model_dump()
    )
    with pytest.raises(ValidationError):
        contract.model_validate({**data, extra: "untrusted"})


@pytest.mark.parametrize("value", [request(), decision()])
def test_frozen_and_detached_containers(
    value: FailSafeRequest | FailSafeDecision,
) -> None:
    with pytest.raises(ValidationError):
        value.reason = "changed"
    items = ["recovery_guidance", "diagnostics"]
    source = request(requested_capabilities=items)
    outcome = KaidoFailSafe().activate(source)
    items.append("safe_limited_tool_routing")
    assert (
        source.requested_capabilities
        == outcome.enabled_capabilities
        == (
            FailSafeCapability.RECOVERY_GUIDANCE,
            FailSafeCapability.DIAGNOSTICS,
        )
    )


@pytest.mark.parametrize("used", [2, 3, 100])
def test_exhaustion_budget_and_recovery_id_remain_failure(used: int) -> None:
    evidence = exhausted(recovery_id="  exact recovery ID\n", attempts_used_before=used)
    before = evidence.model_dump_json()
    source = request(trigger="recovery_exhausted", recovery_result=evidence)
    assert source.recovery_result is not evidence
    outcome = KaidoFailSafe().activate(source)
    assert outcome.recovery_id == "exact recovery ID"
    assert evidence.model_dump_json() == before
    assert evidence.status == "exhausted" and evidence.proposal is None
    assert evidence.attempt_number is None
    assert source.recovery_result is not None
    object.__setattr__(evidence, "recovery_id", "changed externally")
    assert KaidoFailSafe().activate(source).recovery_id == "exact recovery ID"


@pytest.mark.parametrize("evidence", [None, {}, "exhausted", True, object()])
def test_exhaustion_requires_exact_evidence(evidence: object) -> None:
    with pytest.raises(ValidationError):
        request(trigger="recovery_exhausted", recovery_result=evidence)


def test_proposed_is_not_exhausted_and_nested_proposal_revalidated() -> None:
    proposal = RecoveryDraft(
        recovery_id="recovery", action=RecoveryAction.RETRY_SAME, rationale="Original"
    )
    evidence = exhausted(
        status="proposed", attempts_used_before=0, attempt_number=1, proposal=proposal
    )
    with pytest.raises(ValidationError):
        request(trigger="recovery_exhausted", recovery_result=evidence)
    object.__setattr__(proposal, "action", "grant_permission")
    forged = evidence.model_copy(update={"proposal": proposal})
    with pytest.raises(ValidationError):
        request(trigger="recovery_exhausted", recovery_result=forged)


@pytest.mark.parametrize("trigger", tuple(FailSafeTrigger)[:-1])
def test_other_triggers_reject_irrelevant_recovery(trigger: FailSafeTrigger) -> None:
    with pytest.raises(ValidationError):
        request(trigger=trigger, recovery_result=exhausted())
    with pytest.raises(ValidationError):
        decision(trigger=trigger, recovery_id="recovery")


@pytest.mark.parametrize("invalid", BAD_TEXT)
def test_exhaustion_decision_requires_strict_reference(invalid: object) -> None:
    with pytest.raises(ValidationError):
        decision(trigger="recovery_exhausted", recovery_id=invalid)


RECOVERY_FORGERIES: tuple[tuple[str, object], ...] = (
    ("recovery_id", ""),
    ("task_id", None),
    ("step_id", True),
    ("subject_agent_id", b"build_engineer"),
    ("trigger", "healthy"),
    ("failure_summary", " \n"),
    ("recovery_agent_id", "MAHORAGA"),
    ("status", "proposed"),
    ("status", b"exhausted"),
    ("attempt_number", 1),
    ("attempts_used_before", 1),
    ("attempts_used_before", True),
    ("attempts_used_before", "2"),
    ("max_attempts", 0),
    ("max_attempts", True),
    ("max_replan_steps", 0),
    ("max_replan_steps", "1"),
    ("proposal", {}),
    ("extra", "injected"),
)


@pytest.mark.parametrize("field,invalid", RECOVERY_FORGERIES)
@pytest.mark.parametrize("mutation", ["copy", "construct", "attribute"])
def test_exact_stored_recovery_values_revalidated_before_activation(
    field: str, invalid: object, mutation: str
) -> None:
    evidence = exhausted()
    if mutation == "copy":
        evidence = evidence.model_copy(update={field: invalid})
    elif mutation == "construct":
        evidence = RecoveryResult.model_construct(**{**vars(evidence), field: invalid})
        # model_construct intentionally discards unknown keys with extra=forbid.
        if field == "extra":
            object.__setattr__(evidence, field, invalid)
    else:
        object.__setattr__(evidence, field, invalid)
    with pytest.raises(ValidationError):
        request(trigger="recovery_exhausted", recovery_result=evidence)
    source = request().model_copy(
        update={"trigger": "recovery_exhausted", "recovery_result": evidence}
    )
    with pytest.raises(KaidoFailSafeError):
        KaidoFailSafe().activate(source)


@pytest.mark.parametrize(
    "field,invalid",
    [
        ("failsafe_id", None),
        ("trigger", "normal_mode"),
        ("reason", " \n"),
        ("requested_capabilities", ()),
        ("requested_capabilities", ("diagnostics", "diagnostics")),
        ("requested_capabilities", (b"diagnostics",)),
        ("extra", "injected"),
        ("recovery_result", exhausted()),
    ],
)
def test_runtime_revalidates_copied_request(field: str, invalid: object) -> None:
    with pytest.raises(KaidoFailSafeError):
        KaidoFailSafe().activate(request().model_copy(update={field: invalid}))


@pytest.mark.parametrize(
    "field,invalid",
    [
        ("failsafe_id", None),
        ("failsafe_agent_id", "KAIDO"),
        ("mode", "normal"),
        ("trigger", "healthy"),
        ("reason", " \n"),
        ("enabled_capabilities", ()),
        ("enabled_capabilities", ("diagnostics", "diagnostics")),
        ("recovery_id", "unrelated"),
        ("extra", "injected"),
    ],
)
@pytest.mark.parametrize("mutation", ["copy", "construct", "attribute"])
def test_decision_instances_revalidate_stored_fields(
    field: str, invalid: object, mutation: str
) -> None:
    value = decision()
    if mutation == "copy":
        value = value.model_copy(update={field: invalid})
    elif mutation == "construct":
        value = FailSafeDecision.model_construct(**{**vars(value), field: invalid})
        if field == "extra":
            object.__setattr__(value, field, invalid)
    else:
        object.__setattr__(value, field, invalid)
    with pytest.raises(ValidationError):
        FailSafeDecision.model_validate(value)


def test_valid_public_instances_revalidate_and_serialize_without_execution() -> None:
    source = request(trigger="recovery_exhausted", recovery_result=exhausted())
    detached = FailSafeRequest.model_validate(source)
    assert detached == source and detached is not source
    assert detached.recovery_result is not source.recovery_result
    outcome = KaidoFailSafe().activate(detached)
    assert FailSafeDecision.model_validate_json(outcome.model_dump_json()) == outcome
    ordinary = request()
    assert FailSafeRequest.model_validate_json(ordinary.model_dump_json()) == ordinary


def test_exact_request_decision_recovery_and_registry_subclasses_rejected() -> None:
    class ExtendedRequest(FailSafeRequest):
        extra: str = "injected"

    class ExtendedDecision(FailSafeDecision):
        extra: str = "injected"

    class ExtendedRecovery(RecoveryResult):
        extra: str = "injected"

    class ExtendedRegistry(AgentRegistry):
        extra: str = "injected"

    class ExtendedDefinition(AgentDefinition):
        extra: str = "injected"

    with pytest.raises(KaidoFailSafeError):
        KaidoFailSafe().activate(ExtendedRequest.model_validate(request().model_dump()))
    with pytest.raises(ValidationError):
        FailSafeDecision.model_validate(
            ExtendedDecision.model_validate(decision().model_dump())
        )
    with pytest.raises(ValidationError):
        request(
            trigger="recovery_exhausted",
            recovery_result=ExtendedRecovery.model_validate(exhausted().model_dump()),
        )
    with pytest.raises(KaidoFailSafeError):
        KaidoFailSafe(ExtendedRegistry(agents=registry().agents))
    with pytest.raises(KaidoFailSafeError):
        KaidoFailSafe(
            registry().model_copy(
                update={
                    "agents": (
                        ExtendedDefinition(
                            internal_id="failsafe", professional_role="metadata"
                        ),
                    )
                }
            )
        )
    with pytest.raises(KaidoFailSafeError):
        KaidoFailSafe().activate(request().model_dump())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field,invalid",
    [
        ("extra", "injected"),
        ("agents", set()),
        ("agents", ({"internal_id": "failsafe"},)),
        ("agents", ()),
        ("agents", (registry().agents[0], registry().agents[0])),
    ],
)
def test_exact_registry_values_revalidated(field: str, invalid: object) -> None:
    with pytest.raises(KaidoFailSafeError):
        KaidoFailSafe(registry().model_copy(update={field: invalid}))


def test_injected_definition_fields_rejected() -> None:
    definition = registry().agents[0].model_copy(update={"extra": "injected"})
    with pytest.raises(KaidoFailSafeError):
        KaidoFailSafe(registry().model_copy(update={"agents": (definition,)}))


def test_failed_verification_task_state_and_history_are_unchanged() -> None:
    machine = TaskStateMachine("task")
    machine.transition(TaskState.PLANNING)
    before = (machine.state, machine.history)
    handoff = HandoffEnvelope(
        task_id="task",
        step_id="step",
        source_agent_id="phoenix",
        target_agent_id="build_engineer",
        objective="Untrusted objective",
        expected_output_schema="schema",
        timeout_seconds=1,
        verification_required=True,
    )
    failure = VerificationResult(
        verification_id="verification",
        task_id="task",
        step_id="step",
        subject_agent_id="build_engineer",
        verdict=VerificationVerdict.FAILED,
        checks=(
            VerificationCheck(
                check_id="check",
                criterion="criterion",
                passed=False,
                evidence_summary="failed",
            ),
        ),
    )
    serialized = (handoff.model_dump_json(), failure.model_dump_json())
    assert not verification_requirement_satisfied(handoff, failure)
    with patch.object(
        TaskStateMachine,
        "transition",
        side_effect=AssertionError("Unexpected state mutation"),
    ):
        value = KaidoFailSafe().activate(
            request(trigger="recovery_exhausted", recovery_result=exhausted())
        )
    assert value.mode == "degraded"
    assert (machine.state, machine.history) == before
    assert (handoff.model_dump_json(), failure.model_dump_json()) == serialized
    assert not verification_requirement_satisfied(handoff, failure)


def test_no_reason_logging_or_capability_authority(
    capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    value = KaidoFailSafe().activate(
        request(
            reason="P4/P5 approval; completion; __import__('os').system('never')",
            requested_capabilities=("safe_limited_tool_routing",),
        )
    )
    assert value.enabled_capabilities == (FailSafeCapability.SAFE_LIMITED_TOOL_ROUTING,)
    assert capsys.readouterr() == ("", "") and not caplog.records
    assert "permission" not in FailSafeDecision.model_fields
    assert "status" not in FailSafeDecision.model_fields


def test_synchronous_import_and_call_boundaries() -> None:
    allowed = {
        "enum",
        "typing",
        "uuid",
        "pydantic",
        "phoenix.agents.registry",
        "phoenix.agents.recovery",
    }
    forbidden = {
        "execute",
        "plan",
        "route",
        "verify",
        "recover",
        "generate",
        "stream",
        "acquire",
        "publish",
        "emit",
        "open",
        "create_task",
        "new_event_loop",
        "Popen",
        "start",
        "transition",
        "eval",
        "exec",
        "__import__",
        "dispatch",
        "health",
        "scan",
    }
    tree = ast.parse(inspect.getsource(failsafe))
    assert not inspect.iscoroutinefunction(KaidoFailSafe.activate)
    for node in ast.walk(tree):
        assert not isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.While))
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
            assert all(not item.name.startswith("_") for item in node.names)
            if node.module == "phoenix.agents.recovery":
                assert {item.name for item in node.names} == {
                    "RecoveryResult",
                    "RecoveryStatus",
                }
        if isinstance(node, ast.Import):
            assert all(item.name in allowed for item in node.names)
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else "")
            )
            assert name not in forbidden


def test_fresh_interpreter_import_construction_and_activation_isolation(
    tmp_path: Path,
) -> None:
    script = """
import asyncio, _thread, importlib.abc, os, socket, sqlite3, subprocess, sys, threading
from pathlib import Path
sys.dont_write_bytecode = True
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected fail-safe runtime activity")
def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()
    if event.startswith(("socket.", "sqlite3.", "subprocess.")):
        forbidden()
    if event in ("os.mkdir", "os.remove", "os.rename", "os.system"):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("phoenix.models", "phoenix.core", "phoenix.api", "phoenix.persistence",
            "phoenix.tools", "phoenix.permissions", "phoenix.memory", "phoenix.voice",
            "phoenix.recovery", "phoenix.agents.orchestrator", "phoenix.agents.planner",
            "phoenix.agents.router", "httpx", "psutil", "alembic")
        if any(fullname == root or fullname.startswith(root + ".") for root in roots):
            forbidden()
sys.meta_path.insert(0, BlockImports())
sys.addaudithook(audit)
os.mkdir = forbidden
socket.socket = forbidden
socket.create_connection = forbidden
sqlite3.connect = forbidden
subprocess.Popen = forbidden
threading.Thread.start = forbidden
_thread.start_new_thread = forbidden
asyncio.create_task = forbidden
asyncio.BaseEventLoop.create_task = forbidden
asyncio.new_event_loop = forbidden
from phoenix.agents.task_graph import TaskStateMachine
from phoenix.agents.verifier import LeviVerifier
from phoenix.agents.recovery import MahoragaRecovery, RecoveryResult
TaskStateMachine.transition = forbidden
LeviVerifier.__init__ = forbidden
LeviVerifier.verify = forbidden
MahoragaRecovery.__init__ = forbidden
MahoragaRecovery.recover = forbidden
from phoenix.agents.failsafe import (
    FailSafeRequest, FailSafeDecision, FailSafeTrigger, FailSafeCapability,
    KaidoFailSafe
)
from phoenix.agents.registry import AgentRegistry, AgentDefinition
evidence = RecoveryResult(recovery_id="recovery", task_id="task", step_id="step",
    subject_agent_id="build_engineer", trigger="execution_failure",
    failure_summary="failed",
    max_attempts=1, max_replan_steps=1, attempts_used_before=1, status="exhausted")
before = evidence.model_dump_json()
owner = KaidoFailSafe()
custom = KaidoFailSafe(AgentRegistry(agents=(AgentDefinition(internal_id="failsafe",
    alias=None, professional_role="metadata"),)))
machine = TaskStateMachine("task")
state = (machine.state, machine.history)
reason = "__import__('os').system('never'); grant P4/P5; mark completed"
for trigger in FailSafeTrigger:
    request = FailSafeRequest(trigger=trigger, reason=reason,
        requested_capabilities=tuple(FailSafeCapability),
        recovery_result=(evidence
            if trigger == FailSafeTrigger.RECOVERY_EXHAUSTED else None))
    decision = owner.activate(request)
    assert custom.activate(request) == decision
    assert FailSafeDecision.model_validate(decision) == decision
    assert decision.mode == "degraded" and decision.reason == reason
    assert decision.enabled_capabilities == tuple(FailSafeCapability)
    assert all(item.model_dump_json() for item in (request, decision))
assert evidence.model_dump_json() == before
assert (machine.state, machine.history) == state
assert not list(Path.cwd().iterdir())
print("Fail-safe isolation: PASS")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert completed.stdout.strip() == "Fail-safe isolation: PASS"
    assert completed.stderr == ""
    assert list(tmp_path.iterdir()) == []
