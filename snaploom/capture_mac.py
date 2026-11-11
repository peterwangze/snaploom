"""macOS screen capture and Quartz/Accessibility window detection."""

from __future__ import annotations

import ctypes as C
from functools import lru_cache
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QColorSpace, QImage

from .capture import CaptureError, ScreenFrame, _normalized_image


_reference = C.c_void_p
_utf8 = 0x08000100


class _Point(C.Structure):
    _fields_ = [("x", C.c_double), ("y", C.c_double)]


class _Size(C.Structure):
    _fields_ = [("width", C.c_double), ("height", C.c_double)]


class _Rect(C.Structure):
    _fields_ = [("origin", _Point), ("size", _Size)]

    def to_qt(self) -> QRect:
        return QRect(round(self.origin.x), round(self.origin.y), round(self.size.width), round(self.size.height))


def _bind(library, name, restype, *argtypes):
    function = getattr(library, name)
    function.restype = restype
    function.argtypes = argtypes
    return function


class _Frameworks:
    def __init__(self):
        base = "/System/Library/Frameworks"
        cf = C.CDLL(f"{base}/CoreFoundation.framework/CoreFoundation")
        cg = C.CDLL(f"{base}/CoreGraphics.framework/CoreGraphics")
        ax = C.CDLL(f"{base}/ApplicationServices.framework/ApplicationServices")
        self.release = _bind(cf, "CFRelease", None, _reference)
        self.string_create = _bind(cf, "CFStringCreateWithCString", _reference, _reference, C.c_char_p, C.c_uint32)
        self.string_read = _bind(cf, "CFStringGetCString", C.c_bool, _reference, C.c_char_p, C.c_long, C.c_uint32)
        self.dictionary_get = _bind(cf, "CFDictionaryGetValue", _reference, _reference, _reference)
        self.number_get = _bind(cf, "CFNumberGetValue", C.c_bool, _reference, C.c_int, _reference)
        self.array_len = _bind(cf, "CFArrayGetCount", C.c_long, _reference)
        self.array_get = _bind(cf, "CFArrayGetValueAtIndex", _reference, _reference, C.c_long)
        self.preflight = _bind(cg, "CGPreflightScreenCaptureAccess", C.c_bool)
        self.request = _bind(cg, "CGRequestScreenCaptureAccess", C.c_bool)
        self.window_list = _bind(cg, "CGWindowListCopyWindowInfo", _reference, C.c_uint32, C.c_uint32)
        self.dictionary_to_rect = _bind(cg, "CGRectMakeWithDictionaryRepresentation", C.c_bool, _reference, C.POINTER(_Rect))
        self.active_displays = _bind(cg, "CGGetActiveDisplayList", C.c_int, C.c_uint32, C.POINTER(C.c_uint32), C.POINTER(C.c_uint32))
        self.display_bounds = _bind(cg, "CGDisplayBounds", _Rect, C.c_uint32)
        self.ax_trusted = _bind(ax, "AXIsProcessTrusted", C.c_bool)
        self.ax_app = _bind(ax, "AXUIElementCreateApplication", _reference, C.c_int)
        self.ax_element_at = _bind(ax, "AXUIElementCopyElementAtPosition", C.c_int, _reference, C.c_float, C.c_float, C.POINTER(_reference))
        self.ax_get = _bind(ax, "AXUIElementCopyAttributeValue", C.c_int, _reference, _reference, C.POINTER(_reference))
        self.ax_timeout = _bind(ax, "AXUIElementSetMessagingTimeout", C.c_int, _reference, C.c_float)
        self.ax_value = _bind(ax, "AXValueGetValue", C.c_bool, _reference, C.c_int, _reference)
        self.keys = {}

    def key(self, name: str):
        if name not in self.keys:
            self.keys[name] = self.string_create(None, name.encode(), _utf8)
        return self.keys[name]

    def string(self, reference) -> str:
        if not reference:
            return ""
        buffer = C.create_string_buffer(4096)
        if not self.string_read(reference, buffer, len(buffer), _utf8):
            return ""
        return buffer.value.decode("utf-8", errors="replace")

    def number(self, reference) -> int:
        number = C.c_int64()
        if reference:
            self.number_get(reference, 4, C.byref(number))
        return number.value

    def attribute(self, element, name: str):
        value = _reference()
        if self.ax_get(element, self.key(name), C.byref(value)) != 0:
            return None
        return value.value


@lru_cache(maxsize=1)
def _native() -> _Frameworks:
    return _Frameworks()


def _active_display_bounds(native) -> list[QRect]:
    displays = (C.c_uint32 * 64)()
    count = C.c_uint32()
    if native.active_displays(len(displays), displays, C.byref(count)) != 0:
        return []
    return [native.display_bounds(displays[index]).to_qt() for index in range(count.value)]


def _system_capture(screens, include_cursor: bool) -> tuple[ScreenFrame, ...]:
    display_bounds = _active_display_bounds(_native())
    frames = []
    with TemporaryDirectory(prefix="snaploom-capture-") as folder:
        for fallback_index, screen in enumerate(screens, 1):
            geometry = screen.geometry()
            index = display_bounds.index(geometry) + 1 if geometry in display_bounds else fallback_index
            destination = str(Path(folder) / f"display-{index}.png")
            args = ["/usr/sbin/screencapture", "-x", "-D", str(index)]
            if include_cursor:
                args.append("-C")
            try:
                result = subprocess.run([*args, destination], capture_output=True, timeout=12, check=False)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise CaptureError("macOS 截图服务未响应，请重试。") from error
            image = QImage(destination)
            if result.returncode != 0 or image.isNull():
                raise CaptureError("macOS 未返回屏幕图像，请检查「屏幕与系统音频录制」权限。")
            if image.colorSpace().isValid():
                image = image.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
            frames.append(ScreenFrame(geometry, _normalized_image(image)))
    return tuple(frames)


def capture_frames(screens, include_cursor: bool) -> tuple[ScreenFrame, ...]:
    native = _native()
    if not native.preflight() and not native.request():
        raise CaptureError("请在「系统设置 → 隐私与安全性 → 屏幕与系统音频录制」中授权 Snaploom，再重新打开应用。")
    if include_cursor:
        return _system_capture(screens, True)
    frames = tuple(
        ScreenFrame(screen.geometry(), _normalized_image(screen.grabWindow(0).toImage()))
        for screen in screens
    )
    if any(frame.image.isNull() for frame in frames):
        return _system_capture(screens, False)
    return frames


def _quartz_windows() -> list[tuple[QRect, str, int]]:
    native = _native()
    windows = native.window_list(1 | 16, 0)
    if not windows:
        return []
    try:
        result = []
        for index in range(native.array_len(windows)):
            window = native.array_get(windows, index)
            pid = native.number(native.dictionary_get(window, native.key("kCGWindowOwnerPID")))
            layer = native.number(native.dictionary_get(window, native.key("kCGWindowLayer")))
            if pid == os.getpid() or layer != 0:
                continue
            bounds = _Rect()
            value = native.dictionary_get(window, native.key("kCGWindowBounds"))
            if not value or not native.dictionary_to_rect(value, C.byref(bounds)):
                continue
            rect = bounds.to_qt()
            if rect.isEmpty():
                continue
            name = native.string(native.dictionary_get(window, native.key("kCGWindowName")))
            owner = native.string(native.dictionary_get(window, native.key("kCGWindowOwnerName")))
            result.append((rect, name or owner or "窗口", pid))
        return result
    finally:
        native.release(windows)


def detect_windows() -> list[tuple[QRect, str]]:
    return [(rect, title) for rect, title, _pid in _quartz_windows()]


def permission_status() -> tuple[bool, bool]:
    native = _native()
    return native.preflight(), native.ax_trusted()


def _frontmost_process() -> int:
    objc = C.CDLL("/usr/lib/libobjc.A.dylib")
    get_class = _bind(objc, "objc_getClass", _reference, C.c_char_p)
    selector = _bind(objc, "sel_registerName", _reference, C.c_char_p)
    message = C.cast(objc.objc_msgSend, _reference).value
    send_object = C.CFUNCTYPE(_reference, _reference, _reference)(message)
    send_pid = C.CFUNCTYPE(C.c_int, _reference, _reference)(message)
    workspace = send_object(get_class(b"NSWorkspace"), selector(b"sharedWorkspace"))
    application = send_object(workspace, selector(b"frontmostApplication"))
    return send_pid(application, selector(b"processIdentifier")) if application else 0


def active_window() -> QRect | None:
    windows = _quartz_windows()
    if not windows:
        return None
    pid = _frontmost_process()
    return next((rect for rect, _title, owner in windows if owner == pid), windows[0][0])


def detect_elements(position: QPoint) -> list[tuple[QRect, str]]:
    native = _native()
    if not native.ax_trusted():
        return []
    window = next((entry for entry in _quartz_windows() if entry[0].contains(position)), None)
    if window is None:
        return []
    application = native.ax_app(window[2])
    if not application:
        return []
    element = _reference()
    try:
        native.ax_timeout(application, 0.12)
        if native.ax_element_at(application, position.x(), position.y(), C.byref(element)) != 0:
            return []
    finally:
        native.release(application)
    result = []
    current = element.value
    for _ in range(16):
        if not current:
            break
        attributes = []
        try:
            for name in ("AXPosition", "AXSize", "AXTitle", "AXRoleDescription"):
                attributes.append(native.attribute(current, name))
            position_value, size_value, title_value, role_value = attributes
            point, size = _Point(), _Size()
            if (
                position_value and size_value
                and native.ax_value(position_value, 1, C.byref(point))
                and native.ax_value(size_value, 2, C.byref(size))
            ):
                rect = _Rect(point, size).to_qt()
                if not rect.isEmpty() and (not result or rect != result[-1][0]):
                    result.append((rect, native.string(title_value) or native.string(role_value)))
            parent = native.attribute(current, "AXParent")
        finally:
            for value in attributes:
                if value:
                    native.release(value)
            native.release(current)
        current = parent
    if current:
        native.release(current)
    return result
