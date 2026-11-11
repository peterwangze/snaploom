"""Floating references and their presentation controls."""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QGuiApplication, QImage, QPainter, QTransform
from PySide6.QtWidgets import QApplication, QColorDialog, QInputDialog, QMenu, QWidget


class PinWindow(QWidget):
    changed = Signal()
    dismissed = Signal(bool)
    action_requested = Signal(str)

    def __init__(self, image: QImage, state, translate):
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        super().__init__(None, flags)
        self.tr = translate
        self.image = image.copy()
        self.view = QImage()
        self.state = {
            "group": "Default", "opacity": 100, "zoom": 1.0, "rotation": 0,
            "horizontal": False, "vertical": False, "gray": False, "invert": False,
            "click_through": False, "top": True, "thumbnail": False, "background": "",
            "visible": True, "x": QCursor.pos().x(), "y": QCursor.pos().y(),
        } | state
        self.drag_position = None
        self.drag_origin = None
        self.resize_origin = None
        self.track_pick = False
        self.thumbnail_crop = QRect(*self.state.get("thumbnail_crop", [0, 0, 0, 0]))
        self.drag_select = None
        self.selection_rect = QRect()
        self.right_drag = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setWindowTitle(self.tr("Pinned reference", "贴图"))
        self.setAccessibleName(self.windowTitle())
        self.timer = QTimer(self, interval=180, singleShot=True)
        self.timer.timeout.connect(self.changed)
        self.apply_state()
        self.move(self.state["x"], self.state["y"])

    def apply_state(self):
        transform = QTransform().rotate(self.state["rotation"])
        source = self.image
        if self.state.get("annotations"):
            from .editor import Annotation, render_annotations

            annotations = [Annotation(**(item | {"points": tuple(map(tuple, item["points"]))}))
                           for item in self.state["annotations"]]
            source = render_annotations(source, annotations)
        rendered = source.transformed(transform, Qt.TransformationMode.SmoothTransformation)
        if self.state["horizontal"] or self.state["vertical"]:
            rendered = rendered.flipped(
                (Qt.Orientation.Horizontal if self.state["horizontal"] else Qt.Orientation(0))
                | (Qt.Orientation.Vertical if self.state["vertical"] else Qt.Orientation(0))
            )
        if self.state["gray"]:
            gray = rendered.convertToFormat(QImage.Format.Format_Grayscale8)
            if rendered.hasAlphaChannel():
                alpha = rendered.convertToFormat(QImage.Format.Format_RGBA8888)
                rendered = gray.convertToFormat(QImage.Format.Format_RGBA8888)
                from numpy import frombuffer, uint8

                frombuffer(rendered.bits(), dtype=uint8)[3::4] = frombuffer(alpha.constBits(), dtype=uint8)[3::4]
            else:
                rendered = gray
        if self.state["invert"]:
            rendered.invertPixels()
        self.view = rendered
        self.update_window_flags()
        self.update_size()

    def update_window_flags(self):
        was_visible = self.isVisible()
        position = self.pos()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.state["top"])
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, self.state["click_through"])
        self.setWindowOpacity(self.state["opacity"] / 100)
        if was_visible and not self.isVisible():
            self.move(position)
            self.show()

    def display_image(self):
        if self.state["thumbnail"] and not self.thumbnail_crop.isEmpty():
            return self.view.copy(self.thumbnail_crop.intersected(self.view.rect()))
        return self.view

    def update_size(self):
        size = self.display_image().size() / max(1.0, self.view.devicePixelRatio())
        factor = self.state["zoom"]
        if self.state["thumbnail"]:
            factor = min(factor, 132 / max(1, size.width(), size.height()))
        self.resize(QSize(max(20, min(8192, round(size.width() * factor))),
                          max(20, min(8192, round(size.height() * factor)))))
        self.update()

    def recover_position(self):
        for screen in QGuiApplication.screens():
            if self.frameGeometry().intersected(screen.availableGeometry()).width() >= 20 and (
                self.frameGeometry().intersected(screen.availableGeometry()).height() >= 20
            ):
                return
        bounds = QGuiApplication.primaryScreen().availableGeometry()
        self.move(bounds.topLeft() + QPoint(24, 40))

    def set_state(self, key, value):
        self.state[key] = value
        if key == "zoom" or key == "thumbnail":
            self.update_size()
        elif key in {"opacity", "click_through", "top"}:
            self.update_window_flags()
        elif key != "background":
            self.apply_state()
        self.update()
        self.timer.start()

    def replace(self, image):
        self.image = image.copy()
        for key in ("rotation", "horizontal", "vertical", "gray", "invert", "thumbnail"):
            self.state[key] = 0 if key == "rotation" else False
        self.apply_state()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        background = self.state["background"]
        if background:
            painter.fillRect(self.rect(), QColor(background))
        painter.drawImage(self.rect(), self.display_image())
        painter.setPen(QColor(0, 0, 0, 100))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        if self.right_drag and not self.selection_rect.isEmpty():
            painter.fillRect(self.selection_rect, QColor(76, 163, 255, 72))
            painter.setPen(QColor("#4ca3ff"))
            painter.drawRect(self.selection_rect)
        if self.track_pick:
            point = self.mapFromGlobal(QCursor.pos())
            image_point = self.image_point(point)
            if self.display_image().rect().contains(image_point):
                image = self.display_image()
                color = image.pixelColor(image_point).name().upper()
                tile = QRect(point + QPoint(16, 12), QSize(116, 116))
                tile.moveLeft(max(0, min(tile.x(), self.width() - tile.width())))
                tile.moveTop(max(0, min(tile.y(), self.height() - tile.height())))
                sample = QRect(image_point - QPoint(5, 5), QSize(11, 11)).intersected(image.rect())
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                painter.fillRect(tile, QColor("#13253b"))
                painter.drawImage(tile.adjusted(3, 3, -3, -23), image, sample)
                painter.setPen(QColor("white"))
                painter.drawText(tile.adjusted(5, 92, -5, -3), Qt.AlignmentFlag.AlignCenter, color)

    def image_point(self, point):
        image = self.display_image()
        return QPoint(
            math.floor(point.x() * image.width() / max(1, self.width())),
            math.floor(point.y() * image.height() / max(1, self.height())),
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint()
            self.drag_origin = self.pos()
            point = event.position().toPoint()
            if point.x() >= self.width() - 14 and point.y() >= self.height() - 14:
                self.resize_origin = self.state["zoom"]
        elif event.button() == Qt.MouseButton.MiddleButton:
            self.reset()
        elif event.button() == Qt.MouseButton.RightButton and not self.state["thumbnail"]:
            self.drag_select = event.position().toPoint()

    def mouseMoveEvent(self, event):
        if self.drag_position is not None:
            delta = event.globalPosition().toPoint() - self.drag_position
            if self.resize_origin is None:
                if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    delta = QPoint(delta.x(), 0) if abs(delta.x()) >= abs(delta.y()) else QPoint(0, delta.y())
                self.move(self.drag_origin + delta)
            else:
                native_width = max(1, self.display_image().width() / self.view.devicePixelRatio())
                self.zoom(self.resize_origin + delta.x() / native_width)
        elif self.drag_select is not None:
            point = event.position().toPoint()
            if (point - self.drag_select).manhattanLength() > 8:
                self.right_drag = True
                self.selection_rect = QRect(self.drag_select, point).normalized().intersected(self.rect())
                self.update()
        elif self.track_pick:
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = None
            self.resize_origin = None
        elif event.button() == Qt.MouseButton.RightButton and self.drag_select is not None:
            if self.right_drag and not self.selection_rect.isEmpty():
                self.thumbnail_crop = QRect(self.image_point(self.selection_rect.topLeft()),
                                             self.image_point(self.selection_rect.bottomRight())).normalized()
                self.state["thumbnail_crop"] = self.thumbnail_crop.getRect()
                self.set_state("thumbnail", True)
            self.drag_select = None
            self.selection_rect = QRect()
            QTimer.singleShot(100, self.finish_right_drag)

    def finish_right_drag(self):
        self.right_drag = False
        self.update()

    def moveEvent(self, event):
        point = event.pos()
        self.state["x"], self.state["y"] = point.x(), point.y()
        self.timer.start()
        super().moveEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.set_state("thumbnail", not self.state["thumbnail"])
        else:
            self.dismissed.emit(False)

    def wheelEvent(self, event):
        step = 1 if event.angleDelta().y() > 0 else -1
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.set_state("opacity", min(100, max(10, self.state["opacity"] + step * 5)))
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.set_state("rotation", (self.state["rotation"] + step * 90) % 360)
        else:
            self.zoom(self.state["zoom"] * (1.12 ** step))

    def zoom(self, factor):
        self.set_state("zoom", min(8.0, max(0.06, factor)))

    def reset(self):
        self.state.update({"zoom": 1.0, "opacity": 100, "thumbnail": False})
        self.update_window_flags()
        self.update_size()
        self.timer.start()

    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()
        if key == Qt.Key.Key_Escape:
            self.dismissed.emit(bool(modifiers & Qt.KeyboardModifier.ShiftModifier))
        elif key == Qt.Key.Key_Alt:
            self.track_pick = True
            self.update()
        elif key == Qt.Key.Key_C and self.track_pick:
            image = self.display_image()
            point = self.image_point(self.mapFromGlobal(QCursor.pos()))
            if image.rect().contains(point):
                QApplication.clipboard().setText(image.pixelColor(point).name().upper())
        elif key in {Qt.Key.Key_Plus, Qt.Key.Key_Equal, Qt.Key.Key_Minus}:
            step = -1 if key == Qt.Key.Key_Minus else 1
            if modifiers & Qt.KeyboardModifier.ControlModifier:
                self.set_state("opacity", min(100, max(10, self.state["opacity"] + step * 5)))
            else:
                self.zoom(self.state["zoom"] * 1.12 ** step)
        elif key in {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down}:
            delta = {
                Qt.Key.Key_Left: QPoint(-1, 0), Qt.Key.Key_Right: QPoint(1, 0),
                Qt.Key.Key_Up: QPoint(0, -1), Qt.Key.Key_Down: QPoint(0, 1),
            }[key]
            self.move(self.pos() + delta * (10 if modifiers & Qt.KeyboardModifier.ShiftModifier else 1))
        elif modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            action = {Qt.Key.Key_C: "copy", Qt.Key.Key_S: "save", Qt.Key.Key_W: "hide",
                      Qt.Key.Key_E: "annotate"}.get(key)
            if action:
                self.action_requested.emit(action)
        else:
            super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Alt:
            self.track_pick = False
            self.update()
        super().keyReleaseEvent(event)

    def closeEvent(self, event):
        event.ignore()
        self.dismissed.emit(False)

    def contextMenuEvent(self, event):
        if self.right_drag:
            return
        menu = QMenu(self)
        self.populate_menu(menu)
        menu.exec(event.globalPos())

    def populate_menu(self, menu):
        entries = (
            ("copy", "Copy image", "复制图像"), ("save", "Save as…", "另存为…"),
            ("annotate", "Annotate…", "标注…"), ("crop", "Crop…", "裁剪…"), ("print", "Print…", "打印…"),
            ("ocr", "Extract text…", "识别文字…"), ("barcode", "Read QR or barcode…", "识别二维码或条码…"),
            ("group", "Move to group…", "移至分组…"),
        )
        for action, en, zh in entries:
            menu.addAction(self.tr(en, zh), lambda checked=False, selected=action: self.action_requested.emit(selected))
        menu.addSeparator()
        view = menu.addMenu(self.tr("Appearance", "外观"))
        for key, en, zh in (
            ("top", "Always on top", "始终置顶"), ("click_through", "Click through", "鼠标穿透"),
            ("thumbnail", "Thumbnail", "缩略图"), ("horizontal", "Flip horizontally", "水平翻转"),
            ("vertical", "Flip vertically", "垂直翻转"), ("gray", "Grayscale", "灰度"),
            ("invert", "Invert colors", "反色"),
        ):
            action = QAction(self.tr(en, zh), view, checkable=True, checked=self.state[key])
            action.toggled.connect(lambda checked, field=key: self.set_state(field, checked))
            view.addAction(action)
        view.addAction(self.tr("Rotate clockwise", "顺时针旋转"), lambda: self.set_state("rotation", (self.state["rotation"] + 90) % 360))
        view.addAction(self.tr("Background color…", "背景颜色…"), self.background_dialog)
        view.addAction(self.tr("Display width…", "显示宽度…"), self.width_dialog)
        view.addAction(self.tr("Reset size and opacity", "重置尺寸与透明度"), self.reset)
        menu.addAction(self.tr("Hide", "隐藏"), lambda: self.dismissed.emit(False))
        menu.addAction(self.tr("Delete pin", "删除贴图"), lambda: self.dismissed.emit(True))

    def width_dialog(self):
        width, ok = QInputDialog.getInt(self, self.tr("Display size", "显示尺寸"),
                                        self.tr("Width (logical pixels)", "宽度（逻辑像素）"),
                                        self.width(), 20, 8192)
        if ok:
            self.zoom(width * self.view.devicePixelRatio() / max(1, self.view.width()))

    def background_dialog(self):
        color = QColorDialog.getColor(QColor(self.state["background"] or "#ffffff"), self,
                                      options=QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if color.isValid():
            self.set_state("background", color.name(QColor.NameFormat.HexArgb))
