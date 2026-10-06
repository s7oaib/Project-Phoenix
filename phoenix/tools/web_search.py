"""P1 search through immutable declarations and existing permission authority.

Queries and provider evidence are untrusted data. Trusted callers own consent
authenticity, deadlines and the injected provider's lifetime. No retry or fallback.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Self, TypeVar, cast
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    field_validator,
    model_validator,
)

from phoenix.models.provider import CancellationResult, CancellationStatus
from phoenix.security.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionGrant,
    PermissionPolicy,
    PermissionPolicyRule,
    PermissionRequest,
    PermissionStatus,
    UserPermissionDecision,
)
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolResultStatus,
    ToolRiskLevel,
)
from phoenix.web.search import (
    SearchProvider,
    SearchProviderError,
    SearchProviderErrorCode,
    SearchRequest,
    SearchResponse,
)

__all__ = (
    "WebSearchCall",
    "WebSearchIntent",
    "WebSearchToolResult",
    "WebSearchErrorCode",
    "WebSearchError",
    "web_search_tool_definitions",
    "WebSearchTool",
)

_REQUIREMENT = "P1: exact search scope"
_Model = TypeVar("_Model", bound=BaseModel)


class WebSearchErrorCode(StrEnum):
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


class WebSearchError(Exception):
    """Closed code only; no raw query, scope or collaborator diagnostics."""

    def __init__(self, code: WebSearchErrorCode) -> None:
        if not any(code is item for item in WebSearchErrorCode):
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


def _enum(value: object, vocabulary: type[StrEnum]) -> object:
    if type(value) is not str and not any(value is item for item in vocabulary):
        raise ValueError("invalid_input")
    return value


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


class _Contract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        revalidate_instances="always",
        validate_default=True,
        hide_input_in_errors=True,
    )

    @model_validator(mode="wrap")
    @classmethod
    def snapshot(cls, value: object, handler: ModelWrapValidatorHandler[Self]) -> Self:
        try:
            return handler(
                _stored(value, cls) if isinstance(value, BaseModel) else value
            )
        except Exception:
            raise WebSearchError(WebSearchErrorCode.INVALID_INPUT) from None

    @field_validator("call_id", "provider_id", mode="before", check_fields=False)
    @classmethod
    def identity(cls, value: object) -> str:
        return _identifier(value)

    @field_validator(
        "tool_id",
        "action",
        "resource",
        "permission_requirement",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def ordinary_metadata(cls, value: object) -> str:
        return _text(value, 4096, meaningful=True)


class WebSearchCall(_Contract):
    call_id: str = Field(default_factory=lambda: str(uuid4()))
    provider_id: str
    query_text: str = Field(repr=False)
    max_results: Annotated[int, Field(strict=True, ge=1, le=20)]
    as_of: AwareDatetime

    @field_validator("query_text", mode="before")
    @classmethod
    def query(cls, value: object) -> str:
        return _text(value, 4096, meaningful=True)

    @field_validator("as_of", mode="before")
    @classmethod
    def timestamp(cls, value: object) -> object:
        if type(value) not in (str, datetime):
            raise ValueError("invalid_input")
        return value

    @field_validator("as_of")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


def _resource(call: WebSearchCall) -> str:
    return _json(
        [
            "search",
            call.provider_id,
            sha256(call.query_text.encode("utf-8")).hexdigest(),
            call.max_results,
            call.as_of.isoformat(),
        ]
    )


def _resource_fields(resource: str, provider_id: str) -> None:
    data = json.loads(resource)
    if type(data) is not list or len(data) != 5 or data[:2] != ["search", provider_id]:
        raise ValueError("invalid_input")
    digest = data[2]
    if (
        type(digest) is not str
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise ValueError("invalid_input")
    request = SearchRequest(
        request_id="resource", query_text="resource", max_results=data[3], as_of=data[4]
    )
    if data[4] != request.as_of.isoformat() or _json(data) != resource:
        raise ValueError("invalid_input")


def _check_resource(resource: str, provider_id: str) -> None:
    try:
        _resource_fields(resource, provider_id)
    except Exception:
        raise WebSearchError(WebSearchErrorCode.INVALID_INPUT) from None


class WebSearchIntent(_Contract):
    call_id: str
    provider_id: str
    tool_id: str
    action: str
    resource: str
    risk_level: ToolRiskLevel
    permission_requirement: str

    @field_validator("risk_level", mode="before")
    @classmethod
    def risk(cls, value: object) -> object:
        return _enum(value, ToolRiskLevel)

    @model_validator(mode="after")
    def canonical(self) -> Self:
        if (
            self.tool_id,
            self.action,
            self.risk_level,
            self.permission_requirement,
        ) != (
            "web.search",
            "search",
            ToolRiskLevel.P1,
            _REQUIREMENT,
        ):
            raise WebSearchError(WebSearchErrorCode.INVALID_INPUT)
        _check_resource(self.resource, self.provider_id)
        return self


class WebSearchToolResult(_Contract):
    call_id: str
    provider_id: str
    tool_id: str
    resource: str
    status: ToolResultStatus
    attempted: bool = Field(strict=True)
    permission_decision: PermissionDecision
    response: SearchResponse | None = None
    error_code: WebSearchErrorCode | None = None

    @field_validator("permission_decision", mode="before")
    @classmethod
    def permission(cls, value: object) -> PermissionDecision:
        return (
            _exact(value, PermissionDecision)
            if isinstance(value, BaseModel)
            else PermissionDecision.model_validate(value)
        )

    @field_validator("response", mode="before")
    @classmethod
    def response_snapshot(cls, value: object) -> SearchResponse | None:
        return None if value is None else SearchResponse.model_validate(value)

    @field_validator("status", mode="before")
    @classmethod
    def status_enum(cls, value: object) -> object:
        return _enum(value, ToolResultStatus)

    @field_validator("error_code", mode="before")
    @classmethod
    def error_enum(cls, value: object) -> object:
        return None if value is None else _enum(value, WebSearchErrorCode)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        decision = self.permission_decision
        if self.tool_id != "web.search" or (
            decision.tool_id,
            decision.risk_level,
            decision.permission_requirement,
        ) != (
            "web.search",
            ToolRiskLevel.P1,
            _REQUIREMENT,
        ):
            raise WebSearchError(WebSearchErrorCode.INVALID_INPUT)
        _check_resource(self.resource, self.provider_id)
        if decision.status != PermissionStatus.ALLOWED:
            if (
                self.status != ToolResultStatus.DENIED
                or self.attempted
                or self.response is not None
                or self.error_code is not None
            ):
                raise WebSearchError(WebSearchErrorCode.INVALID_INPUT)
        elif not self.attempted:
            raise WebSearchError(WebSearchErrorCode.INVALID_INPUT)
        elif self.status == ToolResultStatus.SUCCESS:
            if self.response is None or self.error_code is not None:
                raise WebSearchError(WebSearchErrorCode.INVALID_INPUT)
            data = json.loads(self.resource)
            try:
                self.response.validate_request(
                    SearchRequest(
                        request_id=self.call_id,
                        query_text="correlation",
                        max_results=data[3],
                        as_of=data[4],
                    ),
                    self.provider_id,
                )
            except Exception:
                raise WebSearchError(WebSearchErrorCode.INVALID_INPUT) from None
        elif (
            self.response is not None
            or self.error_code is None
            or self.status != _failure_status(self.error_code)
        ):
            raise WebSearchError(WebSearchErrorCode.INVALID_INPUT)
        return self


def _failure_status(code: WebSearchErrorCode) -> ToolResultStatus:
    if code == WebSearchErrorCode.TIMEOUT:
        return ToolResultStatus.TIMEOUT
    if code == WebSearchErrorCode.UNAVAILABLE:
        return ToolResultStatus.UNAVAILABLE_DEPENDENCY
    return ToolResultStatus.FAILURE


def web_search_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Pure declaration only; timeout metadata requires cooperative adapters."""
    return (
        ToolDefinition(
            tool_id="web.search",
            description="Search one injected provider; evidence remains untrusted.",
            input_schema=WebSearchCall.model_json_schema(),
            output_schema=WebSearchToolResult.model_json_schema(),
            risk_level=ToolRiskLevel.P1,
            permission_requirement=_REQUIREMENT,
            timeout_seconds=30,
            cancellation_supported=True,
            audit_behavior="no mandatory durable audit",
            supported_platforms=("win32", "linux", "darwin"),
            supported_environments=("local",),
        ),
    )


def _cancellation(
    value: object, operation_id: str, provider_id: str
) -> CancellationResult:
    data = _stored(cast(BaseModel, value), CancellationResult)
    data["operation_id"] = _identifier(data.get("operation_id"))
    data["provider_id"] = _identifier(data.get("provider_id"))
    _enum(data.get("status"), CancellationStatus)
    result = CancellationResult.model_validate(data)
    if (result.operation_id, result.provider_id) != (operation_id, provider_id):
        raise ValueError("invalid_input")
    return result


@dataclass(frozen=True, slots=True, init=False)
class WebSearchTool:
    """Explicit search execution, with caller-owned provider and no hidden work."""

    _engine: PermissionEngine
    _provider: SearchProvider
    _provider_id: str

    def __init__(
        self, registry: ToolRegistry, policy: PermissionPolicy, provider: SearchProvider
    ) -> None:
        try:
            if (
                hasattr(self, "_engine")
                or type(registry) is not ToolRegistry
                or type(policy) is not PermissionPolicy
                or not isinstance(provider, SearchProvider)
            ):
                raise ValueError("invalid_input")
            definitions = tuple(
                _exact(item, ToolDefinition) for item in registry.list_tools()
            )
            for item in definitions:
                _enum(item.risk_level, ToolRiskLevel)
            snapshot = ToolRegistry(definitions)
            canonical = web_search_tool_definitions()[0]
            if snapshot.require("web.search") != canonical:
                raise ValueError("invalid_input")
            identity = _identifier(provider.provider_id)
            if identity != provider.provider_id:
                raise ValueError("invalid_input")
            accepted_policy = _exact(policy, PermissionPolicy)
            rules = tuple(_exact(item, PermissionPolicyRule) for item in policy.rules)
            if accepted_policy.rules != rules:
                raise ValueError("invalid_input")
            engine = PermissionEngine(snapshot, PermissionPolicy(rules=rules))
        except Exception:
            raise WebSearchError(WebSearchErrorCode.INVALID_INPUT) from None
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "_provider", provider)
        object.__setattr__(self, "_provider_id", identity)

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def describe(self, call: WebSearchCall) -> WebSearchIntent:
        try:
            call = _exact(call, WebSearchCall)
            if (
                call.provider_id != self.provider_id
                or self._provider.provider_id != self.provider_id
            ):
                raise ValueError("invalid_input")
            return WebSearchIntent(
                call_id=call.call_id,
                provider_id=self.provider_id,
                tool_id="web.search",
                action="search",
                resource=_resource(call),
                risk_level=ToolRiskLevel.P1,
                permission_requirement=_REQUIREMENT,
            )
        except Exception:
            raise WebSearchError(WebSearchErrorCode.INVALID_INPUT) from None

    async def execute(
        self,
        call: WebSearchCall,
        request: PermissionRequest,
        *,
        grants: Sequence[PermissionGrant] = (),
        user_decision: UserPermissionDecision | None = None,
    ) -> WebSearchToolResult:
        try:
            call = _exact(call, WebSearchCall)
            intent = self.describe(call)
            request = _exact(request, PermissionRequest)
            if (request.tool_id, request.action, request.resource) != (
                intent.tool_id,
                intent.action,
                intent.resource,
            ):
                raise ValueError("invalid_input")
            if type(grants) not in (tuple, list):
                raise ValueError("invalid_input")
            accepted_grants = tuple(_exact(item, PermissionGrant) for item in grants)
            user = (
                None
                if user_decision is None
                else _exact(user_decision, UserPermissionDecision)
            )
            decision = self._engine.evaluate(
                request, grants=accepted_grants, user_decision=user
            )
        except Exception:
            raise WebSearchError(WebSearchErrorCode.INVALID_INPUT) from None
        evidence = dict(
            call_id=call.call_id,
            provider_id=self.provider_id,
            tool_id="web.search",
            resource=intent.resource,
            permission_decision=decision,
        )
        if decision.status != PermissionStatus.ALLOWED:
            return WebSearchToolResult.model_validate(
                evidence | dict(status=ToolResultStatus.DENIED, attempted=False)
            )
        search_request = SearchRequest(
            request_id=call.call_id,
            query_text=call.query_text,
            max_results=call.max_results,
            as_of=call.as_of,
        )
        response = None
        code = None
        try:
            raw = await self._provider.search(search_request)
            response = _exact(raw, SearchResponse)
            response.validate_request(search_request, self.provider_id)
        except SearchProviderError as error:
            code = (
                WebSearchErrorCode(error.code.value)
                if type(error) is SearchProviderError
                and any(error.code is item for item in SearchProviderErrorCode)
                else WebSearchErrorCode.INVALID_RESPONSE
            )
        except TimeoutError:
            code = WebSearchErrorCode.TIMEOUT
        except Exception:
            code = WebSearchErrorCode.INVALID_RESPONSE
        if code is not None:
            return WebSearchToolResult.model_validate(
                evidence
                | dict(status=_failure_status(code), attempted=True, error_code=code)
            )
        return WebSearchToolResult.model_validate(
            evidence
            | dict(status=ToolResultStatus.SUCCESS, attempted=True, response=response)
        )

    async def cancel(self, call_id: str) -> CancellationResult:
        try:
            identity = _identifier(call_id)
        except Exception:
            raise WebSearchError(WebSearchErrorCode.INVALID_INPUT) from None
        try:
            return _cancellation(
                await self._provider.cancel(identity), identity, self.provider_id
            )
        except Exception:
            raise WebSearchError(WebSearchErrorCode.INVALID_RESPONSE) from None
