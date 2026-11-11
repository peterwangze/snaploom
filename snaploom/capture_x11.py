"""X11 capture and EWMH window geometry, including XFixes cursor pixels."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
from functools import lru_cache
import os
import struct

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication, QImage, QPainter

from .capture import CaptureError, ScreenFrame, _normalized_image


_display_type = C.c_void_p
_window_type = C.c_ulong
_atom_type = C.c_ulong
_error_callback = C.CFUNCTYPE(C.c_int, _display_type, C.c_void_p)


class _Monitor(C.Structure):
    _fields_ = [
        ("name", _atom_type), ("primary", C.c_int), ("automatic", C.c_int), ("noutput", C.c_int),
        ("x", C.c_int), ("y", C.c_int), ("width", C.c_int), ("height", C.c_int),
        ("width_mm", C.c_int), ("height_mm", C.c_int), ("outputs", C.c_void_p),
    ]


class _Cursor(C.Structure):
    _fields_ = [
        ("x", C.c_short), ("y", C.c_short), ("width", C.c_ushort), ("height", C.c_ushort),
        ("xhot", C.c_ushort), ("yhot", C.c_ushort), ("serial", C.c_ulong),
        ("pixels", C.POINTER(C.c_ulong)),
    ]


def _bind(library, name, restype, *argtypes):
    function = getattr(library, name)
    function.restype = restype
    function.argtypes = argtypes
    return function


@lru_cache(maxsize=1)
def _xlib():
    lib = C.CDLL("libX11.so.6")
    _bind(lib, "XOpenDisplay", _display_type, C.c_char_p)
    _bind(lib, "XCloseDisplay", C.c_int, _display_type)
    _bind(lib, "XDefaultRootWindow", _window_type, _display_type)
    _bind(lib, "XSetErrorHandler", C.c_void_p, C.c_void_p)
    _bind(lib, "XFree", C.c_int, C.c_void_p)
    _bind(lib, "XInternAtom", _atom_type, _display_type, C.c_char_p, C.c_int)
    _bind(lib, "XGetAtomName", C.c_void_p, _display_type, _atom_type)
    _bind(
        lib, "XGetWindowProperty", C.c_int, _display_type, _window_type, _atom_type,
        C.c_long, C.c_long, C.c_int, _atom_type, C.POINTER(_atom_type), C.POINTER(C.c_int),
        C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.c_void_p),
    )
    _bind(
        lib, "XGetGeometry", C.c_int, _display_type, _window_type, C.POINTER(_window_type),
        C.POINTER(C.c_int), C.POINTER(C.c_int), *([C.POINTER(C.c_uint)] * 4),
    )
    _bind(
        lib, "XTranslateCoordinates", C.c_int, _display_type, _window_type, _window_type,
        C.c_int, C.c_int, C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(_window_type),
    )
    _bind(lib, "XGetInputFocus", C.c_int, _display_type, C.POINTER(_window_type), C.POINTER(C.c_int))
    return lib


@contextmanager
def _connection():
    try:
        xlib = _xlib()
    except OSError:
        yield None, None, None
        return
    display = xlib.XOpenDisplay(None)
    if not display:
        yield xlib, None, None
        return
    previous_address = None

    @_error_callback
    def ignore_closed_window(source, error):
        # A window may be destroyed between the EWMH list and geometry request.
        if source != display and previous_address:
            return _error_callback(previous_address)(source, error)
        return 0

    previous_address = xlib.XSetErrorHandler(C.cast(ignore_closed_window, C.c_void_p))
    try:
        yield xlib, display, xlib.XDefaultRootWindow(display)
    finally:
        xlib.XCloseDisplay(display)
        xlib.XSetErrorHandler(previous_address)


def _property(xlib, display, window, name: bytes) -> list[int] | bytes:
    atom = xlib.XInternAtom(display, name, True)
    if not atom:
        return []
    prop_type, fmt = _atom_type(), C.c_int()
    count, remaining, data = C.c_ulong(), C.c_ulong(), C.c_void_p()
    status = xlib.XGetWindowProperty(
        display, window, atom, 0, 16384, False, 0, C.byref(prop_type), C.byref(fmt),
        C.byref(count), C.byref(remaining), C.byref(data),
    )
    if not data.value:
        return []
    try:
        if status != 0:
            return []
        if fmt.value == 8:
            return C.string_at(data, count.value)
        if fmt.value == 32:
            return C.cast(data, C.POINTER(C.c_ulong))[:count.value]
        return []
    finally:
        xlib.XFree(data)


def _monitor_mapping(xlib, display, root, screens) -> list[tuple[QRect, QRect]]:
    native = {}
    try:
        randr = C.CDLL("libXrandr.so.2")
        get_monitors = _bind(
            randr, "XRRGetMonitors", C.POINTER(_Monitor),
            _display_type, _window_type, C.c_int, C.POINTER(C.c_int),
        )
        free_monitors = _bind(randr, "XRRFreeMonitors", None, C.POINTER(_Monitor))
        count = C.c_int()
        monitors = get_monitors(display, root, True, C.byref(count))
        if monitors:
            try:
                for index in range(count.value):
                    monitor = monitors[index]
                    name = xlib.XGetAtomName(display, monitor.name)
                    if name:
                        try:
                            native[C.string_at(name).decode(errors="replace")] = QRect(
                                monitor.x, monitor.y, monitor.width, monitor.height
                            )
                        finally:
                            xlib.XFree(name)
            finally:
                free_monitors(monitors)
    except (OSError, AttributeError):
        pass

    mapping = []
    for screen in screens:
        logical = screen.geometry()
        physical = native.get(screen.name())
        if physical is None:
            scale = screen.devicePixelRatio()
            physical = QRect(
                logical.x(), logical.y(), round(logical.width() * scale), round(logical.height() * scale)
            )
        mapping.append((logical, physical))
    return mapping


def _cursor_image(xlib, display) -> tuple[QPoint, QImage] | None:
    try:
        fixes = C.CDLL("libXfixes.so.3")
    except OSError:
        return None
    query = _bind(
        fixes, "XFixesQueryExtension", C.c_int, _display_type, C.POINTER(C.c_int), C.POINTER(C.c_int)
    )
    first_event, first_error = C.c_int(), C.c_int()
    if not query(display, C.byref(first_event), C.byref(first_error)):
        return None
    get_cursor = _bind(fixes, "XFixesGetCursorImage", C.POINTER(_Cursor), _display_type)
    result = get_cursor(display)
    if not result:
        return None
    try:
        cursor = result.contents
        count = cursor.width * cursor.height
        pixels = struct.pack(f"={count}I", *cursor.pixels[:count])
        image = QImage(
            pixels, cursor.width, cursor.height, cursor.width * 4, QImage.Format.Format_ARGB32_Premultiplied
        ).copy()
        return QPoint(cursor.x - cursor.xhot, cursor.y - cursor.yhot), image
    finally:
        xlib.XFree(result)


def capture_frames(screens, include_cursor: bool) -> tuple[ScreenFrame, ...]:
    frames = []
    with _connection() as (xlib, display, root):
        if not display:
            raise CaptureError("无法访问 X11 显示服务器。")
        mapping = _monitor_mapping(xlib, display, root, screens)
        cursor = _cursor_image(xlib, display) if include_cursor else None
        if include_cursor and cursor is None:
            raise CaptureError("当前 X11 会话未提供光标图像（需要 XFixes）。")
        for screen, (logical, physical) in zip(screens, mapping, strict=True):
            image = _normalized_image(screen.grabWindow(0).toImage())
            if cursor and not image.isNull():
                position, cursor_image = cursor
                painter = QPainter(image)
                painter.drawImage(position - physical.topLeft(), cursor_image)
                painter.end()
            frames.append(ScreenFrame(logical, image))
    return tuple(frames)


def _window_geometry(xlib, display, root, window) -> QRect:
    base, child = _window_type(), _window_type()
    x, y = C.c_int(), C.c_int()
    width, height, border, depth = C.c_uint(), C.c_uint(), C.c_uint(), C.c_uint()
    if not xlib.XGetGeometry(
        display, window, C.byref(base), C.byref(x), C.byref(y),
        C.byref(width), C.byref(height), C.byref(border), C.byref(depth),
    ):
        return QRect()
    if not xlib.XTranslateCoordinates(display, window, root, 0, 0, C.byref(x), C.byref(y), C.byref(child)):
        return QRect()
    rectangle = QRect(x.value, y.value, width.value, height.value)
    frame = _property(xlib, display, window, b"_NET_FRAME_EXTENTS")
    if isinstance(frame, list) and len(frame) == 4 and all(value < 512 for value in frame):
        left, right, top, bottom = frame
        rectangle.adjust(-left, -top, right, bottom)
    return rectangle


def _qt_window_rect(rectangle: QRect, mapping) -> QRect:
    mapped = QRect()
    for logical, physical in mapping:
        part = rectangle.intersected(physical)
        if part.isEmpty():
            continue
        offset = part.topLeft() - physical.topLeft()
        scale_x = logical.width() / physical.width()
        scale_y = logical.height() / physical.height()
        left, top = round(offset.x() * scale_x), round(offset.y() * scale_y)
        right = round((offset.x() + part.width()) * scale_x)
        bottom = round((offset.y() + part.height()) * scale_y)
        mapped = mapped.united(
            QRect(logical.x() + left, logical.y() + top, max(1, right - left), max(1, bottom - top))
        )
    return mapped


def detect_windows() -> list[tuple[QRect, str]]:
    with _connection() as (xlib, display, root):
        if not display:
            return []
        mapping = _monitor_mapping(xlib, display, root, QGuiApplication.screens())
        windows = _property(xlib, display, root, b"_NET_CLIENT_LIST_STACKING")
        if not isinstance(windows, list):
            return []
        desktop = _property(xlib, display, root, b"_NET_CURRENT_DESKTOP")
        hidden = xlib.XInternAtom(display, b"_NET_WM_STATE_HIDDEN", True)
        result = []
        for window in reversed(windows):
            pid = _property(xlib, display, window, b"_NET_WM_PID")
            workspace = _property(xlib, display, window, b"_NET_WM_DESKTOP")
            if pid == [os.getpid()] or (desktop and workspace and workspace not in (desktop, [0xFFFFFFFF])):
                continue
            state = _property(xlib, display, window, b"_NET_WM_STATE")
            if isinstance(state, list) and hidden in state:
                continue
            rect = _qt_window_rect(_window_geometry(xlib, display, root, window), mapping)
            if not rect.isEmpty():
                title = _property(xlib, display, window, b"_NET_WM_NAME")
                result.append((rect, title.decode(errors="replace") if isinstance(title, bytes) else "窗口"))
        return result


def active_window() -> QRect | None:
    with _connection() as (xlib, display, root):
        if not display:
            return None
        active = _property(xlib, display, root, b"_NET_ACTIVE_WINDOW")
        window = _window_type(active[0] if isinstance(active, list) and active else 0)
        if not window.value:
            revert = C.c_int()
            xlib.XGetInputFocus(display, C.byref(window), C.byref(revert))
        if window.value in (0, 1, root):
            return None
        mapping = _monitor_mapping(xlib, display, root, QGuiApplication.screens())
        rect = _qt_window_rect(_window_geometry(xlib, display, root, window), mapping)
        return rect if not rect.isEmpty() else None
