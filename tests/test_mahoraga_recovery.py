"""PHX-027 explicit failure, bounded proposals and caller-owned lifetimes."""

import ast
import asyncio
import inspect
import subprocess
import sys
from collections.abc import Coroutine
from itertools import product
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from phoenix.agents import recovery
from phoenix.agents.handoff import HandoffBuilder, HandoffEnvelope
from phoenix.agents.recovery import (
    CancellationResult,
    CancellationStatus,
    DuplicateRecoveryError,
    MahoragaRecovery,
    MahoragaRecoveryError,
    RecoveryAction,
    RecoveryDraft,
    RecoveryRequest,
    RecoveryResult,
    RecoveryStatus,
    RecoveryTrigger,
)
from phoenix.agents.registry import (
    AgentDefinition,
    AgentRegistry,
    build_default_agent_registry,
)
from phoenix.agents.task_graph import (
    TaskGraph,
    TaskState,
    TaskStateMachine,
    TaskStepDefinition,
)
from phoenix.agents.verifier import (
    VerificationCheck,
    VerificationResult,
    verification_requirement_satisfied,
)

BAD_TEXT = (None, True, 1, 1.5, b"text", object(), "", " \t\n")
BAD_POSITIVE = (None, True, False, 0, -1, 1.0, "1", b"1", object(), float("inf"))
BAD_NONNEGATIVE = (None, True, False, -1, 0.0, "0", b"0", object(), float("inf"))
ACTION_VALUES: dict[str, object] = {
    "replacement_agent_id": "diagnostics",
    "replacement_provider_id": "neutral-provider",
    "replacement_model_id": "neutral-model",
    "replacement_tool_id": "neutral-tool",
    "parameter_adjustments": ("  First guidance\n", "Second guidance"),
    "replan_step_ids": ("z", "step"),
}
ACTION_FIELDS: dict[RecoveryAction, set[str]] = {
    RecoveryAction.RETRY_SAME: set(),
    RecoveryAction.ADJUST_PARAMETERS: {"parameter_adjustments"},
    RecoveryAction.SWITCH_TOOL: {"replacement_tool_id"},
    RecoveryAction.SWITCH_MODEL_PROVIDER: {
        "replacement_provider_id",
        "replacement_model_id",
    },
    RecoveryAction.SWITCH_AGENT: {"replacement_agent_id"},
    RecoveryAction.REPLAN_SUBGRAPH: {"replan_step_ids"},
}


def request(**changes: object) -> RecoveryRequest:
    return RecoveryRequest.model_validate(
        {
            "recovery_id": "recovery",
            "task_id": "task",
            "step_id": "step",
            "subject_agent_id": "build_engineer",
            "trigger": "execution_failure",
            "failure_summary": "  Original failure\n",
            "attempts_used": 1,
            "max_attempts": 3,
            "max_replan_steps": 2,
            **changes,
        }
    )


def draft(
    action: RecoveryAction = RecoveryAction.RETRY_SAME, /, **changes: object
) -> RecoveryDraft:
    return RecoveryDraft.model_validate(
        {
            "recovery_id": "recovery",
            "action": action,
            "rationale": "  Descriptive strategy\n",
            **{name: ACTION_VALUES[name] for name in ACTION_FIELDS[action]},
            **changes,
        }
    )


def result(**changes: object) -> RecoveryResult:
    data = request().model_dump()
    data["attempts_used_before"] = data.pop("attempts_used")
    return RecoveryResult.model_validate(
        {
            **data,
            "status": "proposed",
            "attempt_number": 2,
            "proposal": draft(),
            **changes,
        }
    )


def graph() -> TaskGraph:
    return TaskGraph(
        task_id="task",
        steps=tuple(
            TaskStepDefinition(
                step_id=identity,
                owner_agent_id="build_engineer",
                objective="  Objective\n",
                input_refs=("file:///never-dereference", "memory:unread"),
                depends_on=dependencies,
                expected_output="opaque expected output",
                timeout_seconds=0.000001,
                completion_rule="unexecuted criterion",
            )
            for identity, dependencies in (
                ("step", ()),
                ("z", ("step",)),
                ("a", ("z",)),
            )
        ),
    )


def handoff() -> HandoffEnvelope:
    return HandoffBuilder().build(
        graph=graph(),
        step_id="step",
        source_agent_id="phoenix",
        expected_output_schema="opaque schema",
        verification_required=True,
        permission_scope=("P3:modify", "P4:publish", "P5:blocked"),
    )


def verification(**changes: object) -> VerificationResult:
    return VerificationResult.model_validate(
        {
            "verification_id": "verification",
            "task_id": "task",
            "step_id": "step",
            "subject_agent_id": "build_engineer",
            "verdict": "failed",
            "checks": (
                VerificationCheck(
                    check_id="check",
                    criterion="explicit",
                    passed=False,
                    evidence_summary="Original failed evidence",
                ),
            ),
            **changes,
        }
    )


class Responder:
    def __init__(self, action: RecoveryAction = RecoveryAction.RETRY_SAME) -> None:
        self.action = action
        self.requests: list[RecoveryRequest] = []

    async def propose(self, value: RecoveryRequest) -> RecoveryDraft:
        self.requests.append(value)
        return draft(self.action, recovery_id=value.recovery_id)


async def recover(
    owner: MahoragaRecovery,
    responder: Responder,
    identity: str = "recovery",
    /,
    **changes: object,
) -> RecoveryResult:
    arguments: dict[str, object] = {
        "graph": graph(),
        "handoff": handoff(),
        "trigger": RecoveryTrigger.EXECUTION_FAILURE,
        "failure_summary": "  Original failure\n",
        "attempts_used": 1,
        "max_attempts": 3,
        "max_replan_steps": 2,
        "recovery_id": identity,
        "responder": responder,
        **changes,
    }
    return await owner.recover(**arguments)  # type: ignore[arg-type]


def run(exercise: Coroutine[object, object, None]) -> None:
    asyncio.run(asyncio.wait_for(exercise, timeout=5))


@pytest.mark.parametrize("alias", [None, "renamed", "MAHORAGA"])
def test_authoritative_recovery_identity_ignores_aliases(alias: str | None) -> None:
    registry = AgentRegistry(
        agents=tuple(
            AgentDefinition(
                internal_id=item.internal_id,
                alias=alias,
                professional_role="Display metadata",
            )
            for item in build_default_agent_registry().agents
        )
    )
    owner = MahoragaRecovery(registry)
    assert owner.recovery_agent_id == "adaptive_recovery"
    assert owner.active_recovery_ids == ()

    async def exercise() -> None:
        assert await recover(owner, Responder()) == result()

    run(exercise())


@pytest.mark.parametrize("identity", ["MAHORAGA", "phoenix", "other"])
def test_alias_role_or_other_id_cannot_establish_authority(identity: str) -> None:
    registry = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id=identity,
                alias="MAHORAGA",
                professional_role="Adaptive recovery",
            ),
        )
    )
    with pytest.raises(MahoragaRecoveryError, match="Recovery internal ID"):
        MahoragaRecovery(registry)
    with pytest.raises(MahoragaRecoveryError):
        MahoragaRecovery(AgentRegistry())


@pytest.mark.parametrize("contract", [request, result])
@pytest.mark.parametrize(
    "field",
    ["recovery_id", "task_id", "step_id", "subject_agent_id", "failure_summary"],
)
@pytest.mark.parametrize("invalid", BAD_TEXT)
def test_strict_ids_and_meaningful_failure_text(
    contract: object, field: str, invalid: object
) -> None:
    factory = request if contract is request else result
    with pytest.raises(ValidationError):
        factory(**{field: invalid})


@pytest.mark.parametrize("contract", [request, result])
@pytest.mark.parametrize("field", ["max_attempts", "max_replan_steps"])
@pytest.mark.parametrize("invalid", BAD_POSITIVE)
def test_explicit_positive_budgets(
    contract: object, field: str, invalid: object
) -> None:
    factory = request if contract is request else result
    with pytest.raises(ValidationError):
        factory(**{field: invalid})


@pytest.mark.parametrize("contract", [request, result])
@pytest.mark.parametrize("invalid", BAD_NONNEGATIVE)
def test_used_attempts_are_strict_nonnegative(
    contract: object, invalid: object
) -> None:
    factory = request if contract is request else result
    field = "attempts_used" if contract is request else "attempts_used_before"
    with pytest.raises(ValidationError):
        factory(**{field: invalid})


@pytest.mark.parametrize("invalid", BAD_POSITIVE)
def test_proposed_attempt_number_is_strict_positive(invalid: object) -> None:
    with pytest.raises(ValidationError):
        result(attempt_number=invalid)


@pytest.mark.parametrize(
    "invalid",
    [
        "success",
        "completed",
        "verified_pass",
        "permission_approved",
        None,
        True,
        1,
        b"timeout",
    ],
)
def test_invalid_trigger_fails_even_on_exhausted_path(invalid: object) -> None:
    with pytest.raises(ValidationError):
        request(trigger=invalid)

    async def exercise() -> None:
        owner, responder = MahoragaRecovery(), Responder()
        with pytest.raises(ValidationError):
            await recover(owner, responder, trigger=invalid, attempts_used=3)
        assert responder.requests == [] and owner.active_recovery_ids == ()

    run(exercise())


def test_exact_vocabularies_normalized_ids_and_original_text() -> None:
    assert [value.value for value in RecoveryTrigger] == [
        "execution_failure",
        "verification_failure",
        "timeout",
        "resource_unavailable",
    ]
    assert [value.value for value in RecoveryAction] == [
        "retry_same",
        "adjust_parameters",
        "switch_tool",
        "switch_model_provider",
        "switch_agent",
        "replan_subgraph",
    ]
    assert [value.value for value in RecoveryStatus] == ["proposed", "exhausted"]
    assert [value.value for value in CancellationStatus] == ["accepted", "not_found"]
    value = request(
        recovery_id=" ID ",
        task_id=" Task ",
        step_id=" Step ",
        subject_agent_id=" Subject ",
    )
    assert (
        value.recovery_id,
        value.task_id,
        value.step_id,
        value.subject_agent_id,
    ) == ("ID", "Task", "Step", "Subject")
    assert value.failure_summary == "  Original failure\n"
    assert draft().rationale == "  Descriptive strategy\n"
    assert (
        draft(
            RecoveryAction.SWITCH_TOOL, replacement_tool_id=" Tool "
        ).replacement_tool_id
        == "Tool"
    )
    data = request().model_dump()
    del data["recovery_id"]
    a, b = RecoveryRequest.model_validate(data), RecoveryRequest.model_validate(data)
    assert a.recovery_id != b.recovery_id
    assert UUID(a.recovery_id).version == UUID(b.recovery_id).version == 4


@pytest.mark.parametrize(
    "value",
    [
        request(),
        draft(),
        result(),
        CancellationResult(recovery_id="id", status=CancellationStatus.NOT_FOUND),
    ],
)
def test_public_models_frozen_extra_forbidden_and_serializable(
    value: BaseModel,
) -> None:
    assert type(value).model_validate_json(value.model_dump_json()) == value
    assert type(value).model_validate(value) == value
    for field in type(value).model_fields:
        with pytest.raises(ValidationError, match="frozen"):
            setattr(value, field, "changed")
    for name in (
        "metadata",
        "callback",
        "permission_grant",
        "completed",
        "raw_response",
    ):
        with pytest.raises(ValidationError, match="Extra inputs"):
            type(value).model_validate({**value.model_dump(), name: object()})
        with pytest.raises(ValidationError, match="Extra inputs"):
            type(value).model_validate(value.model_copy(update={name: object()}))


@pytest.mark.parametrize(
    "invalid", [None, True, "MAHORAGA", "phoenix", " adaptive_recovery "]
)
def test_result_recovery_agent_literal(invalid: object) -> None:
    with pytest.raises(ValidationError):
        result(recovery_agent_id=invalid)


@pytest.mark.parametrize("action", list(RecoveryAction))
@pytest.mark.parametrize("present", list(product((False, True), repeat=6)))
def test_every_action_rejects_missing_and_irrelevant_fields(
    action: RecoveryAction, present: tuple[bool, ...]
) -> None:
    supplied = {
        name: value
        for (name, value), enabled in zip(ACTION_VALUES.items(), present, strict=True)
        if enabled
    }
    data = {
        "recovery_id": "recovery",
        "action": action,
        "rationale": "descriptive",
        **supplied,
    }
    if set(supplied) == ACTION_FIELDS[action]:
        assert RecoveryDraft.model_validate(data).action == action
    else:
        with pytest.raises(ValidationError, match="contradict"):
            RecoveryDraft.model_validate(data)


@pytest.mark.parametrize(
    "field,action",
    [
        ("replacement_agent_id", RecoveryAction.SWITCH_AGENT),
        ("replacement_provider_id", RecoveryAction.SWITCH_MODEL_PROVIDER),
        ("replacement_model_id", RecoveryAction.SWITCH_MODEL_PROVIDER),
        ("replacement_tool_id", RecoveryAction.SWITCH_TOOL),
    ],
)
@pytest.mark.parametrize("invalid", BAD_TEXT)
def test_replacement_identifiers_strict(
    field: str, action: RecoveryAction, invalid: object
) -> None:
    with pytest.raises(ValidationError):
        draft(action, **{field: invalid})


@pytest.mark.parametrize("invalid", BAD_TEXT)
def test_draft_rationale_meaningful(invalid: object) -> None:
    with pytest.raises(ValidationError):
        draft(rationale=invalid)


@pytest.mark.parametrize(
    "field,action",
    [
        ("parameter_adjustments", RecoveryAction.ADJUST_PARAMETERS),
        ("replan_step_ids", RecoveryAction.REPLAN_SUBGRAPH),
    ],
)
@pytest.mark.parametrize(
    "invalid",
    [
        None,
        (),
        [],
        "scalar",
        {"unordered"},
        {},
        (True,),
        (b"text",),
        (" ",),
        ("same", " same\n"),
    ],
)
def test_nonempty_ordered_unique_descriptions(
    field: str, action: RecoveryAction, invalid: object
) -> None:
    with pytest.raises(ValidationError):
        draft(action, **{field: invalid})


def test_sequences_preserve_order_formatting_and_detach_containers() -> None:
    descriptions = ["  z guidance\n", " a guidance "]
    value = draft(RecoveryAction.ADJUST_PARAMETERS, parameter_adjustments=descriptions)
    descriptions.clear()
    assert value.parameter_adjustments == ("  z guidance\n", " a guidance ")
    steps = [" z ", " step "]
    proposal = draft(RecoveryAction.REPLAN_SUBGRAPH, replan_step_ids=steps)
    steps.clear()
    assert proposal.replan_step_ids == ("z", "step")
    forged = draft().model_copy(
        update={
            "action": RecoveryAction.ADJUST_PARAMETERS,
            "parameter_adjustments": ["description"],
        }
    )
    detached = result(proposal=forged)
    assert detached.proposal is not None
    assert detached.proposal.parameter_adjustments == ("description",)
    assert detached.proposal is not forged


@pytest.mark.parametrize(
    "changes",
    [
        {"proposal": None},
        {"proposal": draft(recovery_id="wrong")},
        {"attempt_number": None},
        {"attempt_number": 1},
        {"attempt_number": 3},
        {"max_attempts": 1},
        {"attempts_used_before": 3},
        {"status": "completed"},
        {"status": "passed"},
        {"status": "exhausted"},
        {"status": "exhausted", "attempts_used_before": 3, "proposal": None},
        {"status": "exhausted", "attempts_used_before": 3, "attempt_number": None},
        {
            "proposal": draft(
                RecoveryAction.SWITCH_AGENT, replacement_agent_id="build_engineer"
            )
        },
        {"proposal": draft(RecoveryAction.REPLAN_SUBGRAPH, replan_step_ids=("z",))},
        {"proposal": draft(RecoveryAction.REPLAN_SUBGRAPH), "max_replan_steps": 1},
    ],
)
def test_result_rejects_contradictory_status_budget_and_proposal(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        result(**changes)


@pytest.mark.parametrize("action", list(RecoveryAction))
@pytest.mark.parametrize("used", [0, 1, 2])
def test_one_exact_request_one_bounded_proposal_no_execution(
    action: RecoveryAction, used: int, capsys: pytest.CaptureFixture[str]
) -> None:
    async def exercise() -> None:
        owner, responder, parent, transfer = (
            MahoragaRecovery(),
            Responder(action),
            graph(),
            handoff(),
        )
        machine = TaskStateMachine("task")
        for state in (
            TaskState.PLANNING,
            TaskState.READY,
            TaskState.RUNNING,
            TaskState.FAILED,
        ):
            machine.transition(state)
        before = (
            parent.model_dump_json(),
            transfer.model_dump_json(),
            machine.state,
            machine.history,
        )
        with patch.object(
            TaskStateMachine, "transition", side_effect=AssertionError("state mutation")
        ):
            value = await recover(
                owner,
                responder,
                " Recovery ",
                graph=parent,
                handoff=transfer,
                attempts_used=used,
            )
        assert responder.requests == [
            request(recovery_id="Recovery", attempts_used=used)
        ]
        assert value == result(
            recovery_id="Recovery",
            attempts_used_before=used,
            attempt_number=used + 1,
            proposal=draft(action, recovery_id="Recovery"),
        )
        assert value.status == RecoveryStatus.PROPOSED
        assert value.attempt_number == used + 1 <= value.max_attempts
        assert value.failure_summary == "  Original failure\n"
        assert len(responder.requests) == 1 and owner.active_recovery_ids == ()
        assert (
            parent.model_dump_json(),
            transfer.model_dump_json(),
            machine.state,
            machine.history,
        ) == before

    run(exercise())
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("used", [3, 4, 10**30])
def test_exhaustion_never_calls_responder_or_registers_active_work(used: int) -> None:
    class ForbiddenResponder(Responder):
        async def propose(self, value: RecoveryRequest) -> RecoveryDraft:
            raise AssertionError("Exhausted responder invoked")

    async def exercise() -> None:
        owner = MahoragaRecovery()
        with patch.object(
            asyncio, "current_task", side_effect=AssertionError("active registration")
        ):
            value = await recover(owner, ForbiddenResponder(), attempts_used=used)
        assert value == result(
            status="exhausted",
            attempts_used_before=used,
            attempt_number=None,
            proposal=None,
        )
        assert value.proposal is None and value.attempt_number is None
        assert value.failure_summary == "  Original failure\n"
        assert owner.active_recovery_ids == ()
        assert (await owner.cancel("recovery")).status == CancellationStatus.NOT_FOUND
        assert (await recover(owner, Responder())).attempt_number == 2

    run(exercise())


@pytest.mark.parametrize("trigger", list(RecoveryTrigger))
def test_explicit_failure_triggers_and_levi_evidence_preserved(
    trigger: RecoveryTrigger,
) -> None:
    async def exercise() -> None:
        owner, responder, evidence = MahoragaRecovery(), Responder(), verification()
        before = evidence.model_dump_json()
        value = await recover(
            owner,
            responder,
            trigger=trigger,
            verification_result=evidence
            if trigger == RecoveryTrigger.VERIFICATION_FAILURE
            else None,
        )
        assert value.trigger == trigger
        assert evidence.model_dump_json() == before
        assert not verification_requirement_satisfied(handoff(), evidence)
        assert len(responder.requests) == 1

    run(exercise())


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        object(),
        verification(task_id="other"),
        verification(step_id="other"),
        verification(subject_agent_id="diagnostics"),
        verification(
            verdict="passed",
            checks=(
                VerificationCheck(
                    check_id="pass",
                    criterion="explicit",
                    passed=True,
                    evidence_summary="Passed",
                ),
            ),
        ),
        verification().model_copy(update={"verdict": "passed"}),
        verification().model_copy(update={"grant": object()}),
        verification().model_copy(
            update={
                "checks": (
                    VerificationCheck.model_construct(
                        check_id="forged",
                        criterion="explicit",
                        passed=0,
                        evidence_summary="False",
                    ),
                )
            }
        ),
    ],
)
@pytest.mark.parametrize("used", [1, 3])
def test_verification_failure_requires_exact_correlated_failed_evidence(
    invalid: object, used: int
) -> None:
    async def exercise() -> None:
        owner, responder = MahoragaRecovery(), Responder()
        with pytest.raises((MahoragaRecoveryError, ValidationError)):
            await recover(
                owner,
                responder,
                trigger=RecoveryTrigger.VERIFICATION_FAILURE,
                verification_result=invalid,
                attempts_used=used,
            )
        assert responder.requests == [] and owner.active_recovery_ids == ()

    run(exercise())


@pytest.mark.parametrize(
    "trigger",
    [
        RecoveryTrigger.EXECUTION_FAILURE,
        RecoveryTrigger.TIMEOUT,
        RecoveryTrigger.RESOURCE_UNAVAILABLE,
    ],
)
def test_irrelevant_verification_evidence_rejected(trigger: RecoveryTrigger) -> None:
    async def exercise() -> None:
        with pytest.raises(
            MahoragaRecoveryError, match="requires verification_failure"
        ):
            await recover(
                MahoragaRecovery(),
                Responder(),
                trigger=trigger,
                verification_result=verification(),
            )

    run(exercise())


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "other"),
        ("step_id", "unknown"),
        ("target_agent_id", "diagnostics"),
        ("objective", "different"),
        ("input_refs", ("memory:unread", "file:///never-dereference")),
        ("timeout_seconds", 1),
        ("source_agent_id", "unknown"),
        ("source_agent_id", "PHOENIX"),
    ],
)
@pytest.mark.parametrize("used", [1, 3])
def test_graph_handoff_correlation_precedes_responder_and_exhaustion(
    field: str, value: object, used: int
) -> None:
    async def exercise() -> None:
        owner, responder = MahoragaRecovery(), Responder()
        with pytest.raises(MahoragaRecoveryError):
            await recover(
                owner,
                responder,
                handoff=handoff().model_copy(update={field: value}),
                attempts_used=used,
            )
        assert responder.requests == [] and owner.active_recovery_ids == ()

    run(exercise())


@pytest.mark.parametrize("replacement", ["unknown", "CHOPPER", "build_engineer"])
def test_switch_agent_requires_registered_different_internal_id(
    replacement: str,
) -> None:
    class ReplacementResponder(Responder):
        async def propose(self, value: RecoveryRequest) -> RecoveryDraft:
            self.requests.append(value)
            return draft(RecoveryAction.SWITCH_AGENT, replacement_agent_id=replacement)

    async def exercise() -> None:
        owner, responder = MahoragaRecovery(), ReplacementResponder()
        with pytest.raises(MahoragaRecoveryError):
            await recover(owner, responder)
        assert len(responder.requests) == 1 and owner.active_recovery_ids == ()
        await recover(owner, Responder())

    run(exercise())


@pytest.mark.parametrize(
    "steps,limit,valid",
    [
        (("step",), 1, True),
        (("z", "step"), 2, True),
        (("a", "z", "step"), 3, True),
        (("z", "step"), 1, False),
        (("z",), 2, False),
        (("unknown", "step"), 2, False),
    ],
)
def test_bounded_replan_membership_failed_step_and_order(
    steps: tuple[str, ...], limit: int, valid: bool
) -> None:
    class ReplanResponder(Responder):
        async def propose(self, value: RecoveryRequest) -> RecoveryDraft:
            self.requests.append(value)
            return draft(RecoveryAction.REPLAN_SUBGRAPH, replan_step_ids=steps)

    async def exercise() -> None:
        owner, responder = MahoragaRecovery(), ReplanResponder()
        if valid:
            value = await recover(owner, responder, max_replan_steps=limit)
            assert (
                value.proposal is not None and value.proposal.replan_step_ids == steps
            )
        else:
            with pytest.raises(MahoragaRecoveryError, match="bounded replan"):
                await recover(owner, responder, max_replan_steps=limit)
        assert len(responder.requests) == 1 and owner.active_recovery_ids == ()

    run(exercise())


def test_custom_registry_is_snapshotted_and_every_graph_owner_revalidated() -> None:
    registry = AgentRegistry(
        agents=tuple(
            AgentDefinition(
                internal_id=identity, alias="MAHORAGA", professional_role="Metadata"
            )
            for identity in ("adaptive_recovery", "phoenix", "custom", "replacement")
        )
    )
    data = graph().model_dump()
    for node in data["steps"]:
        node["owner_agent_id"] = "custom"
    parent = TaskGraph.model_validate(data, context={"agent_registry": registry})
    transfer = HandoffBuilder(registry).build(
        graph=parent,
        step_id="step",
        source_agent_id="phoenix",
        expected_output_schema="opaque",
        verification_required=True,
    )
    mutable = list(registry.agents)
    owner = MahoragaRecovery(registry.model_copy(update={"agents": mutable}))
    mutable.clear()

    class CustomResponder(Responder):
        async def propose(self, value: RecoveryRequest) -> RecoveryDraft:
            return draft(
                RecoveryAction.SWITCH_AGENT, replacement_agent_id="replacement"
            )

    async def exercise() -> None:
        value = await recover(owner, CustomResponder(), graph=parent, handoff=transfer)
        assert value.subject_agent_id == "custom"
        assert (
            value.proposal is not None
            and value.proposal.replacement_agent_id == "replacement"
        )
        for invalid_owner in ("missing", "MAHORAGA"):
            invalid_step = parent.steps[-1].model_copy(
                update={"owner_agent_id": invalid_owner}
            )
            forged = parent.model_copy(
                update={"steps": (*parent.steps[:-1], invalid_step)}
            )
            with pytest.raises(ValidationError, match="Unknown step owner"):
                await recover(owner, Responder(), graph=forged, handoff=transfer)
        missing_source = MahoragaRecovery(
            AgentRegistry(
                agents=tuple(
                    agent for agent in registry.agents if agent.internal_id != "phoenix"
                )
            )
        )
        with pytest.raises(MahoragaRecoveryError, match="Handoff agent"):
            await recover(missing_source, Responder(), graph=parent, handoff=transfer)

    run(exercise())


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        "raw",
        object(),
        result(),
        draft(recovery_id="wrong"),
        draft().model_copy(update={"grant": object()}),
        draft().model_copy(update={"action": "mark_completed"}),
        draft().model_copy(update={"rationale": " "}),
        draft().model_copy(update={"replacement_tool_id": "tool"}),
        draft(RecoveryAction.ADJUST_PARAMETERS).model_copy(
            update={"parameter_adjustments": {"set"}}
        ),
        draft(RecoveryAction.ADJUST_PARAMETERS).model_copy(
            update={"parameter_adjustments": ("same", " same ")}
        ),
        draft(RecoveryAction.REPLAN_SUBGRAPH).model_copy(
            update={"replan_step_ids": ("step", " step ")}
        ),
        draft(RecoveryAction.SWITCH_MODEL_PROVIDER).model_copy(
            update={"replacement_model_id": True}
        ),
    ],
)
def test_untrusted_malformed_draft_cleans_and_allows_id_reuse(invalid: object) -> None:
    class InvalidResponder(Responder):
        async def propose(self, value: RecoveryRequest) -> RecoveryDraft:
            self.requests.append(value)
            return invalid  # type: ignore[return-value]

    async def exercise() -> None:
        owner, responder = MahoragaRecovery(), InvalidResponder()
        with pytest.raises(MahoragaRecoveryError):
            await recover(owner, responder)
        assert len(responder.requests) == 1 and owner.active_recovery_ids == ()
        assert (await recover(owner, Responder())).status == RecoveryStatus.PROPOSED

    run(exercise())


@pytest.mark.parametrize(
    "extension",
    ["draft", "graph", "step", "handoff", "verification", "registry", "definition"],
)
def test_extended_exact_boundary_contracts_are_rejected(extension: str) -> None:
    sources: dict[str, BaseModel] = {
        "draft": draft(),
        "graph": graph(),
        "step": graph().steps[0],
        "handoff": handoff(),
        "verification": verification(),
        "registry": build_default_agent_registry(),
        "definition": build_default_agent_registry().require("adaptive_recovery"),
    }
    original = sources[extension]
    extended: type[BaseModel] = type(
        "Extended", (type(original),), {"__annotations__": {"grant": str}}
    )
    value = extended.model_construct(**{**vars(original), "grant": "untrusted"})
    if extension in {"registry", "definition"}:
        supplied = (
            value
            if extension == "registry"
            else AgentRegistry.model_construct(agents=(value,))
        )
        with pytest.raises(MahoragaRecoveryError):
            MahoragaRecovery(supplied)  # type: ignore[arg-type]
    else:

        class ExtendedResponder(Responder):
            async def propose(self, request: RecoveryRequest) -> RecoveryDraft:
                return value if extension == "draft" else draft()  # type: ignore[return-value]

        async def exercise() -> None:
            owner = MahoragaRecovery()
            changes: dict[str, object] = {}
            if extension in {"graph", "handoff"}:
                changes[extension] = value
            elif extension == "step":
                changes["graph"] = graph().model_copy(update={"steps": (value,)})
            elif extension == "verification":
                changes.update(
                    trigger=RecoveryTrigger.VERIFICATION_FAILURE,
                    verification_result=value,
                )
            with pytest.raises(MahoragaRecoveryError):
                await recover(owner, ExtendedResponder(), **changes)
            assert owner.active_recovery_ids == ()

        run(exercise())


def test_constructed_copied_contracts_and_injected_extras_revalidated() -> None:
    async def exercise() -> None:
        owner, responder = MahoragaRecovery(), Responder()
        invalids = (
            {"graph": graph().model_copy(update={"grant": object()})},
            {
                "graph": graph().model_copy(
                    update={
                        "steps": (
                            graph().steps[0].model_copy(update={"grant": object()}),
                        )
                    }
                )
            },
            {
                "graph": graph().model_copy(
                    update={
                        "steps": (
                            graph()
                            .steps[0]
                            .model_copy(update={"timeout_seconds": True}),
                        )
                    }
                )
            },
            {"handoff": handoff().model_copy(update={"verification_required": 1})},
            {"handoff": handoff().model_copy(update={"grant": object()})},
            {"graph": object()},
            {"handoff": object()},
        )
        for changes in invalids:
            with pytest.raises((MahoragaRecoveryError, ValidationError)):
                await recover(owner, responder, **changes)
        assert responder.requests == [] and owner.active_recovery_ids == ()

    run(exercise())
    registry = build_default_agent_registry()
    for invalid in (
        registry.model_copy(update={"grant": object()}),
        registry.model_copy(
            update={
                "agents": (
                    registry.require("adaptive_recovery").model_copy(
                        update={"grant": object()}
                    ),
                )
            }
        ),
    ):
        with pytest.raises(ValidationError):
            MahoragaRecovery(invalid)
    forged = result().model_copy(
        update={"proposal": draft().model_copy(update={"grant": object()})}
    )
    with pytest.raises(ValidationError):
        RecoveryResult.model_validate(forged)


def test_runtime_defaults_and_no_graph_owned_overrides() -> None:
    async def exercise() -> None:
        owner = MahoragaRecovery()
        a = await recover(owner, Responder(), recovery_id=None)
        b = await recover(owner, Responder(), recovery_id=None)
        assert a.recovery_id != b.recovery_id and UUID(a.recovery_id).version == 4

    run(exercise())
    assert set(inspect.signature(MahoragaRecovery.recover).parameters) == {
        "self",
        "graph",
        "handoff",
        "trigger",
        "failure_summary",
        "attempts_used",
        "max_attempts",
        "max_replan_steps",
        "responder",
        "verification_result",
        "recovery_id",
    }


@pytest.mark.parametrize(
    "field,invalid",
    [
        (field, invalid)
        for field in ("recovery_id", "failure_summary")
        for invalid in BAD_TEXT
        if not (field == "recovery_id" and invalid is None)
    ],
)
def test_invalid_runtime_values_never_reach_responder(
    field: str, invalid: object
) -> None:
    async def exercise() -> None:
        owner, responder = MahoragaRecovery(), Responder()
        with pytest.raises(ValidationError):
            await recover(owner, responder, **{field: invalid})
        assert responder.requests == [] and owner.active_recovery_ids == ()

    run(exercise())


class SuspendedResponder(Responder):
    def __init__(self, terminal: str = "proposed", suppress: bool = False) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.terminal = terminal
        self.suppress = suppress
        self.cancelled = False

    async def propose(self, value: RecoveryRequest) -> RecoveryDraft:
        self.requests.append(value)
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            if not self.suppress:
                raise
        if self.terminal == "exception":
            raise RuntimeError("fixture failure")
        if self.terminal == "malformed":
            return draft().model_copy(update={"rationale": " "})
        if self.terminal == "action":
            return draft().model_copy(update={"replacement_tool_id": "tool"})
        if self.terminal == "replan":
            return draft(
                RecoveryAction.REPLAN_SUBGRAPH,
                recovery_id=value.recovery_id,
                replan_step_ids=("step", "unknown"),
            )
        if self.terminal == "agent":
            return draft(
                RecoveryAction.SWITCH_AGENT,
                recovery_id=value.recovery_id,
                replacement_agent_id="unknown",
            )
        return draft(
            recovery_id="wrong" if self.terminal == "mismatch" else value.recovery_id
        )


@pytest.mark.parametrize(
    "terminal",
    [
        "proposed",
        "exception",
        "malformed",
        "action",
        "replan",
        "agent",
        "mismatch",
        "cancel",
        "caller_cancel",
    ],
)
def test_concurrent_isolation_all_terminal_cleanup_and_id_reuse(terminal: str) -> None:
    async def exercise() -> None:
        owner = MahoragaRecovery()
        a, b = SuspendedResponder(terminal), SuspendedResponder()
        tb = asyncio.create_task(recover(owner, b, "b"))
        ta = asyncio.create_task(recover(owner, a, "a"))
        try:
            await a.entered.wait()
            await b.entered.wait()
            snapshot = owner.active_recovery_ids
            assert snapshot == ("a", "b")
            with pytest.raises(AttributeError):
                owner.active_recovery_ids = ()  # type: ignore[misc]
            if terminal == "cancel":
                ack = await owner.cancel(" a ")
                assert (
                    ack.status == CancellationStatus.ACCEPTED and ack.recovery_id == "a"
                )
            elif terminal == "caller_cancel":
                ta.cancel()
            else:
                a.release.set()
            if terminal in {"cancel", "caller_cancel"}:
                with pytest.raises(asyncio.CancelledError):
                    await ta
            elif terminal == "exception":
                with pytest.raises(RuntimeError, match="fixture failure"):
                    await ta
            elif terminal != "proposed":
                with pytest.raises(MahoragaRecoveryError):
                    await ta
            else:
                assert (await ta).status == RecoveryStatus.PROPOSED
            assert snapshot == ("a", "b") and owner.active_recovery_ids == ("b",)
            assert not tb.done() and not b.cancelled
            assert (await owner.cancel("a")).status == CancellationStatus.NOT_FOUND
            assert (await recover(owner, Responder(), "a")).recovery_id == "a"
            assert owner.active_recovery_ids == ("b",)
            b.release.set()
            await tb
            assert owner.active_recovery_ids == ()
        finally:
            ta.cancel()
            tb.cancel()
            await asyncio.gather(ta, tb, return_exceptions=True)

    run(exercise())


@pytest.mark.parametrize("used", [1, 3])
def test_duplicate_active_id_preserves_original_even_for_exhausted_input(
    used: int,
) -> None:
    async def exercise() -> None:
        owner, responder, duplicate = (
            MahoragaRecovery(),
            SuspendedResponder(),
            Responder(),
        )
        task = asyncio.create_task(recover(owner, responder))
        try:
            await responder.entered.wait()
            with pytest.raises(DuplicateRecoveryError):
                await recover(owner, duplicate, " recovery ", attempts_used=used)
            assert duplicate.requests == [] and owner.active_recovery_ids == (
                "recovery",
            )
            assert not task.done() and not responder.cancelled
            responder.release.set()
            await task
            assert (await recover(owner, duplicate)).status == RecoveryStatus.PROPOSED
            assert len(duplicate.requests) == 1 and owner.active_recovery_ids == ()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    run(exercise())


@pytest.mark.parametrize("source", ["caller", "recovery"])
def test_suppressed_cancellation_cannot_finalize_proposal(source: str) -> None:
    async def exercise() -> None:
        owner, responder = MahoragaRecovery(), SuspendedResponder(suppress=True)
        task = asyncio.create_task(recover(owner, responder))
        try:
            await responder.entered.wait()
            if source == "caller":
                task.cancel()
            else:
                assert (
                    await owner.cancel("recovery")
                ).status == CancellationStatus.ACCEPTED
            with pytest.raises(asyncio.CancelledError):
                await task
            assert responder.cancelled and owner.active_recovery_ids == ()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    run(exercise())


@pytest.mark.parametrize("clear_counter", [True, False])
def test_local_cancellation_survives_counter_clear_and_missing_delivery(
    clear_counter: bool,
) -> None:
    owner = MahoragaRecovery()

    class SelfCancellingResponder(Responder):
        async def propose(self, value: RecoveryRequest) -> RecoveryDraft:
            await owner.cancel(value.recovery_id)
            if clear_counter:
                try:
                    await asyncio.sleep(0)  # Deliberate delivery checkpoint.
                except asyncio.CancelledError:
                    task = asyncio.current_task()
                    assert task is not None
                    task.uncancel()
                    assert task.cancelling() == 0
            return draft()

    async def exercise() -> None:
        task = asyncio.create_task(recover(owner, SelfCancellingResponder()))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owner.active_recovery_ids == ()

    run(exercise())


def test_nested_eligible_calls_cannot_share_cancellation_ownership() -> None:
    owner = MahoragaRecovery()

    class NestedResponder(Responder):
        async def propose(self, value: RecoveryRequest) -> RecoveryDraft:
            with pytest.raises(MahoragaRecoveryError, match="already owns"):
                await recover(owner, Responder(), "nested")
            assert owner.active_recovery_ids == ("recovery",)
            assert (await owner.cancel("nested")).status == CancellationStatus.NOT_FOUND
            return draft()

    async def exercise() -> None:
        await recover(owner, NestedResponder())
        assert owner.active_recovery_ids == ()

    run(exercise())


@pytest.mark.parametrize("invalid", BAD_TEXT)
def test_cancel_strict_id_and_unknown_outcome(invalid: object) -> None:
    async def exercise() -> None:
        owner = MahoragaRecovery()
        with pytest.raises(ValidationError):
            await owner.cancel(invalid)  # type: ignore[arg-type]
        assert (await owner.cancel(" absent ")).status == CancellationStatus.NOT_FOUND
        assert owner.active_recovery_ids == ()

    run(exercise())


def test_one_loop_ownership_and_no_hidden_tasks() -> None:
    owner = MahoragaRecovery()

    async def first() -> None:
        loop = asyncio.get_running_loop()
        before = asyncio.all_tasks()
        with patch.object(
            loop, "create_task", side_effect=AssertionError("hidden task")
        ):
            await recover(owner, Responder())
            assert (
                await recover(owner, Responder(), attempts_used=3)
            ).status == RecoveryStatus.EXHAUSTED
        assert asyncio.all_tasks() == before

    async def second() -> None:
        with pytest.raises(MahoragaRecoveryError, match="another event loop"):
            await recover(owner, Responder())
        with pytest.raises(MahoragaRecoveryError, match="another event loop"):
            await owner.cancel("recovery")
        assert owner.active_recovery_ids == ()

    run(first())
    run(second())


def test_neutral_public_fields_and_import_call_boundaries() -> None:
    assert set(RecoveryRequest.model_fields) == {
        "recovery_id",
        "task_id",
        "step_id",
        "subject_agent_id",
        "trigger",
        "failure_summary",
        "attempts_used",
        "max_attempts",
        "max_replan_steps",
    }
    assert set(RecoveryDraft.model_fields) == {
        "recovery_id",
        "action",
        "rationale",
        *ACTION_VALUES,
    }
    assert set(RecoveryResult.model_fields) == {
        "recovery_id",
        "task_id",
        "step_id",
        "subject_agent_id",
        "trigger",
        "failure_summary",
        "recovery_agent_id",
        "status",
        "attempts_used_before",
        "max_attempts",
        "max_replan_steps",
        "attempt_number",
        "proposal",
    }
    allowed = {
        "asyncio",
        "dataclasses",
        "enum",
        "typing",
        "uuid",
        "pydantic",
        "phoenix.agents.registry",
        "phoenix.agents.task_graph",
        "phoenix.agents.handoff",
        "phoenix.agents.verifier",
    }
    forbidden = {
        "execute",
        "plan",
        "route",
        "verify",
        "generate",
        "stream",
        "acquire",
        "publish",
        "emit",
        "open",
        "create_task",
        "wait_for",
        "Popen",
        "start",
        "transition",
        "eval",
        "exec",
        "__import__",
        "dispatch",
        "retry",
        "build",
    }
    source = ast.parse(inspect.getsource(recovery))
    assert sum(isinstance(node, ast.Await) for node in ast.walk(source)) == 1
    assert not any(isinstance(node, ast.While) for node in ast.walk(source))
    for node in ast.walk(source):
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
            assert all(not alias.name.startswith("_") for alias in node.names)
            if node.module == "phoenix.agents.verifier":
                assert {alias.name for alias in node.names} == {
                    "VerificationResult",
                    "VerificationVerdict",
                }
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else "")
            )
            assert name not in forbidden


@pytest.mark.parametrize(
    "contract,field,invalid",
    [
        (RecoveryDraft, "action", b"retry_same"),
        (RecoveryResult, "status", b"proposed"),
        (CancellationResult, "status", b"accepted"),
        (RecoveryDraft, "action", "activate_kaido"),
        (RecoveryDraft, "action", "grant_permission"),
        (RecoveryDraft, "action", "mark_completed"),
    ],
)
def test_vocabulary_values_never_coerce_bytes_or_add_authority(
    contract: type[BaseModel], field: str, invalid: object
) -> None:
    data = (
        draft().model_dump()
        if contract is RecoveryDraft
        else (
            result().model_dump()
            if contract is RecoveryResult
            else {"recovery_id": "recovery", "status": "accepted"}
        )
    )
    with pytest.raises(ValidationError):
        contract.model_validate({**data, field: invalid})


def test_fresh_interpreter_import_construction_proposals_and_exhaustion_isolation(
    tmp_path: Path,
) -> None:
    script = """
import asyncio, _thread, importlib.abc, os, socket, sqlite3, subprocess, sys, threading
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected recovery runtime activity")
def audit(event, args):
    if event == "open" and ("never-dereference" in str(args[0]) or args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    )):
        forbidden()
    if event.startswith(("socket.", "sqlite3.", "subprocess.")):
        forbidden()
    if event in ("os.mkdir", "os.remove", "os.rename", "os.system"):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("phoenix.models", "phoenix.core", "phoenix.api", "phoenix.persistence",
                 "phoenix.tools", "phoenix.permissions", "phoenix.memory",
                 "phoenix.voice", "phoenix.recovery", "phoenix.agents.orchestrator",
                 "phoenix.agents.planner", "phoenix.agents.router",
                 "httpx", "psutil", "alembic")
        if any(fullname == root or fullname.startswith(root + ".") for root in roots):
            forbidden()
sys.dont_write_bytecode = True
sys.meta_path.insert(0, BlockImports())
from phoenix.agents.registry import AgentDefinition, AgentRegistry
from phoenix.agents.task_graph import TaskGraph, TaskStepDefinition, TaskStateMachine
from phoenix.agents.handoff import HandoffEnvelope, HandoffBuilder
from phoenix.agents.verifier import (
    VerificationCheck, VerificationResult, LeviVerifier,
    verification_requirement_satisfied
)
# Caller-owned event-loop resources are prepared before construction guards.
loop = asyncio.new_event_loop()
original_create_task = loop.create_task
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
TaskStateMachine.transition = forbidden
HandoffBuilder.build = forbidden
LeviVerifier.__init__ = forbidden
LeviVerifier.verify = forbidden
from phoenix.agents.recovery import (
    RecoveryRequest, RecoveryDraft, RecoveryResult, RecoveryTrigger,
    RecoveryAction, RecoveryStatus, MahoragaRecovery
)
graph = TaskGraph(task_id="task", steps=tuple(TaskStepDefinition(
    step_id=identity, owner_agent_id="build_engineer", objective="untrusted objective",
    input_refs=("file:///never-dereference",), expected_output="opaque description",
    timeout_seconds=0.000001, completion_rule="unexecuted criterion"
) for identity in ("step", "z")))
handoff = HandoffEnvelope(
    task_id="task", step_id="step", source_agent_id="phoenix",
    target_agent_id="build_engineer", objective="untrusted objective",
    input_refs=graph.steps[0].input_refs, timeout_seconds=0.000001,
    expected_output_schema="opaque schema", verification_required=True,
    permission_scope=("P3", "P4", "P5")
)
failure = "__import__('os').system('untrusted')"
request = RecoveryRequest(task_id="task", step_id="step",
    subject_agent_id="build_engineer", trigger="execution_failure",
    failure_summary=failure, attempts_used=0, max_attempts=1, max_replan_steps=2)
proposal = RecoveryDraft(recovery_id=request.recovery_id, action="retry_same",
                         rationale="Untrusted proposal description")
result = RecoveryResult(recovery_id=request.recovery_id, task_id="task", step_id="step",
    subject_agent_id="build_engineer", trigger="execution_failure",
    failure_summary=failure,
    attempts_used_before=0, max_attempts=1, max_replan_steps=2, attempt_number=1,
    status="proposed", proposal=proposal)
owner = MahoragaRecovery()
custom = AgentRegistry(agents=tuple(AgentDefinition(
    internal_id=identity, alias=None, professional_role="Metadata"
) for identity in ("adaptive_recovery", "phoenix", "build_engineer", "diagnostics")))
assert MahoragaRecovery(custom).recovery_agent_id == "adaptive_recovery"
assert all(value.model_dump_json() for value in (request, proposal, result))
evidence = VerificationResult(verification_id="verification", task_id="task",
    step_id="step",
    subject_agent_id="build_engineer", verdict="failed", checks=(VerificationCheck(
        check_id="check", criterion="untrusted", passed=False,
        evidence_summary="Original failed observation"),))
machine = TaskStateMachine("task")
before = (graph.model_dump_json(), handoff.model_dump_json(),
          evidence.model_dump_json(),
          machine.state, machine.history)
assert not verification_requirement_satisfied(handoff, evidence)
class Responder:
    calls = 0
    def __init__(self, action, **fields):
        self.action, self.fields = action, fields
    async def propose(self, request):
        self.calls += 1
        return RecoveryDraft(recovery_id=request.recovery_id, action=self.action,
            rationale="Untrusted strategy description", **self.fields)
class ForbiddenResponder:
    async def propose(self, request):
        forbidden()
async def exercise():
    proposals = (
        ("retry_same", {}),
        ("adjust_parameters", {"parameter_adjustments": (failure,)}),
        ("switch_tool", {"replacement_tool_id": "unprobed-tool"}),
        ("switch_model_provider", {"replacement_provider_id": "unprobed-provider",
                                   "replacement_model_id": "unprobed-model"}),
        ("switch_agent", {"replacement_agent_id": "diagnostics"}),
        ("replan_subgraph", {"replan_step_ids": ("z", "step")}),
    )
    for action, fields in proposals:
        responder = Responder(action, **fields)
        value = await owner.recover(graph=graph, handoff=handoff,
            trigger=RecoveryTrigger.EXECUTION_FAILURE, failure_summary=failure,
            attempts_used=0, max_attempts=1, max_replan_steps=2,
            responder=responder, recovery_id=request.recovery_id)
        assert value.status == RecoveryStatus.PROPOSED and responder.calls == 1
        assert value.proposal.action.value == action and value.attempt_number == 1
        assert value.failure_summary == failure and owner.active_recovery_ids == ()
    responder = Responder("retry_same")
    value = await owner.recover(graph=graph, handoff=handoff,
        trigger=RecoveryTrigger.VERIFICATION_FAILURE, verification_result=evidence,
        failure_summary=failure, attempts_used=0, max_attempts=1, max_replan_steps=2,
        responder=responder)
    assert value.trigger == RecoveryTrigger.VERIFICATION_FAILURE
    assert responder.calls == 1
    for used in (1, 2):
        value = await owner.recover(graph=graph, handoff=handoff,
            trigger=RecoveryTrigger.VERIFICATION_FAILURE, verification_result=evidence,
            failure_summary=failure, attempts_used=used, max_attempts=1,
            max_replan_steps=2,
            responder=ForbiddenResponder())
        assert value.status == RecoveryStatus.EXHAUSTED and value.proposal is None
        assert value.attempt_number is None and value.attempts_used_before == used
        assert owner.active_recovery_ids == ()
    assert not verification_requirement_satisfied(handoff, evidence)
# Exactly one harness task; production code cannot create hidden work.
task = original_create_task(exercise())
loop.run_until_complete(task)
loop.close()
assert (graph.model_dump_json(), handoff.model_dump_json(), evidence.model_dump_json(),
        machine.state, machine.history) == before
assert not list(Path.cwd().iterdir())
print("Recovery isolation: PASS")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert completed.stdout.strip() == "Recovery isolation: PASS"
    assert list(tmp_path.iterdir()) == []


def test_caller_carries_attempt_evidence_forward_to_exhaustion() -> None:
    async def exercise() -> None:
        owner, responder = MahoragaRecovery(), Responder()
        first = await recover(owner, responder, attempts_used=0, max_attempts=2)
        assert first.attempt_number == 1
        second = await recover(
            owner, responder, attempts_used=first.attempt_number, max_attempts=2
        )
        assert second.attempt_number == 2
        exhausted = await recover(
            owner, responder, attempts_used=second.attempt_number, max_attempts=2
        )
        assert exhausted.status == RecoveryStatus.EXHAUSTED
        assert exhausted.attempt_number is None and exhausted.proposal is None
        assert exhausted.attempts_used_before == exhausted.max_attempts == 2
        assert [entry.attempts_used for entry in responder.requests] == [0, 1]
        assert owner.active_recovery_ids == ()

    run(exercise())


@pytest.mark.parametrize("field", ["attempts_used", "max_attempts", "max_replan_steps"])
@pytest.mark.parametrize("invalid", [True, False, "3", 3.0])
def test_runtime_budget_types_fail_before_responder(
    field: str, invalid: object
) -> None:
    async def exercise() -> None:
        owner, responder = MahoragaRecovery(), Responder()
        with pytest.raises(ValidationError):
            await recover(owner, responder, **{field: invalid})
        assert responder.requests == [] and owner.active_recovery_ids == ()

    run(exercise())
