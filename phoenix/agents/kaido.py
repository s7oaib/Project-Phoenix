"""PHX-119 KAIDO fail-safe in missions (ADR-155..ADR-158).

KAIDO is degraded/fail-safe mode only (INV-010). ``KaidoMonitor`` turns the
same provider-health signal readiness and missions already probe, plus an
optional cheap resource sample, into an engaged/released state with
hysteresis: it engages on the first bad observation and releases only after
``release_after`` consecutive clear ones (resources above the release band).
Every engagement is a PHX-028 ``KaidoFailSafe`` decision, so the trigger, the
reason and the capability list stay explicit and immutable (INV-022).

A KAIDO answer is one generation on the T0 tier through the structured
(thinking-off) provider, inside an 8K-token context budget, with no tools,
no plan and no LEVI check: it is labelled degraded and is never verified.
KAIDO grants no permission and never touches ToolRegistry or PermissionEngine;
it cannot rewrite a failed verification into success (INV-008).
"""

from collections.abc import Callable, Mapping
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from phoenix.agents.failsafe import (
    FailSafeCapability,
    FailSafeDecision,
    FailSafeRequest,
    FailSafeTrigger,
    KaidoFailSafe,
)
from phoenix.agents.mission_events import EventPublisher, EventSink
from phoenix.agents.recovery import RecoveryResult
from phoenix.agents.responders import ModelBinding, _generate
from phoenix.core.events import EventType
from phoenix.models.provider import Identifier, ProviderHealthStatus

KAIDO_AGENT_ID: Final = "failsafe"
KAIDO_ROLE: Final = "kaido"
KAIDO_TIER: Final = "failsafe"  # the tier-table row: T0 only
# docs/05 and MASTER_SPEC: KAIDO context is 8K tokens.
KAIDO_CONTEXT_TOKENS: Final = 8192
KAIDO_MAX_OUTPUT_TOKENS: Final = 1024
# Conservative 3 characters per token for the system prompt plus the request.
KAIDO_MAX_INPUT_CHARS: Final = (KAIDO_CONTEXT_TOKENS - KAIDO_MAX_OUTPUT_TOKENS) * 3
# Basic conversation, diagnostics and recovery guidance; never tool routing.
KAIDO_CAPABILITIES: Final = (
    FailSafeCapability.BASIC_CONVERSATION,
    FailSafeCapability.DIAGNOSTICS,
    FailSafeCapability.RECOVERY_GUIDANCE,
)
# The triggers PHX-119 can evidence; deep-provider and subsystem degradation
# have no mission signal yet.
KAIDO_TRIGGERS: Final = (
    FailSafeTrigger.PRIMARY_MODEL_PROVIDER_UNAVAILABLE,
    FailSafeTrigger.RESOURCE_PRESSURE,
    FailSafeTrigger.RECOVERY_EXHAUSTED,
)
KAIDO_PROMPT_VERSION: Final = "kaido-prompts-v1"
# Owner approval needed (PHX-119 ticket): the system prompt and the label.
# Kept out of prompts.py so PROMPT_SET_VERSION (v4) is unchanged; the first
# line is the stable role marker, as in prompts.py.
KAIDO_SYSTEM: Final = """PHOENIX ROLE: kaido
You are KAIDO, PHOENIX's fail-safe assistant. PHOENIX is running in degraded
mode on a small local model, with no tools and no verification. Answer the
user's message briefly and plainly in one reply. If the request needs files,
tools, long reasoning or current facts, say that PHOENIX is in degraded mode
and suggest trying again when normal mode returns. Never claim to have run
tools, read files, verified anything or been granted any permission."""
KAIDO_ANSWER_LABEL: Final = (
    "[KAIDO degraded mode - unverified answer from the fail-safe model]"
)
_REASONS: Final[Mapping[FailSafeTrigger, str]] = {
    FailSafeTrigger.PRIMARY_MODEL_PROVIDER_UNAVAILABLE: (
        "No model provider reported healthy"
    ),
    FailSafeTrigger.RESOURCE_PRESSURE: "Free memory fell below the KAIDO threshold",
    FailSafeTrigger.RECOVERY_EXHAUSTED: "MAHORAGA's mission recovery budget is spent",
}
# KAIDO may answer on a degraded provider; an unavailable one cannot answer.
KAIDO_USABLE: Final = frozenset(
    {ProviderHealthStatus.HEALTHY, ProviderHealthStatus.DEGRADED}
)


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


KaidoOutcome = Literal["answered", "failed", "unavailable", "declined"]
KaidoScope = Literal["process", "mission"]


class KaidoRecord(_Value):
    """One KAIDO engagement in MissionTrace: closed values and IDs only.

    ``answered``: KAIDO's T0 reply is the mission's answer (never verified).
    ``unavailable``: no usable provider or registered T0 model; ``failed``:
    the generation failed; ``declined``: the request exceeds the 8K budget.
    Only ``answered`` carries an answer; the others keep the mission failed.
    """

    failsafe_id: Identifier
    trigger: FailSafeTrigger
    scope: KaidoScope
    capabilities: tuple[FailSafeCapability, ...] = KAIDO_CAPABILITIES
    recovery_id: Identifier | None = None
    outcome: KaidoOutcome
    verified: Literal[False] = False

    @model_validator(mode="after")
    def consistent_record(self) -> Self:
        if self.trigger not in KAIDO_TRIGGERS:
            raise ValueError("Trigger is not a PHX-119 KAIDO trigger")
        if (self.trigger == FailSafeTrigger.RECOVERY_EXHAUSTED) != (
            self.recovery_id is not None
        ):
            raise ValueError("Recovery reference contradicts trigger")
        if (self.trigger == FailSafeTrigger.RECOVERY_EXHAUSTED) != (
            self.scope == "mission"
        ):
            raise ValueError("Only recovery exhaustion is mission-scoped")
        if FailSafeCapability.SAFE_LIMITED_TOOL_ROUTING in self.capabilities:
            raise ValueError("KAIDO in missions routes no tools")
        return self

    @property
    def answered(self) -> bool:
        return self.outcome == "answered"


class ResourceSample(_Value):
    """Free memory in MiB; None means not measured. Evidence only."""

    free_ram_mib: int | None = Field(default=None, ge=0, strict=True)
    free_vram_mib: int | None = Field(default=None, ge=0, strict=True)


ResourceProbe = Callable[[], ResourceSample]


def system_ram_sample() -> ResourceSample:
    """Available system RAM from psutil: no subprocess, no GPU query."""
    import psutil

    return ResourceSample(free_ram_mib=int(psutil.virtual_memory().available) >> 20)


class KaidoPolicy(_Value):
    """Engage below ``engage_*``; count clear only at or above ``release_*``."""

    release_after: int = Field(default=2, ge=1, le=10, strict=True)
    engage_free_ram_mib: int = Field(default=2048, ge=0, strict=True)
    release_free_ram_mib: int = Field(default=3072, ge=0, strict=True)
    engage_free_vram_mib: int = Field(default=512, ge=0, strict=True)
    release_free_vram_mib: int = Field(default=1024, ge=0, strict=True)

    @model_validator(mode="after")
    def release_band(self) -> Self:
        if (
            self.release_free_ram_mib < self.engage_free_ram_mib
            or self.release_free_vram_mib < self.engage_free_vram_mib
        ):
            raise ValueError("Release thresholds sit at or above engage thresholds")
        return self


class KaidoStatus(_Value):
    """What readiness and the UI see: engaged or not, and why."""

    engaged: bool
    failsafe_id: Identifier | None = None
    trigger: FailSafeTrigger | None = None
    capabilities: tuple[FailSafeCapability, ...] = ()
    clear_observations: int = Field(default=0, ge=0, strict=True)
    release_after: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        if self.engaged != (self.failsafe_id is not None) or self.engaged != (
            self.trigger is not None
        ):
            raise ValueError("Only an engaged status carries a decision")
        if self.engaged != bool(self.capabilities):
            raise ValueError("Only an engaged status carries capabilities")
        return self


def activate(
    failsafe: KaidoFailSafe,
    trigger: FailSafeTrigger,
    recovery: RecoveryResult | None = None,
) -> FailSafeDecision:
    """One PHX-028 decision with fixed reason text and KAIDO's capabilities."""
    return failsafe.activate(
        FailSafeRequest(
            trigger=trigger,
            reason=_REASONS[trigger],
            requested_capabilities=KAIDO_CAPABILITIES,
            recovery_result=recovery,
        )
    )


class KaidoMonitor:
    """Process-wide KAIDO state with hysteresis; one per backend.

    ``observe`` takes the provider statuses a caller just probed (readiness
    or a mission's once-per-mission probe) and samples resources itself.
    Engaging publishes ``phoenix.degraded`` and releasing ``phoenix.ready``,
    both correlated by the engagement's failsafe ID. A failing resource probe
    is no evidence: it neither engages nor counts as clear.
    """

    def __init__(
        self,
        *,
        policy: KaidoPolicy | None = None,
        resource_probe: ResourceProbe | None = None,
        failsafe: KaidoFailSafe | None = None,
        events: EventSink | None = None,
    ) -> None:
        self._policy = KaidoPolicy() if policy is None else policy
        self._probe = resource_probe
        self._failsafe = KaidoFailSafe() if failsafe is None else failsafe
        self._events = EventPublisher(events)
        self._decision: FailSafeDecision | None = None
        self._clear = 0

    @property
    def policy(self) -> KaidoPolicy:
        return self._policy

    @property
    def failsafe(self) -> KaidoFailSafe:
        return self._failsafe

    @property
    def decision(self) -> FailSafeDecision | None:
        return self._decision

    @property
    def dropped_events(self) -> int:
        return self._events.dropped

    def status(self) -> KaidoStatus:
        decision = self._decision
        return KaidoStatus(
            engaged=decision is not None,
            failsafe_id=None if decision is None else decision.failsafe_id,
            trigger=None if decision is None else decision.trigger,
            capabilities=() if decision is None else decision.enabled_capabilities,
            clear_observations=self._clear if decision is not None else 0,
            release_after=self._policy.release_after,
        )

    def _sample(self) -> ResourceSample | None:
        if self._probe is None:
            return ResourceSample()
        try:
            sample = self._probe()
            return ResourceSample.model_validate(sample.model_dump())
        except Exception:
            return None

    def _pressure(self, sample: ResourceSample, *, engage: bool) -> bool:
        policy = self._policy
        ram = policy.engage_free_ram_mib if engage else policy.release_free_ram_mib
        vram = policy.engage_free_vram_mib if engage else policy.release_free_vram_mib
        return (sample.free_ram_mib is not None and sample.free_ram_mib < ram) or (
            sample.free_vram_mib is not None and sample.free_vram_mib < vram
        )

    async def observe(
        self, provider_statuses: Mapping[str, ProviderHealthStatus]
    ) -> KaidoStatus:
        """Record one observation; engage or release KAIDO as the policy says."""
        provider_down = ProviderHealthStatus.HEALTHY not in provider_statuses.values()
        sample = self._sample()
        if provider_down:
            trigger: FailSafeTrigger | None = (
                FailSafeTrigger.PRIMARY_MODEL_PROVIDER_UNAVAILABLE
            )
        elif sample is not None and self._pressure(sample, engage=True):
            trigger = FailSafeTrigger.RESOURCE_PRESSURE
        else:
            trigger = None
        if trigger is not None:
            self._clear = 0
            if self._decision is None:
                self._decision = activate(self._failsafe, trigger)
                await self._publish(
                    EventType.PHOENIX_DEGRADED,
                    self._decision.failsafe_id,
                    trigger=trigger.value,
                )
            return self.status()
        if self._decision is None:
            return self.status()
        clear = sample is not None and not self._pressure(sample, engage=False)
        self._clear = self._clear + 1 if clear else 0
        if self._clear >= self._policy.release_after:
            released = self._decision
            self._decision = None
            self._clear = 0
            await self._publish(EventType.PHOENIX_READY, released.failsafe_id)
        return self.status()

    async def _publish(
        self, event_type: EventType, failsafe_id: str, **fields: str
    ) -> None:
        await self._events.publish(
            event_type,
            {"failsafe_id": failsafe_id, "kaido_scope": "process", **fields},
            correlation_id=failsafe_id,
            agent_id=KAIDO_AGENT_ID,
        )


async def kaido_answer(binding: ModelBinding, text: str) -> str:
    """KAIDO's single reply, labelled degraded and unverified."""
    reply = await _generate(binding, KAIDO_ROLE, KAIDO_SYSTEM, text)
    return f"{KAIDO_ANSWER_LABEL}\n\n{reply}"


def within_budget(text: str) -> bool:
    """The request and KAIDO's system prompt fit the 8K-token context."""
    return len(KAIDO_SYSTEM) + len(text) <= KAIDO_MAX_INPUT_CHARS
