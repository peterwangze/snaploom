"""Win32 capture with physical monitor bounds and cursor composition."""

from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import os

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication, QImage

from .capture import CaptureError, ScreenFrame


_user = C.WinDLL("user32", use_last_error=True)
_gdi = C.WinDLL("gdi32", use_last_error=True)
_dwm = C.WinDLL("dwmapi", use_last_error=True)
_monitor_callback = C.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, C.POINTER(W.RECT), W.LPARAM)
_window_callback = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)


class _MonitorInfo(C.Structure):
    _fields_ = [
        ("size", W.DWORD),
        ("monitor", W.RECT),
        ("work", W.RECT),
        ("flags", W.DWORD),
        ("device", W.WCHAR * 32),
    ]


class _BitmapInfo(C.Structure):
    _fields_ = [
        ("size", W.DWORD),
        ("width", W.LONG),
        ("height", W.LONG),
        ("planes", W.WORD),
        ("bits", W.WORD),
        ("compression", W.DWORD),
        ("image_size", W.DWORD),
        ("horizontal_ppm", W.LONG),
        ("vertical_ppm", W.LONG),
        ("colors_used", W.DWORD),
        ("important_colors", W.DWORD),
    ]


class _CursorInfo(C.Structure):
    _fields_ = [("size", W.DWORD), ("flags", W.DWORD), ("handle", W.HANDLE), ("point", W.POINT)]


class _IconInfo(C.Structure):
    _fields_ = [
        ("icon", W.BOOL),
        ("x", W.DWORD),
        ("y", W.DWORD),
        ("mask", W.HBITMAP),
        ("color", W.HBITMAP),
    ]


def _bind(dll, name, restype, *argtypes):
    function = getattr(dll, name)
    function.restype = restype
    function.argtypes = argtypes
    return function


_get_monitor_info = _bind(_user, "GetMonitorInfoW", W.BOOL, W.HMONITOR, C.POINTER(_MonitorInfo))
_enum_monitors = _bind(_user, "EnumDisplayMonitors", W.BOOL, W.HDC, C.POINTER(W.RECT), _monitor_callback, W.LPARAM)
_get_dc = _bind(_user, "GetDC", W.HDC, W.HWND)
_release_dc = _bind(_user, "ReleaseDC", C.c_int, W.HWND, W.HDC)
_create_dc = _bind(_gdi, "CreateCompatibleDC", W.HDC, W.HDC)
_delete_dc = _bind(_gdi, "DeleteDC", W.BOOL, W.HDC)
_create_dib = _bind(_gdi, "CreateDIBSection", W.HBITMAP, W.HDC, C.POINTER(_BitmapInfo), W.UINT, C.POINTER(C.c_void_p), W.HANDLE, W.DWORD)
_select_object = _bind(_gdi, "SelectObject", W.HANDLE, W.HDC, W.HANDLE)
_delete_object = _bind(_gdi, "DeleteObject", W.BOOL, W.HANDLE)
_bit_blt = _bind(_gdi, "BitBlt", W.BOOL, W.HDC, C.c_int, C.c_int, C.c_int, C.c_int, W.HDC, C.c_int, C.c_int, W.DWORD)
_gdi_flush = _bind(_gdi, "GdiFlush", W.BOOL)
_get_cursor_info = _bind(_user, "GetCursorInfo", W.BOOL, C.POINTER(_CursorInfo))
_get_icon_info = _bind(_user, "GetIconInfo", W.BOOL, W.HANDLE, C.POINTER(_IconInfo))
_draw_icon = _bind(_user, "DrawIconEx", W.BOOL, W.HDC, C.c_int, C.c_int, W.HANDLE, C.c_int, C.c_int, W.UINT, W.HANDLE, W.UINT)
_enum_windows = _bind(_user, "EnumWindows", W.BOOL, _window_callback, W.LPARAM)
_is_visible = _bind(_user, "IsWindowVisible", W.BOOL, W.HWND)
_is_minimized = _bind(_user, "IsIconic", W.BOOL, W.HWND)
_get_pid = _bind(_user, "GetWindowThreadProcessId", W.DWORD, W.HWND, C.POINTER(W.DWORD))
_get_class = _bind(_user, "GetClassNameW", C.c_int, W.HWND, W.LPWSTR, C.c_int)
_get_title = _bind(_user, "GetWindowTextW", C.c_int, W.HWND, W.LPWSTR, C.c_int)
_get_rect = _bind(_user, "GetWindowRect", W.BOOL, W.HWND, C.POINTER(W.RECT))
_get_active = _bind(_user, "GetForegroundWindow", W.HWND)
_screen_to_client = _bind(_user, "ScreenToClient", W.BOOL, W.HWND, C.POINTER(W.POINT))
_child_from_point = _bind(_user, "ChildWindowFromPointEx", W.HWND, W.HWND, W.POINT, W.UINT)
_dwm_attr = _bind(_dwm, "DwmGetWindowAttribute", W.LONG, W.HWND, W.DWORD, C.c_void_p, W.DWORD)
_dwm_set_attr = _bind(_dwm, "DwmSetWindowAttribute", W.LONG, W.HWND, W.DWORD, C.c_void_p, W.DWORD)
_dwm_flush = _bind(_dwm, "DwmFlush", W.LONG)

DWMWA_TRANSITIONS_FORCEDISABLED = 3


def disable_window_transitions(hwnd: int) -> None:
    disabled = W.BOOL(True)
    _dwm_set_attr(hwnd, DWMWA_TRANSITIONS_FORCEDISABLED, C.byref(disabled), C.sizeof(disabled))


def _qt_rect(rect: W.RECT) -> QRect:
    return QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _monitor_bounds() -> dict[str, QRect]:
    result: dict[str, QRect] = {}

    @_monitor_callback
    def add(handle, _dc, _rect, _context):
        info = _MonitorInfo()
        info.size = C.sizeof(info)
        if _get_monitor_info(handle, C.byref(info)):
            result[info.device.casefold()] = _qt_rect(info.monitor)
        return True

    _enum_monitors(None, None, add, 0)
    return result


def _monitor_mapping(screens) -> list[tuple[QRect, QRect]]:
    native_bounds = _monitor_bounds()
    mapping = []
    for screen in screens:
        logical = screen.geometry()
        physical = native_bounds.get(screen.name().casefold())
        if physical is None:
            physical = next(
                (rect for rect in native_bounds.values() if rect.topLeft() == logical.topLeft()),
                None,
            )
        if physical is None:
            ratio = screen.devicePixelRatio()
            physical = QRect(logical.x(), logical.y(), round(logical.width() * ratio), round(logical.height() * ratio))
        mapping.append((logical, physical))
    return mapping


def _desktop_rect(physical: QRect, mapping) -> QRect:
    mapped = QRect()
    for logical, monitor in mapping:
        region = physical.intersected(monitor)
        if region.isEmpty():
            continue
        scale_x = logical.width() / monitor.width()
        scale_y = logical.height() / monitor.height()
        x = region.x() - monitor.x()
        y = region.y() - monitor.y()
        left, top = round(x * scale_x), round(y * scale_y)
        right = round((x + region.width()) * scale_x)
        bottom = round((y + region.height()) * scale_y)
        mapped = mapped.united(
            QRect(logical.x() + left, logical.y() + top, max(1, right - left), max(1, bottom - top))
        )
    return mapped


def _physical_point(point: QPoint, mapping) -> W.POINT:
    for logical, physical in mapping:
        if logical.contains(point):
            return W.POINT(
                physical.x() + round((point.x() - logical.x()) * physical.width() / logical.width()),
                physical.y() + round((point.y() - logical.y()) * physical.height() / logical.height()),
            )
    return W.POINT(point.x(), point.y())


def _cursor_info() -> tuple[W.HANDLE, int, int] | None:
    cursor = _CursorInfo()
    cursor.size = C.sizeof(cursor)
    if not _get_cursor_info(C.byref(cursor)) or not cursor.flags & 1:
        return None
    icon = _IconInfo()
    if not _get_icon_info(cursor.handle, C.byref(icon)):
        return None
    if icon.mask:
        _delete_object(icon.mask)
    if icon.color:
        _delete_object(icon.color)
    return cursor.handle, cursor.point.x - icon.x, cursor.point.y - icon.y


def _capture_display(desktop, rect: QRect, cursor) -> QImage:
    width, height = rect.width(), rect.height()
    target = _create_dc(desktop)
    if not target:
        raise CaptureError("Windows 无法创建截图缓冲区。")
    bitmap = None
    previous = None
    try:
        info = _BitmapInfo()
        info.size = C.sizeof(info)
        info.width, info.height = width, -height
        info.planes, info.bits = 1, 32
        pixels = C.c_void_p()
        bitmap = _create_dib(target, C.byref(info), 0, C.byref(pixels), None, 0)
        if not bitmap:
            raise CaptureError("Windows 无法分配截图像素。")
        previous = _select_object(target, bitmap)
        if not previous or not _bit_blt(target, 0, 0, width, height, desktop, rect.x(), rect.y(), 0x40CC0020):
            raise CaptureError(f"Windows 无法读取显示器画面（错误 {C.get_last_error()}）。")
        if cursor:
            handle, x, y = cursor
            _draw_icon(target, x - rect.x(), y - rect.y(), handle, 0, 0, 0, None, 3)
        _gdi_flush()
        data = C.string_at(pixels, width * height * 4)
        return QImage(data, width, height, width * 4, QImage.Format.Format_RGB32).copy()
    finally:
        if previous:
            _select_object(target, previous)
        if bitmap:
            _delete_object(bitmap)
        _delete_dc(target)


def capture_frames(screens, include_cursor: bool) -> tuple[ScreenFrame, ...]:
    # Let DWM present our pending window changes before copying its desktop image.
    _dwm_flush()
    desktop = _get_dc(None)
    if not desktop:
        raise CaptureError("Windows 无法访问桌面画面。")
    try:
        cursor = _cursor_info() if include_cursor else None
        return tuple(
            ScreenFrame(logical, _capture_display(desktop, physical, cursor))
            for logical, physical in _monitor_mapping(screens)
        )
    finally:
        _release_dc(None, desktop)


def _window_title(window) -> str:
    title = C.create_unicode_buffer(512)
    _get_title(window, title, len(title))
    return title.value


def _window_class(window) -> str:
    name = C.create_unicode_buffer(128)
    _get_class(window, name, len(name))
    return name.value


def _window_rect(window, *, frame: bool = True) -> QRect:
    rect = W.RECT()
    if frame and _dwm_attr(window, 9, C.byref(rect), C.sizeof(rect)) == 0:
        return _qt_rect(rect)
    return _qt_rect(rect) if _get_rect(window, C.byref(rect)) else QRect()


def _capture_window_is_visible(window, own_pid: int) -> bool:
    if not _is_visible(window) or _is_minimized(window):
        return False
    pid = W.DWORD()
    _get_pid(window, C.byref(pid))
    if pid.value == own_pid or _window_class(window) in {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}:
        return False
    cloaked = W.DWORD()
    return _dwm_attr(window, 14, C.byref(cloaked), C.sizeof(cloaked)) != 0 or not cloaked.value


def _visible_windows(point: W.POINT | None = None) -> list[tuple[W.HWND, QRect]]:
    result: list[tuple[W.HWND, QRect]] = []
    own_pid = os.getpid()

    @_window_callback
    def add(window, _context):
        if not _capture_window_is_visible(window, own_pid):
            return True
        rect = _window_rect(window)
        if rect.isEmpty():
            return True
        if point is not None:
            if not rect.contains(point.x, point.y):
                return True
            result.append((window, rect))
            return False
        result.append((window, rect))
        return True

    _enum_windows(add, 0)
    return result


def detect_windows() -> list[tuple[QRect, str]]:
    mapping = _monitor_mapping(QGuiApplication.screens())
    result = []
    for window, physical in _visible_windows():
        rect = _desktop_rect(physical, mapping)
        if not rect.isEmpty():
            result.append((rect, _window_title(window) or "窗口"))
    return result


def active_window() -> QRect | None:
    window = _get_active()
    if not window or not _capture_window_is_visible(window, os.getpid()):
        return None
    rect = _desktop_rect(_window_rect(window), _monitor_mapping(QGuiApplication.screens()))
    return rect if not rect.isEmpty() else None


def detect_elements(position: QPoint) -> list[tuple[QRect, str]]:
    from .capture_uia import elements

    mapping = _monitor_mapping(QGuiApplication.screens())
    point = _physical_point(position, mapping)
    windows = _visible_windows(point)
    if not windows:
        return []
    window, _rect = windows[0]
    accessible = [
        (mapped, label)
        for physical, label in elements(window, point)
        if not (mapped := _desktop_rect(physical, mapping)).isEmpty()
    ]
    if len(accessible) > 1:
        return accessible
    result: list[tuple[QRect, str]] = []
    for _ in range(24):
        rect = _desktop_rect(_window_rect(window, frame=False), mapping)
        if not rect.isEmpty() and (not result or rect != result[-1][0]):
            result.append((rect, _window_title(window) or _window_class(window)))
        client_point = W.POINT(point.x, point.y)
        if not _screen_to_client(window, C.byref(client_point)):
            break
        child = _child_from_point(window, client_point, 1 | 2 | 4)
        if not child or child == window:
            break
        window = child
    return list(reversed(result)) if len(result) > 1 else accessible or result
