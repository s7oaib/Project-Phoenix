"""Explicit accessibility operations on existing windows; UI evidence is untrusted.

Native COM calls are synchronous and cannot be safely preempted. HWND/process/class
checks reduce accidental reuse, without atomic protection against concurrent changes.
UI effects and durable audit commits are separate transactions. No physical input,
process launch, action retry or automatic orchestration is provided.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from itertools import islice
from typing import Annotated, Any, Protocol, Self, TypeVar, cast
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    StringConstraints,
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

__all__ = (
    "WindowsUIOperation",
    "WindowsUIErrorCode",
    "WindowsControlSelector",
    "WindowsUICall",
    "WindowsUIIntent",
    "WindowsWindowEvidence",
    "WindowsControlEvidence",
    "WindowsUIToolResult",
    "WindowsUIInputError",
    "WindowsUIAuditError",
    "WindowsUIPostExecutionAuditError",
    "windows_ui_tool_definitions",
    "WindowsUITool",
)

_MAX_DESCENDANTS = 500
_Id = Annotated[
    str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)
]
_Positive = Annotated[int, Field(strict=True, gt=0)]
_Short = Annotated[str, StringConstraints(strict=True, max_length=256)]
_Text = Annotated[str, StringConstraints(strict=True, max_length=8192)]
_Model = TypeVar("_Model", bound=BaseModel)


class WindowsUIOperation(StrEnum):
    LIST_WINDOWS = "list_windows"
    INSPECT_WINDOW = "inspect_window"
    FOCUS_WINDOW = "focus_window"
    INSPECT_CONTROL = "inspect_control"
    INVOKE_CONTROL = "invoke_control"
    SET_TEXT = "set_text"
    CLOSE_WINDOW = "close_window"


class WindowsUIErrorCode(StrEnum):
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    UI_UNAVAILABLE = "ui_unavailable"
    WINDOW_NOT_FOUND = "window_not_found"
    WINDOW_CHANGED = "window_changed"
    CONTROL_NOT_FOUND = "control_not_found"
    CONTROL_AMBIGUOUS = "control_ambiguous"
    CONTROL_UNSUPPORTED = "control_unsupported"
    INPUT_FORBIDDEN = "input_forbidden"
    LIMIT_EXCEEDED = "limit_exceeded"
    INTERACTION_FAILURE = "interaction_failure"
    IO_FAILURE = "io_failure"
    TIMEOUT = "timeout"


class WindowsUIInputError(ValueError):
    def __init__(self) -> None:
        super().__init__("Invalid Windows UI input or authority")


class WindowsUIAuditError(Exception):
    def __init__(self, call_id: str) -> None:
        super().__init__("Required Windows UI permission audit failed")
        self.call_id = call_id
        self.effect_may_have_occurred = False


class WindowsUIPostExecutionAuditError(WindowsUIAuditError):
    def __init__(self, call_id: str) -> None:
        Exception.__init__(self, "Windows UI outcome audit failed after attempt")
        self.call_id = call_id
        self.effect_may_have_occurred = True


def _enum(value: object, vocabulary: type[StrEnum]) -> object:
    if type(value) is not str and not any(value is member for member in vocabulary):
        raise ValueError("Expected exact vocabulary text or member")
    return value


def _stored(value: BaseModel, contract: type[BaseModel]) -> dict[str, object]:
    if type(value) is not contract:
        raise ValueError("Expected exact contract")
    extra = value.__pydantic_extra__
    if extra is not None and type(extra) is not dict:
        raise ValueError("Invalid stored extras")
    return {**vars(value), **(extra or {})}


def _validated(value: object, contract: type[_Model]) -> _Model:
    if not isinstance(value, BaseModel):
        raise ValueError("Expected exact contract")
    return contract.model_validate(_stored(value, contract))


def _controls(value: str) -> bool:
    return any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)


def _identifier(value: object) -> str:
    if type(value) is not str or _controls(value):
        raise ValueError("Expected ordinary control-free text")
    result = value.strip()
    if not result:
        raise ValueError("Expected nonempty text")
    try:
        result.encode("utf-8")
    except UnicodeError:
        raise ValueError("Expected valid Unicode text") from None
    return result


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
        return _enum(value, WindowsUIOperation)

    @field_validator("call_id", mode="before", check_fields=False)
    @classmethod
    def call_identifier(cls, value: object) -> str:
        return _identifier(value)

    @field_validator(
        "class_name",
        "title",
        "automation_id",
        "control_type",
        "name",
        "value",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def ordinary_evidence(cls, value: object) -> object:
        if value is not None:
            if type(value) is not str:
                raise ValueError("Expected ordinary evidence text")
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise ValueError("Expected valid Unicode evidence") from None
        return value


class WindowsControlSelector(_Contract):
    automation_id: _Short | None = None
    control_type: _Short | None = None
    name: _Short | None = None

    @field_validator("automation_id", "control_type", "name", mode="before")
    @classmethod
    def exact_text(cls, value: object) -> str | None:
        if value is None:
            return None
        result = _identifier(value)
        if any(c in result for c in "*?") or result.startswith(("/", "xpath=")):
            raise ValueError("Only exact selector text is permitted")
        return result

    @model_validator(mode="after")
    def specific(self) -> Self:
        if self.automation_id is None and self.name is None:
            raise ValueError("Selector requires an automation ID or name")
        return self


class WindowsUICall(_Contract):
    call_id: _Id = Field(default_factory=lambda: str(uuid4()))
    operation: WindowsUIOperation
    hwnd: _Positive | None = None
    expected_process_id: _Positive | None = None
    expected_class_name: _Short | None = None
    selector: WindowsControlSelector | None = None
    text: Annotated[str, StringConstraints(strict=True)] | None = Field(
        default=None, repr=False
    )
    max_items: Annotated[int, Field(strict=True, ge=1, le=200)] | None = None
    max_chars: Annotated[int, Field(strict=True, ge=1, le=8192)] | None = None

    @field_validator("expected_class_name", mode="before")
    @classmethod
    def class_name(cls, value: object) -> str | None:
        return _identifier(value) if value is not None else None

    @field_validator("selector", mode="before")
    @classmethod
    def selector_snapshot(cls, value: object) -> WindowsControlSelector | None:
        if value is None:
            return None
        return (
            _validated(value, WindowsControlSelector)
            if isinstance(value, BaseModel)
            else WindowsControlSelector.model_validate(value)
        )

    @field_validator("text", mode="before")
    @classmethod
    def ordinary_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if type(value) is not str:
            raise ValueError("Expected explicit ordinary text")
        try:
            if len(value.encode("utf-8")) > 65536:
                raise ValueError("Text exceeds byte limit")
        except UnicodeError:
            raise ValueError("Expected valid Unicode text") from None
        return value

    @model_validator(mode="after")
    def shape(self) -> Self:
        required = {
            "list_windows": (False, False, False, False, False),
            "inspect_window": (True, False, False, False, False),
            "focus_window": (True, True, True, False, False),
            "inspect_control": (True, True, True, True, False),
            "invoke_control": (True, True, True, True, False),
            "set_text": (True, True, True, True, True),
            "close_window": (True, True, True, False, False),
        }[self.operation]
        if (
            tuple(
                x is not None
                for x in (
                    self.hwnd,
                    self.expected_process_id,
                    self.expected_class_name,
                    self.selector,
                    self.text,
                )
            )
            != required
            or (self.max_items is not None and self.operation != "list_windows")
            or (
                self.max_chars is not None
                and self.operation
                not in (
                    "inspect_window",
                    "inspect_control",
                )
            )
        ):
            raise ValueError("Operation fields disagree")
        return self


_RISKS = (
    ToolRiskLevel.P1,
    ToolRiskLevel.P1,
    ToolRiskLevel.P2,
    ToolRiskLevel.P1,
    ToolRiskLevel.P4,
    ToolRiskLevel.P4,
    ToolRiskLevel.P4,
)


def _metadata(operation: WindowsUIOperation) -> tuple[str, ToolRiskLevel, str]:
    risk = _RISKS[tuple(WindowsUIOperation).index(operation)]
    return (
        f"windows.{operation.value}",
        risk,
        f"{risk.value}: exact Windows UI operation",
    )


def _resource(call: WindowsUICall) -> str:
    data: dict[str, object] = {"operation": call.operation.value}
    for key in ("hwnd", "expected_process_id", "expected_class_name"):
        value = getattr(call, key)
        if value is not None:
            data[key] = value
    if call.selector is not None:
        data["selector"] = call.selector.model_dump()
    if call.operation == "list_windows":
        data["max_items"] = call.max_items or 100
    if call.operation in ("inspect_window", "inspect_control"):
        data["max_chars"] = call.max_chars or 4096
    if call.text is not None:
        data["text_sha256"] = sha256(call.text.encode("utf-8")).hexdigest()
    return json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def _resource_call(resource: str, operation: WindowsUIOperation) -> WindowsUICall:
    data = json.loads(resource)
    if type(data) is not dict or data.get("operation") != operation.value:
        raise ValueError("Invalid resource")
    digest = data.pop("text_sha256", None)
    if operation == "set_text":
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("Invalid text digest")
        data["text"] = ""
    elif digest is not None:
        raise ValueError("Unexpected text digest")
    call = WindowsUICall.model_validate(data | {"call_id": "resource-validation"})
    canonical = json.loads(_resource(call))
    if digest is not None:
        canonical["text_sha256"] = digest
    if (
        json.dumps(canonical, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        != resource
    ):
        raise ValueError("Noncanonical resource")
    return call


class WindowsUIIntent(_Contract):
    call_id: _Id
    operation: WindowsUIOperation
    tool_id: _Id
    action: _Id
    resource: _Id
    risk_level: ToolRiskLevel
    permission_requirement: _Id

    @field_validator("risk_level", mode="before")
    @classmethod
    def risk_member(cls, value: object) -> object:
        return _enum(value, ToolRiskLevel)

    @model_validator(mode="after")
    def canonical(self) -> Self:
        if (self.tool_id, self.risk_level, self.permission_requirement) != _metadata(
            self.operation
        ) or self.action != self.operation.value:
            raise ValueError("Intent disagrees with canonical declaration")
        _resource_call(self.resource, self.operation)
        return self


class WindowsWindowEvidence(_Contract):
    hwnd: _Positive
    process_id: _Positive
    class_name: _Short
    title: _Text
    enabled: bool = Field(strict=True)
    visible: bool = Field(strict=True)


class WindowsControlEvidence(_Contract):
    automation_id: _Short
    control_type: _Short
    name: _Text
    enabled: bool = Field(strict=True)
    visible: bool = Field(strict=True)
    is_password: bool | None = Field(strict=True)
    value: _Text | None = None

    @model_validator(mode="after")
    def no_secret_value(self) -> Self:
        if self.is_password is not False and self.value is not None:
            raise ValueError("Secret or unknown control cannot expose value")
        return self


class WindowsUIToolResult(_Contract):
    call_id: _Id
    operation: WindowsUIOperation
    tool_id: _Id
    resource: _Id
    status: ToolResultStatus
    attempted: bool = Field(strict=True)
    effect_applied: bool = Field(strict=True)
    permission_decision: PermissionDecision
    permission_audit_id: _Id | None = None
    outcome_audit_id: _Id | None = None
    windows: tuple[WindowsWindowEvidence, ...] = ()
    window: WindowsWindowEvidence | None = None
    control: WindowsControlEvidence | None = None
    truncated: bool = Field(default=False, strict=True)
    error_code: WindowsUIErrorCode | None = None

    @field_validator("status", mode="before")
    @classmethod
    def status_member(cls, value: object) -> object:
        return _enum(value, ToolResultStatus)

    @field_validator("error_code", mode="before")
    @classmethod
    def error_member(cls, value: object) -> object:
        return _enum(value, WindowsUIErrorCode) if value is not None else None

    @field_validator("permission_decision", mode="before")
    @classmethod
    def decision_snapshot(cls, value: object) -> PermissionDecision:
        return (
            _validated(value, PermissionDecision)
            if isinstance(value, BaseModel)
            else PermissionDecision.model_validate(value)
        )

    @field_validator("window", "control", mode="before")
    @classmethod
    def evidence_snapshot(cls, value: object, info: Any) -> BaseModel | None:
        if value is None:
            return None
        contract = (
            WindowsWindowEvidence
            if info.field_name == "window"
            else WindowsControlEvidence
        )
        return (
            _validated(value, contract)
            if isinstance(value, BaseModel)
            else contract.model_validate(value)
        )

    @field_validator("windows", mode="before")
    @classmethod
    def windows_snapshot(cls, value: object) -> tuple[WindowsWindowEvidence, ...]:
        if type(value) not in (tuple, list):
            raise ValueError("Expected ordinary window sequence")
        return tuple(
            _validated(x, WindowsWindowEvidence)
            if isinstance(x, BaseModel)
            else WindowsWindowEvidence.model_validate(x)
            for x in cast(Sequence[object], value)
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
            raise ValueError("Result authority evidence disagrees")
        call = _resource_call(self.resource, self.operation)
        allowed = decision.status == PermissionStatus.ALLOWED
        required = audit_required_for_execution(risk)
        action = self.operation in (
            "focus_window",
            "invoke_control",
            "set_text",
            "close_window",
        )
        if (
            self.attempted != allowed
            or (self.permission_audit_id is not None) != required
            or (self.outcome_audit_id is not None) != (required and allowed)
            or (
                self.outcome_audit_id is not None
                and self.outcome_audit_id == self.permission_audit_id
            )
            or (self.effect_applied and (not allowed or not action))
        ):
            raise ValueError("Invalid attempt or audit evidence")
        output = (
            bool(self.windows) or self.window is not None or self.control is not None
        )
        if not allowed:
            if (
                self.status != ToolResultStatus.DENIED
                or output
                or self.effect_applied
                or self.truncated
                or self.error_code is not None
            ):
                raise ValueError("Denied result cannot contain execution evidence")
            return self
        successful = self.status in (
            ToolResultStatus.SUCCESS,
            ToolResultStatus.PARTIAL_SUCCESS,
        )
        if self.truncated != (self.status == ToolResultStatus.PARTIAL_SUCCESS) or (
            self.truncated and action
        ):
            raise ValueError("Invalid truncation evidence")
        if not successful:
            expected_status = (
                ToolResultStatus.UNAVAILABLE_DEPENDENCY
                if self.error_code
                in (
                    WindowsUIErrorCode.UNSUPPORTED_PLATFORM,
                    WindowsUIErrorCode.UI_UNAVAILABLE,
                )
                else ToolResultStatus.TIMEOUT
                if self.error_code == WindowsUIErrorCode.TIMEOUT
                else ToolResultStatus.FAILURE
            )
            if (
                output
                or self.truncated
                or self.error_code is None
                or self.status != expected_status
            ):
                raise ValueError("Invalid failure evidence")
            return self
        if self.error_code is not None or self.effect_applied != action:
            raise ValueError("Invalid successful effect evidence")
        if (
            (self.window is not None) != (self.operation == "inspect_window")
            or (self.control is not None) != (self.operation == "inspect_control")
            or (self.windows and self.operation != "list_windows")
        ):
            raise ValueError("Invalid output shape")
        if self.operation == "list_windows":
            if len(self.windows) > (call.max_items or 100) or tuple(
                x.hwnd for x in self.windows
            ) != tuple(sorted({x.hwnd for x in self.windows})):
                raise ValueError("Invalid list order or bound")
            if any(len(x.title) > 4096 for x in self.windows):
                raise ValueError("Invalid list title bound")
        if self.window is not None and (
            self.window.hwnd != call.hwnd
            or len(self.window.title) > (call.max_chars or 4096)
        ):
            raise ValueError("Invalid inspected window")
        if self.control is not None and (
            len(self.control.name) > (call.max_chars or 4096)
            or (
                self.control.value is not None
                and len(self.control.value) > (call.max_chars or 4096)
            )
        ):
            raise ValueError("Invalid control text bound")
        if self.control is not None and call.selector is not None:
            for field in ("automation_id", "control_type", "name"):
                expected = getattr(call.selector, field)
                bound = (call.max_chars or 4096) if field == "name" else 256
                if (
                    expected is not None
                    and getattr(self.control, field) != expected[:bound]
                ):
                    raise ValueError("Control evidence disagrees with selector")
        return self


def windows_ui_tool_definitions() -> tuple[ToolDefinition, ...]:
    return tuple(
        ToolDefinition(
            tool_id=identity,
            description=f"Explicit {operation.value} via UIA; evidence is untrusted",
            input_schema=WindowsUICall.model_json_schema(),
            output_schema=WindowsUIToolResult.model_json_schema(),
            risk_level=risk,
            permission_requirement=requirement,
            timeout_seconds=10,
            cancellation_supported=False,
            audit_behavior="durable permission and outcome required"
            if risk == ToolRiskLevel.P4
            else "no mandatory durable audit",
            supported_platforms=("win32",),
            supported_environments=("local", "windows-uia"),
        )
        for operation in WindowsUIOperation
        for identity, risk, requirement in (_metadata(operation),)
    )


class _Failure(Exception):
    def __init__(self, code: WindowsUIErrorCode) -> None:
        self.code = code


class _Node(Protocol):
    def identity(self) -> tuple[int, int, str]: ...
    def window_data(self) -> tuple[str, bool, bool]: ...
    def descendants(self) -> Iterable[_Node]: ...
    def selector_data(self) -> tuple[str, str, str]: ...
    def control_state(self) -> tuple[bool, bool, bool | None]: ...
    def ordinary_value(self) -> str | None: ...
    def prepare(
        self, operation: WindowsUIOperation, text: str | None
    ) -> Callable[[], None]: ...


class _Backend(Protocol):
    def windows(self) -> Iterable[_Node]: ...
    def window(self, hwnd: int) -> _Node: ...


def _native_bool(value: object) -> bool | None:
    # UIA COM BOOLs may marshal as Python ints rather than bools. Never apply
    # truthiness to strings, wrappers or other unknown evidence.
    if type(value) is bool:
        return value
    if type(value) is int:
        return value != 0
    return None


class _NativeNode:
    def __init__(
        self,
        wrapper: Any,
        wrapper_type: Any,
        no_pattern: type[Exception],
        info_type: Any,
        uia: Any,
    ) -> None:
        self._wrapper = wrapper
        self._wrapper_type = wrapper_type
        self._no_pattern = no_pattern
        self._info_type = info_type
        self._uia = uia

    def identity(self) -> tuple[int, int, str]:
        element = self._wrapper.element_info.element
        class_name = element.CurrentClassName
        return (
            element.CurrentNativeWindowHandle,
            element.CurrentProcessId,
            ("" if class_name is None else class_name),
        )

    def window_data(self) -> tuple[str, bool, bool]:
        element = self._wrapper.element_info.element
        name = element.CurrentName
        enabled, visible = _native_state(element)
        return "" if name is None else name, enabled, visible

    def descendants(self) -> Iterable[_Node]:
        # FindAll may materialize the native tree before returning. The public
        # boundary admits at most 500 descendants, never actions after overflow.
        return _native_nodes(
            self._wrapper.element_info,
            self._uia.tree_scope["descendants"],
            self._uia,
            self._info_type,
            self._wrapper_type,
            self._no_pattern,
        )

    def selector_data(self) -> tuple[str, str, str]:
        element = self._wrapper.element_info.element
        auto_id, name = element.CurrentAutomationId, element.CurrentName
        return (
            "" if auto_id is None else auto_id,
            self._uia.known_control_type_ids[element.CurrentControlType],
            "" if name is None else name,
        )

    def control_state(self) -> tuple[bool, bool, bool | None]:
        info = self._wrapper.element_info
        try:
            password = _native_bool(info.element.CurrentIsPassword)
        except Exception:
            password = None
        enabled, visible = _native_state(info.element)
        return enabled, visible, password

    def ordinary_value(self) -> str | None:
        try:
            value = self._wrapper.iface_value.CurrentValue
        except self._no_pattern:
            return None
        if type(value) is not str:
            raise _Failure(WindowsUIErrorCode.IO_FAILURE)
        return value

    def prepare(
        self, operation: WindowsUIOperation, text: str | None
    ) -> Callable[[], None]:
        try:
            if operation == "focus_window":
                method = self._wrapper.element_info.element.SetFocus
                if not callable(method):
                    raise _Failure(WindowsUIErrorCode.CONTROL_UNSUPPORTED)
                return lambda: method()
            if operation == "invoke_control":
                method = self._wrapper.iface_invoke.Invoke
                if not callable(method):
                    raise _Failure(WindowsUIErrorCode.CONTROL_UNSUPPORTED)
                return lambda: method()
            if operation == "set_text":
                pattern = self._wrapper.iface_value
                if _native_bool(pattern.CurrentIsReadOnly) is not False:
                    raise _Failure(WindowsUIErrorCode.CONTROL_UNSUPPORTED)
                method = pattern.SetValue
                if not callable(method):
                    raise _Failure(WindowsUIErrorCode.CONTROL_UNSUPPORTED)
                return lambda: method(text)
            if operation == "close_window":
                method = self._wrapper.iface_window.Close
                if not callable(method):
                    raise _Failure(WindowsUIErrorCode.CONTROL_UNSUPPORTED)
                return lambda: method()
        except self._no_pattern:
            raise _Failure(WindowsUIErrorCode.CONTROL_UNSUPPORTED) from None
        raise _Failure(WindowsUIErrorCode.CONTROL_UNSUPPORTED)


def _native_state(element: Any) -> tuple[bool, bool]:
    enabled = _native_bool(element.CurrentIsEnabled)
    offscreen = _native_bool(element.CurrentIsOffscreen)
    if enabled is None or offscreen is None:
        raise _Failure(WindowsUIErrorCode.IO_FAILURE)
    return enabled, not offscreen


def _native_nodes(
    info: Any,
    scope: int,
    uia: Any,
    info_type: Any,
    wrapper_type: Any,
    no_pattern: type[Exception],
) -> Iterable[_Node]:
    # Bypass pywinauto's _get_elements: it logs and returns [] on COM failure.
    # Fixed raw FindAll leaves errors visible to the safe public boundary.
    array = info.element.FindAll(scope, uia.true_condition)
    length = array.Length
    if type(length) is not int or length < 0:
        raise _Failure(WindowsUIErrorCode.IO_FAILURE)
    return (
        _NativeNode(
            wrapper_type(info_type(array.GetElement(index))),
            wrapper_type,
            no_pattern,
            info_type,
            uia,
        )
        for index in range(length)
    )


class _NativeBackend:
    def __init__(self) -> None:
        try:
            api = importlib.import_module("pywinauto")
            info = importlib.import_module("pywinauto.uia_element_info")
            wrappers = importlib.import_module("pywinauto.controls.uiawrapper")
            definitions = importlib.import_module("pywinauto.uia_defines")
            self._desktop = api.Desktop(backend="uia", allow_magic_lookup=False)
            self._info = info.UIAElementInfo
            self._wrapper = wrappers.UIAWrapper
            self._no_pattern = definitions.NoPatternInterfaceError
            self._uia = definitions.IUIA()
        except Exception:
            raise _Failure(WindowsUIErrorCode.UI_UNAVAILABLE) from None

    def windows(self) -> Iterable[_Node]:
        return _native_nodes(
            self._desktop.backend.element_info_class(),
            self._uia.tree_scope["children"],
            self._uia,
            self._info,
            self._wrapper,
            self._no_pattern,
        )

    def window(self, hwnd: int) -> _Node:
        # Direct ElementFromHandle, without WindowSpecification polling/retry.
        try:
            return _NativeNode(
                self._wrapper(self._info(hwnd)),
                self._wrapper,
                self._no_pattern,
                self._info,
                self._uia,
            )
        except Exception:
            raise _Failure(WindowsUIErrorCode.WINDOW_NOT_FOUND) from None


def _load_backend() -> _Backend:
    if sys.platform != "win32":
        raise _Failure(WindowsUIErrorCode.UNSUPPORTED_PLATFORM)
    return _NativeBackend()


def _window_evidence(node: _Node, bound: int) -> tuple[WindowsWindowEvidence, bool]:
    hwnd, pid, class_name = node.identity()
    title, enabled, visible = node.window_data()
    # Validate native evidence before slicing; never coerce wrappers or objects.
    if type(title) is not str or type(class_name) is not str:
        raise _Failure(WindowsUIErrorCode.IO_FAILURE)
    return WindowsWindowEvidence(
        hwnd=hwnd,
        process_id=pid,
        class_name=class_name[:256],
        title=title[:bound],
        enabled=enabled,
        visible=visible,
    ), (len(title) > bound or len(class_name) > 256)


@dataclass(frozen=True, slots=True, init=False)
class WindowsUITool:
    _registry: ToolRegistry
    _permissions: PermissionEngine
    _audit: AuditTrail

    def __init__(
        self, registry: ToolRegistry, policy: PermissionPolicy, audit_trail: AuditTrail
    ) -> None:
        if hasattr(self, "_registry"):
            raise WindowsUIInputError()
        try:
            if (
                type(registry) is not ToolRegistry
                or type(audit_trail) is not AuditTrail
            ):
                raise ValueError("Expected exact authority dependencies")
            snapshot = ToolRegistry(registry.list_tools())
            for canonical in windows_ui_tool_definitions():
                definition = snapshot.require(canonical.tool_id)
                _enum(definition.risk_level, ToolRiskLevel)
                if definition != canonical:
                    raise ValueError("Noncanonical Windows UI declaration")
            permissions = PermissionEngine(
                snapshot, _validated(policy, PermissionPolicy)
            )
        except (ValueError, TypeError, AttributeError, ToolRegistryError):
            raise WindowsUIInputError() from None
        object.__setattr__(self, "_registry", snapshot)
        object.__setattr__(self, "_permissions", permissions)
        object.__setattr__(self, "_audit", audit_trail)

    def describe(self, call: WindowsUICall) -> WindowsUIIntent:
        try:
            call = _validated(call, WindowsUICall)
            definition = self._registry.require(f"windows.{call.operation.value}")
            return WindowsUIIntent(
                call_id=call.call_id,
                operation=call.operation,
                tool_id=definition.tool_id,
                action=call.operation.value,
                resource=_resource(call),
                risk_level=definition.risk_level,
                permission_requirement=definition.permission_requirement,
            )
        except (ValueError, TypeError, AttributeError, ToolRegistryError):
            raise WindowsUIInputError() from None

    def execute(
        self,
        call: WindowsUICall,
        request: PermissionRequest,
        *,
        grants: Sequence[PermissionGrant] = (),
        user_decision: UserPermissionDecision | None = None,
    ) -> WindowsUIToolResult:
        try:
            call = _validated(call, WindowsUICall)
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
            raise WindowsUIInputError() from None
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
                raise WindowsUIAuditError(call.call_id) from None
        allowed = decision.status == PermissionStatus.ALLOWED
        status, effect, output, error = (
            self._attempt(call)
            if allowed
            else (
                ToolResultStatus.DENIED,
                False,
                {},
                None,
            )
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
                raise WindowsUIPostExecutionAuditError(call.call_id) from None
        return WindowsUIToolResult.model_validate(
            dict(
                call_id=call.call_id,
                operation=call.operation,
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

    def _attempt(
        self, call: WindowsUICall
    ) -> tuple[ToolResultStatus, bool, dict[str, object], WindowsUIErrorCode | None]:
        effect = False
        try:
            backend = _load_backend()
            output: dict[str, object] = {}
            partial = False
            if call.operation == "list_windows":
                # Top-level enumeration only; list titles never become selectors.
                nodes = sorted(backend.windows(), key=lambda node: node.identity()[0])
                identities = [node.identity()[0] for node in nodes]
                if any(
                    type(hwnd) is not int or hwnd <= 0 for hwnd in identities
                ) or len(set(identities)) != len(identities):
                    raise _Failure(WindowsUIErrorCode.IO_FAILURE)
                maximum = call.max_items or 100
                records = []
                partial = len(nodes) > maximum
                for node in nodes[:maximum]:
                    record, cut = _window_evidence(node, 4096)
                    records.append(record)
                    partial |= cut
                output["windows"] = tuple(records)
            else:
                assert call.hwnd is not None
                node = backend.window(call.hwnd)
                hwnd, pid, class_name = node.identity()
                if type(hwnd) is not int or hwnd != call.hwnd:
                    raise _Failure(WindowsUIErrorCode.WINDOW_NOT_FOUND)
                if call.operation != "inspect_window" and (
                    type(pid) is not int
                    or type(class_name) is not str
                    or pid != call.expected_process_id
                    or class_name != call.expected_class_name
                ):
                    raise _Failure(WindowsUIErrorCode.WINDOW_CHANGED)
                if call.operation == "inspect_window":
                    record, partial = _window_evidence(node, call.max_chars or 4096)
                    if record.hwnd != call.hwnd:
                        raise _Failure(WindowsUIErrorCode.WINDOW_NOT_FOUND)
                    output["window"] = record
                else:
                    if call.selector is not None:
                        candidates = tuple(
                            islice(node.descendants(), _MAX_DESCENDANTS + 1)
                        )
                        if len(candidates) > _MAX_DESCENDANTS:
                            raise _Failure(WindowsUIErrorCode.LIMIT_EXCEEDED)
                        matches = []
                        for candidate in candidates:
                            values = candidate.selector_data()
                            if (
                                any(type(value) is not str for value in values)
                                or len(values) != 3
                            ):
                                raise _Failure(WindowsUIErrorCode.IO_FAILURE)
                            if all(
                                expected is None or expected == actual
                                for expected, actual in zip(
                                    (
                                        call.selector.automation_id,
                                        call.selector.control_type,
                                        call.selector.name,
                                    ),
                                    values,
                                    strict=True,
                                )
                            ):
                                matches.append((candidate, values))
                        if not matches:
                            raise _Failure(WindowsUIErrorCode.CONTROL_NOT_FOUND)
                        if len(matches) != 1:
                            raise _Failure(WindowsUIErrorCode.CONTROL_AMBIGUOUS)
                        node, values = matches[0]
                        enabled, visible, password = node.control_state()
                        if (
                            type(enabled) is not bool
                            or type(visible) is not bool
                            or (password is not None and type(password) is not bool)
                        ):
                            raise _Failure(WindowsUIErrorCode.IO_FAILURE)
                        if call.operation == "inspect_control":
                            value = node.ordinary_value() if password is False else None
                            if value is not None and type(value) is not str:
                                raise _Failure(WindowsUIErrorCode.IO_FAILURE)
                            bound = call.max_chars or 4096
                            auto_id, control_type, name = values
                            output["control"] = WindowsControlEvidence(
                                automation_id=auto_id[:256],
                                control_type=control_type[:256],
                                name=name[:bound],
                                enabled=enabled,
                                visible=visible,
                                is_password=password,
                                value=value[:bound] if value is not None else None,
                            )
                            partial = (
                                len(auto_id) > 256
                                or len(control_type) > 256
                                or len(name) > bound
                                or (value is not None and len(value) > bound)
                            )
                        else:
                            if not enabled or not visible:
                                raise _Failure(WindowsUIErrorCode.INPUT_FORBIDDEN)
                            if call.operation == "set_text" and password is not False:
                                raise _Failure(WindowsUIErrorCode.INPUT_FORBIDDEN)
                    if call.operation != "inspect_control":
                        action = node.prepare(call.operation, call.text)
                        # Checks and pattern acquisition precede possible effects.
                        effect = True
                        action()
            output["truncated"] = partial
            return (
                (
                    ToolResultStatus.PARTIAL_SUCCESS
                    if partial
                    else ToolResultStatus.SUCCESS
                ),
                effect,
                output,
                None,
            )
        except _Failure as error:
            code = error.code
        except TimeoutError:
            code = WindowsUIErrorCode.TIMEOUT
        except Exception:
            code = (
                WindowsUIErrorCode.INTERACTION_FAILURE
                if effect
                else WindowsUIErrorCode.IO_FAILURE
            )
        status = (
            ToolResultStatus.UNAVAILABLE_DEPENDENCY
            if code
            in (
                WindowsUIErrorCode.UNSUPPORTED_PLATFORM,
                WindowsUIErrorCode.UI_UNAVAILABLE,
            )
            else ToolResultStatus.TIMEOUT
            if code == WindowsUIErrorCode.TIMEOUT
            else ToolResultStatus.FAILURE
        )
        return status, effect, {}, code
