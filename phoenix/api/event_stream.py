"""PHX-103 ``WS /events``: the EventBus to WebSocket bridge (ADR-087).

``EventStream`` is the sink the runtime and the mission service publish to, and
the fan-out to authenticated WebSocket clients:

- ``publish`` never blocks. The bus refuses a publication instead of waiting
  when any subscriber's buffer is full; the refusal is counted in
  ``dropped_events`` and the mission carries on (INV-021, INV-022).
- Each connection gets its own bus subscription, drained at once into a
  bounded per-client buffer. When that buffer is full the client is too slow:
  it is closed with 1013 (try again later), its subscription is released, and
  every other client keeps receiving. The client reconnects and reloads state
  over REST, the source of truth. There is no replay in v1.
- Before sending, every event is re-checked against the PHX-103 payload
  allowlist; anything else is counted in ``rejected_events`` and never sent.
- ``close`` (backend shutdown) closes every connection with 1001.

Frames are server-to-client JSON envelopes only; client frames are ignored.
The WebSocket must already be accepted through ``accept_websocket`` (PHX-101).
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
DEFAULT_CLIENT_BUFFER: Final = 256
DEFAULT_CLOSE_TIMEOUT_SECONDS: Final = 5.0


@dataclass(eq=False)
class _Client:
    websocket: WebSocket
    buffer: asyncio.Queue[EventEnvelope]
    stopping: asyncio.Event = field(default_factory=asyncio.Event)
    close_code: int = WS_GOING_AWAY


class EventStream:
    """Publication sink plus per-connection bounded fan-out; one event loop."""

    def __init__(
        self,
        bus: EventBus,
        *,
        client_buffer: int = DEFAULT_CLIENT_BUFFER,
        close_timeout_seconds: float = DEFAULT_CLOSE_TIMEOUT_SECONDS,
    ) -> None:
        if type(client_buffer) is not int or client_buffer < 1:
            raise ValueError("Client buffer must be a positive integer")
        if not close_timeout_seconds > 0:
            raise ValueError("Close timeout must be positive")
        self._bus = bus
        self._client_buffer = client_buffer
        self._close_timeout = close_timeout_seconds
        self._clients: set[_Client] = set()
        self._closed = False
        self._dropped = 0
        self._rejected = 0
        self._slow = 0

    @property
    def connection_count(self) -> int:
        return len(self._clients)

    @property
    def dropped_events(self) -> int:
        """Publications the bus refused because a subscriber buffer was full."""
        return self._dropped

    @property
    def rejected_events(self) -> int:
        """Events that failed the payload allowlist and were never sent."""
        return self._rejected

    @property
    def slow_disconnects(self) -> int:
        """Clients closed with 1013 because their buffer filled."""
        return self._slow

    async def publish(self, event: EventEnvelope) -> None:
        """Never blocks and never raises for a full bus; the drop is counted."""
        try:
            await self._bus.publish(event)
        except EventBusFullError:
            self._dropped += 1

    async def serve(self, websocket: WebSocket) -> None:
        """Stream events to one accepted client until it leaves or is closed."""
        if self._closed:
            await self._close(websocket, WS_GOING_AWAY)
            return
        client = _Client(websocket, asyncio.Queue(self._client_buffer))
        subscription = await self._bus.subscribe()
        self._clients.add(client)
        tasks = {
            asyncio.create_task(self._pump(subscription, client)),
            asyncio.create_task(self._send(client)),
            asyncio.create_task(self._receive(client)),
            asyncio.create_task(client.stopping.wait()),
        }
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # Released first, so a stalled close never holds the bus.
            await subscription.close()
            self._clients.discard(client)
        if client.stopping.is_set():
            await self._close(websocket, client.close_code)

    async def _pump(self, subscription: Subscription, client: _Client) -> None:
        """Move bus events into the client's buffer; never waits on the client."""
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
            try:
                client.buffer.put_nowait(event)
            except asyncio.QueueFull:
                self._slow += 1
                client.close_code = WS_TRY_AGAIN_LATER
                client.stopping.set()
                await subscription.close()  # stop holding the bus at once
                return

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
        except Exception:
            pass  # already gone, or stalled: the server drops the transport

    async def close(self) -> None:
        """Backend shutdown: close every connection (1001); refuse new ones."""
        self._closed = True
        for client in tuple(self._clients):
            client.stopping.set()
        try:
            async with asyncio.timeout(self._close_timeout):
                while self._clients:
                    await asyncio.sleep(0.01)
        except TimeoutError:
            pass  # a stalled handler is dropped with the server's transports
