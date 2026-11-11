"""Desktop lifecycle, capture workflows and output routing."""

from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QLocale, QLockFile, QMimeData, QObject, QPoint, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QIcon, QImage
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QMenu, QMessageBox, QPushButton, QSpinBox, QStyle, QSystemTrayIcon,
    QTextEdit, QVBoxLayout,
)

from . import VERSION
from .commands import CommandServer, arguments, send_to_instance
from .hotkeys import Hotkeys
from .imaging import clipboard_images, read_image, recognize, stitch_images, text_image
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
        self.dashboard.action.connect(self.action)
        self.dashboard.history_action.connect(self.history_action)
        self.dashboard.pin_action.connect(self.pin_action)
        self.dashboard.files_dropped.connect(self.open_files)
        self.editor = None
        self.capturing = False
        self.capture_context = {}
        self.pin_windows = {}
        self.hidden_pins = []
        self.closed_pins = []
        self.last_fingerprint = None
        self.last_region = next(iter(self.workspace.regions()), None)
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
        self.update_lists()
        if not testing:
            qt.aboutToQuit.connect(self.shutdown)

    def tr(self, en, zh):
        return zh if self.locale.startswith("zh") else en

    def status(self, message):
        self.dashboard.status.setText(message)

    def notify(self, message, error=False):
        self.status(message)
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

    def tray_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.action("capture")
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
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
            ("paste", "Paste or restore a pin", "贴图或恢复"), ("open", "Open image…", "打开图像…"),
        ):
            self.tray_menu.addAction(self.tr(en, zh), lambda checked=False, name=action: self.action(name))
        self.tray_menu.addSeparator()
        groups = self.tray_menu.addMenu(self.tr("Pin groups", "贴图分组"))
        for name in self.groups():
            item = groups.addAction(name)
            item.setCheckable(True)
            item.setChecked(name == self.group)
            item.triggered.connect(lambda checked=False, selected=name: self.switch_group(selected))
        groups.addSeparator()
        groups.addAction(self.tr("New group…", "新建分组…"), self.create_group)
        for action, en, zh in (
            ("toggle_pins", "Show or hide pins", "显示或隐藏贴图"),
            ("recover", "Recover off-screen pins", "找回屏幕外贴图"),
            ("click_through", "Toggle pin click-through", "切换贴图鼠标穿透"),
            ("history", "History", "历史"), ("settings", "Settings", "设置"), ("quit", "Quit", "退出"),
        ):
            self.tray_menu.addAction(self.tr(en, zh), lambda checked=False, name=action: self.action(name))

    def configure_hotkeys(self):
        shortcuts = self.workspace.get("hotkeys")
        unavailable = self.hotkeys.configure(shortcuts if self.hotkeys_enabled else {})
        labels = [
            f'{self.tr("Capture", "截图")}  {shortcuts.get("capture") or "—"}',
            f'{self.tr("Paste", "贴图")}  {shortcuts.get("paste") or "—"}',
            f'{self.tr("Hide/show pins", "显示／隐藏贴图")}  {shortcuts.get("toggle_pins") or "—"}',
        ]
        if unavailable:
            labels.append(self.tr("Some hotkeys are unavailable. Open Settings to change them.", "部分快捷键不可用，可在设置中修改。"))
        self.dashboard.shortcuts.setText("   ·   ".join(labels))

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
        elif action == "open":
            files, _ = QFileDialog.getOpenFileNames(self.dashboard, self.tr("Open image", "打开图像"), "",
                                                   self.tr("Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tiff *.svg)", "图像 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tiff *.svg)"))
            self.open_files(files)
        elif action == "toggle_pins":
            self.toggle_pins()
        elif action == "click_through":
            self.toggle_click_through()
        elif action == "recover":
            self.recover_pins()
        elif action == "dashboard":
            self.show_dashboard()
        elif action == "history":
            self.show_dashboard(1)
        elif action == "settings":
            self.settings()
        elif action == "quit":
            self.qt.quit()

    def capture(self, options, done=None):
        if self.editor is not None or self.capturing:
            self.failure(self.tr("Finish the current capture first.", "请先完成当前截图。"), done)
            return
        if options.get("custom"):
            custom = self.custom_region()
            if custom is None:
                if done:
                    done(code=1)
                return
            options = options | custom
        elif options.get("whiteboard") and not options.get("size") and not options.get("color"):
            custom = self.custom_region(whiteboard=True)
            if custom is None:
                if done:
                    done(code=1)
                return
            options = options | custom
        self.capturing = True
        self.capture_context = {"options": options, "done": done, "dashboard": self.dashboard.isVisible(), "pins": []}
        self.dashboard.hide()
        if self.workspace.get("hide_pins_on_capture"):
            for pin in self.pin_windows.values():
                if pin.isVisible():
                    pin.hide()
                    self.capture_context["pins"].append(pin)
        delay = float(options.get("delay", 0))
        if delay:
            self.notify(self.tr(f"Capturing in {delay:g} seconds", f"{delay:g} 秒后截图"))
        QTimer.singleShot(max(105, int(delay * 1000)), self.start_capture)

    def custom_region(self, whiteboard=False):
        dialog = QDialog(self.dashboard if self.dashboard.isVisible() else None)
        dialog.setWindowTitle(self.tr("Whiteboard", "白板") if whiteboard else self.tr("Capture size and delay", "截图尺寸与延时"))
        layout = QFormLayout(dialog)
        point = QCursor.pos()
        fields = []
        for en, zh, value, lower, upper in (
            ("X", "X", point.x(), -65536, 65536), ("Y", "Y", point.y(), -65536, 65536),
            ("Width (logical pixels)", "宽度（逻辑像素）", 800, 1, 16384),
            ("Height (logical pixels)", "高度（逻辑像素）", 520, 1, 16384),
        ):
            field = QSpinBox()
            field.setRange(lower, upper)
            field.setValue(value)
            layout.addRow(self.tr(en, zh), field)
            fields.append(field)
        delay = QDoubleSpinBox()
        delay.setRange(0, 600)
        layout.addRow(self.tr("Delay (seconds)", "延时（秒）"), delay)
        transparent = QCheckBox(self.tr("Transparent background", "透明背景"))
        if whiteboard:
            layout.addRow(transparent)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        rect = [field.value() for field in fields]
        return {"rect": rect, "size": rect[2:], "delay": delay.value(), "whiteboard": whiteboard,
                "transparent": transparent.isChecked()}

    def start_capture(self):
        context = self.capture_context
        options = context["options"]
        try:
            from .capture import Snapshot, active_window, capture_desktop, detect_elements, detect_windows
            from .editor import CaptureEditor

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
            if selection is not None and options.get("output") and not options.get("hold"):
                image = snapshot.crop(selection)
                self.capture_completed("output", image, selection)
                return
            editor_options = {
                "locale": self.locale, "show_guides": self.workspace.get("show_grid"),
                "color_format": self.workspace.get("color_format").lower(),
                "corner_radius": options.get("corner_radius", 0),
                "custom_outputs": {name: name for name in self.workspace.get("custom_commands")},
                "default_action": "output" if options.get("output") else "copy",
                "aspect_ratio": (tuple(options["size"]) if options.get("size") and options.get("keep_aspect_ratio") else None),
                "detect_elements": None if options.get("whiteboard") else detect_elements,
                "color_only": options.get("color_only", False),
            }
            windows = () if options.get("whiteboard") else detect_windows()
            self.editor = CaptureEditor(snapshot, selection=selection, windows=windows, options=editor_options,
                                        history=self.workspace.regions())
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

        if not self.editor or self.capture_context["options"].get("whiteboard"):
            return
        editor = self.editor
        editor.hide()

        def refresh():
            if self.editor is not editor:
                return
            try:
                snapshot = capture_desktop(include_cursor=self.workspace.get("include_cursor"))
                editor.set_snapshot(snapshot, detect_windows())
            except (CaptureError, RuntimeError) as error:
                self.notify(str(error), error=True)
            finally:
                editor.show()
                editor.activateWindow()

        QTimer.singleShot(120, refresh)

    def release_capture(self, restore_dashboard=True):
        context = self.capture_context
        self.capture_context = {}
        editor, self.editor = self.editor, None
        if editor:
            editor.hide()
            editor.deleteLater()
        for pin in context.get("pins", []):
            if pin in self.pin_windows.values():
                pin.show()
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
        if action == "save" and not self.capture_context.get("options", {}).get("hold"):
            editor = self.editor
            if editor:
                editor.hide()
                try:
                    target = self.save_dialog(image)
                except (OSError, ValueError) as error:
                    self.failure(str(error))
                    target = None
                if not target:
                    editor.show()
                    editor.activateWindow()
                    return
                context = self.release_capture()
                self.last_region = QRect(rect)
                self.output(image, "saved", rect, context.get("done"), target)
                return
        context = self.release_capture()
        options = context.get("options", {})
        done = context.get("done")
        if image.isNull():
            self.failure(self.tr("The captured image is empty.", "截图为空。"), done)
            return
        self.last_region = QRect(rect)
        if options.get("recognition"):
            self.output(image, options["recognition"], rect, done)
            return
        output = options.get("output") if action == "output" or options.get("hold") else None
        if output:
            self.output_pipeline(image, rect, output, done)
            return
        if action == "output":
            action = "copy"
        if action == "scroll":
            self.scroll_capture(image, rect, done)
            return
        self.output(image, action, rect, done)

    def custom_output(self, name, image, rect):
        context = self.release_capture()
        config = self.workspace.get("custom_commands").get(name)
        if not config:
            self.failure("This output command is unavailable.", context.get("done"))
            return
        try:
            path = self.quick_save(image)
            tokens = shlex.split(config, posix=os.name != "nt")
            argv = [part.strip('"').replace("{image}", str(path)) for part in tokens]
            if not argv:
                raise ValueError("The command is empty.")
            subprocess.Popen(argv, shell=False, **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
            self.status(str(path))
            self.workspace.remember(image, rect, name)
            self.update_lists()
            if context.get("done"):
                context["done"](code=0, text=str(path))
        except (OSError, ValueError) as error:
            self.failure(str(error), context.get("done"))

    def failure(self, message, done=None):
        self.notify(message, error=True)
        if done:
            done(code=1, error=message)

    def copy_image(self, image):
        mime = QMimeData()
        mime.setImageData(image)
        mime.setData("image/png", QByteArray(png_bytes(image)))
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

        def execute(index, text=""):
            if index == len(steps):
                self.workspace.remember(image, rect, " / ".join(outputs))
                if self.workspace.get("auto_save") and "no-auto-save" not in outputs and not (
                    {action for action, _ in steps} & {"file", "quick-save", "save"}
                ):
                    try:
                        self.quick_save(image)
                    except OSError as error:
                        self.failure(str(error), done)
                        return
                self.update_lists()
                if done:
                    done(code=0, text=text)
                return
            action, target = steps[index]

            def following(code=0, text="", error=""):
                if code:
                    if done:
                        done(code=code, error=error)
                else:
                    execute(index + 1, text)

            self.output(image, action, None, following, target, silent=silent)

        execute(0)

    def output(self, image, action, rect=None, done=None, target=None, *, silent=False):
        rect = QRect(rect) if rect is not None else QRect()
        text = ""
        try:
            if action == "copy":
                self.copy_image(image)
                if not silent:
                    self.notify(self.tr("Image copied", "图像已复制"))
            elif action == "pin":
                self.open_pin(image, rect.topLeft() if rect.isValid() else None)
            elif action == "save":
                target = self.save_dialog(image)
                if not target:
                    if done:
                        done(code=1, error="Save cancelled.")
                    return
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
                self.start_recognition(image, action, done)
                done = None
            elif action == "copy-file":
                path = self.quick_save(image)
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(str(path))])
                self.qt.clipboard().setMimeData(mime)
                self.notify(self.tr("Image file copied", "图像文件已复制"))
            elif action not in {"success", "saved"}:
                raise ValueError(f"Unknown output: {action}")
            if target:
                text = str(target)
                if action != "silent" and not silent:
                    self.notify(self.tr(f"Saved to {target}", f"已保存至 {target}"))
                if self.workspace.get("copy_after_save"):
                    self.copy_image(image)
            elif self.workspace.get("auto_save") and rect.isValid():
                self.quick_save(image)
            if rect.isValid():
                self.workspace.remember(image, rect, action)
                self.update_lists()
        except (OSError, RuntimeError, ValueError) as error:
            self.failure(str(error), done)
            return
        if done:
            done(code=0, text=text)

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

    def save_dialog(self, image):
        ext = self.workspace.get("format")
        folder = Path(self.workspace.get("save_folder") or pictures_directory())
        folder.mkdir(parents=True, exist_ok=True)
        initial = folder / f"{datetime.now():Snaploom_%Y-%m-%d_%H-%M-%S}.{ext}"
        path, selected = QFileDialog.getSaveFileName(
            self.dashboard if self.dashboard.isVisible() else None,
            self.tr("Save screenshot", "保存截图"), str(initial),
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;Bitmap (*.bmp)",
        )
        if not path:
            return None
        target = Path(path)
        if not target.suffix:
            match = re.search(r"\*\.(\w+)", selected)
            target = target.with_suffix("." + (match.group(1) if match else ext))
        return save_image(image, target)

    def open_pin(self, image, position=None, pin_id=None, state=None, restore=False):
        state = {"group": self.group, "opacity": self.workspace.get("default_pin_opacity")} | (state or {})
        if position is not None:
            state.update({"x": position.x(), "y": position.y()})
        if restore and not self.workspace.get("restore_pins"):
            state["visible"] = False
        if not pin_id:
            pin_id = self.workspace.add_pin(image, state)
        window = PinWindow(image, state, self.tr)
        self.pin_windows[pin_id] = window
        window.changed.connect(lambda: self.save_pin(pin_id))
        window.dismissed.connect(lambda destroy: self.pin_action("delete" if destroy else "hide", pin_id))
        window.action_requested.connect(lambda action: self.pin_action(action, pin_id))
        if state.get("visible", True) and state["group"] == self.group:
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

    def pin_action(self, action, pin_id):
        if action == "recover":
            self.recover_pins()
            return
        if action == "solo":
            for item, window in self.pin_windows.items():
                if window.state["group"] == self.group:
                    self.pin_action("show" if item == pin_id else "hide", item)
            return
        window = self.pin_windows.get(pin_id)
        if window is None:
            return
        if action == "delete":
            window.hide()
            del self.pin_windows[pin_id]
            self.workspace.delete_pin(pin_id)
            window.deleteLater()
            self.rebuild_tray()
        elif action == "hide":
            window.hide()
            window.state["visible"] = False
            if pin_id in self.closed_pins:
                self.closed_pins.remove(pin_id)
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
            group, ok = QInputDialog.getItem(self.dashboard, self.tr("Pin group", "贴图分组"),
                                             self.tr("Move to or create a group", "移动到分组，或输入新分组"),
                                             self.groups(), 0, True)
            if ok and group.strip():
                window.state["group"] = group.strip()
                window.setVisible(window.state["visible"] and window.state["group"] == self.group)
                self.save_pin(pin_id)
                self.rebuild_tray()
        elif action in {"annotate", "crop"}:
            from .editor import Annotation, ImageEditor

            annotations = tuple(Annotation(**(item | {"points": tuple(map(tuple, item["points"]))}))
                                for item in window.state.get("annotations", []))
            dialog = ImageEditor(window.image, parent=None, locale=self.locale, annotations=annotations)
            if action == "crop" and hasattr(dialog, "activate_crop"):
                dialog.activate_crop()
            dialog.applied.connect(lambda updated: self.update_pin_image(
                pin_id, dialog.result_base, [asdict(annotation) for annotation in dialog.annotations]
            ))
            self.keep_dialog(dialog)
        else:
            self.output(window.view, action)
        self.update_lists()

    def update_pin_image(self, pin_id, updated, annotations):
        window = self.pin_windows.get(pin_id)
        if window:
            window.state["annotations"] = annotations
            window.replace(updated)
            self.save_pin(pin_id, include_image=True)
            self.update_lists()

    def groups(self):
        created = self.workspace.get("groups") or []
        return sorted({"Default", self.group, *created, *(win.state["group"] for win in self.pin_windows.values())})

    def create_group(self, name=None):
        if not isinstance(name, str):
            name, ok = QInputDialog.getText(self.dashboard, self.tr("New pin group", "新建贴图分组"),
                                            self.tr("Group name", "分组名称"))
            if not ok:
                return
        name = name.strip()[:100]
        if name:
            self.workspace.set("groups", sorted(set(self.groups()) | {name}))
            self.switch_group(name)

    def switch_group(self, name):
        if name != self.group:
            self.previous_group = self.group
        self.group = name
        self.workspace.set("current_group", name)
        self.hidden_pins = []
        for window in self.pin_windows.values():
            window.setVisible(window.state["group"] == name and window.state["visible"])
        self.rebuild_tray()
        self.update_lists()

    def toggle_pins(self):
        visible = [pin_id for pin_id, pin in self.pin_windows.items() if pin.isVisible()]
        if visible:
            self.hidden_pins = visible
            for pin_id in visible:
                self.pin_windows[pin_id].hide()
        else:
            for pin_id, window in self.pin_windows.items():
                if window.state["group"] == self.group and (pin_id in self.hidden_pins or window.state["visible"]):
                    window.show()
            self.hidden_pins = []
        self.update_lists()

    def toggle_click_through(self):
        point = QCursor.pos()
        target = next((window for window in reversed(list(self.pin_windows.values()))
                       if window.isVisible() and window.geometry().contains(point)), None)
        if target:
            target.set_state("click_through", not target.state["click_through"])
        else:
            for window in self.pin_windows.values():
                window.set_state("click_through", False)
        self.status(self.tr("Click-through updated", "鼠标穿透设置已更新"))

    def recover_pins(self):
        for window in self.pin_windows.values():
            if window.state["group"] == self.group:
                window.recover_position()
                if window.state["visible"]:
                    window.show()
        self.update_lists()

    def paste(self, position=None):
        mime = self.qt.clipboard().mimeData()
        if mime and mime.hasText() and self.workspace.get("allow_remote_images"):
            url = QUrl(mime.text().strip())
            if url.scheme() in {"https", "http"} and url.host() and not mime.hasImage():
                self.fetch_pin(url)
                return
        images = clipboard_images(mime)
        if images:
            fingerprint = hashlib.sha256(png_bytes(images[0])).digest()
            if fingerprint != self.last_fingerprint or not self.closed_pins:
                self.last_fingerprint = fingerprint
                for image in images:
                    self.open_pin(image, position)
                return
        while self.closed_pins:
            pin_id = self.closed_pins.pop()
            if pin_id in self.pin_windows and not self.pin_windows[pin_id].state["visible"]:
                self.pin_action("show", pin_id)
                return
        self.notify(self.tr("Clipboard has no supported content.", "剪贴板中没有可贴图的内容。"))

    def fetch_pin(self, url):
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
                    self.open_pin(image)
                    response.deleteLater()
                    return
            self.notify(self.tr("Could not download an image from the URL.", "该网址无法下载图像。"), error=True)
            response.deleteLater()

        response.finished.connect(finished)

    def open_files(self, files, position=None):
        for filename in files:
            image = read_image(Path(filename))
            if image.isNull():
                self.notify(self.tr(f"Could not open {filename}", f"无法打开 {filename}"), error=True)
            else:
                self.open_pin(image, position)

    def update_lists(self):
        self.dashboard.update_history(self.workspace.history())
        self.dashboard.update_pins(self.pin_windows)

    def history_action(self, action, item_id):
        if action == "clear":
            result = QMessageBox.question(self.dashboard, self.tr("Clear screenshot history", "清空截图历史"),
                                          self.tr("Delete all screenshots stored in local history?", "删除本地历史中的全部截图？"))
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

            dialog = ImageEditor(image, locale=self.locale)
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

    def start_recognition(self, image, action, done=None):
        import threading

        if self.recognition_jobs:
            self.failure(self.tr("Recognition is already running.", "正在识别，请稍候。"), done)
            return
        self.job_id += 1
        job_id = self.job_id
        self.recognition_jobs[job_id] = action, done
        self.status(self.tr("Recognizing locally…", "正在离线识别…"))
        image = image.copy()

        def run():
            try:
                result = recognize(image, action)
                self.signals.recognition.emit(job_id, result, "")
            except Exception as error:
                self.signals.recognition.emit(job_id, "", str(error))

        threading.Thread(target=run, daemon=True).start()

    def recognition_finished(self, job_id, text, error):
        action, done = self.recognition_jobs.pop(job_id)
        if error or not text:
            self.failure(error or self.tr("No text or code was recognized.", "未识别到文字或条码。"), done)
            return
        if done:
            done(code=0, text=text)
        if action in {"ocr-copy", "barcode-copy"}:
            self.qt.clipboard().setText(text)
            self.notify(self.tr("Recognized text copied", "识别结果已复制"))
            return
        dialog = QDialog(self.dashboard if self.dashboard.isVisible() else None)
        dialog.setWindowTitle(self.tr("Recognition result", "识别结果"))
        dialog.resize(540, 330)
        layout = QVBoxLayout(dialog)
        edit = QTextEdit(text)
        layout.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = buttons.addButton(self.tr("Copy text", "复制文字"), QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda: self.qt.clipboard().setText(edit.toPlainText()))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        self.keep_dialog(dialog)

    def scroll_capture(self, initial, region, done):
        from .capture import CaptureError, capture_desktop

        current = initial
        initial_dpr = initial.devicePixelRatio()
        dialog = QDialog(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        dialog.setWindowTitle(self.tr("Scrolling capture", "滚动截图"))
        layout = QVBoxLayout(dialog)
        note = QLabel(self.tr("Scroll down a little in the source window, then add the next frame.",
                              "在原窗口向下滚动一小段，再添加下一帧。"))
        note.setWordWrap(True)
        layout.addWidget(note)
        status = QLabel(f"{current.width()} × {current.height()}")
        status.setWordWrap(True)
        layout.addWidget(status)
        buttons = QHBoxLayout()
        add = QPushButton(self.tr("Add next frame", "添加下一帧"))
        save = QPushButton(self.tr("Save…", "保存…"))
        copy = QPushButton(self.tr("Copy", "复制"))
        pin = QPushButton(self.tr("Pin", "贴图"))
        for button in (add, save, copy, pin):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        dialog.resize(440, 122)
        display = self.qt.screenAt(region.center()) or self.qt.primaryScreen()
        dialog.move(display.availableGeometry().topLeft() + QPoint(8, 8))

        def next_frame():
            dialog.hide()

            def grab():
                nonlocal current
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
            dialog.accept()
            self.output(current, action, region, done)

        add.clicked.connect(next_frame)
        save.clicked.connect(lambda: finish("save"))
        copy.clicked.connect(lambda: finish("copy"))
        pin.clicked.connect(lambda: finish("pin"))
        if done:
            dialog.rejected.connect(lambda: done(code=1, error="Scrolling capture cancelled."))
        self.keep_dialog(dialog)

    def settings(self):
        dialog = Preferences(self.workspace, self.tr, self.dashboard if self.dashboard.isVisible() else None)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if values["run_at_login"] != self.workspace.get("run_at_login"):
            try:
                self.set_login(values["run_at_login"])
            except OSError as error:
                values["run_at_login"] = self.workspace.get("run_at_login")
                self.notify(str(error), error=True)
        for key, value in values.items():
            self.workspace.set(key, value)
        self.configure_hotkeys()
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
        if not name or not self.corners_enabled or self.capturing or self.editor is not None:
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
        if (corner - point).manhattanLength() <= 4:
            if self.last_corner != corner:
                self.last_corner = corner
                self.corner_since = time.monotonic()
            elif time.monotonic() - self.corner_since > 0.65:
                self.corner_since = float("inf")
                self.capture({})
        else:
            self.last_corner = None

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
                        rect = QRect(origin, QCursor.pos()).normalized()
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
            self.show_dashboard(2)
        elif command == "empty-group":
            for pin_id in [item for item, window in self.pin_windows.items() if window.state["group"] == self.group]:
                self.pin_action("delete", pin_id)
        elif command == "show":
            self.show_dashboard()
        elif command == "hide":
            self.dashboard.hide()
        elif command == "restore-pins":
            self.recover_pins()
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
            for window in self.pin_windows.values():
                window.set_state("click_through", False)
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
        QMessageBox.information(
            self.dashboard, "Snaploom",
            self.tr("F1 captures a region. Enter copies the screenshot. F3 pastes or restores a pin. "
                    "Open Settings to customize these shortcuts. The More menu includes offline text and barcode recognition.",
                    "F1 区域截图，回车复制，F3 从剪贴板贴图或恢复贴图。"
                    "可在设置中更改快捷键，截图工具栏的「更多」提供离线文字和条码识别。"),
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
        image = QImage(480, 180, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.white)
        geometry = QRect(0, 0, 480, 180)
        snapshot = Snapshot(image, geometry)
        selected = QRect(5, 5, 210, 90)
        editor = CaptureEditor(snapshot, selection=selected)
        assert editor.selected_geometry() == selected
        assert editor.export_image().size() == selected.size()
        app.workspace.remember(image, geometry, "copy")
        assert app.workspace.history_image(app.workspace.history()[0][0]).size() == image.size()
        pin_id = app.open_pin(image, QPoint(20, 40))
        app.pin_action("hide", pin_id)
        assert not app.pin_windows[pin_id].isVisible()
        app.pin_action("show", pin_id)
        path = save_image(image, Path(directory) / "capture.png")
        assert read_image(path).size() == image.size()
        if ocr:
            from PySide6.QtGui import QFont, QPainter

            painter = QPainter(image)
            painter.setPen(QColor("black"))
            painter.setFont(QFont("Arial", 32, QFont.Weight.Bold))
            painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "SNAPLOOM 123")
            painter.end()
            result = recognize(image, "ocr")
            assert "SNAPLOOM" in result.upper(), result
            import zxingcpp
            from .imaging import pixels

            barcode = zxingcpp.create_barcode("Snaploom", zxingcpp.BarcodeFormat.QRCode)
            code = zxingcpp.write_barcode_to_image(barcode, scale=4)
            code_image = QImage(memoryview(code), code.shape[1], code.shape[0], code.shape[1],
                                QImage.Format.Format_Grayscale8).copy()
            assert recognize(code_image, "barcode") == "Snaploom", pixels(code_image).shape
        editor.close()
        for pin in app.pin_windows.values():
            pin.hide()
        app.shutdown()
    print("Snaploom smoke test passed")
    return 0


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
            if not set(data.get("output") or []) & {"clipboard", "pin", "ocr_clipboard"}:
                qt.exit(code)

        QTimer.singleShot(0, lambda: application.command(data, print_result))
    else:
        QTimer.singleShot(0, lambda: application.command(data))
    return qt.exec()
