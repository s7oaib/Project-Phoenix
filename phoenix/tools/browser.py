"""Explicit isolated DOM capability; page evidence is untrusted, never authority.

Trusted integration owns consent authenticity, request admission and replay control.
Public-host DNS rebinding and hostile page changes after the URL check are not
prevented atomically. Browser effects and durable audit are separate transactions.
No browser binary installation, credentials or automatic action chaining occurs.
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Self, TypeVar, cast
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
    field_serializer,
    field_validator,
    model_validator,
)

from phoenix.security.audit import AuditError, AuditTrail, audit_required_for_execution
from phoenix.security.permissions import (
    PermissionDecision,
    PermissionEngine,
    PermissionGrant,
    PermissionPolicy,
    PermissionRequest,
    PermissionStatus,
    UserPermissionDecision,
)
from phoenix.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    ToolRegistryError,
    ToolResultStatus,
    ToolRiskLevel,
)

if TYPE_CHECKING:
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        CDPSession,
        Locator,
        Page,
        Playwright,
        Route,
    )

__all__ = (
    "BrowserOperation",
    "BrowserErrorCode",
    "BrowserCall",
    "BrowserIntent",
    "BrowserElement",
    "BrowserToolResult",
    "BrowserInputError",
    "BrowserAuditError",
    "BrowserPostExecutionAuditError",
    "browser_tool_definitions",
    "BrowserTool",
)

_TIMEOUT_MS = 30000
_ATTRIBUTES = ("id", "class", "role", "aria-label", "name", "type", "href", "title")
_Id = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Text = Annotated[str, StringConstraints(strict=True)]
_Model = TypeVar("_Model", bound=BaseModel)


class BrowserOperation(StrEnum):
    SESSION_OPEN = "session_open"
    NAVIGATE = "navigate"
    READ_PAGE = "read_page"
    INSPECT_ELEMENT = "inspect_element"
    CLICK = "click"
    FILL = "fill"
    SESSION_CLOSE = "session_close"


class BrowserErrorCode(StrEnum):
    SESSION_EXISTS = "session_exists"
    SESSION_NOT_FOUND = "session_not_found"
    INVALID_URL = "invalid_url"
    BLOCKED_URL = "blocked_url"
    PAGE_CHANGED = "page_changed"
    BROWSER_UNAVAILABLE = "browser_unavailable"
    NAVIGATION_FAILURE = "navigation_failure"
    SELECTOR_INVALID = "selector_invalid"
    ELEMENT_NOT_FOUND = "element_not_found"
    ELEMENT_AMBIGUOUS = "element_ambiguous"
    INTERACTION_FAILURE = "interaction_failure"
    INPUT_FORBIDDEN = "input_forbidden"
    DOWNLOAD_BLOCKED = "download_blocked"
    POPUP_BLOCKED = "popup_blocked"
    TIMEOUT = "timeout"
    IO_FAILURE = "io_failure"


class BrowserInputError(ValueError):
    """Safe invalid call/configuration/permission evidence, without private input."""

    def __init__(self) -> None:
        super().__init__("Invalid browser input or authority")


class BrowserAuditError(Exception):
    def __init__(self, call_id: str) -> None:
        super().__init__("Required browser permission audit failed")
        self.call_id = call_id
        self.effect_may_have_occurred = False


class BrowserPostExecutionAuditError(BrowserAuditError):
    def __init__(self, call_id: str) -> None:
        Exception.__init__(self, "Browser outcome audit failed after attempt")
        self.call_id = call_id
        self.effect_may_have_occurred = True


def _enum(value: object, vocabulary: type[StrEnum]) -> object:
    if type(value) is not str and not any(value is member for member in vocabulary):
        raise ValueError("Expected exact vocabulary text or declared member")
    return value


def _stored(value: BaseModel, contract: type[BaseModel]) -> dict[str, object]:
    if type(value) is not contract:
        raise ValueError("Expected exact contract")
    extras = value.__pydantic_extra__
    if extras is not None and type(extras) is not dict:
        raise ValueError("Invalid stored extras")
    return {**vars(value), **(extras or {})}


def _validated(value: object, contract: type[_Model]) -> _Model:
    if not isinstance(value, BaseModel):
        raise ValueError("Expected exact contract")
    return contract.model_validate(_stored(value, contract))


def _controls(value: str) -> bool:
    return any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)


def _url(value: object) -> str:
    if type(value) is not str or _controls(value) or "\\" in value:
        raise ValueError("Invalid URL")
    normalized = value.strip()
    if not normalized or len(normalized) > 4096:
        raise ValueError("Invalid URL length")
    try:
        parsed = urlsplit(normalized)
        host = parsed.hostname
        if (
            normalized.split(":", 1)[0] not in ("http", "https")
            or not parsed.netloc
            or not host
            or "@" in parsed.netloc
            or "%" in host
            or any(char.isspace() for char in normalized)
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
        ):
            raise ValueError("Invalid URL syntax")
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
        if host == "localhost" or host.endswith(".localhost") or not host:
            raise ValueError("Local host is forbidden")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            # Chromium accepts historical shortened/integer/octal/hex IPv4 forms.
            # Reject numeric terminal labels rather than treating them as DNS names.
            labels = host.split(".")
            if (
                labels[-1].isdigit()
                or labels[-1].startswith("0x")
                or any(not label or len(label) > 63 for label in labels)
                or any(
                    char not in "abcdefghijklmnopqrstuvwxyz0123456789-."
                    for char in host
                )
                or any(label.startswith("-") or label.endswith("-") for label in labels)
            ):
                raise ValueError("Invalid or ambiguous hostname") from None
        else:
            if (
                not address.is_global
                or address.is_multicast
                or address.is_reserved
                or address.is_loopback
                or address.is_link_local
                or address.is_unspecified
                or address.is_private
                or (
                    isinstance(address, ipaddress.IPv6Address)
                    and address.ipv4_mapped is not None
                    and not address.ipv4_mapped.is_global
                )
            ):
                raise ValueError("Nonpublic address is forbidden")
    except (ValueError, UnicodeError):
        raise ValueError("URL is invalid or blocked") from None
    return normalized


def _selector(value: object) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or len(value) > 2048
        or _controls(value)
    ):
        raise ValueError("Invalid CSS selector")
    stripped = value.lstrip()
    if (
        ">>" in value
        or stripped.startswith(
            ("//", "xpath=", "text=", "css=", "id=", "_react=", "_vue=")
        )
        or any(
            token in value
            for token in (
                ":has-text(",
                ":text(",
                ":text-is(",
                ":text-matches(",
                ":nth-match(",
                ":visible",
                ":light(",
            )
        )
    ):
        raise ValueError("Only CSS selector data is permitted")
    return value


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
        return handler(_stored(value, cls) if isinstance(value, BaseModel) else value)

    @field_validator("operation", mode="before", check_fields=False)
    @classmethod
    def operation_member(cls, value: object) -> object:
        return _enum(value, BrowserOperation)

    @field_validator("call_id", "session_id", mode="before", check_fields=False)
    @classmethod
    def opaque_id(cls, value: object) -> object:
        if type(value) is not str or _controls(value):
            raise ValueError("Expected ordinary control-free identifier")
        return value


class BrowserCall(_Contract):
    call_id: _Id = Field(default_factory=lambda: str(uuid4()))
    operation: BrowserOperation
    session_id: _Id
    url: _Text | None = None
    expected_url: _Text | None = None
    selector: _Text | None = None
    text: _Text | None = Field(default=None, repr=False)
    max_chars: Annotated[int, Field(strict=True, ge=1, le=200000)] | None = None

    @field_validator("url", "expected_url", mode="before")
    @classmethod
    def url_value(cls, value: object) -> str | None:
        return _url(value) if value is not None else None

    @field_validator("selector", mode="before")
    @classmethod
    def selector_value(cls, value: object) -> str | None:
        return _selector(value) if value is not None else None

    @field_validator("text", mode="before")
    @classmethod
    def fill_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if type(value) is not str:
            raise ValueError("Fill requires explicit ordinary text")
        try:
            if len(value.encode("utf-8")) > 65536:
                raise ValueError("Fill text exceeds bound")
        except UnicodeError:
            raise ValueError("Fill requires valid Unicode text") from None
        return value

    @model_validator(mode="after")
    def shape(self) -> Self:
        required = {
            BrowserOperation.SESSION_OPEN: (False, False, False, False),
            BrowserOperation.NAVIGATE: (True, False, False, False),
            BrowserOperation.READ_PAGE: (False, True, False, False),
            BrowserOperation.INSPECT_ELEMENT: (False, True, True, False),
            BrowserOperation.CLICK: (False, True, True, False),
            BrowserOperation.FILL: (False, True, True, True),
            BrowserOperation.SESSION_CLOSE: (False, False, False, False),
        }[self.operation]
        readable = self.operation in (
            BrowserOperation.READ_PAGE,
            BrowserOperation.INSPECT_ELEMENT,
        )
        if (
            tuple(
                x is not None
                for x in (self.url, self.expected_url, self.selector, self.text)
            )
            != required
            or (self.max_chars is not None and not readable)
            or (
                self.operation == BrowserOperation.INSPECT_ELEMENT
                and self.max_chars is not None
                and self.max_chars > 50000
            )
        ):
            raise ValueError("Browser operation fields disagree")
        return self


_RISKS = (
    ToolRiskLevel.P2,
    ToolRiskLevel.P2,
    ToolRiskLevel.P1,
    ToolRiskLevel.P1,
    ToolRiskLevel.P4,
    ToolRiskLevel.P4,
    ToolRiskLevel.P2,
)


def _metadata(operation: BrowserOperation) -> tuple[str, ToolRiskLevel, str]:
    risk = _RISKS[tuple(BrowserOperation).index(operation)]
    return (
        f"browser.{operation.value}",
        risk,
        f"{risk.value}: exact isolated browser operation",
    )


def _bound(call: BrowserCall) -> int:
    return call.max_chars or (
        100000 if call.operation == BrowserOperation.READ_PAGE else 20000
    )


def _resource(call: BrowserCall) -> str:
    data: list[object] = [call.operation.value, call.session_id]
    if call.operation == BrowserOperation.NAVIGATE:
        data.append(call.url)
    elif call.expected_url is not None:
        data.append(call.expected_url)
        if call.selector is not None:
            data.append(call.selector)
        if call.operation in (
            BrowserOperation.READ_PAGE,
            BrowserOperation.INSPECT_ELEMENT,
        ):
            data.append(_bound(call))
        elif call.operation == BrowserOperation.FILL:
            assert call.text is not None
            data.append(sha256(call.text.encode("utf-8")).hexdigest())
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"))


def _check_resource(
    resource: str, operation: BrowserOperation, session_id: str
) -> None:
    data = json.loads(resource)
    if type(data) is not list or data[:2] != [operation.value, session_id]:
        raise ValueError("Invalid resource correlation")
    lengths = {
        "session_open": 2,
        "navigate": 3,
        "read_page": 4,
        "inspect_element": 5,
        "click": 4,
        "fill": 5,
        "session_close": 2,
    }
    if len(data) != lengths[operation.value]:
        raise ValueError("Invalid resource shape")
    if len(data) > 2 and _url(data[2]) != data[2]:
        raise ValueError("Noncanonical resource URL")
    if operation in (
        BrowserOperation.INSPECT_ELEMENT,
        BrowserOperation.CLICK,
        BrowserOperation.FILL,
    ):
        _selector(data[3])
    if operation in (BrowserOperation.READ_PAGE, BrowserOperation.INSPECT_ELEMENT):
        maximum = 200000 if operation == BrowserOperation.READ_PAGE else 50000
        if type(data[-1]) is not int or not 1 <= data[-1] <= maximum:
            raise ValueError("Invalid resource bound")
    if operation == BrowserOperation.FILL and (
        type(data[-1]) is not str
        or len(data[-1]) != 64
        or any(c not in "0123456789abcdef" for c in data[-1])
    ):
        raise ValueError("Invalid fill digest")
    if json.dumps(data, ensure_ascii=True, separators=(",", ":")) != resource:
        raise ValueError("Noncanonical resource")


class BrowserIntent(_Contract):
    call_id: _Id
    operation: BrowserOperation
    session_id: _Id
    tool_id: _Id
    action: _Id
    resource: _Id
    risk_level: ToolRiskLevel
    permission_requirement: _Id

    @field_validator("risk_level", mode="before")
    @classmethod
    def risk(cls, value: object) -> object:
        return _enum(value, ToolRiskLevel)

    @model_validator(mode="after")
    def canonical(self) -> Self:
        if (self.tool_id, self.risk_level, self.permission_requirement) != _metadata(
            self.operation
        ) or self.action != self.operation.value:
            raise ValueError("Intent disagrees with canonical tool")
        _check_resource(self.resource, self.operation, self.session_id)
        return self


class BrowserElement(_Contract):
    text: Annotated[str, StringConstraints(strict=True, max_length=50000)]
    attributes: Mapping[str, str]

    @field_validator("attributes", mode="plain", json_schema_input_type=dict[str, str])
    @classmethod
    def attribute_snapshot(cls, value: object) -> Mapping[str, str]:
        if type(value) not in (dict, MappingProxyType):
            raise ValueError("Expected ordinary attribute evidence")
        data = cast(Mapping[object, object], value)
        if any(
            type(k) is not str
            or k not in _ATTRIBUTES
            or type(v) is not str
            or len(v) > 4096
            for k, v in data.items()
        ):
            raise ValueError("Invalid attribute evidence")
        return MappingProxyType(dict(cast(Mapping[str, str], data)))

    @field_serializer("attributes")
    def attributes_json(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)


class BrowserToolResult(_Contract):
    call_id: _Id
    operation: BrowserOperation
    session_id: _Id
    tool_id: _Id
    resource: _Id
    status: ToolResultStatus
    attempted: bool = Field(strict=True)
    effect_applied: bool = Field(strict=True)
    permission_decision: PermissionDecision
    permission_audit_id: _Id | None = None
    outcome_audit_id: _Id | None = None
    final_url: (
        Annotated[str, StringConstraints(strict=True, max_length=4096)] | None
    ) = None
    title: Annotated[str, StringConstraints(strict=True, max_length=4096)] | None = None
    text: Annotated[str, StringConstraints(strict=True, max_length=200000)] | None = (
        None
    )
    element: BrowserElement | None = None
    truncated: bool = Field(default=False, strict=True)
    error_code: BrowserErrorCode | None = None

    @field_validator("status", mode="before")
    @classmethod
    def status_member(cls, value: object) -> object:
        return _enum(value, ToolResultStatus)

    @field_validator("error_code", mode="before")
    @classmethod
    def error_member(cls, value: object) -> object:
        return _enum(value, BrowserErrorCode) if value is not None else None

    @field_validator("permission_decision", mode="before")
    @classmethod
    def decision_snapshot(cls, value: object) -> PermissionDecision:
        return (
            _validated(value, PermissionDecision)
            if isinstance(value, BaseModel)
            else PermissionDecision.model_validate(value)
        )

    @field_validator("element", mode="before")
    @classmethod
    def element_snapshot(cls, value: object) -> BrowserElement | None:
        if value is None:
            return None
        return (
            _validated(value, BrowserElement)
            if isinstance(value, BaseModel)
            else BrowserElement.model_validate(value)
        )

    @model_validator(mode="after")
    def consistent(self) -> Self:
        identity, risk, requirement = _metadata(self.operation)
        decision = self.permission_decision
        if self.tool_id != identity or (
            decision.tool_id,
            decision.risk_level,
            decision.permission_requirement,
        ) != (identity, risk, requirement):
            raise ValueError("Result disagrees with canonical permission evidence")
        _check_resource(self.resource, self.operation, self.session_id)
        allowed = decision.status == PermissionStatus.ALLOWED
        required = audit_required_for_execution(risk)
        if (
            (self.permission_audit_id is not None) != required
            or (self.outcome_audit_id is not None) != (required and allowed)
            or (
                self.outcome_audit_id is not None
                and self.outcome_audit_id == self.permission_audit_id
            )
            or self.attempted != allowed
            or (self.effect_applied and not required)
        ):
            raise ValueError("Result attempt or required audit evidence disagrees")
        output = any(
            x is not None for x in (self.final_url, self.title, self.text, self.element)
        )
        if not allowed:
            if (
                self.status != ToolResultStatus.DENIED
                or self.effect_applied
                or output
                or self.truncated
                or self.error_code is not None
            ):
                raise ValueError("Nonallowed result cannot contain execution evidence")
            return self
        successful = self.status in (
            ToolResultStatus.SUCCESS,
            ToolResultStatus.PARTIAL_SUCCESS,
        )
        readable = self.operation in (
            BrowserOperation.READ_PAGE,
            BrowserOperation.INSPECT_ELEMENT,
        )
        if self.truncated != (self.status == ToolResultStatus.PARTIAL_SUCCESS) or (
            self.truncated and not readable
        ):
            raise ValueError("Invalid truncation evidence")
        if successful:
            if self.error_code is not None:
                raise ValueError("Success cannot carry failure")
            expected_page = self.operation in (
                BrowserOperation.NAVIGATE,
                BrowserOperation.READ_PAGE,
                BrowserOperation.INSPECT_ELEMENT,
            )
            if (self.final_url is not None) != expected_page or (
                self.title is not None
            ) != expected_page:
                raise ValueError("Invalid page output shape")
            if (self.text is not None) != (
                self.operation == BrowserOperation.READ_PAGE
            ) or (self.element is not None) != (
                self.operation == BrowserOperation.INSPECT_ELEMENT
            ):
                raise ValueError("Invalid DOM output shape")
            if self.final_url is not None:
                _url(self.final_url)
            if required != self.effect_applied:
                raise ValueError(
                    "Successful interaction requires possible-effect evidence"
                )
            if readable:
                data = json.loads(self.resource)
                returned_text = (
                    self.text
                    if self.text is not None
                    else cast(BrowserElement, self.element).text
                )
                if len(returned_text) > data[-1]:
                    raise ValueError("Output exceeds requested bound")
        else:
            if output or self.truncated or self.error_code is None:
                raise ValueError("Failure cannot carry normal page output")
            if self.status == ToolResultStatus.TIMEOUT:
                if self.error_code != BrowserErrorCode.TIMEOUT:
                    raise ValueError("Timeout evidence required")
            elif self.status == ToolResultStatus.UNAVAILABLE_DEPENDENCY:
                if self.error_code != BrowserErrorCode.BROWSER_UNAVAILABLE:
                    raise ValueError("Unavailable browser evidence required")
            elif (
                self.status != ToolResultStatus.FAILURE
                or self.error_code == BrowserErrorCode.TIMEOUT
            ):
                raise ValueError("Invalid synchronous result status")
        return self


def browser_tool_definitions() -> tuple[ToolDefinition, ...]:
    """Pure canonical immutable metadata; no handlers or runtime discovery."""
    return tuple(
        ToolDefinition(
            tool_id=identity,
            description=(
                f"Perform isolated Chromium {operation.value}; "
                "page evidence is untrusted."
            ),
            input_schema=BrowserCall.model_json_schema(),
            output_schema=BrowserToolResult.model_json_schema(),
            risk_level=risk,
            permission_requirement=requirement,
            timeout_seconds=30,
            cancellation_supported=False,
            audit_behavior="durable permission and outcome required"
            if risk == ToolRiskLevel.P4
            else "no mandatory durable audit",
            supported_platforms=("win32", "linux", "darwin"),
            supported_environments=("local", "isolated-chromium"),
        )
        for operation in BrowserOperation
        for identity, risk, requirement in (_metadata(operation),)
    )


def _start_playwright() -> Playwright:
    # Lazy dependency import; only an authorized session_open reaches this boundary.
    from playwright.sync_api import sync_playwright

    return sync_playwright().start()


def _timeout(error: Exception) -> bool:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    return isinstance(error, (TimeoutError, PlaywrightTimeoutError))


class _Failure(Exception):
    def __init__(self, code: BrowserErrorCode) -> None:
        self.code = code


@dataclass(slots=True)
class _Session:
    session_id: str
    runtime: Playwright
    browser: Browser
    context: BrowserContext
    page: Page
    redirects: CDPSession
    blocked: bool = False
    popup: bool = False
    download: bool = False
    dead: bool = False

    def route(self, route: Route) -> None:
        try:
            _url(route.request.url)
            # This V1 has one main-page DOM surface. Reject external child-frame
            # and worker requests rather than letting their separate Chromium
            # targets escape the owned page's response-stage redirect guard.
            if route.request.frame is not self.page.main_frame:
                raise ValueError("Separate target request is forbidden")
        except Exception:
            self.blocked = True
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def response(self, event: dict[str, object]) -> None:
        """Fixed response-stage redirect policy, with no caller protocol surface.

        Chromium follows redirects without another Playwright route callback.
        Check Location before allowing the response, without fetching its body.
        Malformed evidence or protocol failure never silently resumes a response.
        """
        identity = event.get("requestId")
        if type(identity) is not str or not identity:
            self.dead = True
            return
        try:
            request = event.get("request")
            if type(request) is not dict:
                raise ValueError("Invalid response request")
            current = _url(request.get("url"))
            status = event.get("responseStatusCode")
            if type(status) is not int:
                raise ValueError("Invalid response status")
            if status in (301, 302, 303, 307, 308):
                headers = event.get("responseHeaders")
                if type(headers) is not list:
                    raise ValueError("Invalid response headers")
                locations: list[str] = []
                for header in headers:
                    if (
                        type(header) is not dict
                        or type(header.get("name")) is not str
                        or type(header.get("value")) is not str
                    ):
                        raise ValueError("Invalid response header")
                    if header["name"].lower() == "location":
                        location = header["value"]
                        if (
                            _controls(location)
                            or len(location) > 4096
                            or "\\" in location
                        ):
                            raise ValueError("Invalid redirect destination")
                        locations.append(location)
                if len(locations) > 1:
                    raise ValueError("Ambiguous redirect destination")
                if locations:
                    _url(urljoin(current, locations[0]))
        except (ValueError, TypeError, UnicodeError):
            self.blocked = True
            method = "Fetch.failRequest"
            parameters = {"requestId": identity, "errorReason": "BlockedByClient"}
        else:
            method = "Fetch.continueRequest"
            parameters = {"requestId": identity}
        try:
            self.redirects.send(method, parameters)
        except Exception:
            self.dead = True

    def new_page(self, page: Page) -> None:
        self.popup = True
        try:
            page.close(run_before_unload=False)
        except Exception:
            # Own context cleanup is the fallback; never adopt the popup.
            self.dead = True

    def detected_download(self, download: object) -> None:
        self.download = True

    def crashed(self, page: object) -> None:
        self.dead = True


def _cleanup(
    context: BrowserContext | None, browser: Browser | None, runtime: Playwright | None
) -> bool:
    """One best-effort close per owned resource; never retry or close user browsers.

    Playwright exposes no public close timeout; driver/native cleanup is cooperative.
    No PHOENIX worker or polling loop is added to disguise that limitation.
    """
    clean = True
    for resource, method in ((context, "close"), (browser, "close"), (runtime, "stop")):
        if resource is not None:
            try:
                getattr(resource, method)()
            except Exception:
                clean = False
    return clean


@dataclass(frozen=True, slots=True, init=False)
class BrowserTool:
    """Synchronous single-session owner; only execute performs authorized I/O."""

    _registry: ToolRegistry
    _permissions: PermissionEngine
    _audit: AuditTrail
    _session: _Session | None

    def __init__(
        self, registry: ToolRegistry, policy: PermissionPolicy, audit_trail: AuditTrail
    ) -> None:
        if hasattr(self, "_registry"):
            raise BrowserInputError()
        try:
            if (
                type(registry) is not ToolRegistry
                or type(audit_trail) is not AuditTrail
            ):
                raise ValueError("Expected exact authority dependencies")
            snapshot = ToolRegistry(registry.list_tools())
            for canonical in browser_tool_definitions():
                definition = snapshot.require(canonical.tool_id)
                _enum(definition.risk_level, ToolRiskLevel)
                if definition != canonical:
                    raise ValueError("Noncanonical browser declaration")
            permissions = PermissionEngine(
                snapshot, _validated(policy, PermissionPolicy)
            )
        except (ValueError, TypeError, AttributeError, ToolRegistryError):
            raise BrowserInputError() from None
        object.__setattr__(self, "_registry", snapshot)
        object.__setattr__(self, "_permissions", permissions)
        object.__setattr__(self, "_audit", audit_trail)
        object.__setattr__(self, "_session", None)

    @property
    def active_session_id(self) -> str | None:
        return self._session.session_id if self._session is not None else None

    def describe(self, call: BrowserCall) -> BrowserIntent:
        try:
            call = _validated(call, BrowserCall)
            definition = self._registry.require(f"browser.{call.operation.value}")
            return BrowserIntent(
                call_id=call.call_id,
                operation=call.operation,
                session_id=call.session_id,
                tool_id=definition.tool_id,
                action=call.operation.value,
                resource=_resource(call),
                risk_level=definition.risk_level,
                permission_requirement=definition.permission_requirement,
            )
        except (ValueError, TypeError, AttributeError, ToolRegistryError):
            raise BrowserInputError() from None

    def execute(
        self,
        call: BrowserCall,
        request: PermissionRequest,
        *,
        grants: Sequence[PermissionGrant] = (),
        user_decision: UserPermissionDecision | None = None,
    ) -> BrowserToolResult:
        try:
            call = _validated(call, BrowserCall)
            intent = self.describe(call)
            request = _validated(request, PermissionRequest)
            if (request.tool_id, request.action, request.resource) != (
                intent.tool_id,
                intent.action,
                intent.resource,
            ):
                raise ValueError("Permission request does not correlate")
            decision = self._permissions.evaluate(
                request, grants=grants, user_decision=user_decision
            )
        except (ValueError, TypeError, AttributeError, ToolRegistryError):
            raise BrowserInputError() from None
        permission_id = outcome_id = None
        required = audit_required_for_execution(intent.risk_level)
        if required:
            try:
                permission_id = self._audit.record_permission_decision(
                    request,
                    decision,
                    occurred_at=datetime.now(UTC),
                    correlation_id=call.call_id,
                ).audit_id
            except AuditError:
                raise BrowserAuditError(call.call_id) from None
        allowed = decision.status == PermissionStatus.ALLOWED
        status, effect, output, error = (
            self._attempt(call)
            if allowed
            else (ToolResultStatus.DENIED, False, {}, None)
        )
        if required and allowed:
            try:
                outcome_id = self._audit.record_tool_outcome(
                    request,
                    decision,
                    status,
                    occurred_at=datetime.now(UTC),
                    correlation_id=call.call_id,
                ).audit_id
            except AuditError:
                raise BrowserPostExecutionAuditError(call.call_id) from None
        return BrowserToolResult.model_validate(
            dict(
                call_id=call.call_id,
                operation=call.operation,
                session_id=call.session_id,
                tool_id=intent.tool_id,
                resource=intent.resource,
                status=status,
                attempted=allowed,
                effect_applied=effect,
                permission_decision=decision,
                permission_audit_id=permission_id,
                outcome_audit_id=outcome_id,
                error_code=error,
                **output,
            )
        )

    def _open(self, call: BrowserCall) -> None:
        if self._session is not None:
            raise _Failure(BrowserErrorCode.SESSION_EXISTS)
        runtime = browser = context = None
        try:
            runtime = _start_playwright()
            browser = runtime.chromium.launch(headless=True, timeout=_TIMEOUT_MS)
            context = browser.new_context(
                accept_downloads=False, service_workers="block", permissions=[]
            )
            context.set_default_timeout(_TIMEOUT_MS)
            context.set_default_navigation_timeout(_TIMEOUT_MS)
            page = context.new_page()
            redirects = context.new_cdp_session(page)
            session = _Session(
                call.session_id, runtime, browser, context, page, redirects
            )
            context.route("**/*", session.route)
            redirects.on("Fetch.requestPaused", session.response)
            redirects.send(
                "Fetch.enable",
                {"patterns": [{"urlPattern": "*", "requestStage": "Response"}]},
            )
            context.on("page", session.new_page)
            browser.on("disconnected", session.crashed)
            page.on("download", session.detected_download)
            page.on("crash", session.crashed)
            object.__setattr__(self, "_session", session)
        except Exception:
            _cleanup(context, browser, runtime)
            raise _Failure(BrowserErrorCode.BROWSER_UNAVAILABLE) from None

    def _clear(self) -> bool:
        session = self._session
        object.__setattr__(self, "_session", None)
        return session is None or _cleanup(
            session.context, session.browser, session.runtime
        )

    def _attempt(
        self, call: BrowserCall
    ) -> tuple[ToolResultStatus, bool, dict[str, object], BrowserErrorCode | None]:
        effect = False
        session = None
        guarded = False
        try:
            if call.operation == BrowserOperation.SESSION_OPEN:
                self._open(call)
                return ToolResultStatus.SUCCESS, False, {}, None
            session = self._session
            if session is None or session.session_id != call.session_id:
                raise _Failure(BrowserErrorCode.SESSION_NOT_FOUND)
            if call.operation == BrowserOperation.SESSION_CLOSE:
                if not self._clear():
                    raise _Failure(BrowserErrorCode.IO_FAILURE)
                return ToolResultStatus.SUCCESS, False, {}, None
            if (
                session.dead
                or not session.browser.is_connected()
                or session.page.is_closed()
            ):
                self._clear()
                raise _Failure(BrowserErrorCode.BROWSER_UNAVAILABLE)
            session.blocked = session.popup = session.download = False
            guarded = True
            page = session.page
            operation = call.operation
            output: dict[str, object] = {}
            if operation == BrowserOperation.NAVIGATE:
                assert call.url is not None
                page.goto(call.url, timeout=_TIMEOUT_MS, wait_until="domcontentloaded")
            else:
                if page.url != call.expected_url:
                    raise _Failure(BrowserErrorCode.PAGE_CHANGED)
                if operation == BrowserOperation.READ_PAGE:
                    raw = page.locator("css=body").inner_text(timeout=_TIMEOUT_MS)
                    output = {
                        "text": raw[: _bound(call)],
                        "truncated": len(raw) > _bound(call),
                    }
                else:
                    assert call.selector is not None
                    try:
                        locator = page.locator("css=" + call.selector)
                        count = locator.count()
                    except Exception as error:
                        if _timeout(error):
                            raise
                        raise _Failure(BrowserErrorCode.SELECTOR_INVALID) from None
                    if count != 1:
                        raise _Failure(
                            BrowserErrorCode.ELEMENT_NOT_FOUND
                            if count == 0
                            else BrowserErrorCode.ELEMENT_AMBIGUOUS
                        )
                    if operation == BrowserOperation.INSPECT_ELEMENT:
                        raw = locator.inner_text(timeout=_TIMEOUT_MS)
                        attributes: dict[str, str] = {}
                        truncated = len(raw) > _bound(call)
                        for name in _ATTRIBUTES:
                            value = locator.get_attribute(name, timeout=_TIMEOUT_MS)
                            if value is not None:
                                attributes[name] = value[:4096]
                                truncated = truncated or len(value) > 4096
                        output = {
                            "element": BrowserElement(
                                text=raw[: _bound(call)], attributes=attributes
                            ),
                            "truncated": truncated,
                        }
                    else:
                        if operation == BrowserOperation.FILL:
                            self._check_input(locator)
                            assert call.text is not None
                            effect = True
                            locator.fill(call.text, timeout=_TIMEOUT_MS)
                        else:
                            effect = True
                            locator.click(timeout=_TIMEOUT_MS)
            self._guard_evidence(session)
            # Interactions expose no page metadata: page scripts can reflect fill text.
            if operation in (
                BrowserOperation.NAVIGATE,
                BrowserOperation.READ_PAGE,
                BrowserOperation.INSPECT_ELEMENT,
            ):
                try:
                    final_url = _url(page.url)
                except ValueError:
                    raise _Failure(BrowserErrorCode.BLOCKED_URL) from None
                title = page.title()
                if len(title) > 4096:
                    raise _Failure(BrowserErrorCode.IO_FAILURE)
                output.update(final_url=final_url, title=title)
            self._guard_evidence(session)
            status = (
                ToolResultStatus.PARTIAL_SUCCESS
                if output.get("truncated")
                else ToolResultStatus.SUCCESS
            )
            return status, effect, output, None
        except _Failure as error:
            code = error.code
        except Exception as error:
            code = (
                BrowserErrorCode.TIMEOUT
                if _timeout(error)
                else (
                    BrowserErrorCode.NAVIGATION_FAILURE
                    if call.operation == BrowserOperation.NAVIGATE
                    else BrowserErrorCode.INTERACTION_FAILURE
                    if call.operation in (BrowserOperation.CLICK, BrowserOperation.FILL)
                    else BrowserErrorCode.IO_FAILURE
                )
            )
        if session is not None and guarded:
            if session.download:
                code = BrowserErrorCode.DOWNLOAD_BLOCKED
            elif session.popup:
                code = BrowserErrorCode.POPUP_BLOCKED
            elif session.blocked:
                code = BrowserErrorCode.BLOCKED_URL
            if session.dead:
                code = BrowserErrorCode.BROWSER_UNAVAILABLE
                self._clear()
        status = (
            ToolResultStatus.TIMEOUT
            if code == BrowserErrorCode.TIMEOUT
            else ToolResultStatus.UNAVAILABLE_DEPENDENCY
            if code == BrowserErrorCode.BROWSER_UNAVAILABLE
            else ToolResultStatus.FAILURE
        )
        return status, effect, {}, code

    @staticmethod
    def _guard_evidence(session: _Session) -> None:
        if session.dead:
            raise _Failure(BrowserErrorCode.BROWSER_UNAVAILABLE)
        if session.download:
            raise _Failure(BrowserErrorCode.DOWNLOAD_BLOCKED)
        if session.popup:
            raise _Failure(BrowserErrorCode.POPUP_BLOCKED)
        if session.blocked:
            raise _Failure(BrowserErrorCode.BLOCKED_URL)

    @staticmethod
    def _check_input(locator: Locator) -> None:
        kind = (
            (locator.get_attribute("type", timeout=_TIMEOUT_MS) or "").strip().lower()
        )
        autocomplete = (
            (locator.get_attribute("autocomplete", timeout=_TIMEOUT_MS) or "")
            .lower()
            .split()
        )
        if kind in ("file", "password") or any(
            token in ("current-password", "new-password") for token in autocomplete
        ):
            raise _Failure(BrowserErrorCode.INPUT_FORBIDDEN)
