"""PHX-111 web research tool: one search plus bounded page reads, P1 scoped.

ADR-137: the permission scope is the search provider, not the query. The
resource is ``search:<provider_id>``, so the user's SESSION or PERSISTENT grant
("PHOENIX may send web searches to DuckDuckGo and read the top result pages")
survives a mission re-run whose model proposes a slightly different query.
Every call still goes through ToolRegistry and PermissionEngine (INV-004);
without a matching grant nothing leaves the PC and the result is DENIED-shaped
evidence with ``attempted=False``.

Search results and page text are untrusted data (INV-017). A page that cannot be
read falls back, observably, to the search snippet. Failures are explicit
closed codes (INV-021): ``rate_limited`` is never retried here.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Final, Literal, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from phoenix.security.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionGrant,
    PermissionPolicy,
    PermissionRequest,
    PermissionStatus,
)
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResultStatus,
    ToolRiskLevel,
)
from phoenix.web.page_fetch import PageFetcher, PageFetchError
from phoenix.web.search import (
    SearchProvider,
    SearchProviderError,
    SearchProviderErrorCode,
    SearchRequest,
    SearchResponse,
)

__all__ = (
    "RESEARCH_TOOL_ID",
    "RESEARCH_ACTION",
    "ResearchCall",
    "ResearchIntent",
    "ResearchSourceKind",
    "ResearchSource",
    "WebResearchErrorCode",
    "WebResearchResult",
    "WebResearchInputError",
    "research_resource",
    "web_research_tool_definitions",
    "WebResearchTool",
)

RESEARCH_TOOL_ID: Final = "web.research"
RESEARCH_ACTION: Final = "research"
_REQUIREMENT = "P1: web search provider scope"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_Sha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class WebResearchInputError(ValueError):
    """Invalid call, configuration or permission correlation; nothing was sent."""


class _Value(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", revalidate_instances="always"
    )


class ResearchCall(_Value):
    """What PHOENIX (never the model directly) asks the tool to do."""

    call_id: str = Field(default_factory=lambda: str(uuid4()))
    query: str = Field(min_length=1, max_length=400, repr=False)
    max_results: int = Field(default=5, strict=True, ge=1, le=10)
    max_sources: int = Field(default=3, strict=True, ge=1, le=5)
    max_chars_per_source: int = Field(default=4000, strict=True, ge=200, le=20000)

    @field_validator("query")
    @classmethod
    def plain_query(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value or _CONTROL.search(value):
            raise ValueError("Query must be meaningful plain text")
        return value

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.max_sources > self.max_results:
            raise ValueError("max_sources exceeds max_results")
        return self


def research_resource(provider_id: str) -> str:
    """The provider-level permission scope (ADR-137)."""
    return f"search:{provider_id}"


class ResearchIntent(_Value):
    call_id: str
    tool_id: Literal["web.research"]
    action: Literal["research"]
    resource: str
    risk_level: ToolRiskLevel
    permission_requirement: str


class ResearchSourceKind(StrEnum):
    PAGE_TEXT = "page_text"
    SEARCH_SNIPPET = "search_snippet"


class ResearchSource(_Value):
    """One fetched source; content is untrusted text, never instructions."""

    source_id: str = Field(pattern=r"^S[1-5]$")
    url: str = Field(min_length=1, max_length=4096)
    title: str = Field(max_length=1024)
    content: str = Field(max_length=20000, repr=False)
    kind: ResearchSourceKind
    content_sha256: _Sha
    truncated: bool = Field(strict=True)

    @model_validator(mode="after")
    def digest(self) -> Self:
        if self.content_sha256 != sha256(self.content.encode("utf-8")).hexdigest():
            raise ValueError("Content digest mismatch")
        return self


class WebResearchErrorCode(StrEnum):
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid_response"
    NO_RESULTS = "no_results"


_FAILURE_STATUS = {
    WebResearchErrorCode.RATE_LIMITED: ToolResultStatus.FAILURE,
    WebResearchErrorCode.TIMEOUT: ToolResultStatus.TIMEOUT,
    WebResearchErrorCode.UNAVAILABLE: ToolResultStatus.UNAVAILABLE_DEPENDENCY,
    WebResearchErrorCode.INVALID_RESPONSE: ToolResultStatus.FAILURE,
    WebResearchErrorCode.NO_RESULTS: ToolResultStatus.FAILURE,
}


class WebResearchResult(_Value):
    """Evidence of one research call: decision, status and the fetched sources."""

    call_id: str
    tool_id: Literal["web.research"]
    resource: str
    status: ToolResultStatus
    attempted: bool = Field(strict=True)
    effect_applied: Literal[False] = False
    permission_decision: PermissionDecision
    query_sha256: _Sha
    sources: tuple[ResearchSource, ...] = Field(default=(), max_length=5)
    pages_read: int = Field(default=0, strict=True, ge=0, le=5)
    page_failures: int = Field(default=0, strict=True, ge=0, le=5)
    error_code: WebResearchErrorCode | None = None

    @model_validator(mode="after")
    def consistent(self) -> Self:
        allowed = self.permission_decision.status == PermissionStatus.ALLOWED
        if self.attempted != allowed:
            raise ValueError("Only an allowed call is attempted")
        if not allowed:
            if self.status != ToolResultStatus.DENIED or self.sources:
                raise ValueError("A refused call has no sources")
        elif self.status == ToolResultStatus.SUCCESS:
            if not self.sources or self.error_code is not None:
                raise ValueError("Success needs sources and no error")
        elif self.error_code is None or self.sources:
            raise ValueError("A failure carries a code and no sources")
        elif _FAILURE_STATUS[self.error_code] != self.status:
            raise ValueError("Failure status disagrees with its code")
        if [s.source_id for s in self.sources] != [
            f"S{i}" for i in range(1, len(self.sources) + 1)
        ] or len({s.url for s in self.sources}) != len(self.sources):
            raise ValueError("Sources must be S1..Sn with distinct URLs")
        if self.pages_read + self.page_failures > len(self.sources) and self.sources:
            raise ValueError("Page accounting exceeds the sources")
        return self


def web_research_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Pure canonical declaration; no executable handler."""
    return (
        ToolDefinition(
            tool_id=RESEARCH_TOOL_ID,
            description=(
                "Send one web search query to the configured provider and read"
                " the top result pages as untrusted text."
            ),
            input_schema=ResearchCall.model_json_schema(),
            output_schema=WebResearchResult.model_json_schema(),
            risk_level=ToolRiskLevel.P1,
            permission_requirement=_REQUIREMENT,
            timeout_seconds=90,
            cancellation_supported=True,
            audit_behavior="no mandatory durable audit",
            supported_platforms=("win32", "linux", "darwin"),
            supported_environments=("local",),
        ),
    )


def _code(error: BaseException) -> WebResearchErrorCode:
    if isinstance(error, SearchProviderError):
        return {
            SearchProviderErrorCode.RATE_LIMITED: WebResearchErrorCode.RATE_LIMITED,
            SearchProviderErrorCode.TIMEOUT: WebResearchErrorCode.TIMEOUT,
            SearchProviderErrorCode.UNAVAILABLE: WebResearchErrorCode.UNAVAILABLE,
        }.get(error.code, WebResearchErrorCode.INVALID_RESPONSE)
    if isinstance(error, TimeoutError):
        return WebResearchErrorCode.TIMEOUT
    return WebResearchErrorCode.INVALID_RESPONSE


class WebResearchTool:
    """Explicit research execution over an injected provider and page fetcher.

    The caller owns the provider's and fetcher's lifetimes. ``fetcher=None``
    reads search snippets only.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        provider: SearchProvider,
        fetcher: PageFetcher | None = None,
        policy: PermissionPolicy | None = None,
    ) -> None:
        if type(registry) is not ToolRegistry or not isinstance(
            provider, SearchProvider
        ):
            raise WebResearchInputError("Expected a ToolRegistry and SearchProvider")
        if fetcher is not None and not isinstance(fetcher, PageFetcher):
            raise WebResearchInputError("Expected a PageFetcher")
        snapshot = ToolRegistry(registry.list_tools())
        canonical = web_research_tool_definitions()[0]
        if snapshot.get(RESEARCH_TOOL_ID) != canonical:
            raise WebResearchInputError("Noncanonical web research declaration")
        self._engine = PermissionEngine(snapshot, policy)
        self._provider = provider
        self._fetcher = fetcher

    @property
    def provider_id(self) -> str:
        return self._provider.provider_id

    def describe(self, call: ResearchCall) -> ResearchIntent:
        if type(call) is not ResearchCall:
            raise WebResearchInputError("Expected a ResearchCall")
        call = ResearchCall.model_validate(call)
        return ResearchIntent(
            call_id=call.call_id,
            tool_id=RESEARCH_TOOL_ID,
            action=RESEARCH_ACTION,
            resource=research_resource(self.provider_id),
            risk_level=ToolRiskLevel.P1,
            permission_requirement=_REQUIREMENT,
        )

    async def execute(
        self,
        call: ResearchCall,
        request: PermissionRequest,
        *,
        grants: Sequence[PermissionGrant] = (),
    ) -> WebResearchResult:
        intent = self.describe(call)
        call = ResearchCall.model_validate(call)
        if type(request) is not PermissionRequest:
            raise WebResearchInputError("Expected a PermissionRequest")
        if (request.tool_id, request.action, request.resource) != (
            intent.tool_id,
            intent.action,
            intent.resource,
        ):
            raise WebResearchInputError("Permission request does not correlate")
        decision = self._engine.evaluate(request, grants=tuple(grants))
        evidence: dict[str, object] = {
            "call_id": call.call_id,
            "tool_id": RESEARCH_TOOL_ID,
            "resource": intent.resource,
            "permission_decision": decision,
            "query_sha256": sha256(call.query.encode("utf-8")).hexdigest(),
        }
        if decision.status != PermissionStatus.ALLOWED:
            # Nothing leaves the PC without the user's grant.
            return WebResearchResult.model_validate(
                evidence | {"status": ToolResultStatus.DENIED, "attempted": False}
            )
        search = SearchRequest(
            request_id=call.call_id,
            query_text=call.query,
            max_results=call.max_results,
            as_of=datetime.now(UTC),
        )
        try:
            raw = await self._provider.search(search)
            if type(raw) is not SearchResponse:
                raise TypeError("Expected a SearchResponse")
            response = SearchResponse.model_validate(raw)
            response.validate_request(search, self.provider_id)
        except Exception as error:  # cancellation (BaseException) propagates
            return self._failed(evidence, _code(error))
        if not response.results:
            return self._failed(evidence, WebResearchErrorCode.NO_RESULTS)
        sources: list[ResearchSource] = []
        pages_read = page_failures = 0
        for index, hit in enumerate(response.results[: call.max_sources], start=1):
            content, title = hit.snippet, hit.title
            kind, truncated = ResearchSourceKind.SEARCH_SNIPPET, False
            if self._fetcher is not None:
                try:
                    page = await self._fetcher.fetch(
                        hit.url, max_chars=call.max_chars_per_source
                    )
                except PageFetchError:
                    page = None
                if page is None or not page.text.strip():
                    page_failures += 1
                else:
                    pages_read += 1
                    content, truncated = page.text, page.truncated
                    kind = ResearchSourceKind.PAGE_TEXT
                    title = title or page.title
            content = content[: call.max_chars_per_source]
            sources.append(
                ResearchSource(
                    source_id=f"S{index}",
                    url=hit.url,
                    title=title,
                    content=content,
                    kind=kind,
                    content_sha256=sha256(content.encode("utf-8")).hexdigest(),
                    truncated=truncated,
                )
            )
        if not any(source.content.strip() for source in sources):
            return self._failed(evidence, WebResearchErrorCode.NO_RESULTS)
        return WebResearchResult.model_validate(
            evidence
            | {
                "status": ToolResultStatus.SUCCESS,
                "attempted": True,
                "sources": tuple(sources),
                "pages_read": pages_read,
                "page_failures": page_failures,
            }
        )

    @staticmethod
    def _failed(
        evidence: dict[str, object], code: WebResearchErrorCode
    ) -> WebResearchResult:
        return WebResearchResult.model_validate(
            evidence
            | {"status": _FAILURE_STATUS[code], "attempted": True, "error_code": code}
        )
