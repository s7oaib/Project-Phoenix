"""PHX-110 knowledge steps: PHOENIX-run retrieval over the user's own knowledge.

A knowledge step (domain ``knowledge_rag``, owned by the ``knowledge_rag``
agent) makes exactly one retrieval, then at most one generation:

1. PHOENIX, not the model, builds the retrieval. The query is the planned step
   objective and the scope is the caller's KnowledgeSource (the user's indexed
   uploads and the user memory scope). The model proposes nothing and chooses
   no scope.
2. ``knowledge.retrieve`` is a P0 ToolDefinition (docs/08: memory retrieval is
   internal). PermissionEngine evaluates the PHOENIX-issued PermissionRequest
   and only ALLOWED proceeds. P0 writes no audit rows (INV-007 covers P3/P4).
3. Each retrieved document chunk or memory reaches the answer turn only as a
   numbered guarded item (``UPLOADED_DOCUMENT`` or ``EXTERNAL_TEXT``, INV-017).
   Numbers are mission-wide, so a later step continues the numbering. With no
   sources the step output is PHOENIX's fixed no-answer sentence and no
   generation runs.
4. LEVI checks deterministically before any model check: the retrieval and
   permission evidence correlate with what PHOENIX issued; the candidate cites
   only numbers PHOENIX issued for this step (or is exactly the no-answer
   sentence); every claim that cites a source shares a term with it or takes
   all its numbers from it, and every number the step states comes from a
   cited source, the objective, the request or an earlier verified output. The
   ``knowledge_verifier`` model then judges support, seeing only the cited
   sources. The verdict stays LEVI's all-checks rule (INV-008).

The final answer may repeat the markers. When a mission ran a knowledge step,
LEVI also checks deterministically that every marker in the final answer is a
source a verified knowledge step cited, and that the cited claim is supported.

Retrieved content never enters the trace, events or errors: RetrievalRecord and
MissionCitation hold IDs, hashes, counts and provenance labels only. Failures
are explicit (``tool_failed``, ``tool_rejected``; INV-021). Like a document
step, the step's own generation is not retried (PHX-109 R10); its LEVI
generation is. No events are added: the PHX-103 allowlist is unchanged, so the
step publishes the specialist agent phase and LEVI's verification events only.
"""

import asyncio
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Final, Literal, Protocol, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from phoenix.agents import prompts
from phoenix.agents.handoff import HandoffEnvelope
from phoenix.agents.responders import (
    ModelBinding,
    ModelVerificationResponder,
    _generate,
)
from phoenix.agents.tool_steps import (
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
    VerificationResponder,
)
from phoenix.memory.schema import MemoryType
from phoenix.models.provider import Identifier, NonnegativeInt
from phoenix.rag.citations import CitationRenderer, ProvenanceCitationService
from phoenix.rag.retrieval import ContextItem, RetrievalContextResult
from phoenix.security.permissions import (
    DecisionReason,
    PermissionDecision,
    PermissionEngine,
    PermissionRequest,
    PermissionStatus,
)
from phoenix.security.prompt_injection import (
    PromptInjectionGuard,
    UntrustedContentOrigin,
)
from phoenix.tools.registry import ToolDefinition, ToolRegistry, ToolRiskLevel

KNOWLEDGE_ACTOR_ID = "knowledge_rag"
KNOWLEDGE_TOOL_ID: Final = "knowledge.retrieve"
KNOWLEDGE_ACTION = "retrieve"
KNOWLEDGE_RESOURCE = "knowledge:user"
# Fixed; never derived from model text.
KNOWLEDGE_TOOL_REASON = "PHOENIX knowledge step: search the user's own knowledge"
MAX_CITATION_ORDINAL = 999
_MARKER = re.compile(r"\[(\d{1,3})\]")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
# Common words that never count as shared evidence between a claim and a source.
_STOPWORDS = frozenset(
    {
        "about",
        "above",
        "after",
        "again",
        "also",
        "answer",
        "because",
        "been",
        "before",
        "being",
        "both",
        "does",
        "document",
        "documents",
        "each",
        "from",
        "have",
        "here",
        "into",
        "memory",
        "memories",
        "more",
        "most",
        "only",
        "other",
        "over",
        "said",
        "says",
        "should",
        "some",
        "source",
        "sources",
        "states",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "under",
        "user",
        "users",
        "very",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "will",
        "with",
        "would",
        "your",
    }
)


def knowledge_tool_definitions() -> tuple[ToolDefinition, ...]:
    """The one P0 retrieval definition; PermissionEngine derives risk from it."""
    return (
        ToolDefinition(
            tool_id=KNOWLEDGE_TOOL_ID,
            description=(
                "Search the user's indexed uploads and saved memories with a"
                " PHOENIX-built query and scope (read-only)"
            ),
            input_schema={
                "type": "object",
                "properties": {"query_text": {"type": "string"}},
                "required": ["query_text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"items": {"type": "array"}},
                "required": ["items"],
                "additionalProperties": False,
            },
            risk_level=ToolRiskLevel.P0,
            permission_requirement="P0: internal read-only knowledge retrieval",
            timeout_seconds=60,
            cancellation_supported=True,
            audit_behavior="no mandatory durable audit",
            supported_platforms=("Windows", "Linux", "macOS"),
            supported_environments=("local",),
        ),
    )


class KnowledgeSource(Protocol):
    """The caller's retrieval scope; it owns embeddings, storage and bounds."""

    async def available(self) -> bool:
        """True when at least one indexed document or memory exists."""
        ...

    async def retrieve(
        self, *, retrieval_id: str, query_text: str
    ) -> RetrievalContextResult:
        """Scoped, bounded context for this retrieval ID, or an explicit error."""
        ...

    async def titles(self, document_ids: tuple[str, ...]) -> Mapping[str, str]:
        """Display names (upload file names) for documents; missing IDs omitted."""
        ...


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


_Label = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=500)]


class RetrievalRecord(_Value):
    """Privacy-preserving evidence of one knowledge retrieval: IDs and counts."""

    step_id: Identifier
    retrieval_id: Identifier
    permission_request_id: Identifier
    tool_id: Literal["knowledge.retrieve"] = KNOWLEDGE_TOOL_ID
    decision_status: PermissionStatus
    outcome: Literal["retrieved", "failed", "rejected"]
    item_count: NonnegativeInt = 0
    document_items: NonnegativeInt = 0
    memory_items: NonnegativeInt = 0
    total_chars: NonnegativeInt = 0
    first_ordinal: int = Field(strict=True, ge=1, le=MAX_CITATION_ORDINAL)


class MissionCitation(_Value):
    """One source a verified knowledge step cited; provenance only, no content."""

    ordinal: int = Field(strict=True, ge=1, le=MAX_CITATION_ORDINAL)
    marker: str = Field(strict=True)
    step_id: Identifier
    retrieval_id: Identifier
    source_kind: Literal["memory", "document_chunk"]
    source_id: Identifier
    source_hash: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    authoritative: bool = Field(strict=True)
    document_id: Identifier | None = None
    title: _Label | None = None
    page: int | None = Field(default=None, strict=True, ge=1)
    section: _Label | None = None
    chunk_index: int | None = Field(default=None, strict=True, ge=0)
    memory_type: MemoryType | None = None
    memory_scope: Identifier | None = None
    label: _Label

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.marker != f"[{self.ordinal}]":
            raise ValueError("Citation marker must match its ordinal")
        if (self.source_kind == "memory") != (self.memory_type is not None):
            raise ValueError("Memory provenance contradicts the source kind")
        if (self.source_kind == "document_chunk") != (self.document_id is not None):
            raise ValueError("Document provenance contradicts the source kind")
        return self


@dataclass(frozen=True)
class KnowledgeEvidence:
    """What PHOENIX issued and what retrieval returned, for LEVI's correlation."""

    step_id: str
    request: PermissionRequest
    decision: PermissionDecision
    retrieval_id: str
    result: RetrievalContextResult
    first_ordinal: int
    titles: Mapping[str, str]

    @property
    def sources(self) -> dict[int, ContextItem]:
        return {
            self.first_ordinal + index: item
            for index, item in enumerate(self.result.items)
        }


@dataclass
class KnowledgeLedger:
    """One mission's retrievals and accepted citations (content stays in memory)."""

    records: list[RetrievalRecord] = field(default_factory=list)
    citations: list[MissionCitation] = field(default_factory=list)
    # Cited source text for the final answer's check; never traced or logged.
    cited_text: dict[int, str] = field(default_factory=dict)
    next_ordinal: int = 1

    def accept(self, evidence: KnowledgeEvidence, candidate: str) -> None:
        """Record the sources a verified knowledge step actually cited."""
        sources = evidence.sources
        cited = [ordinal for ordinal in cited_ordinals(candidate) if ordinal in sources]
        if not cited:
            return
        bundle = ProvenanceCitationService().build(evidence.result)
        labels = CitationRenderer().render(bundle)
        for ordinal in cited:
            index = ordinal - evidence.first_ordinal
            item, entry, label = (
                sources[ordinal],
                bundle.citations[index],
                labels[index],
            )
            title = (
                None
                if item.document_id is None
                else evidence.titles.get(item.document_id)
            )
            self.citations.append(
                MissionCitation(
                    ordinal=ordinal,
                    marker=f"[{ordinal}]",
                    step_id=evidence.step_id,
                    retrieval_id=entry.retrieval_id,
                    source_kind=entry.source_kind,
                    source_id=entry.source_id,
                    source_hash=entry.source_hash,
                    authoritative=entry.authoritative,
                    document_id=entry.document_id,
                    title=None if title is None else _one_line(title),
                    page=entry.page,
                    section=None if entry.section is None else _one_line(entry.section),
                    chunk_index=entry.chunk_index,
                    memory_type=entry.memory_type,
                    memory_scope=entry.memory_scope,
                    label=_one_line(label.label),
                )
            )
            self.cited_text[ordinal] = item.content


def _one_line(text: str) -> str:
    return " ".join(text.split())[:500]


def cited_ordinals(text: str) -> tuple[int, ...]:
    """Every [n] marker in first-appearance order, without duplicates."""
    return tuple(dict.fromkeys(int(found) for found in _MARKER.findall(text)))


def _normalized(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def _terms(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[^\W_]+", _normalized(_MARKER.sub(" ", text)))
        if len(word) >= 4 and not word.isdigit() and word not in _STOPWORDS
    }


def _numbers(text: str) -> set[str]:
    return {
        found.replace(",", "")
        for found in _NUMBER.findall(_normalized(_MARKER.sub(" ", text)))
    }


def _claims(text: str) -> list[tuple[str, tuple[int, ...]]]:
    """Sentences with the markers that cite them; a marker-only fragment
    belongs to the sentence before it."""
    claims: list[tuple[str, tuple[int, ...]]] = []
    for part in _SENTENCE.split(text):
        markers = cited_ordinals(part)
        body = _MARKER.sub(" ", part)
        if not re.search(r"[^\W_]", body):
            if claims and markers:
                previous, earlier = claims[-1]
                claims[-1] = (previous, tuple(dict.fromkeys((*earlier, *markers))))
            continue
        claims.append((body, markers))
    return claims


def citations_supported(
    candidate: str,
    sources: Mapping[int, str],
    *,
    allowed_numbers: set[str],
    strict_uncited: bool,
) -> bool:
    """Deterministic support: a cited source shares a term with the claim or
    holds all of its numbers; numbers come from cited sources or allowed text.

    Claims without a marker are checked only when ``strict_uncited`` (a
    knowledge step), against every source the candidate cites.
    """
    cited = [sources[ordinal] for ordinal in cited_ordinals(candidate)]
    every = set().union(*(_numbers(text) for text in cited))
    for body, markers in _claims(candidate):
        numbers = _numbers(body)
        if markers:
            texts = [sources[ordinal] for ordinal in markers]
            terms = _terms(body)
            for text in texts:
                if not (terms & _terms(text)) and not (
                    numbers and numbers <= _numbers(text)
                ):
                    return False
            pool = allowed_numbers | set().union(*(_numbers(t) for t in texts))
        elif strict_uncited:
            pool = allowed_numbers | every
        else:
            continue
        if not numbers <= pool:
            return False
    return True


class KnowledgeStepRunner:
    """One knowledge step: permission-check and retrieve, then answer."""

    def __init__(self, source: KnowledgeSource, guard: PromptInjectionGuard) -> None:
        self._source = source
        self._guard = guard
        self._permissions = PermissionEngine(ToolRegistry(knowledge_tool_definitions()))

    async def available(self) -> bool:
        """Offer the domain only when there is something to search."""
        try:
            return (await self._source.available()) is True
        except asyncio.CancelledError:
            raise
        except Exception:
            return False  # the domain is simply not offered; nothing ran

    async def perform(
        self,
        binding: ModelBinding,
        handoff: HandoffEnvelope,
        *,
        user_request: str,
        evidence: tuple[tuple[str, str], ...],
        ledger: KnowledgeLedger,
    ) -> tuple[str, KnowledgeEvidence]:
        step_id = handoff.step_id
        first = ledger.next_ordinal
        request = PermissionRequest(
            request_id=str(uuid4()),
            actor_id=KNOWLEDGE_ACTOR_ID,
            tool_id=KNOWLEDGE_TOOL_ID,
            action=KNOWLEDGE_ACTION,
            resource=KNOWLEDGE_RESOURCE,
            reason=KNOWLEDGE_TOOL_REASON,
            requested_at=datetime.now(UTC),
            task_id=handoff.task_id,
        )
        decision = self._permissions.evaluate(request)
        retrieval_id = str(uuid4())

        def record(
            outcome: Literal["retrieved", "failed", "rejected"],
            found: RetrievalContextResult | None = None,
        ) -> None:
            items = () if found is None else found.items
            ledger.records.append(
                RetrievalRecord(
                    step_id=step_id,
                    retrieval_id=retrieval_id,
                    permission_request_id=request.request_id,
                    decision_status=decision.status,
                    outcome=outcome,
                    item_count=len(items),
                    document_items=sum(
                        i.source_kind == "document_chunk" for i in items
                    ),
                    memory_items=sum(i.source_kind == "memory" for i in items),
                    total_chars=0 if found is None else found.total_chars,
                    first_ordinal=first,
                )
            )

        if decision.status != PermissionStatus.ALLOWED:
            record("rejected")
            raise ToolStepRejected("Knowledge retrieval was not allowed")
        try:
            result = RetrievalContextResult.model_validate(
                await self._source.retrieve(
                    retrieval_id=retrieval_id, query_text=handoff.objective
                )
            )
            documents = tuple(
                dict.fromkeys(
                    item.document_id
                    for item in result.items
                    if item.document_id is not None
                )
            )
            titles = dict(await self._source.titles(documents)) if documents else {}
            if first + len(result.items) - 1 > MAX_CITATION_ORDINAL:
                raise ValueError("Mission citation numbering exhausted")
        except asyncio.CancelledError:
            raise
        except Exception:
            record("failed")
            raise ToolStepFailed("Knowledge retrieval failed") from None
        ledger.next_ordinal = first + len(result.items)
        record("retrieved", result)
        knowledge = KnowledgeEvidence(
            step_id=step_id,
            request=request,
            decision=decision,
            retrieval_id=retrieval_id,
            result=result,
            first_ordinal=first,
            titles={str(key): str(value) for key, value in titles.items()},
        )
        if not result.items:
            return prompts.KNOWLEDGE_NO_ANSWER, knowledge
        model = UntrustedContentOrigin.MODEL_OUTPUT
        items: tuple[_Item, ...] = (
            (
                prompts.OBJECTIVE_ITEM,
                model,
                prompts.OBJECTIVE_ITEM,
                "output",
                handoff.objective,
            ),
            *_source_items(knowledge.sources),
            *(
                (identity, model, identity, "output", text)
                for identity, text in evidence
            ),
        )
        answer = await _generate(
            binding,
            "knowledge_answer",
            prompts.KNOWLEDGE_ANSWER_SYSTEM,
            "\n\n".join(
                (
                    f"Original request:\n{user_request}",
                    f"Expected output:\n{handoff.expected_output_schema}",
                    _guarded(
                        self._guard,
                        f"{handoff.task_id}:{step_id}:knowledge",
                        prompts.KNOWLEDGE_ANSWER_TASK,
                        items,
                    ),
                )
            ),
        )
        return answer, knowledge


def _source_items(sources: Mapping[int, ContextItem]) -> tuple[_Item, ...]:
    """Each source as one numbered guarded item; identical for LEVI."""
    return tuple(
        (
            f"[{ordinal}]",
            UntrustedContentOrigin.UPLOADED_DOCUMENT
            if item.source_kind == "document_chunk"
            else UntrustedContentOrigin.EXTERNAL_TEXT,
            item.source_id,
            "content",
            item.content,
        )
        for ordinal, item in sources.items()
    )


def _numbers_in(texts: Sequence[str]) -> set[str]:
    return set().union(*(_numbers(text) for text in texts))


class KnowledgeVerificationResponder(ModelVerificationResponder):
    """LEVI for one knowledge step: evidence, citations and support first.

    A failed deterministic check is final: the model is not consulted. With no
    retrieved sources the fixed no-answer output is verified deterministically
    and no model runs. Otherwise the knowledge_verifier sees only the sources
    the candidate cites (every source for the no-answer sentence) as the same
    numbered guarded items the answer turn saw.
    """

    def __init__(
        self,
        binding: ModelBinding,
        guard: PromptInjectionGuard,
        *,
        original_request: str,
        evidence: KnowledgeEvidence,
        prior: tuple[tuple[str, str], ...],
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
        self._prior = prior

    def _correlation(self) -> VerificationCheck:
        evidence = self._evidence
        request, decision, result = evidence.request, evidence.decision, evidence.result
        passed = (
            decision.request_id == request.request_id
            and decision.actor_id == request.actor_id == KNOWLEDGE_ACTOR_ID
            and decision.tool_id == request.tool_id == KNOWLEDGE_TOOL_ID
            and request.action == KNOWLEDGE_ACTION
            and request.resource == KNOWLEDGE_RESOURCE
            and decision.risk_level == ToolRiskLevel.P0
            and decision.status == PermissionStatus.ALLOWED
            and decision.reason_code == DecisionReason.P0_INTERNAL
            and result.retrieval_id == evidence.retrieval_id
            and evidence.first_ordinal >= 1
            and evidence.first_ordinal + len(result.items) - 1 <= MAX_CITATION_ORDINAL
        )
        return VerificationCheck(
            check_id="deterministic.knowledge_evidence",
            criterion="Retrieval evidence correlates with the PHOENIX-issued request",
            passed=passed,
            evidence_summary="Allowed internal retrieval; evidence correlates"
            if passed
            else "Retrieval evidence does not correlate or was not allowed",
        )

    def _citations(
        self, candidate: str, objective: str
    ) -> tuple[VerificationCheck, ...]:
        sources = {o: item.content for o, item in self._evidence.sources.items()}
        cited = cited_ordinals(candidate)
        no_answer = candidate.strip() == prompts.KNOWLEDGE_NO_ANSWER
        valid = (no_answer and not cited) or (
            bool(cited) and all(ordinal in sources for ordinal in cited)
        )
        checks = [
            VerificationCheck(
                check_id="deterministic.citations",
                criterion="The output cites only sources PHOENIX retrieved for it",
                passed=valid,
                evidence_summary=f"{len(cited)} cited of {len(sources)} retrieved"
                if valid
                else "Missing citations or a citation PHOENIX did not issue",
            )
        ]
        if valid and not no_answer:
            allowed = _numbers_in(
                (
                    self._original_request,
                    objective,
                    *(text for _, text in self._prior),
                )
            )
            supported = citations_supported(
                candidate,
                sources,
                allowed_numbers=allowed,
                strict_uncited=True,
            )
            checks.append(
                VerificationCheck(
                    check_id="deterministic.citation_support",
                    criterion="Each cited claim is grounded in the source it cites",
                    passed=supported,
                    evidence_summary="Cited claims share terms and numbers with"
                    " their sources"
                    if supported
                    else "A cited claim is not grounded in its cited source",
                )
            )
        return tuple(checks)

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        correlation = self._correlation()
        if not correlation.passed:
            return VerificationDraft(
                verification_id=request.verification_id, checks=(correlation,)
            )
        candidate = request.candidate_output
        if not self._evidence.result.items:
            exact = candidate == prompts.KNOWLEDGE_NO_ANSWER
            return VerificationDraft(
                verification_id=request.verification_id,
                checks=(
                    correlation,
                    VerificationCheck(
                        check_id="deterministic.no_sources",
                        criterion="With no sources the output is the fixed sentence",
                        passed=exact,
                        evidence_summary="No sources; fixed no-answer output"
                        if exact
                        else "No sources, but the output is not the fixed sentence",
                    ),
                ),
            )
        checks = (
            correlation,
            *self._deterministic(candidate),
            *self._citations(candidate, request.objective),
        )
        if not all(check.passed for check in checks):
            return VerificationDraft(
                verification_id=request.verification_id, checks=checks
            )
        sources = self._evidence.sources
        cited = cited_ordinals(candidate)
        shown = {o: sources[o] for o in cited} if cited else sources
        model = UntrustedContentOrigin.MODEL_OUTPUT
        user = "\n\n".join(
            (
                f"Original request:\n{self._original_request}",
                f"Expected output:\n{request.expected_output_schema}",
                f"Completion rule:\n{request.completion_rule}",
                _guarded(
                    self._guard,
                    request.verification_id,
                    prompts.KNOWLEDGE_CANDIDATE_TASK,
                    (
                        (
                            prompts.OBJECTIVE_ITEM,
                            model,
                            prompts.OBJECTIVE_ITEM,
                            "output",
                            request.objective,
                        ),
                        *_source_items(shown),
                        (
                            prompts.CANDIDATE_ITEM,
                            model,
                            prompts.CANDIDATE_ITEM,
                            "output",
                            candidate,
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
                "knowledge_verifier",
                prompts.KNOWLEDGE_VERIFICATION_SYSTEM.format(
                    max_checks=self._max_checks
                ),
                user,
            ),
            self._max_checks,
        )


class CitedFinalVerificationResponder:
    """LEVI for the final answer of a mission that ran a knowledge step.

    Every [n] in the final answer must be a source a verified knowledge step
    cited, and each claim citing one must be grounded in it. A failed check is
    final: the inner final verifier is not consulted. Markers are optional, so
    an exact-format answer without them is unaffected.
    """

    def __init__(self, inner: VerificationResponder, ledger: KnowledgeLedger) -> None:
        self._inner = inner
        self._ledger = ledger

    def _check(self, candidate: str) -> VerificationCheck:
        sources = dict(self._ledger.cited_text)
        cited = cited_ordinals(candidate)
        passed = all(ordinal in sources for ordinal in cited) and citations_supported(
            candidate,
            sources,
            allowed_numbers=set(),
            strict_uncited=False,
        )
        return VerificationCheck(
            check_id="deterministic.final_citations",
            criterion="Final-answer citations name verified, supporting sources",
            passed=passed,
            evidence_summary=f"{len(cited)} citations, all verified sources"
            if passed
            else "A citation is unknown or does not support its claim",
        )

    async def verify(self, request: VerificationRequest) -> VerificationDraft:
        check = self._check(request.candidate_output)
        if not check.passed:
            return VerificationDraft(
                verification_id=request.verification_id, checks=(check,)
            )
        draft = await self._inner.verify(request)
        return VerificationDraft(
            verification_id=draft.verification_id, checks=(check, *draft.checks)
        )
