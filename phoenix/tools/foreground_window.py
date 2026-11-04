"""PHX-135 (ADR-247): the foreground window, for "what's on my screen".

``ScreenshotCaptureTool`` observes one exact window (hwnd, process ID and class
name). The screen quick command looks at the window the user is looking at:
the foreground one. This module only reads its identity; capture still goes
through the tool and PermissionEngine. Windows only; anything else, or any
failure, is None.
"""

from __future__ import annotations

import ctypes
import sys

from phoenix.tools.screenshot import ScreenshotCall

__all__ = ("foreground_call",)

_CLASS_CHARS = 257  # 256 plus the terminator (ScreenshotCall's class bound)


def foreground_call() -> ScreenshotCall | None:
    """A ScreenshotCall naming the foreground window, or None."""
    if sys.platform != "win32":
        return None
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        foreground = user32.GetForegroundWindow
        foreground.argtypes = ()
        foreground.restype = wintypes.HWND
        owner = user32.GetWindowThreadProcessId
        owner.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        owner.restype = wintypes.DWORD
        class_name = user32.GetClassNameW
        class_name.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        class_name.restype = ctypes.c_int
        hwnd = foreground()
        if not hwnd:
            return None
        process_id = wintypes.DWORD()
        owner(hwnd, ctypes.byref(process_id))
        buffer = ctypes.create_unicode_buffer(_CLASS_CHARS)
        if not process_id.value or class_name(hwnd, buffer, _CLASS_CHARS) <= 0:
            return None
        return ScreenshotCall(
            hwnd=int(hwnd),
            expected_process_id=int(process_id.value),
            expected_class_name=buffer.value,
        )
    except Exception:
        return None
