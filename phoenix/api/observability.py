"""PHX-115 runtime metrics and event logs (ADR-145 to ADR-148).

``MetricsCollector`` sits between PHOENIX's publishers (the runtime and the
mission service) and the PHX-103 ``EventStream``. For every event it

1. re-checks the PHX-103 allowlist (an off-list event is counted, never used),
2. updates bounded in-memory aggregates and writes one structured log record
   (``phoenix.events``) carrying the correlation, mission, task, step and agent
   IDs and the allowlisted payload, which is content-free by construction,
3. forwards the event unchanged to the stream; observing never blocks, raises
   into or alters a mission (INV-021, INV-022).

``snapshot()`` serves ``GET /metrics``. It adds, lazily and boundedly, what the
events do not carry: token counts, generation durations, per-model usage and
the provider health each mission saw, from the stored ``MissionTrace`` of
missions that ended since the last snapshot. It samples process and system RAM
and process CPU with the standard library (``ctypes`` on Windows, ``/proc``
elsewhere; no new dependency) and VRAM from Ollama's own placement report
(``GET /api/ps``), bounded by the probe timeout.

TTFT: missions use non-streaming generation, whose time to first token is
unknown (ADR-011/ADR-016 forbid inferring it). The reported ``ttft`` is the
mission's time to first visible answer, from ``queued`` to ``completed``: in a
non-streaming product the user's first answer token arrives with the whole
answer. Its basis is named in the response.

Nothing here grants authority or changes routing; numbers are evidence only.
"""

import asyncio
import ctypes
import logging
import mmap
import os
import re
import sys
import time
from collections import Counter, OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Final, Literal

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from phoenix.agents.mission_events import EventPayloadError, EventSink, check_event
from phoenix.agents.runtime import MissionTrace
from phoenix.api.structured_logging import log_entry
from phoenix.core.events import EventEnvelope, EventSeverity, EventType

LOGGER_NAME: Final = "phoenix.events"
LATENCY_WINDOW: Final = 256
MAX_TRACKED_MISSIONS: Final = 256
MAX_TRACES_PER_SNAPSHOT: Final = 64
MAX_MODELS: Final = 16
RECENT_LOGS: Final = 64
MAX_PS_BYTES: Final = 256 * 1024
TERMINAL: Final = frozenset(
    {"completed", "failed", "cancelled", "waiting_permission", "superseded"}
)
_TRACED: Final = frozenset({"completed", "failed", "cancelled", "waiting_permission"})
_MODEL_NAME: Final = re.compile(r"[A-Za-z0-9._:/@+-]{1,128}")
_LEVELS: Final[Mapping[EventSeverity, int]] = {
    EventSeverity.DEBUG: logging.DEBUG,
    EventSeverity.INFO: logging.INFO,
    EventSeverity.WARNING: logging.WARNING,
    EventSeverity.ERROR: logging.ERROR,
    EventSeverity.CRITICAL: logging.CRITICAL,
}

TraceLoader = Callable[[str], Awaitable[MissionTrace | None]]


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StageLatency(_Out):
    """``count`` is every observation; percentiles cover the last 256."""

    stage: str
    count: int = Field(ge=0)
    p50_ms: int | None = None
    p95_ms: int | None = None
    max_ms: int | None = None
    mean_ms: float | None = None


class ModelUsage(_Out):
    provider_id: str
    model_id: str
    generations: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class ProviderHealthOut(_Out):
    provider_id: str
    status: str


class GenerationMetrics(_Out):
    missions_ingested: int = Field(ge=0)
    generations: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    duration: StageLatency
    output_tokens_per_second_mean: float | None = None
    models: tuple[ModelUsage, ...]
    provider_health: tuple[ProviderHealthOut, ...]


class ToolMetrics(_Out):
    calls: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    success_rate: float | None = None
    by_status: dict[str, int]


class PermissionMetrics(_Out):
    requested: int = Field(ge=0)
    approved: int = Field(ge=0)
    denied: int = Field(ge=0)
    policy_denied: int = Field(ge=0)
    by_risk: dict[str, int]


class VerificationMetrics(_Out):
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    by_verdict: dict[str, int]


class RecoveryMetrics(_Out):
    started: int = Field(ge=0)
    completed: int = Field(ge=0)
    exhausted: int = Field(ge=0)
    by_action: dict[str, int]
    by_outcome: dict[str, int]


class VramModel(_Out):
    model_id: str
    size_bytes: int = Field(ge=0)
    vram_bytes: int = Field(ge=0)


class VramReport(_Out):
    """Ollama's own placement report; ``unavailable`` when it cannot be read."""

    status: Literal["ok", "unavailable"]
    total_vram_bytes: int | None = None
    models: tuple[VramModel, ...] = ()


class ResourceMetrics(_Out):
    process_rss_bytes: int | None = None
    system_ram_total_bytes: int | None = None
    system_ram_available_bytes: int | None = None
    process_cpu_percent: float | None = None
    vram: VramReport


class EventCounters(_Out):
    observed: int = Field(ge=0)
    rejected: int = Field(ge=0)
    stream_dropped: int = Field(ge=0)
    stream_rejected: int = Field(ge=0)


class LogOut(_Out):
    id: str
    ts: str
    level: str
    subsystem: str
    message: str
    correlation_id: str | None = None
    mission_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    agent_id: str | None = None
    event_type: str | None = None


class MetricsOut(_Out):
    """``GET /metrics``: counts, timings, IDs and closed values only."""

    generated_at: AwareDatetime
    uptime_seconds: int = Field(ge=0)
    ttft_basis: Literal["mission_first_answer"] = "mission_first_answer"
    missions: dict[str, int]
    latency: tuple[StageLatency, ...]
    generation: GenerationMetrics
    tools: ToolMetrics
    permissions: PermissionMetrics
    verification: VerificationMetrics
    recoveries: RecoveryMetrics
    resources: ResourceMetrics
    events: EventCounters
    logs: tuple[LogOut, ...]


# ---- resources (standard library only)


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


def _windows_memory() -> tuple[int | None, int | None, int | None]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    total = available = rss = None
    if kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        total, available = int(status.ullTotalPhys), int(status.ullAvailPhys)
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
    get_info = kernel32.K32GetProcessMemoryInfo
    get_info.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_ProcessMemoryCounters),
        ctypes.c_ulong,
    ]
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    if get_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        rss = int(counters.WorkingSetSize)
    return rss, total, available


def _proc_memory() -> tuple[int | None, int | None, int | None]:
    rss = total = available = None
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            rss = int(statm.read().split()[1]) * mmap.PAGESIZE
    except (OSError, ValueError, IndexError):
        pass
    try:
        with open("/proc/meminfo", encoding="ascii") as meminfo:
            fields = dict(line.split(":", 1) for line in meminfo if ":" in line)
        total = int(fields["MemTotal"].split()[0]) * 1024
        available = int(fields["MemAvailable"].split()[0]) * 1024
    except (OSError, ValueError, KeyError, IndexError):
        pass
    return rss, total, available


def sample_memory() -> tuple[int | None, int | None, int | None]:
    """(process RSS, system total, system available) in bytes; None if unknown."""
    try:
        if sys.platform == "win32":
            return _windows_memory()
        return _proc_memory()
    except Exception:
        return None, None, None


# ---- VRAM from Ollama's placement report


def parse_ollama_ps(body: object) -> VramReport:
    """``{"models": [{"name", "size", "size_vram"}, ...]}``; anything else is
    unavailable. Names are kept only when they look like model tags."""
    if not isinstance(body, dict) or not isinstance(body.get("models"), list):
        return VramReport(status="unavailable")
    models: list[VramModel] = []
    for item in body["models"][:MAX_MODELS]:
        if not isinstance(item, dict):
            return VramReport(status="unavailable")
        name, size, vram = item.get("name"), item.get("size"), item.get("size_vram")
        if (
            not isinstance(name, str)
            or _MODEL_NAME.fullmatch(name) is None
            or type(size) is not int
            or type(vram) is not int
            or size < 0
            or vram < 0
        ):
            return VramReport(status="unavailable")
        models.append(VramModel(model_id=name, size_bytes=size, vram_bytes=vram))
    return VramReport(
        status="ok",
        total_vram_bytes=sum(model.vram_bytes for model in models),
        models=tuple(models),
    )


async def probe_ollama_vram(
    base_url: str, timeout: float, *, transport: httpx.AsyncBaseTransport | None = None
) -> VramReport:
    """One bounded ``GET /api/ps``; every failure is ``unavailable``, never raised."""
    try:
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=timeout,
                trust_env=False,
                follow_redirects=False,
                transport=transport,
            ) as client:
                response = await client.get("/api/ps")
                if response.status_code != 200 or len(response.content) > MAX_PS_BYTES:
                    return VramReport(status="unavailable")
                return parse_ollama_ps(response.json())
    except Exception:
        return VramReport(status="unavailable")


async def _no_vram() -> VramReport:
    return VramReport(status="unavailable")


# ---- aggregation


class _Window:
    def __init__(self) -> None:
        self.count = 0
        self.values: deque[int] = deque(maxlen=LATENCY_WINDOW)

    def add(self, value: int) -> None:
        self.count += 1
        self.values.append(value)

    def summary(self, stage: str) -> StageLatency:
        if not self.values:
            return StageLatency(stage=stage, count=self.count)
        ordered = sorted(self.values)

        def rank(fraction: float) -> int:
            index = max(0, -(-int(fraction * 100) * len(ordered) // 100) - 1)
            return ordered[min(index, len(ordered) - 1)]

        return StageLatency(
            stage=stage,
            count=self.count,
            p50_ms=rank(0.50),
            p95_ms=rank(0.95),
            max_ms=ordered[-1],
            mean_ms=round(sum(ordered) / len(ordered), 1),
        )


def _remember(
    table: "OrderedDict[str, float]", key: str, value: float, limit: int
) -> None:
    table[key] = value
    table.move_to_end(key)
    while len(table) > limit:
        table.popitem(last=False)


def _str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _ms(payload: Mapping[str, object]) -> int | None:
    value = payload.get("duration_ms")
    return value if type(value) is int else None


def _message(event: EventEnvelope) -> str:
    parts = [event.event_type.value]
    for key, value in event.payload.items():
        if isinstance(value, bool):
            parts.append(f"{key}={'true' if value else 'false'}")
        elif isinstance(value, str | int):
            parts.append(f"{key}={value}")
    return " ".join(parts)


class MetricsCollector:
    """An ``EventSink`` that records metrics and logs, then forwards (one loop)."""

    def __init__(
        self,
        sink: EventSink,
        *,
        vram_probe: Callable[[], Awaitable[VramReport]] | None = None,
        memory_sampler: Callable[
            [], tuple[int | None, int | None, int | None]
        ] = sample_memory,
        clock: Callable[[], float] = time.monotonic,
        cpu_clock: Callable[[], float] = time.process_time,
        logger: logging.Logger | None = None,
    ) -> None:
        self._sink = sink
        self._vram_probe = _no_vram if vram_probe is None else vram_probe
        self._memory = memory_sampler
        self._clock = clock
        self._cpu_clock = cpu_clock
        self._logger = logging.getLogger(LOGGER_NAME) if logger is None else logger
        self._started = clock()
        self._cpu_mark: tuple[float, float] = (clock(), cpu_clock())
        self._trace_loader: TraceLoader | None = None
        self._stream_counters: Callable[[], tuple[int, int]] | None = None
        self._observed = 0
        self._rejected = 0
        self._log_seq = 0
        self._logs: deque[LogOut] = deque(maxlen=RECENT_LOGS)
        self._statuses: Counter[str] = Counter()
        self._latency: dict[str, _Window] = {}
        self._queued: OrderedDict[str, float] = OrderedDict()
        self._running: OrderedDict[str, float] = OrderedDict()
        self._pending_traces: OrderedDict[str, None] = OrderedDict()
        self._traced: OrderedDict[str, float] = OrderedDict()
        self._tool_status: Counter[str] = Counter()
        self._tool_calls = 0
        self._permissions: Counter[str] = Counter()
        self._risk: Counter[str] = Counter()
        self._verdicts: Counter[str] = Counter()
        self._verification: Counter[str] = Counter()
        self._recovery: Counter[str] = Counter()
        self._recovery_actions: Counter[str] = Counter()
        self._recovery_outcomes: Counter[str] = Counter()
        self._missions_ingested = 0
        self._generations = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._generation_ms = _Window()
        self._rates: deque[float] = deque(maxlen=LATENCY_WINDOW)
        self._models: OrderedDict[tuple[str, str], list[int]] = OrderedDict()
        self._provider_health: tuple[ProviderHealthOut, ...] = ()

    def bind_traces(self, loader: TraceLoader) -> None:
        """Where stored traces come from (``MissionService.get_trace``)."""
        self._trace_loader = loader

    def bind_stream_counters(self, counters: Callable[[], tuple[int, int]]) -> None:
        """The stream's (dropped, rejected) counts, reported alongside ours."""
        self._stream_counters = counters

    @property
    def observed_events(self) -> int:
        return self._observed

    @property
    def rejected_events(self) -> int:
        return self._rejected

    # -- the sink

    async def publish(self, event: EventEnvelope) -> None:
        try:
            self.observe(event)
        except Exception:
            self._rejected += 1  # observing is best effort; the event still goes
        await self._sink.publish(event)

    def observe(self, event: EventEnvelope) -> None:
        try:
            check_event(event)
        except EventPayloadError:
            self._rejected += 1
            return
        self._observed += 1
        self._log(event)
        payload = event.payload
        kind = event.event_type
        duration = _ms(payload)
        if kind == EventType.TASK_STATE_CHANGED:
            if payload.get("scope") == "mission":
                self._mission_status(event.correlation_id, payload)
        elif kind in (EventType.AGENT_COMPLETED, EventType.AGENT_FAILED):
            role = _str(payload, "role")
            if role is not None and duration is not None:
                self._record(f"agent.{role}", duration)
        elif kind == EventType.STEP_COMPLETED and duration is not None:
            self._record("step", duration)
        elif kind in (EventType.TOOL_COMPLETED, EventType.TOOL_FAILED):
            self._tool_calls += 1
            status = _str(payload, "tool_status") or (
                "success" if kind == EventType.TOOL_COMPLETED else "failure"
            )
            self._tool_status[status] += 1
            if _str(payload, "decision") in ("denied", "blocked"):
                self._permissions["policy_denied"] += 1
            if duration is not None:
                self._record("tool", duration)
        elif kind == EventType.PERMISSION_REQUESTED:
            self._permissions["requested"] += 1
            risk = _str(payload, "risk_level")
            if risk is not None:
                self._risk[risk] += 1
        elif kind in (EventType.VERIFICATION_PASSED, EventType.VERIFICATION_FAILED):
            passed = kind == EventType.VERIFICATION_PASSED
            self._verification["passed" if passed else "failed"] += 1
            verdict = _str(payload, "verdict")
            if verdict is not None:
                self._verdicts[verdict] += 1
            if duration is not None:
                self._record("verification", duration)
        elif kind == EventType.RECOVERY_STARTED:
            self._recovery["started"] += 1
            action = _str(payload, "action")
            if action is not None:
                self._recovery_actions[action] += 1
        elif kind == EventType.RECOVERY_COMPLETED:
            self._recovery["completed"] += 1
            outcome = _str(payload, "outcome")
            if outcome is not None:
                self._recovery_outcomes[outcome] += 1
            if duration is not None:
                self._record("recovery", duration)
        elif kind == EventType.RECOVERY_EXHAUSTED:
            self._recovery["exhausted"] += 1

    def _record(self, stage: str, value: int) -> None:
        window = self._latency.get(stage)
        if window is None:
            window = self._latency[stage] = _Window()
        window.add(value)

    def _elapsed(self, since: float) -> int:
        return max(0, round((self._clock() - since) * 1000))

    def _mission_status(self, mission_id: str, payload: Mapping[str, object]) -> None:
        status = _str(payload, "mission_status")
        if status is None:
            return
        self._statuses[status] += 1
        now = self._clock()
        if status == "queued":
            _remember(self._queued, mission_id, now, MAX_TRACKED_MISSIONS)
        elif status == "running":
            queued = self._queued.get(mission_id)
            if queued is not None:
                self._record("mission.queue_wait", self._elapsed(queued))
            _remember(self._running, mission_id, now, MAX_TRACKED_MISSIONS)
        if status == "failed" and payload.get("failure_code") == "permission_denied":
            self._permissions["denied"] += 1
        elif status == "superseded":
            self._permissions["approved"] += 1
        if status not in TERMINAL:
            return
        running = self._running.pop(mission_id, None)
        if running is not None:
            self._record("mission.run", self._elapsed(running))
        queued = self._queued.pop(mission_id, None)
        if status == "completed" and queued is not None:
            self._record("mission.first_answer", self._elapsed(queued))
        if status in _TRACED and mission_id not in self._traced:
            _remember(self._traced, mission_id, now, MAX_TRACKED_MISSIONS)
            self._pending_traces[mission_id] = None
            while len(self._pending_traces) > MAX_TRACKED_MISSIONS:
                self._pending_traces.popitem(last=False)

    def _log(self, event: EventEnvelope) -> None:
        level = _LEVELS[event.severity]
        extra = {
            "correlation_id": event.correlation_id,
            "mission_id": event.correlation_id,
            "event_type": event.event_type.value,
        }
        if event.task_id is not None:
            extra["task_id"] = event.task_id
        if event.agent_id is not None:
            extra["agent_id"] = event.agent_id
        step = _str(event.payload, "step_id")
        if step is not None:
            extra["step_id"] = step
        message = _message(event)
        record = self._logger.makeRecord(
            self._logger.name, level, __file__, 0, message, (), None, extra=extra
        )
        entry = log_entry(record)
        self._log_seq += 1
        self._logs.append(
            LogOut.model_validate({**entry, "id": f"log-{self._log_seq}"}, strict=False)
        )
        if self._logger.isEnabledFor(level):
            self._logger.handle(record)

    # -- traces

    async def _ingest_traces(self) -> None:
        loader = self._trace_loader
        if loader is None:
            return
        for _ in range(min(MAX_TRACES_PER_SNAPSHOT, len(self._pending_traces))):
            mission_id, _unused = self._pending_traces.popitem(last=False)
            try:
                trace = await loader(mission_id)
            except Exception:
                continue  # deleted or unreadable: nothing to add
            if trace is not None:
                self._ingest(trace)

    def _ingest(self, trace: MissionTrace) -> None:
        self._missions_ingested += 1
        for generation in trace.generations:
            self._generations += 1
            self._input_tokens += generation.input_tokens or 0
            self._output_tokens += generation.output_tokens or 0
            if generation.duration_ms is not None:
                self._generation_ms.add(round(generation.duration_ms))
                if generation.output_tokens and generation.duration_ms > 0:
                    # Output tokens over total latency (the PHX-091 FYP rule).
                    self._rates.append(
                        generation.output_tokens / (generation.duration_ms / 1000)
                    )
            key = (generation.provider_id, generation.model_id)
            usage = self._models.get(key)
            if usage is None:
                if len(self._models) >= MAX_MODELS:
                    self._models.popitem(last=False)
                usage = self._models[key] = [0, 0, 0]
            usage[0] += 1
            usage[1] += generation.input_tokens or 0
            usage[2] += generation.output_tokens or 0
        if trace.provider_health:
            self._provider_health = tuple(
                ProviderHealthOut(
                    provider_id=item.provider_id, status=item.status.value
                )
                for item in trace.provider_health
            )

    # -- the snapshot

    def _cpu_percent(self) -> float | None:
        wall, cpu = self._clock(), self._cpu_clock()
        last_wall, last_cpu = self._cpu_mark
        self._cpu_mark = (wall, cpu)
        if wall - last_wall <= 0.05:
            return None
        share = (cpu - last_cpu) / (wall - last_wall) / (os.cpu_count() or 1)
        return round(max(0.0, min(share * 100, 100.0)), 1)

    async def snapshot(self) -> MetricsOut:
        await self._ingest_traces()
        rss, total, available = self._memory()
        vram = await self._vram_probe()
        dropped, stream_rejected = (
            (0, 0) if self._stream_counters is None else self._stream_counters()
        )
        calls = self._tool_calls
        succeeded = self._tool_status["success"]
        rates = tuple(self._rates)
        return MetricsOut(
            generated_at=datetime.now(UTC),
            uptime_seconds=max(0, int(self._clock() - self._started)),
            missions=dict(sorted(self._statuses.items())),
            latency=tuple(
                window.summary(stage) for stage, window in sorted(self._latency.items())
            ),
            generation=GenerationMetrics(
                missions_ingested=self._missions_ingested,
                generations=self._generations,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                duration=self._generation_ms.summary("generation"),
                output_tokens_per_second_mean=round(sum(rates) / len(rates), 2)
                if rates
                else None,
                models=tuple(
                    ModelUsage(
                        provider_id=provider_id,
                        model_id=model_id,
                        generations=usage[0],
                        input_tokens=usage[1],
                        output_tokens=usage[2],
                    )
                    for (provider_id, model_id), usage in self._models.items()
                ),
                provider_health=self._provider_health,
            ),
            tools=ToolMetrics(
                calls=calls,
                succeeded=succeeded,
                failed=calls - succeeded,
                success_rate=round(succeeded / calls, 4) if calls else None,
                by_status=dict(sorted(self._tool_status.items())),
            ),
            permissions=PermissionMetrics(
                requested=self._permissions["requested"],
                approved=self._permissions["approved"],
                denied=self._permissions["denied"],
                policy_denied=self._permissions["policy_denied"],
                by_risk=dict(sorted(self._risk.items())),
            ),
            verification=VerificationMetrics(
                passed=self._verification["passed"],
                failed=self._verification["failed"],
                by_verdict=dict(sorted(self._verdicts.items())),
            ),
            recoveries=RecoveryMetrics(
                started=self._recovery["started"],
                completed=self._recovery["completed"],
                exhausted=self._recovery["exhausted"],
                by_action=dict(sorted(self._recovery_actions.items())),
                by_outcome=dict(sorted(self._recovery_outcomes.items())),
            ),
            resources=ResourceMetrics(
                process_rss_bytes=rss,
                system_ram_total_bytes=total,
                system_ram_available_bytes=available,
                process_cpu_percent=self._cpu_percent(),
                vram=vram,
            ),
            events=EventCounters(
                observed=self._observed,
                rejected=self._rejected,
                stream_dropped=dropped,
                stream_rejected=stream_rejected,
            ),
            logs=tuple(self._logs),
        )
