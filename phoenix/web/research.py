"""Bounded advisory research over injected, caller-owned collaborators.

Source and synthesis text are inert untrusted data. Structural corroboration
counts declared supporting sources; it proves neither entailment nor truth.
Callers own cooperative deadlines and future browser/model integration.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, ClassVar, Literal, Self, TypeVar, cast
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    field_validator,
    model_validator,
)

from phoenix.agents.registry import AgentDefinition, AgentRegistry
from phoenix.models.provider import CancellationResult, CancellationStatus
from phoenix.security.permissions import (
    PermissionDecision,
    PermissionGrant,
    PermissionRequest,
    PermissionStatus,
    UserPermissionDecision,
)
from phoenix.tools.browser import BrowserOperation, BrowserToolResult
from phoenix.tools.registry import ToolResultStatus, ToolRiskLevel
from phoenix.tools.web_search import WebSearchCall, WebSearchTool, WebSearchToolResult
from phoenix.web.search import SearchRequest, SearchResult

__all__ = (
    "ResearchErrorCode",
    "ResearchError",
    "ResearchPageRequest",
    "ResearchPageReader",
    "ResearchRequest",
    "research_search_call",
    "ResearchContentKind",
    "ResearchPageReadStatus",
    "ResearchSourceEvidence",
    "ResearchSynthesizer",
    "ResearchSynthesisRequest",
    "ResearchClaimDraft",
    "ResearchSynthesisDraft",
    "ResearchClaim",
    "ResearchDiagnostics",
    "ResearchReport",
    "UraharaResearchService",
)

_NO_EVIDENCE = "No selected source evidence is available. This report is advisory only."
_Model = TypeVar("_Model", bound=BaseModel)


class ResearchErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    SEARCH_DENIED = "search_denied"
    SEARCH_FAILED = "search_failed"
    INVALID_SEARCH_EVIDENCE = "invalid_search_evidence"
    PAGE_READER_FAILURE = "page_reader_failure"
    SYNTHESIS_FAILURE = "synthesis_failure"
    INVALID_SYNTHESIS_EVIDENCE = "invalid_synthesis_evidence"
    BUSY = "busy"
    CANCELLED = "cancelled"
    CLEANUP_FAILURE = "cleanup_failure"
    WRONG_EVENT_LOOP = "wrong_event_loop"


class ResearchError(Exception):
    """Only safe closed codes, without source or collaborator diagnostics."""

    def __init__(self, code: ResearchErrorCode) -> None:
        if not any(code is item for item in ResearchErrorCode):
            raise ValueError("invalid_input")
        self.code = code
        super().__init__(code.value)


def _text(value: object, bound: int, *, meaningful: bool = False) -> str:
    if type(value) is not str or "\0" in value or len(value) > bound:
        raise ValueError("invalid_input")
    value.encode("utf-8")
    if meaningful and not value.strip():
        raise ValueError("invalid_input")
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or any(
        ord(c) < 32 or 127 <= ord(c) <= 159 for c in value
    ):
        raise ValueError("invalid_input")
    return _text(value.strip(), 256, meaningful=True)


def _stored(value: BaseModel, contract: type[BaseModel]) -> dict[str, object]:
    if type(value) is not contract:
        raise ValueError("invalid_input")
    extras = value.__pydantic_extra__
    if extras is not None and (type(extras) is not dict or extras):
        raise ValueError("invalid_input")
    return dict(vars(value))


def _exact(value: object, contract: type[_Model]) -> _Model:
    if not isinstance(value, BaseModel):
        raise ValueError("invalid_input")
    return contract.model_validate(_stored(value, contract))


def _public_url(value: object) -> str:
    # Reuse the PUBLIC PHX-053 contract, never a private URL validator or DNS.
    return SearchResult.model_validate(
        dict(result_id="url-admission", rank=1, url=value, title="", snippet="")
    ).url


def _enum(value: object, vocabulary: type[StrEnum]) -> object:
    if type(value) is not str and not any(value is item for item in vocabulary):
        raise ValueError("invalid_input")
    return value


def _sequence(value: object, contract: type[_Model], bound: int) -> tuple[_Model, ...]:
    if type(value) not in (tuple, list) or len(cast(Sequence[object], value)) > bound:
        raise ValueError("invalid_input")
    return tuple(
        contract.model_validate(item) for item in cast(Sequence[object], value)
    )


def _source_id(research_id: str, result_id: str) -> str:
    data = json.dumps(
        [research_id, result_id], ensure_ascii=True, separators=(",", ":")
    )
    return "source-" + sha256(data.encode("utf-8")).hexdigest()


def _page_id(research_id: str, result_id: str) -> str:
    return "page-" + _source_id(research_id, result_id)[7:]


_Id = Annotated[str, BeforeValidator(_identifier)]
_Url = Annotated[str, BeforeValidator(_public_url)]


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )
    _failure_code: ClassVar[ResearchErrorCode] = ResearchErrorCode.INVALID_INPUT

    @model_validator(mode="wrap")
    @classmethod
    def snapshot(cls, value: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        try:
            return handler(
                _stored(value, cls) if isinstance(value, BaseModel) else value
            )
        except Exception:
            raise ResearchError(cls._failure_code) from None

    @field_validator("as_of", "published_at", mode="before", check_fields=False)
    @classmethod
    def timestamp(cls, value: object) -> object:
        if value is not None and type(value) not in (str, datetime):
            raise ValueError("invalid_input")
        return value

    @field_validator("as_of", "published_at", check_fields=False)
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else value.astimezone(UTC)

    @field_validator("query_text", mode="before", check_fields=False)
    @classmethod
    def query(cls, value: object) -> str:
        return _text(value, 4096, meaningful=True)


class ResearchPageRequest(_Contract):
    operation_id: _Id
    source_result_id: _Id
    requested_url: _Url
    max_chars: Annotated[int, Field(strict=True, ge=1, le=50000)]


class ResearchPageReader(ABC):
    """Future integration owns BrowserTool execution and its permission evidence."""

    def __init__(self, reader_id: str) -> None:
        try:
            self._reader_id = _identifier(reader_id)
        except Exception:
            raise ResearchError(ResearchErrorCode.INVALID_INPUT) from None

    @property
    def reader_id(self) -> str:
        return self._reader_id

    @property
    @abstractmethod
    def active_operation_ids(self) -> tuple[str, ...]:
        """Sorted immutable operation IDs, without task objects or authority."""

    @abstractmethod
    async def read(self, request: ResearchPageRequest) -> BrowserToolResult:
        """Return exact correlated BrowserTool READ_PAGE evidence."""

    @abstractmethod
    async def cancel(self, operation_id: str) -> CancellationResult:
        """Request cancellation of only this page-read operation."""

    @abstractmethod
    async def aclose(self) -> None:
        """Release owned resources explicitly; consumers do not close this reader."""


class ResearchRequest(_Contract):
    research_id: _Id = Field(default_factory=lambda: str(uuid4()))
    query_text: str = Field(repr=False)
    as_of: AwareDatetime
    search_provider_id: _Id
    max_search_results: Annotated[int, Field(strict=True, ge=1, le=20)]
    max_sources: Annotated[int, Field(strict=True, ge=1, le=8)]
    max_age_days: Annotated[int, Field(strict=True, ge=1, le=3650)] | None = None
    max_chars_per_source: Annotated[int, Field(strict=True, ge=1, le=50000)]
    max_total_chars: Annotated[int, Field(strict=True, ge=1, le=200000)]

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if (
            self.max_sources > self.max_search_results
            or self.max_total_chars < self.max_chars_per_source
        ):
            raise ResearchError(self._failure_code)
        return self


def research_search_call(request: ResearchRequest) -> WebSearchCall:
    """Pure exact query/time/provider derivation; creates no permission evidence."""
    try:
        request = _exact(request, ResearchRequest)
        return WebSearchCall(
            call_id=request.research_id,
            provider_id=request.search_provider_id,
            query_text=request.query_text,
            max_results=request.max_search_results,
            as_of=request.as_of,
        )
    except Exception:
        raise ResearchError(ResearchErrorCode.INVALID_INPUT) from None


class ResearchContentKind(StrEnum):
    PAGE_TEXT = "page_text"
    SEARCH_SNIPPET = "search_snippet"


class ResearchPageReadStatus(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    INVALID_EVIDENCE = "invalid_evidence"
    NOT_ATTEMPTED = "not_attempted"


class ResearchSourceEvidence(_Contract):
    source_id: _Id
    search_result_id: _Id
    provider_rank: Annotated[int, Field(strict=True, ge=1, le=20)]
    requested_url: _Url
    final_url: _Url
    title: str = Field(repr=False)
    source_name: str | None = Field(default=None, strict=True)
    published_at: AwareDatetime | None = None
    date_text: str | None = Field(default=None, strict=True)
    source_ref: str | None = Field(default=None, strict=True)
    content_kind: ResearchContentKind
    content: str = Field(repr=False)
    content_sha256: str = Field(strict=True)
    content_truncated: bool = Field(strict=True)
    page_read_status: ResearchPageReadStatus
    page_reader_id: _Id | None = None

    @field_validator("content", mode="before")
    @classmethod
    def content_text(cls, value: object) -> str:
        return _text(value, 50000)

    @field_validator("title", mode="before")
    @classmethod
    def title_text(cls, value: object) -> str:
        return _text(value, 1024)

    @field_validator("content_kind", mode="before")
    @classmethod
    def kind(cls, value: object) -> object:
        return _enum(value, ResearchContentKind)

    @field_validator("page_read_status", mode="before")
    @classmethod
    def page_status(cls, value: object) -> object:
        return _enum(value, ResearchPageReadStatus)

    @model_validator(mode="after")
    def integrity(self) -> Self:
        try:
            # Metadata retains EXACT PHX-053 field semantics and spelling.
            SearchResult(
                result_id=self.search_result_id,
                rank=self.provider_rank,
                url=self.requested_url,
                title=self.title,
                snippet="",
                source_name=self.source_name,
                published_at=self.published_at,
                date_text=self.date_text,
                source_ref=self.source_ref,
            )
            if (
                type(self.content_sha256) is not str
                or self.content_sha256
                != sha256(self.content.encode("utf-8")).hexdigest()
            ):
                raise ValueError("invalid_input")
            if (self.page_reader_id is None) != (
                self.page_read_status == ResearchPageReadStatus.NOT_ATTEMPTED
            ):
                raise ValueError("invalid_input")
            if (self.content_kind == ResearchContentKind.PAGE_TEXT) != (
                self.page_read_status == ResearchPageReadStatus.SUCCESS
            ):
                raise ValueError("invalid_input")
            if (
                self.content_kind == ResearchContentKind.SEARCH_SNIPPET
                and self.final_url != self.requested_url
            ):
                raise ValueError("invalid_input")
        except Exception:
            raise ResearchError(self._failure_code) from None
        return self


class ResearchSynthesisRequest(_Contract):
    research_id: _Id
    query_text: str = Field(repr=False)
    as_of: AwareDatetime
    sources: tuple[ResearchSourceEvidence, ...]

    @field_validator("sources", mode="before")
    @classmethod
    def sources_snapshot(cls, value: object) -> tuple[ResearchSourceEvidence, ...]:
        return _sequence(value, ResearchSourceEvidence, 8)

    @model_validator(mode="after")
    def bounded_sources(self) -> Self:
        if (
            sum(len(source.content) for source in self.sources) > 200000
            or len({s.source_id for s in self.sources}) != len(self.sources)
            or any(
                s.source_id != _source_id(self.research_id, s.search_result_id)
                for s in self.sources
            )
        ):
            raise ResearchError(self._failure_code)
        return self


class ResearchClaimDraft(_Contract):
    _failure_code = ResearchErrorCode.INVALID_SYNTHESIS_EVIDENCE
    claim_id: _Id
    text: str = Field(repr=False)
    supporting_source_ids: tuple[_Id, ...]
    uncertainty_note: str = Field(repr=False)

    @field_validator("text", mode="before")
    @classmethod
    def claim_text(cls, value: object) -> str:
        return _text(value, 8192, meaningful=True)

    @field_validator("uncertainty_note", mode="before")
    @classmethod
    def uncertainty(cls, value: object) -> str:
        return _text(value, 2048)

    @field_validator("supporting_source_ids", mode="before")
    @classmethod
    def support(cls, value: object) -> tuple[str, ...]:
        if type(value) not in (tuple, list):
            raise ValueError("invalid_input")
        ids = tuple(_identifier(item) for item in cast(Sequence[object], value))
        if not 1 <= len(ids) <= 8 or len(set(ids)) != len(ids):
            raise ValueError("invalid_input")
        return ids


class ResearchSynthesisDraft(_Contract):
    _failure_code = ResearchErrorCode.INVALID_SYNTHESIS_EVIDENCE
    research_id: _Id
    synthesizer_id: _Id
    summary: str = Field(repr=False)
    claims: tuple[ResearchClaimDraft, ...]

    @field_validator("summary", mode="before")
    @classmethod
    def summary_text(cls, value: object) -> str:
        return _text(value, 12000)

    @field_validator("claims", mode="before")
    @classmethod
    def claim_snapshots(cls, value: object) -> tuple[ResearchClaimDraft, ...]:
        return _sequence(value, ResearchClaimDraft, 50)

    @model_validator(mode="after")
    def unique_claims(self) -> Self:
        if len({claim.claim_id for claim in self.claims}) != len(self.claims):
            raise ResearchError(self._failure_code)
        return self


class ResearchSynthesizer(ABC):
    """Structured interpretation only; no model coupling or authority is supplied."""

    def __init__(self, synthesizer_id: str) -> None:
        try:
            self._synthesizer_id = _identifier(synthesizer_id)
        except Exception:
            raise ResearchError(ResearchErrorCode.INVALID_INPUT) from None

    @property
    def synthesizer_id(self) -> str:
        return self._synthesizer_id

    @property
    @abstractmethod
    def active_research_ids(self) -> tuple[str, ...]:
        """Sorted immutable IDs; future adapters own cooperative inference limits."""

    @abstractmethod
    async def synthesize(
        self, request: ResearchSynthesisRequest
    ) -> ResearchSynthesisDraft:
        """Interpret explicit source DATA, never return actions or permissions."""

    @abstractmethod
    async def cancel(self, research_id: str) -> CancellationResult:
        """Cancel this request only; acceptance does not prove remote cessation."""

    @abstractmethod
    async def aclose(self) -> None:
        """Explicit caller-owned cleanup."""


class ResearchClaim(ResearchClaimDraft):
    """Structural support count only; corroborated is never a truth assertion."""

    supporting_source_count: Annotated[int, Field(strict=True, ge=1, le=8)]
    corroborated: bool = Field(strict=True)

    @model_validator(mode="after")
    def support_accounting(self) -> Self:
        if self.supporting_source_count != len(
            self.supporting_source_ids
        ) or self.corroborated != (self.supporting_source_count >= 2):
            raise ResearchError(self._failure_code)
        return self


class ResearchDiagnostics(_Contract):
    search_results_received: Annotated[int, Field(strict=True, ge=0, le=20)]
    age_exclusions: Annotated[int, Field(strict=True, ge=0, le=20)]
    date_anomalies: Annotated[int, Field(strict=True, ge=0, le=20)]
    sources_selected: Annotated[int, Field(strict=True, ge=0, le=8)]
    page_reads_attempted: Annotated[int, Field(strict=True, ge=0, le=8)]
    page_reads_succeeded: Annotated[int, Field(strict=True, ge=0, le=8)]
    snippet_fallbacks: Annotated[int, Field(strict=True, ge=0, le=8)]
    page_evidence_rejections: Annotated[int, Field(strict=True, ge=0, le=8)]
    source_content_truncations: Annotated[int, Field(strict=True, ge=0, le=8)]
    total_source_chars: Annotated[int, Field(strict=True, ge=0, le=200000)]
    claims_received: Annotated[int, Field(strict=True, ge=0, le=50)]
    claims_corroborated: Annotated[int, Field(strict=True, ge=0, le=50)]

    @model_validator(mode="after")
    def accounting(self) -> Self:
        if (
            self.age_exclusions + self.date_anomalies > self.search_results_received
            or self.sources_selected
            > self.search_results_received - self.age_exclusions
            or self.page_reads_attempted > self.sources_selected
            or self.page_reads_succeeded > self.page_reads_attempted
            or self.page_reads_succeeded + self.snippet_fallbacks
            != self.sources_selected
            or self.page_evidence_rejections > self.snippet_fallbacks
            or self.source_content_truncations > self.sources_selected
            or self.claims_corroborated > self.claims_received
        ):
            raise ResearchError(self._failure_code)
        return self


class ResearchReport(_Contract):
    research_id: _Id
    owner_id: Literal["web_research"]
    as_of: AwareDatetime
    search_provider_id: _Id
    synthesizer_id: _Id | None
    summary: str = Field(repr=False)
    claims: tuple[ResearchClaim, ...]
    sources: tuple[ResearchSourceEvidence, ...]
    diagnostics: ResearchDiagnostics
    advisory_only: Literal[True]

    @field_validator("owner_id", "advisory_only", mode="before")
    @classmethod
    def exact_literals(cls, value: object) -> object:
        if type(value) not in (str, bool):
            raise ValueError("invalid_input")
        return value

    @field_validator("summary", mode="before")
    @classmethod
    def summary_text(cls, value: object) -> str:
        return _text(value, 12000)

    @field_validator("claims", mode="before")
    @classmethod
    def claim_snapshots(cls, value: object) -> tuple[ResearchClaim, ...]:
        return _sequence(value, ResearchClaim, 50)

    @field_validator("sources", mode="before")
    @classmethod
    def source_snapshots(cls, value: object) -> tuple[ResearchSourceEvidence, ...]:
        return _sequence(value, ResearchSourceEvidence, 8)

    @field_validator("diagnostics", mode="before")
    @classmethod
    def diagnostic_snapshot(cls, value: object) -> ResearchDiagnostics:
        return ResearchDiagnostics.model_validate(value)

    @model_validator(mode="after")
    def correlated(self) -> Self:
        sources = self.sources
        known = {s.source_id for s in sources}
        d = self.diagnostics
        if (
            type(self.advisory_only) is not bool
            or len(known) != len(sources)
            or len({s.search_result_id for s in sources}) != len(sources)
            or len({s.requested_url for s in sources}) != len(sources)
            or tuple(s.provider_rank for s in sources)
            != tuple(sorted({s.provider_rank for s in sources}))
            or any(
                s.source_id != _source_id(self.research_id, s.search_result_id)
                for s in sources
            )
            or len({c.claim_id for c in self.claims}) != len(self.claims)
            or any(not set(c.supporting_source_ids) <= known for c in self.claims)
            or (
                not sources
                and (
                    self.synthesizer_id is not None
                    or self.claims
                    or self.summary != _NO_EVIDENCE
                )
            )
            or (sources and self.synthesizer_id is None)
        ):
            raise ResearchError(self._failure_code)
        counts = (
            len(sources),
            sum(s.page_reader_id is not None for s in sources),
            sum(s.content_kind == ResearchContentKind.PAGE_TEXT for s in sources),
            sum(s.content_kind == ResearchContentKind.SEARCH_SNIPPET for s in sources),
            sum(
                s.page_read_status == ResearchPageReadStatus.INVALID_EVIDENCE
                for s in sources
            ),
            sum(s.content_truncated for s in sources),
            sum(len(s.content) for s in sources),
            len(self.claims),
            sum(c.corroborated for c in self.claims),
        )
        if counts != (
            d.sources_selected,
            d.page_reads_attempted,
            d.page_reads_succeeded,
            d.snippet_fallbacks,
            d.page_evidence_rejections,
            d.source_content_truncations,
            d.total_source_chars,
            d.claims_received,
            d.claims_corroborated,
        ):
            raise ResearchError(self._failure_code)
        return self


def _page_evidence(
    raw: object, request: ResearchPageRequest
) -> tuple[BrowserToolResult | None, ResearchPageReadStatus]:
    try:
        if type(raw) is not BrowserToolResult:
            raise ValueError("invalid_input")
        _exact(raw.permission_decision, PermissionDecision)
        result = _exact(raw, BrowserToolResult)
        _exact(result.permission_decision, PermissionDecision)
        resource = json.loads(result.resource)
        if (
            result.call_id != request.operation_id
            or result.operation != BrowserOperation.READ_PAGE
            or result.tool_id != "browser.read_page"
            or result.permission_decision.risk_level != ToolRiskLevel.P1
            or resource[2] != request.requested_url
            or resource[3] != request.max_chars
            or result.effect_applied
        ):
            raise ValueError("invalid_input")
        if result.status == ToolResultStatus.DENIED:
            return None, ResearchPageReadStatus.DENIED
        if result.status == ToolResultStatus.UNAVAILABLE_DEPENDENCY:
            return None, ResearchPageReadStatus.UNAVAILABLE
        if result.status in (ToolResultStatus.FAILURE, ToolResultStatus.TIMEOUT):
            return None, ResearchPageReadStatus.FAILED
        if (
            result.status
            not in (ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL_SUCCESS)
            or not result.attempted
            or result.permission_decision.status != PermissionStatus.ALLOWED
            or result.text is None
            or result.final_url is None
            or result.error_code is not None
            or _public_url(result.final_url) != result.final_url
        ):
            raise ValueError("invalid_input")
        _text(result.text, request.max_chars)
        return result, ResearchPageReadStatus.SUCCESS
    except Exception:
        return None, ResearchPageReadStatus.INVALID_EVIDENCE


@dataclass(slots=True)
class _Active:
    research_id: str
    task: asyncio.Task[object]
    stage: str = "search"
    operation_id: str = ""
    cancelled: bool = False


class UraharaResearchService:
    """One active caller operation, no queue, hidden worker or final authority.

    Search denial/failure raises a safe error. Exact individual denied/unavailable/
    failed/invalid page evidence falls back observably to the search snippet.
    An unexpected reader exception is fatal. Cancellation always propagates.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        search_tool: WebSearchTool,
        page_reader: ResearchPageReader,
        synthesizer: ResearchSynthesizer,
    ) -> None:
        try:
            if (
                type(registry) is not AgentRegistry
                or type(search_tool) is not WebSearchTool
                or not isinstance(page_reader, ResearchPageReader)
                or not isinstance(synthesizer, ResearchSynthesizer)
            ):
                raise ValueError("invalid_input")
            data = _stored(registry, AgentRegistry)
            agents = tuple(
                _exact(agent, AgentDefinition)
                for agent in cast(Sequence[object], data["agents"])
            )
            AgentRegistry(agents=agents).require("web_research")
            reader_id = _identifier(page_reader.reader_id)
            synthesizer_id = _identifier(synthesizer.synthesizer_id)
            if (
                reader_id != page_reader.reader_id
                or synthesizer_id != synthesizer.synthesizer_id
            ):
                raise ValueError("invalid_input")
        except Exception:
            raise ResearchError(ResearchErrorCode.INVALID_INPUT) from None
        self._search_tool = search_tool
        self._reader = page_reader
        self._reader_id = reader_id
        self._synthesizer = synthesizer
        self._synthesizer_id = synthesizer_id
        self._active: _Active | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def owner_id(self) -> Literal["web_research"]:
        return "web_research"

    @property
    def active_research_ids(self) -> tuple[str, ...]:
        return () if self._active is None else (self._active.research_id,)

    def _event_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise ResearchError(ResearchErrorCode.WRONG_EVENT_LOOP)

    @staticmethod
    def _check_cancel(active: _Active) -> None:
        if active.cancelled or active.task.cancelling():
            raise asyncio.CancelledError

    async def research(
        self,
        request: ResearchRequest,
        *,
        search_permission_request: PermissionRequest,
        search_grants: Sequence[PermissionGrant] = (),
        search_user_decision: UserPermissionDecision | None = None,
    ) -> ResearchReport:
        try:
            request = _exact(request, ResearchRequest)
            call = research_search_call(request)
            intent = self._search_tool.describe(call)
        except Exception:
            raise ResearchError(ResearchErrorCode.INVALID_INPUT) from None
        self._event_loop()
        if self._active is not None:
            raise ResearchError(ResearchErrorCode.BUSY)
        task = asyncio.current_task()
        assert task is not None
        active = _Active(request.research_id, task, operation_id=call.call_id)
        self._active = active
        try:
            try:
                raw_search = await self._search_tool.execute(
                    call,
                    search_permission_request,
                    grants=search_grants,
                    user_decision=search_user_decision,
                )
            except Exception:
                raise ResearchError(ResearchErrorCode.SEARCH_FAILED) from None
            self._check_cancel(active)
            try:
                result = _exact(raw_search, WebSearchToolResult)
                if (
                    result.call_id,
                    result.provider_id,
                    result.tool_id,
                    result.resource,
                ) != (call.call_id, call.provider_id, intent.tool_id, intent.resource):
                    raise ValueError("invalid_input")
            except Exception:
                raise ResearchError(ResearchErrorCode.INVALID_SEARCH_EVIDENCE) from None
            if result.status == ToolResultStatus.DENIED:
                raise ResearchError(ResearchErrorCode.SEARCH_DENIED)
            if result.status != ToolResultStatus.SUCCESS or result.response is None:
                raise ResearchError(ResearchErrorCode.SEARCH_FAILED)
            response = result.response
            try:
                response.validate_request(
                    SearchRequest(
                        request_id=call.call_id,
                        query_text=call.query_text,
                        max_results=call.max_results,
                        as_of=call.as_of,
                    ),
                    call.provider_id,
                )
            except Exception:
                raise ResearchError(ResearchErrorCode.INVALID_SEARCH_EVIDENCE) from None
            selected: list[SearchResult] = []
            exclusions = anomalies = 0
            # Age comparison uses timedelta difference, avoiding datetime underflow.
            for candidate in response.results:
                published = candidate.published_at
                if published is not None and published > request.as_of:
                    anomalies += 1
                elif (
                    request.max_age_days is not None
                    and published is not None
                    and request.as_of - published > timedelta(days=request.max_age_days)
                ):
                    exclusions += 1
                    continue
                if len(selected) < request.max_sources:
                    selected.append(candidate)
            sources: list[ResearchSourceEvidence] = []
            remaining = request.max_total_chars
            for candidate in selected:
                self._check_cancel(active)
                page_request = ResearchPageRequest(
                    operation_id=_page_id(request.research_id, candidate.result_id),
                    source_result_id=candidate.result_id,
                    requested_url=candidate.url,
                    max_chars=request.max_chars_per_source,
                )
                active.stage, active.operation_id = "page", page_request.operation_id
                if self._reader.reader_id != self._reader_id:
                    raise ResearchError(ResearchErrorCode.PAGE_READER_FAILURE)
                try:
                    raw_page = await self._reader.read(page_request)
                except Exception:
                    raise ResearchError(ResearchErrorCode.PAGE_READER_FAILURE) from None
                self._check_cancel(active)
                page, page_status = _page_evidence(raw_page, page_request)
                text = cast(str, page.text) if page is not None else candidate.snippet
                content = text[: min(request.max_chars_per_source, remaining)]
                remaining -= len(content)
                sources.append(
                    ResearchSourceEvidence(
                        source_id=_source_id(request.research_id, candidate.result_id),
                        search_result_id=candidate.result_id,
                        provider_rank=candidate.rank,
                        requested_url=candidate.url,
                        final_url=cast(str, page.final_url)
                        if page is not None
                        else candidate.url,
                        title=candidate.title,
                        source_name=candidate.source_name,
                        published_at=candidate.published_at,
                        date_text=candidate.date_text,
                        source_ref=candidate.source_ref,
                        content_kind=ResearchContentKind.PAGE_TEXT
                        if page is not None
                        else ResearchContentKind.SEARCH_SNIPPET,
                        content=content,
                        content_sha256=sha256(content.encode("utf-8")).hexdigest(),
                        content_truncated=len(content) < len(text)
                        or (page is not None and page.truncated),
                        page_read_status=page_status,
                        page_reader_id=self._reader_id,
                    )
                )
            claims: tuple[ResearchClaim, ...] = ()
            summary = _NO_EVIDENCE
            synthesizer_id = None
            if sources:
                self._check_cancel(active)
                active.stage, active.operation_id = "synthesis", request.research_id
                if self._synthesizer.synthesizer_id != self._synthesizer_id:
                    raise ResearchError(ResearchErrorCode.SYNTHESIS_FAILURE)
                synthesis_request = ResearchSynthesisRequest(
                    research_id=request.research_id,
                    query_text=request.query_text,
                    as_of=request.as_of,
                    sources=tuple(sources),
                )
                try:
                    raw_draft = await self._synthesizer.synthesize(synthesis_request)
                except Exception:
                    raise ResearchError(ResearchErrorCode.SYNTHESIS_FAILURE) from None
                self._check_cancel(active)
                try:
                    draft = _exact(raw_draft, ResearchSynthesisDraft)
                    known = {source.source_id for source in sources}
                    if (draft.research_id, draft.synthesizer_id) != (
                        request.research_id,
                        self._synthesizer_id,
                    ) or any(
                        not set(claim.supporting_source_ids) <= known
                        for claim in draft.claims
                    ):
                        raise ValueError("invalid_input")
                    claims = tuple(
                        ResearchClaim(
                            **claim.model_dump(),
                            supporting_source_count=len(claim.supporting_source_ids),
                            corroborated=len(claim.supporting_source_ids) >= 2,
                        )
                        for claim in draft.claims
                    )
                except Exception:
                    raise ResearchError(
                        ResearchErrorCode.INVALID_SYNTHESIS_EVIDENCE
                    ) from None
                summary, synthesizer_id = draft.summary, self._synthesizer_id
            self._check_cancel(active)
            diagnostics = ResearchDiagnostics(
                search_results_received=len(response.results),
                age_exclusions=exclusions,
                date_anomalies=anomalies,
                sources_selected=len(sources),
                page_reads_attempted=len(sources),
                page_reads_succeeded=sum(
                    s.content_kind == ResearchContentKind.PAGE_TEXT for s in sources
                ),
                snippet_fallbacks=sum(
                    s.content_kind == ResearchContentKind.SEARCH_SNIPPET
                    for s in sources
                ),
                page_evidence_rejections=sum(
                    s.page_read_status == ResearchPageReadStatus.INVALID_EVIDENCE
                    for s in sources
                ),
                source_content_truncations=sum(s.content_truncated for s in sources),
                total_source_chars=sum(len(s.content) for s in sources),
                claims_received=len(claims),
                claims_corroborated=sum(c.corroborated for c in claims),
            )
            return ResearchReport(
                research_id=request.research_id,
                owner_id=self.owner_id,
                as_of=request.as_of,
                search_provider_id=request.search_provider_id,
                synthesizer_id=synthesizer_id,
                summary=summary,
                claims=claims,
                sources=tuple(sources),
                diagnostics=diagnostics,
                advisory_only=True,
            )
        finally:
            self._active = None

    async def cancel(self, research_id: str) -> CancellationResult:
        try:
            identity = _identifier(research_id)
        except Exception:
            raise ResearchError(ResearchErrorCode.INVALID_INPUT) from None
        self._event_loop()
        active = self._active
        if active is None or active.research_id != identity:
            return CancellationResult(
                operation_id=identity,
                provider_id=self.owner_id,
                status=CancellationStatus.NOT_FOUND,
            )
        active.cancelled = True
        try:
            if active.stage == "search":
                result = await self._search_tool.cancel(active.operation_id)
                provider_id = self._search_tool.provider_id
            elif active.stage == "page":
                result = await self._reader.cancel(active.operation_id)
                provider_id = self._reader_id
            else:
                result = await self._synthesizer.cancel(active.operation_id)
                provider_id = self._synthesizer_id
            data = _stored(result, CancellationResult)
            data["operation_id"] = _identifier(data.get("operation_id"))
            data["provider_id"] = _identifier(data.get("provider_id"))
            _enum(data.get("status"), CancellationStatus)
            checked = CancellationResult.model_validate(data)
            if (checked.operation_id, checked.provider_id) != (
                active.operation_id,
                provider_id,
            ):
                raise ValueError("invalid_input")
        except Exception:
            raise ResearchError(ResearchErrorCode.CLEANUP_FAILURE) from None
        finally:
            if (
                self._active is active
                and active.task is not asyncio.current_task()
                and not active.task.done()
            ):
                active.task.cancel()
        return CancellationResult(
            operation_id=identity,
            provider_id=self.owner_id,
            status=CancellationStatus.ACCEPTED,
        )
