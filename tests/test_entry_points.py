import tempfile
import unittest
from pathlib import Path
from threading import Event
from time import monotonic
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QPushButton, QTextEdit, QToolButton

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
        self.folder = Path(directory.name)
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
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)

    def click_tab(self, index):
        tabs = self.app.dashboard.tabs
        QTest.mouseClick(tabs.tabBar(), Qt.MouseButton.LeftButton, pos=tabs.tabBar().tabRect(index).center())
        self.assertEqual(tabs.currentIndex(), index)

    def click_pin_row(self, pin_id):
        pins = self.app.dashboard.pin_list
        item = next(pins.item(row) for row in range(pins.count())
                    if pins.item(row).data(Qt.ItemDataRole.UserRole) == pin_id)
        QTest.mouseClick(pins.viewport(), Qt.MouseButton.LeftButton, pos=pins.visualItemRect(item).center())
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
        choose = lambda: QTimer.singleShot(
            0, lambda: QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
        )
        menu.aboutToShow.connect(choose)
        try:
            QTest.mouseClick(more, Qt.MouseButton.LeftButton)
        finally:
            menu.aboutToShow.disconnect(choose)

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
            self.click_button("Save", "保存")
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
            self.assertIs(self.app.editor, editor)
            self.click_capture_menu_item(editor, "Copy image as file", "复制为文件")
        self.assertEqual(len(warnings), 2)
        self.assertTrue(all(occupied_path.name in message for message in warnings))
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
        editor = self.app.editor
        labels = {button.text() for button in editor.toolbar.findChildren(QToolButton) if button.isVisible()}
        self.assertNotIn(self.app.tr("Copy", "复制"), labels)
        self.assertNotIn(self.app.tr("Save", "保存"), labels)
        with patch.object(self.app, "save_dialog") as save_dialog:
            self.click_button("Confirm requested output", "确认输出", parent=editor.toolbar, widget=QToolButton)
        save_dialog.assert_not_called()
        self.assertEqual(completed[0]["code"], 0)
        self.assertIsNone(self.app.editor)
        self.assertEqual(QImage(str(target)).size(), region.size())
        self.assertEqual(len(self.app.workspace.history()), 1)

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
            QTest.keyClick(editor, Qt.Key.Key_F5)
            self.assertFalse(editor.isVisible())
            self.wait_until(editor.isVisible)
            self.assertEqual(editor.export_image().pixelColor(20, 20), QColor("#386ef2"))
            self.assertEqual(self.app.dashboard.status.text(), "Desktop capture temporarily unavailable")
            QTest.keyClick(editor, Qt.Key.Key_F5)
            self.assertFalse(editor.isVisible())
            self.wait_until(editor.isVisible)
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
            self.click_button("Save", "保存", parent=editor.toolbar, widget=QToolButton)
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
                self.click_button("Save…", "保存…", parent=dialog)
                self.assertTrue(dialog.isVisible())
                self.assertFalse(pin.isVisible())
                self.assertEqual(self.app.workspace.history(), [])
            self.click_button("Save…", "保存…", parent=dialog)

        self.assertTrue(all(call.args[0] is dialog for call in choose.call_args_list))
        stitched = QImage(str(target))
        self.assertEqual(stitched.size(), document.size())
        self.assertEqual(stitched.pixelColor(70, 195), document.pixelColor(70, 195))
        self.assertFalse(self.app.capturing)
        self.assertTrue(pin.isVisible())
        self.assertTrue(self.app.dashboard.isVisible())

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
        self.assertEqual(result.findChild(QTextEdit).toPlainText(), "<b>Literal text</b>")
        QTest.keyClick(result, Qt.Key.Key_Escape)
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
