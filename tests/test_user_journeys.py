import tempfile
import unittest
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QLabel, QMessageBox, QPushButton, QSpinBox,
    QSystemTrayIcon, QTextEdit, QToolButton,
)

from snaploom.app import Application
from snaploom.capture import CaptureError, Snapshot
from snaploom.commands import arguments
from snaploom.ui import Preferences


class UserJourneyTests(unittest.TestCase):
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
        self.qt.clipboard().clear()
        self.addCleanup(self.qt.clipboard().clear)
        self.application = None
        self.addCleanup(self.stop_application)
        self.start_application()
        self.image = QImage(800, 520, QImage.Format.Format_RGB32)
        self.image.fill(QColor("#f0f4f8"))
        self.selection = QRect(45, 54, 180, 140)
        self.screen = self.enterContext(patch("snaploom.capture.capture_desktop", return_value=self.snapshot(self.image)))
        self.enterContext(patch("snaploom.capture.detect_windows", return_value=[]))
        self.enterContext(patch("snaploom.capture.detect_elements", return_value=[]))

    def start_application(self):
        self.application = Application(self.qt, self.folder, testing=True)
        self.application.workspace.set("save_folder", str(self.folder))
        self.application.show_dashboard()
        self.qt.processEvents()

    def stop_application(self):
        app = self.application
        if app is None:
            return
        if app.editor:
            app.editor.close()
        for dialog in tuple(app.dialogs):
            dialog.reject()
        for pin in app.pin_windows.values():
            pin.hide()
            pin.deleteLater()
        app.dashboard.hide()
        app.dashboard.deleteLater()
        app.shutdown()
        self.application = None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    @staticmethod
    def snapshot(image):
        return Snapshot(image, image.rect())

    def until(self, condition, description, timeout=2500):
        deadline = monotonic() + timeout / 1000
        while not condition() and monotonic() < deadline:
            self.qt.processEvents()
            sleep(0.005)
        self.assertTrue(condition(), description)

    def button(self, parent, en, zh, widget=QPushButton):
        label = self.application.tr(en, zh)
        return next(button for button in parent.findChildren(widget) if button.text() == label and button.isVisible())

    @staticmethod
    def click(button):
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)

    @staticmethod
    def drag(widget, start, end):
        QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(widget, end)
        QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=end)

    def open_capture(self, en="Capture region", zh="区域截图"):
        app = self.application
        self.click(self.button(app.dashboard, en, zh))
        self.until(lambda: app.editor is not None and app.editor.isVisible(), "Screenshot editor did not open.")
        return app.editor

    def select_region(self, editor, region=None):
        region = region or self.selection
        self.drag(editor, region.topLeft(), region.bottomRight())
        self.assertEqual(editor.selected_geometry(), region)

    def select_tab(self, index):
        tabs = self.application.dashboard.tabs
        QTest.mouseClick(tabs.tabBar(), Qt.MouseButton.LeftButton, pos=tabs.tabBar().tabRect(index).center())
        self.assertEqual(tabs.currentIndex(), index)

    def capture_menu_action(self, editor, en, zh):
        menu = next(button.menu() for button in editor.toolbar.findChildren(QToolButton) if button.menu() is not None)
        action = next(action for action in menu.actions() if action.text() == self.application.tr(en, zh))
        action.trigger()

    def test_editor_more_menu_closes_with_escape_without_cancelling_capture(self):
        editor = self.open_capture()
        self.select_region(editor)
        more = next(button for button in editor.toolbar.findChildren(QToolButton) if button.menu() is not None)
        menu = more.menu()
        escaped = []

        def close_menu():
            was_visible = menu.isVisible()
            QTest.keyClick(menu, Qt.Key.Key_Escape)
            escaped.append(was_visible and not menu.isVisible())
            if menu.isVisible():
                menu.hide()

        menu.aboutToShow.connect(lambda: QTimer.singleShot(0, close_menu))
        self.click(more)
        self.assertEqual(escaped, [True])
        self.assertIs(self.application.editor, editor)
        self.assertEqual(editor.selected_geometry(), self.selection)
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        self.assertIsNone(self.application.editor)

    def test_capture_annotation_copy_and_history_preview(self):
        app = self.application
        editor = self.open_capture()
        self.assertFalse(app.dashboard.isVisible())
        self.select_region(editor)
        self.click(editor.tool_buttons["redact"])
        start = self.selection.topLeft() + QPoint(12, 18)
        self.drag(editor, start, start + QPoint(36, 34))
        self.click(editor.undo_button)
        self.assertEqual(editor.export_image().pixelColor(20, 30), QColor("#f0f4f8"))
        self.click(editor.redo_button)
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.assertIsNone(app.editor)
        self.assertTrue(app.dashboard.isVisible())
        copied = self.qt.clipboard().image()
        self.assertEqual(copied.size(), self.selection.size())
        self.assertNotEqual(copied.pixelColor(20, 30), QColor("#f0f4f8"))
        self.select_tab(1)
        self.assertEqual(app.dashboard.history_list.count(), 1)
        self.click(self.button(app.dashboard, "Preview", "预览"))
        preview, = app.dialogs
        self.assertEqual(preview.canvas.export_image().pixelColor(20, 30), copied.pixelColor(20, 30))
        preview.reject()
        self.click(self.button(app.dashboard, "Delete", "删除"))
        self.assertEqual(app.workspace.history(), [])
        self.assertEqual(app.dashboard.history_list.count(), 0)

    def test_save_cancel_keeps_selection_and_second_attempt_writes_file(self):
        editor = self.open_capture()
        self.select_region(editor)
        filename = self.folder / "review"
        with patch("snaploom.app.QFileDialog.getSaveFileName", side_effect=[
            ("", "PNG (*.png)"),
            (str(filename), "PNG (*.png)"),
        ]):
            self.click(self.button(editor.toolbar, "Save", "保存", QToolButton))
            self.assertIs(self.application.editor, editor)
            self.assertTrue(editor.isVisible())
            self.assertEqual(editor.selected_geometry(), self.selection)
            self.assertEqual(self.application.workspace.history(), [])
            self.click(self.button(editor.toolbar, "Save", "保存", QToolButton))
        self.assertIsNone(self.application.editor)
        exported = QImage(str(filename.with_suffix(".png")))
        self.assertEqual(exported.size(), self.selection.size())
        self.assertEqual(exported.pixelColor(0, 0), QColor("#f0f4f8"))
        self.assertEqual(self.application.workspace.regions(), [self.selection])

    def test_quick_save_and_copy_as_file_create_readable_images(self):
        editor = self.open_capture()
        self.select_region(editor)
        self.capture_menu_action(editor, "Quick save", "快速保存")
        quick_save, = self.folder.glob("*.png")
        self.assertEqual(QImage(str(quick_save)).size(), self.selection.size())
        editor = self.open_capture()
        self.select_region(editor)
        self.capture_menu_action(editor, "Copy image as file", "复制为文件")
        link, = self.qt.clipboard().mimeData().urls()
        copied = Path(link.toLocalFile())
        self.assertTrue(copied.parent.samefile(self.folder))
        self.assertNotEqual(copied, quick_save)
        self.assertEqual(QImage(str(copied)).size(), self.selection.size())
        self.assertEqual(self.application.workspace.regions(), [self.selection, self.selection])

    def test_scripted_region_writes_a_file_and_copies_the_same_selection(self):
        app = self.application
        path = self.folder / "scripted result.png"
        area = [str(value) for value in self.selection.getRect()]
        response = []
        command = arguments(["snip", "--area", *area, "-o", f"{path};clipboard"])
        app.command(command, lambda **result: response.append(result))
        self.until(lambda: bool(response), "Scripted capture did not report completion.")
        self.assertEqual(response[0]["code"], 0)
        self.assertIsNone(app.editor)
        saved = QImage(str(path))
        copied = self.qt.clipboard().image()
        self.assertEqual(saved.size(), self.selection.size())
        self.assertEqual(saved.pixelColor(25, 30), copied.pixelColor(25, 30))
        self.assertEqual(app.workspace.regions(), [self.selection])

    def test_cancel_capture_restores_dashboard_and_previously_visible_pins(self):
        app = self.application
        self.qt.clipboard().setText("Keep this text")
        self.click(self.button(app.dashboard, "Paste or restore pin", "贴图或恢复"))
        pin, = app.pin_windows.values()
        app.workspace.set("hide_pins_on_capture", True)
        editor = self.open_capture()
        self.assertFalse(pin.isVisible())
        self.select_region(editor)
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        self.assertIsNone(app.editor)
        self.assertTrue(app.dashboard.isVisible())
        self.assertTrue(pin.isVisible())
        self.assertEqual(self.qt.clipboard().text(), "Keep this text")
        self.assertEqual(app.workspace.history(), [])

    def test_desktop_capture_failure_leaves_an_available_dashboard(self):
        app = self.application
        self.screen.side_effect = [CaptureError("Screen capture permission denied"), self.snapshot(self.image)]
        self.click(self.button(app.dashboard, "Capture region", "区域截图"))
        self.until(lambda: app.dashboard.isVisible() and not app.capturing, "Dashboard did not recover after failure.")
        self.assertEqual(app.dashboard.status.text(), "Screen capture permission denied")
        self.assertIsNone(app.editor)
        editor = self.open_capture()
        QTest.keyClick(editor, Qt.Key.Key_Escape)

    def test_tray_capture_supports_keyboard_selection_and_copy(self):
        app = self.application
        app.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
        self.until(lambda: app.editor is not None and app.editor.isVisible(), "Tray did not open the editor.")
        editor = app.editor
        start = QPoint(45, 54)
        QTest.mouseMove(editor, start)
        QTest.keyClick(editor, Qt.Key.Key_Space)
        QTest.keyClick(editor, Qt.Key.Key_Right, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(editor, Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(editor, Qt.Key.Key_Enter)
        self.assertEqual(editor.selected_geometry(), QRect(45, 54, 11, 11))
        QTest.keyClick(editor, Qt.Key.Key_Enter)
        self.assertIsNone(app.editor)
        self.assertEqual(self.qt.clipboard().image().size(), QRect(45, 54, 11, 11).size())

    def test_active_window_mode_opens_the_requested_window_rectangle(self):
        target = QRect(50, 90, 200, 130)
        with patch("snaploom.capture.active_window", return_value=target):
            editor = self.open_capture("Active window", "当前窗口")
        self.assertEqual(editor.selected_geometry(), target)
        self.click(self.button(editor.toolbar, "Pin", "贴图", QToolButton))
        pin, = self.application.pin_windows.values()
        self.assertTrue(pin.isVisible())
        self.assertEqual(pin.image.size(), target.size())
        self.assertEqual(self.application.workspace.regions(), [target])

    def test_custom_dimensions_and_transparent_whiteboard(self):
        original_exec = QDialog.exec
        requested = QRect(21, 42, 180, 150)
        callback_errors = []

        def enter_dimensions(dialog):
            def confirm():
                try:
                    fields = dialog.findChildren(QSpinBox)
                    self.assertEqual(len(fields), 4)
                    for field, value in zip(fields, requested.getRect(), strict=True):
                        field.setValue(value)
                    transparent = dialog.findChild(QCheckBox)
                    if transparent is not None:
                        QTest.keyClick(transparent, Qt.Key.Key_Space)
                        self.assertTrue(transparent.isChecked())
                    buttons = dialog.findChild(QDialogButtonBox)
                    self.click(buttons.button(QDialogButtonBox.StandardButton.Ok))
                    self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                except Exception as error:
                    callback_errors.append(error)
                    dialog.reject()

            QTimer.singleShot(0, confirm)
            return original_exec(dialog)

        with patch.object(QDialog, "exec", enter_dimensions):
            editor = self.open_capture("Size and delay…", "尺寸与延时…")
            if callback_errors:
                raise callback_errors[0]
            self.assertEqual(editor.selected_geometry(), requested)
            QTest.keyClick(editor, Qt.Key.Key_Escape)
            self.screen.reset_mock()
            editor = self.open_capture("Whiteboard…", "白板…")
            if callback_errors:
                raise callback_errors[0]
            self.screen.assert_not_called()
            QTest.keyClick(editor, Qt.Key.Key_Return)
            output = self.qt.clipboard().image()
            self.assertEqual(output.size(), requested.size())
            self.assertEqual(output.pixelColor(15, 15).alpha(), 0)

    def test_clipboard_pin_can_be_hidden_restored_and_reopened_after_restart(self):
        app = self.application
        self.qt.clipboard().setImage(self.image.copy(QRect(0, 0, 280, 90)))
        paste = self.button(app.dashboard, "Paste or restore pin", "贴图或恢复")
        button_point = paste.mapToGlobal(paste.rect().center())
        self.click(paste)
        pin_id, = app.pin_windows
        pin = app.pin_windows[pin_id]
        self.assertFalse(pin.frameGeometry().contains(button_point))
        QTest.keyClick(pin, Qt.Key.Key_Escape)
        self.assertFalse(pin.isVisible())
        self.click(paste)
        self.assertEqual(list(app.pin_windows), [pin_id])
        self.assertTrue(pin.isVisible())
        toggle = self.button(app.dashboard, "Show or hide pins", "显示或隐藏贴图")
        self.click(toggle)
        self.assertFalse(pin.isVisible())
        self.click(toggle)
        self.assertTrue(pin.isVisible())
        self.stop_application()
        self.start_application()
        restored = self.application.pin_windows[pin_id]
        self.assertTrue(restored.isVisible())
        self.assertEqual(restored.image.pixelColor(12, 12), QColor("#f0f4f8"))
        self.select_tab(2)
        self.assertEqual(self.application.dashboard.pin_list.count(), 1)
        self.click(self.button(self.application.dashboard, "Delete", "删除"))
        self.assertEqual(list(self.application.workspace.pins()), [])

    def test_paste_again_after_a_hidden_pin_is_shown_or_deleted_from_the_list(self):
        for en, zh in (("Show", "显示"), ("Delete", "删除")):
            with self.subTest(action=en):
                app = self.application
                self.qt.clipboard().setText(f"Reference for {en}")
                paste = self.button(app.dashboard, "Paste or restore pin", "贴图或恢复")
                previous = set(app.pin_windows)
                self.click(paste)
                pin_id, = app.pin_windows.keys() - previous
                pin = app.pin_windows[pin_id]
                QTest.keyClick(pin, Qt.Key.Key_Escape)
                self.assertFalse(pin.isVisible())
                self.select_tab(2)
                pin_list = app.dashboard.pin_list
                row = next(i for i in range(pin_list.count()) if pin_list.item(i).data(Qt.ItemDataRole.UserRole) == pin_id)
                point = pin_list.visualItemRect(pin_list.item(row)).center()
                QTest.mouseClick(pin_list.viewport(), Qt.MouseButton.LeftButton, pos=point)
                self.click(self.button(app.dashboard, en, zh))
                count = len(app.pin_windows)
                self.select_tab(0)
                self.click(paste)
                self.assertEqual(len(app.pin_windows), count + 1)

    def test_dropping_a_local_image_on_the_dashboard_creates_a_pin(self):
        app = self.application
        source = self.image.copy(QRect(0, 0, 85, 72))
        path = self.folder / "dropped.png"
        self.assertTrue(source.save(str(path)))
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        point = QPoint(180, 100)
        args = (Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        enter = QDragEnterEvent(point, *args)
        QApplication.sendEvent(app.dashboard, enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(QPointF(point), *args)
        QApplication.sendEvent(app.dashboard, drop)
        self.assertTrue(drop.isAccepted())
        pin, = app.pin_windows.values()
        self.assertTrue(pin.isVisible())
        self.assertEqual(pin.image.size(), source.size())
        self.assertEqual(pin.image.pixelColor(10, 10), source.pixelColor(10, 10))

    def test_repeat_region_hotkey_and_clear_history_confirmation(self):
        app = self.application
        editor = self.open_capture()
        self.select_region(editor)
        QTest.keyClick(editor, Qt.Key.Key_Return)
        app.hotkeys.triggered.emit("repeat")
        self.until(lambda: app.editor is not None and app.editor.isVisible(), "Repeat shortcut did not open the editor.")
        self.assertEqual(app.editor.selected_geometry(), self.selection)
        QTest.keyClick(app.editor, Qt.Key.Key_Return)
        self.select_tab(1)
        clear = self.button(app.dashboard, "Clear history…", "清空历史…")
        with patch("snaploom.app.QMessageBox.question", side_effect=[
            QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        ]):
            self.click(clear)
            self.assertEqual(app.dashboard.history_list.count(), 2)
            self.click(clear)
        self.assertEqual(app.workspace.history(), [])
        self.assertEqual(app.dashboard.history_list.count(), 0)

    def test_preferences_save_applies_shortcut_and_disables_history(self):
        app = self.application
        editor = self.open_capture()
        self.select_region(editor)
        QTest.keyClick(editor, Qt.Key.Key_Return)
        original_exec = Preferences.exec
        callback_errors = []

        def edit_preferences(dialog):
            def save():
                try:
                    dialog.fields["history_limit"].setValue(0)
                    dialog.shortcuts["capture"].setText("Ctrl+Alt+S")
                    buttons = dialog.findChild(QDialogButtonBox)
                    self.click(buttons.button(QDialogButtonBox.StandardButton.Save))
                    self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                except Exception as error:
                    callback_errors.append(error)
                    dialog.reject()

            QTimer.singleShot(0, save)
            return original_exec(dialog)

        with (
            patch.object(Preferences, "exec", edit_preferences),
            patch.object(app.hotkeys, "configure", return_value=[]) as register,
            patch("snaploom.ui.QMessageBox.warning") as warning,
        ):
            self.click(self.button(app.dashboard, "Settings", "设置"))
        if callback_errors:
            raise callback_errors[0]
        warning.assert_not_called()
        self.assertEqual(app.workspace.get("hotkeys")["capture"], "Ctrl+Alt+S")
        self.assertEqual(register.call_args.args[0]["capture"], "Ctrl+Alt+S")
        self.assertIn("Ctrl+Alt+S", app.dashboard.shortcuts.text())
        self.assertEqual(app.workspace.history(), [])
        editor = self.open_capture()
        self.select_region(editor)
        QTest.keyClick(editor, Qt.Key.Key_Return)
        self.assertEqual(app.workspace.history(), [])
        self.stop_application()
        self.start_application()
        self.assertEqual(self.application.workspace.get("history_limit"), 0)
        self.assertEqual(self.application.workspace.get("hotkeys")["capture"], "Ctrl+Alt+S")

    def test_barcode_menu_decodes_a_qr_image_and_copies_the_result(self):
        import zxingcpp

        app = self.application
        code = zxingcpp.create_barcode("Snaploom check", zxingcpp.BarcodeFormat.QRCode)
        pixels = zxingcpp.write_barcode_to_image(code, scale=5)
        side = pixels.shape[0]
        qr = QImage(memoryview(pixels), side, side, side, QImage.Format.Format_Grayscale8).copy()
        self.screen.return_value = self.snapshot(qr)
        editor = self.open_capture("All displays", "所有屏幕")
        self.capture_menu_action(editor, "Scan barcode or QR code", "识别条形码或二维码")
        self.until(lambda: bool(app.dialogs), "The QR recognition result did not open.", timeout=5000)
        result, = app.dialogs
        self.assertEqual(result.findChild(QTextEdit).toPlainText(), "Snaploom check")
        self.click(self.button(result, "Copy text", "复制文字"))
        self.assertEqual(self.qt.clipboard().text(), "Snaploom check")
        result.reject()

    def test_scrolling_capture_recovers_after_a_frame_has_no_overlap(self):
        import numpy as np

        rgb = np.random.default_rng(47).integers(0, 255, (260, 160, 3), dtype=np.uint8)
        document = QImage(rgb, 160, 260, 480, QImage.Format.Format_RGB888).copy()
        first = document.copy(0, 0, 160, 160)
        second = document.copy(0, 55, 160, 160)
        self.screen.side_effect = [self.snapshot(first), self.snapshot(first), self.snapshot(second)]
        editor = self.open_capture("All displays", "所有屏幕")
        self.capture_menu_action(editor, "Scrolling capture", "滚动截图")
        dialog, = self.application.dialogs
        self.click(self.button(dialog, "Add next frame", "添加下一帧"))
        self.until(dialog.isVisible, "Scrolling capture dialog did not return after the rejected frame.")
        self.assertTrue(any(
            self.application.tr("Overlap not found", "未找到重叠区域") in label.text()
            for label in dialog.findChildren(QLabel)
        ))
        self.click(self.button(dialog, "Add next frame", "添加下一帧"))
        self.until(dialog.isVisible, "Scrolling capture dialog did not return after stitching.")
        self.click(self.button(dialog, "Copy", "复制"))
        copied = self.qt.clipboard().image()
        self.assertEqual((copied.width(), copied.height()), (160, 215))
        self.assertEqual(copied.pixelColor(70, 195), document.pixelColor(70, 195))
        self.assertTrue(self.application.dashboard.isVisible())


if __name__ == "__main__":
    unittest.main()
