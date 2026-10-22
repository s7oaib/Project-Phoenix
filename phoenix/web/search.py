"""Pure search contracts; candidate sources remain untrusted, inert data.

Future research orchestration belongs to web_research (URAHARA display alias).
Actual P1 search/tool/permission integration belongs to PHX-054. This module
neither fetches nor resolves URLs; lexical admission is not navigation approval
or DNS-rebinding protection. Ranks and dates establish no truth or freshness.
"""

from __future__ import annotations

import ipaddress
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, ClassVar, Self, cast
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    ValidationError,
    field_validator,
    model_validator,
)

from phoenix.models.provider import CancellationResult

__all__ = (
    "SearchResult",
    "SearchRequest",
    "SearchResponse",
    "SearchProvider",
    "SearchProviderError",
    "SearchProviderErrorCode",
)

_Count = Annotated[int, Field(strict=True, ge=1, le=20)]


class SearchProviderErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    INVALID_RESPONSE = "invalid_response"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    UNAUTHORIZED = "unauthorized"
    BUSY = "busy"
    CLOSED = "closed"
    WRONG_EVENT_LOOP = "wrong_event_loop"
    CLEANUP_FAILURE = "cleanup_failure"


class SearchProviderError(Exception):
    """Closed category only, without query, source or raw provider diagnostics."""

    def __init__(self, code: SearchProviderErrorCode) -> None:
        if not any(code is member for member in SearchProviderErrorCode):
            raise ValueError("invalid_input")
        self.code = code
        super().__init__(code.value)


def _controls(text: str) -> bool:
    return any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in text)


def _text(value: object, bound: int, *, meaningful: bool = False) -> str:
    if type(value) is not str or "\0" in value or len(value) > bound:
        raise ValueError("invalid_input")
    value.encode("utf-8")
    if meaningful and not value.strip():
        raise ValueError("invalid_input")
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or _controls(value):
        raise ValueError("invalid_input")
    normalized = _text(value.strip(), 256, meaningful=True)
    if not normalized or len(normalized) > 256:
        raise ValueError("invalid_input")
    return normalized


# PHX-123 (ADR-183): ranges Python 3.11's ``is_global`` admits but that can
# reach this machine or its network: IETF protocol assignments, 6to4 and
# Teredo (both embed an IPv4 address a relay would forward to).
_DENIED_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("192.0.0.0/24", "2002::/16", "2001::/32")
)


def public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Whether ``address`` is globally routable and reaches no local network."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return public_ip(address.ipv4_mapped)
    if any(address in network for network in _DENIED_NETWORKS):
        return False
    return address.is_global and not (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _url(value: object) -> str:
    # Check before stripping: urlsplit otherwise removes some control characters.
    if type(value) is not str or _controls(value) or "\\" in value:
        raise ValueError("invalid_response")
    normalized = value.strip()
    _text(normalized, 4096, meaningful=True)
    parsed = urlsplit(normalized)
    host = parsed.hostname
    if (
        normalized.split(":", 1)[0] not in ("http", "https")
        or not host
        or "@" in parsed.netloc
        or "%" in host
        or any(char.isspace() for char in normalized)
        or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        or parsed.netloc.endswith(":")
    ):
        raise ValueError("invalid_response")
    ascii_host = host.encode("idna").decode("ascii").lower().rstrip(".")
    if parsed.netloc.startswith("["):
        suffix = parsed.netloc[parsed.netloc.index("]") + 1 :]
        if suffix and (not suffix.startswith(":") or not suffix[1:].isdigit()):
            raise ValueError("invalid_response")
    if not ascii_host or ascii_host == "localhost" or ascii_host.endswith(".localhost"):
        raise ValueError("invalid_response")
    try:
        address = ipaddress.ip_address(ascii_host)
    except ValueError:
        # Numeric legacy IPv4 spellings are ambiguous browser metadata, not DNS.
        labels = ascii_host.split(".")
        if (
            labels[-1].isdigit()
            or labels[-1].startswith("0x")
            or any(not label or len(label) > 63 for label in labels)
            or any(
                char not in "abcdefghijklmnopqrstuvwxyz0123456789-."
                for char in ascii_host
            )
            or any(label.startswith("-") or label.endswith("-") for label in labels)
            or "[" in parsed.netloc
            or "]" in parsed.netloc
        ):
            raise ValueError("invalid_response") from None
    else:
        if not public_ip(address):
            raise ValueError("invalid_response")
    return normalized


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )
    _failure_code: ClassVar[SearchProviderErrorCode] = (
        SearchProviderErrorCode.INVALID_RESPONSE
    )

    @model_validator(mode="wrap")
    @classmethod
    def exact_snapshot(
        cls, value: object, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        try:
            if isinstance(value, BaseModel):
                if type(value) is not cls:
                    raise ValueError("invalid_input")
                extras = value.__pydantic_extra__
                if extras is not None and (type(extras) is not dict or extras):
                    raise ValueError("invalid_input")
                value = dict(vars(value))
            return handler(value)
        except (
            ValidationError,
            ValueError,
            TypeError,
            AttributeError,
            OverflowError,
            RecursionError,
        ):
            # Do not retain a Pydantic error containing raw input/extra field names.
            raise SearchProviderError(cls._failure_code) from None

    @field_validator(
        "request_id", "result_id", "provider_id", mode="before", check_fields=False
    )
    @classmethod
    def identifier(cls, value: object) -> str:
        return _identifier(value)

    @field_validator("as_of", "published_at", mode="before", check_fields=False)
    @classmethod
    def timestamp_input(cls, value: object) -> object:
        if value is not None and type(value) not in (datetime, str):
            raise ValueError("invalid_input")
        if type(value) is str:
            _text(value, 256, meaningful=True)
        return value

    @field_validator("as_of", "published_at", check_fields=False)
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None


class SearchRequest(_Contract):
    """Explicit reference time and untrusted query, without backend options."""

    _failure_code = SearchProviderErrorCode.INVALID_INPUT
    request_id: str = Field(default_factory=lambda: str(uuid4()))
    query_text: str = Field(repr=False)
    max_results: _Count
    as_of: AwareDatetime

    @field_validator("query_text", mode="before")
    @classmethod
    def query(cls, value: object) -> str:
        return _text(value, 4096, meaningful=True)


class SearchResult(_Contract):
    """Provider-local ordering and provenance, never approved navigation or truth."""

    result_id: str
    rank: _Count
    url: str
    title: str = Field(repr=False)
    snippet: str = Field(repr=False)
    source_name: str | None = None
    published_at: AwareDatetime | None = None
    date_text: str | None = None
    source_ref: str | None = None

    @field_validator("url", mode="before")
    @classmethod
    def public_url(cls, value: object) -> str:
        return _url(value)

    @field_validator("title", mode="before")
    @classmethod
    def title_text(cls, value: object) -> str:
        return _text(value, 1024)

    @field_validator("snippet", mode="before")
    @classmethod
    def snippet_text(cls, value: object) -> str:
        return _text(value, 8192)

    @field_validator("source_name", mode="before")
    @classmethod
    def source_text(cls, value: object) -> str | None:
        return None if value is None else _text(value, 512, meaningful=True)

    @field_validator("date_text", mode="before")
    @classmethod
    def raw_date_text(cls, value: object) -> str | None:
        return None if value is None else _text(value, 256)

    @field_validator("source_ref", mode="before")
    @classmethod
    def opaque_reference(cls, value: object) -> str | None:
        if value is None:
            return None
        text = _text(value, 1024)
        if _controls(text):
            raise ValueError("invalid_response")
        return text


class SearchResponse(_Contract):
    """Detached provider evidence; empty results are valid, not a success claim."""

    request_id: str
    provider_id: str
    as_of: AwareDatetime
    results: tuple[SearchResult, ...]

    @field_validator("results", mode="before")
    @classmethod
    def result_snapshots(cls, value: object) -> tuple[SearchResult, ...]:
        if type(value) not in (list, tuple) or len(cast(Sequence[object], value)) > 20:
            raise ValueError("invalid_response")
        return tuple(
            SearchResult.model_validate(item) for item in cast(Sequence[object], value)
        )

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if (
            tuple(item.rank for item in self.results)
            != tuple(range(1, len(self.results) + 1))
            or len({item.result_id for item in self.results}) != len(self.results)
            or len({item.url for item in self.results}) != len(self.results)
        ):
            raise SearchProviderError(SearchProviderErrorCode.INVALID_RESPONSE)
        return self

    def validate_request(self, request: SearchRequest, provider_id: str) -> None:
        """Revalidate exact stored evidence and check correlation, not query meaning."""
        response = SearchResponse.model_validate(self)
        if type(request) is not SearchRequest:
            raise SearchProviderError(SearchProviderErrorCode.INVALID_INPUT)
        accepted = SearchRequest.model_validate(request)
        try:
            provider = _identifier(provider_id)
        except (ValueError, TypeError):
            raise SearchProviderError(SearchProviderErrorCode.INVALID_INPUT) from None
        if (response.request_id, response.provider_id, response.as_of) != (
            accepted.request_id,
            provider,
            accepted.as_of,
        ) or len(response.results) > accepted.max_results:
            raise SearchProviderError(SearchProviderErrorCode.INVALID_RESPONSE)


class SearchProvider(ABC):
    """Pure neutral boundary, not a tool or concrete search implementation.

    Adapters call super().__init__ with their stable ID. Implementations own
    sorted immutable active-ID snapshots, bounded I/O, per-request cancellation
    and explicit cleanup. Accepted cancellation acknowledges a request, not
    proven remote cessation. Construction here starts no work; aliases, IDs and
    response content grant no permission. Future consumers must revalidate and
    correlate every response before use.
    """

    def __init__(self, provider_id: str) -> None:
        try:
            self._provider_id = _identifier(provider_id)
        except (ValueError, TypeError):
            raise SearchProviderError(SearchProviderErrorCode.INVALID_INPUT) from None

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    @abstractmethod
    def active_request_ids(self) -> tuple[str, ...]:
        """Sorted immutable observational IDs, with no tasks or authority."""

    @abstractmethod
    async def search(self, request: SearchRequest) -> SearchResponse:
        """Return exact correlated untrusted evidence or a safe explicit error."""

    @abstractmethod
    async def cancel(self, request_id: str) -> CancellationResult:
        """Request cancellation of only this ID, never global shutdown."""

    @abstractmethod
    async def aclose(self) -> None:
        """Explicitly release owned resources; construction allocates none here."""
