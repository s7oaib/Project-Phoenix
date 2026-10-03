"""Global heavy-generation leases; coordination never implies model residency."""

import asyncio
from datetime import UTC, datetime
from typing import Self

from pydantic import AwareDatetime, Field, TypeAdapter, field_validator

from phoenix.models.provider import Identifier, PositiveInt, ProviderModel


class ModelLifecycleError(Exception):
    """Explicit lifecycle coordination misuse, independent of provider failure."""


class LeaseSnapshot(ProviderModel):
    """Immutable allocation evidence; no physical model or permission state."""

    provider_id: Identifier
    model_id: Identifier
    operation_id: Identifier
    acquired_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("acquired_at")
    @classmethod
    def normalize_utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class GenerationLease:
    """Read-only identity with idempotent synchronous release on its owning loop.

    Use async with immediately after acquisition, or explicitly release in a
    finally block. A manually abandoned lease is not automatically reclaimed.
    """

    __slots__ = ("_manager", "_snapshot", "_released", "_entered")

    def __init__(
        self, manager: "ModelLifecycleManager", snapshot: LeaseSnapshot
    ) -> None:
        self._manager = manager
        self._snapshot = snapshot
        self._released = False
        self._entered = False

    @property
    def snapshot(self) -> LeaseSnapshot:
        return self._snapshot

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if not self._released:
            self._manager._release(self)
            self._released = True

    async def __aenter__(self) -> Self:
        if self._released:
            raise ModelLifecycleError("Cannot enter a released generation lease")
        if self._entered:
            raise ModelLifecycleError("Generation lease context is already entered")
        self._manager._check_loop()
        self._entered = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.release()


class ModelLifecycleManager:
    """One event loop, one global capacity; no inference, registry or scheduler.

    Default capacity is one for BLAZE-6. BoundedSemaphore handles caller waiting
    and cancellation; no strict fairness/priority guarantee is added. Snapshots
    cover committed leases. available_count is capacity minus committed leases,
    including slots being handed to waking waiters; it is not an admission promise.
    The manager owns no workers or external resources and needs no close method.
    """

    def __init__(self, heavy_generation_capacity: int = 1) -> None:
        self._capacity = TypeAdapter(PositiveInt).validate_python(
            heavy_generation_capacity
        )
        self._permits = asyncio.BoundedSemaphore(self._capacity)
        self._active: dict[str, GenerationLease] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def active_count(self) -> int:
        return len(self._active)

    @property
    def available_count(self) -> int:
        return self._capacity - self.active_count

    def active_leases(self) -> tuple[LeaseSnapshot, ...]:
        return tuple(self._active[key].snapshot for key in sorted(self._active))

    def is_active(self, operation_id: str) -> bool:
        identity = TypeAdapter(Identifier).validate_python(operation_id)
        return identity in self._active

    def _check_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise ModelLifecycleError("Lifecycle manager belongs to another event loop")
        self._loop = loop

    async def acquire_heavy(
        self, *, provider_id: str, model_id: str, operation_id: str
    ) -> GenerationLease:
        identifier = TypeAdapter(Identifier)
        provider_id = identifier.validate_python(provider_id)
        model_id = identifier.validate_python(model_id)
        operation_id = identifier.validate_python(operation_id)
        self._check_loop()
        if operation_id in self._active:
            raise ModelLifecycleError("Duplicate active generation operation ID")
        await self._permits.acquire()
        try:
            # Another waiter may have committed this ID while we were suspended.
            if operation_id in self._active:
                raise ModelLifecycleError("Duplicate active generation operation ID")
            snapshot = LeaseSnapshot(
                provider_id=provider_id, model_id=model_id, operation_id=operation_id
            )
            lease = GenerationLease(self, snapshot)
            self._active[operation_id] = lease
            return lease
        except BaseException:
            # No await separates permit acquisition, validation and commit.
            self._permits.release()
            raise

    def _release(self, lease: GenerationLease) -> None:
        self._check_loop()
        operation_id = lease.snapshot.operation_id
        if self._active.get(operation_id) is not lease:
            raise ModelLifecycleError("Generation lease is not active in this manager")
        del self._active[operation_id]
        self._permits.release()
