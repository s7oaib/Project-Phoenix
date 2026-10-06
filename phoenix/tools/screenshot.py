"""Explicit P1 window observation, with no inference or image persistence.

PrintWindow is synchronous/non-preemptible and some windows cannot render it.
Pre/post identity checks narrow handle races without an atomic capture guarantee.
Frames are sensitive untrusted data; possession conveys no permission authority.
"""

from __future__ import annotations

import base64
import binascii
import ctypes
import json
import struct
import sys
import zlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Annotated, Any, Protocol, Self, TypeVar
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
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
    "ScreenshotCall",
    "ScreenshotIntent",
    "ScreenshotFrame",
    "ScreenshotToolResult",
    "ScreenshotErrorCode",
    "ScreenshotInputError",
    "ScreenshotCaptureTool",
    "screenshot_tool_definitions",
)

_MAX_PNG = 8 * 1024 * 1024
_MAX_PIXELS = 8294400
_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_Id = Annotated[str, StringConstraints(strict=True, min_length=1)]
_Class = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=256)]
_Positive = Annotated[int, Field(strict=True, gt=0)]
_Dimension = Annotated[int, Field(strict=True, ge=1, le=4096)]
_Model = TypeVar("_Model", bound=BaseModel)
_REQUIREMENT = "P1: exact scoped window observation"


def _identifier(value: object) -> str:
    if type(value) is not str or any(
        ord(c) < 32 or 127 <= ord(c) <= 159 for c in value
    ):
        raise ValueError("Expected ordinary control-free identifier")
    text = value.strip()
    if not text:
        raise ValueError("Expected nonempty identifier")
    try:
        text.encode("utf-8")
    except UnicodeError:
        raise ValueError("Expected valid Unicode identifier") from None
    return text


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


def _enum(value: object, vocabulary: type[StrEnum]) -> object:
    if type(value) is not str and not any(value is item for item in vocabulary):
        raise ValueError("Expected exact vocabulary text or member")
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

    @field_validator(
        "call_id",
        "frame_id",
        "class_name",
        "expected_class_name",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def identity_text(cls, value: object) -> str:
        return _identifier(value)


class ScreenshotErrorCode(StrEnum):
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    CAPTURE_UNAVAILABLE = "capture_unavailable"
    WINDOW_NOT_FOUND = "window_not_found"
    WINDOW_CHANGED = "window_changed"
    WINDOW_HIDDEN = "window_hidden"
    WINDOW_MINIMIZED = "window_minimized"
    LIMIT_EXCEEDED = "limit_exceeded"
    INVALID_PIXELS = "invalid_pixels"
    ENCODING_FAILURE = "encoding_failure"
    IO_FAILURE = "io_failure"


class ScreenshotInputError(ValueError):
    def __init__(self) -> None:
        super().__init__("Invalid screenshot input or authority")


class ScreenshotCall(_Contract):
    call_id: _Id = Field(default_factory=lambda: str(uuid4()))
    hwnd: _Positive
    expected_process_id: _Positive
    expected_class_name: _Class


def _resource(call: ScreenshotCall) -> str:
    return json.dumps(
        [
            "capture_window",
            call.hwnd,
            call.expected_process_id,
            call.expected_class_name,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _resource_call(resource: str) -> ScreenshotCall:
    data = json.loads(resource)
    if type(data) is not list or len(data) != 4 or data[0] != "capture_window":
        raise ValueError("Invalid screenshot resource")
    call = ScreenshotCall(
        call_id="resource",
        hwnd=data[1],
        expected_process_id=data[2],
        expected_class_name=data[3],
    )
    if _resource(call) != resource:
        raise ValueError("Noncanonical screenshot resource")
    return call


class ScreenshotIntent(_Contract):
    call_id: _Id
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
        if (
            self.tool_id,
            self.action,
            self.risk_level,
            self.permission_requirement,
        ) != (
            "screen.capture_window",
            "capture_window",
            ToolRiskLevel.P1,
            _REQUIREMENT,
        ):
            raise ValueError("Intent disagrees with canonical declaration")
        _resource_call(self.resource)
        return self


def _dimensions(width: object, height: object) -> tuple[int, int]:
    if (
        type(width) is not int
        or type(height) is not int
        or not (
            1 <= width <= 4096 and 1 <= height <= 4096 and width * height <= _MAX_PIXELS
        )
    ):
        raise ValueError("Invalid bounded dimensions")
    return width, height


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", binascii.crc32(kind + data))
    )


def _encode_png(
    width: int, height: int, pixels: bytes | bytearray, top_down: bool
) -> bytes:
    _dimensions(width, height)
    if (
        type(pixels) not in (bytes, bytearray)
        or len(pixels) != width * height * 4
        or type(top_down) is not bool
    ):
        raise ValueError("Invalid exact BGRA pixels")
    rows = bytearray()
    stride = width * 4
    for row in range(height):
        offset = (row if top_down else height - 1 - row) * stride
        rows.append(0)
        for pixel in range(offset, offset + stride, 4):
            rows.extend(
                (pixels[pixel + 2], pixels[pixel + 1], pixels[pixel], pixels[pixel + 3])
            )
    try:
        png = _SIGNATURE + _chunk(
            b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
        )
        png += _chunk(b"IDAT", zlib.compress(rows, level=9)) + _chunk(b"IEND", b"")
    finally:
        rows[:] = b"\0" * len(rows)
    if len(png) > _MAX_PNG:
        raise _Failure(ScreenshotErrorCode.LIMIT_EXCEEDED)
    return png


def _validate_png(png: bytes, width: int, height: int) -> None:
    if len(png) > _MAX_PNG or not png.startswith(_SIGNATURE):
        raise ValueError("Invalid PNG boundary")
    offset = 8
    chunks: list[tuple[bytes, bytes]] = []
    while offset < len(png):
        if len(chunks) >= 3 or offset + 12 > len(png):
            raise ValueError("Invalid PNG chunks")
        length = struct.unpack_from(">I", png, offset)[0]
        end = offset + 12 + length
        if end > len(png):
            raise ValueError("Invalid PNG chunk length")
        kind = png[offset + 4 : offset + 8]
        data = png[offset + 8 : end - 4]
        if binascii.crc32(kind + data) != struct.unpack_from(">I", png, end - 4)[0]:
            raise ValueError("Invalid PNG CRC")
        chunks.append((kind, data))
        offset = end
    if [kind for kind, _ in chunks] != [b"IHDR", b"IDAT", b"IEND"] or chunks[2][
        1
    ] != b"":
        raise ValueError("Expected deterministic metadata-free PNG")
    if chunks[0][1] != struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0):
        raise ValueError("PNG dimensions or encoding disagree")
    expected = height * (1 + width * 4)
    try:
        decoder = zlib.decompressobj()
        raw = decoder.decompress(chunks[1][1], expected + 1)
        if (
            len(raw) != expected
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
        ):
            raise ValueError("Invalid bounded PNG raster")
        if any(raw[row * (1 + width * 4)] != 0 for row in range(height)):
            raise ValueError("Expected fixed PNG filter")
    except zlib.error:
        raise ValueError("Invalid PNG compression") from None


class ScreenshotFrame(_Contract):
    frame_id: _Id
    captured_at: AwareDatetime
    hwnd: _Positive
    process_id: _Positive
    class_name: _Class
    x: int = Field(strict=True)
    y: int = Field(strict=True)
    width: _Dimension
    height: _Dimension
    png_sha256: Annotated[
        str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")
    ]
    png_base64: str = Field(strict=True, repr=False)

    @field_validator("captured_at")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("png_base64", "png_sha256", mode="before")
    @classmethod
    def ordinary_image_text(cls, value: object) -> str:
        if type(value) is not str:
            raise ValueError("Expected ordinary image evidence string")
        return value

    @model_validator(mode="after")
    def image(self) -> Self:
        _dimensions(self.width, self.height)
        if len(self.png_base64) > 4 * ((_MAX_PNG + 2) // 3):
            raise ValueError("Encoded PNG exceeds bound")
        try:
            png = base64.b64decode(self.png_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Invalid image encoding") from None
        if (
            base64.b64encode(png).decode("ascii") != self.png_base64
            or sha256(png).hexdigest() != self.png_sha256
        ):
            raise ValueError("Noncanonical base64 or hash mismatch")
        _validate_png(png, self.width, self.height)
        return self


class ScreenshotToolResult(_Contract):
    call_id: _Id
    tool_id: _Id
    resource: _Id
    status: ToolResultStatus
    attempted: bool = Field(strict=True)
    permission_decision: PermissionDecision
    frame: ScreenshotFrame | None = None
    error_code: ScreenshotErrorCode | None = None

    @field_validator("status", mode="before")
    @classmethod
    def status_member(cls, value: object) -> object:
        return _enum(value, ToolResultStatus)

    @field_validator("error_code", mode="before")
    @classmethod
    def error_member(cls, value: object) -> object:
        return _enum(value, ScreenshotErrorCode) if value is not None else None

    @field_validator("permission_decision", mode="before")
    @classmethod
    def decision_snapshot(cls, value: object) -> PermissionDecision:
        return (
            _validated(value, PermissionDecision)
            if isinstance(value, BaseModel)
            else PermissionDecision.model_validate(value)
        )

    @field_validator("frame", mode="before")
    @classmethod
    def frame_snapshot(cls, value: object) -> ScreenshotFrame | None:
        if value is None:
            return None
        return (
            _validated(value, ScreenshotFrame)
            if isinstance(value, BaseModel)
            else ScreenshotFrame.model_validate(value)
        )

    @model_validator(mode="after")
    def consistent(self) -> Self:
        call = _resource_call(self.resource)
        decision = self.permission_decision
        if self.tool_id != "screen.capture_window" or (
            decision.tool_id,
            decision.risk_level,
            decision.permission_requirement,
        ) != (self.tool_id, ToolRiskLevel.P1, _REQUIREMENT):
            raise ValueError("Result authority disagrees")
        allowed = decision.status == PermissionStatus.ALLOWED
        if self.attempted != allowed:
            raise ValueError("Invalid attempt evidence")
        if not allowed:
            if (
                self.status != ToolResultStatus.DENIED
                or self.frame is not None
                or self.error_code is not None
            ):
                raise ValueError("Denied result cannot contain capture evidence")
        elif self.status == ToolResultStatus.SUCCESS:
            if (
                self.frame is None
                or self.error_code is not None
                or (self.frame.hwnd, self.frame.process_id, self.frame.class_name)
                != (call.hwnd, call.expected_process_id, call.expected_class_name)
            ):
                raise ValueError("Invalid successful capture")
        else:
            status = (
                ToolResultStatus.UNAVAILABLE_DEPENDENCY
                if self.error_code
                in (
                    ScreenshotErrorCode.UNSUPPORTED_PLATFORM,
                    ScreenshotErrorCode.CAPTURE_UNAVAILABLE,
                )
                else ToolResultStatus.FAILURE
            )
            if (
                self.frame is not None
                or self.error_code is None
                or self.status != status
            ):
                raise ValueError("Invalid failed capture")
        return self


def screenshot_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            tool_id="screen.capture_window",
            description="Observe one exact window; pixels are untrusted sensitive data",
            input_schema=ScreenshotCall.model_json_schema(),
            output_schema=ScreenshotToolResult.model_json_schema(),
            risk_level=ToolRiskLevel.P1,
            permission_requirement=_REQUIREMENT,
            timeout_seconds=10,
            cancellation_supported=False,
            audit_behavior="no mandatory durable audit",
            supported_platforms=("win32",),
            supported_environments=("local", "windows-gdi"),
        ),
    )


class _Failure(Exception):
    def __init__(self, code: ScreenshotErrorCode) -> None:
        self.code = code


@dataclass(frozen=True, slots=True)
class _Window:
    process_id: int
    class_name: str
    visible: bool
    minimized: bool
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class _Pixels:
    x: int
    y: int
    width: int
    height: int
    top_down: bool
    bgra: bytes | bytearray


class _CaptureBackend(Protocol):
    def inspect(self, hwnd: int) -> _Window: ...
    def capture(self, hwnd: int, window: _Window) -> _Pixels: ...


class _Rect(ctypes.Structure):
    _fields_ = [(key, ctypes.c_int32) for key in ("left", "top", "right", "bottom")]


class _BitmapInfo(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("width", ctypes.c_int32),
        ("height", ctypes.c_int32),
        ("planes", ctypes.c_uint16),
        ("bit_count", ctypes.c_uint16),
        ("compression", ctypes.c_uint32),
        ("size_image", ctypes.c_uint32),
        ("x_pels", ctypes.c_int32),
        ("y_pels", ctypes.c_int32),
        ("clr_used", ctypes.c_uint32),
        ("clr_important", ctypes.c_uint32),
    ]


def _win_api() -> tuple[Any, Any]:
    user = ctypes.WinDLL("user32", use_last_error=True)
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    handle = ctypes.c_void_p
    declarations: tuple[tuple[Any, str, list[Any], Any], ...] = (
        (user, "IsWindow", [handle], ctypes.c_int),
        (user, "SetThreadDpiAwarenessContext", [handle], handle),
        (
            user,
            "GetWindowThreadProcessId",
            [handle, ctypes.POINTER(ctypes.c_uint32)],
            ctypes.c_uint32,
        ),
        (user, "GetClassNameW", [handle, ctypes.c_wchar_p, ctypes.c_int], ctypes.c_int),
        (user, "IsWindowVisible", [handle], ctypes.c_int),
        (user, "IsIconic", [handle], ctypes.c_int),
        (user, "GetWindowRect", [handle, ctypes.POINTER(_Rect)], ctypes.c_int),
        (user, "GetWindowDC", [handle], handle),
        (user, "ReleaseDC", [handle, handle], ctypes.c_int),
        (user, "PrintWindow", [handle, handle, ctypes.c_uint], ctypes.c_int),
        (gdi, "CreateCompatibleDC", [handle], handle),
        (
            gdi,
            "CreateDIBSection",
            [
                handle,
                ctypes.POINTER(_BitmapInfo),
                ctypes.c_uint,
                ctypes.POINTER(handle),
                handle,
                ctypes.c_uint32,
            ],
            handle,
        ),
        (gdi, "SelectObject", [handle, handle], handle),
        (gdi, "DeleteObject", [handle], ctypes.c_int),
        (gdi, "DeleteDC", [handle], ctypes.c_int),
        (gdi, "GdiFlush", [], ctypes.c_int),
    )
    for library, name, arguments, result in declarations:
        function = getattr(library, name)
        function.argtypes = arguments
        function.restype = result
    return user, gdi


class _WindowsCapture:
    def __init__(self) -> None:
        try:
            self._user, self._gdi = _win_api()
        except Exception:
            raise _Failure(ScreenshotErrorCode.CAPTURE_UNAVAILABLE) from None

    @contextmanager
    def _physical_pixels(self) -> Iterator[None]:
        # GetWindowRect is DPI-virtualized. Temporarily use physical per-monitor
        # coordinates on this calling thread, never change process-wide policy.
        previous = self._user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-3))
        if not previous:
            raise _Failure(ScreenshotErrorCode.CAPTURE_UNAVAILABLE)
        try:
            yield
        finally:
            try:
                if not self._user.SetThreadDpiAwarenessContext(previous):
                    raise _Failure(ScreenshotErrorCode.IO_FAILURE)
            except Exception:
                raise _Failure(ScreenshotErrorCode.IO_FAILURE) from None

    def inspect(self, hwnd: int) -> _Window:
        with self._physical_pixels():
            return self._inspect(hwnd)

    def _inspect(self, hwnd: int) -> _Window:
        user = self._user
        if not user.IsWindow(hwnd):
            raise _Failure(ScreenshotErrorCode.WINDOW_NOT_FOUND)
        pid = ctypes.c_uint32()
        name = ctypes.create_unicode_buffer(258)
        rect = _Rect()
        if not user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            raise _Failure(ScreenshotErrorCode.WINDOW_NOT_FOUND)
        count = user.GetClassNameW(hwnd, name, len(name))
        if not count or count > 256 or not user.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise _Failure(ScreenshotErrorCode.IO_FAILURE)
        return _Window(
            pid.value,
            name.value,
            bool(user.IsWindowVisible(hwnd)),
            bool(user.IsIconic(hwnd)),
            rect.left,
            rect.top,
            rect.right - rect.left,
            rect.bottom - rect.top,
        )

    def capture(self, hwnd: int, window: _Window) -> _Pixels:
        pixels = None
        try:
            with self._physical_pixels():
                pixels = self._capture(hwnd, window)
            return pixels
        except BaseException:
            if pixels is not None and type(pixels.bgra) is bytearray:
                pixels.bgra[:] = b"\0" * len(pixels.bgra)
            raise

    def _capture(self, hwnd: int, window: _Window) -> _Pixels:
        width, height = window.width, window.height
        _dimensions(width, height)
        dc = memory = bitmap = previous = None
        address = ctypes.c_void_p()
        cleanup_failed = False
        try:
            dc = self._user.GetWindowDC(hwnd)
            if not dc:
                raise _Failure(ScreenshotErrorCode.CAPTURE_UNAVAILABLE)
            memory = self._gdi.CreateCompatibleDC(dc)
            if not memory:
                raise _Failure(ScreenshotErrorCode.CAPTURE_UNAVAILABLE)
            info = _BitmapInfo()
            info.size = ctypes.sizeof(_BitmapInfo)
            info.width, info.height, info.planes, info.bit_count = width, -height, 1, 32
            bitmap = self._gdi.CreateDIBSection(
                dc, ctypes.byref(info), 0, ctypes.byref(address), None, 0
            )
            if not bitmap or not address.value:
                raise _Failure(ScreenshotErrorCode.CAPTURE_UNAVAILABLE)
            previous = self._gdi.SelectObject(memory, bitmap)
            if not previous or previous == ctypes.c_void_p(-1).value:
                previous = None
                raise _Failure(ScreenshotErrorCode.CAPTURE_UNAVAILABLE)
            ctypes.memset(address, 0, width * height * 4)
            # Fixed whole-target rendering; never copy from the desktop/screen.
            if not self._user.PrintWindow(hwnd, memory, 0) or not self._gdi.GdiFlush():
                raise _Failure(ScreenshotErrorCode.CAPTURE_UNAVAILABLE)
            pixels = bytearray(ctypes.string_at(address, width * height * 4))
            # BI_RGB/GDI's reserved fourth channel has no defined alpha: the
            # window rendering is opaque. Synthetic encoder input keeps its alpha.
            pixels[3::4] = b"\xff" * (width * height)
            return _Pixels(window.x, window.y, width, height, True, pixels)
        finally:
            if bitmap and address.value:
                try:
                    ctypes.memset(address, 0, width * height * 4)
                except Exception:
                    cleanup_failed = True
            for resource, function, arguments in (
                (previous, self._gdi.SelectObject, (memory, previous)),
                (bitmap, self._gdi.DeleteObject, (bitmap,)),
                (memory, self._gdi.DeleteDC, (memory,)),
                (dc, self._user.ReleaseDC, (hwnd, dc)),
            ):
                if resource:
                    try:
                        returned = function(*arguments)
                        if not returned or returned == ctypes.c_void_p(-1).value:
                            cleanup_failed = True
                    except Exception:
                        cleanup_failed = True
            if cleanup_failed:
                raise _Failure(ScreenshotErrorCode.IO_FAILURE) from None


def _load_backend() -> _CaptureBackend:
    if sys.platform != "win32":
        raise _Failure(ScreenshotErrorCode.UNSUPPORTED_PLATFORM)
    return _WindowsCapture()


def _check_window(window: _Window, call: ScreenshotCall) -> None:
    if (
        type(window) is not _Window
        or type(window.process_id) is not int
        or type(window.class_name) is not str
    ):
        raise _Failure(ScreenshotErrorCode.IO_FAILURE)
    if (window.process_id, window.class_name) != (
        call.expected_process_id,
        call.expected_class_name,
    ):
        raise _Failure(ScreenshotErrorCode.WINDOW_CHANGED)
    if (
        type(window.visible) is not bool
        or type(window.minimized) is not bool
        or type(window.x) is not int
        or type(window.y) is not int
    ):
        raise _Failure(ScreenshotErrorCode.IO_FAILURE)
    if not window.visible:
        raise _Failure(ScreenshotErrorCode.WINDOW_HIDDEN)
    if window.minimized:
        raise _Failure(ScreenshotErrorCode.WINDOW_MINIMIZED)
    try:
        _dimensions(window.width, window.height)
    except ValueError:
        raise _Failure(ScreenshotErrorCode.LIMIT_EXCEEDED) from None


@dataclass(frozen=True, slots=True, init=False)
class ScreenshotCaptureTool:
    _registry: ToolRegistry
    _permissions: PermissionEngine

    def __init__(self, registry: ToolRegistry, policy: PermissionPolicy) -> None:
        if hasattr(self, "_registry"):
            raise ScreenshotInputError()
        try:
            if type(registry) is not ToolRegistry:
                raise ValueError("Expected exact registry")
            snapshot = ToolRegistry(registry.list_tools())
            canonical = screenshot_tool_definitions()[0]
            definition = snapshot.require(canonical.tool_id)
            _enum(definition.risk_level, ToolRiskLevel)
            if definition != canonical:
                raise ValueError("Noncanonical screenshot declaration")
            permissions = PermissionEngine(
                snapshot, _validated(policy, PermissionPolicy)
            )
        except (ValueError, TypeError, AttributeError, ToolRegistryError):
            raise ScreenshotInputError() from None
        object.__setattr__(self, "_registry", snapshot)
        object.__setattr__(self, "_permissions", permissions)

    def describe(self, call: ScreenshotCall) -> ScreenshotIntent:
        try:
            call = _validated(call, ScreenshotCall)
            definition = self._registry.require("screen.capture_window")
            return ScreenshotIntent(
                call_id=call.call_id,
                tool_id=definition.tool_id,
                action="capture_window",
                resource=_resource(call),
                risk_level=definition.risk_level,
                permission_requirement=definition.permission_requirement,
            )
        except (ValueError, TypeError, AttributeError, ToolRegistryError):
            raise ScreenshotInputError() from None

    def execute(
        self,
        call: ScreenshotCall,
        request: PermissionRequest,
        *,
        grants: Sequence[PermissionGrant] = (),
        user_decision: UserPermissionDecision | None = None,
    ) -> ScreenshotToolResult:
        try:
            call = _validated(call, ScreenshotCall)
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
            raise ScreenshotInputError() from None
        allowed = decision.status == PermissionStatus.ALLOWED
        frame = None
        error = None
        status = ToolResultStatus.DENIED
        if allowed:
            frame, error = self._capture(call)
            status = (
                ToolResultStatus.SUCCESS
                if frame is not None
                else (
                    ToolResultStatus.UNAVAILABLE_DEPENDENCY
                    if error
                    in (
                        ScreenshotErrorCode.UNSUPPORTED_PLATFORM,
                        ScreenshotErrorCode.CAPTURE_UNAVAILABLE,
                    )
                    else ToolResultStatus.FAILURE
                )
            )
        return ScreenshotToolResult(
            call_id=call.call_id,
            tool_id=intent.tool_id,
            resource=intent.resource,
            status=status,
            attempted=allowed,
            permission_decision=decision,
            frame=frame,
            error_code=error,
        )

    @staticmethod
    def _capture(
        call: ScreenshotCall,
    ) -> tuple[ScreenshotFrame | None, ScreenshotErrorCode | None]:
        pixels = None
        try:
            backend = _load_backend()
            window = backend.inspect(call.hwnd)
            _check_window(window, call)
            pixels = backend.capture(call.hwnd, window)
            if (
                type(pixels) is not _Pixels
                or type(pixels.x) is not int
                or type(pixels.y) is not int
                or (pixels.x, pixels.y) != (window.x, window.y)
                or (pixels.width, pixels.height) != (window.width, window.height)
                or type(pixels.width) is not int
                or type(pixels.height) is not int
                or type(pixels.top_down) is not bool
                or type(pixels.bgra) not in (bytes, bytearray)
                or len(pixels.bgra) != window.width * window.height * 4
            ):
                raise _Failure(ScreenshotErrorCode.INVALID_PIXELS)
            try:
                after = backend.inspect(call.hwnd)
            except _Failure as failure:
                if failure.code == ScreenshotErrorCode.WINDOW_NOT_FOUND:
                    raise _Failure(ScreenshotErrorCode.WINDOW_CHANGED) from None
                raise
            if (
                type(after) is not _Window
                or type(after.process_id) is not int
                or type(after.class_name) is not str
                or (after.process_id, after.class_name)
                != (window.process_id, window.class_name)
                or (after.x, after.y, after.width, after.height)
                != (window.x, window.y, window.width, window.height)
            ):
                raise _Failure(ScreenshotErrorCode.WINDOW_CHANGED)
            try:
                png = _encode_png(
                    pixels.width, pixels.height, pixels.bgra, pixels.top_down
                )
            except _Failure:
                raise
            except Exception:
                raise _Failure(ScreenshotErrorCode.ENCODING_FAILURE) from None
            return ScreenshotFrame(
                frame_id=str(uuid4()),
                captured_at=datetime.now(UTC),
                hwnd=call.hwnd,
                process_id=window.process_id,
                class_name=window.class_name,
                x=window.x,
                y=window.y,
                width=window.width,
                height=window.height,
                png_sha256=sha256(png).hexdigest(),
                png_base64=base64.b64encode(png).decode("ascii"),
            ), None
        except _Failure as failure:
            return None, failure.code
        except Exception:
            return None, ScreenshotErrorCode.IO_FAILURE
        finally:
            if type(pixels) is _Pixels and type(pixels.bgra) is bytearray:
                pixels.bgra[:] = b"\0" * len(pixels.bgra)
