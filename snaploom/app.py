"""Desktop lifecycle, capture workflows and output routing."""

from __future__ import annotations

import os
import plistlib
import re
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import time
from base64 import b64encode
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QLocale, QLockFile, QMimeData, QObject, QPoint, QProcess, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QIcon, QImage
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QMenu, QMessageBox, QProgressBar, QPushButton, QSpinBox, QStyle, QSystemTrayIcon,
    QTextEdit, QVBoxLayout,
)

from . import VERSION
from .capture import disable_capture_window_transitions
from .commands import CommandServer, arguments, send_to_instance
from .hotkeys import Hotkeys
from .imaging import clipboard_images, parse_color, read_image, recognize, shadowed, stitch_images, text_image
from .pins import PinWindow
from .storage import Workspace, data_directory, pictures_directory, png_bytes, save_image
from .ui import Dashboard, Preferences


class Signals(QObject):
    recognition = Signal(int, str, str)
    super_snip = Signal(QRect)


def app_icon():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    for filename in ("packaging/assets/snaploom.png", "snaploom.png", "packaging/snaploom.svg"):
        asset = root / filename
        if asset.exists():
            return QIcon(str(asset))
    return QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)


class Application:
    def __init__(self, qt, directory=None, testing=False):
        self.qt = qt
        self.testing = testing
        self.workspace = Workspace(directory)
        self.locale = "zh-CN" if QLocale.system().language() == QLocale.Language.Chinese else "en"
        self.dashboard = Dashboard(self.tr)
        self.dashboard.setWindowIcon(app_icon())
        self.dashboard.action.connect(self.dashboard_action)
        self.dashboard.history_action.connect(self.history_action)
        self.dashboard.pin_action.connect(self.pin_action)
        self.dashboard.export_action.connect(self.saved_file_action)
        self.dashboard.pin_delete_requested.connect(self.request_pin_delete)
        self.dashboard.files_dropped.connect(self.open_files)
        self.dashboard.closing.connect(self.close_dashboard)
        self.editor = None
        self.scroll_dialog = None
        self.capturing = False
        self.capture_context = {}
        self.pin_windows = {}
        self.closed_pins = []
        self.last_region = next(iter(self.workspace.regions()), None)
        self.last_saved_file = None
        self.group = self.workspace.get("current_group") or "Default"
        self.previous_group = self.group
        self.hotkeys_enabled = True
        self.corners_enabled = True
        self.dialogs = set()
        self.network = QNetworkAccessManager()
        self.hotkeys = Hotkeys()
        self.hotkeys.triggered.connect(self.action)
        self.hotkeys.status.connect(self.status)
        self.signals = Signals()
        self.signals.recognition.connect(self.recognition_finished)
        self.signals.super_snip.connect(lambda rect: self.capture({"rect": rect.getRect()}))
        self.recognition_jobs = {}
        self.job_id = 0
        self.last_corner = None
        self.corner_since = 0
        self.mouse_hook = None
        self.corner_timer = QTimer(interval=180)
        self.corner_timer.timeout.connect(self.check_corner)
        self.tray = QSystemTrayIcon(app_icon())
        self.tray.setToolTip("Snaploom")
        self.tray.activated.connect(self.tray_clicked)
        self.tray_menu = QMenu()
        self.tray.setContextMenu(self.tray_menu)
        self.rebuild_tray()
        self.refresh_saved_files()
        if not testing:
            self.tray.show()
            self.configure_hotkeys()
            self.corner_timer.start()
            self.configure_super_snip()
        for pin_id, image, state in self.workspace.pins():
            if not image.isNull():
                self.open_pin(image, pin_id=pin_id, state=state, restore=True)
                if not state.get("visible", True):
                    self.closed_pins.append(pin_id)
        self.closed_pins.sort(key=lambda pin_id: (self.pin_windows[pin_id].state.get("hidden_at", 0), pin_id))
        self.update_lists()
        if not testing:
            qt.aboutToQuit.connect(self.shutdown)

    def tr(self, en, zh):
        return zh if self.locale.startswith("zh") else en

    def status(self, message):
        self.dashboard.status.setText(message)

    def notify(self, message, error=False, *, success=False):
        self.status(message)
        if success and not self.workspace.get("show_success_notifications"):
            return
        if self.tray.isVisible():
            self.tray.showMessage("Snaploom", message, QSystemTrayIcon.MessageIcon.Warning if error
                                  else QSystemTrayIcon.MessageIcon.Information, 3000)
        elif error and not self.dashboard.isVisible() and not self.testing:
            self.show_dashboard()

    def show_dashboard(self, tab=0):
        self.dashboard.tabs.setCurrentIndex(tab)
        self.dashboard.showNormal()
        self.dashboard.raise_()
        self.dashboard.activateWindow()

    def close_dashboard(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.qt.quit()

    def tray_clicked(self, reason):
        if reason in {QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick}:
            self.show_dashboard()
        elif reason == QSystemTrayIcon.ActivationReason.MiddleClick:
            self.action("paste")

    def rebuild_tray(self):
        self.tray_menu.clear()
        for action, en, zh in (
            ("dashboard", "Open Snaploom", "打开 Snaploom"), ("capture", "Capture region", "区域截图"),
            ("screen", "Current display", "当前屏幕"), ("full", "All displays", "所有屏幕"),
            ("active", "Active window", "当前窗口"), ("repeat", "Repeat last capture", "重复上次区域"),
            ("custom", "Size and delay…", "尺寸与延时…"), ("whiteboard", "Whiteboard…", "白板…"),
            ("paste", "Pin clipboard content", "悬浮剪贴板内容"), ("open", "Open image…", "打开图像…"),
        ):
            self.tray_menu.addAction(self.tr(en, zh), lambda checked=False, name=action: self.action(name))
        self.tray_save_location = self.tray_menu.addAction(
            self.tr("Open last save location", "打开最近保存位置"), self.open_save_location,
        )
        self.tray_save_location.setVisible(self.last_saved_file is not None)
        self.tray_menu.addAction(self.tr("Saved files", "已保存文件"), lambda: self.action("saved-files"))
        self.tray_menu.addSeparator()
        groups = self.tray_menu.addMenu(self.tr("Pin groups", "贴图分组"))
        for name in self.groups():
            item = groups.addAction(name)
            item.setCheckable(True)
            item.setChecked(name == self.group)
            item.triggered.connect(lambda checked=False, selected=name: self.switch_group(selected))
        groups.addSeparator()
        groups.addAction(self.tr("New group…", "新建分组…"), self.create_group)
        groups.addAction(self.tr("Manage groups…", "管理分组…"), self.manage_groups)
        for action, en, zh in (
            ("restore-last-pin", "Restore last hidden pin", "恢复最近隐藏的贴图"),
            ("toggle_pins", "Show or hide pins", "显示或隐藏贴图"),
            ("recover", "Recover off-screen pins", "找回屏幕外贴图"),
            ("no-click-through", "Disable click-through for all pins", "关闭所有贴图的鼠标穿透"),
            ("history", "History", "历史"), ("settings", "Settings", "设置"),
            ("help", "Help", "操作帮助"), ("quit", "Quit", "退出"),
        ):
            self.tray_menu.addAction(self.tr(en, zh), lambda checked=False, name=action: self.action(name))

    def configure_hotkeys(self, shortcuts=None):
        if shortcuts is None:
            shortcuts = self.workspace.get("hotkeys")
        previously_unavailable = self.hotkeys.unavailable
        unavailable = self.hotkeys.configure(shortcuts if self.hotkeys_enabled else {})
        if not self.hotkeys_enabled:
            self.dashboard.shortcuts.setText(self.tr("Global shortcuts are paused.", "全局快捷键已暂停。"))
            return unavailable
        labels = [
            f'{self.tr("Capture", "截图")}  {shortcuts.get("capture") or "—"}',
            f'{self.tr("Pin clipboard", "悬浮剪贴板")}  {shortcuts.get("paste") or "—"}',
            f'{self.tr("Hide/show pins", "显示／隐藏贴图")}  {shortcuts.get("toggle_pins") or "—"}',
        ]
        if unavailable:
            details = []
            for action in unavailable:
                error = self.hotkeys.errors.get(action)
                reason = {
                    1409: self.tr("already in use", "已被占用"),
                    "invalid": self.tr("invalid or duplicate", "无效或重复"),
                }.get(error, self.tr("unavailable", "不可用"))
                details.append(f"{shortcuts[action]} ({reason})")
            labels.append(self.tr("Change in Settings: ", "请在设置中修改：") + ", ".join(details))
            self.status(self.tr("Some global shortcuts could not be registered.", "部分全局快捷键注册失败。"))
        elif previously_unavailable:
            self.status(self.tr("Global shortcuts are ready.", "全局快捷键已就绪。"))
        self.dashboard.shortcuts.setText("   ·   ".join(labels))
        return unavailable

    def apply_hotkeys(self, shortcuts):
        previous = self.workspace.get("hotkeys")
        if not self.hotkeys_enabled or (shortcuts == previous and not self.hotkeys.unavailable):
            return None
        unavailable = self.configure_hotkeys(shortcuts)
        for action in unavailable:
            if shortcuts[action] != previous.get(action) and self.hotkeys.errors.get(action) == 1409:
                self.configure_hotkeys(previous)
                key = shortcuts[action]
                return action, self.tr(f"{key} is already in use. Choose a different key or cancel to keep the current shortcuts.",
                                       f"{key} 已被占用。请选择其他按键，或取消以保留当前快捷键。")
        return None

    def dashboard_action(self, action):
        if action == "paste":
            frame = self.dashboard.frameGeometry()
            available = self.dashboard.screen().availableGeometry()
            if available.right() - frame.right() >= 240:
                position = frame.topRight() + QPoint(16, 0)
            else:
                position = self.dashboard.mapToGlobal(QPoint(24, self.dashboard.height() * 2 // 3))
                position.setX(max(available.left(), min(position.x(), available.right() - 120)))
                position.setY(max(available.top(), min(position.y(), available.bottom() - 64)))
            self.paste(position)
        else:
            self.action(action)

    def action(self, action):
        modes = {
            "capture": {}, "screen": {"active_screen": True}, "full": {"full": True},
            "active": {"active_window": True}, "repeat": {"last": True}, "custom": {"custom": True},
            "whiteboard": {"whiteboard": True},
        }
        if action in modes:
            self.capture(modes[action])
        elif action == "paste":
            self.paste()
        elif action == "restore-last-pin":
            self.restore_last_pin()
        elif action == "open":
            files, _ = QFileDialog.getOpenFileNames(self.dashboard, self.tr("Open image", "打开图像"), "",
                                                   self.tr("Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tiff *.svg)", "图像 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tiff *.svg)"))
            self.open_files(files)
        elif action == "open-save-location":
            self.open_save_location()
        elif action == "saved-files":
            self.show_dashboard(self.dashboard.tabs.indexOf(self.dashboard.exports_page))
        elif action == "clear-saved-files":
            self.clear_saved_files()
        elif action == "toggle_pins":
            self.toggle_pins()
        elif action == "click_through":
            self.toggle_click_through()
        elif action == "no-click-through":
            self.disable_click_through()
        elif action == "recover":
            self.recover_pins()
        elif action == "manage-groups":
            self.manage_groups()
        elif action == "dashboard":
            self.show_dashboard()
        elif action == "history":
            self.show_dashboard(1)
        elif action == "settings":
            self.settings()
        elif action == "help":
            self.help()
        elif action == "quit":
            self.qt.quit()

    def capture(self, options, done=None):
        if self.scroll_dialog is not None:
            message = self.tr("Finish or cancel the scrolling capture to start a new screenshot.",
                              "请先完成或取消滚动截图，再开始新的截图。")
            self.status(message)
            self.raise_scroll_capture()
            if done:
                done(code=1, error=message)
            return
        if self.editor is not None or self.capturing:
            self.failure(self.tr("Finish the current capture first.", "请先完成当前截图。"), done)
            return
        self.capturing = True
        whiteboard = options.get("whiteboard", False)
        if options.get("custom") or (whiteboard and not options.get("size") and not options.get("color")):
            custom = self.custom_region(whiteboard=whiteboard)
            if custom is None:
                self.capturing = False
                if done:
                    done(code=1, error=self.tr("Capture cancelled.", "已取消截图。"))
                return
            options = options | custom
        self.capture_context = {"options": options, "done": done, "dashboard": self.dashboard.isVisible()}
        self.dashboard.hide()
        self.capture_context["pins"] = self.hide_capture_pins()
        delay = float(options.get("delay", 0))
        if delay:
            self.status(self.tr(f"Capturing in {delay:g} seconds", f"{delay:g} 秒后截图"))
        QTimer.singleShot(max(105, int(delay * 1000)), self.start_capture)

    def custom_region(self, whiteboard=False):
        dialog = QDialog(self.dashboard if self.dashboard.isVisible() else None)
        dialog.setWindowTitle(self.tr("Whiteboard", "白板") if whiteboard else self.tr("Capture size and delay", "截图尺寸与延时"))
        layout = QFormLayout(dialog)
        point = QCursor.pos()
        specs = []
        max_width, max_height = 16384, 16384
        if whiteboard:
            geometry = (self.qt.screenAt(point) or self.qt.primaryScreen()).geometry()
            max_width, max_height = geometry.width(), geometry.height()
        else:
            specs.extend((("X", "X", point.x(), -65536, 65536), ("Y", "Y", point.y(), -65536, 65536)))
        specs.extend((
            ("Width (logical pixels)", "宽度（逻辑像素）", 800, 1, max_width),
            ("Height (logical pixels)", "高度（逻辑像素）", 520, 1, max_height),
        ))
        fields = []
        for en, zh, value, lower, upper in specs:
            field = QSpinBox()
            field.setRange(lower, upper)
            field.setValue(value)
            layout.addRow(self.tr(en, zh), field)
            fields.append(field)
        if whiteboard:
            transparent = QCheckBox(self.tr("Transparent background", "透明背景"))
            layout.addRow(transparent)
        else:
            delay = QDoubleSpinBox()
            delay.setRange(0, 600)
            layout.addRow(self.tr("Delay (seconds)", "延时（秒）"), delay)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        disable_capture_window_transitions(dialog)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        values = [field.value() for field in fields]
        if whiteboard:
            return {"size": values, "transparent": transparent.isChecked()}
        return {"rect": values, "size": values[2:], "delay": delay.value()}

    def start_capture(self):
        context = self.capture_context
        options = context["options"]
        try:
            from .capture import Snapshot, active_window, capture_desktop, detect_elements, detect_windows
            from .editor import CaptureEditor, render_annotations

            if options.get("active_window"):
                target = active_window()
                if target is not None:
                    options = dict(options) | {"rect": target.getRect()}
                else:
                    self.status(self.tr("Select the window to capture.", "请选择要截取的窗口。"))
            if options.get("whiteboard"):
                geometry = (self.qt.screenAt(QCursor.pos()) or self.qt.primaryScreen()).geometry()
                width, height = options.get("size") or (800, 520)
                board = QImage(min(width, geometry.width()), min(height, geometry.height()), QImage.Format.Format_ARGB32)
                color = QColor(options.get("color") or "white")
                board.fill(Qt.GlobalColor.transparent if options.get("transparent") else color)
                bounds = QRect(geometry.center() - QPoint(board.width() // 2, board.height() // 2), board.size())
                snapshot = Snapshot(board, bounds)
                selection = bounds
            else:
                snapshot = capture_desktop(include_cursor=options.get("cursor") or self.workspace.get("include_cursor"))
                selection = self.capture_selection(options, snapshot.geometry)
            if selection is not None and selection.isEmpty():
                raise ValueError(self.tr("The requested region is outside the desktop.", "请求的区域不在桌面范围内。"))
            radius = options.get("corner_radius") or self.workspace.get("rounded_corners")
            if selection is not None and options.get("output") and not options.get("hold"):
                image = snapshot.crop(selection)
                if radius and not image.isNull():
                    image = render_annotations(image, (), corner_radius=radius, logical_size=selection.size())
                self.capture_completed("output", image, selection)
                return
            suggest_windows = self.workspace.get("detect_windows") and not options.get("whiteboard")
            requested_output = options.get("output") or options.get("recognition")
            editor_options = self._annotation_options() | {
                "locale": self.locale, "show_guides": self.workspace.get("show_grid"),
                "show_magnifier": self.workspace.get("show_magnifier"),
                "auto_detect": suggest_windows,
                "color_format": self.workspace.get("color_format").lower(),
                "corner_radius": radius,
                "custom_outputs": {name: name for name in self.workspace.get("custom_commands")},
                "default_action": "output" if requested_output else "copy",
                "requested_outputs": options.get("output", ()),
                "aspect_ratio": (tuple(options["size"]) if options.get("size") and options.get("keep_aspect_ratio") else None),
                "detect_elements": detect_elements if suggest_windows else None,
                "allow_refresh": not options.get("whiteboard"),
                "allow_scroll": not options.get("whiteboard"),
                "color_only": options.get("color_only", False),
            }
            if options.get("recognition") == "barcode-copy":
                editor_options["requested_output_label"] = self.tr("Scan and copy code", "识别并复制条码")
            windows = detect_windows() if suggest_windows else ()
            self.editor = CaptureEditor(snapshot, selection=selection, windows=windows, options=editor_options,
                                        history=self.workspace.regions())
            self._connect_annotation_settings(self.editor)
            self.editor.completed.connect(self.capture_completed)
            self.editor.cancelled.connect(self.capture_cancelled)
            self.editor.refresh_requested.connect(self.refresh_capture)
            if hasattr(self.editor, "custom_output"):
                self.editor.custom_output.connect(self.custom_output)
            if hasattr(self.editor, "color_copied"):
                self.editor.color_copied.connect(self.color_copied)
            self.editor.show()
            self.editor.activateWindow()
            self.editor.setFocus()
        except (OSError, RuntimeError, ValueError, ImportError, TypeError) as error:
            self.release_capture()
            self.failure(str(error), context.get("done"))

    def _annotation_options(self):
        return {
            "annotation_width": self.workspace.get("annotation_width"),
            "annotation_styles": self.workspace.get("annotation_styles")
            if self.workspace.get("remember_annotation_styles") else {},
            "shortcuts": self.workspace.get("editor_shortcuts"),
        }

    def _connect_annotation_settings(self, editor):
        editor.annotation_width_changed.connect(lambda width: self.workspace.set("annotation_width", width))
        editor.annotation_style_changed.connect(self._remember_annotation_style)

    def _remember_annotation_style(self, tool, style):
        if self.workspace.get("remember_annotation_styles"):
            saved = self.workspace.get("annotation_styles")
            self.workspace.set("annotation_styles", saved | {tool: style})

    def capture_selection(self, options, bounds):
        if options.get("rect"):
            return QRect(*options["rect"]).intersected(bounds)
        if options.get("full"):
            return bounds
        if options.get("active_screen"):
            return (self.qt.screenAt(QCursor.pos()) or self.qt.primaryScreen()).geometry().intersected(bounds)
        if options.get("last"):
            if self.last_region is None:
                self.status(self.tr("Select a region for your first capture.", "请先选择一个区域截图。"))
                return None
            return self.last_region.intersected(bounds)
        if options.get("size"):
            point = QCursor.pos()
            width, height = options["size"]
            return QRect(point - QPoint(width // 2, height // 2), QSize(width, height)).intersected(bounds)
        return None

    def refresh_capture(self):
        from .capture import CaptureError, capture_desktop, detect_windows

        if not self.editor or not self.editor.isVisible() or self.capture_context["options"].get("whiteboard"):
            return
        editor = self.editor
        include_cursor = self.capture_context["options"].get("cursor") or self.workspace.get("include_cursor")
        editor.hide()

        def refresh():
            if self.editor is not editor:
                return
            try:
                snapshot = capture_desktop(include_cursor=include_cursor)
                windows = detect_windows() if editor.options["auto_detect"] else ()
                editor.set_snapshot(snapshot, windows)
            except (CaptureError, RuntimeError) as error:
                self.notify(str(error), error=True)
            finally:
                editor.show()
                editor.activateWindow()

        QTimer.singleShot(120, refresh)

    def hide_capture_pins(self):
        hidden = []
        if self.workspace.get("hide_pins_on_capture"):
            for pin in self.pin_windows.values():
                if pin.isVisible():
                    disable_capture_window_transitions(pin)
                    pin.hide()
                    hidden.append(pin)
        return hidden

    def restore_capture_pins(self, pins):
        for pin in pins:
            if pin in self.pin_windows.values() and pin.state["group"] == self.group and pin.state["visible"]:
                pin.show()

    def release_capture(self, restore_dashboard=True, *, restore_pins=True):
        context = self.capture_context
        self.capture_context = {}
        editor, self.editor = self.editor, None
        if editor:
            editor.hide()
            editor.deleteLater()
        if restore_pins:
            self.restore_capture_pins(context.get("pins", []))
        if restore_dashboard and (context.get("dashboard") or not QSystemTrayIcon.isSystemTrayAvailable()):
            self.dashboard.show()
        self.capturing = False
        return context

    def capture_cancelled(self):
        context = self.release_capture()
        if context.get("done"):
            context["done"](code=1, error="Capture cancelled.")

    def color_copied(self, value):
        self.status(self.tr(f"Copied {value}", f"已复制 {value}"))
        if self.capture_context.get("options", {}).get("color_only"):
            context = self.release_capture()
            if context.get("done"):
                context["done"](code=0, text=value)

    def capture_completed(self, action, image, rect):
        options = self.capture_context.get("options", {})
        outputs = options.get("output") if action == "output" else None
        recognition = options.get("recognition")
        if image.isNull():
            context = self.release_capture()
            self.failure(self.tr("The captured image is empty.", "截图为空。"), context.get("done"))
            return
        shadow = options.get("shadow")
        if shadow is None:
            shadow = self.workspace.get("capture_shadow")
        if shadow and action not in {"ocr", "barcode", "scroll"}:
            try:
                image = shadowed(image)
            except (ValueError, RuntimeError) as error:
                self.notify(str(error), error=True)
        if self.editor is not None and not recognition and not outputs:
            if action in {"save", "quick-save", "copy-file", "print"}:
                self._complete_editor_output(action, image, rect)
                return
            if action in {"ocr", "barcode"}:
                self._recognize_editor_selection(image, action)
                return
        if action == "scroll" and not recognition and not outputs:
            context = self.release_capture(restore_dashboard=False, restore_pins=False)
            self.scroll_capture(image, rect, context.get("done"), restore_dashboard=context.get("dashboard", False),
                                hidden_pins=context.get("pins", []))
            return
        context = self.release_capture()
        done = context.get("done")
        self.last_region = QRect(rect)
        if recognition:
            self.output(image, recognition, rect, done)
            return
        if outputs:
            self.output_pipeline(image, rect, outputs, done)
            return
        if action == "output":
            action = "copy"
        self.output(image, action, rect, done)

    def _complete_editor_output(self, action, image, rect):
        editor = self.editor
        if action in {"save", "copy-file", "print"}:
            editor.hide()

        def restore_editor():
            if self.editor is editor:
                editor.show()
                editor.activateWindow()

        def show_error(message):
            if self.editor is editor:
                restore_editor()
                self.status(message)
                QMessageBox.warning(editor, self.tr("Output could not be completed", "无法完成输出"), message)

        def complete(code=0, text="", error=""):
            if self.editor is not editor:
                return
            if code:
                restore_editor()
                return
            context = self.release_capture()
            self.last_region = QRect(rect)
            done = context.get("done")
            try:
                history_action = "saved" if action == "save" else action
                self._record_output(image, rect, history_action, already_saved=action in {"save", "quick-save", "copy-file"})
            except (OSError, RuntimeError, ValueError) as error:
                self.failure(str(error), done)
                return
            if done:
                done(code=0, text=text)

        self.output(image, action, rect, complete, record=False, on_error=show_error)

    def _recognize_editor_selection(self, image, action):
        editor = self.editor
        title = self.tr("Recognize selection", "识别选区")
        if self.recognition_jobs:
            QMessageBox.information(editor, title, self.tr("Recognition is already running.", "正在识别，请稍候。"))
            return

        dialog = QDialog(editor)
        dialog.setWindowTitle(title)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(self.tr("Recognizing the selection…", "正在识别选区…")))
        progress = QProgressBar()
        progress.setRange(0, 0)
        layout.addWidget(progress)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        def complete(code=0, text="", error=""):
            if dialog not in self.dialogs or self.editor is not editor:
                return False
            dialog.accept()
            if code:
                QMessageBox.information(editor, title, error)
            return not code

        self.keep_dialog(dialog)
        self.start_recognition(image, action, complete, result_parent=editor)

    def custom_output(self, name, image, rect):
        config = self.workspace.get("custom_commands").get(name)
        path = None
        try:
            if not config:
                raise ValueError(self.tr("This output command is unavailable.", "此输出命令不可用。"))
            tokens = shlex.split(config, posix=os.name != "nt")
            if not tokens:
                raise ValueError(self.tr("The command is empty.", "输出命令为空。"))
            path = self.quick_save(image)
            argv = [part.strip('"').replace("{image}", str(path)) for part in tokens]
            subprocess.Popen(argv, shell=False, **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        except (OSError, ValueError) as error:
            if path is not None:
                self.report_saved_file(path, silent=True)
            if self.editor is not None:
                self.status(str(error))
                QMessageBox.warning(self.editor, self.tr("Output could not be completed", "无法完成输出"), str(error))
            else:
                self.failure(str(error), self.capture_context.get("done"))
            return
        context = self.release_capture()
        self.last_region = QRect(rect)
        self.report_saved_file(path)
        self.workspace.remember(image, rect, name)
        self.update_lists()
        if context.get("done"):
            context["done"](code=0, text=str(path))

    def failure(self, message, done=None):
        self.notify(message, error=True)
        if done:
            done(code=1, error=message)

    def copy_image(self, image):
        mime = QMimeData()
        mime.setImageData(image)
        png = QByteArray(png_bytes(image))
        mime.setData("image/png", png)
        if sys.platform == "win32":
            # Windows applications can request the registered PNG clipboard format by name.
            mime.setData('application/x-qt-windows-mime;value="PNG"', png)
        self.qt.clipboard().setMimeData(mime)

    def output_pipeline(self, image, rect, outputs, done):
        special = {"silent", "no-auto-save"}
        aliases = {"clipboard": "copy", "ocr_clipboard": "ocr-copy", "file-dialog": "save", "printer": "print"}
        known = {"pin", "copy", "quick-save", "ocr-copy", "save", "print", "success", "copy-file"}
        steps = []
        for item in outputs:
            if item in special:
                continue
            action = aliases.get(item, item)
            if action in known:
                steps.append((action, None))
            elif action.startswith("exec(") and action.endswith(")"):
                steps.append(("external", action[5:-1]))
            else:
                steps.append(("file", Path(action)))
        if not steps:
            steps.append(("success", None))
        order = ("file", "copy", "copy-file", "save", "print", "quick-save", "pin", "success", "external", "ocr-copy")
        steps.sort(key=lambda step: order.index(step[0]))
        silent = "silent" in outputs

        def execute(index, result=""):
            if index == len(steps):
                self.workspace.remember(image, rect, " / ".join(outputs))
                if self.workspace.get("auto_save") and "no-auto-save" not in outputs and not (
                    {action for action, _ in steps} & {"file", "quick-save", "save", "copy-file"}
                ):
                    try:
                        self.report_saved_file(self.quick_save(image), silent=True)
                    except OSError as error:
                        self.failure(str(error), done)
                        return
                self.update_lists()
                if done:
                    done(code=0, text=result)
                return
            action, target = steps[index]

            def following(code=0, text="", error=""):
                if code:
                    if done:
                        done(code=code, error=error)
                else:
                    execute(index + 1, text or result)

            self.output(image, action, rect, following, target, silent=silent, record=False, prompt_file=False)

        execute(0)

    def output(self, image, action, rect=None, done=None, target=None, *, silent=False, record=True, on_error=None,
               dialog_parent=None, prompt_file=True):
        rect = QRect(rect) if rect is not None else QRect()
        text = ""
        try:
            if action == "copy":
                self.copy_image(image)
                if not silent:
                    self.notify(self.tr("Image copied", "图像已复制"), success=True)
            elif action == "pin":
                if rect.isValid():
                    image = QImage(image)
                    image.setDevicePixelRatio(max(1.0, image.width() / max(1, rect.width())))
                self.open_pin(image, rect.topLeft() if rect.isValid() else None)
            elif action in {"save", "copy-file"}:
                if action == "copy-file" and not prompt_file:
                    target = self.quick_save(image)
                else:
                    title = self.tr("Save As and Copy File", "另存为并复制文件") if action == "copy-file" else None
                    target = self.save_dialog(image, parent=dialog_parent, title=title)
                if not target:
                    if done:
                        done(code=1, error="Save cancelled.")
                    return
                if action == "copy-file":
                    url = QUrl.fromLocalFile(str(target))
                    mime = QMimeData()
                    mime.setUrls([url])
                    if sys.platform.startswith("linux"):
                        mime.setData("x-special/gnome-copied-files", QByteArray(b"copy\n") + url.toEncoded())
                    self.qt.clipboard().setMimeData(mime)
            elif action in {"quick-save", "silent"}:
                target = self.quick_save(image)
            elif action == "file":
                target = save_image(image, target)
            elif action == "print":
                if not self.print_image(image):
                    if done:
                        done(code=1, error="Print cancelled.")
                    return
            elif action == "external":
                self.external(shlex.split(target, posix=os.name != "nt"))
                target = None
            elif action in {"ocr", "ocr-copy", "barcode", "barcode-copy"}:
                self.start_recognition(image, action, done, silent=silent)
                done = None
            elif action not in {"success", "saved"}:
                raise ValueError(f"Unknown output: {action}")
            if target:
                text = str(target)
                self.report_saved_file(target, copied_file=action == "copy-file", silent=silent or action == "silent")
                if action != "copy-file" and self.workspace.get("copy_after_save"):
                    self.copy_image(image)
            if record:
                self._record_output(image, rect, action, already_saved=bool(target))
        except (OSError, RuntimeError, ValueError) as error:
            if on_error is None:
                self.failure(str(error), done)
            else:
                message = str(error)
                on_error(message)
                if done:
                    done(code=1, error=message)
            return
        if done:
            done(code=0, text=text)

    def report_saved_file(self, path, *, copied_file=False, silent=False):
        path = Path(path)
        try:
            self.workspace.remember_export(path)
            self.refresh_saved_files()
        except (sqlite3.Error, OSError, RuntimeError):
            self.update_last_saved_file(path)
            if not silent:
                self.notify(self.tr(f"Saved to {path}. Could not update saved files.",
                                    f"已保存至 {path}。无法更新保存记录。"), error=True)
            return
        if not silent:
            if copied_file:
                message = self.tr(f"File copied to clipboard. Saved to {path}", f"文件已复制到剪贴板。保存位置为 {path}")
            else:
                message = self.tr(f"Saved to {path}", f"已保存至 {path}")
            self.notify(message, success=True)

    def refresh_saved_files(self):
        rows = self.workspace.recent_exports()
        self.dashboard.update_saved_files(rows)
        self.update_last_saved_file(rows[0][0] if rows else None)

    def update_last_saved_file(self, path):
        self.last_saved_file = path
        self.dashboard.save_location_button.setToolTip(str(path) if path is not None else "")
        self.dashboard.save_location_button.setVisible(path is not None)
        self.tray_save_location.setVisible(path is not None)

    def open_save_location(self):
        if self.last_saved_file is not None:
            self.saved_file_action("open", str(self.last_saved_file))

    def saved_file_action(self, action, path):
        if action == "copy":
            self.qt.clipboard().setText(path)
            self.status(self.tr("File path copied", "已复制文件路径"))
            return
        folder = Path(path).parent
        try:
            if folder.is_dir() and QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
                return
        except OSError:
            pass
        self.notify(self.tr(f"Could not open the save folder {folder}", f"无法打开保存位置 {folder}"), error=True)

    def clear_saved_files(self):
        answer = QMessageBox.question(
            self.dashboard, self.tr("Clear saved file records", "清空保存记录"),
            self.tr("Clear the list of saved files? The images will stay in their folders.",
                    "要清空保存记录吗？已保存的图片会保留在原来的文件夹中。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                self.workspace.clear_exports()
                self.refresh_saved_files()
            except sqlite3.Error:
                self.notify(self.tr("Could not clear the saved file records.", "无法清空保存记录。"), error=True)

    def _record_output(self, image, rect, action, *, already_saved):
        if not rect.isValid():
            return
        self.workspace.remember(image, rect, action)
        self.update_lists()
        if not already_saved and self.workspace.get("auto_save"):
            try:
                self.report_saved_file(self.quick_save(image), silent=True)
            except (OSError, RuntimeError, ValueError) as error:
                self.notify(self.tr(f"Automatic save failed. {error}", f"自动保存失败。{error}"), error=True)

    def print_image(self, image):
        from PySide6.QtGui import QPainter
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter

        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self.dashboard if self.dashboard.isVisible() else None)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return False
        painter = QPainter(printer)
        if not painter.isActive():
            raise OSError("Could not open the printer.")
        viewport = painter.viewport()
        size = image.size().scaled(viewport.size(), Qt.AspectRatioMode.KeepAspectRatio)
        point = viewport.center() - QPoint(size.width() // 2, size.height() // 2)
        painter.drawImage(QRect(point, size), image)
        painter.end()
        return True

    def quick_save(self, image):
        folder = Path(self.workspace.get("save_folder") or pictures_directory()).expanduser()
        ext = self.workspace.get("format")
        pattern = self.workspace.get("save_pattern") or "Snaploom_%Y-%m-%d_%H-%M-%S"
        title = self.tr("Screenshot", "截图")
        for variable, value in (("%title%", title), ("%width%", image.width()), ("%height%", image.height())):
            pattern = pattern.replace(variable, str(value))
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", datetime.now().strftime(pattern)).strip(". ")[:155] or "Snaploom"
        path = folder / f"{filename}.{ext}"
        for index in range(1, 10000):
            if not path.exists():
                return save_image(image, path)
            path = folder / f"{filename}_{index}.{ext}"
        raise OSError("Too many files use this filename pattern.")

    def save_dialog(self, image, *, parent=None, title=None):
        ext = self.workspace.get("format")
        folder = Path(self.workspace.get("save_folder") or pictures_directory()).expanduser()
        try:
            if not folder.is_dir():
                folder = next((candidate for candidate in (folder.parent, pictures_directory().parent)
                               if candidate.is_dir()), Path.home())
        except OSError:
            folder = Path.home()
        initial = folder / f"{datetime.now():Snaploom_%Y-%m-%d_%H-%M-%S}"
        filters = {"png": "PNG (*.png)", "jpg": "JPEG (*.jpg *.jpeg)", "webp": "WebP (*.webp)", "bmp": "Bitmap (*.bmp)"}
        owner = parent if parent is not None else self.dashboard if self.dashboard.isVisible() else None
        path, selected = QFileDialog.getSaveFileName(
            owner, title or self.tr("Save Image As", "另存图像"), str(initial),
            ";;".join(filters.values()), filters.get(ext, filters["png"]),
        )
        if not path:
            return None
        target = Path(path)
        if not target.suffix:
            suffix = next((extension for extension, label in filters.items() if label == selected), ext)
            target = target.with_suffix("." + suffix)
            if target.is_file():
                choice = QMessageBox.question(
                    owner, self.tr("Replace existing file?", "替换已有文件？"),
                    self.tr(f"Replace {target}?", f"要替换 {target} 吗？"),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
                )
                if choice != QMessageBox.StandardButton.Yes:
                    return None
        return save_image(image, target)

    def open_pin(self, image, position=None, pin_id=None, state=None, restore=False):
        state = {"group": self.group, "opacity": self.workspace.get("default_pin_opacity"),
                 "dpr": image.devicePixelRatio()} | (state or {})
        image = QImage(image)
        image.setDevicePixelRatio(state["dpr"])
        if position is not None:
            state.update({"x": position.x(), "y": position.y()})
        if not pin_id:
            pin_id = self.workspace.add_pin(image, state)
        window = PinWindow(image, state, self.tr)
        window.snap_targets = lambda: [other.frameGeometry() for other in self.pin_windows.values()
                                       if other is not window and other.isVisible()]
        self.pin_windows[pin_id] = window
        window.changed.connect(lambda: self.save_pin(pin_id))
        window.dismissed.connect(lambda destroy: self.request_pin_delete([pin_id], window)
                                 if destroy else self.pin_action("hide", pin_id))
        window.action_requested.connect(lambda action: self.pin_action(action, pin_id))
        if (state.get("visible", True) and state["group"] == self.group
                and (not restore or self.workspace.get("restore_pins"))):
            window.show()
        if not restore:
            window.recover_position()
            self.update_lists()
            self.rebuild_tray()
        return pin_id

    def save_pin(self, pin_id, include_image=False):
        window = self.pin_windows.get(pin_id)
        if window:
            self.workspace.update_pin(pin_id, window.state, window.image if include_image else None)

    def request_pin_delete(self, pin_ids, parent=None):
        ids = [pin_id for pin_id in pin_ids if pin_id in self.pin_windows]
        if not ids:
            return
        message = (self.tr("Permanently delete this pin?", "永久删除这张贴图？") if len(ids) == 1 else
                   self.tr(f"Permanently delete these {len(ids)} pins?", f"永久删除这 {len(ids)} 张贴图？"))
        result = QMessageBox.question(parent or self.dashboard, self.tr("Delete pins", "删除贴图"), message,
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if result == QMessageBox.StandardButton.Yes:
            for pin_id in ids:
                self.pin_action("delete", pin_id)

    def pin_action(self, action, pin_id):
        if action == "recover":
            self.recover_pins()
            return
        window = self.pin_windows.get(pin_id)
        if window is None:
            return
        if action == "solo":
            for item, peer in self.pin_windows.items():
                if item != pin_id and peer.state["group"] == window.state["group"]:
                    self.pin_action("hide", item)
            self.pin_action("show", pin_id)
            return
        if action in {"show", "delete"} and pin_id in self.closed_pins:
            self.closed_pins.remove(pin_id)
        if action == "delete":
            window.hide()
            del self.pin_windows[pin_id]
            self.workspace.delete_pin(pin_id)
            window.deleteLater()
            self.rebuild_tray()
        elif action == "hide":
            if not window.state["visible"]:
                return
            window.hide()
            window.state["visible"] = False
            window.state["hidden_at"] = time.time_ns()
            self.closed_pins.append(pin_id)
            self.save_pin(pin_id)
        elif action == "show":
            if window.state["group"] != self.group:
                self.switch_group(window.state["group"])
            window.state["visible"] = True
            window.recover_position()
            window.show()
            window.raise_()
            self.save_pin(pin_id)
        elif action == "group":
            groups = self.groups()
            group, ok = QInputDialog.getItem(self.dashboard if self.dashboard.isVisible() else window,
                                             self.tr("Pin group", "贴图分组"),
                                             self.tr("Move to or create a group", "移动到分组，或输入新分组"),
                                             groups, groups.index(window.state["group"]), True)
            group = group.strip()[:100]
            if ok and group and group != window.state["group"]:
                if group not in groups:
                    self.workspace.set("groups", [*groups, group])
                window.state["group"] = group
                window.setVisible(window.state["visible"] and window.state["group"] == self.group)
                self.save_pin(pin_id)
                self.rebuild_tray()
        elif action in {"annotate", "crop"}:
            from .editor import Annotation, ImageEditor

            cropped = action == "crop"
            annotations = () if cropped else tuple(
                Annotation(**(item | {"points": tuple(map(tuple, item["points"]))}))
                for item in window.state.get("annotations", [])
            )
            dialog = ImageEditor(window.view if cropped else window.image,
                                 parent=window, locale=self.locale, annotations=annotations,
                                 options=self._annotation_options())
            self._connect_annotation_settings(dialog.canvas)
            if cropped:
                dialog.activate_crop()
            dialog.applied.connect(lambda updated: self.update_pin_image(
                pin_id, dialog.result_base, [asdict(annotation) for annotation in dialog.annotations], cropped=cropped
            ))
            self.keep_dialog(dialog)
        elif action == "replace":
            mime = self.qt.clipboard().mimeData()
            images = clipboard_images(mime)
            if not images:
                self.notify(self.tr("Copy an image, text, or color before replacing a pin.",
                                    "请先复制图片、文字或色值，再替换贴图。"))
                return
            self.replace_pin_source(pin_id, images[0], self.clipboard_state(mime))
        elif action == "edit-color":
            current = QColor(window.state["color_card"])
            color = QColorDialog.getColor(current, window)
            if color.isValid():
                text = color.name().upper()
                self.replace_pin_source(pin_id, text_image(text), {"source_text": text, "color_card": text})
        else:
            self.output(window.view, action, dialog_parent=window if action == "save" else None)
        self.update_lists()

    def update_pin_image(self, pin_id, updated, annotations, *, cropped=False):
        window = self.pin_windows.get(pin_id)
        if window:
            updated.setDevicePixelRatio(window.state.get("dpr", 1))
            if cropped:
                self.replace_pin_source(pin_id, updated, {})
            else:
                window.state["annotations"] = annotations
                window.replace(updated, preserve_appearance=True)
                self.save_pin(pin_id, include_image=True)
            self.update_lists()

    def replace_pin_source(self, pin_id, image, state):
        window = self.pin_windows[pin_id]
        for key in ("source_text", "color_card", "animation", "paused", "annotations"):
            window.state.pop(key, None)
        window.state.update(state)
        window.state["dpr"] = image.devicePixelRatio()
        window.replace(image)
        window.load_animation()
        self.save_pin(pin_id, include_image=True)

    @staticmethod
    def clipboard_state(mime):
        if mime is not None and mime.hasText() and not (mime.hasImage() or mime.hasUrls()):
            text = mime.text()
            color = parse_color(text)
            return {"source_text": text, "color_card": color.name().upper() if color.isValid() else ""}
        return {}

    def groups(self):
        created = self.workspace.get("groups") or []
        available = ["Default", *created, self.group, *(win.state["group"] for win in self.pin_windows.values())]
        return list(dict.fromkeys(available))

    def create_group(self, name=None):
        if not isinstance(name, str):
            name, ok = QInputDialog.getText(self.dashboard, self.tr("New pin group", "新建贴图分组"),
                                            self.tr("Group name", "分组名称"))
            if not ok:
                return
        name = name.strip()[:100]
        if name:
            self.workspace.set("groups", list(dict.fromkeys([*self.groups(), name])))
            self.switch_group(name)

    def manage_groups(self):
        dialog = QDialog(self.dashboard if self.dashboard.isVisible() else None)
        dialog.setWindowTitle(self.tr("Pin groups", "贴图分组"))
        dialog.resize(455, 330)
        layout = QVBoxLayout(dialog)
        groups = QListWidget()
        groups.setAccessibleName(dialog.windowTitle())
        layout.addWidget(groups, 1)

        def refresh(selected=None):
            names = self.groups()
            groups.clear()
            groups.addItems(names)
            groups.setCurrentRow(names.index(selected) if selected in names else names.index(self.group))

        def selected():
            item = groups.currentItem()
            return item.text() if item else self.group

        def rename():
            source = selected()
            if source == "Default":
                return
            name, ok = QInputDialog.getText(dialog, self.tr("Rename group", "重命名分组"),
                                            self.tr("New name", "新名称"), text=source)
            name = name.strip()[:100]
            if ok and name and name not in self.groups():
                previous = self.previous_group
                for pin_id, window in self.pin_windows.items():
                    if window.state["group"] == source:
                        window.state["group"] = name
                        self.save_pin(pin_id)
                self.workspace.set("groups", [name if group == source else group for group in self.groups()])
                self.switch_group(name if self.group == source else self.group)
                self.previous_group = name if previous == source else previous
                refresh(name)

        def remove():
            source = selected()
            if source == "Default":
                return
            approved = QMessageBox.question(dialog, self.tr("Remove group", "移除分组"),
                                            self.tr("Move its pins to Default and remove this group?", "把该组贴图移至默认分组并移除此组？"))
            if approved != QMessageBox.StandardButton.Yes:
                return
            previous = self.previous_group
            for pin_id, window in self.pin_windows.items():
                if window.state["group"] == source:
                    window.state["group"] = "Default"
                    self.save_pin(pin_id)
            self.workspace.set("groups", [name for name in self.groups() if name != source])
            self.switch_group("Default" if self.group == source else self.group)
            self.previous_group = self.group if previous == source else previous
            refresh()

        def move(step):
            row = groups.currentRow()
            names = self.groups()
            target = row + step
            if row > 0 and 0 < target < len(names):
                names[row], names[target] = names[target], names[row]
                self.workspace.set("groups", names)
                self.rebuild_tray()
                refresh(names[target])

        row = QHBoxLayout()
        group_buttons = {}
        for action, en, zh, callback in (
            ("new", "New…", "新建…", lambda: (self.create_group(), refresh())),
            ("rename", "Rename…", "重命名…", rename), ("remove", "Remove…", "移除…", remove),
            ("up", "Up", "上移", lambda: move(-1)), ("down", "Down", "下移", lambda: move(1)),
        ):
            button = QPushButton(self.tr(en, zh))
            button.clicked.connect(callback)
            group_buttons[action] = button
            row.addWidget(button)

        def update_actions(index):
            group_buttons["rename"].setEnabled(index > 0)
            group_buttons["remove"].setEnabled(index > 0)
            group_buttons["up"].setEnabled(index > 1)
            group_buttons["down"].setEnabled(0 < index < groups.count() - 1)

        layout.addLayout(row)
        bottom = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        switch = bottom.addButton(self.tr("Switch to group", "切换到该组"), QDialogButtonBox.ButtonRole.ActionRole)
        switch.clicked.connect(lambda: self.switch_group(selected()))
        bottom.rejected.connect(dialog.reject)
        groups.itemDoubleClicked.connect(lambda item: self.switch_group(item.text()))
        groups.currentRowChanged.connect(update_actions)
        layout.addWidget(bottom)
        refresh()
        self.keep_dialog(dialog)

    def switch_group(self, name):
        if name != self.group:
            self.previous_group = self.group
        self.group = name
        self.workspace.set("current_group", name)
        for window in self.pin_windows.values():
            window.setVisible(window.state["group"] == name and window.state["visible"])
        self.rebuild_tray()
        self.update_lists()

    def toggle_pins(self):
        group = [pin for pin in self.pin_windows.values() if pin.state["group"] == self.group]
        show = not any(pin.isVisible() for pin in group)
        for pin in group:
            pin.setVisible(show and pin.state["visible"])
        self.update_lists()

    def toggle_click_through(self):
        point = QCursor.pos()
        target = next((window for window in reversed(list(self.pin_windows.values()))
                       if window.isVisible() and window.geometry().contains(point)), None)
        if target:
            target.set_state("click_through", not target.state["click_through"])
        else:
            self.disable_click_through()
            return
        self.status(self.tr("Click-through updated", "鼠标穿透设置已更新"))

    def disable_click_through(self):
        for window in self.pin_windows.values():
            if window.state["click_through"]:
                window.set_state("click_through", False)
        self.status(self.tr("Mouse interaction restored for all pins.", "所有贴图均已恢复鼠标操作。"))

    def recover_pins(self, *, show=False):
        for window in self.pin_windows.values():
            if window.state["group"] == self.group:
                window.recover_position()
                if show and window.state["visible"]:
                    window.show()
        self.update_lists()

    def restore_last_pin(self):
        while self.closed_pins:
            pin_id = self.closed_pins.pop()
            window = self.pin_windows.get(pin_id)
            if window is not None and not window.state["visible"]:
                self.pin_action("show", pin_id)
                self.status(self.tr("The last hidden pin is visible again.", "已重新显示最近隐藏的贴图。"))
                return
        self.notify(self.tr("There are no hidden pins to restore.", "没有隐藏的贴图可恢复。"))

    def paste(self, position=None):
        mime = self.qt.clipboard().mimeData()
        if mime and mime.hasText() and self.workspace.get("allow_remote_images"):
            url = QUrl(mime.text().strip())
            if url.scheme() in {"https", "http"} and url.host() and not mime.hasImage():
                self.fetch_pin(url, position)
                return
        if mime and mime.hasUrls() and not mime.hasImage():
            local = [url.toLocalFile() for url in mime.urls() if url.isLocalFile() and Path(url.toLocalFile()).is_file()]
            if local:
                self.open_files(local, position)
                return
        images = clipboard_images(mime)
        if not images:
            self.notify(self.tr("Copy an image, text, or color before pinning clipboard content.",
                                "请先复制图片、文字或色值，再悬浮剪贴板内容。"))
            return
        for image in images:
            self.open_pin(image, position, state=self.clipboard_state(mime))

    def fetch_pin(self, url, position=None):
        request = QNetworkRequest(url)
        request.setTransferTimeout(12000)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        response = self.network.get(request)
        response.downloadProgress.connect(lambda received, total: response.abort()
                                          if max(received, total) > 25_000_000 else None)

        def finished():
            if response.error() == QNetworkReply.NetworkError.NoError:
                image = QImage.fromData(response.readAll())
                if not image.isNull():
                    self.open_pin(image, position)
                    response.deleteLater()
                    return
            self.notify(self.tr("Could not download an image from the URL.", "该网址无法下载图像。"), error=True)
            response.deleteLater()

        response.finished.connect(finished)

    def open_files(self, files, position=None):
        for filename in files:
            path = Path(filename)
            image = read_image(path)
            if image.isNull():
                self.notify(self.tr(f"Could not open {filename}", f"无法打开 {filename}"), error=True)
            else:
                state = {}
                if path.suffix.lower() == ".gif":
                    try:
                        if path.stat().st_size <= 25_000_000:
                            state["animation"] = b64encode(path.read_bytes()).decode("ascii")
                    except OSError:
                        pass
                self.open_pin(image, position, state=state)

    def update_lists(self):
        self.dashboard.update_history(self.workspace.history())
        self.dashboard.update_pins(self.pin_windows, self.group)

    def history_action(self, action, item_id):
        if action == "clear":
            result = QMessageBox.question(
                self.dashboard, self.tr("Clear screenshot history", "清空截图历史"),
                self.tr("Delete all screenshots stored in local history?", "删除本地历史中的全部截图？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
            )
            if result == QMessageBox.StandardButton.Yes:
                self.workspace.clear_history()
                self.update_lists()
            return
        if action == "delete":
            self.workspace.delete_history(item_id)
            self.update_lists()
            return
        image = self.workspace.history_image(item_id)
        if image.isNull():
            return
        if action == "preview":
            from .editor import ImageEditor

            dialog = ImageEditor(image, locale=self.locale, options=self._annotation_options(),
                                 apply_text=self.tr("Create floating pin", "新建悬浮贴图"))
            dialog.setWindowTitle(self.tr("Screenshot history preview", "截图历史预览"))
            self._connect_annotation_settings(dialog.canvas)
            dialog.applied.connect(lambda updated: self.open_pin(updated))
            self.keep_dialog(dialog)
        else:
            self.output(image, action)

    def keep_dialog(self, dialog):
        self.dialogs.add(dialog)
        dialog.finished.connect(lambda: self.dialogs.discard(dialog))
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()
        dialog.raise_()

    def start_recognition(self, image, action, done=None, *, result_parent=None, silent=False):
        import threading

        if self.recognition_jobs:
            self.failure(self.tr("Recognition is already running.", "正在识别，请稍候。"), done)
            return
        self.job_id += 1
        job_id = self.job_id
        self.recognition_jobs[job_id] = action, done, result_parent, silent
        self.status(self.tr("Recognizing locally…", "正在离线识别…"))
        image = image.copy()
        provider = self.workspace.get("ocr_provider") if action in {"ocr", "ocr-copy"} else "local"
        if provider != "local":
            self.status(self.tr(f"Sending the image to {provider}…", f"正在将图像发送给 {provider}…"))

        def run():
            try:
                if provider == "local":
                    result = recognize(image, action)
                else:
                    from .cloud_ocr import recognize_cloud

                    result = recognize_cloud(image, provider)
                self.signals.recognition.emit(job_id, result, "")
            except Exception as error:
                self.signals.recognition.emit(job_id, "", str(error))

        threading.Thread(target=run, daemon=True).start()

    def recognition_finished(self, job_id, text, error):
        action, done, result_parent, silent = self.recognition_jobs.pop(job_id)
        if error or not text:
            message = error or self.tr("No text or code was recognized.", "未识别到文字或条码。")
            if result_parent is not None:
                done(code=1, error=message)
            else:
                self.failure(message, done)
            return
        if done and done(code=0, text=text) is False:
            return
        if action in {"ocr-copy", "barcode-copy"}:
            self.qt.clipboard().setText(text)
            message = self.tr("Recognized text copied", "识别结果已复制")
            if silent:
                self.status(message)
            else:
                self.notify(message, success=True)
            return
        if result_parent is None and self.dashboard.isVisible():
            result_parent = self.dashboard
        dialog = QDialog(result_parent)
        if result_parent is self.editor and result_parent is not None:
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setWindowTitle(self.tr("Recognition result", "识别结果"))
        dialog.resize(540, 330)
        layout = QVBoxLayout(dialog)
        edit = QTextEdit()
        edit.setPlainText(text)
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = buttons.addButton(self.tr("Copy text", "复制文字"), QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda: self.qt.clipboard().setText(edit.toPlainText()))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self.keep_dialog(dialog)

    def raise_scroll_capture(self):
        dialog = self.scroll_dialog
        if dialog is not None and dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()

    def scroll_capture(self, initial, region, done, *, restore_dashboard=False, hidden_pins=()):
        from .capture import CaptureError, capture_desktop

        self.capturing = True
        hidden_pins = list(hidden_pins)
        current = initial
        initial_dpr = initial.devicePixelRatio()
        dialog = QDialog(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.scroll_dialog = dialog
        dialog.setWindowTitle(self.tr("Scrolling capture", "滚动截图"))
        layout = QVBoxLayout(dialog)
        note = QLabel(self.tr("Scroll down a little in the source window, then add the next frame.",
                              "在原窗口向下滚动一小段，再添加下一帧。"))
        note.setWordWrap(True)
        layout.addWidget(note)
        status = QLabel(f"{current.width()} × {current.height()}")
        status.setTextFormat(Qt.TextFormat.PlainText)
        status.setWordWrap(True)
        layout.addWidget(status)
        buttons = QHBoxLayout()
        add = QPushButton(self.tr("Add next frame", "添加下一帧"))
        save = QPushButton(self.tr("Save As…", "另存为…"))
        copy = QPushButton(self.tr("Copy", "复制"))
        pin = QPushButton(self.tr("Pin", "贴图"))
        cancel = QPushButton(self.tr("Cancel", "取消"))
        cancel.setAccessibleName(self.tr("Cancel scrolling capture", "取消滚动截图"))
        for button in (add, save, copy, pin, cancel):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        dialog.resize(440, 122)
        display = self.qt.screenAt(region.center()) or self.qt.primaryScreen()
        dialog.move(display.availableGeometry().topLeft() + QPoint(8, 8))

        def next_frame():
            if not dialog.isVisible():
                return
            disable_capture_window_transitions(dialog)
            dialog.hide()
            hidden_pins.extend(pin for pin in self.hide_capture_pins() if pin not in hidden_pins)

            def grab():
                nonlocal current
                if dialog not in self.dialogs:
                    return
                try:
                    snapshot = capture_desktop(include_cursor=False)
                    next_image = snapshot.crop(region)
                    merged = stitch_images(current, next_image)
                    if merged is None:
                        status.setText(self.tr("Overlap not found. Scroll less and try again.", "未找到重叠区域。请减小滚动幅度后重试。"))
                    else:
                        merged.setDevicePixelRatio(initial_dpr)
                        current = merged
                        status.setText(f"{current.width()} × {current.height()}")
                except (CaptureError, RuntimeError) as error:
                    status.setText(str(error))
                finally:
                    dialog.show()

            QTimer.singleShot(130, grab)

        def finish(action):
            def complete(code=0, text="", error=""):
                if code:
                    return
                self.last_region = QRect(region)
                dialog.accept()
                if done:
                    done(code=0, text=text)

            self.output(current, action, region, complete, dialog_parent=dialog, on_error=status.setText)

        def close_scroll(result):
            self.scroll_dialog = None
            self.capturing = False
            self.restore_capture_pins(hidden_pins)
            if restore_dashboard or not QSystemTrayIcon.isSystemTrayAvailable():
                self.dashboard.show()
            if done and result == QDialog.DialogCode.Rejected:
                done(code=1, error="Scrolling capture cancelled.")

        add.clicked.connect(next_frame)
        save.clicked.connect(lambda: finish("save"))
        copy.clicked.connect(lambda: finish("copy"))
        pin.clicked.connect(lambda: finish("pin"))
        cancel.clicked.connect(dialog.reject)
        dialog.finished.connect(close_scroll)
        self.keep_dialog(dialog)

    def settings(self):
        dialog = Preferences(self.workspace, self.tr, self.dashboard if self.dashboard.isVisible() else None,
                             apply_hotkeys=self.apply_hotkeys)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        super_snip_changed = values["super_snip"] != self.workspace.get("super_snip")
        if values["run_at_login"] != self.workspace.get("run_at_login"):
            try:
                self.set_login(values["run_at_login"])
            except OSError as error:
                values["run_at_login"] = self.workspace.get("run_at_login")
                self.notify(str(error), error=True)
        for key, value in values.items():
            self.workspace.set(key, value)
        if super_snip_changed:
            self.configure_super_snip()
        self.update_lists()

    def set_login(self, enabled):
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(sys.argv[0]).resolve()))
        command.append("--background")
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run",
                                0, winreg.KEY_SET_VALUE) as key:
                if enabled:
                    winreg.SetValueEx(key, "Snaploom", 0, winreg.REG_SZ, subprocess.list2cmdline(command))
                else:
                    try:
                        winreg.DeleteValue(key, "Snaploom")
                    except FileNotFoundError:
                        pass
            return
        if sys.platform == "darwin":
            path = Path.home() / "Library" / "LaunchAgents" / "com.snaploom.app.plist"
            if enabled:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("wb") as stream:
                    plistlib.dump({"Label": "com.snaploom.app", "ProgramArguments": command, "RunAtLoad": True}, stream)
            else:
                path.unlink(missing_ok=True)
        else:
            path = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "snaploom.desktop"
            if enabled:
                path.parent.mkdir(parents=True, exist_ok=True)
                command[0] = os.environ.get("APPIMAGE") or command[0]
                quoted = " ".join('"' + part.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"' for part in command)
                path.write_text(f"[Desktop Entry]\nType=Application\nName=Snaploom\nExec={quoted}\nTerminal=false\n", encoding="utf-8")
            else:
                path.unlink(missing_ok=True)

    def check_corner(self):
        name = self.workspace.get("hot_corner")
        if not name or not self.corners_enabled:
            self.last_corner = None
            return
        point = QCursor.pos()
        screen = self.qt.screenAt(point)
        if not screen:
            self.last_corner = None
            return
        geometry = screen.geometry()
        positions = {"top-left": geometry.topLeft, "top-right": geometry.topRight,
                     "bottom-left": geometry.bottomLeft, "bottom-right": geometry.bottomRight}
        corner = positions[name]()
        if (corner - point).manhattanLength() > 4:
            self.last_corner = None
            return
        if self.capturing or self.editor is not None:
            # A corner that already triggered stays disarmed until the pointer leaves it.
            if self.last_corner != corner or self.corner_since != float("inf"):
                self.last_corner = None
            return
        if self.last_corner != corner:
            self.last_corner = corner
            self.corner_since = time.monotonic()
        elif time.monotonic() - self.corner_since > 0.65:
            self.corner_since = float("inf")
            self.capture({})

    def configure_super_snip(self):
        if self.mouse_hook:
            self.mouse_hook.stop()
            self.mouse_hook = None
        modifier = self.workspace.get("super_snip")
        if not modifier:
            return
        if sys.platform.startswith("linux") and (os.getenv("WAYLAND_DISPLAY") or os.getenv("XDG_SESSION_TYPE") == "wayland"):
            self.status(self.tr("Modifier drag requires X11 on Linux.", "Linux 的修饰键拖拽截图需要 X11。"))
            return
        try:
            from pynput import keyboard, mouse

            held = set()
            origin = None
            key_hook = None
            key_name = "cmd" if modifier == "super" else modifier

            def on_press(key):
                held.add(key_hook.canonical(key))

            def on_release(key):
                held.discard(key_hook.canonical(key))

            def on_click(x, y, button, pressed):
                nonlocal origin
                if button == mouse.Button.right:
                    if pressed:
                        origin = QCursor.pos() if getattr(keyboard.Key, key_name) in held else None
                    elif origin is not None:
                        end = QCursor.pos()
                        top_left = QPoint(min(origin.x(), end.x()), min(origin.y(), end.y()))
                        bottom_right = QPoint(max(origin.x(), end.x()), max(origin.y(), end.y()))
                        rect = QRect(top_left, bottom_right)
                        origin = None
                        if rect.width() > 8 and rect.height() > 8:
                            self.signals.super_snip.emit(rect)

            key_hook = keyboard.Listener(on_press=on_press, on_release=on_release)
            mouse_hook = mouse.Listener(on_click=on_click)
            self.mouse_hook = HookPair(key_hook, mouse_hook)
            key_hook.start()
            mouse_hook.start()
        except (ImportError, OSError, RuntimeError) as error:
            self.status(str(error))

    def command(self, data, done=None):
        command = data.get("command")
        if command == "snip":
            self.capture(data, done)
            return
        if command == "paste":
            files = data.get("files")
            pos = QPoint(*data["pos"]) if data.get("pos") else None
            if data.get("plain") is not None or data.get("html") is not None:
                self.open_pin(text_image(data.get("plain") or "", data.get("html") or ""), pos)
            elif files:
                self.open_files(files, pos)
            else:
                self.paste(pos)
        elif command in {"ocr", "barcode-scan", "barcode-decode"}:
            image = read_image(Path(data["file"])) if data.get("file") else self.qt.clipboard().image()
            if image.isNull():
                if command == "barcode-scan":
                    self.capture({"recognition": "barcode-copy"}, done)
                else:
                    self.failure("No image is available for recognition.", done)
            else:
                self.start_recognition(image, "ocr-copy" if command == "ocr" else "barcode-copy", done)
            return
        elif command in {"whiteboard", "transparent-whiteboard"}:
            self.capture(data | {"whiteboard": True, "transparent": command.startswith("transparent")}, done)
            return
        elif command == "create-group":
            self.create_group(data.get("name"))
        elif command == "switch-group":
            groups = self.groups()
            self.switch_group(data.get("name") or groups[(groups.index(self.group) + 1) % len(groups)])
        elif command == "switch-group-prev":
            self.switch_group(self.previous_group)
        elif command in {"show-group-manager", "switch-groups"}:
            self.manage_groups()
        elif command == "empty-group":
            for pin_id in [item for item, window in self.pin_windows.items() if window.state["group"] == self.group]:
                self.pin_action("delete", pin_id)
        elif command == "show":
            self.show_dashboard()
        elif command == "hide":
            self.dashboard.hide()
        elif command == "restore-pins":
            self.recover_pins(show=True)
        elif command in {"show-images", "hide-images"}:
            for window in self.pin_windows.values():
                if window.state["group"] == self.group:
                    window.setVisible(command == "show-images" and window.state["visible"])
            self.update_lists()
        elif command in {"toggle-pins", "toggle-images"}:
            self.toggle_pins()
        elif command == "toggle-click-through":
            self.toggle_click_through()
        elif command == "no-click-through":
            self.disable_click_through()
        elif command == "show-tray-menu":
            self.tray_menu.popup(QCursor.pos())
        elif command == "clear-snip-history":
            self.workspace.clear_history()
            self.update_lists()
        elif command in {"enable-hotkeys", "disable-hotkeys", "toggle-hotkeys"}:
            self.hotkeys_enabled = not self.hotkeys_enabled if command.startswith("toggle") else command.startswith("enable")
            self.configure_hotkeys()
        elif command in {"enable-hot-corners", "disable-hot-corners", "toggle-hot-corners"}:
            self.corners_enabled = not self.corners_enabled if command.startswith("toggle") else command.startswith("enable")
        elif command == "external":
            try:
                self.external(data.get("argv") or [])
            except (ValueError, OSError) as error:
                self.failure(str(error), done)
                return
        elif command == "pick-color":
            self.capture({"color_only": True}, done)
            return
        elif command in {"paste-as-image", "paste-as-plain-text"}:
            self.paste_converted(command)
        elif command == "docs":
            self.help()
        elif command == "open-preferences":
            self.settings()
        elif command == "exit":
            self.qt.quit()
        elif command in {"settings", "history", "quit"}:
            self.action(command)
        elif command is None and not data.get("background") and self.scroll_dialog is not None:
            self.raise_scroll_capture()
        elif not data.get("background") and (not self.workspace.get("start_hidden") or not QSystemTrayIcon.isSystemTrayAvailable()):
            self.show_dashboard()
        if done:
            done(code=0)

    def external(self, argv):
        if not argv:
            raise ValueError("An executable and optional arguments are required.")
        subprocess.Popen([part.strip('"') for part in argv], shell=False)

    def paste_converted(self, command):
        clipboard = self.qt.clipboard()
        if command == "paste-as-plain-text":
            clipboard.setText(clipboard.text())
        else:
            images = clipboard_images(clipboard.mimeData())
            if not images:
                return
            self.copy_image(images[0])
        if sys.platform == "win32":
            import ctypes

            QTimer.singleShot(40, lambda: self.send_windows_paste(ctypes.windll.user32))

    @staticmethod
    def send_windows_paste(user32):
        for key, flags in ((0x11, 0), (0x56, 0), (0x56, 2), (0x11, 2)):
            user32.keybd_event(key, 0, flags, 0)

    def help(self):
        shortcuts = self.workspace.get("hotkeys")
        capture = shortcuts.get("capture") if self.hotkeys_enabled and "capture" not in self.hotkeys.unavailable else None
        paste = shortcuts.get("paste") if self.hotkeys_enabled and "paste" not in self.hotkeys.unavailable else None
        capture_hint = self.tr(f"Press {capture} to capture a region.", f"按 {capture} 开始区域截图。") if capture else self.tr(
            "Choose Capture region from the main window or tray.", "可从主窗口或托盘选择「区域截图」。")
        paste_hint = self.tr(f"Press {paste} to pin clipboard content.", f"按 {paste} 悬浮剪贴板内容。") if paste else self.tr(
            "Choose Pin clipboard content from the main window or tray.", "可从主窗口或托盘选择「悬浮剪贴板内容」。")
        QMessageBox.information(
            self.dashboard, "Snaploom",
            "\n".join((capture_hint, paste_hint, self.tr(
                "On the screenshot canvas, Enter confirms the selection or copies it once selected. "
                "Restore last hidden pin brings back a pin you hid earlier. Change shortcuts in Settings.",
                "在截图画布中，回车可确认框选或复制已确认的选区。"
                "「恢复最近隐藏的贴图」可重新显示收起的小窗。可在设置中修改快捷键。",
            ))),
        )

    def shutdown(self):
        self.hotkeys.close()
        if self.mouse_hook:
            self.mouse_hook.stop()
        for pin_id, pin in self.pin_windows.items():
            pin.timer.stop()
            self.save_pin(pin_id)
        self.workspace.close()


class HookPair:
    def __init__(self, key, mouse):
        self.key = key
        self.mouse = mouse

    def stop(self):
        self.key.stop()
        self.mouse.stop()


def smoke_test(qt, ocr=False):
    from .capture import Snapshot
    from .editor import CaptureEditor

    with tempfile.TemporaryDirectory(prefix="snaploom-smoke-") as directory:
        app = Application(qt, Path(directory), testing=True)
        try:
            image = QImage(480, 180, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.white)
            geometry = QRect(0, 0, 480, 180)
            snapshot = Snapshot(image, geometry)
            selected = QRect(5, 5, 210, 90)
            editor = CaptureEditor(snapshot, selection=selected)
            assert editor.selected_geometry() == selected, "Selection coordinates changed"
            assert editor.export_image().size() == selected.size(), "Export size changed"
            app.workspace.remember(image, geometry, "copy")
            assert app.workspace.history_image(app.workspace.history()[0][0]).size() == image.size(), "History reload failed"
            pin_id = app.open_pin(image, QPoint(20, 40))
            app.pin_action("hide", pin_id)
            assert not app.pin_windows[pin_id].isVisible(), "Pin hide failed"
            app.pin_action("show", pin_id)
            path = save_image(image, Path(directory) / "capture.png")
            assert read_image(path).size() == image.size(), "PNG round trip failed"
            if ocr:
                smoke_recognition()
            editor.close()
        finally:
            for pin in app.pin_windows.values():
                pin.hide()
            app.shutdown()
    print("Snaploom smoke test passed")
    return 0


def smoke_recognition():
    import zxingcpp
    from PIL import Image, ImageDraw, ImageFont

    sample = Image.new("RGB", (480, 180), "white")
    ImageDraw.Draw(sample).text((240, 90), "SNAPLOOM 123", fill="black",
                                font=ImageFont.load_default(size=44), anchor="mm")
    fixture = QImage(sample.tobytes(), 480, 180, 1440, QImage.Format.Format_RGB888).copy()
    result = recognize(fixture, "ocr")
    assert "SNAPLOOM" in result.upper(), f"OCR result: {result!r}"
    barcode = zxingcpp.create_barcode("Snaploom", zxingcpp.BarcodeFormat.QRCode)
    code = zxingcpp.write_barcode_to_image(barcode, scale=4)
    code_image = QImage(memoryview(code), code.shape[1], code.shape[0], code.shape[1],
                        QImage.Format.Format_Grayscale8).copy()
    assert recognize(code_image, "barcode") == "Snaploom", "QR decode failed"


def main(values=None):
    data = arguments(sys.argv[1:] if values is None else values)
    qt = QApplication([sys.argv[0]])
    qt.setApplicationName("Snaploom")
    qt.setApplicationDisplayName("Snaploom")
    qt.setApplicationVersion(VERSION)
    qt.setDesktopFileName("io.github.peterwangze.snaploom")
    qt.setQuitOnLastWindowClosed(False)
    qt.setWindowIcon(app_icon())
    if data["smoke_test"] or data["smoke_test_ocr"]:
        return smoke_test(qt, data["smoke_test_ocr"])
    result = send_to_instance(data)
    if result is not None:
        return result
    if data.get("command") in {"quit", "exit"}:
        return 0
    output = set(data.get("output") or [])
    needs_resident = (
        data.get("command") == "snip" and (not output or bool(output & {"clipboard", "pin", "ocr_clipboard", "copy-file"}))
    ) or data.get("command") in {"ocr", "barcode-scan", "barcode-decode"}
    if data.get("block") and needs_resident:
        executable = Path(sys.executable)
        command = ["--background"]
        if not getattr(sys, "frozen", False):
            command.insert(0, str(Path(sys.argv[0]).resolve()))
        elif sys.platform == "win32" and executable.name.lower() == "snaploom-cli.exe":
            executable = executable.with_name("Snaploom.exe")
        if QProcess.startDetached(str(executable), command)[0]:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                result = send_to_instance(data)
                if result is not None:
                    return result
                time.sleep(0.1)
        print("Could not start the Snaploom background application.", file=sys.stderr)
        return 2
    location = data_directory()
    location.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(location / "instance.lock"))
    if not lock.tryLock(0):
        result = send_to_instance(data)
        return 2 if result is None else result
    application = Application(qt)
    server = CommandServer(application.command)
    qt.aboutToQuit.connect(server.server.close)
    if data.get("block"):
        def print_result(code=0, text="", error=""):
            if text:
                print(text)
            if error:
                print(error, file=sys.stderr)
            qt.exit(code)

        QTimer.singleShot(0, lambda: application.command(data, print_result))
        QTimer.singleShot(int(data["block"] * 1000), lambda: qt.exit(2))
    else:
        QTimer.singleShot(0, lambda: application.command(data))
    return qt.exec()
