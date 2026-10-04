"""Authority, contract, concurrent lifetime and isolation evidence for PHX-021."""

import ast
import asyncio
import inspect
import subprocess
import sys
from collections.abc import Awaitable
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from phoenix.agents import orchestrator
from phoenix.agents.orchestrator import (
    CancellationResult,
    CancellationStatus,
    DirectResponse,
    DuplicateRequestError,
    OrchestrationPath,
    PhoenixOrchestrationError,
    PhoenixOrchestrator,
    PhoenixRequest,
    PhoenixResult,
    PhoenixResultKind,
)
from phoenix.agents.registry import AgentDefinition, AgentRegistry, UnknownAgentError


def run(coroutine: Awaitable[None]) -> None:
    async def bounded() -> None:
        await asyncio.wait_for(coroutine, 3)

    asyncio.run(bounded())


class Responder:
    def __init__(self) -> None:
        self.requests: list[PhoenixRequest] = []

    async def respond(self, request: PhoenixRequest) -> DirectResponse:
        self.requests.append(request)
        return DirectResponse(request_id=request.request_id, text="  answer\n")


class SuspendedResponder(Responder):
    def __init__(self, *, fail: bool = False, suppress_cancel: bool = False) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = fail
        self.suppress_cancel = suppress_cancel
        self.cancelled = False

    async def respond(self, request: PhoenixRequest) -> DirectResponse:
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            if not self.suppress_cancel:
                raise
        if self.fail:
            raise RuntimeError("fixture failure")
        return await super().respond(request)


def request(identity: str = "request") -> PhoenixRequest:
    return PhoenixRequest(request_id=identity, text="  input\nwith whitespace  ")


@pytest.mark.parametrize("alias", [None, "PHOENIX", "Renamed"])
def test_authority_uses_only_internal_id(alias: str | None) -> None:
    store = AgentRegistry(
        agents=(
            AgentDefinition(internal_id="phoenix", alias=alias, professional_role="R"),
            AgentDefinition(
                internal_id="other", alias="PHOENIX", professional_role="Supreme"
            ),
        )
    )
    before = store.model_dump_json()
    owner = PhoenixOrchestrator(store)
    assert owner.orchestrator_id == "phoenix"
    assert store.model_dump_json() == before
    with pytest.raises(AttributeError):
        owner.orchestrator_id = "other"  # type: ignore[misc,assignment]

    async def exercise() -> None:
        result = await owner.execute(
            request(), OrchestrationPath.DIRECT, direct_responder=Responder()
        )
        assert result.orchestrator_id == "phoenix"

    run(exercise())


@pytest.mark.parametrize("identity", ["PHOENIX", "strategist", "custom"])
def test_alias_and_role_cannot_supply_missing_authority(identity: str) -> None:
    store = AgentRegistry(
        agents=(
            AgentDefinition(
                internal_id=identity, alias="PHOENIX", professional_role="Supreme"
            ),
        )
    )
    with pytest.raises(UnknownAgentError):
        PhoenixOrchestrator(store)


def test_default_and_empty_registry() -> None:
    owner = PhoenixOrchestrator()
    assert owner.orchestrator_id == "phoenix" and owner.active_request_ids == ()
    with pytest.raises(UnknownAgentError):
        PhoenixOrchestrator(AgentRegistry())


def test_request_ids_are_generated_or_preserved_and_text_is_preserved() -> None:
    first, second = PhoenixRequest(text="x"), PhoenixRequest(text="x")
    assert UUID(first.request_id).version == 4 and first.request_id != second.request_id
    value = request("  Caller-ID \t")
    assert value.request_id == "Caller-ID"
    assert value.text == "  input\nwith whitespace  "
    assert PhoenixRequest.model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize("field", ["request_id", "text"])
@pytest.mark.parametrize(
    "value", ["", " \t\n", None, True, False, 1, 1.5, b"x", {}, []]
)
@pytest.mark.parametrize("model", [PhoenixRequest, DirectResponse])
def test_strict_request_and_response_fields(
    model: type[BaseModel], field: str, value: object
) -> None:
    values: dict[str, Any] = {"request_id": "id", "text": "text", field: value}
    with pytest.raises(ValidationError):
        model(**values)


@pytest.mark.parametrize(
    "value",
    [
        request(),
        DirectResponse(request_id="id", text="content"),
        PhoenixResult(request_id="id", kind=PhoenixResultKind.COMPLETED, text="x"),
        PhoenixResult(request_id="id", kind=PhoenixResultKind.PLANNING_REQUIRED),
        CancellationResult(request_id="id", status=CancellationStatus.ACCEPTED),
    ],
)
def test_values_are_frozen_extra_forbidden_and_serializable(value: BaseModel) -> None:
    with pytest.raises(ValidationError):
        value.request_id = "changed"  # type: ignore[attr-defined]
    with pytest.raises(ValidationError):
        type(value).model_validate({**value.model_dump(), "metadata": {}})
    assert type(value).model_validate_json(value.model_dump_json()) == value


@pytest.mark.parametrize(
    "identity", ["PHOENIX", "strategist", "verifier", "other", None]
)
def test_final_result_authority_is_literal(identity: object) -> None:
    with pytest.raises(ValidationError):
        PhoenixResult(
            request_id="id",
            orchestrator_id=identity,  # type: ignore[arg-type]
            kind=PhoenixResultKind.COMPLETED,
            text="x",
        )


@pytest.mark.parametrize("text", [None, "", " \n", True, 2, {}, b"x"])
def test_completed_result_requires_strict_meaningful_text(text: object) -> None:
    with pytest.raises(ValidationError):
        PhoenixResult(
            request_id="id",
            kind=PhoenixResultKind.COMPLETED,
            text=text,  # type: ignore[arg-type]
        )


def test_planning_result_has_no_completion_content_and_paths_are_small() -> None:
    with pytest.raises(ValidationError):
        PhoenixResult(
            request_id="id", kind=PhoenixResultKind.PLANNING_REQUIRED, text="success"
        )
    assert set(OrchestrationPath) == {"direct", "complex"}
    assert set(PhoenixResultKind) == {"completed", "planning_required"}
    assert set(CancellationStatus) == {"accepted", "not_found"}


def test_direct_invokes_once_and_phoenix_finalizes_correlated_text() -> None:
    async def exercise() -> None:
        owner, responder, value = PhoenixOrchestrator(), Responder(), request()
        result = await owner.execute(
            value, OrchestrationPath.DIRECT, direct_responder=responder
        )
        assert responder.requests == [value]
        assert isinstance(result, PhoenixResult)
        assert result.request_id == value.request_id
        assert result.orchestrator_id == "phoenix"
        assert result.kind == PhoenixResultKind.COMPLETED
        assert result.text == "  answer\n" and owner.active_request_ids == ()

    run(exercise())


def test_complex_ignores_responder_and_returns_only_planning_required() -> None:
    async def exercise() -> None:
        owner, responder = PhoenixOrchestrator(), Responder()
        result = await owner.execute(
            request(), OrchestrationPath.COMPLEX, direct_responder=responder
        )
        assert responder.requests == [] and owner.active_request_ids == ()
        assert result.kind == PhoenixResultKind.PLANNING_REQUIRED
        assert result.text is None and result.orchestrator_id == "phoenix"
        assert result.request_id == "request"
        assert (await owner.execute(request(), OrchestrationPath.COMPLEX)) == result

    run(exercise())


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        "raw",
        object(),
        PhoenixResult(request_id="request", kind=PhoenixResultKind.COMPLETED, text="x"),
        DirectResponse(request_id="different", text="x"),
        DirectResponse.model_construct(request_id="request", text=""),
        DirectResponse.model_construct(request_id="request", text=" \n"),
        DirectResponse.model_construct(request_id="request", text=5),
        DirectResponse.model_construct(request_id="request", text=None),
        DirectResponse.model_construct(request_id=True, text="x"),
        DirectResponse(request_id="request", text="x").model_copy(
            update={"metadata": {}}
        ),
    ],
)
def test_invalid_subordinate_response_fails_and_cleans_up(response: object) -> None:
    class InvalidResponder:
        async def respond(self, value: PhoenixRequest) -> DirectResponse:
            return response  # type: ignore[return-value]

    async def exercise() -> None:
        owner = PhoenixOrchestrator()
        with pytest.raises(PhoenixOrchestrationError):
            await owner.execute(
                request(), OrchestrationPath.DIRECT, direct_responder=InvalidResponder()
            )
        assert owner.active_request_ids == ()
        assert (await owner.execute(request(), OrchestrationPath.COMPLEX)).text is None

    run(exercise())


def test_missing_responder_bad_path_and_forged_request_fail_without_leaks() -> None:
    async def exercise() -> None:
        owner = PhoenixOrchestrator()
        with pytest.raises(PhoenixOrchestrationError):
            await owner.execute(request(), OrchestrationPath.DIRECT)
        with pytest.raises(ValidationError):
            await owner.execute(request(), "unknown")  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            await owner.execute(
                PhoenixRequest.model_construct(request_id="id", text=""),
                OrchestrationPath.COMPLEX,
            )
        with pytest.raises(PhoenixOrchestrationError):
            await owner.execute({}, OrchestrationPath.COMPLEX)  # type: ignore[arg-type]
        assert owner.active_request_ids == ()

    run(exercise())


@pytest.mark.parametrize("terminal", ["success", "failure", "caller_cancel", "cancel"])
def test_two_concurrent_requests_cleanup_and_cancellation_are_isolated(
    terminal: str,
) -> None:
    async def exercise() -> None:
        owner = PhoenixOrchestrator()
        a = SuspendedResponder(fail=terminal == "failure")
        b = SuspendedResponder()
        tb = asyncio.create_task(
            owner.execute(request("b"), OrchestrationPath.DIRECT, direct_responder=b)
        )
        ta = asyncio.create_task(
            owner.execute(request("a"), OrchestrationPath.DIRECT, direct_responder=a)
        )
        try:
            await a.entered.wait()
            await b.entered.wait()
            snapshot = owner.active_request_ids
            assert snapshot == ("a", "b")
            assert all(isinstance(identity, str) for identity in snapshot)
            with pytest.raises(AttributeError):
                owner.active_request_ids = ()  # type: ignore[misc]
            if terminal == "cancel":
                outcome = await owner.cancel(" a \n")
                assert outcome.status == CancellationStatus.ACCEPTED
                assert outcome.request_id == "a"
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
            else:
                assert (await ta).kind == PhoenixResultKind.COMPLETED
            assert snapshot == ("a", "b") and owner.active_request_ids == ("b",)
            assert not tb.done() and not b.cancelled
            assert (await owner.cancel("a")).status == CancellationStatus.NOT_FOUND
            await owner.execute(request("a"), OrchestrationPath.COMPLEX)
            assert owner.active_request_ids == ("b",)
            b.release.set()
            assert (await tb).request_id == "b"
            assert owner.active_request_ids == ()
        finally:
            for task in (ta, tb):
                task.cancel()
            await asyncio.gather(ta, tb, return_exceptions=True)

    run(exercise())


def test_b_completion_does_not_remove_a_and_duplicate_does_not_corrupt_original() -> (
    None
):
    async def exercise() -> None:
        owner, a, b, duplicate = (
            PhoenixOrchestrator(),
            SuspendedResponder(),
            SuspendedResponder(),
            Responder(),
        )
        ta = asyncio.create_task(
            owner.execute(request("a"), OrchestrationPath.DIRECT, direct_responder=a)
        )
        tb = asyncio.create_task(
            owner.execute(request("b"), OrchestrationPath.DIRECT, direct_responder=b)
        )
        try:
            await a.entered.wait()
            await b.entered.wait()
            for path in OrchestrationPath:
                with pytest.raises(DuplicateRequestError):
                    await owner.execute(
                        request(" a "), path, direct_responder=duplicate
                    )
                assert owner.active_request_ids == ("a", "b")
                assert not ta.done() and not a.cancelled and duplicate.requests == []
            b.release.set()
            assert (await tb).request_id == "b"
            assert owner.active_request_ids == ("a",) and not ta.done()
            a.release.set()
            await ta
            assert owner.active_request_ids == ()
            assert (
                await owner.execute(
                    request("a"), OrchestrationPath.DIRECT, direct_responder=duplicate
                )
            ).kind == PhoenixResultKind.COMPLETED
            assert len(duplicate.requests) == 1
        finally:
            ta.cancel()
            tb.cancel()
            await asyncio.gather(ta, tb, return_exceptions=True)

    run(exercise())


@pytest.mark.parametrize("source", ["caller", "orchestrator"])
def test_collaborator_cannot_suppress_cancellation_and_finalize(source: str) -> None:
    async def exercise() -> None:
        owner, responder = (
            PhoenixOrchestrator(),
            SuspendedResponder(suppress_cancel=True),
        )
        task = asyncio.create_task(
            owner.execute(
                request(), OrchestrationPath.DIRECT, direct_responder=responder
            )
        )
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


@pytest.mark.parametrize("identity", ["", " \n", None, True, 1, {}, b"id"])
def test_cancel_validates_id_and_idle_unknown_is_not_found(identity: object) -> None:
    async def exercise() -> None:
        owner = PhoenixOrchestrator()
        with pytest.raises(ValidationError):
            await owner.cancel(identity)  # type: ignore[arg-type]
        assert (await owner.cancel(" unknown ")).status == CancellationStatus.NOT_FOUND
        assert owner.active_request_ids == ()

    run(exercise())


def test_one_event_loop_ownership_is_explicit() -> None:
    owner = PhoenixOrchestrator()

    async def first() -> None:
        await owner.execute(request(), OrchestrationPath.COMPLEX)

    async def second() -> None:
        with pytest.raises(PhoenixOrchestrationError, match="another event loop"):
            await owner.execute(request(), OrchestrationPath.COMPLEX)
        with pytest.raises(PhoenixOrchestrationError, match="another event loop"):
            await owner.cancel("request")
        assert owner.active_request_ids == ()

    run(first())
    run(second())


def test_nested_request_cannot_share_cancellation_ownership() -> None:
    owner = PhoenixOrchestrator()

    class NestedResponder:
        async def respond(self, value: PhoenixRequest) -> DirectResponse:
            with pytest.raises(PhoenixOrchestrationError, match="already owns"):
                await owner.execute(request("nested"), OrchestrationPath.COMPLEX)
            assert owner.active_request_ids == ("request",)
            assert (await owner.cancel("nested")).status == CancellationStatus.NOT_FOUND
            return DirectResponse(request_id=value.request_id, text="x")

    async def exercise() -> None:
        assert (
            await owner.execute(
                request(), OrchestrationPath.DIRECT, direct_responder=NestedResponder()
            )
        ).text == "x"
        assert owner.active_request_ids == ()

    run(exercise())


def test_response_subclasses_cannot_add_metadata_or_authority() -> None:
    class ExtendedResponse(DirectResponse):
        metadata: str = "raw"

    class ExtendedResponder:
        async def respond(self, value: PhoenixRequest) -> DirectResponse:
            return ExtendedResponse(request_id=value.request_id, text="x")

    async def exercise() -> None:
        owner = PhoenixOrchestrator()
        with pytest.raises(PhoenixOrchestrationError):
            await owner.execute(
                request(),
                OrchestrationPath.DIRECT,
                direct_responder=ExtendedResponder(),
            )
        assert owner.active_request_ids == ()

    run(exercise())


def test_explicit_execution_creates_no_hidden_tasks() -> None:
    async def exercise() -> None:
        owner = PhoenixOrchestrator()
        loop = asyncio.get_running_loop()
        before = asyncio.all_tasks()
        with patch.object(
            loop, "create_task", side_effect=AssertionError("Hidden task")
        ):
            await owner.execute(
                request(), OrchestrationPath.DIRECT, direct_responder=Responder()
            )
            await owner.execute(request(), OrchestrationPath.COMPLEX)
            await owner.cancel("missing")
        assert asyncio.all_tasks() == before and owner.active_request_ids == ()

    run(exercise())


def test_public_contract_has_no_provider_tools_or_future_phase_imports() -> None:
    tree = ast.parse(inspect.getsource(orchestrator))
    allowed = {
        "asyncio",
        "dataclasses",
        "enum",
        "typing",
        "uuid",
        "pydantic",
        "phoenix.agents.registry",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed
        if isinstance(node, ast.Import):
            assert all(alias.name in allowed for alias in node.names)
    assert set(PhoenixRequest.model_fields) == {"request_id", "text"}
    assert set(DirectResponse.model_fields) == {"request_id", "text"}
    assert set(PhoenixResult.model_fields) == {
        "request_id",
        "orchestrator_id",
        "kind",
        "text",
    }
    assert set(CancellationResult.model_fields) == {"request_id", "status"}


def test_fresh_interpreter_import_construct_inspect_isolation(tmp_path: Path) -> None:
    script = """
import asyncio, importlib.abc, os, socket, sqlite3, subprocess, sys, threading
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected orchestrator runtime activity")
def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("phoenix.models", "phoenix.core", "phoenix.api", "phoenix.persistence",
                 "phoenix.tools", "phoenix.permissions", "phoenix.memory",
                 "phoenix.voice", "phoenix.recovery", "httpx", "psutil", "alembic")
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
from phoenix.agents.orchestrator import (
    PhoenixOrchestrator, PhoenixRequest, DirectResponse, PhoenixResult,
    CancellationResult,
)
owner = PhoenixOrchestrator()
request = PhoenixRequest(text="content")
assert DirectResponse(request_id=request.request_id, text="answer").text == "answer"
result = PhoenixResult(request_id=request.request_id, kind="completed", text="x")
assert result.text == "x"
pending = PhoenixResult(request_id=request.request_id, kind="planning_required")
assert pending.text is None
cancel = CancellationResult(request_id=request.request_id, status="not_found")
assert cancel.status == "not_found"
assert owner.orchestrator_id == "phoenix" and owner.active_request_ids == ()
assert not list(Path.cwd().iterdir())
print("Orchestrator isolation: PASS")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "Orchestrator isolation: PASS"
