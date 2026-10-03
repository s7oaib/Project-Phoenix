"""PHX-006 canonical contracts, fan-out/backpressure, lifecycle and isolation."""

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from phoenix.core.event_bus import EventBus, EventBusFullError, SubscriptionClosedError
from phoenix.core.events import EventEnvelope, EventSeverity, EventType

CANONICAL = """phoenix.started phoenix.ready phoenix.degraded
agent.started agent.completed agent.failed task.created task.state_changed
step.started step.completed tool.requested tool.started tool.completed tool.failed
permission.requested permission.approved permission.denied
model.loading model.loaded model.unloaded provider.unavailable
verification.started verification.passed verification.failed
recovery.started recovery.completed recovery.exhausted memory.created document.indexed
voice.listening voice.speaking voice.interrupted""".split()


def test_canonical_names_and_severity() -> None:
    assert [event.value for event in EventType] == CANONICAL
    assert len(EventType) == 32
    assert [value.value for value in EventSeverity] == [
        "debug",
        "info",
        "warning",
        "error",
        "critical",
    ]
    for name in CANONICAL:
        assert (
            EventEnvelope.model_validate({"event_type": name}).model_dump(mode="json")[
                "event_type"
            ]
            == name
        )


def test_envelope_defaults_and_trace() -> None:
    first = EventEnvelope(event_type=EventType.TASK_CREATED)
    second = EventEnvelope(event_type=EventType.TASK_CREATED, correlation_id="trace-1")
    assert isinstance(first.event_id, UUID) and first.event_id != second.event_id
    assert first.correlation_id and first.correlation_id != second.correlation_id
    assert second.correlation_id == "trace-1"
    assert first.timestamp.tzinfo is UTC
    assert first.severity is EventSeverity.INFO
    assert first.task_id is None and first.agent_id is None
    with pytest.raises(TypeError):
        first.payload["other"] = 1  # type: ignore[index]  # Default is immutable too.
    assert set(first.model_dump()) == {
        "event_id",
        "event_type",
        "timestamp",
        "task_id",
        "agent_id",
        "payload",
        "severity",
        "correlation_id",
    }
    data = json.loads(first.model_dump_json())
    assert data["event_id"] == str(first.event_id)
    assert data["severity"] == "info" and data["timestamp"].endswith("Z")


def test_timestamp_and_optional_ids() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=5)))
    event = EventEnvelope(
        event_type=EventType.AGENT_STARTED,
        timestamp=timestamp,
        task_id="task-1",
        agent_id="planner",
    )
    assert event.timestamp == timestamp.astimezone(UTC)
    assert event.timestamp.tzinfo is UTC
    assert event.task_id == "task-1" and event.agent_id == "planner"


@pytest.mark.parametrize(
    "override",
    [
        {"event_type": "unknown.event"},
        {"severity": "P4"},
        {"timestamp": "2026-01-01T00:00:00"},
        {"event_id": "bad-id"},
        {"correlation_id": ""},
    ],
)
def test_invalid_envelope(override: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate({"event_type": "task.created", **override})


def test_json_payload_snapshot_and_immutability() -> None:
    payload = {"items": [None, True, 1, 1.5, "text", {"nested": "value"}]}
    event = EventEnvelope.model_validate(
        {"event_type": "task.created", "payload": payload}
    )
    expected = json.loads(json.dumps(payload))
    payload["items"].append("later")
    assert json.loads(event.model_dump_json())["payload"] == expected
    assert EventEnvelope.model_validate_json(event.model_dump_json()).model_dump() == (
        event.model_dump()
    )
    with pytest.raises(ValidationError):
        event.severity = EventSeverity.ERROR
    with pytest.raises(TypeError):
        event.payload["other"] = 1  # type: ignore[index]  # Intentional mutation.
    nested = event.payload["items"]
    assert isinstance(nested, tuple)
    with pytest.raises(TypeError):
        nested[-1]["nested"] = "changed"


@pytest.mark.parametrize(
    "value",
    [
        Path("private"),
        object(),
        lambda: None,
        b"private",
        {1, 2},
        float("inf"),
        float("nan"),
        {1: "bad-key"},
    ],
)
def test_non_json_payload_rejected(value: object) -> None:
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(
            {"event_type": "task.created", "payload": {"bad": value}}
        )


def test_open_file_payload_rejected(tmp_path: Path) -> None:
    with (tmp_path / "input.txt").open("w") as file:
        with pytest.raises(ValidationError):
            EventEnvelope.model_validate(
                {"event_type": "task.created", "payload": {"file": file}}
            )


def test_fanout_order_and_close() -> None:
    async def run() -> None:
        bus = EventBus()
        events = [EventEnvelope(event_type=EventType.TASK_CREATED) for _ in range(3)]
        await bus.publish(events[0])  # Zero subscribers is successful.
        first, second = await bus.subscribe(), await bus.subscribe()
        for event in events:
            await bus.publish(event)
        for event in events:
            assert await first.get() is event
            assert await second.get() is event
        await first.close()
        await first.close()
        assert first.closed and bus.subscriber_count == 1
        with pytest.raises(SubscriptionClosedError):
            await first.get()
        await bus.publish(events[0])
        assert await second.get() is events[0]
        await second.close()
        assert bus.subscriber_count == 0

    asyncio.run(run())


def test_atomic_backpressure_and_retry() -> None:
    async def run() -> None:
        bus = EventBus(capacity=1)
        first, second = await bus.subscribe(), await bus.subscribe()
        event = EventEnvelope(event_type=EventType.TASK_CREATED)
        await bus.publish(event)
        assert await first.get() is event
        rejected = EventEnvelope(event_type=EventType.TASK_CREATED)
        with pytest.raises(EventBusFullError):
            await bus.publish(rejected)
        assert await second.get() is event
        # If partial fan-out occurred, first would receive the rejected event.
        await bus.publish(rejected)
        assert await first.get() is rejected and await second.get() is rejected
        await first.close()
        await second.close()

    asyncio.run(run())


def test_default_capacity_and_no_loss() -> None:
    async def run() -> None:
        bus = EventBus()
        subscription = await bus.subscribe()
        events = [EventEnvelope(event_type=EventType.TASK_CREATED) for _ in range(256)]
        for event in events:
            await bus.publish(event)
        with pytest.raises(EventBusFullError):
            await bus.publish(events[0])
        assert [await subscription.get() for _ in events] == events
        await subscription.close()

    asyncio.run(run())


@pytest.mark.parametrize("capacity", [0, -1, True, 1.5])
def test_invalid_capacity(capacity: object) -> None:
    with pytest.raises(ValueError):
        EventBus(capacity=capacity)  # type: ignore[arg-type]  # Invalid input.


def test_pending_receivers_wake_on_close_and_cancel_cleanly() -> None:
    async def run() -> None:
        bus = EventBus()
        subscription = await bus.subscribe()
        receivers = [asyncio.create_task(subscription.get()) for _ in range(2)]
        await asyncio.sleep(0)
        await subscription.close()
        for receiver in receivers:
            with pytest.raises(SubscriptionClosedError):
                await asyncio.wait_for(receiver, timeout=1)
        subscription = await bus.subscribe()
        receiver = asyncio.create_task(subscription.get())
        await asyncio.sleep(0)
        receiver.cancel()
        with pytest.raises(asyncio.CancelledError):
            await receiver
        event = EventEnvelope(event_type=EventType.TASK_CREATED)
        await bus.publish(event)
        assert await subscription.get() is event
        await subscription.close()

    asyncio.run(run())


def test_concurrent_publishers_have_same_order() -> None:
    async def run() -> None:
        bus = EventBus()
        first, second = await bus.subscribe(), await bus.subscribe()
        events = [EventEnvelope(event_type=EventType.TASK_CREATED) for _ in range(20)]
        await asyncio.gather(*(bus.publish(event) for event in events))
        first_events = [await first.get() for _ in events]
        second_events = [await second.get() for _ in events]
        assert first_events == second_events
        assert {event.event_id for event in first_events} == {
            event.event_id for event in events
        }
        await first.close()
        await second.close()

    asyncio.run(run())


def test_event_operations_are_isolated(tmp_path: Path) -> None:
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

# The caller explicitly creates its loop before guards; asyncio's loop itself
# uses an internal wakeup socket on Windows. Event operations create no sockets.
loop = asyncio.new_event_loop()

def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected I/O or hidden background work")

def guard_file_writes(event, args):
    if event == "open" and args[2] & (
        os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    ):
        forbidden()

class BlockBoundaries(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("fastapi", "starlette", "sqlalchemy", "alembic", "tauri",
                 "ollama", "colibri", "phoenix.api", "phoenix.persistence",
                 "phoenix.agents", "phoenix.tools", "phoenix.permissions",
                 "phoenix.voice", "phoenix.models", "phoenix.memory")
        if any(fullname == root or fullname.startswith(root + ".") for root in roots):
            raise AssertionError("Unexpected import: " + fullname)

sys.dont_write_bytecode = True
sys.addaudithook(guard_file_writes)
sys.meta_path.insert(0, BlockBoundaries())
os.mkdir = forbidden
Path.mkdir = forbidden
Path.write_text = forbidden
Path.write_bytes = forbidden
sqlite3.connect = forbidden
sqlite3.dbapi2.connect = forbidden
socket.socket.connect = forbidden
socket.socket.bind = forbidden
subprocess.Popen = forbidden
threading.Thread.start = forbidden

from phoenix.core.events import EventEnvelope, EventType
from phoenix.core.event_bus import EventBus

async def run():
    asyncio.create_task = forbidden
    asyncio.get_running_loop().create_task = forbidden
    event = EventEnvelope(event_type=EventType.TASK_CREATED, payload={"ok": True})
    bus = EventBus()
    first, second = await bus.subscribe(), await bus.subscribe()
    await bus.publish(event)
    assert await first.get() is event and await second.get() is event
    await first.close()
    await first.close()
    await second.close()
    assert bus.subscriber_count == 0
    assert not list(Path.cwd().iterdir())
    print("Event import/lifecycle isolation: PASS")

# Run exactly the test coroutine; no runner shutdown helper tasks are needed.
try:
    loop.run_until_complete(run())
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
    assert result.stdout.strip() == "Event import/lifecycle isolation: PASS"
