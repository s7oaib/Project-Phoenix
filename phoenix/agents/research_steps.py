"""PHX-111 web research steps (URAHARA) for the PHOENIX mission runtime.

A research step makes at most one research call between two generations, like a
PHX-046 document step:

1. URAHARA (``research_specialist``) may only propose one search query.
2. PHOENIX builds the ``ResearchCall`` and ``PermissionRequest`` itself. The
   WebResearchTool decides through ToolRegistry and PermissionEngine (INV-004):
   without the user's grant for the search provider nothing leaves the PC and
   the mission waits for permission (ADR-137).
3. The answer turn (``research_answer``) sees every fetched source only as
   guarded untrusted WEB_PAGE / WEB_SEARCH items (INV-017) and replies with
   claims, each citing a source URL and a quoted span.
4. LEVI checks the step deterministically (ADR-139): the tool evidence
   correlates with PHOENIX's call and the caller's grant; every cited URL is a
   fetched source (no fabricated URL); every quote occurs in that source's
   content; every number in a claim occurs in its quotes; and the candidate is
   exactly PHOENIX's rendering of the parsed claims. The verdict is all checks.

Grants come only from the runtime caller (INV-003). Only P1 is reachable, so no
audit rows are written (INV-007). Source text never enters the trace, events or
errors: evidence keeps IDs, statuses, counts and hashes. Research generations
are not retried by MAHORAGA (they would repeat a permissioned network call), and
a rate-limited provider fails the mission with the closed ``rate_limited`` code.
"""

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from phoenix.agents import prompts
from phoenix.agents.capability_steps import capability_grants
from phoenix.agents.handoff import HandoffEnvelope
from phoenix.agents.mission_events import MissionEvents, elapsed_ms
from phoenix.agents.planner import PlanDraft, PlanningRequest, PlanStep
from phoenix.agents.responders import (
    ModelBinding,
    ResponderErrorCode,
    ResponderOutputError,
    _generate,
    parse_json_object,
)
from phoenix.agents.tool_steps import (
    ToolPermissionRequired,
    ToolStepFailed,
    ToolStepRejected,
    _guarded,
    _Item,
    document_grants,
)
from phoenix.agents.verifier import (
    VerificationCheck,
    VerificationDraft,
    VerificationRequest,
)
from phoenix.core.events import EventType
from phoenix.models.provider import Identifier, NonnegativeInt
from phoenix.security.permissions import (
    DecisionReason,
    GrantDuration,
    PermissionGrant,
    PermissionRequest,
    PermissionStatus,
)
from phoenix.security.prompt_injection import (
    PromptInjectionGuard,
    UntrustedContentOrigin,
)
from phoenix.tools.registry import ToolResultStatus, ToolRiskLevel
from phoenix.tools.web_research import (
    RESEARCH_ACTION,
    RESEARCH_TOOL_ID,
    ResearchCall,
    ResearchSourceKind,
    WebResearchErrorCode,
    WebResearchInputError,
    WebResearchResult,
    WebResearchTool,
)

RESEARCH_ACTOR_ID = "web_research"
# Fixed so caller grants can match exactly; never derived from model text.
RESEARCH_TOOL_REASON = (
    "PHOENIX research step: send web search queries to the search provider and"
    " read the top result pages"
)
RESEARCH_TOOL_IDS: tuple[str, ...] = (RESEARCH_TOOL_ID,)
RESEARCH_ROLE = "research"
MAX_CLAIMS = 8
MAX_CITATIONS = 3
MIN_QUOTE_CHARS = 12
_GRANT_DURATIONS = (GrantDuration.SESSION, GrantDuration.PERSISTENT)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_FOLD = str.maketrans(
    {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-"} | {"—": "-", " ": " "}
)


class ResearchRateLimited(ToolStepFailed):
    """The search provider throttled PHOENIX; explicit, never retried here."""


def research_grants(grants: object) -> tuple[PermissionGrant, ...]:
    """Caller grants for research steps only: SESSION or PERSISTENT, P1, exact."""
    if type(grants) not in (tuple, list):
        raise ValueError("Grants require an ordered sequence")
    accepted: list[PermissionGrant] = []
    for grant in cast(Sequence[object], grants):
        if type(grant) is not PermissionGrant:
            raise ValueError("Expected PermissionGrant instances")
        grant = PermissionGrant.model_validate(grant)
        if (
            grant.duration not in _GRANT_DURATIONS
            or grant.risk_level != ToolRiskLevel.P1
            or grant.actor_id != RESEARCH_ACTOR_ID
            or grant.reason != RESEARCH_TOOL_REASON
            or grant.tool_id != RESEARCH_TOOL_ID
            or grant.action != RESEARCH_ACTION
            or not grant.resource.startswith("search:")
        ):
            raise ValueError("Grant is outside the research step scope")
        accepted.append(grant)
    return tuple(accepted)


def mission_grants(
    grants: object, *, capabilities: bool = False
) -> tuple[PermissionGrant, ...]:
    """Caller grants for document or research steps; nothing else, no duplicates.

    PHX-120 (ADR-162): with ``capabilities`` a non-research grant may also be a
    PHX-113 media or document grant (``capability_grants``, which passes PHX-046
    document grants unchanged).
    """
    if type(grants) not in (tuple, list):
        raise ValueError("Grants require an ordered sequence")
    other = capability_grants if capabilities else document_grants
    accepted: list[PermissionGrant] = []
    for grant in cast(Sequence[object], grants):
        try:
            accepted.extend(research_grants([grant]))
        except ValueError:
            accepted.extend(other([grant]))
    ids = [grant.grant_id for grant in accepted]
    scopes = [
        (g.tool_id, g.action, g.resource, g.duration, g.session_id) for g in accepted
    ]
    if len(set(ids)) != len(ids) or len(set(scopes)) != len(scopes):
        raise ValueError("Duplicate grant ID or effective scope")
    return tuple(accepted)


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


class ResearchCallRecord(_Value):
    """Privacy-preserving evidence of one research call: no query or source text."""

    step_id: Identifier
    call_id: Identifier
    request_id: Identifier
    tool_id: Identifier
    decision_status: PermissionStatus
    tool_status: ToolResultStatus
    attempted: bool = Field(strict=True)
    query_sha256: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    sources: NonnegativeInt = 0
    pages_read: NonnegativeInt = 0
    page_failures: NonnegativeInt = 0
    error_code: WebResearchErrorCode | None = None


class ResearchCitation(_Value):
    url: str
    title: str
    quote: str


class ResearchClaim(_Value):
    text: str
    citations: tuple[ResearchCitation, ...]


@dataclass(frozen=True)
class ResearchEvidence:
    """What PHOENIX issued, what the tool returned and the parsed claims."""

    call: ResearchCall
    request: PermissionRequest
    result: WebResearchResult
    claims: tuple[ResearchClaim, ...]

    def cited_sources(self) -> tuple[tuple[str, str], ...]:
        """(title, url) of every fetched source a claim cites, in citation order."""
        fetched = {source.url: source.title for source in self.result.sources}
        found: dict[str, str] = {}
        for claim in self.claims:
            for citation in claim.citations:
                if citation.url in fetched and citation.url not in found:
                    found[citation.url] = fetched[citation.url]
        return tuple((title, url) for url, title in found.items())


class _WireModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


_Query = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)
]
_Text = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]
_Url = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)
]


class _SearchWire(_WireModel):
    query: _Query


class _SearchEnvelope(_WireModel):
    search_request: _SearchWire


class _CitationWire(_WireModel):
    url: _Url
    quote: _Text


class _ClaimWire(_WireModel):
    text: _Text
    citations: list[_CitationWire] = Field(min_length=1, max_length=MAX_CITATIONS)


class _AnswerWire(_WireModel):
    claims: list[_ClaimWire] = Field(min_length=1, max_length=MAX_CLAIMS)


class _PlanStepWire(_WireModel):
    objective: _Text


class _PlanWire(_WireModel):
    steps: list[_PlanStepWire] = Field(min_length=1)


class ResearchPlanningResponder:
    """AIZEN's planner when web research is offered (v5 research_planner).

    The same contract as ModelPlanningResponder; only the system prompt also
    allows web search steps. Without a research tool the runtime keeps the
    unchanged PLANNING_SYSTEM.
    """

    def __init__(self, binding: ModelBinding, *, max_steps: int) -> None:
        self._binding = binding
        self._max_steps = max_steps

    async def propose(self, request: PlanningRequest) -> PlanDraft:
        system = prompts.RESEARCH_PLANNING_SYSTEM.format(max_steps=self._max_steps)
        text = await _generate(
            self._binding, "research_planner", system, request.objective
        )
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


def _source_items(result: WebResearchResult) -> tuple[_Item, ...]:
    """Each source as three guarded untrusted items; identical for every turn."""
    items: list[_Item] = []
    for source in result.sources:
        origin = (
            UntrustedContentOrigin.WEB_PAGE
            if source.kind == ResearchSourceKind.PAGE_TEXT
            else UntrustedContentOrigin.WEB_SEARCH
        )
        sid = source.source_id
        items += [
            (f"{sid}_url", origin, sid, "url", source.url),
            (f"{sid}_title", origin, sid, "title", source.title or "(untitled)"),
            (f"{sid}_content", origin, sid, "content", source.content or "(empty)"),
        ]
    return tuple(items)


def _clean_quote(quote: str) -> str:
    return quote.strip().strip("\"'“”‘’").strip(". …")


def _normalized(text: str) -> str:
    return " ".join(text.translate(_FOLD).casefold().split())


def render_claims(claims: Sequence[ResearchClaim]) -> str:
    """PHOENIX's canonical plain-text rendering of cited claims."""
    lines: list[str] = []
    for index, claim in enumerate(claims, start=1):
        lines.append(f"{index}. {claim.text}")
        for citation in claim.citations:
            lines.append(f"   Source: {citation.title} <{citation.url}>")
            lines.append(f'   Quote: "{citation.quote}"')
    return "\n".join(lines)


def sources_footer(evidence: Sequence[ResearchEvidence]) -> str:
    """The deterministic citation list PHOENIX appends to the final answer."""
    seen: dict[str, str] = {}
    for item in evidence:
        for title, url in item.cited_sources():
            seen.setdefault(url, title)
    if not seen:
        return ""
    lines = ["Sources:"]
    lines += [f"- {title or url}: {url}" for url, title in seen.items()]
    return "\n".join(lines)


class ResearchStepRunner:
    """One research step: propose a query, permission-check and run, then cite."""

    def __init__(self, tool: WebResearchTool, guard: PromptInjectionGuard) -> None:
        self._tool = tool
        self._guard = guard

    async def perform(
        self,
        binding: ModelBinding,
        handoff: HandoffEnvelope,
        *,
        user_request: str,
        evidence: tuple[tuple[str, str], ...],
        grants: tuple[PermissionGrant, ...],
        session_id: str | None,
        record: Callable[[ResearchCallRecord], None],
        events: MissionEvents | None = None,
    ) -> tuple[str, ResearchEvidence]:
        model = UntrustedContentOrigin.MODEL_OUTPUT
        step_id = handoff.step_id

        async def emit(event_type: EventType, **payload: str | int) -> None:
            if events is not None:
                await events.emit(
                    event_type, agent_id=RESEARCH_ACTOR_ID, step_id=step_id, **payload
                )

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
            "research_specialist",
            prompts.RESEARCH_SPECIALIST_SYSTEM,
            "\n\n".join(
                (
                    *fixed,
                    _guarded(
                        self._guard,
                        f"{boundary}:tool",
                        prompts.RESEARCH_TOOL_TASK,
                        (objective, *prior),
                    ),
                )
            ),
        )
        wire = parse_json_object(proposal, _SearchEnvelope).search_request
        try:
            call = ResearchCall(query=wire.query)
            intent = self._tool.describe(call)
        except (ValidationError, WebResearchInputError):
            await emit(EventType.TOOL_FAILED, code="tool_rejected")
            raise ToolStepRejected("Invalid research request") from None
        request = PermissionRequest(
            request_id=str(uuid4()),
            actor_id=RESEARCH_ACTOR_ID,
            tool_id=intent.tool_id,
            action=intent.action,
            resource=intent.resource,
            reason=RESEARCH_TOOL_REASON,
            requested_at=datetime.now(UTC),
            task_id=handoff.task_id,
            session_id=session_id,
        )
        ids = {"tool_id": intent.tool_id, "call_id": call.call_id}
        await emit(
            EventType.TOOL_REQUESTED,
            **ids,
            operation=RESEARCH_ACTION,
            permission_request_id=request.request_id,
        )
        await emit(EventType.TOOL_STARTED, **ids)
        clock = time.monotonic()
        try:
            result = WebResearchResult.model_validate(
                await self._tool.execute(call, request, grants=grants)
            )
        except BaseException as interrupted:
            code = (
                "cancelled"
                if not isinstance(interrupted, Exception)
                else ("internal_error")
            )
            await emit(
                EventType.TOOL_FAILED, **ids, code=code, duration_ms=elapsed_ms(clock)
            )
            raise
        status = result.permission_decision.status
        record(
            ResearchCallRecord(
                step_id=step_id,
                call_id=call.call_id,
                request_id=request.request_id,
                tool_id=intent.tool_id,
                decision_status=status,
                tool_status=result.status,
                attempted=result.attempted,
                query_sha256=result.query_sha256,
                sources=len(result.sources),
                pages_read=result.pages_read,
                page_failures=result.page_failures,
                error_code=result.error_code,
            )
        )
        ended = {**ids, "tool_status": result.status.value}
        if status == PermissionStatus.APPROVAL_REQUIRED:
            await emit(
                EventType.PERMISSION_REQUESTED,
                **ids,
                permission_request_id=request.request_id,
                risk_level=intent.risk_level.value,
            )
            raise ToolPermissionRequired(request)
        failure: tuple[str, Exception] | None = None
        if status != PermissionStatus.ALLOWED:
            failure = "tool_rejected", ToolStepRejected("Permission was not granted")
        elif result.error_code == WebResearchErrorCode.RATE_LIMITED:
            failure = "rate_limited", ResearchRateLimited("Search provider throttled")
        elif result.status != ToolResultStatus.SUCCESS:
            failure = "tool_failed", ToolStepFailed("Web research failed")
        if failure is not None:
            code, error = failure
            await emit(
                EventType.TOOL_FAILED,
                **ended,
                code=code,
                decision=status.value,
                duration_ms=elapsed_ms(clock),
            )
            raise error
        await emit(EventType.TOOL_COMPLETED, **ended, duration_ms=elapsed_ms(clock))
        answer = await _generate(
            binding,
            "research_answer",
            prompts.RESEARCH_ANSWER_SYSTEM,
            "\n\n".join(
                (
                    *fixed,
                    _guarded(
                        self._guard,
                        boundary,
                        prompts.RESEARCH_ANSWER_TASK,
                        (objective, *_source_items(result), *prior),
                    ),
                )
            ),
        )
        parsed = parse_json_object(answer, _AnswerWire)
        titles = {source.url: source.title for source in result.sources}
        claims = tuple(
            ResearchClaim(
                text=" ".join(claim.text.split()),
                citations=tuple(
                    ResearchCitation(
                        url=citation.url,
                        # An unknown URL keeps no title; LEVI rejects it.
                        title=titles.get(citation.url, "unknown source") or "untitled",
                        quote=" ".join(_clean_quote(citation.quote).split()),
                    )
                    for citation in claim.citations
                ),
            )
            for claim in parsed.claims
        )
        return render_claims(claims), ResearchEvidence(
            call=call, request=request, result=result, claims=claims
        )


class ResearchVerificationResponder:
    """LEVI's deterministic responder for one research step (ADR-139).

    No model generation: every check is computed from PHOENIX's own evidence,
    so injected source text cannot influence the verdict.
    """

    def __init__(
        self, evidence: ResearchEvidence, grants: tuple[PermissionGrant, ...]
    ) -> None:
        self._evidence = evidence
        self._grant_ids = frozenset(grant.grant_id for grant in grants)

    def _tool_check(self) -> VerificationCheck:
        call, request, result = (
            self._evidence.call,
            self._evidence.request,
            self._evidence.result,
        )
        decision = result.permission_decision
        passed = (
            result.call_id == call.call_id
            and decision.request_id == request.request_id
            and decision.actor_id == request.actor_id == RESEARCH_ACTOR_ID
            and result.tool_id == decision.tool_id == request.tool_id
            and result.tool_id == RESEARCH_TOOL_ID
            and result.resource == request.resource
            and decision.risk_level == ToolRiskLevel.P1
            and decision.status == PermissionStatus.ALLOWED
            and decision.reason_code == DecisionReason.MATCHING_GRANT
            and decision.matched_grant_id in self._grant_ids
            and result.attempted is True
            and result.status == ToolResultStatus.SUCCESS
            and bool(result.sources)
        )
        return _check(
            "deterministic.research_evidence",
            "Research evidence correlates with the PHOENIX-issued call and a grant",
            passed,
            "Allowed by a caller grant; the research call succeeded",
            "Research evidence does not correlate or is not an allowed success",
        )

    def _citation_checks(self, candidate: str) -> tuple[VerificationCheck, ...]:
        claims = self._evidence.claims
        sources = {s.url: _normalized(s.content) for s in self._evidence.result.sources}
        citations = [c for claim in claims for c in claim.citations]
        known = bool(claims) and all(c.url in sources for c in citations)
        quoted = known and all(
            len(_normalized(c.quote)) >= MIN_QUOTE_CHARS
            and _normalized(c.quote) in sources[c.url]
            for c in citations
        )
        numbers = all(
            all(
                number.replace(",", "")
                in " ".join(_normalized(c.quote) for c in claim.citations).replace(
                    ",", ""
                )
                for number in _NUMBER.findall(claim.text)
            )
            for claim in claims
        )
        rendered = bool(claims) and candidate == render_claims(claims)
        return (
            _check(
                "deterministic.citation_urls",
                "Every cited URL is a source PHOENIX fetched (no fabricated URL)",
                known,
                "Every citation names a fetched source URL",
                "A citation names a URL PHOENIX did not fetch",
            ),
            _check(
                "deterministic.citation_quotes",
                "Every quote occurs in the cited source's fetched content",
                quoted,
                "Every quote was found in its cited source",
                "A quote is missing from, or too short for, its cited source",
            ),
            _check(
                "deterministic.claim_numbers",
                "Every number in a claim occurs in that claim's quotes",
                numbers,
                "Every number is supported by a quote",
                "A claim states a number that none of its quotes contains",
            ),
            _check(
                "deterministic.rendering",
                "The step output is PHOENIX's rendering of the cited claims",
                rendered,
                "The output matches the parsed claims exactly",
                "The output differs from the parsed claims",
            ),
        )

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        tool = self._tool_check()
        checks: tuple[VerificationCheck, ...] = (tool,)
        if tool.passed:
            checks += self._citation_checks(request.candidate_output)
        return VerificationDraft(verification_id=request.verification_id, checks=checks)


def _check(
    check_id: str, criterion: str, passed: bool, good: str, bad: str
) -> VerificationCheck:
    return VerificationCheck(
        check_id=check_id,
        criterion=criterion,
        passed=passed,
        evidence_summary=good if passed else bad,
    )
