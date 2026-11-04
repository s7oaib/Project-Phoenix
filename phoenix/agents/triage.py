"""PHX-105 request triage: PHOENIX's routing hint for a request without a path.

When the caller gives no path, PHOENIX asks one structured (thinking off)
generation on the ``triage`` tier (T1 qwen3.5:4b, then T0 qwen3.5:2b) whether
the request goes DIRECT or COMPLEX. The model sees ``TRIAGE_SYSTEM`` and the
user's request text only, as the user message, exactly as the DIRECT and
planning roles see it (ADR-081: only runtime constants and the user's own
request appear as raw prompt text); the request is not wrapped by
PromptInjectionGuard, whose origins are all untrusted third-party content and
whose blocking signals would turn the user's own wording into a fallback.

The reply must be exactly ``{"path": "direct"}`` or ``{"path": "complex"}``,
parsed by the frozen responder rules. The decision is a routing hint only: it
grants nothing (INV-003) and PHOENIX still runs the chosen path with all its
checks (INV-001). Any failure falls back to COMPLEX, AIZEN planning plus LEVI
verification, observably (INV-022): the runtime records the outcome and reason
code in MissionTrace and publishes them on PHOENIX's agent events.
Cancellation is never a fallback; it propagates.

PHX-131 (ADR-225): before the model, a deterministic rule
(``rule_decides_direct``) sends a clearly simple request DIRECT without a
generation, recorded as ``rule``. It only ever chooses DIRECT; anything it is
not sure of goes to the model triage above, unchanged.

PHX-140 (ADR-266): before that, ``rule_wants_research`` sends a clearly
current/online question (news, weather, prices, scores, "who won", today, a
year from the current one on) COMPLEX, where the research planner can search
the web, only while web research is enabled. Recorded as ``decided`` (the
``rule`` outcome stays DIRECT-only, migration 0010); every search still needs
the user's permission.
"""

import re
from datetime import date
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from phoenix.agents import prompts
from phoenix.agents.orchestrator import OrchestrationPath, PhoenixRequest
from phoenix.agents.responders import (
    ModelBinding,
    ResponderErrorCode,
    ResponderOutputError,
    _generate,
    parse_json_object,
)
from phoenix.agents.router import AgentModelRoutingError
from phoenix.models.provider import ModelProviderError, ProviderUnavailableError

# The tier-table row, the generation record role and the agent event role.
TRIAGE_ROLE = "triage"


class TriageOutcome(StrEnum):
    """Who decided a mission's path."""

    DECIDED = "decided"  # the triage model chose it
    FALLBACK = "fallback"  # triage failed, so COMPLEX by rule
    CLIENT = "client"  # the caller chose it; triage did not run
    RULE = "rule"  # PHX-131: the pre-triage rule chose DIRECT; no model ran


class TriageFallbackReason(StrEnum):
    """Why triage fell back to COMPLEX; a closed code, never model text."""

    TRUNCATED = ResponderErrorCode.TRUNCATED.value
    OVERSIZED = ResponderErrorCode.OVERSIZED.value
    NOT_JSON = ResponderErrorCode.NOT_JSON.value
    SCHEMA = ResponderErrorCode.SCHEMA.value
    INVALID_TEXT = ResponderErrorCode.INVALID_TEXT.value
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_ERROR = "provider_error"
    NO_ELIGIBLE_MODEL = "no_eligible_model"
    INTERNAL_ERROR = "internal_error"


class _TriageWire(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    path: Literal["direct", "complex"]


class ModelTriageResponder:
    """One structured generation that proposes DIRECT or COMPLEX."""

    def __init__(self, binding: ModelBinding) -> None:
        self._binding = binding

    async def classify(self, request: PhoenixRequest) -> OrchestrationPath:
        text = await _generate(
            self._binding, TRIAGE_ROLE, prompts.TRIAGE_SYSTEM, request.text
        )
        return OrchestrationPath(parse_json_object(text, _TriageWire).path)


# PHX-131 (ADR-226): the pre-triage rule. Short, one plain question or
# instruction, no character outside plain prose, no dotted name (file, URL,
# version), no list or multi-step connector and no word that points at a file,
# the workspace, memory, the web, the desktop, a tool or something time-bound.
RULE_MAX_WORDS = 25
RULE_MAX_CHARS = 200
_RULE_PLAIN = re.compile(r"[A-Za-z0-9 ,.?!'\":;()+*=%-]+")
_RULE_SHAPE = re.compile(r"\.\S|\b\d+[.)]\s|\bafter that\b")
_RULE_WORD = re.compile(r"[a-z0-9]+")
_RULE_BLOCKED = frozenset(
    """
    file files filename filenames folder folders directory directories dir path
    paths drive disk txt csv pdf docx xlsx md
    workspace workspaces upload uploads uploaded uploading attach attached
    attachment attachments
    memory memories remember remembered recall forget note notes notebook
    web website websites webpage site sites internet online url urls link links
    http https www google browse browsing browser youtube video videos
    research researching search searches searched searching find finding
    lookup look looking
    window windows app apps application applications screen screenshot desktop
    tab tabs program programs setting settings system computer pc mouse
    keyboard volume brightness wifi bluetooth
    document documents doc docs spreadsheet excel
    create creates created creating make makes making generate build write
    writes writing draft save saves saved saving export download downloads
    install delete remove rename move copy paste edit modify update
    read reads reading open opens opened opening close show list
    click clicks clicked type types typed typing press scroll drag select
    run runs running execute launch start stop kill command commands terminal
    shell powershell cmd script scripts
    check checks verify compare comparison evaluate analyze analyse analysis
    rank recommend plan planning schedule design outline step steps breakdown
    summarize summarise summary review improve
    weather news today tonight tomorrow yesterday latest current currently
    recent price prices stock stocks score scores time date clock
    my mine our ours above previous earlier
    email emails mail message messages send call remind reminder alarm timer
    calendar tool tools agent permission
    image images photo photos picture pictures camera microphone audio record
    play music song
    then afterwards afterward next finally lastly firstly secondly thirdly
    """.split()
)


def rule_decides_direct(text: str) -> bool:
    """True only for a clearly simple request (PHX-131); else model triage.

    Deterministic and conservative: it never chooses COMPLEX and never grants
    anything; DIRECT still runs with all PHOENIX's checks (INV-001, INV-003).
    """
    lowered = text.strip().lower()
    return (
        0 < len(lowered) <= RULE_MAX_CHARS
        and len(lowered.split()) <= RULE_MAX_WORDS
        and _RULE_PLAIN.fullmatch(lowered) is not None
        and _RULE_SHAPE.search(lowered) is None
        and lowered.count("?") <= 1
        and _RULE_BLOCKED.isdisjoint(_RULE_WORD.findall(lowered))
    )


# PHX-140 (ADR-266): current/online wording, and local wording that vetoes it.
_RESEARCH_WORDS = frozenset(
    "news headlines latest weather forecast price prices score scores "
    "today tonight".split()
)
_RESEARCH_VETO = frozenset(
    """
    my mine me i file files folder document documents doc docs note notes
    memory memories remember remind reminder alarm timer calendar schedule
    open play write draft create make save screen app apps email time date day
    """.split()
)
_YEAR = re.compile(r"\b(?:19|20)\d\d\b")


def rule_wants_research(text: str, year: int | None = None) -> bool:
    """True only for a short, clearly current/online question (PHX-140).

    Deterministic and conservative; it grants nothing. ``year`` (tests)
    replaces the current year.
    """
    lowered = text.strip().lower()
    words = set(_RULE_WORD.findall(lowered))
    current = date.today().year if year is None else year
    return (
        0 < len(lowered) <= RULE_MAX_CHARS
        and len(lowered.split()) <= RULE_MAX_WORDS
        and _RESEARCH_VETO.isdisjoint(words)
        and (
            not _RESEARCH_WORDS.isdisjoint(words)
            or "who won" in " ".join(lowered.split())
            or any(int(found) >= current for found in _YEAR.findall(lowered))
        )
    )


def fallback_reason(error: Exception) -> TriageFallbackReason:
    """The closed reason for a triage failure; anything unknown is internal."""
    if isinstance(error, ResponderOutputError):
        try:
            return TriageFallbackReason(error.code.value)
        except ValueError:
            return TriageFallbackReason.SCHEMA
    if isinstance(error, TimeoutError):
        return TriageFallbackReason.TIMEOUT
    if isinstance(error, ProviderUnavailableError):
        return TriageFallbackReason.PROVIDER_UNAVAILABLE
    if isinstance(error, ModelProviderError):
        return TriageFallbackReason.PROVIDER_ERROR
    if isinstance(error, AgentModelRoutingError):
        return TriageFallbackReason.NO_ELIGIBLE_MODEL
    return TriageFallbackReason.INTERNAL_ERROR
