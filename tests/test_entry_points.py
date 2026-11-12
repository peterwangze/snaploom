import tempfile
import unittest
from pathlib import Path
from threading import Event
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QMessageBox, QPushButton,
    QSpinBox, QTextEdit, QToolButton,
)
from shiboken6 import isValid

from snaploom.app import Application
from snaploom.capture import CaptureError, Snapshot
from snaploom.commands import arguments


class DashboardEntryPointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        if cls.qt.platformName() != "offscreen":
            raise unittest.SkipTest("Set QT_QPA_PLATFORM=offscreen to isolate the desktop and clipboard.")
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.folder = Path(directory.name).resolve()
        self.app = Application(self.qt, self.folder, testing=True)
        self.addCleanup(self.close_app)
        self.qt.clipboard().clear()
        self.addCleanup(self.qt.clipboard().clear)
        self.app.show_dashboard()
        self.qt.processEvents()

    def close_app(self):
        if self.app.editor:
            self.app.editor.close()
        for dialog in tuple(self.app.dialogs):
            dialog.reject()
        for pin in self.app.pin_windows.values():
            pin.hide()
            pin.deleteLater()
        self.app.dashboard.hide()
        self.app.dashboard.deleteLater()
        self.app.shutdown()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    @staticmethod
    def image(width, height, color):
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        return image

    def click_button(self, en, zh, *, parent=None, widget=QPushButton):
        scope = self.app.dashboard if parent is None else parent
        label = self.app.tr(en, zh)
        button = next(button for button in scope.findChildren(widget)
                      if button.isVisible() and button.text() == label)
        self.click_at(button)

    def click_at(self, widget, point=None):
        window = widget.window()
        local = window.mapFromGlobal(widget.mapToGlobal(widget.rect().center() if point is None else point))
        hit = window.childAt(local) or window
        self.assertTrue(hit is widget or widget.isAncestorOf(hit), f"{widget} is covered or clipped")
        QTest.mouseClick(window.windowHandle(), Qt.MouseButton.LeftButton, pos=local)

    def click_tab(self, index):
        tabs = self.app.dashboard.tabs
        self.click_at(tabs.tabBar(), tabs.tabBar().tabRect(index).center())
        self.assertEqual(tabs.currentIndex(), index)

    def click_pin_row(self, pin_id):
        pins = self.app.dashboard.pin_list
        item = next(pins.item(row) for row in range(pins.count())
                    if pins.item(row).data(Qt.ItemDataRole.UserRole) == pin_id)
        self.click_at(pins.viewport(), pins.visualItemRect(item).center())
        self.assertIs(pins.currentItem(), item)

    def open_capture(self, en, zh, image):
        with (
            patch("snaploom.capture.capture_desktop", return_value=Snapshot(image, image.rect())),
            patch("snaploom.capture.detect_windows", return_value=[]),
            patch("snaploom.capture.detect_elements", return_value=[]),
        ):
            self.click_button(en, zh)
            self.wait_for_capture()
        return self.app.editor

    def wait_for_capture(self):
        deadline = monotonic() + 2
        while self.app.capturing and self.app.editor is None and monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNotNone(self.app.editor, "Screenshot editor did not open.")
        self.assertTrue(self.app.editor.isVisible())

    def wait_until(self, predicate):
        deadline = monotonic() + 3
        while not predicate() and monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate())

    def click_capture_menu_item(self, editor, en, zh):
        more = next(button for button in editor.toolbar.findChildren(QToolButton) if button.menu())
        menu = more.menu()
        action = next(action for action in menu.actions() if action.text() == self.app.tr(en, zh))
        clicked = []

        def choose():
            clicked.append(True)
            QTest.mouseClick(menu.windowHandle(), Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
            if isValid(menu) and menu.isVisible():
                menu.hide()

        schedule_choice = lambda: QTimer.singleShot(0, choose)
        menu.aboutToShow.connect(schedule_choice)
        try:
            self.click_at(more)
        finally:
            if isValid(menu):
                menu.aboutToShow.disconnect(schedule_choice)
        self.assertEqual(clicked, [True])

    def test_solo_shows_a_pin_in_another_group_and_leaves_original_group_intact(self):
        image = self.image(120, 80, "#386ef2")
        original = self.app.open_pin(image, QPoint(25, 40))
        target = self.app.open_pin(image, QPoint(160, 40), state={"group": "Review"})
        peer = self.app.open_pin(image, QPoint(290, 40), state={"group": "Review"})
        pins = self.app.pin_windows
        self.assertTrue(pins[original].isVisible())
        self.assertFalse(pins[target].isVisible())

        self.click_tab(2)
        self.click_pin_row(target)
        self.click_button("Solo", "独显")

        self.assertEqual(self.app.group, "Review")
        self.assertTrue(pins[target].isVisible())
        self.assertFalse(pins[peer].isVisible())
        self.assertFalse(pins[original].isVisible())
        self.assertTrue(pins[original].state["visible"])

        self.click_pin_row(original)
        self.click_button("Show", "显示")
        self.assertEqual(self.app.group, "Default")
        self.assertTrue(pins[original].isVisible())
        self.assertFalse(pins[target].isVisible())

    def test_solo_ignores_the_focused_row_after_it_is_deselected(self):
        image = self.image(120, 80, "#386ef2")
        self.app.open_pin(image, QPoint(25, 40))
        target = self.app.open_pin(image, QPoint(160, 40))
        self.click_tab(2)
        self.click_pin_row(target)
        pins = self.app.dashboard.pin_list
        point = pins.visualItemRect(pins.currentItem()).center()
        QTest.mouseClick(pins.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier, pos=point)
        self.assertEqual(pins.selectedItems(), [])

        self.click_button("Solo", "独显")

        self.assertTrue(all(pin.isVisible() for pin in self.app.pin_windows.values()))

    def test_current_display_and_all_displays_use_distinct_bounds(self):
        screen = self.qt.primaryScreen().geometry()
        desktop = self.image(screen.width() + 210, screen.height(), "#386ef2")
        editor = self.open_capture("Current display", "当前屏幕", desktop)
        self.assertEqual(editor.selected_geometry(), screen.intersected(desktop.rect()))
        self.click_button("Copy", "复制", parent=editor.toolbar, widget=QToolButton)
        self.assertEqual(self.qt.clipboard().image().size(), screen.size())

        editor = self.open_capture("All displays", "所有屏幕", desktop)
        self.assertEqual(editor.selected_geometry(), desktop.rect())
        self.click_button("Copy", "复制", parent=editor.toolbar, widget=QToolButton)
        self.assertEqual(self.qt.clipboard().image().size(), desktop.size())

    def test_current_window_without_an_eligible_foreground_allows_window_selection(self):
        desktop = self.image(800, 520, "#386ef2")
        candidate = QRect(50, 90, 200, 130)

        def active_window():
            self.assertFalse(self.app.dashboard.isVisible())
            return None

        with (
            patch("snaploom.capture.capture_desktop", return_value=Snapshot(desktop, desktop.rect())),
            patch("snaploom.capture.active_window", side_effect=active_window),
            patch("snaploom.capture.detect_windows", return_value=[(candidate, "Editor")]),
            patch("snaploom.capture.detect_elements", return_value=[]),
        ):
            self.click_button("Active window", "当前窗口")
            self.wait_for_capture()
            editor = self.app.editor
            self.assertTrue(editor.selected_geometry().isEmpty())
            QTest.mouseMove(editor, candidate.center())
            QTest.mouseClick(editor, Qt.MouseButton.LeftButton, pos=candidate.center())

        self.assertEqual(editor.selected_geometry(), candidate)

    def test_repeat_without_history_starts_a_fresh_region_and_cancel_returns(self):
        desktop = self.image(800, 520, "#386ef2")
        editor = self.open_capture("Repeat last region", "重复上次区域", desktop)
        self.assertTrue(editor.selected_geometry().isEmpty())
        self.assertEqual(self.app.dashboard.status.text(),
                         self.app.tr("Select a region for your first capture.", "请先选择一个区域截图。"))
        start, finish = QPoint(40, 50), QPoint(180, 160)
        handle = editor.windowHandle()
        QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(handle, finish)
        QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=finish)
        self.assertEqual(editor.selected_geometry(), QRect(start, finish))
        self.click_button("Cancel", "取消", parent=editor.toolbar, widget=QToolButton)
        self.assertIsNone(self.app.editor)
        self.assertFalse(self.app.capturing)
        self.assertTrue(self.app.dashboard.isVisible())
        self.assertEqual(self.app.workspace.history(), [])

    def test_size_dialog_keyboard_entry_and_delay_gate_the_desktop_capture(self):
        desktop = self.image(800, 520, "#386ef2")
        requested = QRect(21, 42, 180, 150)
        original_exec = QDialog.exec
        accepted_at, capture_at, callback_errors = [], [], []

        def configure(dialog):
            def enter_values():
                try:
                    fields = dialog.findChildren(QSpinBox)
                    delay = dialog.findChild(QDoubleSpinBox)
                    values = [*map(str, requested.getRect()), delay.locale().toString(0.35, "f", 2)]
                    for spin, value in zip([*fields, delay], values, strict=True):
                        field = spin.lineEdit()
                        self.click_at(field)
                        QTest.keyClick(field, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
                        QTest.keyClicks(field, value)
                    accepted_at.append(monotonic())
                    buttons = dialog.findChild(QDialogButtonBox)
                    self.click_at(buttons.button(QDialogButtonBox.StandardButton.Ok))
                except Exception as error:
                    callback_errors.append(error)
                    dialog.reject()

            QTimer.singleShot(0, enter_values)
            return original_exec(dialog)

        def capture(**_):
            capture_at.append(monotonic())
            return Snapshot(desktop, desktop.rect())

        with (
            patch.object(QDialog, "exec", configure),
            patch("snaploom.capture.capture_desktop", side_effect=capture),
            patch("snaploom.capture.detect_windows", return_value=[]),
        ):
            self.click_button("Size and delay…", "尺寸与延时…")
            if callback_errors:
                raise callback_errors[0]
            self.assertTrue(self.app.capturing)
            self.assertIsNone(self.app.editor)
            self.assertEqual(capture_at, [])
            self.wait_for_capture()

        self.assertGreaterEqual(capture_at[0] - accepted_at[0], 0.28)
        self.assertEqual(self.app.editor.selected_geometry(), requested)

    def test_hot_corner_requires_a_new_dwell_after_a_capture_is_cancelled(self):
        desktop = self.image(800, 520, "#386ef2")
        corner = self.qt.primaryScreen().geometry().topLeft()
        self.app.workspace.set("hot_corner", "top-left")

        def tick(point, now):
            with patch("snaploom.app.QCursor.pos", return_value=point), patch("snaploom.app.time.monotonic", return_value=now):
                self.app.corner_timer.timeout.emit()

        with (
            patch("snaploom.capture.capture_desktop", return_value=Snapshot(desktop, desktop.rect())) as capture,
            patch("snaploom.capture.detect_windows", return_value=[]),
        ):
            tick(corner, 0.0)
            tick(corner, 0.5)
            self.assertFalse(self.app.capturing)
            tick(corner, 0.7)
            self.wait_for_capture()
            tick(corner, 1.0)
            QTest.keyClick(self.app.editor.windowHandle(), Qt.Key.Key_Escape)
            self.assertIsNone(self.app.editor)
            tick(corner, 2.0)
            tick(corner, 3.0)
            self.assertFalse(self.app.capturing)
            tick(corner + QPoint(30, 30), 4.0)
            tick(corner, 4.2)
            tick(corner, 5.0)
            self.wait_for_capture()
            self.assertEqual(capture.call_count, 2)

    def test_modifier_right_drag_captures_only_an_armed_valid_region(self):
        desktop = self.image(360, 260, "#386ef2")
        self.app.workspace.set("super_snip", "alt")
        alt, right = object(), object()
        keyboard_listener = Mock(canonical=lambda key: key)
        mouse_listener = Mock()
        keyboard = SimpleNamespace(Key=SimpleNamespace(alt=alt), Listener=Mock(return_value=keyboard_listener))
        mouse = SimpleNamespace(Button=SimpleNamespace(right=right), Listener=Mock(return_value=mouse_listener))
        with (
            patch.dict("sys.modules", {"pynput": SimpleNamespace(keyboard=keyboard, mouse=mouse)}),
            patch.dict("os.environ", {"XDG_SESSION_TYPE": "x11", "WAYLAND_DISPLAY": ""}),
        ):
            self.app.configure_super_snip()
        keyboard_listener.start.assert_called_once()
        mouse_listener.start.assert_called_once()
        press_key = keyboard.Listener.call_args.kwargs["on_press"]
        release_key = keyboard.Listener.call_args.kwargs["on_release"]
        click = mouse.Listener.call_args.kwargs["on_click"]

        def drag(start, end):
            with patch("snaploom.app.QCursor.pos", side_effect=[start, end]):
                click(start.x(), start.y(), right, True)
                click(end.x(), end.y(), right, False)

        start, end = QPoint(210, 150), QPoint(40, 60)
        drag(start, end)
        self.assertFalse(self.app.capturing)
        press_key(alt)
        drag(start, start - QPoint(3, 3))
        self.assertFalse(self.app.capturing)
        with (
            patch("snaploom.capture.capture_desktop", return_value=Snapshot(desktop, desktop.rect())),
            patch("snaploom.capture.detect_windows", return_value=[]),
        ):
            drag(start, end)
            release_key(alt)
            self.wait_for_capture()
        self.assertEqual(self.app.editor.selected_geometry(), QRect(end, start))
        QTest.keyClick(self.app.editor.windowHandle(), Qt.Key.Key_Escape)
        drag(start, end)
        self.assertFalse(self.app.capturing)

    def test_history_row_can_be_saved_copied_and_pinned(self):
        desktop = self.image(800, 520, "#386ef2")
        editor = self.open_capture("Capture region", "区域截图", desktop)
        region = QRect(45, 54, 180, 140)
        QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=region.topLeft())
        QTest.mouseMove(editor, region.bottomRight())
        QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=region.bottomRight())
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.click_tab(1)
        self.qt.clipboard().clear()

        self.click_button("Copy", "复制")
        self.assertEqual(self.qt.clipboard().image().size(), region.size())
        self.assertEqual(self.qt.clipboard().image().pixelColor(5, 5), QColor("#386ef2"))
        target = self.folder / "history.png"
        with patch("snaploom.app.QFileDialog.getSaveFileName", return_value=(str(target), "PNG (*.png)")):
            self.click_button("Save As…", "另存为…")
        self.assertEqual(QImage(str(target)).pixelColor(5, 5), QColor("#386ef2"))
        self.click_button("Pin", "贴图")
        pin, = self.app.pin_windows.values()
        self.assertTrue(pin.isVisible())
        self.assertEqual(pin.image.size(), region.size())
        self.assertEqual(len(self.app.workspace.history()), 1)

    def test_file_output_failure_keeps_annotations_and_quick_save_can_retry(self):
        occupied_path = self.folder / "existing-file"
        occupied_path.touch()
        self.app.workspace.set("save_folder", str(occupied_path))
        self.app.workspace.set("auto_save", True)
        self.qt.clipboard().setText("Existing clipboard content")
        desktop = self.image(800, 520, "#386ef2")
        editor = self.open_capture("All displays", "所有屏幕", desktop)
        QTest.mouseClick(editor.tool_buttons["redact"], Qt.MouseButton.LeftButton)
        QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=QPoint(45, 54))
        QTest.mouseMove(editor, QPoint(85, 90))
        QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=QPoint(85, 90))
        annotated_color = editor.export_image().pixelColor(60, 70)
        self.assertNotEqual(annotated_color, desktop.pixelColor(60, 70))

        warnings = []
        original_warning = QMessageBox.warning

        def acknowledge():
            warning = self.qt.activeModalWidget()
            warnings.append(warning.text())
            QTest.mouseClick(warning.button(QMessageBox.StandardButton.Ok), Qt.MouseButton.LeftButton)

        def warn(*args):
            QTimer.singleShot(0, acknowledge)
            return original_warning(*args)

        with patch("snaploom.app.QMessageBox.warning", side_effect=warn):
            self.click_capture_menu_item(editor, "Quick save", "快速保存")
        warning, = warnings
        self.assertIn(occupied_path.name, warning)
        self.assertIs(self.app.editor, editor)
        self.assertTrue(editor.isVisible())
        self.assertEqual(editor.export_image().pixelColor(60, 70), annotated_color)
        self.assertEqual(self.app.workspace.history(), [])
        self.assertEqual(list(self.folder.glob("*.png")), [])
        self.assertEqual(self.qt.clipboard().text(), "Existing clipboard content")

        self.app.workspace.set("save_folder", str(self.folder))
        self.click_capture_menu_item(editor, "Quick save", "快速保存")
        self.assertIsNone(self.app.editor)
        saved, = self.folder.glob("*.png")
        self.assertEqual(QImage(str(saved)).pixelColor(60, 70), annotated_color)
        self.assertEqual(len(self.app.workspace.history()), 1)

    def test_save_as_and_copy_file_can_cancel_and_retry_without_losing_capture(self):
        self.app.workspace.set("save_folder", str(self.folder))
        self.app.workspace.set("auto_save", True)
        self.app.workspace.set("copy_after_save", True)
        self.qt.clipboard().setText("Keep the old clipboard")
        desktop = self.image(800, 520, "#386ef2")
        editor = self.open_capture("Capture region", "区域截图", desktop)
        start, finish = QPoint(40, 45), QPoint(350, 245)
        QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(editor, finish)
        QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=finish)
        selection = editor.selected_geometry()
        QTest.mouseClick(editor.tool_buttons["redact"], Qt.MouseButton.LeftButton)
        QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=start + QPoint(25, 30))
        QTest.mouseMove(editor, start + QPoint(75, 80))
        QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=start + QPoint(75, 80))
        marked_point = QPoint(45, 50)
        marked_color = editor.export_image().pixelColor(marked_point)
        self.assertNotEqual(marked_color, desktop.pixelColor(start + marked_point))

        occupied = self.folder / "existing-file"
        occupied.touch()
        target = self.folder / "截图 示例.png"
        choices = [("", ""), (str(occupied / "image.png"), "PNG (*.png)"), (str(target), "PNG (*.png)")]
        with (
            patch.object(QFileDialog, "getSaveFileName", side_effect=choices) as choose,
            patch.object(QMessageBox, "warning") as warning,
        ):
            for expected_warnings in (0, 1):
                self.click_capture_menu_item(editor, "Save As and Copy File…", "另存为并复制文件…")
                self.assertEqual(warning.call_count, expected_warnings)
                self.assertIs(self.app.editor, editor)
                self.assertTrue(editor.isVisible())
                self.assertTrue(self.app.capturing)
                self.assertEqual(editor.selected_geometry(), selection)
                self.assertEqual(editor.export_image().pixelColor(marked_point), marked_color)
                self.assertEqual(self.app.workspace.history(), [])
                self.assertEqual(list(self.folder.glob("*.png")), [])
                self.assertEqual(self.qt.clipboard().text(), "Keep the old clipboard")

            self.click_capture_menu_item(editor, "Save As and Copy File…", "另存为并复制文件…")

        self.assertEqual(choose.call_count, 3)
        warning.assert_called_once()
        self.assertIn(occupied.name, warning.call_args.args[-1])
        self.assertIsNone(self.app.editor)
        self.assertFalse(self.app.capturing)
        self.assertTrue(self.app.dashboard.isVisible())
        self.assertEqual(list(self.folder.glob("*.png")), [target])
        saved = QImage(str(target))
        self.assertEqual(saved.size(), selection.size())
        self.assertEqual(saved.pixelColor(marked_point), marked_color)
        url, = self.qt.clipboard().mimeData().urls()
        self.assertTrue(url.isLocalFile())
        self.assertEqual(Path(url.toLocalFile()), target)
        self.assertIn(str(target), self.app.dashboard.status.text())
        entry, = self.app.workspace.history()
        self.assertEqual(entry[3], "copy-file")

    def test_cancelled_print_leaves_capture_open_for_another_output(self):
        desktop = self.image(800, 520, "#386ef2")
        editor = self.open_capture("All displays", "所有屏幕", desktop)
        with patch.object(self.app, "print_image", return_value=False) as print_image:
            self.click_capture_menu_item(editor, "Print", "打印")
        print_image.assert_called_once()
        self.assertIs(self.app.editor, editor)
        self.assertTrue(editor.isVisible())
        self.assertEqual(self.app.workspace.history(), [])
        self.click_button("Copy", "复制", parent=editor.toolbar, widget=QToolButton)
        self.assertIsNone(self.app.editor)
        self.assertEqual(self.qt.clipboard().image().pixelColor(5, 5), QColor("#386ef2"))

    def test_capture_output_shortcuts_deliver_to_each_destination(self):
        desktop = self.image(360, 260, "#386ef2")
        ctrl, shift = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier
        cases = (
            (Qt.Key.Key_C, ctrl, "copy"), (Qt.Key.Key_P, ctrl, "pin"), (Qt.Key.Key_S, ctrl, "saved"),
            (Qt.Key.Key_S, ctrl | shift, "quick-save"), (Qt.Key.Key_C, ctrl | shift, "copy-file"),
        )
        self.app.workspace.set("save_folder", str(self.folder))
        target = self.folder / "Selected via Save.png"
        file_target = self.folder / "Selected for copying.png"
        choices = [(str(path), "PNG (*.png)") for path in (target, file_target)]
        with patch.object(QFileDialog, "getSaveFileName", side_effect=choices) as save_dialog:
            for key, modifiers, action in cases:
                with self.subTest(action=action):
                    self.qt.clipboard().clear()
                    editor = self.open_capture("All displays", "所有屏幕", desktop)
                    editor.activateWindow()
                    editor.setFocus()
                    self.qt.processEvents()
                    QTest.keyClick(editor.windowHandle(), key, modifiers)
                    self.assertIsNone(self.app.editor)
                    self.assertFalse(self.app.capturing)
                    self.assertTrue(self.app.dashboard.isVisible())
                    self.assertEqual(self.app.workspace.history()[0][3], action)
                    if action == "copy":
                        result = self.qt.clipboard().image()
                    elif action == "pin":
                        pin_id, = self.app.pin_windows
                        pin = self.app.pin_windows[pin_id]
                        self.assertTrue(pin.isVisible())
                        result = QImage(pin.image)
                        self.app.pin_action("delete", pin_id)
                    elif action == "saved":
                        result = QImage(str(target))
                    elif action == "quick-save":
                        files = set(self.folder.glob("*.png")) - {target}
                        path, = files
                        result = QImage(str(path))
                    else:
                        url, = self.qt.clipboard().mimeData().urls()
                        self.assertTrue(url.isLocalFile())
                        self.assertEqual(Path(url.toLocalFile()), file_target)
                        result = QImage(url.toLocalFile())
                    self.assertEqual(result.size(), desktop.size())
                    self.assertEqual(result.pixelColor(5, 5), QColor("#386ef2"))
        self.assertEqual(save_dialog.call_count, 2)

    def test_print_menu_creates_a_pdf_through_the_qt_print_painter(self):
        desktop = self.image(360, 260, "#386ef2")
        editor = self.open_capture("All displays", "所有屏幕", desktop)
        output = self.folder / "printed capture.pdf"

        def choose_pdf(dialog):
            printer = dialog.printer()
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(output))
            return QDialog.DialogCode.Accepted

        with patch.object(QPrintDialog, "exec", choose_pdf):
            self.click_capture_menu_item(editor, "Print", "打印")
        self.assertIsNone(self.app.editor)
        self.assertTrue(output.read_bytes().startswith(b"%PDF-"))
        self.assertEqual(self.app.workspace.history()[0][3], "print")

    def test_custom_menu_command_launch_failure_keeps_capture_for_retry(self):
        self.app.workspace.set("save_folder", str(self.folder))
        self.app.workspace.set("custom_commands", {"Inspect exported image": 'viewer --input "{image}"'})
        desktop = self.image(360, 260, "#386ef2")
        editor = self.open_capture("All displays", "所有屏幕", desktop)
        with (
            patch("snaploom.app.subprocess.Popen", side_effect=[OSError("Viewer unavailable"), None]) as launch,
            patch.object(QMessageBox, "warning") as warning,
        ):
            self.click_capture_menu_item(editor, "Inspect exported image", "Inspect exported image")
            self.assertIs(self.app.editor, editor)
            self.assertTrue(editor.isVisible())
            self.assertEqual(editor.selected_geometry(), desktop.rect())
            self.assertEqual(self.app.workspace.history(), [])
            self.assertEqual(warning.call_args.args[-1], "Viewer unavailable")
            self.click_capture_menu_item(editor, "Inspect exported image", "Inspect exported image")

        self.assertEqual(launch.call_count, 2)
        args = launch.call_args.args[0]
        self.assertEqual(args[:2], ["viewer", "--input"])
        self.assertEqual(len(args), 3)
        self.assertFalse(launch.call_args.kwargs["shell"])
        self.assertEqual(QImage(args[2]).pixelColor(5, 5), QColor("#386ef2"))
        self.assertIsNone(self.app.editor)
        self.assertEqual(self.app.workspace.history()[0][3], "Inspect exported image")

    def test_cli_hold_explicitly_confirms_requested_output(self):
        desktop = self.image(800, 520, "#386ef2")
        target = self.folder / "held capture.png"
        region = QRect(45, 54, 180, 140)
        dimensions = [str(value) for value in region.getRect()]
        data = arguments(["snip", "--area", *dimensions, "--hold", "-o", str(target)])
        completed = []
        with (
            patch("snaploom.capture.capture_desktop", return_value=Snapshot(desktop, desktop.rect())),
            patch("snaploom.capture.detect_windows", return_value=[]),
            patch("snaploom.capture.detect_elements", return_value=[]),
        ):
            self.app.command(data, lambda **result: completed.append(result))
            self.wait_for_capture()
            QTest.keyClick(self.app.editor.windowHandle(), Qt.Key.Key_Escape)
            cancelled, = completed
            self.assertEqual(cancelled["code"], 1)
            self.assertFalse(target.exists())
            self.assertEqual(self.app.workspace.history(), [])
            completed.clear()
            self.app.command(data, lambda **result: completed.append(result))
            self.wait_for_capture()
        editor = self.app.editor
        labels = {button.text() for button in editor.toolbar.findChildren(QToolButton) if button.isVisible()}
        self.assertNotIn(self.app.tr("Copy", "复制"), labels)
        self.assertNotIn(self.app.tr("Save As…", "另存为…"), labels)
        with patch.object(self.app, "save_dialog") as save_dialog:
            self.click_button("Confirm requested output", "确认输出", parent=editor.toolbar, widget=QToolButton)
        save_dialog.assert_not_called()
        self.assertEqual(completed[0]["code"], 0)
        self.assertIsNone(self.app.editor)
        self.assertEqual(QImage(str(target)).size(), region.size())
        self.assertEqual(len(self.app.workspace.history()), 1)

    def test_cli_copy_file_writes_once_and_returns_path_without_a_dialog(self):
        desktop = self.image(360, 260, "#386ef2")
        self.app.workspace.set("save_folder", str(self.folder))
        self.app.workspace.set("auto_save", True)
        self.app.workspace.set("copy_after_save", True)
        results = []
        with (
            patch("snaploom.capture.capture_desktop", return_value=Snapshot(desktop, desktop.rect())),
            patch.object(QFileDialog, "getSaveFileName") as choose,
        ):
            data = arguments(["snip", "--full", "--block", "-o", "copy-file"])
            self.app.command(data, lambda **result: results.append(result))
            self.wait_until(lambda: bool(results))

        choose.assert_not_called()
        self.assertIsNone(self.app.editor)
        self.assertFalse(self.app.capturing)
        result, = results
        self.assertEqual(result["code"], 0)
        path, = self.folder.glob("*.png")
        self.assertEqual(Path(result["text"]), path)
        saved = QImage(str(path))
        self.assertEqual(saved.size(), desktop.size())
        self.assertEqual(saved.pixelColor(5, 5), QColor("#386ef2"))
        url, = self.qt.clipboard().mimeData().urls()
        self.assertTrue(url.isLocalFile())
        self.assertEqual(Path(url.toLocalFile()), path)
        entry, = self.app.workspace.history()
        self.assertEqual(entry[3], "copy-file")

    def test_cli_invalid_region_and_failed_write_return_errors_without_later_outputs(self):
        desktop = self.image(360, 260, "#386ef2")
        occupied = self.folder / "not a directory"
        occupied.touch()
        self.qt.clipboard().setText("Keep the old clipboard")
        cases = (
            (["snip", "--area", "500", "500", "20", "20", "-o", "clipboard"],
             self.app.tr("The requested region is outside the desktop.", "请求的区域不在桌面范围内。")),
            (["snip", "--full", "-o", f"clipboard;{occupied / 'image.png'}"], occupied.name),
        )
        with patch("snaploom.capture.capture_desktop", return_value=Snapshot(desktop, desktop.rect())):
            for argv, message in cases:
                with self.subTest(argv=argv):
                    results = []
                    self.app.command(arguments(argv), lambda **result: results.append(result))
                    self.wait_until(lambda: bool(results))
                    result, = results
                    self.assertEqual(result["code"], 1)
                    self.assertIn(message, result["error"])
                    self.assertIsNone(self.app.editor)
                    self.assertFalse(self.app.capturing)
                    self.assertTrue(self.app.dashboard.isVisible())
                    self.assertEqual(self.qt.clipboard().text(), "Keep the old clipboard")
                    self.assertEqual(self.app.workspace.history(), [])

    def test_color_picker_mouse_enter_and_escape_return_the_expected_result(self):
        desktop = self.image(360, 260, "#386ef2")
        point = QPoint(40, 50)
        desktop.setPixelColor(point, QColor("#19a085"))
        cases = (("mouse", "HEX", "#19A085"), ("enter", "RGB", "rgb(25, 160, 133)"), ("cancel", "HEX", ""))
        with (
            patch("snaploom.capture.capture_desktop", return_value=Snapshot(desktop, desktop.rect())),
            patch("snaploom.capture.detect_windows", return_value=[]),
        ):
            for mode, color_format, expected in cases:
                with self.subTest(mode=mode):
                    self.qt.clipboard().setText("Keep the old clipboard")
                    self.app.workspace.set("color_format", color_format)
                    results = []
                    self.app.command(arguments(["pick-color"]), lambda **result: results.append(result))
                    self.wait_for_capture()
                    editor = self.app.editor
                    self.assertTrue(editor.color_only)
                    self.assertFalse(editor.toolbar.isVisible())
                    handle = editor.windowHandle()
                    QTest.mouseMove(handle, point + QPoint(15, 10))
                    QTest.mouseMove(handle, point)
                    if mode == "mouse":
                        QTest.mouseClick(handle, Qt.MouseButton.LeftButton, pos=point)
                    else:
                        QTest.keyClick(handle, Qt.Key.Key_Return if mode == "enter" else Qt.Key.Key_Escape)

                    result, = results
                    if expected:
                        self.assertEqual(result, {"code": 0, "text": expected})
                        self.assertEqual(self.qt.clipboard().text(), expected)
                    else:
                        self.assertEqual(result["code"], 1)
                        self.assertIn("cancel", result["error"].lower())
                        self.assertEqual(self.qt.clipboard().text(), "Keep the old clipboard")
                    self.assertIsNone(self.app.editor)
                    self.assertTrue(self.app.dashboard.isVisible())
                    self.assertEqual(self.app.workspace.history(), [])

    def test_barcode_cli_without_clipboard_image_confirms_the_selected_region(self):
        desktop = self.image(360, 260, "#386ef2")
        start, finish = QPoint(40, 50), QPoint(200, 160)
        results = []
        self.qt.clipboard().setText("This is not an image")
        with (
            patch("snaploom.capture.capture_desktop", return_value=Snapshot(desktop, desktop.rect())),
            patch("snaploom.capture.detect_windows", return_value=[]),
            patch("snaploom.app.recognize", return_value="Recognized code") as recognize,
        ):
            self.app.command(arguments(["barcode-scan", "--block"]), lambda **result: results.append(result))
            self.wait_for_capture()
            editor = self.app.editor
            handle = editor.windowHandle()
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(handle, finish)
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=finish)
            self.assertFalse(results)
            self.assertEqual(self.qt.clipboard().text(), "This is not an image")
            self.click_button("Scan and copy code", "识别并复制条码", parent=editor.toolbar, widget=QToolButton)
            self.wait_until(lambda: bool(results))
        self.assertEqual(results, [{"code": 0, "text": "Recognized code"}])
        self.assertEqual(self.qt.clipboard().text(), "Recognized code")
        self.assertEqual(recognize.call_args.args[0].size(), QRect(start, finish).size())
        self.assertEqual(recognize.call_args.args[1], "barcode-copy")
        self.assertIsNone(self.app.editor)
        self.assertFalse(self.app.capturing)

    def test_size_dialog_rejects_a_second_capture_and_releases_on_cancel(self):
        original_exec = QDialog.exec
        observed = []

        def while_open(dialog):
            def cancel_after_hotkey():
                self.app.hotkeys.triggered.emit("capture")
                observed.append((self.app.capturing, self.app.dashboard.status.text()))
                dialog.reject()

            QTimer.singleShot(0, cancel_after_hotkey)
            return original_exec(dialog)

        with patch.object(QDialog, "exec", while_open):
            self.click_button("Size and delay…", "尺寸与延时…")

        self.assertEqual(observed, [(True, self.app.tr("Finish the current capture first.", "请先完成当前截图。"))])
        self.assertIsNone(self.app.editor)
        self.assertFalse(self.app.capturing)
        self.assertTrue(self.app.dashboard.isVisible())
        self.open_capture("All displays", "所有屏幕", self.image(800, 520, "#386ef2"))

    def test_refresh_keeps_explicit_cursor_and_disabled_window_suggestions(self):
        first = self.image(800, 520, "#386ef2")
        second = self.image(800, 520, "#23a097")
        self.app.workspace.set("detect_windows", False)
        frames = [Snapshot(first, first.rect()), CaptureError("Desktop capture temporarily unavailable"),
                  Snapshot(second, second.rect())]
        with (
            patch("snaploom.capture.capture_desktop", side_effect=frames) as capture,
            patch("snaploom.capture.detect_windows") as windows,
        ):
            self.app.command(arguments(["snip", "--full", "--cursor"]))
            self.wait_for_capture()
            editor = self.app.editor
            self.click_capture_menu_item(editor, "Refresh screenshot", "刷新截图")
            self.wait_until(lambda: capture.call_count == 2 and editor.isVisible())
            self.assertEqual(editor.export_image().pixelColor(20, 20), QColor("#386ef2"))
            self.assertEqual(self.app.dashboard.status.text(), "Desktop capture temporarily unavailable")
            self.click_capture_menu_item(editor, "Refresh screenshot", "刷新截图")
            self.wait_until(lambda: capture.call_count == 3 and editor.isVisible())
            windows.assert_not_called()

        self.assertEqual([call.kwargs for call in capture.call_args_list], [{"include_cursor": True}] * 3)
        self.assertEqual(editor.selected_geometry(), first.rect())
        self.assertEqual(editor.export_image().pixelColor(20, 20), QColor("#23a097"))

    def test_failed_automatic_save_keeps_copy_and_history_and_allows_manual_save(self):
        blocked_folder = self.folder / "existing-file"
        blocked_folder.touch()
        self.app.workspace.set("save_folder", str(blocked_folder))
        self.app.workspace.set("auto_save", True)
        desktop = self.image(800, 520, "#386ef2")
        editor = self.open_capture("All displays", "所有屏幕", desktop)
        self.click_button("Copy", "复制", parent=editor.toolbar, widget=QToolButton)
        self.assertIsNone(self.app.editor)
        self.assertEqual(self.qt.clipboard().image().pixelColor(5, 5), QColor("#386ef2"))
        self.assertEqual(len(self.app.workspace.history()), 1)
        self.assertIn(self.app.tr("Automatic save failed.", "自动保存失败。"), self.app.dashboard.status.text())

        editor = self.open_capture("All displays", "所有屏幕", desktop)
        target = self.folder / "manual.png"
        with patch.object(QFileDialog, "getSaveFileName", return_value=(str(target), "PNG (*.png)")) as choose:
            self.click_button("Save As…", "另存为…", parent=editor.toolbar, widget=QToolButton)
        self.assertEqual(Path(choose.call_args.args[2]).parent, self.folder)
        self.assertEqual(QImage(str(target)).pixelColor(5, 5), QColor("#386ef2"))
        self.assertIsNone(self.app.editor)

    def test_scrolling_save_cancel_and_failure_keep_stitched_frames_and_pins_hidden(self):
        import numpy as np

        rgb = np.random.default_rng(17).integers(0, 255, (215, 160, 3), dtype=np.uint8)
        document = QImage(rgb, 160, 215, 480, QImage.Format.Format_RGB888).copy()
        first = document.copy(0, 0, 160, 160)
        second = document.copy(0, 55, 160, 160)
        pin_id = self.app.open_pin(self.image(100, 70, "#da5d35"), QPoint(80, 30))
        pin = self.app.pin_windows[pin_id]
        self.app.workspace.set("hide_pins_on_capture", True)
        editor = self.open_capture("All displays", "所有屏幕", first)

        def capture_frame(**_options):
            self.assertFalse(pin.isVisible())
            self.assertFalse(dialog.isVisible())
            return Snapshot(second, second.rect())

        with patch("snaploom.capture.capture_desktop", side_effect=capture_frame):
            self.click_capture_menu_item(editor, "Scrolling capture", "滚动截图")
            dialog, = self.app.dialogs
            self.assertFalse(pin.isVisible())
            self.assertTrue(self.app.capturing)
            self.assertFalse(self.app.dashboard.isVisible())
            self.click_button("Add next frame", "添加下一帧", parent=dialog)
            self.wait_until(dialog.isVisible)

        invalid_parent = self.folder / "blocked"
        invalid_parent.touch()
        target = self.folder / "stitched.png"
        choices = [("", ""), (str(invalid_parent / "image.png"), "PNG (*.png)"),
                   (str(target), "PNG (*.png)")]
        with patch.object(QFileDialog, "getSaveFileName", side_effect=choices) as choose:
            for _ in range(2):
                self.click_button("Save As…", "另存为…", parent=dialog)
                self.assertTrue(dialog.isVisible())
                self.assertFalse(pin.isVisible())
                self.assertEqual(self.app.workspace.history(), [])
            self.click_button("Save As…", "另存为…", parent=dialog)

        self.assertTrue(all(call.args[0] is dialog for call in choose.call_args_list))
        stitched = QImage(str(target))
        self.assertEqual(stitched.size(), document.size())
        self.assertEqual(stitched.pixelColor(70, 195), document.pixelColor(70, 195))
        self.assertFalse(self.app.capturing)
        self.assertTrue(pin.isVisible())
        self.assertTrue(self.app.dashboard.isVisible())

    def test_scrolling_pin_uses_assembled_pixels_and_escape_releases_capture(self):
        import numpy as np

        rgb = np.random.default_rng(70).integers(0, 255, (220, 100, 3), dtype=np.uint8)
        document = QImage(rgb, 100, 220, 300, QImage.Format.Format_RGB888).copy()
        first, second = document.copy(0, 0, 100, 140), document.copy(0, 80, 100, 140)
        for pin_result in (True, False):
            with self.subTest(pin_result=pin_result):
                editor = self.open_capture("All displays", "所有屏幕", first)
                with patch("snaploom.capture.capture_desktop", return_value=Snapshot(second, second.rect())) as grab:
                    self.click_capture_menu_item(editor, "Scrolling capture", "滚动截图")
                    dialog, = self.app.dialogs
                    if pin_result:
                        self.click_button("Add next frame", "添加下一帧", parent=dialog)
                        self.wait_until(lambda: grab.called and dialog.isVisible())
                if pin_result:
                    self.click_button("Pin", "贴图", parent=dialog)
                    pin_id, = self.app.pin_windows
                    pin = self.app.pin_windows[pin_id]
                    self.assertTrue(pin.isVisible())
                    self.assertEqual(pin.image.size(), document.size())
                    self.assertEqual(pin.image.pixelColor(20, 190), document.pixelColor(20, 190))
                    self.app.pin_action("delete", pin_id)
                else:
                    QTest.keyClick(dialog.windowHandle(), Qt.Key.Key_Escape)
                    self.assertFalse(self.app.pin_windows)
                self.assertFalse(self.app.capturing)
                self.assertFalse(self.app.dialogs)
                self.assertTrue(self.app.dashboard.isVisible())
        self.assertEqual([record[3] for record in self.app.workspace.history()], ["pin"])

    def test_recognition_busy_no_result_and_success_keep_selection_available(self):
        desktop = self.image(800, 520, "#386ef2")
        editor = self.open_capture("All displays", "所有屏幕", desktop)
        self.app.recognition_jobs[0] = ("ocr", None, None)
        with patch.object(QMessageBox, "information") as notice:
            self.click_capture_menu_item(editor, "Extract text", "提取文字")
            notice.assert_called_once()
            self.assertIn(self.app.tr("already running", "正在识别"), notice.call_args.args[2])
        self.app.recognition_jobs.clear()
        self.assertTrue(editor.isVisible())

        with (
            patch("snaploom.app.recognize", side_effect=["", "<b>Literal text</b>"]),
            patch.object(QMessageBox, "information") as notice,
        ):
            self.click_capture_menu_item(editor, "Extract text", "提取文字")
            self.wait_until(lambda: notice.called)
            self.assertIn(self.app.tr("No text", "未识别到文字"), notice.call_args.args[2])
            self.assertTrue(editor.isVisible())
            self.assertEqual(self.app.workspace.history(), [])
            self.click_capture_menu_item(editor, "Extract text", "提取文字")
            self.wait_until(lambda: any(dialog.findChild(QTextEdit) for dialog in self.app.dialogs))

        result, = self.app.dialogs
        text = result.findChild(QTextEdit)
        self.assertEqual(text.toPlainText(), "<b>Literal text</b>")
        self.click_at(text.viewport())
        QTest.keyClick(text, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(text, "Corrected OCR result")
        self.click_button("Copy text", "复制文字", parent=result)
        self.assertEqual(self.qt.clipboard().text(), "Corrected OCR result")
        buttons = result.findChild(QDialogButtonBox)
        self.click_at(buttons.button(QDialogButtonBox.StandardButton.Close))
        self.assertTrue(editor.isVisible())
        self.assertEqual(editor.selected_geometry(), desktop.rect())
        self.click_button("Copy", "复制", parent=editor.toolbar, widget=QToolButton)
        self.assertEqual(self.qt.clipboard().image().pixelColor(5, 5), QColor("#386ef2"))
        self.assertEqual(len(self.app.workspace.history()), 1)

    def test_cancelling_recognition_does_not_open_a_late_result(self):
        desktop = self.image(800, 520, "#386ef2")
        editor = self.open_capture("All displays", "所有屏幕", desktop)
        pending = Event()
        release = Event()

        def recognize(_image, _action):
            pending.set()
            release.wait(3)
            return "A late result"

        with patch("snaploom.app.recognize", side_effect=recognize):
            try:
                self.click_capture_menu_item(editor, "Extract text", "提取文字")
                self.wait_until(pending.is_set)
                progress, = self.app.dialogs
                QTest.keyClick(progress, Qt.Key.Key_Escape)
                self.assertFalse(self.app.dialogs)
                self.assertTrue(editor.isVisible())
            finally:
                release.set()
            self.wait_until(lambda: not self.app.recognition_jobs)

        self.assertFalse(self.app.dialogs)
        self.assertTrue(editor.isVisible())
        self.assertEqual(self.app.workspace.history(), [])
