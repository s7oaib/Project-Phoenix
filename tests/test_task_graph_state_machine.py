"""PHX-024 structural DAG, canonical transitions, atomicity and isolation proof."""

import ast
import inspect
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from itertools import permutations
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from phoenix.agents import task_graph
from phoenix.agents.registry import (
    AgentDefinition,
    AgentRegistry,
    build_default_agent_registry,
)
from phoenix.agents.task_graph import (
    TASK_TRANSITIONS,
    TaskGraph,
    TaskState,
    TaskStateMachine,
    TaskStateTransitionError,
    TaskStepDefinition,
    TaskTransition,
    UnknownStepError,
)

# Independent acceptance policy, not copied from the production transition map.
EXPECTED = {
    "created": ("planning", "cancelled"),
    "planning": ("ready", "failed", "cancelled"),
    "ready": ("running", "cancelled"),
    "running": ("waiting_permission", "verifying", "failed", "cancelled"),
    "waiting_permission": ("running", "failed", "cancelled"),
    "verifying": ("completed", "failed", "recovering", "cancelled"),
    "completed": (),
    "failed": ("recovering", "degraded", "cancelled"),
    "recovering": ("planning", "ready", "running", "failed", "degraded", "cancelled"),
    "degraded": (),
    "cancelled": (),
}
PATHS = {
    "created": (),
    "planning": ("planning",),
    "ready": ("planning", "ready"),
    "running": ("planning", "ready", "running"),
    "waiting_permission": ("planning", "ready", "running", "waiting_permission"),
    "verifying": ("planning", "ready", "running", "verifying"),
    "completed": ("planning", "ready", "running", "verifying", "completed"),
    "failed": ("planning", "failed"),
    "recovering": ("planning", "failed", "recovering"),
    "degraded": ("planning", "failed", "degraded"),
    "cancelled": ("cancelled",),
}


def step(identity: str = "step", /, **changes: object) -> TaskStepDefinition:
    return TaskStepDefinition.model_validate(
        {
            "step_id": identity,
            "owner_agent_id": "build_engineer",
            "objective": "  describe requirements\nwithout execution  ",
            "expected_output": "  written description\n",
            "timeout_seconds": 30,
            "completion_rule": "  description supplied  ",
            **changes,
        }
    )


def graph(**changes: object) -> TaskGraph:
    return TaskGraph.model_validate({"task_id": "task", "steps": (step(),), **changes})


def machine_at(state: str) -> TaskStateMachine:
    owner = TaskStateMachine(" task ")
    for target in PATHS[state]:
        owner.transition(TaskState(target))
    assert owner.state.value == state
    return owner


def record(**changes: object) -> TaskTransition:
    return TaskTransition.model_validate(
        {"task_id": "task", "from_state": "created", "to_state": "planning", **changes}
    )


def test_exact_states_and_immutable_transition_table() -> None:
    assert [state.value for state in TaskState] == list(EXPECTED)
    assert len(TaskState) == 11
    assert {
        state.value: {target.value for target in targets}
        for state, targets in TASK_TRANSITIONS.items()
    } == {state: set(targets) for state, targets in EXPECTED.items()}
    assert sum(len(targets) for targets in TASK_TRANSITIONS.values()) == 27
    assert isinstance(TASK_TRANSITIONS, MappingProxyType)
    assert all(isinstance(targets, frozenset) for targets in TASK_TRANSITIONS.values())
    with pytest.raises(TypeError):
        TASK_TRANSITIONS[TaskState.CREATED] = frozenset()  # type: ignore[index]


@pytest.mark.parametrize("source", EXPECTED)
@pytest.mark.parametrize("target", EXPECTED)
def test_every_state_pair_legality_and_failure_atomicity(
    source: str, target: str
) -> None:
    owner = machine_at(source)
    before = owner.state, owner.history
    destination = TaskState(target)
    legal = target in EXPECTED[source]
    assert owner.can_transition(destination) == legal
    assert (owner.state, owner.history) == before
    assert owner.is_terminal == (source in {"completed", "degraded", "cancelled"})
    if legal:
        transition = owner.transition(destination)
        assert owner.state == destination
        assert owner.history == (*before[1], transition)
        assert len(owner.history) == len(before[1]) + 1
        assert transition.task_id == "task"
        assert (
            transition.from_state.value == source and transition.to_state == destination
        )
        assert transition.occurred_at.tzinfo is UTC
    else:
        with pytest.raises(
            TaskStateTransitionError, match="Illegal task state transition"
        ):
            owner.transition(destination)
        assert (owner.state, owner.history) == before


@pytest.mark.parametrize("invalid", ["", "unknown", "CREATED", None, 1, True, object()])
def test_invalid_state_validation_preserves_history(invalid: object) -> None:
    owner = machine_at("running")
    before = owner.state, owner.history
    for method in (owner.can_transition, owner.transition):
        with pytest.raises(ValidationError):
            method(invalid)  # type: ignore[arg-type]
        assert (owner.state, owner.history) == before


def test_initial_read_only_properties_and_history_snapshots() -> None:
    owner = TaskStateMachine(" task ")
    assert owner.task_id == "task" and owner.state == TaskState.CREATED
    assert owner.history == () and not owner.is_terminal
    snapshot = owner.history
    transition = owner.transition(TaskState.PLANNING)
    assert snapshot == () and owner.history == (transition,)
    for field in ("task_id", "state", "history", "is_terminal"):
        with pytest.raises(AttributeError):
            setattr(owner, field, "changed")
    with pytest.raises(ValidationError, match="frozen"):
        transition.to_state = TaskState.CANCELLED


def test_record_creation_failure_is_atomic() -> None:
    owner = machine_at("running")
    before = owner.state, owner.history
    with patch.object(
        task_graph, "TaskTransition", side_effect=RuntimeError("clock failure")
    ):
        with pytest.raises(RuntimeError, match="clock failure"):
            owner.transition(TaskState.VERIFYING)
    assert (owner.state, owner.history) == before
    assert owner.transition(TaskState.VERIFYING) == owner.history[-1]


def test_history_order_does_not_depend_on_wall_clock() -> None:
    first = record(occurred_at=datetime(2026, 10, 5, tzinfo=UTC))
    second = record(
        from_state="planning",
        to_state="ready",
        occurred_at=datetime(2026, 10, 4, tzinfo=UTC),
    )
    owner = TaskStateMachine("task")
    with patch.object(task_graph, "TaskTransition", side_effect=(first, second)):
        owner.transition(TaskState.PLANNING)
        owner.transition(TaskState.READY)
    assert owner.history == (first, second) and owner.state == TaskState.READY


def test_transition_utc_normalization_roundtrip_and_exact_fields() -> None:
    timestamp = datetime(2026, 10, 5, 7, tzinfo=timezone(timedelta(hours=5)))
    value = record(task_id=" task ", occurred_at=timestamp)
    assert value.task_id == "task" and value.occurred_at == timestamp
    assert value.occurred_at.tzinfo is UTC and value.occurred_at.hour == 2
    assert TaskTransition.model_validate_json(value.model_dump_json()) == value
    assert set(value.model_dump()) == {
        "task_id",
        "from_state",
        "to_state",
        "occurred_at",
    }
    with pytest.raises(ValidationError):
        record(occurred_at=datetime(2026, 10, 5))
    with pytest.raises(ValidationError, match="Illegal task transition record"):
        record(to_state="completed")


@pytest.mark.parametrize("value", ["", " \t\n", None, True, 1, b"id", object()])
@pytest.mark.parametrize(
    "field",
    [
        "graph_task",
        "machine_task",
        "record_task",
        "step_id",
        "owner_agent_id",
        "dependency",
        "input_ref",
    ],
)
def test_all_identifiers_strict(value: object, field: str) -> None:
    with pytest.raises(ValidationError):
        if field == "graph_task":
            graph(task_id=value)
        elif field == "machine_task":
            TaskStateMachine(value)  # type: ignore[arg-type]
        elif field == "record_task":
            record(task_id=value)
        elif field == "dependency":
            step(depends_on=(value,))
        elif field == "input_ref":
            step(input_refs=(value,))
        else:
            step(**{field: value})


def test_graph_task_id_generated_and_caller_identity_preserved() -> None:
    first = TaskGraph(steps=(step(),))
    second = TaskGraph(steps=(step(),))
    UUID(first.task_id)
    assert first.task_id != second.task_id
    assert graph(task_id=" caller task ").task_id == "caller task"
    assert step(
        " id ",
        owner_agent_id=" build_engineer ",
        depends_on=[" before "],
        input_refs=[" ref "],
    ).model_dump() == {
        **step("id").model_dump(),
        "depends_on": ("before",),
        "input_refs": ("ref",),
    }


@pytest.mark.parametrize("field", ["objective", "expected_output", "completion_rule"])
@pytest.mark.parametrize("value", ["", " \t\n", None, True, 1, b"text", [], object()])
def test_descriptive_text_strict_and_meaningful(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        step(**{field: value})


def test_descriptive_text_preserves_formatting() -> None:
    value = "  meaningful\n content  "
    node = step(objective=value, expected_output=value, completion_rule=value)
    assert node.objective == node.expected_output == node.completion_rule == value


@pytest.mark.parametrize(
    "value",
    [
        0,
        -1,
        -0.5,
        float("nan"),
        float("inf"),
        float("-inf"),
        True,
        False,
        "1",
        None,
        b"1",
        object(),
    ],
)
def test_timeout_invalid_numeric_or_nonfinite_rejected(value: object) -> None:
    with pytest.raises(ValidationError):
        step(timeout_seconds=value)


@pytest.mark.parametrize("value", [1, 0.01, 30.5])
def test_timeout_metadata_accepts_positive_numbers(value: float) -> None:
    assert step(timeout_seconds=value).timeout_seconds == value


@pytest.mark.parametrize("field", ["depends_on", "input_refs"])
@pytest.mark.parametrize("value", [{"id"}, {"id": "value"}, "id", None])
def test_reference_order_requires_sequence(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        step(**{field: value})


@pytest.mark.parametrize("contract", ["step", "graph", "transition"])
@pytest.mark.parametrize(
    "field",
    [
        "metadata",
        "callback",
        "provider_id",
        "model_id",
        "permission_scope",
        "source_agent",
        "task_state",
    ],
)
def test_extra_fields_rejected(contract: str, field: str) -> None:
    models: dict[str, BaseModel] = {
        "step": step(),
        "graph": graph(),
        "transition": record(),
    }
    model = models[contract]
    with pytest.raises(ValidationError, match="Extra inputs"):
        type(model).model_validate({**model.model_dump(), field: "unexpected"})


def test_models_frozen_json_and_detached_caller_containers() -> None:
    dependencies = [" root "]
    refs = [" second ", "first"]
    child = step("child", depends_on=dependencies, input_refs=refs)
    raw = step("root").model_dump()
    steps = [child, raw]
    value = graph(steps=steps)
    dependencies.clear()
    refs.clear()
    steps.clear()
    raw["objective"] = "changed"
    assert value.steps[0].depends_on == ("root",)
    assert value.steps[0].input_refs == ("second", "first")
    assert value.steps[1].objective == step().objective
    assert value.steps[0] is not child
    assert TaskGraph.model_validate_json(value.model_dump_json()) == value
    assert TaskStepDefinition.model_validate_json(child.model_dump_json()) == child
    for model, field in ((value, "task_id"), (value.steps[0], "objective")):
        with pytest.raises(ValidationError, match="frozen"):
            setattr(model, field, "changed")


@pytest.mark.parametrize("agent", build_default_agent_registry().list_agents())
@pytest.mark.parametrize("alias", [None, "Renamed display alias"])
def test_every_registered_owner_and_alias_independence(
    agent: AgentDefinition,
    alias: str | None,
) -> None:
    node = step(owner_agent_id=agent.internal_id)
    expected = graph(steps=(node,))
    agents = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id=agent.internal_id,
                alias=alias,
                professional_role="unrelated text",
            ),
        )
    )
    actual = TaskGraph.model_validate(
        expected.model_dump(), context={"agent_registry": agents}
    )
    assert actual == expected
    assert actual.steps[0].owner_agent_id == agent.internal_id


@pytest.mark.parametrize(
    "alias", ["FRANKY", "LEVI", "AIZEN", "PHOENIX", "MAHORAGA", "KAIDO"]
)
def test_alias_cannot_substitute_for_owner(alias: str) -> None:
    with pytest.raises(ValidationError, match="Unknown step owner"):
        graph(steps=(step(owner_agent_id=alias),))


def test_custom_owner_explicit_context_and_registry_detachment() -> None:
    agents = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id="future_role",
                alias="build_engineer",
                professional_role="Role",
            ),
        )
    )
    data = {"task_id": "task", "steps": (step(owner_agent_id="future_role"),)}
    value = TaskGraph.model_validate(data, context={"agent_registry": agents})
    assert set(value.model_dump()) == {"task_id", "steps"}
    assert (
        TaskGraph.model_validate_json(
            value.model_dump_json(), context={"agent_registry": agents}
        )
        == value
    )
    with pytest.raises(ValidationError, match="Unknown step owner"):
        TaskGraph.model_validate(data)
    with pytest.raises(ValidationError, match="Unknown step owner"):
        TaskGraph.model_validate(
            {"steps": (step(),)}, context={"agent_registry": agents}
        )
    with pytest.raises(ValidationError, match="Unknown step owner"):
        TaskGraph.model_validate(data, context={"agent_registry": AgentRegistry()})
    with pytest.raises(ValidationError, match="AgentRegistry"):
        TaskGraph.model_validate(data, context={"agent_registry": object()})
    with pytest.raises(ValidationError, match="dictionary"):
        TaskGraph.model_validate(data, context="invalid")
    assert agents.list_agents()[0].alias == "build_engineer"


def test_nested_copied_constructed_extended_contracts_revalidated() -> None:
    for forged in (
        step().model_copy(update={"timeout_seconds": True}),
        step().model_copy(update={"depends_on": ("step",)}),
        step().model_copy(update={"input_refs": (None,)}),
        TaskStepDefinition.model_construct(**{**step().model_dump(), "objective": 1}),
    ):
        with pytest.raises(ValidationError):
            graph(steps=(forged,))

    class ExtendedStep(TaskStepDefinition):
        callback: str = "unexpected"

    with pytest.raises(ValidationError, match="Extra inputs"):
        graph(steps=(ExtendedStep(**step().model_dump()),))
    with pytest.raises(ValidationError, match="Duplicate task step ID"):
        TaskGraph.model_validate(graph().model_copy(update={"steps": (step(), step())}))
    forged_registry = AgentRegistry.model_construct(
        agents=(
            AgentDefinition.model_construct(
                internal_id="build_engineer", professional_role=1
            ),
        )
    )
    with pytest.raises(ValidationError):
        TaskGraph.model_validate(
            graph().model_dump(), context={"agent_registry": forged_registry}
        )


@pytest.mark.parametrize("value", [(), [], None, set(), {"id": "step"}, "step"])
def test_graph_requires_nonempty_ordered_steps(value: object) -> None:
    with pytest.raises(ValidationError):
        graph(steps=value)


def test_duplicate_normalized_step_dependency_and_self_dependency_rejected() -> None:
    with pytest.raises(ValidationError, match="Duplicate task step ID"):
        graph(steps=(step(" duplicate "), step("duplicate")))
    with pytest.raises(ValidationError, match="Duplicate step dependency"):
        step("child", depends_on=(" root ", "root"))
    with pytest.raises(ValidationError, match="Self-dependency"):
        step(" child ", depends_on=("child",))
    with pytest.raises(ValidationError, match="Unknown step dependency"):
        graph(steps=(step("child", depends_on=("absent",)),))


@pytest.mark.parametrize(
    "edges",
    [
        (("a", ("b",)), ("b", ("a",))),
        (("a", ("b",)), ("b", ("c",)), ("c", ("a",))),
        (("root", ()), ("a", ("b",)), ("b", ("c",)), ("c", ("a",))),
        (("root", ()), ("tail", ("a",)), ("a", ("b",)), ("b", ("a",))),
    ],
)
def test_cycles_rejected_without_repair(
    edges: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    nodes = tuple(
        step(identity, depends_on=dependencies) for identity, dependencies in edges
    )
    with pytest.raises(ValidationError, match="dependency cycle"):
        graph(steps=nodes)
    assert tuple(node.depends_on for node in nodes) == tuple(deps for _, deps in edges)


def test_source_order_roots_topology_readiness_and_lookup() -> None:
    value = graph(
        steps=(
            step("z-child", depends_on=("z-root",)),
            step("a-child", depends_on=("z-root", "a-root")),
            step("z-root"),
            step("a-root"),
            step("final", depends_on=("z-child", "a-child")),
        )
    )
    before = value.model_dump_json()
    assert tuple(node.step_id for node in value.steps) == (
        "z-child",
        "a-child",
        "z-root",
        "a-root",
        "final",
    )
    assert value.root_step_ids == ("z-root", "a-root")
    # Newly ready z-child has an earlier declared position than pending a-root.
    assert value.topological_step_ids == (
        "z-root",
        "z-child",
        "a-root",
        "a-child",
        "final",
    )
    assert value.ready_step_ids(()) == ("z-root", "a-root")
    assert value.ready_step_ids([" z-root "]) == ("z-child", "a-root")
    assert value.ready_step_ids(("a-root", "z-root")) == ("z-child", "a-child")
    assert value.ready_step_ids(("z-root", "a-root", "z-child", "a-child")) == (
        "final",
    )
    assert value.ready_step_ids([node.step_id for node in value.steps]) == ()
    assert value.step(" z-child ") is value.steps[0]
    assert value.step("unknown") is None and value.step("Z-CHILD") is None
    assert value.model_dump_json() == before
    with pytest.raises(UnknownStepError, match="Unknown completed"):
        value.ready_step_ids(("absent",))
    with pytest.raises(ValueError, match="Duplicate completed"):
        value.ready_step_ids((" z-root ", "z-root"))
    assert value.model_dump_json() == before


@pytest.mark.parametrize("invalid", ["", " \t ", None, True, 1, b"id", object()])
def test_lookup_and_completed_identifiers_strict(invalid: object) -> None:
    value = graph()
    before = value.model_dump_json()
    with pytest.raises(ValidationError):
        value.step(invalid)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        value.ready_step_ids([invalid])  # type: ignore[list-item]
    assert value.model_dump_json() == before


@pytest.mark.parametrize("invalid", [{"step"}, {"step": True}, "step", None])
def test_completed_query_requires_ordered_sequence(invalid: object) -> None:
    with pytest.raises(ValueError, match="ordered tuple or list"):
        graph().ready_step_ids(invalid)  # type: ignore[arg-type]


def test_all_four_node_dag_edge_sets_and_declared_orders() -> None:
    ids = ("z", "a", "m", "b")
    possible = tuple((ids[j], ids[i]) for i in range(4) for j in range(i + 1, 4))
    checked = 0
    for mask in range(1 << len(possible)):
        edges = tuple(pair for bit, pair in enumerate(possible) if mask & (1 << bit))
        for declaration in permutations(ids):
            value = graph(
                steps=tuple(
                    step(
                        identity,
                        depends_on=tuple(
                            dependency
                            for child, dependency in edges
                            if child == identity
                        ),
                    )
                    for identity in declaration
                )
            )
            # Brute-force reference: first valid permutation of source positions.
            expected = next(
                order
                for order in permutations(declaration)
                if all(
                    order.index(dependency) < order.index(child)
                    for child, dependency in edges
                )
            )
            assert value.topological_step_ids == expected
            assert value.root_step_ids == tuple(
                identity
                for identity in declaration
                if not any(child == identity for child, _ in edges)
            )
            checked += 1
    assert checked == 1536


def test_large_reverse_declared_chain_without_recursive_traversal() -> None:
    count = 1500
    nodes = tuple(
        step(f"step-{i}", depends_on=() if i == 0 else (f"step-{i - 1}",))
        for i in reversed(range(count))
    )
    value = graph(steps=nodes)
    assert value.root_step_ids == ("step-0",)
    assert value.topological_step_ids == tuple(f"step-{i}" for i in range(count))


def test_public_fields_imports_and_calls_only_graph_state_scope() -> None:
    assert set(TaskGraph.model_fields) == {"task_id", "steps"}
    assert set(TaskStepDefinition.model_fields) == {
        "step_id",
        "owner_agent_id",
        "objective",
        "depends_on",
        "input_refs",
        "expected_output",
        "timeout_seconds",
        "completion_rule",
    }
    allowed = {
        "collections.abc",
        "datetime",
        "enum",
        "heapq",
        "types",
        "typing",
        "uuid",
        "pydantic",
        "phoenix.agents.registry",
    }
    forbidden = {
        "execute",
        "plan",
        "route",
        "generate",
        "stream",
        "cancel",
        "acquire",
        "publish",
        "emit",
        "open",
        "create_task",
        "wait_for",
        "Popen",
        "start",
    }
    for node in ast.walk(ast.parse(inspect.getsource(task_graph))):
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
        assert not isinstance(node, (ast.AsyncFunctionDef, ast.Await))
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else "")
            )
            assert name not in forbidden
    assert not inspect.iscoroutinefunction(TaskStateMachine.transition)
    assert not inspect.iscoroutinefunction(TaskGraph.ready_step_ids)


def test_fresh_interpreter_construction_queries_and_state_isolation(
    tmp_path: Path,
) -> None:
    script = """
import asyncio, importlib.abc, os, socket, sqlite3, subprocess, sys, threading
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected graph/state runtime activity")
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
                 "phoenix.agents.planner", "phoenix.agents.router",
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
from phoenix.agents.task_graph import (
    TaskStepDefinition, TaskGraph, TaskState, TaskStateMachine, TaskStateTransitionError
)
from phoenix.agents.registry import AgentRegistry, AgentDefinition
node = TaskStepDefinition(
    step_id="step", owner_agent_id="build_engineer", objective="descriptive",
    input_refs=["file:///absent", "memory:unread"], expected_output="description",
    timeout_seconds=0.001, completion_rule="descriptive only"
)
graph = TaskGraph(steps=[node])
custom = AgentRegistry(agents=[AgentDefinition(
    internal_id="build_engineer", alias=None, professional_role="Role")])
assert TaskGraph.model_validate(
    graph.model_dump(), context={"agent_registry":custom}
) == graph
assert graph.root_step_ids == graph.topological_step_ids == ("step",)
assert graph.ready_step_ids([]) == ("step",) and graph.ready_step_ids(["step"]) == ()
assert graph.step("step").objective == "descriptive" and graph.step("absent") is None
assert TaskGraph.model_validate_json(graph.model_dump_json()) == graph
owner = TaskStateMachine(graph.task_id)
for target in (TaskState.PLANNING, TaskState.READY, TaskState.RUNNING,
               TaskState.WAITING_PERMISSION, TaskState.RUNNING, TaskState.VERIFYING,
               TaskState.RECOVERING, TaskState.FAILED, TaskState.DEGRADED):
    assert owner.can_transition(target)
    owner.transition(target)
assert owner.is_terminal and len(owner.history) == 9
assert all(item.model_dump_json() for item in owner.history)
before = owner.history
try:
    owner.transition(TaskState.RUNNING)
except TaskStateTransitionError:
    pass
else:
    raise AssertionError("Terminal escape")
assert owner.history == before
assert not list(Path.cwd().iterdir())
print("Graph/state isolation: PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "Graph/state isolation: PASS"
    assert list(tmp_path.iterdir()) == []
