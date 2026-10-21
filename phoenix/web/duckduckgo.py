"""PHX-111 concrete SearchProvider: DuckDuckGo's HTML endpoint, no API key.

ADR-135: plain httpx with an honest User-Agent; no browser fingerprint
impersonation, no CAPTCHA handling. A block or anomaly page is reported as
RATE_LIMITED, never retried here. Results remain untrusted, inert data: the
PHX-053 contracts admit only public http(s) URLs. Construction performs no I/O;
the caller owns ``aclose``. Queries leave the PC only when a caller runs
``search`` (in PHOENIX, only after PermissionEngine allowed the research call).
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import httpx

from phoenix.models.provider import CancellationResult, CancellationStatus
from phoenix.web.search import (
    SearchProvider,
    SearchProviderError,
    SearchProviderErrorCode,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

__all__ = (
    "DUCKDUCKGO_PROVIDER_ID",
    "DUCKDUCKGO_ENDPOINT",
    "DuckDuckGoSearchProvider",
    "parse_results",
)

DUCKDUCKGO_PROVIDER_ID = "duckduckgo"
DUCKDUCKGO_ENDPOINT = "https://html.duckduckgo.com/html/"
USER_AGENT = "PHOENIX/0.1 (local desktop assistant; +https://github.com/s7oaib)"
# The HTML endpoint answers a throttled client with 202 or an anomaly page.
_RATE_LIMIT_STATUSES = frozenset({202, 403, 418, 429})
_MAX_BODY_CHARS = 2_000_000


class _ResultParser(HTMLParser):
    """Collects (href, title, snippet) from result__a / result__snippet anchors."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hits: list[list[str]] = []
        self._field: int | None = None  # 1 = title, 2 = snippet
        self._tag: str | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._field is not None:
            if tag == self._tag:
                self._depth += 1
            return
        classes = (dict(attrs).get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self.hits.append([dict(attrs).get("href") or "", "", ""])
            self._field, self._tag, self._depth = 1, tag, 0
        elif "result__snippet" in classes and self.hits:
            self._field, self._tag, self._depth = 2, tag, 0

    def handle_endtag(self, tag: str) -> None:
        if self._field is not None and tag == self._tag:
            if self._depth == 0:
                self._field = self._tag = None
            else:
                self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._field is not None and self.hits:
            self.hits[-1][self._field] += data


def _target(href: str) -> str:
    """The result URL; DuckDuckGo may wrap it in a /l/?uddg= redirect."""
    href = href.strip()
    if href.startswith("//"):
        href = "https:" + href
    parts = urlsplit(href)
    if parts.hostname in ("duckduckgo.com", "html.duckduckgo.com") and (
        parts.path.startswith("/l/")
    ):
        wrapped = parse_qs(parts.query).get("uddg")
        return wrapped[0] if wrapped else ""
    return href


def _clean(text: str) -> str:
    return " ".join(
        "".join(c for c in text if ord(c) >= 32 and not 127 <= ord(c) <= 159).split()
    )


def parse_results(html: str) -> tuple[tuple[str, str, str], ...]:
    """(url, title, snippet) in page order; ads and inadmissible URLs dropped."""
    parser = _ResultParser()
    parser.feed(html[:_MAX_BODY_CHARS])
    parser.close()
    found: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for href, title, snippet in parser.hits:
        url = _target(href)
        if not url or "duckduckgo.com/y.js" in url:
            continue  # sponsored result
        try:
            url = SearchResult(
                result_id="admission", rank=1, url=url, title="", snippet=""
            ).url
        except SearchProviderError:
            continue  # not a public http(s) URL
        if url in seen:
            continue
        seen.add(url)
        found.append((url, _clean(title)[:1024], _clean(snippet)[:8192]))
    return tuple(found)


def _blocked(html: str) -> bool:
    lowered = html.lower()
    return "anomaly" in lowered and "result__a" not in lowered


class DuckDuckGoSearchProvider(SearchProvider):
    """One request at a time per request ID; bounded by an explicit timeout."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        super().__init__(DUCKDUCKGO_PROVIDER_ID)
        if not 0 < timeout_seconds <= 60:
            raise SearchProviderError(SearchProviderErrorCode.INVALID_INPUT)
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=timeout_seconds,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
            trust_env=False,
        )
        self._active: dict[str, asyncio.Task[object]] = {}
        self._closed = False

    @property
    def active_request_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active))

    async def search(self, request: SearchRequest) -> SearchResponse:
        if type(request) is not SearchRequest:
            raise SearchProviderError(SearchProviderErrorCode.INVALID_INPUT)
        request = SearchRequest.model_validate(request)
        if self._closed:
            raise SearchProviderError(SearchProviderErrorCode.CLOSED)
        if request.request_id in self._active:
            raise SearchProviderError(SearchProviderErrorCode.BUSY)
        task = asyncio.current_task()
        assert task is not None
        self._active[request.request_id] = task
        try:
            response = await self._client.post(
                DUCKDUCKGO_ENDPOINT, data={"q": request.query_text, "kl": "wt-wt"}
            )
        except httpx.TimeoutException:
            raise SearchProviderError(SearchProviderErrorCode.TIMEOUT) from None
        except httpx.HTTPError:
            raise SearchProviderError(SearchProviderErrorCode.UNAVAILABLE) from None
        finally:
            self._active.pop(request.request_id, None)
        if response.status_code in _RATE_LIMIT_STATUSES:
            raise SearchProviderError(SearchProviderErrorCode.RATE_LIMITED)
        if response.status_code != 200:
            raise SearchProviderError(SearchProviderErrorCode.UNAVAILABLE)
        body = response.text
        if _blocked(body):
            raise SearchProviderError(SearchProviderErrorCode.RATE_LIMITED)
        hits = parse_results(body)[: request.max_results]
        return SearchResponse(
            request_id=request.request_id,
            provider_id=self.provider_id,
            as_of=request.as_of,
            results=tuple(
                SearchResult(
                    result_id=f"r{rank}",
                    rank=rank,
                    url=url,
                    title=title,
                    snippet=snippet,
                    source_name=urlsplit(url).hostname,
                )
                for rank, (url, title, snippet) in enumerate(hits, start=1)
            ),
        )

    async def cancel(self, request_id: str) -> CancellationResult:
        task = self._active.get(request_id)
        if task is None or task.done():
            status = CancellationStatus.NOT_FOUND
        else:
            task.cancel()
            status = CancellationStatus.ACCEPTED
        return CancellationResult(
            operation_id=request_id, provider_id=self.provider_id, status=status
        )

    async def aclose(self) -> None:
        self._closed = True
        await self._client.aclose()
