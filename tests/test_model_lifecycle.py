"""Deterministic lease capacity, cancellation-race and isolation verification."""

import ast
import asyncio
import inspect
import subprocess
import sys
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from phoenix.models import lifecycle
from phoenix.models.lifecycle import (
    GenerationLease,
    LeaseSnapshot,
    ModelLifecycleError,
    ModelLifecycleManager,
)
from phoenix.models.provider import ModelCapabilities, ModelProviderError
from phoenix.models.registry import ModelCapabilityRegistry


def run(coroutine: Awaitable[None]) -> None:
    async def bounded() -> None:
        await asyncio.wait_for(coroutine, 3)

    asyncio.run(bounded())


async def acquire(
    manager: ModelLifecycleManager,
    operation: str,
    provider: str = "fixture",
    model: str = "model",
) -> GenerationLease:
    return await manager.acquire_heavy(
        provider_id=provider, model_id=model, operation_id=operation
    )


def assert_counts(manager: ModelLifecycleManager, active: int) -> None:
    assert manager.active_count == active
    assert manager.available_count == manager.capacity - active
    assert 0 <= manager.active_count <= manager.capacity
    assert 0 <= manager.available_count <= manager.capacity
    assert len(manager.active_leases()) == active


@pytest.mark.parametrize("capacity", [0, -1, True, False, "2", 1.5, None])
def test_invalid_capacity(capacity: object) -> None:
    with pytest.raises(ValidationError):
        ModelLifecycleManager(capacity)  # type: ignore[arg-type]


@pytest.mark.parametrize("capacity", [1, 2, 4])
def test_positive_capacity_and_read_only_inspection(capacity: int) -> None:
    manager = ModelLifecycleManager(capacity)
    assert manager.capacity == capacity
    assert_counts(manager, 0)
    assert not manager.is_active("unknown")
    assert isinstance(manager.active_leases(), tuple)
    with pytest.raises(AttributeError):
        manager.capacity = 10  # type: ignore[misc]
    assert ModelLifecycleManager().capacity == 1


@pytest.mark.parametrize("field", ["provider_id", "model_id", "operation_id"])
@pytest.mark.parametrize("value", ["", " ", 1, None, True])
def test_invalid_acquisition_ids(field: str, value: object) -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        ids = {
            "provider_id": "fixture",
            "model_id": "model",
            "operation_id": "operation",
            field: value,
        }
        with pytest.raises(ValidationError):
            await manager.acquire_heavy(**ids)  # type: ignore[arg-type]
        assert_counts(manager, 0)
        lease = await acquire(manager, "valid")
        lease.release()

    run(exercise())


@pytest.mark.parametrize("operation_id", ["", " ", 1, None])
def test_is_active_validates_id(operation_id: object) -> None:
    with pytest.raises(ValidationError):
        ModelLifecycleManager().is_active(operation_id)  # type: ignore[arg-type]


def test_lease_identity_accounting_idempotent_release_and_id_reuse() -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        assert_counts(manager, 0)
        lease = await manager.acquire_heavy(
            provider_id=" fixture ",
            model_id=" model:latest ",
            operation_id=" operation ",
        )
        assert_counts(manager, 1)
        assert manager.is_active(" operation ")
        snapshot = lease.snapshot
        assert (snapshot.provider_id, snapshot.model_id, snapshot.operation_id) == (
            "fixture",
            "model:latest",
            "operation",
        )
        assert snapshot.acquired_at.tzinfo is UTC
        assert not lease.released
        assert manager.active_leases() == (snapshot,)
        with pytest.raises(AttributeError):
            lease.snapshot = snapshot  # type: ignore[misc]
        with pytest.raises(AttributeError):
            lease.released = True  # type: ignore[misc]
        with pytest.raises(ValidationError):
            snapshot.operation_id = "other"
        lease.release()
        assert lease.released and not manager.is_active("operation")
        assert_counts(manager, 0)
        lease.release()
        assert_counts(manager, 0)
        replacement = await acquire(manager, "operation")
        lease.release()
        assert manager.active_leases() == (replacement.snapshot,)
        replacement.release()
        assert_counts(manager, 0)

    run(exercise())


def test_snapshot_utc_json_and_forbidden_extra_fields() -> None:
    stamp = datetime(2026, 10, 4, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    record = LeaseSnapshot(
        provider_id="p", model_id="m", operation_id="o", acquired_at=stamp
    )
    assert record.acquired_at == stamp and record.acquired_at.tzinfo is UTC
    assert LeaseSnapshot.model_validate_json(record.model_dump_json()) == record
    assert record.model_dump_json() == record.model_dump_json()
    assert set(LeaseSnapshot.model_fields) == {
        "provider_id",
        "model_id",
        "operation_id",
        "acquired_at",
    }
    for changes in (
        {"acquired_at": datetime(2026, 1, 1)},
        {"loaded": True},
        {"score": 1},
        {"prompt": "content"},
        {"metadata": object()},
    ):
        with pytest.raises(ValidationError):
            LeaseSnapshot.model_validate({**record.model_dump(), **changes})


def test_global_capacity_blocks_different_providers_and_models() -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        first = await acquire(manager, "a", "ollama", "model-a")
        queued = asyncio.Event()
        entered = asyncio.Event()
        finish = asyncio.Event()

        async def waiting() -> None:
            queued.set()
            async with await acquire(manager, "b", "future-provider", "model-b"):
                entered.set()
                await finish.wait()

        task = asyncio.create_task(waiting())
        await queued.wait()
        assert not entered.is_set() and not task.done()
        assert_counts(manager, 1)
        assert not manager.is_active("b")
        first.release()
        await entered.wait()
        assert_counts(manager, 1)
        assert manager.is_active("b") and not manager.is_active("a")
        finish.set()
        await task
        assert_counts(manager, 0)

    run(exercise())


def test_capacity_two_exactly_two_and_sorted_snapshots() -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager(2)
        first = await acquire(manager, "z", "p-one", "same-model")
        second = await acquire(manager, "a", "p-two", "same-model")
        assert_counts(manager, 2)
        listing = manager.active_leases()
        assert tuple(s.operation_id for s in listing) == ("a", "z")
        assert {s.provider_id for s in listing} == {"p-one", "p-two"}
        queued = asyncio.Event()

        async def waiting() -> GenerationLease:
            queued.set()
            return await acquire(manager, "third")

        task = asyncio.create_task(waiting())
        await queued.wait()
        assert not task.done() and not manager.is_active("third")
        first.release()
        third = await task
        assert_counts(manager, 2)
        assert len(listing) == 2 and listing[1] is first.snapshot
        second.release()
        third.release()
        assert_counts(manager, 0)

    run(exercise())


@pytest.mark.parametrize("capacity", [1, 2])
def test_duplicate_active_id_rejected_before_waiting(capacity: int) -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager(capacity)
        first = await acquire(manager, "same")
        with pytest.raises(ModelLifecycleError, match="Duplicate"):
            await acquire(manager, "same", "different-provider", "different-model")
        assert_counts(manager, 1)
        first.release()
        leases = [await acquire(manager, str(index)) for index in range(capacity)]
        assert_counts(manager, capacity)
        for lease in leases:
            lease.release()
        assert_counts(manager, 0)

    run(exercise())


def test_duplicate_after_waiting_returns_reserved_permit() -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager(2)
        holders = [await acquire(manager, str(index)) for index in range(2)]
        started = [asyncio.Event(), asyncio.Event()]

        async def waiting(index: int) -> GenerationLease:
            started[index].set()
            return await acquire(manager, "same")

        tasks = [asyncio.create_task(waiting(index)) for index in range(2)]
        await asyncio.gather(*(event.wait() for event in started))
        assert all(not task.done() for task in tasks)
        for holder in holders:
            holder.release()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        leases = [result for result in results if isinstance(result, GenerationLease)]
        failures = [
            result for result in results if isinstance(result, ModelLifecycleError)
        ]
        assert len(leases) == len(failures) == 1
        assert_counts(manager, 1)
        other = await acquire(manager, "other")
        assert_counts(manager, 2)
        leases[0].release()
        other.release()
        assert_counts(manager, 0)

    run(exercise())


@pytest.mark.parametrize("window", ["queued", "woken"])
def test_waiter_cancellation_before_and_after_wakeup(window: str) -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        holder = await acquire(manager, "holder")
        queued = asyncio.Event()

        async def waiting() -> GenerationLease:
            queued.set()
            return await acquire(manager, "cancelled")

        task = asyncio.create_task(waiting())
        await queued.wait()
        assert not task.done() and not manager.is_active("cancelled")
        if window == "woken":
            # Cancel after a permit is assigned, before the waiter can resume.
            holder.release()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not manager.is_active("cancelled")
        if window == "queued":
            assert_counts(manager, 1)
            assert manager.is_active("holder")
            holder.release()
        assert_counts(manager, 0)
        async with await acquire(manager, "later"):
            assert_counts(manager, 1)
        assert_counts(manager, 0)

    run(exercise())


@pytest.mark.parametrize("window", ["queued", "woken"])
def test_cancelled_waiter_does_not_block_remaining_waiter(window: str) -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        first = await acquire(manager, "first")
        queued = [asyncio.Event(), asyncio.Event()]

        async def waiting(index: int) -> GenerationLease:
            queued[index].set()
            return await acquire(manager, f"waiting-{index}")

        tasks = [asyncio.create_task(waiting(index)) for index in range(2)]
        await asyncio.gather(*(event.wait() for event in queued))
        if window == "woken":
            first.release()
        tasks[0].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        first.release()
        next_lease = await tasks[1]
        assert next_lease.snapshot.operation_id == "waiting-1"
        next_lease.release()
        assert_counts(manager, 0)

    run(exercise())


def test_pending_cancellation_on_immediate_acquire_unwinds_context() -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        entered = asyncio.Event()

        async def holder() -> None:
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            async with await acquire(manager, "holder"):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(holder())
        with pytest.raises(asyncio.CancelledError):
            await task
        assert entered.is_set()
        assert_counts(manager, 0)
        async with await acquire(manager, "next"):
            assert_counts(manager, 1)

    run(exercise())


@pytest.mark.parametrize("cancellations", [1, 2])
def test_holder_cancellation_unwinds_context(cancellations: int) -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        entered = asyncio.Event()
        held: list[GenerationLease] = []

        async def holding() -> None:
            async with await acquire(manager, "holder") as lease:
                held.append(lease)
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(holding())
        await entered.wait()
        assert_counts(manager, 1)
        for _ in range(cancellations):
            task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert held[0].released
        assert_counts(manager, 0)
        async with await acquire(manager, "later"):
            assert_counts(manager, 1)

    run(exercise())


@pytest.mark.parametrize(
    "failure", [RuntimeError("fixture error"), ModelProviderError("provider error")]
)
def test_caller_exception_propagates_and_releases(failure: Exception) -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        with pytest.raises(type(failure)) as caught:
            async with await acquire(manager, "operation"):
                raise failure
        assert caught.value is failure
        assert_counts(manager, 0)
        async with await acquire(manager, "next"):
            assert_counts(manager, 1)

    run(exercise())


@pytest.mark.parametrize(
    "failure", [RuntimeError("construction failed"), asyncio.CancelledError()]
)
def test_failure_between_permit_and_registration_returns_capacity(
    failure: BaseException,
) -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        with patch.object(lifecycle, "LeaseSnapshot", side_effect=failure):
            with pytest.raises(type(failure)) as caught:
                await acquire(manager, "failed")
        assert caught.value is failure
        assert_counts(manager, 0)
        async with await acquire(manager, "next"):
            assert_counts(manager, 1)

    run(exercise())


def test_cancellation_requested_during_context_exit_cannot_interrupt_release() -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        release = GenerationLease.release

        def cancel_and_release(lease: GenerationLease) -> None:
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            release(lease)

        async def holder() -> None:
            with patch.object(GenerationLease, "release", cancel_and_release):
                async with await acquire(manager, "holder"):
                    assert_counts(manager, 1)

        task = asyncio.create_task(holder())
        with pytest.raises(asyncio.CancelledError):
            await task
        assert_counts(manager, 0)
        async with await acquire(manager, "next"):
            assert_counts(manager, 1)

    run(exercise())


def test_released_and_nested_contexts_fail_without_corrupting_capacity() -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        lease = await acquire(manager, "operation")
        async with lease:
            with pytest.raises(ModelLifecycleError, match="already entered"):
                async with lease:
                    raise AssertionError("unexpected nested entry")
            assert_counts(manager, 1)
        with pytest.raises(ModelLifecycleError, match="released"):
            async with lease:
                raise AssertionError("unexpected released entry")
        assert_counts(manager, 0)

    run(exercise())


def test_unregistered_handle_cannot_release_another_lease() -> None:
    async def exercise() -> None:
        manager = ModelLifecycleManager()
        original = await acquire(manager, "operation")
        impostor = GenerationLease(manager, original.snapshot)
        with pytest.raises(ModelLifecycleError, match="not active"):
            impostor.release()
        assert not impostor.released
        assert manager.active_leases() == (original.snapshot,)
        original.release()
        assert_counts(manager, 0)

    run(exercise())


def test_other_event_loop_rejected_even_without_semaphore_contention() -> None:
    manager = ModelLifecycleManager()

    async def initial() -> None:
        async with await acquire(manager, "first"):
            pass

    run(initial())

    async def wrong_loop() -> None:
        with pytest.raises(ModelLifecycleError, match="another event loop"):
            await acquire(manager, "other")
        assert_counts(manager, 0)

    run(wrong_loop())


def test_registry_presence_does_not_allocate_or_change_lifecycle_state() -> None:
    store = ModelCapabilityRegistry()
    record = store.register(
        ModelCapabilities(
            provider_id="fixture",
            model_id="model",
            supports_streaming=True,
            supports_structured_output=False,
            supports_tool_use=True,
            supports_vision=False,
            supports_cancellation=True,
        )
    )
    manager = ModelLifecycleManager()
    assert_counts(manager, 0)

    async def exercise() -> None:
        with patch.object(
            ModelCapabilityRegistry,
            "refresh",
            side_effect=AssertionError("unexpected registry call"),
        ):
            async with await acquire(manager, "operation"):
                assert_counts(manager, 1)
                assert store.get("fixture", "model") is record
        assert_counts(manager, 0)

    run(exercise())
    assert store.list_records() == (record,)


def test_module_imports_only_neutral_dependencies() -> None:
    tree = ast.parse(inspect.getsource(lifecycle))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name == "asyncio" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module in {
                "datetime",
                "typing",
                "pydantic",
                "phoenix.models.provider",
            }


def test_import_construction_and_explicit_permits_are_isolated(tmp_path: Path) -> None:
    script = """
import asyncio
import importlib.abc
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
loop = asyncio.new_event_loop()
caller_create_task = loop.create_task
def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected lifecycle side effect")
def audit(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()
class BlockImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("httpx", "ollama", "colibri", "psutil", "fastapi", "sqlalchemy",
                 "alembic", "phoenix.models.ollama", "phoenix.models.registry",
                 "phoenix.core", "phoenix.api", "phoenix.persistence",
                 "phoenix.agents", "phoenix.tools", "phoenix.permissions",
                 "phoenix.voice")
        if any(fullname == root or fullname.startswith(root + ".") for root in roots):
            forbidden()
sys.dont_write_bytecode = True
sys.addaudithook(audit)
sys.meta_path.insert(0, BlockImports())
os.mkdir = forbidden
sqlite3.connect = forbidden
sqlite3.dbapi2.connect = forbidden
socket.socket = forbidden
socket.create_connection = forbidden
subprocess.Popen = forbidden
threading.Thread.start = forbidden
asyncio.create_task = forbidden
asyncio.BaseEventLoop.create_task = forbidden
from phoenix.models.lifecycle import ModelLifecycleManager
manager = ModelLifecycleManager()
assert manager.capacity == manager.available_count == 1
assert manager.active_leases() == ()
async def explicit():
    async with await manager.acquire_heavy(
        provider_id="fixture", model_id="model", operation_id="operation"
    ) as lease:
        assert manager.active_count == 1 and manager.available_count == 0
        assert manager.is_active("operation")
        assert manager.active_leases() == (lease.snapshot,)
    lease.release()
    assert manager.active_count == 0 and manager.available_count == 1
    assert not list(Path.cwd().iterdir())
    print("Lifecycle isolation: PASS")
# The harness creates one explicit caller task through the saved public method.
# All task creation APIs remain blocked for the module and permit operations.
caller = caller_create_task(explicit())
try:
    loop.run_until_complete(caller)
finally:
    loop.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.strip() == "Lifecycle isolation: PASS"
