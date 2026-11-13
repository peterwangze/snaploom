"""Accessible workspace and preferences for Snaploom."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from .capture import disable_capture_window_transitions
from .hotkeys import LABELS, editor_key_for_hotkey, parse
from .storage import DEFAULTS


HOTKEY_ZH = {
    "capture": "区域截图", "paste": "悬浮剪贴板内容", "toggle_pins": "显示或隐藏贴图",
    "repeat": "重复上次区域", "click_through": "切换贴图鼠标穿透", "active": "截取当前窗口",
}
EDITOR_ACTIONS = {
    "copy": ("Save and copy screenshot", "保存并复制截图"), "save": ("Save As…", "另存为…"),
    "pin": ("Pin screenshot", "悬浮截图"), "quick-save": ("Quick save", "快速保存"),
    "copy-file": ("Save As and Copy File…", "另存为并复制文件…"), "refresh": ("Refresh screenshot", "刷新截图"),
    "color": ("Copy pixel color", "复制像素颜色"), "undo": ("Undo step", "撤销一步"),
    "redo": ("Redo step", "重做一步"),
}


class WorkspaceList(QListWidget):
    open_requested = Signal(object)
    delete_requested = Signal()

    def __init__(self):
        super().__init__()
        self.itemDoubleClicked.connect(self.open_item)

    def open_item(self, item):
        self.open_requested.emit(item.data(Qt.ItemDataRole.UserRole))

    def keyPressEvent(self, event):
        if event.modifiers() in {Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier}:
            if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                item = self.currentItem()
                if item is not None and item.isSelected():
                    self.open_item(item)
                event.accept()
                return
            if event.key() == Qt.Key.Key_Delete:
                self.delete_requested.emit()
                event.accept()
                return
        super().keyPressEvent(event)


class Dashboard(QWidget):
    action = Signal(str)
    history_action = Signal(str, int)
    pin_action = Signal(str, int)
    export_action = Signal(str, str)
    pin_delete_requested = Signal(list)
    files_dropped = Signal(list)
    closing = Signal()

    def __init__(self, translate):
        super().__init__()
        self.tr = translate
        self.setWindowTitle("Snaploom")
        self.resize(820, 610)
        self.setMinimumSize(630, 530)
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 14)
        layout.setSpacing(15)
        header = QFrame(objectName="Brand")
        content = QHBoxLayout(header)
        content.setContentsMargins(21, 15, 21, 15)
        title = QLabel("Snaploom", objectName="BrandTitle")
        title.setAccessibleName("Snaploom")
        content.addWidget(title)
        content.addStretch()
        content.addWidget(QLabel(self.tr("Screenshot workspace", "截图工作台"), objectName="BrandSubtitle"))
        layout.addWidget(header)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self.home(), self.tr("Capture", "截图"))
        self.history_list = WorkspaceList()
        self.pin_list = WorkspaceList()
        self._history_rows = None
        self._list_wide_actions = {}
        self.pin_list.setIconSize(QSize(56, 40))
        self.history_list.setAccessibleName(self.tr("Screenshot history", "截图历史"))
        self.pin_list.setAccessibleName(self.tr("Pinned references", "贴图"))
        self.history_list.open_requested.connect(lambda item_id: self.history_action.emit("preview", item_id))
        self.pin_list.open_requested.connect(lambda item_id: self.pin_action.emit("show", item_id))
        history_page = self.list_page(self.history_list, [
            ("preview", "Preview", "预览"), ("copy", "Copy", "复制"), ("pin", "Pin", "贴图"),
            ("save", "Save As…", "另存为…"), ("delete", "Delete", "删除"), ("clear", "Clear history…", "清空历史…"),
        ], self.history_action)
        pins_page = self.list_page(self.pin_list, [
            ("show", "Show", "显示"), ("hide", "Hide", "隐藏"), ("recover", "Recover off-screen", "找回屏幕外贴图"),
            ("group", "Move…", "移动…"), ("solo", "Solo", "独显"), ("delete", "Delete", "删除"),
        ], self.pin_action)
        manager = QPushButton(self.tr("Manage groups…", "管理分组…"))
        manager.clicked.connect(lambda: self.action.emit("manage-groups"))
        pins_page.layout().insertWidget(0, manager, alignment=Qt.AlignmentFlag.AlignRight)
        self.pin_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.tabs.addTab(history_page, self.tr("History", "历史"))
        self.tabs.addTab(pins_page, self.tr("Pins", "贴图"))
        self.exports_page = self.saved_files_page()
        self.tabs.addTab(self.exports_page, self.tr("Saved files", "已保存文件"))
        bottom = QHBoxLayout()
        self.status = QLabel(self.tr("Ready", "就绪"))
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        bottom.addWidget(self.status)
        self.save_location_button = QPushButton(self.tr("Open last save location", "打开最近保存位置"))
        self.save_location_button.clicked.connect(lambda: self.action.emit("open-save-location"))
        self.save_location_button.hide()
        bottom.addWidget(self.save_location_button)
        help_button = QPushButton(self.tr("Help", "操作帮助"))
        help_button.clicked.connect(lambda: self.action.emit("help"))
        bottom.addWidget(help_button)
        prefs = QPushButton(self.tr("Settings", "设置"))
        prefs.clicked.connect(lambda: self.action.emit("settings"))
        bottom.addWidget(prefs)
        layout.addLayout(bottom)
        self.setStyleSheet("""
            #Brand { background: #152841; border-radius: 12px; }
            #BrandTitle { color: #ffffff; font-size: 25px; font-weight: bold; }
            #BrandSubtitle { color: #bdcce1; }
            QPushButton { padding: 7px 11px; }
            QPushButton#Primary { background: #2164d5; color: white; border: 1px solid #1859bc;
                                  border-radius: 9px; font-weight: bold; padding: 15px; }
            QPushButton#Primary:hover { background: #1859bc; }
            QPushButton#Primary:focus { border: 2px solid #82afff; }
            QPushButton#Secondary { padding: 15px; font-weight: bold; }
            QLabel#Section { font-size: 15px; font-weight: bold; }
            QTabWidget::pane { top: -1px; }
            QListWidget::item { padding: 7px; }
        """)

    def saved_files_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self.exports_empty = QLabel(self.tr("Images you save will appear here.", "保存过的图片会显示在这里。"))
        layout.addWidget(self.exports_empty)
        self.export_list = WorkspaceList()
        self.export_list.setAccessibleName(self.tr("Saved files", "已保存文件"))
        self.export_list.open_requested.connect(lambda path: self.export_action.emit("open", path))
        layout.addWidget(self.export_list, 1)
        controls = QHBoxLayout()
        for action, english, chinese in (
            ("open", "Open folder", "打开所在文件夹"),
            ("copy", "Copy file path", "复制文件路径"),
        ):
            button = QPushButton(self.tr(english, chinese))
            button.setEnabled(False)
            self.export_list.itemSelectionChanged.connect(
                lambda button=button: button.setEnabled(bool(self.export_list.selectedItems()))
            )
            button.clicked.connect(lambda checked=False, action=action: self.export_action.emit(
                action, self.export_list.selectedItems()[0].data(Qt.ItemDataRole.UserRole)
            ))
            controls.addWidget(button)
        controls.addStretch()
        self.clear_exports_button = QPushButton(self.tr("Clear records…", "清空记录…"))
        self.clear_exports_button.clicked.connect(lambda: self.action.emit("clear-saved-files"))
        controls.addWidget(self.clear_exports_button)
        layout.addLayout(controls)
        return page

    def update_saved_files(self, rows):
        selected = self.export_list.selectedItems()
        selected_path = selected[0].data(Qt.ItemDataRole.UserRole) if selected else None
        self.export_list.clear()
        for path, timestamp in rows:
            stamp = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            item = QListWidgetItem(f"{path.name}    ·    {stamp}\n{path.parent}")
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.export_list.addItem(item)
            if str(path) == selected_path:
                self.export_list.setCurrentItem(item)
        if rows and not self.export_list.selectedItems():
            self.export_list.setCurrentRow(0)
        self.exports_empty.setVisible(not rows)
        self.clear_exports_button.setEnabled(bool(rows))

    def home(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 18, 16, 16)
        layout.setSpacing(12)
        actions = QHBoxLayout()
        for action, en, zh, style in (
            ("capture", "Capture region", "区域截图", "Primary"),
            ("paste", "Pin clipboard content", "悬浮剪贴板内容", "Secondary"),
        ):
            button = QPushButton(self.tr(en, zh), objectName=style)
            button.setMinimumHeight(55)
            button.clicked.connect(lambda checked=False, selected=action: self.action.emit(selected))
            actions.addWidget(button, 1)
        layout.addLayout(actions)
        self.shortcuts = QLabel()
        self.shortcuts.setWordWrap(True)
        layout.addWidget(self.shortcuts)
        layout.addSpacing(12)
        layout.addWidget(QLabel(self.tr("Capture modes", "截图方式"), objectName="Section"))
        for row in (
            (("screen", "Current display", "当前屏幕"), ("full", "All displays", "所有屏幕"), ("active", "Active window", "当前窗口")),
            (("repeat", "Repeat last region", "重复上次区域"), ("custom", "Size and delay…", "尺寸与延时…"),
             ("whiteboard", "Whiteboard…", "白板…")),
        ):
            line = QHBoxLayout()
            for action, en, zh in row:
                button = QPushButton(self.tr(en, zh))
                button.clicked.connect(lambda checked=False, selected=action: self.action.emit(selected))
                line.addWidget(button, 1)
            layout.addLayout(line)
        layout.addSpacing(10)
        pin_hint = QLabel(self.tr("Keep a screenshot, image, or text above other windows while you work.",
                                  "贴图会把截图、图片或文字悬浮在其他窗口上方，方便随时对照。"))
        pin_hint.setWordWrap(True)
        layout.addWidget(pin_hint)
        line = QHBoxLayout()
        for action, en, zh in (
            ("open", "Open image…", "打开图像…"),
            ("restore-last-pin", "Restore last hidden pin", "恢复最近隐藏的贴图"),
            ("toggle_pins", "Show or hide pins", "显示或隐藏贴图"),
        ):
            button = QPushButton(self.tr(en, zh))
            button.clicked.connect(lambda checked=False, selected=action: self.action.emit(selected))
            line.addWidget(button, 1)
        layout.addLayout(line)
        layout.addStretch(1)
        return page

    def list_page(self, widget, buttons, signal):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(widget)
        row = QHBoxLayout()
        row.setSpacing(5)
        for action, en, zh in buttons:
            button = QPushButton(self.tr(en, zh))
            button.clicked.connect(lambda checked=False, selected=action: self.emit_selection(widget, selected, signal))
            if action in {"clear", "recover"}:
                self._list_wide_actions[widget] = button
                button.setEnabled(False)
            else:
                def update_enabled(control=button, selected=action):
                    count = len(widget.selectedItems())
                    control.setEnabled(count == 1 if selected == "solo" else count > 0)
                widget.itemSelectionChanged.connect(update_enabled)
                update_enabled()
            row.addWidget(button)
        layout.addLayout(row)
        widget.delete_requested.connect(lambda: self.emit_selection(widget, "delete", signal))
        return page

    def emit_selection(self, widget, action, signal):
        if action in {"clear", "recover", "solo"}:
            current = widget.currentItem()
            if action == "solo" and (current is None or not current.isSelected()):
                return
            signal.emit(action, current.data(Qt.ItemDataRole.UserRole) if current else -1)
            return
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in widget.selectedItems()]
        if widget is self.pin_list and action == "delete":
            if ids:
                self.pin_delete_requested.emit(ids)
            return
        for item_id in ids:
            signal.emit(action, item_id)

    def update_history(self, rows):
        if self._history_rows == rows:
            return
        self._history_rows = rows
        selected = self.history_list.selectedItems()
        selected_id = selected[0].data(Qt.ItemDataRole.UserRole) if selected else None
        was_empty = not self.history_list.count()
        self.history_list.clear()
        actions = {
            "copy": ("Copied", "已复制"), "save-copy": ("Saved and copied", "已保存并复制"), "pin": ("Pinned", "已贴图"),
            "save": ("Saved", "已保存"), "saved": ("Saved", "已保存"),
            "quick-save": ("Quick saved", "已快速保存"), "copy-file": ("File copied", "已保存并复制文件"),
            "print": ("Printed", "已打印"), "ocr": ("Text recognized", "已识别文字"),
            "barcode": ("Code scanned", "已识别条码"),
        }
        for item_id, timestamp, _, action in rows:
            stamp = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
            label = self.tr(*actions.get(action, ("Exported", "已输出")))
            item = QListWidgetItem(f"{stamp}    ·    {label}")
            item.setData(Qt.ItemDataRole.UserRole, item_id)
            self.history_list.addItem(item)
            if item_id == selected_id:
                self.history_list.setCurrentItem(item)
        if was_empty and self.history_list.count():
            self.history_list.setCurrentRow(0)
        self._list_wide_actions[self.history_list].setEnabled(bool(rows))

    def update_pins(self, pins, current_group):
        selected = {item.data(Qt.ItemDataRole.UserRole) for item in self.pin_list.selectedItems()}
        current = self.pin_list.currentItem()
        current_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        was_empty = not self.pin_list.count()
        self.pin_list.clear()
        for pin_id, window in pins.items():
            group = window.state["group"]
            if window.isVisible():
                label = self.tr("Visible", "已显示")
            elif not window.state["visible"]:
                label = self.tr("Hidden individually", "已单独隐藏")
            elif group != current_group:
                label = self.tr("In another group", "位于其他分组")
            else:
                label = self.tr("Temporarily hidden", "暂时隐藏")
            source = " ".join(window.state.get("source_text", "").split())
            detail = f"{window.image.width()} × {window.image.height()}"
            if source:
                detail = source[:71] + "…" if len(source) > 72 else source
            thumbnail = window.view.scaled(self.pin_list.iconSize(), Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
            item = QListWidgetItem(QIcon(QPixmap.fromImage(thumbnail)), f"{detail}    ·    {group}    ·    {label}")
            item.setData(Qt.ItemDataRole.UserRole, pin_id)
            self.pin_list.addItem(item)
            if pin_id == current_id:
                self.pin_list.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
            item.setSelected(pin_id in selected)
        if was_empty and self.pin_list.count():
            self.pin_list.setCurrentRow(0)
        self._list_wide_actions[self.pin_list].setEnabled(bool(pins))

    def showEvent(self, event):
        disable_capture_window_transitions(self)
        super().showEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()

    def closeEvent(self, event):
        super().closeEvent(event)
        self.closing.emit()


class Preferences(QDialog):
    def __init__(self, workspace, translate, parent, *, apply_hotkeys=None):
        super().__init__(parent)
        from .editor import DEFAULT_EDITOR_SHORTCUTS, TOOLS

        self.tr = translate
        self.workspace = workspace
        self.apply_hotkeys = apply_hotkeys
        self.editor_defaults = DEFAULT_EDITOR_SHORTCUTS
        self.editor_labels = {tool: self.tr(en, zh) for tool, en, zh, _ in TOOLS} | {
            name: self.tr(en, zh) for name, (en, zh) in EDITOR_ACTIONS.items()
        }
        self.setWindowTitle(self.tr("Snaploom settings", "Snaploom 设置"))
        self.resize(550, 470)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        general = QWidget()
        form = QFormLayout(general)
        tabs.addTab(general, self.tr("General", "常规"))
        self.fields = {}
        for key, en, zh in (
            ("auto_save", "Automatically save other completed captures", "自动保存其他已完成的截图"),
            ("copy_after_save", "Also copy the saved file when saving only", "仅保存时也复制已保存的文件"),
            ("include_cursor", "Include the cursor in screenshots", "截图包含鼠标指针"),
            ("restore_pins", "Reopen visible pins on startup", "启动时重新显示上次可见的贴图"),
            ("hide_pins_on_capture", "Hide pins during capture", "截图时隐藏贴图"),
            ("run_at_login", "Start in the background at sign-in", "登录时在后台启动"),
            ("start_hidden", "Start in the tray", "启动后收起到托盘"),
            ("allow_remote_images", "Fetch images from clipboard URLs", "允许下载剪贴板网址中的图像"),
            ("show_grid", "Show selection guides", "显示选区参考线"),
            ("show_magnifier", "Enable the Alt magnifier", "按住 Alt 显示放大镜"),
            ("detect_windows", "Suggest windows and controls under the cursor", "识别指针下的窗口和控件"),
        ):
            box = QCheckBox(self.tr(en, zh))
            box.setChecked(workspace.get(key))
            form.addRow(box)
            self.fields[key] = box
        for key, en, zh, minimum, maximum in (
            ("history_limit", "Screenshot history limit (0 clears and disables)", "截图历史条数上限（0 清空并关闭）", 0, 1000),
            ("default_pin_opacity", "Default pin opacity (%)", "贴图默认不透明度 (%)", 10, 100),
        ):
            spin = QSpinBox()
            spin.setRange(minimum, maximum)
            spin.setValue(workspace.get(key))
            form.addRow(self.tr(en, zh), spin)
            self.fields[key] = spin
        annotation = QWidget()
        annotation_form = QFormLayout(annotation)
        tabs.addTab(annotation, self.tr("Annotation", "标注"))
        width = QSpinBox(objectName="annotationDefaultWidth")
        width.setRange(1, 48)
        width.setValue(workspace.get("annotation_width"))
        annotation_form.addRow(self.tr("Default stroke width", "默认线宽"), width)
        self.fields["annotation_width"] = width
        remember = QCheckBox(
            self.tr("Remember the last settings for each tool", "记住各标注工具上次使用的设置"),
            objectName="rememberAnnotationStyles",
        )
        remember.setChecked(workspace.get("remember_annotation_styles"))
        annotation_form.addRow(remember)
        self.fields["remember_annotation_styles"] = remember
        annotation_hint = QLabel(self.tr(
            "Tools without a saved width use the default width.",
            "工具没有已保存的线宽时，使用默认线宽。",
        ))
        annotation_hint.setWordWrap(True)
        annotation_form.addRow(annotation_hint)
        self.clear_annotation_styles = QCheckBox(
            self.tr("Clear saved tool styles when saving", "保存时清除各工具已记住的样式"),
            objectName="clearAnnotationStyles",
        )
        self.clear_annotation_styles.setEnabled(bool(workspace.get("annotation_styles")))
        annotation_form.addRow(self.clear_annotation_styles)
        output = QWidget()
        output_form = QFormLayout(output)
        tabs.addTab(output, self.tr("Output", "输出"))
        folder = QLineEdit(workspace.get("save_folder"))
        pick = QPushButton(self.tr("Browse…", "浏览…"))
        pick.clicked.connect(lambda: self.choose_folder(folder))
        row = QHBoxLayout()
        row.addWidget(folder, 1)
        row.addWidget(pick)
        output_form.addRow(self.tr("Default save folder", "默认保存目录"), row)
        self.fields["save_folder"] = folder
        pattern = QLineEdit(workspace.get("save_pattern"))
        pattern.setToolTip("Python strftime, %title%, %width%, %height%")
        output_form.addRow(self.tr("Default filename pattern", "默认文件名模板"), pattern)
        self.fields["save_pattern"] = pattern
        corner = QSpinBox()
        corner.setRange(0, 1024)
        corner.setValue(workspace.get("rounded_corners"))
        output_form.addRow(self.tr("Corner radius (logical pixels)", "截图圆角（逻辑像素）"), corner)
        self.fields["rounded_corners"] = corner
        shadow = QCheckBox(self.tr("Add a drop shadow around screenshots", "为截图添加阴影"))
        shadow.setChecked(workspace.get("capture_shadow"))
        output_form.addRow(shadow)
        self.fields["capture_shadow"] = shadow
        notifications = QCheckBox(self.tr("Show success notifications", "显示操作成功通知"))
        notifications.setToolTip(self.tr("Show a desktop notification after copying or saving an image.",
                                         "复制或保存图片后显示桌面通知。"))
        notifications.setChecked(workspace.get("show_success_notifications"))
        output_form.addRow(notifications)
        self.fields["show_success_notifications"] = notifications
        provider = QComboBox()
        provider.addItem(self.tr("On this device", "在本机识别"), "local")
        provider.addItem("OCR.space", "ocr-space")
        provider.addItem(self.tr("Tencent Cloud", "腾讯云"), "tencent")
        provider.setCurrentIndex(max(0, provider.findData(workspace.get("ocr_provider"))))
        provider.setToolTip(self.tr("Cloud providers receive the image you submit for recognition.",
                                    "云端服务会收到你提交识别的图像。"))
        output_form.addRow(self.tr("Text recognition provider", "文字识别服务"), provider)
        self.fields["ocr_provider"] = provider
        for key, en, zh, values in (
            ("format", "Image format", "图像格式", ("png", "jpg", "webp", "bmp")),
            ("color_format", "Color picker format", "取色格式", ("HEX", "RGB", "HSL", "HSV", "CMYK")),
        ):
            combo = QComboBox()
            combo.addItems(values)
            combo.setCurrentText(workspace.get(key))
            output_form.addRow(self.tr(en, zh), combo)
            self.fields[key] = combo
        shortcuts = QWidget()
        shortcut_form = QFormLayout(shortcuts)
        tabs.addTab(shortcuts, self.tr("Global shortcuts", "全局快捷键"))
        self.shortcuts = {}
        for action, label in LABELS.items():
            field = QLineEdit(workspace.get("hotkeys").get(action, ""))
            field.setPlaceholderText(self.tr("Disabled", "未设置"))
            shortcut_form.addRow(self.tr(label, HOTKEY_ZH[action]), field)
            self.shortcuts[action] = field
        hint = QLabel(self.tr("Global shortcuts work even while another app is active. Leave a field blank to disable it.",
                              "全局快捷键在使用其他应用时也能触发。留空即可禁用对应操作。"))
        hint.setWordWrap(True)
        shortcut_form.addRow(hint)
        for key, en, zh, choices in (
            ("hot_corner", "Pause in a corner to capture", "鼠标停留触发截图", (
                ("", "Off", "关闭"), ("top-left", "Top left", "左上角"), ("top-right", "Top right", "右上角"),
                ("bottom-left", "Bottom left", "左下角"), ("bottom-right", "Bottom right", "右下角"),
            )),
            ("super_snip", "Modifier + right-drag to capture", "修饰键 + 右键拖拽截图", (
                ("", "Off", "关闭"), ("alt", "Alt / Option", "Alt / Option"), ("ctrl", "Ctrl", "Ctrl"),
                ("shift", "Shift", "Shift"), ("super", "Windows / Command / Super", "Windows / Command / Super"),
            )),
        ):
            combo = QComboBox()
            for value, english, chinese in choices:
                combo.addItem(self.tr(english, chinese), value)
            combo.setCurrentIndex(max(0, combo.findData(workspace.get(key))))
            shortcut_form.addRow(self.tr(en, zh), combo)
            self.fields[key] = combo
        reset_global = QPushButton(self.tr("Restore default global shortcuts", "恢复默认全局快捷键"))
        reset_global.clicked.connect(lambda: self.reset_shortcuts(self.shortcuts, DEFAULTS["hotkeys"]))
        shortcut_form.addRow(reset_global)
        commands = QWidget()
        command_layout = QVBoxLayout(commands)
        tabs.addTab(commands, self.tr("Custom outputs", "自定义输出"))
        command_hint = QLabel(self.tr(
            "Enter one name = command per line. Use {image} for the saved file path. "
            "Outputs run only when selected from the screenshot menu.",
            "每行填写一项「名称 = 命令」。用 {image} 代替已保存的文件路径。"
            "只有在截图菜单中选中后才会执行。",
        ))
        command_hint.setWordWrap(True)
        command_layout.addWidget(command_hint)
        self.commands = QPlainTextEdit()
        self.commands.setPlainText("\n".join(
            f"{name} = {command}" for name, command in workspace.get("custom_commands").items()
        ))
        self.commands.setPlaceholderText(self.tr("Open in editor = my-editor {image}", "在编辑器打开 = my-editor {image}"))
        command_layout.addWidget(self.commands, 1)
        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        tabs.addTab(editor, self.tr("Editor shortcuts", "编辑器快捷键"))
        editor_help = QLabel(self.tr(
            "Edit one key combination per action, for example R or Ctrl+Shift+S. Leave blank to disable that shortcut.",
            "每个操作可设置一个组合键，例如 R 或 Ctrl+Shift+S。留空即可禁用对应快捷键。",
        ))
        editor_help.setWordWrap(True)
        editor_layout.addWidget(editor_help)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        editor_fields = QWidget()
        editor_form = QFormLayout(editor_fields)
        scroll.setWidget(editor_fields)
        self.editor_shortcuts = {}
        editor_values = self.editor_defaults | workspace.get("editor_shortcuts")
        for action in self.editor_defaults:
            field = QLineEdit(editor_values[action])
            field.setPlaceholderText(self.tr("Disabled", "未设置"))
            editor_form.addRow(self.editor_labels[action], field)
            self.editor_shortcuts[action] = field
        editor_layout.addWidget(scroll, 1)
        reset_editor = QPushButton(self.tr("Restore default editor shortcuts", "恢复默认编辑器快捷键"))
        reset_editor.clicked.connect(lambda: self.reset_shortcuts(self.editor_shortcuts, self.editor_defaults))
        editor_layout.addWidget(reset_editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_folder(self, field):
        selected = QFileDialog.getExistingDirectory(self, self.tr("Default save folder", "默认保存目录"),
                                                    field.text() or str(Path.home()))
        if selected:
            field.setText(selected)

    @staticmethod
    def reset_shortcuts(fields, defaults):
        for action, field in fields.items():
            field.setText(defaults[action])

    def values(self):
        data = {}
        for key, widget in self.fields.items():
            if isinstance(widget, QCheckBox):
                data[key] = widget.isChecked()
            elif isinstance(widget, QSpinBox):
                data[key] = widget.value()
            elif isinstance(widget, QComboBox):
                data[key] = widget.currentData() if widget.currentData() is not None else widget.currentText()
            else:
                data[key] = widget.text().strip()
        data["hotkeys"] = {key: field.text().strip() for key, field in self.shortcuts.items()}
        data["editor_shortcuts"] = {key: field.text().strip() for key, field in self.editor_shortcuts.items()
                                    if field.text().strip() != self.editor_defaults[key]}
        if self.clear_annotation_styles.isChecked():
            data["annotation_styles"] = {}
        commands = {}
        for line in self.commands.toPlainText().splitlines():
            name, _, command = (part.strip() for part in line.partition("="))
            if name and command:
                commands[name] = command
        data["custom_commands"] = commands
        return data

    def warn_field(self, field, message):
        QMessageBox.warning(self, self.windowTitle(), message)
        tabs = self.findChild(QTabWidget)
        page = next(tabs.widget(index) for index in range(tabs.count()) if tabs.widget(index).isAncestorOf(field))
        tabs.setCurrentWidget(page)
        field.setFocus()
        if isinstance(field, QLineEdit):
            field.selectAll()

    def validate_shortcuts(self, data):
        reserved = {"Esc", "Return", "Enter", "Space", "Del", "Backspace", "Tab", "Backtab", "Shift+Tab",
                    "Ctrl+A", "Ctrl+M", "Alt", ",", "."}
        reserved.update(modifier + direction for direction in ("Left", "Right", "Up", "Down")
                        for modifier in ("", "Shift+", "Ctrl+", "Ctrl+Shift+"))
        used = {}
        editor_values = self.editor_defaults | data["editor_shortcuts"]
        saved_editor = self.editor_defaults | self.workspace.get("editor_shortcuts")
        for action, value in editor_values.items():
            if not value:
                continue
            sequence = QKeySequence.fromString(value, QKeySequence.SequenceFormat.PortableText)
            normalized = sequence.toString(QKeySequence.SequenceFormat.PortableText)
            label = self.editor_labels[action]
            if sequence.count() != 1 or sequence[0].key() == Qt.Key.Key_unknown:
                return self.editor_shortcuts[action], self.tr(f"Enter one valid shortcut for {label}.",
                                                              f"请为「{label}」填写一个有效的快捷键。")
            if normalized in reserved:
                return self.editor_shortcuts[action], self.tr(f"{value} is reserved by the editor.",
                                                              f"{value} 已用于编辑器的固定操作。")
            if normalized in used:
                previous = used[normalized]
                if value == saved_editor[action] and editor_values[previous] != saved_editor[previous]:
                    action, previous = previous, action
                other = self.editor_labels[previous]
                return self.editor_shortcuts[action], self.tr(f"{value} is already assigned to {other}.",
                                                              f"{value} 已分配给「{other}」。")
            used[normalized] = action
        if used.get(self.editor_defaults["redo"]) == "redo":
            used.setdefault("Ctrl+Y", "redo")

        global_keys = {}
        saved_global = self.workspace.get("hotkeys")
        for action, value in data["hotkeys"].items():
            if not value:
                continue
            field = self.shortcuts[action]
            label = self.tr(LABELS[action], HOTKEY_ZH[action])
            try:
                modifiers, key, code = parse(value)
            except ValueError:
                return field, self.tr(f"Enter a valid global shortcut for {label}, such as F1 or Ctrl+Alt+S.",
                                      f"请为「{label}」填写有效的全局快捷键，例如 F1 或 Ctrl+Alt+S。")
            identity = frozenset(modifiers), code
            if identity in global_keys:
                previous = global_keys[identity]
                if value == saved_global[action] and data["hotkeys"][previous] != saved_global[previous]:
                    action, previous = previous, action
                other = self.tr(LABELS[previous], HOTKEY_ZH[previous])
                return self.shortcuts[action], self.tr(f"{value} is already assigned to {other}.",
                                                       f"{value} 已分配给「{other}」。")
            global_keys[identity] = action
            portable = editor_key_for_hotkey(modifiers, key)
            normalized = QKeySequence.fromString(portable, QKeySequence.SequenceFormat.PortableText).toString(
                QKeySequence.SequenceFormat.PortableText)
            if normalized in used or normalized in reserved:
                editor_action = used.get(normalized)
                if editor_action and value == saved_global[action] and editor_values[editor_action] != saved_editor[editor_action]:
                    return self.editor_shortcuts[editor_action], self.tr(f"{value} is already assigned globally to {label}.",
                                                                         f"{value} 已分配给全局操作「{label}」。")
                return field, self.tr(f"{value} would intercept an editor shortcut. Choose a different global shortcut.",
                                      f"{value} 会拦截编辑器中的操作，请选择其他全局快捷键。")
        return None

    def accept(self):
        data = self.values()
        invalid = self.validate_shortcuts(data)
        if invalid:
            self.warn_field(*invalid)
            return
        commands = set()
        for index, line in enumerate(self.commands.toPlainText().splitlines(), 1):
            if not line.strip():
                continue
            name, _, command = (part.strip() for part in line.partition("="))
            if not name or not command or name in commands:
                self.warn_field(self.commands, self.tr(f"Custom output line {index} needs a unique name and a command.",
                                                       f"自定义输出的第 {index} 行需要唯一的名称和命令。"))
                return
            commands.add(name)
        removed = self.workspace.history_count() - data["history_limit"]
        if removed > 0:
            confirm = QMessageBox.question(
                self, self.tr("Reduce screenshot history", "缩减截图历史"),
                self.tr(f"Saving this limit will permanently remove the {removed} oldest screenshots from local history.",
                        f"保存此上限将永久删除本地历史中最早的 {removed} 张截图。"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        if self.apply_hotkeys and (conflict := self.apply_hotkeys(data["hotkeys"])):
            action, message = conflict
            self.warn_field(self.shortcuts[action], message)
            return
        super().accept()
