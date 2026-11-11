"""Desktop frames in Qt global coordinates, with native-resolution crops."""

from __future__ import annotations

from dataclasses import dataclass
import os
import sys

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter


class CaptureError(RuntimeError):
    """A desktop capture failed and can be retried after resolving its cause."""


@dataclass(frozen=True)
class ScreenFrame:
    geometry: QRect
    image: QImage

    @property
    def scale(self) -> tuple[float, float]:
        return (
            self.image.width() / self.geometry.width(),
            self.image.height() / self.geometry.height(),
        )

    def native_rect(self, rect: QRect) -> QRect:
        offset = rect.topLeft() - self.geometry.topLeft()
        scale_x, scale_y = self.scale
        left, top = round(offset.x() * scale_x), round(offset.y() * scale_y)
        right = round((offset.x() + rect.width()) * scale_x)
        bottom = round((offset.y() + rect.height()) * scale_y)
        return QRect(left, top, max(1, right - left), max(1, bottom - top))


@dataclass(frozen=True)
class Snapshot:
    image: QImage
    geometry: QRect
    frames: tuple[ScreenFrame, ...] = ()

    def pixel_size(self, rect: QRect) -> QSize:
        """Return the output size without allocating a cropped image."""
        rect = rect.normalized().intersected(self.geometry)
        if rect.isEmpty():
            return QSize(0, 0)
        if not self.frames:
            return rect.size()
        for frame in self.frames:
            if frame.geometry.contains(rect):
                return frame.native_rect(rect).size()
        frames = tuple(frame for frame in self.frames if frame.geometry.intersects(rect))
        if not frames:
            return QSize(0, 0)
        return _scaled_size(rect, max(max(frame.scale) for frame in frames))

    def crop(self, rect: QRect) -> QImage:
        """Crop a global DIP rectangle using the captured physical pixels."""
        rect = rect.normalized().intersected(self.geometry)
        if rect.isEmpty():
            return QImage()
        if not self.frames:
            return self.image.copy(rect.translated(-self.geometry.topLeft()))

        for frame in self.frames:
            if frame.geometry.contains(rect):
                return frame.image.copy(frame.native_rect(rect))

        frames = tuple(frame for frame in self.frames if frame.geometry.intersects(rect))
        if not frames:
            return QImage()
        scale = max(max(frame.scale) for frame in frames)
        return _composite(frames, rect, scale)

    def color_at(self, point: QPoint) -> QColor:
        """Read the physical pixel at a Qt global DIP position."""
        for frame in self.frames:
            if frame.geometry.contains(point):
                native_point = frame.native_rect(QRect(point, QSize(1, 1))).topLeft()
                return frame.image.pixelColor(native_point)
        if self.geometry.contains(point):
            return self.image.pixelColor(point - self.geometry.topLeft())
        return QColor()


def _scaled_size(bounds: QRect, scale: float) -> QSize:
    return QSize(max(1, round(bounds.width() * scale)), max(1, round(bounds.height() * scale)))


def _composite(frames: tuple[ScreenFrame, ...], bounds: QRect, scale: float) -> QImage:
    image = QImage(_scaled_size(bounds, scale), QImage.Format.Format_ARGB32_Premultiplied)
    if image.isNull():
        raise CaptureError("截图范围过大，无法分配图像内存。")
    image.fill(0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, scale != 1)
    for frame in frames:
        visible = frame.geometry.intersected(bounds)
        if visible.isEmpty():
            continue
        offset = visible.topLeft() - bounds.topLeft()
        left, top = round(offset.x() * scale), round(offset.y() * scale)
        right = round((offset.x() + visible.width()) * scale)
        bottom = round((offset.y() + visible.height()) * scale)
        painter.drawImage(
            QRect(left, top, right - left, bottom - top),
            frame.image,
            frame.native_rect(visible),
        )
    painter.end()
    return image


def _backend() -> str:
    platform = QGuiApplication.platformName().lower()
    if platform.startswith("wayland") or (
        sys.platform.startswith("linux") and os.environ.get("XDG_SESSION_TYPE") == "wayland"
    ):
        return "wayland"
    if platform == "xcb":
        return "x11"
    if platform == "windows":
        return "windows"
    if platform == "cocoa":
        return "macos"
    return platform or "unavailable"


def capture_capabilities() -> dict[str, object]:
    backend = _backend()
    capabilities = {
        "backend": backend,
        "desktop": backend in {"windows", "macos", "x11", "wayland"},
        "window_detection": backend in {"windows", "macos", "x11"},
        "element_detection": backend in {"windows", "macos"},
        "cursor_capture": backend in {"windows", "macos", "x11"},
        "reason": (
            "当前 Wayland 会话由系统门户授权截图，窗口探测与光标捕获取决于桌面环境。"
            if backend == "wayland"
            else ""
        ),
    }
    if backend == "macos":
        from .capture_mac import permission_status

        screen_allowed, accessibility_allowed = permission_status()
        capabilities["screen_permission"] = screen_allowed
        capabilities["accessibility_permission"] = accessibility_allowed
        capabilities["element_detection"] = accessibility_allowed
        if not screen_allowed:
            capabilities["reason"] = "请在 macOS 系统设置中允许屏幕录制。"
        elif not accessibility_allowed:
            capabilities["reason"] = "请在 macOS 系统设置中允许辅助功能以识别控件。"
    return capabilities


def _normalized_image(image: QImage) -> QImage:
    image.setDevicePixelRatio(1)
    return image


def capture_desktop(include_cursor: bool = False) -> Snapshot:
    """Capture connected displays before the selection overlay is shown."""
    screens = QGuiApplication.screens()
    if not screens:
        raise CaptureError("没有可用的显示器。")

    backend = _backend()
    if backend == "wayland":
        from . import capture_wayland

        if include_cursor:
            raise CaptureError("Wayland 截图门户没有光标开关。请关闭光标捕获后重试。")
        frames = capture_wayland.capture_frames(screens)
    elif backend == "windows":
        from . import capture_win

        frames = capture_win.capture_frames(screens, include_cursor)
    elif backend == "macos":
        from . import capture_mac

        frames = capture_mac.capture_frames(screens, include_cursor)
    elif backend == "x11":
        from . import capture_x11

        frames = capture_x11.capture_frames(screens, include_cursor)
    else:
        raise CaptureError(f"当前会话无法访问桌面截图接口（{backend}）。")

    if not frames or any(frame.image.isNull() for frame in frames):
        raise CaptureError("没有读到显示器画面。请检查截图权限和显示器连接。")
    bounds = QRect()
    for frame in frames:
        bounds = bounds.united(frame.geometry)
    return Snapshot(_composite(frames, bounds, 1), bounds, frames)


def _window_backend():
    backend = _backend()
    if backend == "windows":
        from . import capture_win

        return capture_win
    if backend == "macos":
        from . import capture_mac

        return capture_mac
    if backend == "x11":
        from . import capture_x11

        return capture_x11
    return None


def detect_windows() -> list[tuple[QRect, str]]:
    """List visible windows from front to back, excluding the current process."""
    backend = _window_backend()
    return backend.detect_windows() if backend else []


def active_window() -> QRect | None:
    backend = _window_backend()
    return backend.active_window() if backend else None


def detect_elements(position: QPoint) -> list[tuple[QRect, str]]:
    """Hit-test a point and return accessible elements from smallest to largest."""
    backend = _window_backend()
    if backend and hasattr(backend, "detect_elements"):
        return backend.detect_elements(position)
    return []
