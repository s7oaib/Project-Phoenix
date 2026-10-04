"""PHX-026 correlation, immutable evidence, INV-008 and scoped lifetimes."""

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

from phoenix.agents import verifier
from phoenix.agents.handoff import HandoffBuilder, HandoffEnvelope
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
    CancellationResult,
    CancellationStatus,
    DuplicateVerificationError,
    LeviVerificationError,
    LeviVerifier,
    VerificationCheck,
    VerificationDraft,
    VerificationRequest,
    VerificationResult,
    VerificationVerdict,
    verification_requirement_satisfied,
)

BAD_TEXT = (None, True, 1, 1.5, b"text", object(), "", " \t\n")


def request(**changes: object) -> VerificationRequest:
    return VerificationRequest.model_validate(
        {
            "verification_id": "verification",
            "task_id": "task",
            "step_id": "step",
            "subject_agent_id": "build_engineer",
            "objective": "  Objective\n",
            "expected_output_schema": "  opaque contract/v1\n",
            "completion_rule": "  explicit completion criterion\n",
            "candidate_output": "  untrusted candidate\n",
            **changes,
        }
    )


def check(
    identity: str = "z", passed: bool = True, /, **changes: object
) -> VerificationCheck:
    return VerificationCheck.model_validate(
        {
            "check_id": identity,
            "criterion": "  Describe evidence\n",
            "passed": passed,
            "evidence_summary": "  Supplied evidence\n",
            **changes,
        }
    )


def checks() -> tuple[VerificationCheck, ...]:
    return (check(), check("a"))


def draft(**changes: object) -> VerificationDraft:
    return VerificationDraft.model_validate(
        {
            "verification_id": "verification",
            "checks": checks(),
            **changes,
        }
    )


def result(**changes: object) -> VerificationResult:
    return VerificationResult.model_validate(
        {
            "verification_id": "verification",
            "task_id": "task",
            "step_id": "step",
            "subject_agent_id": "build_engineer",
            "verdict": "passed",
            "checks": checks(),
            **changes,
        }
    )


def graph() -> TaskGraph:
    return TaskGraph(
        task_id="task",
        steps=(
            TaskStepDefinition(
                step_id="step",
                owner_agent_id="build_engineer",
                objective="  Objective\n",
                input_refs=("file:///never-dereference", "memory:unread"),
                expected_output="graph description",
                timeout_seconds=0.000001,
                completion_rule="  explicit completion criterion\n",
            ),
        ),
    )


def handoff(required: bool = True) -> HandoffEnvelope:
    return HandoffBuilder().build(
        graph=graph(),
        step_id="step",
        source_agent_id="phoenix",
        expected_output_schema="  opaque contract/v1\n",
        verification_required=required,
        permission_scope=("P3:edit", "P4:upload", "P5:blocked"),
    )


class Responder:
    def __init__(self, evidence: tuple[VerificationCheck, ...] | None = None) -> None:
        self.requests: list[VerificationRequest] = []
        self.evidence = checks() if evidence is None else evidence

    async def verify(self, value: VerificationRequest) -> VerificationDraft:
        self.requests.append(value)
        return VerificationDraft(
            verification_id=value.verification_id, checks=self.evidence
        )


class SuspendedResponder(Responder):
    def __init__(self, terminal: str = "passed", suppress: bool = False) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.terminal = terminal
        self.suppress = suppress
        self.cancelled = False

    async def verify(self, value: VerificationRequest) -> VerificationDraft:
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
            return VerificationDraft.model_construct(
                verification_id=value.verification_id, checks=()
            )
        return VerificationDraft(
            verification_id="wrong"
            if self.terminal == "mismatch"
            else value.verification_id,
            checks=(check(passed=self.terminal != "failed"),),
        )


async def verify(
    owner: LeviVerifier,
    responder: Responder,
    identity: str = "verification",
    /,
    **changes: object,
) -> VerificationResult:
    arguments: dict[str, object] = {
        "graph": graph(),
        "handoff": handoff(),
        "candidate_output": "  untrusted candidate\n",
        "verification_id": identity,
        "responder": responder,
        **changes,
    }
    return await owner.verify(**arguments)  # type: ignore[arg-type]


def run(exercise: Coroutine[object, object, None]) -> None:
    asyncio.run(asyncio.wait_for(exercise, timeout=5))


@pytest.mark.parametrize("alias", [None, "renamed", "LEVI"])
def test_verifier_authority_and_behavior_ignore_aliases(alias: str | None) -> None:
    registry = AgentRegistry(
        agents=tuple(
            AgentDefinition(
                internal_id=item.internal_id, alias=alias, professional_role="Metadata"
            )
            for item in build_default_agent_registry().agents
        )
    )
    owner = LeviVerifier(registry)
    assert owner.verifier_id == "verifier" and owner.active_verification_ids == ()

    async def exercise() -> None:
        assert await verify(owner, Responder()) == result()

    run(exercise())


@pytest.mark.parametrize("identity", ["LEVI", "phoenix", "other"])
def test_alias_role_or_other_identity_cannot_replace_verifier(identity: str) -> None:
    registry = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id=identity,
                alias="LEVI",
                professional_role="Verification/QA",
            ),
        )
    )
    with pytest.raises(LeviVerificationError, match="Verifier internal ID is absent"):
        LeviVerifier(registry)
    with pytest.raises(LeviVerificationError):
        LeviVerifier(AgentRegistry())


@pytest.mark.parametrize("field", list(VerificationRequest.model_fields))
@pytest.mark.parametrize("invalid", BAD_TEXT)
def test_request_strict_ids_and_meaningful_text(field: str, invalid: object) -> None:
    with pytest.raises(ValidationError):
        request(**{field: invalid})


@pytest.mark.parametrize("field", ["check_id", "criterion", "evidence_summary"])
@pytest.mark.parametrize("invalid", BAD_TEXT)
def test_check_strict_identifier_and_meaningful_evidence(
    field: str, invalid: object
) -> None:
    with pytest.raises(ValidationError):
        check(**{field: invalid})


@pytest.mark.parametrize("invalid", [None, 0, 1, "true", "false", b"true", object()])
def test_check_passed_is_strict_boolean(invalid: object) -> None:
    with pytest.raises(ValidationError):
        VerificationCheck.model_validate({**check().model_dump(), "passed": invalid})


@pytest.mark.parametrize(
    "field", ["verification_id", "task_id", "step_id", "subject_agent_id"]
)
@pytest.mark.parametrize("invalid", BAD_TEXT)
def test_result_strict_identifiers(field: str, invalid: object) -> None:
    with pytest.raises(ValidationError):
        result(**{field: invalid})


@pytest.mark.parametrize("invalid", [None, True, "LEVI", "phoenix", " verifier "])
def test_result_verifier_id_literal(invalid: object) -> None:
    with pytest.raises(ValidationError):
        result(verifier_id=invalid)


def test_generated_normalized_ids_original_text_and_verdict_vocabulary() -> None:
    data = request().model_dump()
    del data["verification_id"]
    a, b = (
        VerificationRequest.model_validate(data),
        VerificationRequest.model_validate(data),
    )
    assert a.verification_id != b.verification_id
    assert UUID(a.verification_id).version == UUID(b.verification_id).version == 4
    value = request(
        verification_id=" ID ",
        task_id=" Task ",
        step_id=" Step ",
        subject_agent_id=" Subject ",
    )
    assert (
        value.verification_id,
        value.task_id,
        value.step_id,
        value.subject_agent_id,
    ) == ("ID", "Task", "Step", "Subject")
    assert (
        value.objective == "  Objective\n"
        and value.candidate_output == "  untrusted candidate\n"
    )
    assert check(" Check ").check_id == "Check"
    assert check().criterion == "  Describe evidence\n"
    assert [item.value for item in VerificationVerdict] == ["passed", "failed"]
    assert [item.value for item in CancellationStatus] == ["accepted", "not_found"]


@pytest.mark.parametrize(
    "value",
    [
        request(),
        check(),
        draft(),
        result(),
        CancellationResult(verification_id="id", status=CancellationStatus.NOT_FOUND),
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
    for name in ("metadata", "permission_grant", "callback", "completed", "repair"):
        with pytest.raises(ValidationError, match="Extra inputs"):
            type(value).model_validate({**value.model_dump(), name: object()})
        with pytest.raises(ValidationError, match="Extra inputs"):
            type(value).model_validate(value.model_copy(update={name: object()}))


@pytest.mark.parametrize(
    "invalid", [(), [], None, {"unordered"}, "checks", (object(),)]
)
def test_checks_nonempty_and_ordered(invalid: object) -> None:
    for model in (VerificationDraft, VerificationResult):
        data = (
            draft().model_dump()
            if model is VerificationDraft
            else result().model_dump()
        )
        with pytest.raises(ValidationError):
            model.model_validate({**data, "checks": invalid})


def test_check_order_duplicate_normalization_and_nested_snapshots() -> None:
    values = [check().model_dump(), check("a").model_dump()]
    value = draft(checks=values)
    values[0]["passed"] = False
    values.clear()
    assert tuple(item.check_id for item in value.checks) == ("z", "a")
    assert all(item.passed for item in value.checks)
    with pytest.raises(ValidationError, match="Duplicate verification check"):
        draft(checks=(check("id"), check(" id\n")))
    mutable = [check(), check("a")]
    forged = VerificationDraft.model_construct(
        verification_id="verification", checks=mutable
    )
    validated = VerificationDraft.model_validate(forged)
    mutable.clear()
    assert validated.checks == checks()


@pytest.mark.parametrize("outcomes", list(product((True, False), repeat=3)))
def test_all_true_only_passes_and_false_verification_never_retries(
    outcomes: tuple[bool, ...],
) -> None:
    async def exercise() -> None:
        owner = LeviVerifier()
        evidence = tuple(
            check(str(index), passed) for index, passed in enumerate(outcomes)
        )
        responder = Responder(evidence)
        value = await verify(owner, responder)
        expected = (
            VerificationVerdict.PASSED if all(outcomes) else VerificationVerdict.FAILED
        )
        assert value.verdict == expected and value.checks == evidence
        assert len(responder.requests) == 1 and owner.active_verification_ids == ()
        assert verification_requirement_satisfied(handoff(), value) is all(outcomes)

    run(exercise())


@pytest.mark.parametrize("verdict,passed", [("passed", False), ("failed", True)])
def test_contradictory_results_and_forged_gate_evidence_rejected(
    verdict: str, passed: bool
) -> None:
    with pytest.raises(ValidationError, match="contradicts"):
        result(verdict=verdict, checks=(check(passed=passed),))
    forged = result().model_copy(
        update={"verdict": verdict, "checks": (check(passed=passed),)}
    )
    with pytest.raises(ValidationError, match="contradicts"):
        verification_requirement_satisfied(handoff(), forged)


@pytest.mark.parametrize("required", [True, False])
def test_requirement_gate_missing_passed_and_failed_evidence(required: bool) -> None:
    value = handoff(required)
    assert verification_requirement_satisfied(value, None) is (not required)
    assert verification_requirement_satisfied(value, result()) is True
    failed = result(verdict="failed", checks=(check(passed=False),))
    assert verification_requirement_satisfied(value, failed) is (not required)


@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize("field", ["task_id", "step_id", "subject_agent_id"])
def test_gate_rejects_unrelated_evidence_even_without_requirement(
    required: bool, field: str
) -> None:
    with pytest.raises(LeviVerificationError, match="correlation mismatch"):
        verification_requirement_satisfied(
            handoff(required), result(**{field: "unrelated"})
        )


def test_exact_derived_request_result_and_readonly_graph_handoff_state(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def exercise() -> None:
        owner, responder, parent, transfer = (
            LeviVerifier(),
            Responder(),
            graph(),
            handoff(),
        )
        machine = TaskStateMachine(parent.task_id)
        for target in (TaskState.PLANNING, TaskState.READY, TaskState.RUNNING):
            machine.transition(target)
        before = (
            parent.model_dump_json(),
            transfer.model_dump_json(),
            machine.state,
            machine.history,
        )
        with patch.object(
            TaskStateMachine, "transition", side_effect=AssertionError("state mutation")
        ):
            value = await verify(
                owner, responder, " Verification ", graph=parent, handoff=transfer
            )
        assert responder.requests == [request(verification_id="Verification")]
        assert value == result(verification_id="Verification")
        assert (
            parent.model_dump_json(),
            transfer.model_dump_json(),
            machine.state,
            machine.history,
        ) == before
        assert owner.active_verification_ids == () and len(responder.requests) == 1

    run(exercise())
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "field,value",
    [
        ("task_id", "other"),
        ("step_id", "absent"),
        ("target_agent_id", "voice"),
        ("objective", "different"),
        ("input_refs", ("different",)),
        ("timeout_seconds", 1),
    ],
)
def test_graph_handoff_mismatch_fails_before_responder(
    field: str, value: object
) -> None:
    async def exercise() -> None:
        owner, responder = LeviVerifier(), Responder()
        transfer = handoff().model_copy(update={field: value})
        with pytest.raises(LeviVerificationError):
            await verify(owner, responder, handoff=transfer)
        assert responder.requests == [] and owner.active_verification_ids == ()

    run(exercise())


@pytest.mark.parametrize("source", ["unknown", "AIZEN"])
def test_unknown_or_alias_handoff_source_fails_before_responder(source: str) -> None:
    async def exercise() -> None:
        owner, responder = LeviVerifier(), Responder()
        transfer = handoff().model_copy(update={"source_agent_id": source})
        with pytest.raises(LeviVerificationError, match="Handoff agent"):
            await verify(owner, responder, handoff=transfer)
        assert responder.requests == [] and owner.active_verification_ids == ()

    run(exercise())


@pytest.mark.parametrize(
    "field,invalid",
    [
        (field, invalid)
        for field in ("verification_id", "candidate_output")
        for invalid in BAD_TEXT
        if not (field == "verification_id" and invalid is None)
    ],
)
def test_invalid_entry_values_fail_before_registration(
    field: str, invalid: object
) -> None:
    async def exercise() -> None:
        owner, responder = LeviVerifier(), Responder()
        with pytest.raises(ValidationError):
            await verify(owner, responder, **{field: invalid})
        assert owner.active_verification_ids == () and responder.requests == []

    run(exercise())


def test_runtime_default_ids_and_no_derived_override_api() -> None:
    async def exercise() -> None:
        owner, responder = LeviVerifier(), Responder()
        a = await verify(owner, responder, verification_id=None)
        b = await verify(owner, responder, verification_id=None)
        assert a.verification_id != b.verification_id
        assert UUID(a.verification_id).version == 4

    run(exercise())
    assert set(inspect.signature(LeviVerifier.verify).parameters) == {
        "self",
        "graph",
        "handoff",
        "candidate_output",
        "responder",
        "verification_id",
    }


def test_custom_registry_snapshot_subject_identity_and_alias_rejection() -> None:
    registry = AgentRegistry(
        agents=tuple(
            AgentDefinition(
                internal_id=identity, alias="LEVI", professional_role="Role"
            )
            for identity in ("verifier", "custom", "phoenix")
        )
    )
    node = graph().steps[0].model_copy(update={"owner_agent_id": "custom"})
    parent = TaskGraph.model_validate(
        {"task_id": "task", "steps": (node,)}, context={"agent_registry": registry}
    )
    transfer = HandoffBuilder(registry).build(
        graph=parent,
        step_id="step",
        source_agent_id="phoenix",
        expected_output_schema="schema",
        verification_required=True,
    )
    mutable = list(registry.agents)
    owner = LeviVerifier(registry.model_copy(update={"agents": mutable}))
    mutable.clear()

    async def exercise() -> None:
        value = await verify(owner, Responder(), graph=parent, handoff=transfer)
        assert value.subject_agent_id == "custom"
        missing = LeviVerifier(AgentRegistry(agents=(registry.require("verifier"),)))
        with pytest.raises(ValidationError, match="Unknown step owner"):
            await verify(missing, Responder(), graph=parent, handoff=transfer)
        forged = parent.model_copy(
            update={"steps": (node.model_copy(update={"owner_agent_id": "LEVI"}),)}
        )
        with pytest.raises(ValidationError, match="Unknown step owner"):
            await verify(owner, Responder(), graph=forged, handoff=transfer)

    run(exercise())


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        "raw",
        object(),
        result(),
        draft(verification_id="wrong"),
        draft().model_copy(update={"checks": ()}),
        draft().model_copy(update={"checks": (object(),)}),
        draft().model_copy(update={"checks": (check("same"), check("same"))}),
        draft().model_copy(
            update={"checks": (check().model_copy(update={"passed": 1}),)}
        ),
        draft().model_copy(
            update={"checks": (check().model_copy(update={"evidence_summary": " "}),)}
        ),
        draft().model_copy(update={"metadata": {}}),
        draft().model_copy(
            update={"checks": (check().model_copy(update={"permission": "grant"}),)}
        ),
    ],
)
def test_malformed_draft_fails_cleans_and_allows_id_reuse(invalid: object) -> None:
    class InvalidResponder(Responder):
        async def verify(self, value: VerificationRequest) -> VerificationDraft:
            return invalid  # type: ignore[return-value]

    async def exercise() -> None:
        owner = LeviVerifier()
        with pytest.raises(LeviVerificationError):
            await verify(owner, InvalidResponder())
        assert owner.active_verification_ids == ()
        assert (await verify(owner, Responder())).verdict == VerificationVerdict.PASSED

    run(exercise())


@pytest.mark.parametrize(
    "extension",
    ["draft", "check", "graph", "step", "handoff", "registry", "definition", "result"],
)
def test_extended_contracts_cannot_smuggle_authority(extension: str) -> None:
    classes: dict[str, type[BaseModel]] = {
        "draft": VerificationDraft,
        "check": VerificationCheck,
        "graph": TaskGraph,
        "step": TaskStepDefinition,
        "handoff": HandoffEnvelope,
        "registry": AgentRegistry,
        "definition": AgentDefinition,
        "result": VerificationResult,
    }
    sources: dict[str, BaseModel] = {
        "draft": draft(),
        "check": check(),
        "graph": graph(),
        "step": graph().steps[0],
        "handoff": handoff(),
        "registry": build_default_agent_registry(),
        "definition": build_default_agent_registry().require("verifier"),
        "result": result(),
    }
    extended: type[BaseModel] = type(
        "Extended", (classes[extension],), {"__annotations__": {"grant": str}}
    )
    value = extended.model_validate(
        {**sources[extension].model_dump(), "grant": "untrusted"}
    )
    if extension in {"registry", "definition"}:
        supplied = (
            value
            if extension == "registry"
            else AgentRegistry.model_construct(agents=(value,))
        )
        with pytest.raises((LeviVerificationError, ValidationError)):
            LeviVerifier(supplied)  # type: ignore[arg-type]
    elif extension == "result":
        with pytest.raises(LeviVerificationError):
            verification_requirement_satisfied(handoff(), value)  # type: ignore[arg-type]
    else:

        class ExtendedResponder(Responder):
            async def verify(self, request: VerificationRequest) -> VerificationDraft:
                if extension == "draft":
                    return value  # type: ignore[return-value]
                return VerificationDraft.model_construct(
                    verification_id=request.verification_id, checks=(value,)
                )

        async def exercise() -> None:
            owner, responder = LeviVerifier(), ExtendedResponder()
            changes: dict[str, object] = {}
            if extension in {"graph", "handoff"}:
                changes[extension] = value
            elif extension == "step":
                changes["graph"] = graph().model_copy(update={"steps": (value,)})
            with pytest.raises((LeviVerificationError, ValidationError)):
                await verify(owner, responder, **changes)
            assert owner.active_verification_ids == ()

        run(exercise())


def test_copied_or_constructed_boundary_values_are_revalidated() -> None:
    async def exercise() -> None:
        owner, responder = LeviVerifier(), Responder()
        invalids = (
            {"graph": graph().model_copy(update={"grant": "untrusted"})},
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
            {
                "graph": graph().model_copy(
                    update={
                        "steps": (
                            graph().steps[0].model_copy(update={"grant": object()}),
                        )
                    }
                )
            },
            {"handoff": handoff().model_copy(update={"verification_required": 1})},
            {"handoff": handoff().model_copy(update={"grant": object()})},
            {"graph": object()},
            {"handoff": object()},
        )
        for invalid in invalids:
            with pytest.raises((LeviVerificationError, ValidationError)):
                await verify(owner, responder, **invalid)
        assert owner.active_verification_ids == () and responder.requests == []

    run(exercise())
    registry = build_default_agent_registry()
    for invalid in (
        registry.model_copy(update={"grant": object()}),
        registry.model_copy(
            update={
                "agents": (
                    registry.require("verifier").model_copy(update={"grant": object()}),
                )
            }
        ),
    ):
        with pytest.raises(ValidationError):
            LeviVerifier(invalid)
    mutable = [check()]
    forged = result().model_copy(update={"checks": mutable})
    assert verification_requirement_satisfied(handoff(), forged)
    assert vars(forged)["checks"] is mutable


@pytest.mark.parametrize(
    "terminal",
    [
        "passed",
        "failed",
        "exception",
        "mismatch",
        "malformed",
        "caller_cancel",
        "cancel",
    ],
)
def test_concurrent_verifications_isolation_cleanup_and_id_reuse(terminal: str) -> None:
    async def exercise() -> None:
        owner = LeviVerifier()
        a, b = SuspendedResponder(terminal), SuspendedResponder()
        tb = asyncio.create_task(verify(owner, b, "b"))
        ta = asyncio.create_task(verify(owner, a, "a"))
        try:
            await a.entered.wait()
            await b.entered.wait()
            snapshot = owner.active_verification_ids
            assert snapshot == ("a", "b")
            with pytest.raises(AttributeError):
                owner.active_verification_ids = ()  # type: ignore[misc]
            if terminal == "cancel":
                ack = await owner.cancel(" a ")
                assert (
                    ack.status == CancellationStatus.ACCEPTED
                    and ack.verification_id == "a"
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
            elif terminal in {"mismatch", "malformed"}:
                with pytest.raises(LeviVerificationError):
                    await ta
            else:
                assert (await ta).verdict.value == terminal
            assert snapshot == ("a", "b") and owner.active_verification_ids == ("b",)
            assert not tb.done() and not b.cancelled
            assert (await owner.cancel("a")).status == CancellationStatus.NOT_FOUND
            assert (await verify(owner, Responder(), "a")).verification_id == "a"
            assert owner.active_verification_ids == ("b",)
            b.release.set()
            await tb
            assert owner.active_verification_ids == ()
        finally:
            ta.cancel()
            tb.cancel()
            await asyncio.gather(ta, tb, return_exceptions=True)

    run(exercise())


def test_duplicate_does_not_overwrite_cancel_or_merge_original() -> None:
    async def exercise() -> None:
        owner, responder, duplicate = LeviVerifier(), SuspendedResponder(), Responder()
        task = asyncio.create_task(verify(owner, responder))
        try:
            await responder.entered.wait()
            with pytest.raises(DuplicateVerificationError):
                await verify(owner, duplicate, " verification ")
            assert (
                owner.active_verification_ids == ("verification",)
                and duplicate.requests == []
            )
            assert not task.done() and not responder.cancelled
            responder.release.set()
            await task
            await verify(owner, duplicate)
            assert len(duplicate.requests) == 1 and owner.active_verification_ids == ()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    run(exercise())


@pytest.mark.parametrize("source", ["caller", "verifier"])
def test_suppressed_cancellation_cannot_finalize_passed(source: str) -> None:
    async def exercise() -> None:
        owner, responder = LeviVerifier(), SuspendedResponder(suppress=True)
        task = asyncio.create_task(verify(owner, responder))
        try:
            await responder.entered.wait()
            if source == "caller":
                task.cancel()
            else:
                assert (
                    await owner.cancel("verification")
                ).status == CancellationStatus.ACCEPTED
            with pytest.raises(asyncio.CancelledError):
                await task
            assert responder.cancelled and owner.active_verification_ids == ()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    run(exercise())


@pytest.mark.parametrize("clear_counter", [True, False])
def test_recorded_local_cancellation_cannot_be_cleared_or_skipped(
    clear_counter: bool,
) -> None:
    owner = LeviVerifier()

    class SelfCancellingResponder(Responder):
        async def verify(self, value: VerificationRequest) -> VerificationDraft:
            await owner.cancel(value.verification_id)
            if clear_counter:
                try:
                    await asyncio.sleep(0)  # Deliberate delivery checkpoint.
                except asyncio.CancelledError:
                    task = asyncio.current_task()
                    assert task is not None
                    task.uncancel()
                    assert task.cancelling() == 0
            return draft(verification_id=value.verification_id)

    async def exercise() -> None:
        task = asyncio.create_task(verify(owner, SelfCancellingResponder()))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owner.active_verification_ids == ()

    run(exercise())


def test_nested_verifications_cannot_share_cancellation_ownership() -> None:
    owner = LeviVerifier()

    class NestedResponder(Responder):
        async def verify(self, value: VerificationRequest) -> VerificationDraft:
            with pytest.raises(LeviVerificationError, match="already owns"):
                await verify(owner, Responder(), "nested")
            assert owner.active_verification_ids == ("verification",)
            assert (await owner.cancel("nested")).status == CancellationStatus.NOT_FOUND
            return draft()

    async def exercise() -> None:
        await verify(owner, NestedResponder())
        assert owner.active_verification_ids == ()

    run(exercise())


@pytest.mark.parametrize("invalid", BAD_TEXT)
def test_cancel_strict_id_validation_and_unknown_outcome(invalid: object) -> None:
    async def exercise() -> None:
        owner = LeviVerifier()
        with pytest.raises(ValidationError):
            await owner.cancel(invalid)  # type: ignore[arg-type]
        assert (await owner.cancel(" absent ")).status == CancellationStatus.NOT_FOUND
        assert owner.active_verification_ids == ()

    run(exercise())


def test_one_event_loop_ownership() -> None:
    owner = LeviVerifier()

    async def first() -> None:
        await verify(owner, Responder())

    async def second() -> None:
        with pytest.raises(LeviVerificationError, match="another event loop"):
            await verify(owner, Responder())
        with pytest.raises(LeviVerificationError, match="another event loop"):
            await owner.cancel("verification")
        assert owner.active_verification_ids == ()

    run(first())
    run(second())


def test_explicit_verification_creates_no_hidden_task() -> None:
    async def exercise() -> None:
        owner, responder = LeviVerifier(), Responder()
        loop = asyncio.get_running_loop()
        before = asyncio.all_tasks()
        with patch.object(
            loop, "create_task", side_effect=AssertionError("hidden task")
        ):
            assert (
                await verify(owner, responder)
            ).verdict == VerificationVerdict.PASSED
            assert verification_requirement_satisfied(handoff(), result())
        assert asyncio.all_tasks() == before

    run(exercise())


def test_public_fields_imports_and_calls_preserve_verification_only_scope() -> None:
    assert set(VerificationRequest.model_fields) == {
        "verification_id",
        "task_id",
        "step_id",
        "subject_agent_id",
        "objective",
        "expected_output_schema",
        "completion_rule",
        "candidate_output",
    }
    assert set(VerificationCheck.model_fields) == {
        "check_id",
        "criterion",
        "passed",
        "evidence_summary",
    }
    assert set(VerificationDraft.model_fields) == {"verification_id", "checks"}
    assert set(VerificationResult.model_fields) == {
        "verification_id",
        "task_id",
        "step_id",
        "subject_agent_id",
        "verifier_id",
        "verdict",
        "checks",
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
    }
    forbidden = {
        "execute",
        "plan",
        "route",
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
        "repair",
        "retry",
        "build",
    }
    for node in ast.walk(ast.parse(inspect.getsource(verifier))):
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else "")
            )
            assert name not in forbidden
    assert not inspect.iscoroutinefunction(verification_requirement_satisfied)


def test_fresh_interpreter_import_construction_gate_and_explicit_verification_isolation(
    tmp_path: Path,
) -> None:
    script = """
import asyncio, _thread, importlib.abc, os, socket, sqlite3, subprocess, sys, threading
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected verification runtime activity")
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
from phoenix.agents.task_graph import TaskGraph, TaskStepDefinition, TaskStateMachine
from phoenix.agents.handoff import HandoffEnvelope
from phoenix.agents.registry import AgentDefinition, AgentRegistry
# Prepare caller-owned loop resources before blocking construction activity.
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
from phoenix.agents.verifier import (
    VerificationRequest, VerificationCheck, VerificationDraft, VerificationResult,
    VerificationVerdict, LeviVerifier, verification_requirement_satisfied
)
graph = TaskGraph(task_id="task", steps=(TaskStepDefinition(
    step_id="step", owner_agent_id="build_engineer", objective="description",
    input_refs=("file:///never-dereference",), expected_output="opaque",
    timeout_seconds=0.000001,
    completion_rule="unexecuted rule"
),))
handoff = HandoffEnvelope(
    task_id="task", step_id="step", source_agent_id="phoenix",
    target_agent_id="build_engineer", objective="description",
    input_refs=graph.steps[0].input_refs, timeout_seconds=0.000001,
    expected_output_schema="__import__('os').system('untrusted')",
    verification_required=True,
    permission_scope=("P3", "P4", "P5")
)
check = VerificationCheck(check_id="check", criterion="untrusted", passed=True,
                          evidence_summary="supplied observation")
request = VerificationRequest(task_id="task", step_id="step",
    subject_agent_id="build_engineer",
    objective="description", expected_output_schema=handoff.expected_output_schema,
    completion_rule="unexecuted rule", candidate_output="untrusted command text")
draft = VerificationDraft(verification_id=request.verification_id, checks=(check,))
result = VerificationResult(verification_id=request.verification_id,
    task_id="task", step_id="step",
    subject_agent_id="build_engineer", verdict="passed", checks=draft.checks)
owner = LeviVerifier()
custom = AgentRegistry(agents=tuple(AgentDefinition(
    internal_id=identity, alias=None, professional_role="Metadata"
) for identity in ("verifier", "build_engineer", "phoenix")))
assert LeviVerifier(custom).verifier_id == "verifier"
assert verification_requirement_satisfied(handoff, result)
assert not verification_requirement_satisfied(handoff, None)
assert all(value.model_dump_json() for value in (request, check, draft, result))
machine = TaskStateMachine("task")
before = (graph.model_dump_json(), handoff.model_dump_json(),
          machine.state, machine.history)
class Responder:
    calls = 0
    async def verify(self, request):
        self.calls += 1
        return VerificationDraft(verification_id=request.verification_id,
                                 checks=(check,))
responder = Responder()
async def exercise():
    value = await owner.verify(graph=graph, handoff=handoff,
        candidate_output=request.candidate_output, responder=responder,
        verification_id=request.verification_id)
    assert value == result and responder.calls == 1
    assert owner.active_verification_ids == ()
# Exactly one test-harness task; explicit production verify cannot create any.
task = original_create_task(exercise())
loop.run_until_complete(task)
loop.close()
assert (graph.model_dump_json(), handoff.model_dump_json(),
        machine.state, machine.history) == before
assert not list(Path.cwd().iterdir())
print("Verification isolation: PASS")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert completed.stdout.strip() == "Verification isolation: PASS"
    assert list(tmp_path.iterdir()) == []
