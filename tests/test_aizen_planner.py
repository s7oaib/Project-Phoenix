"""PHX-022 subordinate planning, exact contracts and isolated request lifetimes."""

import ast
import asyncio
import inspect
import subprocess
import sys
from collections.abc import Awaitable
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from phoenix.agents import planner
from phoenix.agents.orchestrator import (
    OrchestrationPath,
    PhoenixOrchestrator,
    PhoenixRequest,
    PhoenixResult,
    PhoenixResultKind,
)
from phoenix.agents.planner import (
    AizenPlan,
    AizenPlanner,
    AizenPlanningError,
    CancellationResult,
    CancellationStatus,
    DuplicatePlanningRequestError,
    PlanDraft,
    PlanningRequest,
    PlanStep,
)
from phoenix.agents.registry import (
    AgentDefinition,
    AgentRegistry,
    UnknownAgentError,
    build_default_agent_registry,
)


def run(coroutine: Awaitable[None]) -> None:
    async def bounded() -> None:
        await asyncio.wait_for(coroutine, 3)

    asyncio.run(bounded())


def request(identity: str = "request") -> PlanningRequest:
    return PlanningRequest(request_id=identity, objective="  objective\ncontent  ")


def steps() -> tuple[PlanStep, ...]:
    # Order deliberately differs from alphabetical IDs; it is not a scheduler.
    return (
        PlanStep(step_id="z", objective="  inspect requirements\n"),
        PlanStep(step_id="a", objective="Describe an approach"),
    )


class Responder:
    def __init__(self) -> None:
        self.requests: list[PlanningRequest] = []

    async def propose(self, value: PlanningRequest) -> PlanDraft:
        self.requests.append(value)
        return PlanDraft(request_id=value.request_id, steps=steps())


class SuspendedResponder(Responder):
    def __init__(
        self, *, terminal: str = "success", suppress_cancel: bool = False
    ) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.terminal = terminal
        self.suppress_cancel = suppress_cancel
        self.cancelled = False

    async def propose(self, value: PlanningRequest) -> PlanDraft:
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            if not self.suppress_cancel:
                raise
        if self.terminal == "failure":
            raise RuntimeError("fixture failure")
        if self.terminal == "mismatch":
            return PlanDraft(request_id="wrong", steps=steps())
        if self.terminal == "malformed":
            return PlanDraft.model_construct(request_id=value.request_id, steps=())
        return await super().propose(value)


@pytest.mark.parametrize("alias", [None, "AIZEN", "Renamed"])
def test_identity_and_behavior_do_not_depend_on_alias(alias: str | None) -> None:
    store = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id="strategist", alias=alias, professional_role="R"
            ),
            AgentDefinition(
                internal_id="other", alias="AIZEN", professional_role="Planner"
            ),
        )
    )
    before = store.model_dump_json()
    owner = AizenPlanner(store)
    assert owner.planner_id == "strategist" and owner.active_request_ids == ()
    assert store.model_dump_json() == before
    with pytest.raises(AttributeError):
        owner.planner_id = "AIZEN"  # type: ignore[misc,assignment]

    async def exercise() -> None:
        result = await owner.plan(request(), responder=Responder())
        assert result.planner_id == "strategist"

    run(exercise())


@pytest.mark.parametrize("identity", ["AIZEN", "phoenix", "other"])
def test_alias_and_role_cannot_replace_missing_strategist(identity: str) -> None:
    store = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id=identity, alias="AIZEN", professional_role="Chief planner"
            ),
        )
    )
    with pytest.raises(UnknownAgentError):
        AizenPlanner(store)


def test_default_registry_and_phoenix_identity_remain_separate() -> None:
    store = build_default_agent_registry()
    assert store.require("phoenix").professional_role == "Supreme orchestrator"
    assert (
        store.require("strategist").professional_role
        == "Chief strategist/master planner"
    )
    assert AizenPlanner().planner_id == "strategist"
    with pytest.raises(UnknownAgentError):
        AizenPlanner(AgentRegistry())


def test_generated_and_caller_ids_preserve_meaningful_objective() -> None:
    first, second = PlanningRequest(objective="x"), PlanningRequest(objective="x")
    assert UUID(first.request_id).version == 4 and first.request_id != second.request_id
    value = request("  Caller-ID\n")
    assert value.request_id == "Caller-ID"
    assert value.objective == "  objective\ncontent  "
    assert PlanningRequest.model_validate_json(value.model_dump_json()) == value
    assert PlanStep(step_id=" z \t", objective="  text\n").step_id == "z"
    assert PlanStep(step_id="z", objective="  text\n").objective == "  text\n"


@pytest.mark.parametrize(
    "model,field",
    [
        (PlanningRequest, "request_id"),
        (PlanningRequest, "objective"),
        (PlanStep, "step_id"),
        (PlanStep, "objective"),
        (PlanDraft, "request_id"),
        (AizenPlan, "request_id"),
        (CancellationResult, "request_id"),
    ],
)
@pytest.mark.parametrize(
    "value", ["", " \t\n", None, True, False, 1, 1.5, b"x", {}, object()]
)
def test_strict_nonempty_strings(
    model: type[BaseModel], field: str, value: object
) -> None:
    values: dict[str, object] = {
        "request_id": "id",
        "objective": "objective",
        "step_id": "step",
        "steps": steps(),
        "status": CancellationStatus.ACCEPTED,
    }
    data = {key: item for key, item in values.items() if key in model.model_fields}
    data[field] = value
    with pytest.raises(ValidationError):
        model.model_validate(data)


@pytest.mark.parametrize(
    "value",
    [
        request(),
        steps()[0],
        PlanDraft(request_id="id", steps=steps()),
        AizenPlan(request_id="id", steps=steps()),
        CancellationResult(request_id="id", status=CancellationStatus.ACCEPTED),
    ],
)
def test_models_frozen_extra_forbidden_and_json_serializable(value: BaseModel) -> None:
    field = next(iter(type(value).model_fields))
    with pytest.raises(ValidationError):
        setattr(value, field, "changed")
    with pytest.raises(ValidationError):
        type(value).model_validate({**value.model_dump(), "metadata": {}})
    assert type(value).model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize("model", [PlanDraft, AizenPlan])
@pytest.mark.parametrize(
    "invalid_steps",
    [
        (),
        [],
        None,
        {},
        "raw",
        (object(),),
        (
            PlanStep(step_id="same", objective="x"),
            PlanStep(step_id=" same ", objective="y"),
        ),
        (PlanStep.model_construct(step_id="id", objective=""),),
        (PlanStep.model_construct(step_id=True, objective="x"),),
        (PlanStep(step_id="id", objective="x").model_copy(update={"metadata": {}}),),
    ],
)
def test_empty_duplicate_or_invalid_nested_steps_fail(
    model: type[BaseModel], invalid_steps: object
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate({"request_id": "id", "steps": invalid_steps})


@pytest.mark.parametrize("model", [PlanDraft, AizenPlan])
def test_steps_snapshot_caller_containers_preserving_exact_order(
    model: type[BaseModel],
) -> None:
    source = [step.model_dump() for step in steps()]
    result = model.model_validate({"request_id": "id", "steps": source})
    source[0]["objective"] = "changed"
    source.reverse()
    source.clear()
    assert result.steps == steps() and isinstance(result.steps, tuple)  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        result.steps[0].objective = "changed"  # type: ignore[attr-defined]


@pytest.mark.parametrize("identity", ["AIZEN", "phoenix", "other", None, True])
def test_final_planner_id_is_literal(identity: object) -> None:
    with pytest.raises(ValidationError):
        AizenPlan.model_validate(
            {"request_id": "id", "planner_id": identity, "steps": steps()}
        )


def test_responder_called_once_and_final_plan_is_subordinate() -> None:
    async def exercise() -> None:
        owner, responder, value = AizenPlanner(), Responder(), request()
        result = await owner.plan(value, responder=responder)
        assert responder.requests == [value]
        assert responder.requests[0].model_dump_json() == value.model_dump_json()
        assert type(result) is AizenPlan and not isinstance(result, PhoenixResult)
        assert (
            result.request_id == value.request_id and result.planner_id == "strategist"
        )
        assert result.steps == steps()
        assert tuple(step.step_id for step in result.steps) == ("z", "a")
        assert owner.active_request_ids == ()

    run(exercise())


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        "raw",
        object(),
        AizenPlan(request_id="request", steps=steps()),
        PhoenixResult(
            request_id="request", kind=PhoenixResultKind.COMPLETED, text="success"
        ),
        PlanDraft(request_id="wrong", steps=steps()),
        PlanDraft.model_construct(request_id="request", steps=()),
        PlanDraft.model_construct(request_id="request", steps=(object(),)),
        PlanDraft.model_construct(
            request_id="request",
            steps=(
                PlanStep(step_id="same", objective="x"),
                PlanStep(step_id="same", objective="y"),
            ),
        ),
        PlanDraft.model_construct(
            request_id="request",
            steps=(PlanStep.model_construct(step_id="id", objective=" \n"),),
        ),
        PlanDraft.model_construct(
            request_id="request",
            steps=(PlanStep.model_construct(step_id=True, objective="x"),),
        ),
        PlanDraft.model_construct(request_id=True, steps=steps()),
        PlanDraft(request_id="request", steps=steps()).model_copy(
            update={"metadata": {}}
        ),
    ],
)
def test_malformed_subordinate_output_fails_and_cleans_up(response: object) -> None:
    class InvalidResponder:
        async def propose(self, value: PlanningRequest) -> PlanDraft:
            return response  # type: ignore[return-value]

    async def exercise() -> None:
        owner = AizenPlanner()
        with pytest.raises(AizenPlanningError):
            await owner.plan(request(), responder=InvalidResponder())
        assert owner.active_request_ids == ()
        assert (
            await owner.plan(request(), responder=Responder())
        ).request_id == "request"

    run(exercise())


@pytest.mark.parametrize("extension", ["draft", "step"])
def test_extended_contracts_cannot_smuggle_runtime_data(extension: str) -> None:
    class ExtendedDraft(PlanDraft):
        metadata: str = "provider payload"

    class ExtendedStep(PlanStep):
        target_agent: str = "other"

    class ExtendedResponder:
        async def propose(self, value: PlanningRequest) -> PlanDraft:
            if extension == "draft":
                return ExtendedDraft(request_id=value.request_id, steps=steps())
            return PlanDraft.model_construct(
                request_id=value.request_id,
                steps=(ExtendedStep(step_id="id", objective="x"),),
            )

    async def exercise() -> None:
        owner = AizenPlanner()
        with pytest.raises(AizenPlanningError):
            await owner.plan(request(), responder=ExtendedResponder())
        assert owner.active_request_ids == ()

    run(exercise())


def test_invalid_requests_fail_before_responder_or_registration() -> None:
    class ExtendedRequest(PlanningRequest):
        metadata: str = "raw"

    async def exercise() -> None:
        owner, responder = AizenPlanner(), Responder()
        invalid_requests: tuple[object, ...] = ({}, ExtendedRequest(objective="x"))
        for invalid in invalid_requests:
            with pytest.raises(AizenPlanningError):
                await owner.plan(invalid, responder=responder)  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            await owner.plan(
                PlanningRequest.model_construct(request_id="id", objective=""),
                responder=responder,
            )
        assert responder.requests == [] and owner.active_request_ids == ()

    run(exercise())


@pytest.mark.parametrize(
    "terminal",
    ["success", "failure", "mismatch", "malformed", "caller_cancel", "cancel"],
)
def test_concurrent_lifetimes_and_cancellation_remain_independent(
    terminal: str,
) -> None:
    async def exercise() -> None:
        owner = AizenPlanner()
        a, b = SuspendedResponder(terminal=terminal), SuspendedResponder()
        # Reverse insertion order verifies the inspection is sorted.
        tb = asyncio.create_task(owner.plan(request("b"), responder=b))
        ta = asyncio.create_task(owner.plan(request("a"), responder=a))
        try:
            await a.entered.wait()
            await b.entered.wait()
            snapshot = owner.active_request_ids
            assert snapshot == ("a", "b")
            assert all(isinstance(identity, str) for identity in snapshot)
            with pytest.raises(AttributeError):
                owner.active_request_ids = ()  # type: ignore[misc]
            if terminal == "cancel":
                result = await owner.cancel(" a \n")
                assert result.status == CancellationStatus.ACCEPTED
                assert result.request_id == "a"
            elif terminal == "caller_cancel":
                ta.cancel()
            else:
                a.release.set()
            if terminal in {"cancel", "caller_cancel"}:
                with pytest.raises(asyncio.CancelledError):
                    await ta
            elif terminal == "failure":
                with pytest.raises(RuntimeError, match="fixture failure"):
                    await ta
            elif terminal in {"mismatch", "malformed"}:
                with pytest.raises(AizenPlanningError):
                    await ta
            else:
                assert (await ta).request_id == "a"
            assert snapshot == ("a", "b") and owner.active_request_ids == ("b",)
            assert not tb.done() and not b.cancelled
            assert (await owner.cancel("a")).status == CancellationStatus.NOT_FOUND
            await owner.plan(request("a"), responder=Responder())
            assert owner.active_request_ids == ("b",)
            b.release.set()
            assert (await tb).request_id == "b" and owner.active_request_ids == ()
        finally:
            ta.cancel()
            tb.cancel()
            await asyncio.gather(ta, tb, return_exceptions=True)

    run(exercise())


def test_duplicate_rejection_b_completion_and_id_reuse() -> None:
    async def exercise() -> None:
        owner, a, b, duplicate = (
            AizenPlanner(),
            SuspendedResponder(),
            SuspendedResponder(),
            Responder(),
        )
        ta = asyncio.create_task(owner.plan(request("a"), responder=a))
        tb = asyncio.create_task(owner.plan(request("b"), responder=b))
        try:
            await a.entered.wait()
            await b.entered.wait()
            with pytest.raises(DuplicatePlanningRequestError):
                await owner.plan(request(" a "), responder=duplicate)
            assert owner.active_request_ids == ("a", "b") and duplicate.requests == []
            assert not ta.done() and not a.cancelled
            b.release.set()
            assert (await tb).request_id == "b"
            assert owner.active_request_ids == ("a",) and not ta.done()
            a.release.set()
            await ta
            assert owner.active_request_ids == ()
            assert (
                await owner.plan(request("a"), responder=duplicate)
            ).request_id == "a"
            assert len(duplicate.requests) == 1
        finally:
            ta.cancel()
            tb.cancel()
            await asyncio.gather(ta, tb, return_exceptions=True)

    run(exercise())


@pytest.mark.parametrize("source", ["caller", "planner"])
def test_suppressed_cancellation_cannot_finalize_plan(source: str) -> None:
    async def exercise() -> None:
        owner, responder = AizenPlanner(), SuspendedResponder(suppress_cancel=True)
        task = asyncio.create_task(owner.plan(request(), responder=responder))
        try:
            await responder.entered.wait()
            if source == "caller":
                task.cancel()
            else:
                assert (
                    await owner.cancel("request")
                ).status == CancellationStatus.ACCEPTED
            with pytest.raises(asyncio.CancelledError):
                await task
            assert responder.cancelled and owner.active_request_ids == ()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    run(exercise())


def test_recorded_cancellation_survives_responder_clearing_task_counter() -> None:
    owner = AizenPlanner()

    class ClearingResponder:
        async def propose(self, value: PlanningRequest) -> PlanDraft:
            assert (
                await owner.cancel(value.request_id)
            ).status == CancellationStatus.ACCEPTED
            try:
                await asyncio.sleep(0)  # Explicit checkpoint delivers cancellation.
            except asyncio.CancelledError:
                task = asyncio.current_task()
                assert task is not None
                task.uncancel()
                assert task.cancelling() == 0
            return PlanDraft(request_id=value.request_id, steps=steps())

    async def exercise() -> None:
        task = asyncio.create_task(owner.plan(request(), responder=ClearingResponder()))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owner.active_request_ids == ()

    run(exercise())


def test_self_cancellation_without_responder_await_cannot_finalize() -> None:
    owner = AizenPlanner()

    class SelfCancellingResponder:
        async def propose(self, value: PlanningRequest) -> PlanDraft:
            assert (
                await owner.cancel(value.request_id)
            ).status == CancellationStatus.ACCEPTED
            return PlanDraft(request_id=value.request_id, steps=steps())

    async def exercise() -> None:
        task = asyncio.create_task(
            owner.plan(request(), responder=SelfCancellingResponder())
        )
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owner.active_request_ids == ()

    run(exercise())


def test_nested_requests_cannot_share_cancellation_ownership() -> None:
    owner = AizenPlanner()

    class NestedResponder:
        async def propose(self, value: PlanningRequest) -> PlanDraft:
            with pytest.raises(AizenPlanningError, match="already owns"):
                await owner.plan(request("nested"), responder=Responder())
            assert owner.active_request_ids == ("request",)
            assert (await owner.cancel("nested")).status == CancellationStatus.NOT_FOUND
            return PlanDraft(request_id=value.request_id, steps=steps())

    async def exercise() -> None:
        await owner.plan(request(), responder=NestedResponder())
        assert owner.active_request_ids == ()

    run(exercise())


@pytest.mark.parametrize("identity", ["", " \n", None, True, 1, {}, b"id"])
def test_cancel_validates_ids_and_unknown_is_not_found(identity: object) -> None:
    async def exercise() -> None:
        owner = AizenPlanner()
        with pytest.raises(ValidationError):
            await owner.cancel(identity)  # type: ignore[arg-type]
        assert (await owner.cancel(" unknown ")).status == CancellationStatus.NOT_FOUND
        assert owner.active_request_ids == ()

    run(exercise())


def test_one_event_loop_ownership() -> None:
    owner = AizenPlanner()

    async def first() -> None:
        await owner.plan(request(), responder=Responder())

    async def second() -> None:
        with pytest.raises(AizenPlanningError, match="another event loop"):
            await owner.plan(request(), responder=Responder())
        with pytest.raises(AizenPlanningError, match="another event loop"):
            await owner.cancel("request")
        assert owner.active_request_ids == ()

    run(first())
    run(second())


def test_no_hidden_tasks_and_phoenix_complex_stays_planning_required() -> None:
    async def exercise() -> None:
        owner, phoenix, responder = AizenPlanner(), PhoenixOrchestrator(), Responder()
        loop = asyncio.get_running_loop()
        before = asyncio.all_tasks()
        with patch.object(
            loop, "create_task", side_effect=AssertionError("Hidden task")
        ):
            await owner.plan(request(), responder=responder)
            await owner.cancel("missing")
            with patch.object(
                owner, "plan", side_effect=AssertionError("Planner integration")
            ):
                result = await phoenix.execute(
                    PhoenixRequest(text="complex"), OrchestrationPath.COMPLEX
                )
            assert (
                result.kind == PhoenixResultKind.PLANNING_REQUIRED
                and result.text is None
            )
        assert asyncio.all_tasks() == before
        assert owner.active_request_ids == () and phoenix.active_request_ids == ()

    run(exercise())


def test_public_fields_and_imports_remain_neutral_and_unassigned() -> None:
    allowed = {
        "asyncio",
        "dataclasses",
        "enum",
        "typing",
        "uuid",
        "pydantic",
        "phoenix.agents.registry",
    }
    for node in ast.walk(ast.parse(inspect.getsource(planner))):
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
    assert set(PlanningRequest.model_fields) == {"request_id", "objective"}
    assert set(PlanStep.model_fields) == {"step_id", "objective"}
    assert set(PlanDraft.model_fields) == {"request_id", "steps"}
    assert set(AizenPlan.model_fields) == {"request_id", "planner_id", "steps"}
    assert set(CancellationResult.model_fields) == {"request_id", "status"}
    assert set(CancellationStatus) == {"accepted", "not_found"}


def test_fresh_interpreter_import_construct_inspect_isolation(tmp_path: Path) -> None:
    script = """
import asyncio, importlib.abc, os, socket, sqlite3, subprocess, sys, threading
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected planner runtime activity")
def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("phoenix.models", "phoenix.core", "phoenix.api", "phoenix.persistence",
                 "phoenix.tools", "phoenix.permissions", "phoenix.memory",
                 "phoenix.voice", "phoenix.recovery", "phoenix.agents.orchestrator",
                 "httpx", "psutil", "alembic")
        if any(fullname == root or fullname.startswith(root + ".") for root in roots):
            forbidden()
sys.dont_write_bytecode = True
sys.meta_path.insert(0, BlockImports())
sys.addaudithook(audit)
os.mkdir = forbidden
socket.socket = forbidden
socket.create_connection = forbidden
sqlite3.connect = forbidden
subprocess.Popen = forbidden
threading.Thread.start = forbidden
asyncio.create_task = forbidden
asyncio.BaseEventLoop.create_task = forbidden
from phoenix.agents.planner import (
    AizenPlanner, PlanningRequest, PlanStep, PlanDraft, AizenPlan, CancellationResult
)
owner = AizenPlanner()
request = PlanningRequest(objective="x")
step = PlanStep(step_id="id", objective="x")
draft = PlanDraft(request_id=request.request_id, steps=(step,))
plan = AizenPlan(request_id=request.request_id, steps=draft.steps)
assert plan.planner_id == "strategist" and plan.steps == (step,)
cancel = CancellationResult(request_id=request.request_id, status="not_found")
assert cancel.status == "not_found"
assert owner.planner_id == "strategist" and owner.active_request_ids == ()
assert request.model_dump_json() and plan.model_dump_json()
assert not list(Path.cwd().iterdir())
print("Planner isolation: PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "Planner isolation: PASS"
