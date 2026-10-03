"""Memory-only asyncio fan-out on one event loop, with no hidden workers."""

import asyncio

from phoenix.core.events import EventEnvelope


class EventBusFullError(Exception):
    """Publication rejected for all subscribers because a buffer is full."""


class SubscriptionClosedError(Exception):
    """A receiver attempted to read a closed subscription."""


class Subscription:
    """Explicit lifetime; closing discards queued events and wakes receivers."""

    def __init__(self, bus: "EventBus", capacity: int) -> None:
        self._bus = bus
        self._queue: asyncio.Queue[EventEnvelope | None] = asyncio.Queue(capacity)
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    async def get(self) -> EventEnvelope:
        if self._closed:
            raise SubscriptionClosedError("Subscription is closed")
        event = await self._queue.get()
        if event is None:
            # Keep the close marker available to all already-waiting receivers.
            self._queue.put_nowait(None)
            raise SubscriptionClosedError("Subscription is closed")
        return event

    async def close(self) -> None:
        async with self._bus._lock:
            if self._closed:
                return
            self._closed = True
            self._bus._subscribers.pop(self, None)
            while not self._queue.empty():
                self._queue.get_nowait()
            self._queue.put_nowait(None)


class EventBus:
    """All-or-none publication; not thread-safe or cross-event-loop safe."""

    def __init__(self, capacity: int = 256) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("EventBus capacity must be a positive integer")
        self._capacity = capacity
        self._lock = asyncio.Lock()
        self._subscribers: dict[Subscription, None] = {}

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def subscribe(self) -> Subscription:
        async with self._lock:
            subscription = Subscription(self, self._capacity)
            self._subscribers[subscription] = None
            return subscription

    async def publish(self, event: EventEnvelope) -> None:
        async with self._lock:
            if any(sub._queue.full() for sub in self._subscribers):
                raise EventBusFullError(
                    "Publication rejected: subscriber buffer is full"
                )
            # No await from capacity check through fan-out: another operation
            # cannot interleave and cause partial delivery on this event loop.
            for subscription in self._subscribers:
                subscription._queue.put_nowait(event)
