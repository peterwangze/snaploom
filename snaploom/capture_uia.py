"""A bounded Windows UI Automation search underneath the capture overlay."""

from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from time import monotonic
from uuid import UUID

from PySide6.QtCore import QRect


class _GUID(C.Structure):
    _fields_ = [("bytes", C.c_ubyte * 16)]


_AUTOMATION_CLASS = _GUID.from_buffer_copy(UUID("e22ad333-b25f-460c-83d0-0581107395c9").bytes_le)
_AUTOMATION_INTERFACE = _GUID.from_buffer_copy(UUID("34723aff-0c9d-49d0-9896-7ab52df8cd8a").bytes_le)
_POINTER = C.c_void_p
_OUT_POINTER = C.POINTER(_POINTER)
_ole = C.WinDLL("ole32")
_ole.CoInitializeEx.argtypes = [_POINTER, W.DWORD]
_ole.CoInitializeEx.restype = W.LONG
_ole.CoCreateInstance.argtypes = [C.POINTER(_GUID), _POINTER, W.DWORD, C.POINTER(_GUID), _OUT_POINTER]
_ole.CoCreateInstance.restype = W.LONG
_ole.CoUninitialize.argtypes = []
_ole.CoUninitialize.restype = None
_oleaut = C.WinDLL("oleaut32")
_oleaut.SysFreeString.argtypes = [_POINTER]
_oleaut.SysFreeString.restype = None

# Method slots in UIAutomationClient.h for IUIAutomation2, IUIAutomationElement
# and IUIAutomationTreeWalker. IUnknown::Release uses the same slot on all three.
_RELEASE = 2
_ELEMENT_FROM_HANDLE = 6
_CONTROL_VIEW_WALKER = 14
_SET_CONNECTION_TIMEOUT = 61
_SET_TRANSACTION_TIMEOUT = 63
_FIRST_CHILD = 4
_NEXT_SIBLING = 6
_CURRENT_NAME = 23
_CURRENT_BOUNDING_RECTANGLE = 43


def _invoke(interface, slot, result_type, arg_types, *args):
    vtable = C.cast(interface, C.POINTER(C.POINTER(_POINTER))).contents
    method = C.WINFUNCTYPE(result_type, _POINTER, *arg_types)(vtable[slot])
    return method(interface, *args)


def _release(interface):
    if interface:
        _invoke(interface, _RELEASE, W.ULONG, ())


def _interface_method(interface, slot, arg_types=(), *args):
    result = _POINTER()
    status = _invoke(interface, slot, W.LONG, (*arg_types, _OUT_POINTER), *args, C.byref(result))
    return result if status >= 0 and result.value else None


def _geometry(element) -> QRect:
    rect = W.RECT()
    status = _invoke(
        element, _CURRENT_BOUNDING_RECTANGLE, W.LONG, (C.POINTER(W.RECT),), C.byref(rect)
    )
    if status < 0:
        return QRect()
    return QRect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def _name(element) -> str:
    value = _interface_method(element, _CURRENT_NAME)
    if not value:
        return ""
    try:
        return C.wstring_at(value)
    finally:
        _oleaut.SysFreeString(value)


def _smallest_child(walker, parent, position, deadline):
    child = _interface_method(walker, _FIRST_CHILD, (_POINTER,), parent)
    best = None
    best_area = float("inf")
    try:
        for _ in range(96):
            if not child or monotonic() >= deadline:
                break
            rect = _geometry(child)
            area = rect.width() * rect.height()
            next_child = _interface_method(walker, _NEXT_SIBLING, (_POINTER,), child)
            if rect.contains(position.x, position.y) and 0 < area < best_area:
                _release(best)
                best, best_area = child, area
            else:
                _release(child)
            child = next_child
        return best
    finally:
        _release(child)


def elements(window, point) -> list[tuple[QRect, str]]:
    """Find a native window's accessible descendants covering a physical point."""
    if _ole.CoInitializeEx(None, 0) < 0:
        return []
    automation, walker, current = _POINTER(), None, None
    try:
        if _ole.CoCreateInstance(
            C.byref(_AUTOMATION_CLASS), None, 1, C.byref(_AUTOMATION_INTERFACE), C.byref(automation)
        ) < 0:
            return []
        _invoke(automation, _SET_CONNECTION_TIMEOUT, W.LONG, (W.DWORD,), 150)
        _invoke(automation, _SET_TRANSACTION_TIMEOUT, W.LONG, (W.DWORD,), 150)
        walker = _interface_method(automation, _CONTROL_VIEW_WALKER)
        current = _interface_method(automation, _ELEMENT_FROM_HANDLE, (W.HWND,), window)
        if not walker or not current:
            return []
        result = []
        deadline = monotonic() + 0.18
        for _ in range(20):
            if not current or monotonic() >= deadline:
                break
            rect = _geometry(current)
            if rect.contains(point.x, point.y) and (not result or rect != result[-1][0]):
                result.append((rect, _name(current)))
            child = _smallest_child(walker, current, point, deadline)
            _release(current)
            current = child
        return list(reversed(result))
    finally:
        _release(current)
        _release(walker)
        _release(automation)
        _ole.CoUninitialize()
