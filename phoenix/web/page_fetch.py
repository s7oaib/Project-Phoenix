"""PHX-111 bounded page reading: public http(s) pages to untrusted plain text.

ADR-136: httpx plus the standard-library HTML parser; no new dependency, no
JavaScript, cookies, credentials or downloads. Every hop (the first URL and at
most ``max_redirects`` redirects) must pass the PHX-053 public-URL admission and
resolve only to globally routable addresses. Bodies are capped in bytes before
decoding. The text is untrusted data, never instructions (INV-017).

PHX-123 (ADR-181): the connection goes to the address PHOENIX vetted, never a
second lookup (no DNS rebinding between check and connect); the URL's host
stays the Host header and the TLS server name, so certificates are still
checked against the hostname. Each redirect hop is resolved and vetted again,
may not downgrade https to http, and only ports 80 and 443 are fetched. One
deadline bounds the whole fetch, redirects and body included.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from phoenix.web.search import SearchProviderError, SearchResult, public_ip

__all__ = (
    "FetchedPage",
    "PageFetchErrorCode",
    "PageFetchError",
    "PageFetcher",
    "HttpPageFetcher",
    "html_to_text",
)

_SKIP = frozenset(
    {"script", "style", "noscript", "template", "svg", "head", "iframe", "object"}
)
_BLOCK = frozenset(
    {
        "p",
        "div",
        "br",
        "li",
        "ul",
        "ol",
        "tr",
        "td",
        "th",
        "table",
        "section",
        "article",
        "header",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "main",
        "nav",
        "aside",
        "dd",
        "dt",
        "figcaption",
    }
)
_TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_SPACE = re.compile(r"\s+")
_USER_AGENT = "PHOENIX/0.1 (local desktop assistant)"
_PORTS = (None, 80, 443)


class PageFetchErrorCode(StrEnum):
    BLOCKED_URL = "blocked_url"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    HTTP_STATUS = "http_status"
    UNSUPPORTED_CONTENT = "unsupported_content"
    TOO_MANY_REDIRECTS = "too_many_redirects"


class PageFetchError(Exception):
    """Closed code only; no URL, body or transport diagnostics."""

    def __init__(self, code: PageFetchErrorCode) -> None:
        self.code = PageFetchErrorCode(code)
        super().__init__(self.code.value)


@dataclass(frozen=True)
class FetchedPage:
    final_url: str
    title: str
    text: str
    truncated: bool


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            # HTML collapses source whitespace; only block tags break lines.
            self.parts.append(_SPACE.sub(" ", data))


def _printable(text: str) -> str:
    return "".join(
        c for c in text if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) <= 159)
    )


def html_to_text(html: str) -> tuple[str, str]:
    """(title, text): visible text with block breaks, whitespace collapsed."""
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    lines = (
        " ".join(line.split()) for line in _printable("".join(parser.parts)).split("\n")
    )
    text = "\n".join(line for line in lines if line)
    return " ".join(_printable(parser.title).split()), text


def _admit(url: str) -> str:
    try:
        return SearchResult(
            result_id="admission", rank=1, url=url, title="", snippet=""
        ).url
    except SearchProviderError:
        raise PageFetchError(PageFetchErrorCode.BLOCKED_URL) from None


async def _system_resolver(host: str) -> tuple[str, ...]:
    infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
    return tuple(str(info[4][0]) for info in infos)


def _global(address: str) -> bool:
    try:
        return public_ip(ipaddress.ip_address(address.split("%", 1)[0]))
    except ValueError:
        return False


def _pinned(url: str, address: str) -> tuple[str, str, str]:
    """(URL to the vetted address, Host header, TLS server name) for ``url``."""
    parts = urlsplit(url)
    host = (parts.hostname or "").encode("idna").decode("ascii").lower()
    header = host if parts.port is None else f"{host}:{parts.port}"
    target = str(httpx.URL(url).copy_with(host=address.split("%", 1)[0]))
    return target, header, host


class PageFetcher(ABC):
    """Reads one public page; callers own permission and deadlines."""

    @abstractmethod
    async def fetch(self, url: str, *, max_chars: int) -> FetchedPage:
        """Return bounded untrusted text, or raise PageFetchError."""

    async def aclose(self) -> None:
        """Release owned resources (none by default)."""


class HttpPageFetcher(PageFetcher):
    """GET with manual, re-admitted redirects and a byte cap."""

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[[str], Awaitable[tuple[str, ...]]] | None = None,
        timeout_seconds: float = 10.0,
        max_bytes: int = 1_000_000,
        max_redirects: int = 3,
        deadline_seconds: float = 30.0,
    ) -> None:
        if not 0 < timeout_seconds <= 60 or not 0 < max_bytes <= 5_000_000:
            raise ValueError("Invalid page fetch limits")
        if not 0 < deadline_seconds <= 120:
            raise ValueError("Invalid page fetch limits")
        if not 0 <= max_redirects <= 5:
            raise ValueError("Invalid page fetch limits")
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=timeout_seconds,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html, text/plain"},
            follow_redirects=False,
            trust_env=False,
            # A pooled connection is bound to one vetted address and TLS name.
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        self._resolve = _system_resolver if resolver is None else resolver
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects
        self._deadline = deadline_seconds

    async def _check_host(self, url: str) -> str:
        """The vetted address to connect to; every answer must be global."""
        parts = urlsplit(url)
        if parts.port not in _PORTS:
            raise PageFetchError(PageFetchErrorCode.BLOCKED_URL)
        host = parts.hostname or ""
        try:
            addresses = await self._resolve(host)
        except (OSError, UnicodeError):
            raise PageFetchError(PageFetchErrorCode.UNAVAILABLE) from None
        if not addresses or not all(_global(item) for item in addresses):
            raise PageFetchError(PageFetchErrorCode.BLOCKED_URL)
        return addresses[0]

    async def fetch(self, url: str, *, max_chars: int) -> FetchedPage:
        if type(max_chars) is not int or not 1 <= max_chars <= 50000:
            raise ValueError("Invalid max_chars")
        current = _admit(url)
        try:
            async with asyncio.timeout(self._deadline):
                return await self._fetch(current, max_chars)
        except TimeoutError:
            raise PageFetchError(PageFetchErrorCode.TIMEOUT) from None

    async def _fetch(self, current: str, max_chars: int) -> FetchedPage:
        for _ in range(self._max_redirects + 1):
            address = await self._check_host(current)
            target, host_header, server_name = _pinned(current, address)
            try:
                async with self._client.stream(
                    "GET",
                    target,
                    headers={"Host": host_header},
                    extensions={"sni_hostname": server_name},
                ) as response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        following = _admit(urljoin(current, location))
                        if current.startswith("https:") and not following.startswith(
                            "https:"
                        ):
                            raise PageFetchError(PageFetchErrorCode.BLOCKED_URL)
                        current = following
                        continue
                    if response.status_code != 200:
                        raise PageFetchError(PageFetchErrorCode.HTTP_STATUS)
                    kind = response.headers.get("content-type", "").lower()
                    if not kind.startswith(_TEXT_TYPES):
                        raise PageFetchError(PageFetchErrorCode.UNSUPPORTED_CONTENT)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body += chunk
                        if len(body) >= self._max_bytes:
                            del body[self._max_bytes :]
                            break
                    encoding = response.charset_encoding or "utf-8"
            except httpx.TimeoutException:
                raise PageFetchError(PageFetchErrorCode.TIMEOUT) from None
            except httpx.HTTPError:
                raise PageFetchError(PageFetchErrorCode.UNAVAILABLE) from None
            try:
                raw = bytes(body).decode(encoding, errors="replace")
            except LookupError:
                raw = bytes(body).decode("utf-8", errors="replace")
            if kind.startswith("text/plain"):
                title, text = (
                    "",
                    "\n".join(
                        " ".join(line.split()) for line in _printable(raw).split("\n")
                    ).strip(),
                )
            else:
                title, text = html_to_text(raw)
            return FetchedPage(
                final_url=current,
                title=title[:1024],
                text=text[:max_chars],
                truncated=len(text) > max_chars,
            )
        raise PageFetchError(PageFetchErrorCode.TOO_MANY_REDIRECTS)

    async def aclose(self) -> None:
        await self._client.aclose()
