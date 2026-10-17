"""PHX-103 ``WS /events``: the EventBus to WebSocket bridge (ADR-087).

``EventStream`` is the sink the runtime and the mission service publish to, and
the fan-out to authenticated WebSocket clients:

- ``publish`` never blocks. ``EventBus`` publication is all-or-none, so the
  bridge holds ONE bus subscription (while any client is connected), with the
  bus's generous buffer, and a pump that drains it promptly, yielding after
  each event so the senders keep up. The pump fans each event out into a
  bounded buffer per client.
- A client whose own buffer is full is too slow: that client alone is closed
  with 1013 (try again later). If the bridge's bus subscription itself
  overflows (the bus refuses a publication), every client has missed an event,
  so every client is closed with 1013; nothing is dropped silently. Either way
  the client reconnects and reloads state over REST, the source of truth.
  There is no replay in v1.
- Before fan-out, every event is re-checked against the PHX-103 payload
  allowlist; anything else is counted in ``rejected_events`` and never sent.
- At most ``max_connections`` clients hold a slot (``reserve`` before accept);
  ``close`` (backend shutdown) closes every connection with 1001 and refuses
  new ones.

Frames are server-to-client JSON envelopes only; client frames are ignored.
The WebSocket must already be accepted through ``accept_websocket`` (PHX-101).
Each connection runs three tasks (sender, receiver, stop waiter); the pump is
shared.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Final

from starlette.websockets import WebSocket

from phoenix.agents.mission_events import EventPayloadError, check_event
from phoenix.core.event_bus import (
    EventBus,
    EventBusFullError,
    Subscription,
    SubscriptionClosedError,
)
from phoenix.core.events import EventEnvelope

EVENTS_PATH: Final = "/events"
WS_GOING_AWAY: Final = 1001
WS_TRY_AGAIN_LATER: Final = 1013
# The bridge's bus subscription; bursts larger than this close every client.
EVENT_BUS_CAPACITY: Final = 4096
DEFAULT_CLIENT_BUFFER: Final = 256
DEFAULT_MAX_CONNECTIONS: Final = 16
DEFAULT_CLOSE_TIMEOUT_SECONDS: Final = 5.0


def _retrieve(task: "asyncio.Task[object]") -> None:
    if not task.cancelled():
        task.exception()  # retrieved: a finished child never warns


@dataclass(eq=False)
class _Client:
    websocket: WebSocket
    buffer: asyncio.Queue[EventEnvelope]
    stopping: asyncio.Event = field(default_factory=asyncio.Event)
    close_code: int = WS_GOING_AWAY

    def stop(self, code: int) -> None:
        if not self.stopping.is_set():
            self.close_code = code
            self.stopping.set()


class EventStream:
    """Publication sink plus one shared, bounded fan-out; one event loop."""

    def __init__(
        self,
        bus: EventBus,
        *,
        client_buffer: int = DEFAULT_CLIENT_BUFFER,
        max_connections: int = DEFAULT_MAX_CONNECTIONS,
        close_timeout_seconds: float = DEFAULT_CLOSE_TIMEOUT_SECONDS,
    ) -> None:
        for value in (client_buffer, max_connections):
            if type(value) is not int or value < 1:
                raise ValueError("Buffers and connection caps must be positive")
        if not close_timeout_seconds > 0:
            raise ValueError("Close timeout must be positive")
        self._bus = bus
        self._client_buffer = client_buffer
        self._max_connections = max_connections
        self._close_timeout = close_timeout_seconds
        self._lock = asyncio.Lock()
        self._clients: set[_Client] = set()
        self._subscription: Subscription | None = None
        self._pump_task: asyncio.Task[None] | None = None
        self._slots = 0
        self._closed = False
        self._dropped = 0
        self._rejected = 0
        self._slow = 0
        self._overflows = 0
        self._refused = 0
        self._failed_closes = 0
        self._closing: set[asyncio.Task[None]] = set()

    @property
    def connection_count(self) -> int:
        return len(self._clients)

    @property
    def dropped_events(self) -> int:
        """Publications the bus refused (every client was then closed 1013)."""
        return self._dropped

    @property
    def rejected_events(self) -> int:
        """Events that failed the payload allowlist and were never sent."""
        return self._rejected

    @property
    def slow_disconnects(self) -> int:
        """Clients closed with 1013 because their own buffer filled."""
        return self._slow

    @property
    def overflow_disconnects(self) -> int:
        """Bus refusals that closed every client with 1013."""
        return self._overflows

    @property
    def refused_connections(self) -> int:
        """Connections refused because every slot was taken."""
        return self._refused

    @property
    def reserved_connections(self) -> int:
        """Slots held; a slot is released when its handler has fully ended."""
        return self._slots

    @property
    def failed_closes(self) -> int:
        """Closes that failed unexpectedly (not already-gone or stalled)."""
        return self._failed_closes

    async def publish(self, event: EventEnvelope) -> None:
        """Never blocks or raises; a bus refusal closes every client (1013)."""
        try:
            await self._bus.publish(event)
        except EventBusFullError:
            self._dropped += 1
            if self._clients:
                self._overflows += 1
            for client in tuple(self._clients):
                client.stop(WS_TRY_AGAIN_LATER)

    def reserve(self) -> bool:
        """Take a connection slot before accepting; False when full or closed."""
        if self._closed or self._slots >= self._max_connections:
            self._refused += 1
            return False
        self._slots += 1
        return True

    def release(self) -> None:
        self._slots = max(0, self._slots - 1)

    async def serve(self, websocket: WebSocket) -> None:
        """Stream events to one accepted client holding a reserved slot."""
        client = _Client(websocket, asyncio.Queue(self._client_buffer))
        try:
            if not await self._attach(client):
                await self._close(websocket, WS_GOING_AWAY)
                return
            tasks = {
                asyncio.create_task(self._send(client)),
                asyncio.create_task(self._receive(client)),
                asyncio.create_task(client.stopping.wait()),
            }
            for task in tasks:
                task.add_done_callback(_retrieve)
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                self._detach(client)
                # asyncio.wait, never gather: gather would re-raise a child's
                # message-less CancelledError in place of a cancellation of this
                # handler (e.g. an anyio cancel scope's), which the canceller
                # then fails to recognise and lets escape.
                await asyncio.wait(tasks)
            if client.stopping.is_set():
                await self._close(websocket, client.close_code)
        finally:
            self.release()

    async def _attach(self, client: _Client) -> bool:
        async with self._lock:  # one subscribe at a time
            if self._subscription is None and not self._closed:
                subscription = await self._bus.subscribe()
                self._subscription = subscription
                self._pump_task = asyncio.create_task(self._pump(subscription))
            # Checked after any contended subscribe: a close() that signalled
            # the clients meanwhile cannot miss this one.
            if self._closed:
                if not self._clients:
                    self._stop_pump()
                return False
            self._clients.add(client)
            return True

    def _detach(self, client: _Client) -> None:
        """Synchronous, so a cancelled handler still releases everything."""
        self._clients.discard(client)
        if not self._clients:
            self._stop_pump()

    def _stop_pump(self) -> None:
        """Stop the pump and release the bus subscription, without awaiting.

        The close runs as its own task, so it completes even if the caller is
        being cancelled: the bus never keeps a dead subscriber that would fill
        and refuse every publication.
        """
        pump, subscription = self._pump_task, self._subscription
        self._subscription = self._pump_task = None
        if pump is not None:
            pump.cancel()
        if subscription is not None:
            closing = asyncio.create_task(subscription.close())
            self._closing.add(closing)
            closing.add_done_callback(self._closing.discard)

    async def _pump(self, subscription: Subscription) -> None:
        """Drain the bus promptly into each client's buffer; never waits on one."""
        while True:
            try:
                event = await subscription.get()
            except SubscriptionClosedError:
                return
            try:
                check_event(event)
            except EventPayloadError:
                self._rejected += 1
                continue
            for client in tuple(self._clients):
                if client.stopping.is_set():
                    continue
                try:
                    client.buffer.put_nowait(event)
                except asyncio.QueueFull:
                    self._slow += 1
                    client.stop(WS_TRY_AGAIN_LATER)
            await asyncio.sleep(0)  # let the senders keep up with a burst

    async def _send(self, client: _Client) -> None:
        while True:
            event = await client.buffer.get()
            await client.websocket.send_text(event.model_dump_json())

    async def _receive(self, client: _Client) -> None:
        """Waits for the client to leave; v1 ignores client frames."""
        while True:
            message = await client.websocket.receive()
            if message["type"] == "websocket.disconnect":
                return

    async def _close(self, websocket: WebSocket, code: int) -> None:
        try:
            async with asyncio.timeout(self._close_timeout):
                await websocket.close(code=code)
        except (RuntimeError, OSError):
            # Expected: already closed or disconnected (Starlette RuntimeError,
            # uvicorn ClientDisconnected) or stalled (TimeoutError); the server
            # drops the transport.
            pass
        except Exception:
            self._failed_closes += 1  # unexpected: counted, never hidden

    async def close(self) -> None:
        """Backend shutdown: close every connection (1001); refuse new ones."""
        self._closed = True
        for client in tuple(self._clients):
            client.stop(WS_GOING_AWAY)
        try:
            async with asyncio.timeout(self._close_timeout):
                while self._clients:
                    await asyncio.sleep(0.01)
        except TimeoutError:
            pass  # a stalled handler is dropped with the server's transports
