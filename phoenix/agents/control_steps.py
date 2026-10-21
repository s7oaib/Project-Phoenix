"""PHX-112 permissioned computer-control steps for the PHOENIX mission runtime.

A computer-control step (domain ``computer_control``, KAKASHI) runs between two
generations, like a PHX-046 document step:

1. The control specialist may only propose one ``{"control_request": ...}`` of
   at most MAX_CONTROL_ACTIONS actions on the browser, Windows UI, screenshot
   or terminal tools. Both turns use the structured (thinking off) binding.
2. PHOENIX builds every tool call and PermissionRequest itself. Model text never
   supplies session IDs, window handles, process IDs or page URLs for later
   actions: PHOENIX opens and closes its own isolated browser session, resolves
   a window title through one permissioned list_windows call, and tracks the
   page it actually reached. Each action goes through its tool, which asks
   ToolRegistry and PermissionEngine (INV-004) and writes the P3/P4 audit rows
   itself (INV-007). P5 is never reachable (INV-006).
3. Before any effect, PHOENIX pre-checks every action whose resource is already
   known with its own PermissionEngine snapshot; the first one that needs
   approval ends the mission ``waiting_permission`` with nothing executed.
4. The answer turn sees the results only as one guarded ``TOOL_OUTPUT`` item
   (INV-017); LEVI checks the tool evidence deterministically before its model
   check (INV-008).

Grants come only from the runtime caller (INV-003): SESSION or PERSISTENT P1/P2
grants and SESSION P3 grants with the fixed CONTROL_TOOL_REASON. A P4 action
needs an explicit user decision bound to the exact request the user saw
(INV-005): the caller passes it as a ControlApproval, it is used at most once,
and a mission runs at most one P4 action (ADR-151). The one PHOENIX-owned
policy rule is the browser teardown (``control_permission_policy``).

Kill switch (ADR-153): every tool call runs on the runner's single worker
thread. PhoenixRuntime.cancel trips the mission's ControlKillSwitch before it
cancels the task, so no further action starts; the in-flight synchronous call
cannot be pre-empted (the tools declare no cancellation) and is bounded by its
tool's own timeout, its result is discarded, and the browser session is closed
right after it returns. Tool output is never logged or traced: records keep
IDs, statuses, codes, hashes and lengths only.
"""

import asyncio
import contextlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Annotated, Literal, TypeVar, cast
from uuid import uuid4

import psutil
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from phoenix.agents import control_prompts, prompts
from phoenix.agents.handoff import HandoffEnvelope
from phoenix.agents.planner import AizenPlan, PlanDraft, PlanningRequest, PlanStep
from phoenix.agents.responders import (
    ModelBinding,
    ModelVerificationResponder,
    ResponderErrorCode,
    ResponderOutputError,
    StepAssignment,
    StepAssignments,
    _AssignmentsWire,
    _evidence,
    _generate,
    _PlanWire,
    parse_json_object,
)
from phoenix.agents.router import RoutingDomain
from phoenix.agents.tool_steps import (
    ToolPermissionRequired,
    ToolStepFailed,
    ToolStepRejected,
    _guarded,
    _Item,
    _model_checks,
)
from phoenix.agents.verifier import (
    VerificationCheck,
    VerificationDraft,
    VerificationRequest,
)
from phoenix.models.provider import Identifier, NonnegativeInt
from phoenix.models.vision import VisionError, VisionLoopRequest, VisionLoopService
from phoenix.security.audit import audit_required_for_execution
from phoenix.security.permissions import (
    DecisionReason,
    GrantDuration,
    PermissionEngine,
    PermissionGrant,
    PermissionPolicy,
    PermissionPolicyRule,
    PermissionRequest,
    PermissionStatus,
    UserDecision,
    UserPermissionDecision,
)
from phoenix.security.prompt_injection import (
    PromptInjectionGuard,
    UntrustedContentOrigin,
)
from phoenix.tools.browser import (
    BrowserAuditError,
    BrowserCall,
    BrowserInputError,
    BrowserOperation,
    BrowserTool,
    BrowserToolResult,
    browser_tool_definitions,
)
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResultStatus,
    ToolRiskLevel,
)
from phoenix.tools.screenshot import (
    ScreenshotCall,
    ScreenshotCaptureTool,
    ScreenshotFrame,
    ScreenshotInputError,
    ScreenshotToolResult,
    screenshot_tool_definitions,
)
from phoenix.tools.terminal import (
    TerminalAuditError,
    TerminalCall,
    TerminalInputError,
    TerminalTool,
    TerminalToolResult,
    terminal_tool_definitions,
)
from phoenix.tools.windows_ui import (
    WindowsControlSelector,
    WindowsUIAuditError,
    WindowsUICall,
    WindowsUIInputError,
    WindowsUIOperation,
    WindowsUITool,
    WindowsUIToolResult,
    windows_ui_tool_definitions,
)

CONTROL_ACTOR_ID = "computer_control"
# Fixed so caller grants can match exactly; never derived from model text.
CONTROL_TOOL_REASON = "PHOENIX computer-control step: one proposed action"
# One PHOENIX-owned browser session at a time (missions run one at a time).
BROWSER_SESSION_ID = "phoenix-control"
MAX_CONTROL_ACTIONS = 4
# Page/element text and terminal streams per action, well under the guard cap.
MAX_READ_CHARS = 8000
MAX_STREAM_CHARS = 4000
MAX_LISTED_WINDOWS = 100
CLOSE_POLICY_RULE_ID = "phx-112.browser-teardown"
# Display-only approval summaries kept for pending requests (newest kept).
MAX_APPROVAL_SUMMARIES = 256

_DEFINITIONS: tuple[ToolDefinition, ...] = (
    *browser_tool_definitions(),
    *windows_ui_tool_definitions(),
    *screenshot_tool_definitions(),
    *terminal_tool_definitions(),
)
CONTROL_TOOL_RISKS: Mapping[str, ToolRiskLevel] = MappingProxyType(
    {definition.tool_id: definition.risk_level for definition in _DEFINITIONS}
)
CONTROL_TOOL_IDS: tuple[str, ...] = tuple(CONTROL_TOOL_RISKS)
_GRANT_RISKS = (ToolRiskLevel.P1, ToolRiskLevel.P2, ToolRiskLevel.P3)
_ToolKind = Literal["browser", "windows", "screen", "terminal"]
_Result = (
    BrowserToolResult
    | WindowsUIToolResult
    | ScreenshotToolResult
    | (TerminalToolResult)
)
_Call = BrowserCall | WindowsUICall | ScreenshotCall | TerminalCall
_T = TypeVar("_T")


def control_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Every canonical definition a control step may reach, for ToolRegistry."""
    return _DEFINITIONS


def control_action(tool_id: str) -> str:
    """The PermissionRequest action each canonical control tool uses."""
    if tool_id not in CONTROL_TOOL_RISKS:
        raise ValueError("Unknown control tool")
    if tool_id.startswith("terminal."):
        return "execute"
    return tool_id.split(".", 1)[1]


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def control_permission_policy() -> PermissionPolicy:
    """PHOENIX's one standing rule: closing its own browser session (P2 teardown).

    Teardown removes capability and has no external effect, so it never waits
    for a user (ADR-153). Opening the session and every other action still
    needs a user grant or decision.
    """
    return PermissionPolicy(
        rules=(
            PermissionPolicyRule(
                rule_id=CLOSE_POLICY_RULE_ID,
                actor_id=CONTROL_ACTOR_ID,
                tool_id="browser.session_close",
                action="session_close",
                resource=_canonical(["session_close", BROWSER_SESSION_ID]),
                reason=CONTROL_TOOL_REASON,
                risk_level=ToolRiskLevel.P2,
            ),
        )
    )


def control_grants(grants: object) -> tuple[PermissionGrant, ...]:
    """Caller grants for control steps only: exact scope, P1-P3, user durations.

    P3 grants last for the session only (PermissionEngine never matches a
    persistent P3 grant); P4 has no grant form and needs a ControlApproval.
    """
    if type(grants) not in (tuple, list):
        raise ValueError("Grants require an ordered sequence")
    accepted: list[PermissionGrant] = []
    for grant in cast(Sequence[object], grants):
        if type(grant) is not PermissionGrant:
            raise ValueError("Expected PermissionGrant instances")
        grant = PermissionGrant.model_validate(grant)
        risk = CONTROL_TOOL_RISKS.get(grant.tool_id)
        if (
            risk not in _GRANT_RISKS
            or grant.risk_level != risk
            or grant.actor_id != CONTROL_ACTOR_ID
            or grant.reason != CONTROL_TOOL_REASON
            or grant.action != control_action(grant.tool_id)
            or grant.duration not in (GrantDuration.SESSION, GrantDuration.PERSISTENT)
            or (risk == ToolRiskLevel.P3 and grant.duration != GrantDuration.SESSION)
        ):
            raise ValueError("Grant is outside the control step scope")
        accepted.append(grant)
    ids = [grant.grant_id for grant in accepted]
    scopes = [
        (grant.tool_id, grant.resource, grant.duration, grant.session_id)
        for grant in accepted
    ]
    if len(set(ids)) != len(ids) or len(set(scopes)) != len(scopes):
        raise ValueError("Duplicate grant ID or effective scope")
    return tuple(accepted)


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


class ControlApproval(_Value):
    """The user's explicit decision on one exact P4 control request (INV-005).

    The runtime reuses ``request`` unchanged when the re-run proposes the same
    action, so PermissionEngine correlates the decision with the request the
    user saw; any other action, or a second use, needs a new decision.
    """

    request: PermissionRequest
    decision: UserPermissionDecision

    @model_validator(mode="after")
    def correlated(self) -> "ControlApproval":
        request, decision = self.request, self.decision
        if (
            decision.request_id != request.request_id
            or decision.decision != UserDecision.APPROVED
            or decision.decided_at < request.requested_at
            or request.actor_id != CONTROL_ACTOR_ID
            or request.reason != CONTROL_TOOL_REASON
            or CONTROL_TOOL_RISKS.get(request.tool_id) != ToolRiskLevel.P4
            or request.action != control_action(request.tool_id)
        ):
            raise ValueError("Approval does not match one P4 control request")
        return self


def control_approvals(approvals: object) -> tuple[ControlApproval, ...]:
    if type(approvals) not in (tuple, list):
        raise ValueError("Approvals require an ordered sequence")
    accepted: list[ControlApproval] = []
    for approval in cast(Sequence[object], approvals):
        if type(approval) is not ControlApproval:
            raise ValueError("Expected ControlApproval instances")
        accepted.append(ControlApproval.model_validate(approval))
    ids = [approval.request.request_id for approval in accepted]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate approval")
    return tuple(accepted)


class ControlCallRecord(_Value):
    """Privacy-preserving evidence of one control action: IDs, codes, hashes."""

    step_id: Identifier
    call_id: Identifier
    request_id: Identifier
    tool_id: Identifier
    action: Identifier
    risk_level: ToolRiskLevel
    decision_status: PermissionStatus
    decision_reason: DecisionReason
    tool_status: ToolResultStatus
    attempted: bool = Field(strict=True)
    effect_applied: bool = Field(strict=True)
    audited: bool = Field(strict=True)
    output_chars: NonnegativeInt | None = None
    output_sha256: str | None = Field(
        default=None, strict=True, pattern=r"^[0-9a-f]{64}$"
    )


# What each control tool does, in words, for the approval summary.
APPROVAL_ACTIONS: Mapping[str, str] = MappingProxyType(
    {
        "browser.session_open": "Open PHOENIX's own isolated browser",
        "browser.navigate": "Open a web page in PHOENIX's browser",
        "browser.read_page": "Read the text of the current page",
        "browser.inspect_element": "Read one element of the current page",
        "browser.click": "Click an element on the current page",
        "browser.fill": "Type text into a field on the current page",
        "browser.session_close": "Close PHOENIX's browser",
        "windows.list_windows": "List the titles of open windows",
        "windows.inspect_window": "Inspect a window",
        "windows.focus_window": "Bring a window to the front",
        "windows.inspect_control": "Read one control in a window",
        "windows.invoke_control": "Press or activate a control in a window",
        "windows.set_text": "Type text into a control in a window",
        "windows.close_window": "Close a window",
        "screen.capture_window": "Take a screenshot of a window",
        "terminal.inspect": "Run a read-only terminal profile",
        "terminal.project": "Run a project terminal profile",
        "terminal.sensitive": "Run a sensitive terminal profile",
    }
)
SECRET_TEXT_NOTE = (
    "This text looks like a password, key or token, so it is hidden here."
    " Approving types the exact text PHOENIX proposed."
)
_SECRET_PREFIXES = (
    "sk-",
    "sk_",
    "pk_",
    "rk_",
    "ghp_",
    "gho_",
    "ghs_",
    "github_pat_",
    "glpat-",
    "xox",
    "AKIA",
    "ASIA",
    "AIza",
    "eyJ",
    "-----BEGIN",
)


def looks_like_secret(text: str) -> bool:
    """A display heuristic that errs toward hiding; it authorizes nothing.

    A word is secret-looking when it has a known key/token prefix, or is at
    least 8 characters with lower case plus two of upper case, digits and
    symbols (a password shape), or is a 24+ character token of mixed classes.
    """
    for word in text.split():
        if word.startswith(_SECRET_PREFIXES):
            return True
        if len(word) < 8:
            continue
        lower = any(c.islower() for c in word)
        classes = sum(
            (
                lower,
                any(c.isupper() for c in word),
                any(c.isdigit() for c in word),
                any(not c.isalnum() for c in word),
            )
        )
        if (lower and classes >= 3) or (len(word) >= 24 and classes >= 2):
            return True
    return False


def _process_info(process_id: int) -> tuple[str | None, str | None]:
    """The real process name and executable path behind a window (read-only).

    A window title is set by the window's own program and can claim anything;
    the process and executable show which program the user would act on.
    """
    try:
        process = psutil.Process(process_id)
    except (psutil.Error, ValueError, OverflowError):
        return None, None
    name = path = None
    with contextlib.suppress(psutil.Error, OSError):
        name = process.name() or None
    with contextlib.suppress(psutil.Error, OSError):
        path = process.exe() or None
    return name, path


class ApprovalSummary(_Value):
    """What a pending control request would do, in words (display only).

    Built by PHOENIX from the call it issued and what it observed (the page URL
    the browser reached, the listed window and its real process), never from
    model text alone. It is shown next to the stored request and is never used
    for authorization: PermissionEngine decides on the request's exact tool,
    action and resource, and an approval binds to that request only (ADR-151).
    Unknown fields are None.
    """

    request_id: Identifier
    tool_id: Identifier
    risk_level: ToolRiskLevel
    action: str
    page_url: str | None = None
    element: str | None = None
    window_title: str | None = None
    process_name: str | None = None
    executable_path: str | None = None
    process_id: NonnegativeInt | None = None
    command: str | None = None
    text: str | None = None
    text_masked: bool = Field(default=False, strict=True)
    note: str | None = None


class ControlKillSwitch:
    """One mission's kill switch; thread-safe, tripped once, never reset."""

    def __init__(self) -> None:
        self._flag = threading.Event()

    def trip(self) -> None:
        self._flag.set()

    @property
    def tripped(self) -> bool:
        return self._flag.is_set()


@dataclass
class ControlMission:
    """Per-mission control state: kill switch, unused approvals, the P4 budget."""

    kill: ControlKillSwitch = field(default_factory=ControlKillSwitch)
    approvals: list[ControlApproval] = field(default_factory=list)
    used_decisions: set[str] = field(default_factory=set)
    p4_executed: bool = False


@dataclass(frozen=True)
class ControlActionEvidence:
    tool_id: str
    call_id: str
    risk_level: ToolRiskLevel
    request: PermissionRequest
    result: _Result


@dataclass(frozen=True)
class ControlEvidence:
    """What PHOENIX issued and what the tools returned, for LEVI's correlation."""

    actions: tuple[ControlActionEvidence, ...]
    text: str
    approval_ids: frozenset[str]
    source_id: str


class _Killed(Exception):
    """The kill switch was tripped before this action started."""


class _WireModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


_Short = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]
# Exact values (never stripped): URLs, selectors, titles, names and arguments.
_Exact = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
_Free = Annotated[str, StringConstraints(max_length=65536)]


class _ActionWire(_WireModel):
    tool: Literal["browser", "windows", "screen", "terminal"]
    operation: _Short
    url: _Exact | None = None
    selector: _Exact | None = None
    text: _Free | None = None
    window_title: _Exact | None = None
    automation_id: _Exact | None = None
    name: _Exact | None = None
    control_type: _Exact | None = None
    query: _Short | None = None
    profile_id: _Short | None = None
    arguments: list[_Exact] | None = Field(default=None, max_length=64)


class _ControlRequestWire(_WireModel):
    actions: list[_ActionWire] = Field(min_length=1)


class _ControlEnvelope(_WireModel):
    control_request: _ControlRequestWire


# Fields each proposed operation must (and may only) carry.
_SHAPES: Mapping[tuple[str, str], tuple[frozenset[str], frozenset[str]]] = (
    MappingProxyType(
        {
            ("browser", "navigate"): (frozenset({"url"}), frozenset()),
            ("browser", "read_page"): (frozenset(), frozenset()),
            ("browser", "inspect_element"): (frozenset({"selector"}), frozenset()),
            ("browser", "click"): (frozenset({"selector"}), frozenset()),
            ("browser", "fill"): (frozenset({"selector", "text"}), frozenset()),
            ("windows", "list_windows"): (frozenset(), frozenset()),
            ("windows", "inspect_window"): (frozenset({"window_title"}), frozenset()),
            ("windows", "focus_window"): (frozenset({"window_title"}), frozenset()),
            ("windows", "close_window"): (frozenset({"window_title"}), frozenset()),
            ("windows", "inspect_control"): (
                frozenset({"window_title"}),
                frozenset({"automation_id", "name", "control_type"}),
            ),
            ("windows", "invoke_control"): (
                frozenset({"window_title"}),
                frozenset({"automation_id", "name", "control_type"}),
            ),
            ("windows", "set_text"): (
                frozenset({"window_title", "text"}),
                frozenset({"automation_id", "name", "control_type"}),
            ),
            ("screen", "capture_window"): (
                frozenset({"window_title", "query"}),
                frozenset(),
            ),
            ("terminal", "run"): (frozenset({"profile_id"}), frozenset({"arguments"})),
        }
    )
)
_OPTIONAL_FIELDS = (
    "url",
    "selector",
    "text",
    "window_title",
    "automation_id",
    "name",
    "control_type",
    "query",
    "profile_id",
    "arguments",
)


def _check_shape(action: _ActionWire) -> None:
    shape = _SHAPES.get((action.tool, action.operation))
    if shape is None:
        raise ToolStepRejected("Operation is not on the control allowlist")
    required, optional = shape
    present = {name for name in _OPTIONAL_FIELDS if getattr(action, name) is not None}
    if not required <= present or present - required - optional:
        raise ToolStepRejected("Control action fields disagree")
    if action.operation in ("inspect_control", "invoke_control", "set_text") and not (
        {"automation_id", "name"} & present
    ):
        raise ToolStepRejected("A control needs an automation ID or name")


@dataclass(frozen=True)
class _Window:
    hwnd: int
    process_id: int
    class_name: str
    title: str


@dataclass
class _Step:
    """One step's PHOENIX-issued actions, observations and browser state."""

    handoff: HandoffEnvelope
    grants: tuple[PermissionGrant, ...]
    session_id: str | None
    mission: ControlMission
    record: Callable[[ControlCallRecord], None]
    actions: list[ControlActionEvidence] = field(default_factory=list)
    observations: list[dict[str, object]] = field(default_factory=list)
    page_url: str | None = None
    session_open: bool = False
    # Set once session_open is submitted, so a cancel during it still tears down.
    browser_started: bool = False


class ControlStepRunner:
    """Computer-control steps over explicit, already-constructed tools.

    ``registry`` and ``policy`` must be the ones the tools were built with: the
    runner's PermissionEngine snapshot only pre-checks requests, and each tool
    still decides with its own engine. Pass ``control_permission_policy()`` (or
    a policy that contains its rule) so PHOENIX can close its browser session.
    A screenshot needs the Windows UI tool (to resolve the window) and a vision
    service; vision output is advisory text only.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PermissionPolicy,
        browser: BrowserTool | None = None,
        windows: WindowsUITool | None = None,
        screen: ScreenshotCaptureTool | None = None,
        vision: VisionLoopService | None = None,
        vision_provider_id: str | None = None,
        vision_model_id: str | None = None,
        terminal: TerminalTool | None = None,
    ) -> None:
        if type(registry) is not ToolRegistry or type(policy) is not PermissionPolicy:
            raise ValueError("Expected an exact ToolRegistry and PermissionPolicy")
        tools: dict[str, object] = {}
        for kind, tool, expected in (
            ("browser", browser, BrowserTool),
            ("windows", windows, WindowsUITool),
            ("screen", screen, ScreenshotCaptureTool),
            ("terminal", terminal, TerminalTool),
        ):
            if tool is not None:
                if type(tool) is not expected:
                    raise ValueError("Expected an exact control tool")
                tools[kind] = tool
        if not tools:
            raise ValueError("At least one control tool is required")
        if "screen" in tools and (
            "windows" not in tools
            or type(vision) is not VisionLoopService
            or not vision_provider_id
            or not vision_model_id
        ):
            raise ValueError("Screenshots need the Windows UI tool and vision")
        snapshot = ToolRegistry(registry.list_tools())
        for definition in _DEFINITIONS:
            kind = definition.tool_id.split(".", 1)[0]
            if kind in tools and snapshot.get(definition.tool_id) != definition:
                raise ValueError("Registry lacks a canonical control definition")
        self._engine = PermissionEngine(snapshot, policy)
        self._policy_ids = frozenset(rule.rule_id for rule in policy.rules)
        if "browser" in tools and (
            control_permission_policy().rules[0] not in policy.rules
        ):
            # Without the teardown rule a cancelled step could leak its session.
            raise ValueError("The browser needs PHOENIX's teardown policy rule")
        self._busy = False
        self._tools = MappingProxyType(tools)
        self._vision = vision
        self._vision_provider = vision_provider_id
        self._vision_model = vision_model_id
        self._worker: ThreadPoolExecutor | None = None
        self._summaries: dict[str, ApprovalSummary] = {}

    @property
    def tool_kinds(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def approval_summary(self, request_id: str) -> ApprovalSummary | None:
        """The display-only summary of a pending request this runner raised.

        Kept in memory only (the typed text never reaches the database); after
        a restart the summary is gone and the request shows its raw resource.
        """
        return self._summaries.get(request_id)

    def mission(self, approvals: Sequence[ControlApproval] = ()) -> ControlMission:
        return ControlMission(approvals=list(control_approvals(approvals)))

    def close(self) -> None:
        """Stop accepting work; an in-flight call finishes on its own timeout."""
        if self._worker is not None:
            self._worker.shutdown(wait=False, cancel_futures=False)
            self._worker = None

    # ---- planning and assignment (control-enabled variants)

    def planning_responder(
        self, binding: ModelBinding, *, max_steps: int
    ) -> "ControlPlanningResponder":
        return ControlPlanningResponder(binding, max_steps=max_steps)

    @staticmethod
    def assignment_responder(
        binding: ModelBinding,
        guard: PromptInjectionGuard,
        *,
        allowed_domains: tuple[RoutingDomain, ...],
    ) -> "ControlAssignmentResponder":
        return ControlAssignmentResponder(
            binding, guard, allowed_domains=allowed_domains
        )

    # ---- one step

    async def perform(
        self,
        binding: ModelBinding,
        guard: PromptInjectionGuard,
        handoff: HandoffEnvelope,
        *,
        user_request: str,
        evidence: tuple[tuple[str, str], ...],
        grants: tuple[PermissionGrant, ...],
        session_id: str | None,
        mission: ControlMission,
        record: Callable[[ControlCallRecord], None],
    ) -> tuple[str, ControlEvidence]:
        model = UntrustedContentOrigin.MODEL_OUTPUT
        objective: _Item = (
            prompts.OBJECTIVE_ITEM,
            model,
            prompts.OBJECTIVE_ITEM,
            "output",
            handoff.objective,
        )
        prior = tuple(
            (identity, model, identity, "output", text) for identity, text in evidence
        )
        fixed = (
            f"Original request:\n{user_request}",
            f"Expected output:\n{handoff.expected_output_schema}",
        )
        boundary = f"{handoff.task_id}:{handoff.step_id}"
        proposal = await _generate(
            binding,
            "control_specialist",
            control_prompts.CONTROL_SPECIALIST_SYSTEM.format(
                max_actions=MAX_CONTROL_ACTIONS
            ),
            "\n\n".join(
                (
                    *fixed,
                    _guarded(
                        guard,
                        f"{boundary}:control",
                        control_prompts.CONTROL_TOOL_TASK,
                        (objective, *prior),
                    ),
                )
            ),
        )
        wire = parse_json_object(proposal, _ControlEnvelope).control_request
        if len(wire.actions) > MAX_CONTROL_ACTIONS:
            raise ResponderOutputError(ResponderErrorCode.SCHEMA)
        for action in wire.actions:
            _check_shape(action)
            if action.tool not in self._tools:
                raise ToolStepRejected("Control tool is not enabled")
        if mission.kill.tripped:
            raise asyncio.CancelledError()
        if self._busy:
            # One fixed browser session and one worker: one control step at a time.
            raise ToolStepRejected("Another control step is running")
        self._busy = True
        step = _Step(handoff, grants, session_id, mission, record)
        try:
            await self._act(step, tuple(wire.actions), binding)
            if step.session_open:
                await self._close(step)
        except asyncio.CancelledError:
            mission.kill.trip()
            if step.session_open or step.browser_started:
                self._teardown_later()
            raise
        except Exception:
            if step.session_open:
                try:
                    await self._close(step)
                except asyncio.CancelledError:
                    mission.kill.trip()
                    self._teardown_later()
                    raise
                except Exception:
                    pass  # the original failure is the step's outcome
            raise
        finally:
            self._busy = False
        text = json.dumps(step.observations, ensure_ascii=False, sort_keys=True)
        if len(text) > binding.max_output_chars:
            raise ToolStepFailed("Control output exceeds the configured limit")
        found = ControlEvidence(
            actions=tuple(step.actions),
            text=text,
            approval_ids=frozenset(mission.used_decisions),
            source_id=str(uuid4()),
        )
        answer = await _generate(
            binding,
            "control_answer",
            control_prompts.CONTROL_ANSWER_SYSTEM,
            "\n\n".join(
                (
                    *fixed,
                    _guarded(
                        guard,
                        boundary,
                        control_prompts.CONTROL_ANSWER_TASK,
                        (objective, _output_item(found), *prior),
                    ),
                )
            ),
        )
        return answer, found

    async def _act(
        self, step: _Step, actions: tuple[_ActionWire, ...], binding: ModelBinding
    ) -> None:
        # Phase A: resolve window titles with one permissioned P1 listing.
        windows: tuple[_Window, ...] = ()
        if any(action.window_title is not None for action in actions):
            listing = self._planned(step, "windows", _list_call(), {})
            self._precheck(step, listing)
            result = await self._run(step, listing, binding, observe=False)
            windows = _windows_of(cast(WindowsUIToolResult, result))
            step.observations.append(
                {
                    "tool": "windows",
                    "operation": "resolve_window_titles",
                    "status": result.status.value,
                }
            )
        # Phase B: PHOENIX builds every call; pre-check what is already known.
        planned: list[_Planned] = []
        browser = any(action.tool == "browser" for action in actions)
        if browser:
            planned.append(
                self._planned(
                    step,
                    "browser",
                    BrowserCall(
                        operation=BrowserOperation.SESSION_OPEN,
                        session_id=BROWSER_SESSION_ID,
                    ),
                    {"tool": "browser", "operation": "session_open"},
                )
            )
        navigated = False
        for index, action in enumerate(actions, start=1):
            item = self._build(step, action, windows, navigated, index)
            if action.window_title is not None:
                item.window = _resolve(windows, action.window_title)
            planned.append(item)
            navigated = navigated or (action.tool, action.operation) == (
                "browser",
                "navigate",
            )
        p4 = [item for item in planned if item.risk == ToolRiskLevel.P4]
        if len(p4) > 1 or (p4 and step.mission.p4_executed):
            raise ToolStepRejected("A mission runs at most one P4 action")
        if (
            p4
            and p4[0].call is None
            and any(
                item.risk in (ToolRiskLevel.P3, ToolRiskLevel.P4)
                for item in planned[: planned.index(p4[0])]
            )
        ):
            # A deferred P4 page action may still need approval; a re-run would
            # then repeat the earlier P3 effect (ADR-151).
            raise ToolStepRejected("A P3 action cannot precede a deferred P4 action")
        for item in planned:
            if item.call is not None:
                self._precheck(step, item)
        if browser:
            self._precheck(step, self._closing(step))
        # Phase C: execute in order; the kill switch stops the next action.
        for item in planned:
            if step.mission.kill.tripped:
                raise asyncio.CancelledError()
            await self._run(step, item, binding)

    def _build(
        self,
        step: _Step,
        action: _ActionWire,
        windows: tuple[_Window, ...],
        navigated: bool,
        index: int,
    ) -> "_Planned":
        base: dict[str, object] = {
            "action": index,
            "tool": action.tool,
            "operation": action.operation,
        }
        try:
            if action.tool == "browser":
                if action.operation == "navigate":
                    call: _Call = BrowserCall(
                        operation=BrowserOperation.NAVIGATE,
                        session_id=BROWSER_SESSION_ID,
                        url=action.url,
                    )
                    return self._planned(step, "browser", call, base)
                if not navigated:
                    raise ToolStepRejected("A browser action needs a page first")
                return _Planned(
                    kind="browser",
                    tool_id=f"browser.{action.operation}",
                    observation=base,
                    build=_page_action(action),
                )
            if action.tool == "terminal":
                call = TerminalCall(
                    profile_id=cast(str, action.profile_id),
                    arguments=tuple(action.arguments or ()),
                )
                return self._planned(step, "terminal", call, base)
            if action.operation == "list_windows":
                return self._planned(step, "windows", _list_call(), base)
            target = _resolve(windows, cast(str, action.window_title))
            if action.tool == "screen":
                call = ScreenshotCall(
                    hwnd=target.hwnd,
                    expected_process_id=target.process_id,
                    expected_class_name=target.class_name,
                )
                return self._planned(step, "screen", call, base, query=action.query)
            operation = WindowsUIOperation(action.operation)
            fields: dict[str, object] = {"operation": operation, "hwnd": target.hwnd}
            if operation != WindowsUIOperation.INSPECT_WINDOW:
                fields["expected_process_id"] = target.process_id
                fields["expected_class_name"] = target.class_name
            if operation in (
                WindowsUIOperation.INSPECT_CONTROL,
                WindowsUIOperation.INVOKE_CONTROL,
                WindowsUIOperation.SET_TEXT,
            ):
                fields["selector"] = WindowsControlSelector(
                    automation_id=action.automation_id,
                    control_type=action.control_type,
                    name=action.name,
                )
            if operation == WindowsUIOperation.SET_TEXT:
                fields["text"] = action.text
            return self._planned(
                step, "windows", WindowsUICall.model_validate(fields), base
            )
        except (ToolStepRejected, ToolStepFailed):
            raise
        except ValueError:  # pydantic ValidationError is a ValueError
            raise ToolStepRejected("Invalid control action") from None

    def _planned(
        self,
        step: _Step,
        kind: _ToolKind,
        call: _Call,
        observation: dict[str, object],
        *,
        query: str | None = None,
    ) -> "_Planned":
        item = _Planned(
            kind=kind, tool_id="", observation=observation, call=call, query=query
        )
        self._prepare(step, item)
        return item

    def _describe(self, item: "_Planned") -> tuple[str, str, str, ToolRiskLevel]:
        tool, call = self._tools[item.kind], item.call
        try:
            if isinstance(tool, BrowserTool) and isinstance(call, BrowserCall):
                b = tool.describe(call)
                return b.tool_id, b.action, b.resource, b.risk_level
            if isinstance(tool, WindowsUITool) and isinstance(call, WindowsUICall):
                w = tool.describe(call)
                return w.tool_id, w.action, w.resource, w.risk_level
            if isinstance(tool, ScreenshotCaptureTool) and isinstance(
                call, ScreenshotCall
            ):
                s = tool.describe(call)
                return s.tool_id, s.action, s.resource, s.risk_level
            if isinstance(tool, TerminalTool) and isinstance(call, TerminalCall):
                t = tool.describe(call)
                return t.tool_id, t.action, t.resource, t.risk_level
        except (
            BrowserInputError,
            WindowsUIInputError,
            ScreenshotInputError,
            TerminalInputError,
        ):
            raise ToolStepRejected("Invalid control action") from None
        raise ToolStepRejected("Control tool and call disagree")

    def _prepare(self, step: _Step, item: "_Planned") -> None:
        """Describe through the tool, then build or reuse the PermissionRequest."""
        tool_id, action, resource, risk = self._describe(item)
        if (
            CONTROL_TOOL_RISKS.get(tool_id) != risk
            or risk in (ToolRiskLevel.P0, ToolRiskLevel.P5)
            or action != control_action(tool_id)
        ):
            # P5 is blocked outright (INV-006); P0 has no place in a control step.
            raise ToolStepRejected("Control action is blocked")
        item.tool_id = tool_id
        scope = (CONTROL_ACTOR_ID, tool_id, action, resource, CONTROL_TOOL_REASON)
        if risk == ToolRiskLevel.P4:
            for approval in step.mission.approvals:
                stored = approval.request
                if (
                    stored.actor_id,
                    stored.tool_id,
                    stored.action,
                    stored.resource,
                    stored.reason,
                ) == scope:
                    item.request, item.approval = stored, approval
                    return
        item.request = PermissionRequest(
            request_id=str(uuid4()),
            actor_id=CONTROL_ACTOR_ID,
            tool_id=tool_id,
            action=action,
            resource=resource,
            reason=CONTROL_TOOL_REASON,
            requested_at=datetime.now(UTC),
            task_id=step.handoff.task_id,
            session_id=step.session_id,
        )

    def _precheck(self, step: _Step, item: "_Planned") -> None:
        """PHOENIX's own pure evaluation before any effect; tools decide again."""
        assert item.request is not None
        decision = self._engine.evaluate(
            item.request,
            grants=step.grants,
            user_decision=None if item.approval is None else item.approval.decision,
        )
        if decision.status == PermissionStatus.APPROVAL_REQUIRED:
            raise self._pending(item)
        if decision.status != PermissionStatus.ALLOWED:
            raise ToolStepRejected("Permission was not granted")

    def _pending(self, item: "_Planned") -> ToolPermissionRequired:
        """Record the display-only summary, then ask for approval."""
        assert item.request is not None
        summary = _summarize(item)
        self._summaries.pop(summary.request_id, None)
        self._summaries[summary.request_id] = summary
        while len(self._summaries) > MAX_APPROVAL_SUMMARIES:
            del self._summaries[next(iter(self._summaries))]
        return ToolPermissionRequired(item.request)

    def _closing(self, step: _Step) -> "_Planned":
        return self._planned(
            step,
            "browser",
            BrowserCall(
                operation=BrowserOperation.SESSION_CLOSE, session_id=BROWSER_SESSION_ID
            ),
            {"tool": "browser", "operation": "session_close"},
        )

    async def _close(self, step: _Step) -> None:
        item = self._closing(step)
        step.session_open = False
        await self._run(step, item, None, guarded=False)

    def _teardown_later(self) -> None:
        """Queue the browser teardown behind the in-flight call (never awaited)."""
        worker, browser = self._worker, self._tools.get("browser")
        if worker is None or not isinstance(browser, BrowserTool):
            return
        call = BrowserCall(
            operation=BrowserOperation.SESSION_CLOSE, session_id=BROWSER_SESSION_ID
        )
        intent = browser.describe(call)
        request = PermissionRequest(
            request_id=str(uuid4()),
            actor_id=CONTROL_ACTOR_ID,
            tool_id=intent.tool_id,
            action=intent.action,
            resource=intent.resource,
            reason=CONTROL_TOOL_REASON,
            requested_at=datetime.now(UTC),
        )

        def close() -> None:
            try:
                browser.execute(call, request)
            except Exception:
                pass  # bounded best effort; the next session_open reports leftovers

        worker.submit(close)

    async def _run(
        self,
        step: _Step,
        item: "_Planned",
        binding: ModelBinding | None,
        *,
        observe: bool = True,
        guarded: bool = True,
    ) -> _Result:
        if item.call is None:
            assert item.build is not None
            if step.page_url is None:
                raise ToolStepRejected("A browser action needs a page first")
            item.call = item.build(step.page_url)
            self._prepare(step, item)
        call, request = item.call, item.request
        assert request is not None
        risk = item.risk
        if risk == ToolRiskLevel.P4 and step.mission.p4_executed:
            raise ToolStepRejected("A mission runs at most one P4 action")
        decision = None
        if item.approval is not None:
            decision = item.approval.decision
            step.mission.approvals.remove(item.approval)
            step.mission.used_decisions.add(decision.decision_id)
        tool, grants = self._tools[item.kind], step.grants

        def execute() -> _Result:
            return _execute(tool, call, request, grants, decision)

        if (
            isinstance(call, BrowserCall)
            and call.operation == BrowserOperation.SESSION_OPEN
        ):
            step.browser_started = True
        try:
            result = await self._in_worker(
                step.mission.kill if guarded else None, execute
            )
        except (
            BrowserInputError,
            WindowsUIInputError,
            ScreenshotInputError,
            TerminalInputError,
        ):
            raise ToolStepRejected("Invalid control action") from None
        except (BrowserAuditError, WindowsUIAuditError, TerminalAuditError) as error:
            if risk == ToolRiskLevel.P4 and error.effect_may_have_occurred:
                step.mission.p4_executed = True  # conservative: count the attempt
            raise ToolStepFailed("Required control audit failed") from None
        if risk == ToolRiskLevel.P4 and result.attempted:
            step.mission.p4_executed = True
        status = result.permission_decision.status
        success = status == PermissionStatus.ALLOWED and (
            result.status == ToolResultStatus.SUCCESS
            or (
                result.status == ToolResultStatus.PARTIAL_SUCCESS
                and risk == ToolRiskLevel.P1
            )
        )
        observation: dict[str, object] | None = None
        if success and observe:
            observation = {**item.observation, "status": result.status.value}
            observation.update(_observed(result))
            if isinstance(result, ScreenshotToolResult):
                assert binding is not None and item.query is not None
                assert result.frame is not None
                observation.update(
                    await self._interpret(binding, result.frame, item.query)
                )
        rendered = (
            None
            if observation is None
            else json.dumps(observation, ensure_ascii=False, sort_keys=True)
        )
        step.record(
            ControlCallRecord(
                step_id=step.handoff.step_id,
                call_id=call.call_id,
                request_id=request.request_id,
                tool_id=item.tool_id,
                action=request.action,
                risk_level=risk,
                decision_status=status,
                decision_reason=result.permission_decision.reason_code,
                tool_status=result.status,
                attempted=result.attempted,
                effect_applied=bool(getattr(result, "effect_applied", False)),
                audited=_audited(result),
                output_chars=None if rendered is None else len(rendered),
                output_sha256=None
                if rendered is None
                else sha256(rendered.encode("utf-8")).hexdigest(),
            )
        )
        if status == PermissionStatus.APPROVAL_REQUIRED:
            raise self._pending(item)
        if status != PermissionStatus.ALLOWED:
            raise ToolStepRejected("Permission was not granted")
        if not success:
            raise ToolStepFailed("Control action failed")
        step.actions.append(
            ControlActionEvidence(
                tool_id=item.tool_id,
                call_id=call.call_id,
                risk_level=risk,
                request=request,
                result=result,
            )
        )
        if isinstance(result, BrowserToolResult):
            if result.operation == BrowserOperation.SESSION_OPEN:
                step.session_open = True
            elif result.final_url is not None:
                step.page_url = result.final_url
        if observation is not None:
            step.observations.append(observation)
        return result

    async def _interpret(
        self, binding: ModelBinding, frame: ScreenshotFrame, query: str
    ) -> dict[str, object]:
        """Advisory vision over the captured frame; one heavy generation (INV-023)."""
        assert self._vision is not None
        provider = cast(str, self._vision_provider)
        model_id = cast(str, self._vision_model)
        loop_id = str(uuid4())
        try:
            async with await binding.lifecycle.acquire_heavy(
                provider_id=provider, model_id=model_id, operation_id=loop_id
            ):
                result = await self._vision.run(
                    VisionLoopRequest(
                        loop_id=loop_id,
                        provider_id=provider,
                        model_id=model_id,
                        query_text=query,
                        frames=(frame,),
                        max_iterations=1,
                    )
                )
        except (VisionError, ValidationError):
            raise ToolStepFailed("Vision interpretation failed") from None
        first = result.interpretations[0]
        return {
            "advisory_summary": first.summary,
            "regions": [region.label for region in first.regions],
        }

    async def _in_worker(
        self, kill: ControlKillSwitch | None, function: Callable[[], _T]
    ) -> _T:
        """Every tool call runs on one worker thread, in submission order."""
        if self._worker is None:
            self._worker = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="phoenix-control"
            )
        worker = self._worker

        def run() -> _T:
            if kill is not None and kill.tripped:
                raise _Killed()
            return function()

        try:
            return await asyncio.get_running_loop().run_in_executor(worker, run)
        except _Killed:
            raise asyncio.CancelledError() from None

    # ---- LEVI

    def levi_responder(
        self,
        verifier: ModelBinding,
        guard: PromptInjectionGuard,
        *,
        original_request: str,
        evidence: ControlEvidence,
        grants: tuple[PermissionGrant, ...],
    ) -> "ControlEvidenceVerificationResponder":
        return ControlEvidenceVerificationResponder(
            ControlVerificationResponder(
                verifier, guard, original_request=original_request, evidence=evidence
            ),
            evidence,
            grants,
            self._policy_ids,
        )


@dataclass
class _Planned:
    kind: _ToolKind
    tool_id: str
    observation: dict[str, object]
    call: _Call | None = None
    build: Callable[[str], BrowserCall] | None = None
    query: str | None = None
    request: PermissionRequest | None = None
    approval: ControlApproval | None = None
    # Display-only context for the approval summary (never for authorization).
    window: _Window | None = None

    @property
    def risk(self) -> ToolRiskLevel:
        return CONTROL_TOOL_RISKS[self.tool_id]


def _summarize(item: _Planned) -> ApprovalSummary:
    request, call, window = item.request, item.call, item.window
    assert request is not None
    fields: dict[str, object] = {
        "request_id": request.request_id,
        "tool_id": request.tool_id,
        "risk_level": CONTROL_TOOL_RISKS[request.tool_id],
        "action": APPROVAL_ACTIONS[request.tool_id],
    }
    text: str | None = None
    if isinstance(call, BrowserCall):
        fields["page_url"] = call.url or call.expected_url
        fields["element"] = call.selector
        text = call.text
    elif isinstance(call, WindowsUICall):
        selector = call.selector
        if selector is not None:
            parts = (
                ("automation ID", selector.automation_id),
                ("name", selector.name),
                ("type", selector.control_type),
            )
            fields["element"] = ", ".join(
                f"{label} {value!r}" for label, value in parts if value
            )
        text = call.text
    elif isinstance(call, TerminalCall):
        fields["command"] = " ".join((call.profile_id, *call.arguments))
    if window is not None:
        name, path = _process_info(window.process_id)
        fields.update(
            window_title=window.title,
            process_id=window.process_id,
            process_name=name,
            executable_path=path,
        )
    if text is not None:
        if looks_like_secret(text):
            fields.update(
                text=f"[hidden: {len(text)} characters]",
                text_masked=True,
                note=SECRET_TEXT_NOTE,
            )
        else:
            fields["text"] = text
    return ApprovalSummary.model_validate(fields)


def _list_call() -> WindowsUICall:
    return WindowsUICall(
        operation=WindowsUIOperation.LIST_WINDOWS, max_items=MAX_LISTED_WINDOWS
    )


def _page_action(action: _ActionWire) -> Callable[[str], BrowserCall]:
    """A browser call on the page PHOENIX actually reached (never model text)."""

    def build(page_url: str) -> BrowserCall:
        operation = BrowserOperation(action.operation)
        fields: dict[str, object] = {
            "operation": operation,
            "session_id": BROWSER_SESSION_ID,
            "expected_url": page_url,
        }
        if operation != BrowserOperation.READ_PAGE:
            fields["selector"] = action.selector
        if operation in (BrowserOperation.READ_PAGE, BrowserOperation.INSPECT_ELEMENT):
            fields["max_chars"] = MAX_READ_CHARS
        if operation == BrowserOperation.FILL:
            fields["text"] = action.text
        try:
            return BrowserCall.model_validate(fields)
        except ValidationError:
            raise ToolStepRejected("Invalid control action") from None

    return build


def _windows_of(result: WindowsUIToolResult) -> tuple[_Window, ...]:
    return tuple(
        _Window(
            hwnd=window.hwnd,
            process_id=window.process_id,
            class_name=window.class_name,
            title=window.title,
        )
        for window in result.windows
    )


def _resolve(windows: tuple[_Window, ...], title: str) -> _Window:
    """Exactly one listed window with this exact title, or an explicit failure."""
    matches = [window for window in windows if window.title == title]
    if len(matches) != 1:
        raise ToolStepFailed("Window title not found or ambiguous")
    return matches[0]


def _execute(
    tool: object,
    call: _Call,
    request: PermissionRequest,
    grants: tuple[PermissionGrant, ...],
    decision: UserPermissionDecision | None,
) -> _Result:
    if isinstance(tool, BrowserTool) and isinstance(call, BrowserCall):
        return tool.execute(call, request, grants=grants, user_decision=decision)
    if isinstance(tool, WindowsUITool) and isinstance(call, WindowsUICall):
        return tool.execute(call, request, grants=grants, user_decision=decision)
    if isinstance(tool, ScreenshotCaptureTool) and isinstance(call, ScreenshotCall):
        return tool.execute(call, request, grants=grants, user_decision=decision)
    if isinstance(tool, TerminalTool) and isinstance(call, TerminalCall):
        return tool.execute(call, request, grants=grants, user_decision=decision)
    raise ToolStepRejected("Control tool and call disagree")


def _audited(result: _Result) -> bool:
    return (
        getattr(result, "permission_audit_id", None) is not None
        and getattr(result, "outcome_audit_id", None) is not None
    )


def _clip(text: str) -> tuple[str, bool]:
    return text[:MAX_STREAM_CHARS], len(text) > MAX_STREAM_CHARS


_WINDOW_FIELDS = {"title", "class_name", "enabled", "visible"}


def _observed(result: _Result) -> dict[str, object]:
    """Untrusted tool output for the guarded TOOL_OUTPUT item; never logged."""
    data: dict[str, object] = {}
    if isinstance(result, BrowserToolResult):
        if result.final_url is not None:
            data["url"] = result.final_url
        if result.title is not None:
            data["title"] = result.title
        if result.text is not None:
            data["text"] = result.text
        if result.element is not None:
            data["element"] = result.element.model_dump(mode="json")
        if result.truncated:
            data["truncated"] = True
    elif isinstance(result, WindowsUIToolResult):
        if result.windows:
            data["windows"] = [
                window.model_dump(mode="json", include=_WINDOW_FIELDS)
                for window in result.windows
            ]
        if result.window is not None:
            data["window"] = result.window.model_dump(
                mode="json", include=_WINDOW_FIELDS
            )
        if result.control is not None:
            data["control"] = result.control.model_dump(mode="json")
        if result.truncated:
            data["truncated"] = True
    elif isinstance(result, TerminalToolResult):
        stdout, cut_out = _clip(result.stdout)
        stderr, cut_err = _clip(result.stderr)
        data.update(
            exit_code=result.exit_code,
            stdout=stdout,
            stderr=stderr,
            truncated=cut_out
            or cut_err
            or result.stdout_truncated
            or result.stderr_truncated,
        )
    # A frame is never rendered: only the advisory description is (_interpret).
    return data


def _output_item(evidence: ControlEvidence) -> _Item:
    """The one guarded TOOL_OUTPUT item, identical for the answer turn and LEVI."""
    return (
        prompts.TOOL_OUTPUT_ITEM,
        UntrustedContentOrigin.TOOL_OUTPUT,
        evidence.source_id,
        "output",
        evidence.text,
    )


class ControlPlanningResponder:
    """AIZEN's planner with the control-enabled planning prompt (ADR-152)."""

    def __init__(self, binding: ModelBinding, *, max_steps: int) -> None:
        self._binding = binding
        self._max_steps = max_steps

    async def propose(self, request: PlanningRequest) -> PlanDraft:
        system = control_prompts.CONTROL_PLANNING_SYSTEM.format(
            max_steps=self._max_steps
        )
        text = await _generate(self._binding, "planner", system, request.objective)
        wire = parse_json_object(text, _PlanWire)
        if len(wire.steps) > self._max_steps:
            raise ResponderOutputError(ResponderErrorCode.SCHEMA)
        return PlanDraft(
            request_id=request.request_id,
            steps=tuple(
                PlanStep(step_id=f"s{index}", objective=step.objective)
                for index, step in enumerate(wire.steps, start=1)
            ),
        )


class ControlAssignmentResponder:
    """PHOENIX's step assignment with the computer_control domain described."""

    def __init__(
        self,
        binding: ModelBinding,
        guard: PromptInjectionGuard,
        *,
        allowed_domains: tuple[RoutingDomain, ...],
    ) -> None:
        self._descriptions = {
            **prompts.DOMAIN_DESCRIPTIONS,
            RoutingDomain.COMPUTER_CONTROL.value: (
                control_prompts.CONTROL_DOMAIN_DESCRIPTION
            ),
        }
        if not allowed_domains or any(
            domain.value not in self._descriptions for domain in allowed_domains
        ):
            raise ValueError("Every allowed domain needs a prompt description")
        self._binding = binding
        self._guard = guard
        self._allowed = allowed_domains

    async def assign(self, plan: AizenPlan) -> StepAssignments:
        system = prompts.ASSIGNMENT_SYSTEM.format(
            domains="\n".join(
                f"- {domain.value}: {self._descriptions[domain.value]}"
                for domain in self._allowed
            )
        )
        user = _evidence(
            self._guard,
            f"{plan.request_id}:assignment",
            "Assign one allowed domain to every plan step listed below.",
            tuple((step.step_id, step.objective) for step in plan.steps),
        )
        wire = parse_json_object(
            await _generate(self._binding, "assignment", system, user),
            _AssignmentsWire,
        )
        step_ids = [step.step_id for step in plan.steps]
        allowed = {domain.value: domain for domain in self._allowed}
        chosen: dict[str, RoutingDomain] = {}
        for item in wire.assignments:
            if item.step_id not in step_ids or item.step_id in chosen:
                raise ResponderOutputError(ResponderErrorCode.UNKNOWN_STEP)
            if item.domain not in allowed:
                raise ResponderOutputError(ResponderErrorCode.DISALLOWED_DOMAIN)
            chosen[item.step_id] = allowed[item.domain]
        if len(chosen) != len(step_ids):
            raise ResponderOutputError(ResponderErrorCode.UNKNOWN_STEP)
        return StepAssignments(
            request_id=plan.request_id,
            assignments=tuple(
                StepAssignment(step_id=identity, domain=chosen[identity])
                for identity in step_ids
            ),
        )


class ControlVerificationResponder(ModelVerificationResponder):
    """LEVI's model verifier for one control step, grounded in its tool output.

    The plain-text and size checks run first and the model is skipped if any
    fails; the model then sees the same guarded TOOL_OUTPUT item the answer
    turn saw. Wrap it in ControlEvidenceVerificationResponder.
    """

    def __init__(
        self,
        binding: ModelBinding,
        guard: PromptInjectionGuard,
        *,
        original_request: str,
        evidence: ControlEvidence,
        max_checks: int = 6,
    ) -> None:
        super().__init__(
            binding,
            guard,
            original_request=original_request,
            plain_text_required=True,
            max_checks=max_checks,
        )
        self._evidence = evidence

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        checks = self._deterministic(request.candidate_output)
        if not all(check.passed for check in checks):
            return VerificationDraft(
                verification_id=request.verification_id, checks=checks
            )
        model = UntrustedContentOrigin.MODEL_OUTPUT
        user = "\n\n".join(
            (
                f"Original request:\n{self._original_request}",
                f"Expected output:\n{request.expected_output_schema}",
                f"Completion rule:\n{request.completion_rule}",
                _guarded(
                    self._guard,
                    request.verification_id,
                    control_prompts.CONTROL_CANDIDATE_TASK,
                    (
                        (
                            prompts.OBJECTIVE_ITEM,
                            model,
                            prompts.OBJECTIVE_ITEM,
                            "output",
                            request.objective,
                        ),
                        _output_item(self._evidence),
                        (
                            prompts.CANDIDATE_ITEM,
                            model,
                            prompts.CANDIDATE_ITEM,
                            "output",
                            request.candidate_output,
                        ),
                    ),
                ),
            )
        )
        return _model_checks(
            request,
            checks,
            await _generate(
                self._binding,
                "control_verifier",
                control_prompts.CONTROL_VERIFICATION_SYSTEM.format(
                    max_checks=self._max_checks
                ),
                user,
            ),
            self._max_checks,
        )


class ControlEvidenceVerificationResponder:
    """LEVI for control steps: deterministic tool evidence first, then the model.

    Every action PHOENIX ran must correlate with the call and request PHOENIX
    issued, be ALLOWED by a caller grant, PHOENIX's teardown rule or the user's
    explicit P4 decision, have been attempted and succeeded, and carry both
    durable audit IDs when it is P3/P4 (INV-007). A failed check is final.
    """

    def __init__(
        self,
        inner: ModelVerificationResponder,
        evidence: ControlEvidence,
        grants: tuple[PermissionGrant, ...],
        policy_ids: frozenset[str],
    ) -> None:
        self._inner = inner
        self._evidence = evidence
        self._grant_ids = frozenset(grant.grant_id for grant in grants)
        self._policy_ids = policy_ids

    def _correlates(self, action: ControlActionEvidence) -> bool:
        result, request = action.result, action.request
        decision = result.permission_decision
        risk = CONTROL_TOOL_RISKS.get(action.tool_id)
        reason = decision.reason_code
        authorized = (
            (
                reason == DecisionReason.MATCHING_GRANT
                and decision.matched_grant_id in self._grant_ids
            )
            or (
                reason == DecisionReason.MATCHING_POLICY
                and decision.matched_policy_rule_id in self._policy_ids
                and action.tool_id == "browser.session_close"
            )
            or (
                reason == DecisionReason.EXPLICIT_USER_APPROVAL
                and decision.user_decision_id in self._evidence.approval_ids
                and risk == ToolRiskLevel.P4
            )
        )
        return (
            risk is not None
            and risk == action.risk_level == decision.risk_level
            and risk not in (ToolRiskLevel.P0, ToolRiskLevel.P5)
            and result.call_id == action.call_id
            and result.tool_id == request.tool_id == decision.tool_id == action.tool_id
            and result.resource == request.resource
            and decision.request_id == request.request_id
            and request.actor_id == decision.actor_id == CONTROL_ACTOR_ID
            and request.reason == CONTROL_TOOL_REASON
            and decision.status == PermissionStatus.ALLOWED
            and authorized
            and result.attempted is True
            and (
                result.status == ToolResultStatus.SUCCESS
                or (
                    result.status == ToolResultStatus.PARTIAL_SUCCESS
                    and risk == ToolRiskLevel.P1
                )
            )
            and (not audit_required_for_execution(risk) or _audited(result))
        )

    def _check(self) -> VerificationCheck:
        actions = self._evidence.actions
        passed = bool(actions) and all(self._correlates(item) for item in actions)
        return VerificationCheck(
            check_id="deterministic.control_evidence",
            criterion="Every control action correlates with a PHOENIX-issued call",
            passed=passed,
            evidence_summary="Each action was permitted, attempted and succeeded"
            if passed
            else "Control evidence does not correlate or is not a permitted success",
        )

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        check = self._check()
        if not check.passed:
            return VerificationDraft(
                verification_id=request.verification_id, checks=(check,)
            )
        draft = await self._inner.verify(request)
        return VerificationDraft(
            verification_id=draft.verification_id, checks=(check, *draft.checks)
        )
