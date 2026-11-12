import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QContextMenuEvent, QEnterEvent, QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QListWidget, QMenu, QMessageBox, QPushButton, QToolButton

from snaploom.app import Application


class PinInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.start_application()
        self.addCleanup(self.close_application)

    def start_application(self):
        self.application = Application(self.qt, self.directory, testing=True)
        self.application.dashboard.show()
        self.qt.processEvents()

    def close_application(self):
        for window in self.application.pin_windows.values():
            window.hide()
            window.deleteLater()
        self.application.dashboard.hide()
        self.application.shutdown()
        self.application.dashboard.deleteLater()
        self.application.tray.deleteLater()
        self.application.tray_menu.deleteLater()
        self.qt.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.qt.processEvents()

    def open_pin(self, position, *, width=240, state=None):
        image = QImage(width, 105, QImage.Format.Format_ARGB32)
        image.fill(QColor("#2868bd"))
        pin_id = self.application.open_pin(image, position, state=state)
        self.qt.processEvents()
        return pin_id, self.application.pin_windows[pin_id]

    def show_menu(self, pin):
        point = QPoint(10, 10)
        QTest.mouseClick(pin, Qt.MouseButton.RightButton, pos=point)
        self.assertIs(self.qt.activePopupWidget(), pin.menu)
        self.assertIsNotNone(pin.menu)

    def choose_menu_item(self, pin, en, zh):
        label = self.application.tr(en, zh)
        action = next(action for action in pin.menu.actions() if action.text() == label)
        QTest.mouseClick(pin.menu, Qt.MouseButton.LeftButton, pos=pin.menu.actionGeometry(action).center())

    def choose_tray_item(self, en, zh):
        label = self.application.tr(en, zh)
        action = next(action for action in self.application.tray_menu.actions() if action.text() == label)
        action.trigger()

    def button(self, parent, en, zh):
        return next(button for button in parent.findChildren(QPushButton) if button.text() == self.application.tr(en, zh))

    def hover(self, widget, position):
        event = QEnterEvent(QPointF(position), QPointF(widget.mapTo(widget.window(), position)),
                            QPointF(widget.mapToGlobal(position)))
        self.qt.sendEvent(widget, event)

    def test_hover_does_not_paste_a_link_and_explicit_paste_can_be_hidden_and_restored(self):
        link = "https://example.invalid/capture-help"
        mime = QMimeData()
        mime.setText(link)
        clipboard = SimpleNamespace(mimeData=lambda: mime)
        app = self.application
        button = app.dashboard.findChild(QPushButton, "Secondary")
        restore_text = app.tr("Restore last hidden pin", "恢复最近隐藏的贴图")
        restore = next(button for button in app.dashboard.findChildren(QPushButton) if button.text() == restore_text)
        with (
            patch.object(self.application, "fetch_pin") as fetch,
            patch.object(self.qt, "clipboard", return_value=clipboard) as read_clipboard,
        ):
            for control in (button, restore):
                point = control.rect().center()
                QTest.mouseMove(control, point)
                self.hover(control, point)
                QTest.qWait(750)
                self.assertEqual(app.pin_windows, {})
                self.assertIsNone(self.qt.activePopupWidget())
                read_clipboard.assert_not_called()
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            read_clipboard.assert_called_once()
            fetch.assert_not_called()

        self.assertEqual(len(self.application.pin_windows), 1)
        pin_id, pin = next(iter(self.application.pin_windows.items()))
        self.assertTrue(pin.isVisible())
        self.assertEqual(pin.state["source_text"], link)
        self.assertFalse(pin.view.isNull())
        self.hover(pin, QPoint(12, 12))
        dismiss = pin.findChild(QToolButton, "PinDismiss")
        self.assertTrue(dismiss.isVisible())
        QTest.mouseClick(dismiss, Qt.MouseButton.LeftButton)
        self.assertFalse(pin.isVisible())
        self.assertFalse(pin.state["visible"])

        with patch.object(self.qt, "clipboard", return_value=clipboard) as read_clipboard:
            QTest.mouseClick(restore, Qt.MouseButton.LeftButton)
            read_clipboard.assert_not_called()
        self.assertEqual(list(self.application.pin_windows), [pin_id])
        self.assertTrue(pin.isVisible())

    def test_escape_and_native_close_dismiss_the_pin_menu(self):
        pin_id, pin = self.open_pin(QPoint(100, 110))
        for _ in range(2):
            self.show_menu(pin)
            QTest.keyClick(pin.menu, Qt.Key.Key_Escape)
            self.assertIsNone(self.qt.activePopupWidget())
            self.assertTrue(pin.isVisible())
            self.qt.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertFalse(pin.findChildren(QMenu))
        self.show_menu(pin)
        self.assertTrue(pin.close())
        self.assertFalse(pin.isVisible())
        self.assertFalse(self.application.pin_windows[pin_id].state["visible"])
        self.assertIsNone(self.qt.activePopupWidget())

    def test_close_button_remains_on_screen_when_pin_overflows(self):
        screen = self.qt.primaryScreen().availableGeometry()
        position = QPoint(screen.right() - 119, screen.top() + 35)
        _, pin = self.open_pin(position, width=300)
        self.hover(pin, QPoint(12, 12))
        dismiss = pin.findChild(QToolButton, "PinDismiss")
        self.assertTrue(dismiss.isVisible())
        control = QRect(dismiss.mapToGlobal(QPoint()), dismiss.size())
        self.assertTrue(screen.contains(control))
        QTest.mouseClick(dismiss, Qt.MouseButton.LeftButton)
        self.assertFalse(pin.isVisible())

    def test_click_through_pin_can_be_hidden_from_the_dashboard(self):
        _, pin = self.open_pin(QPoint(90, 100), state={"click_through": True})
        dashboard = self.application.dashboard
        dashboard.tabs.setCurrentIndex(2)
        hide_text = self.application.tr("Hide", "隐藏")
        hide = next(button for button in dashboard.tabs.currentWidget().findChildren(QPushButton)
                    if button.text() == hide_text)
        self.assertTrue(pin.isVisible())
        QTest.mouseClick(hide, Qt.MouseButton.LeftButton)
        self.assertFalse(pin.isVisible())
        self.assertFalse(pin.state["visible"])

    def test_restored_pin_does_not_resume_a_drag_interrupted_by_hiding(self):
        pin_id, pin = self.open_pin(QPoint(90, 100))
        original = pin.pos()
        QTest.mousePress(pin, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
        self.assertIsNotNone(pin.drag_position)
        self.application.pin_action("hide", pin_id)
        QTest.mouseRelease(self.application.dashboard, Qt.MouseButton.LeftButton)
        self.application.pin_action("show", pin_id)
        QTest.mouseMove(pin, QPoint(65, 50))
        self.assertEqual(pin.pos(), original)

    def test_offscreen_recovery_preserves_group_and_individual_hide_state(self):
        app = self.application
        hidden_id, hidden = self.open_pin(QPoint(90, 100))
        _, eligible = self.open_pin(QPoint(160, 100))
        app.hotkeys.triggered.emit("toggle_pins")
        self.assertFalse(hidden.isVisible())
        self.assertFalse(eligible.isVisible())

        app.dashboard.tabs.setCurrentIndex(2)
        rows = app.dashboard.pin_list
        item = next(rows.item(row) for row in range(rows.count())
                    if rows.item(row).data(Qt.ItemDataRole.UserRole) == hidden_id)
        QTest.mouseClick(rows.viewport(), Qt.MouseButton.LeftButton, pos=rows.visualItemRect(item).center())
        QTest.mouseClick(self.button(app.dashboard.tabs.currentWidget(), "Hide", "隐藏"), Qt.MouseButton.LeftButton)
        self.assertFalse(hidden.state["visible"])

        bounds = self.qt.primaryScreen().availableGeometry()
        eligible.move(bounds.bottomRight() + QPoint(200, 200))
        self.choose_tray_item("Recover off-screen pins", "找回屏幕外贴图")
        self.assertTrue(bounds.contains(eligible.pos()))
        self.assertFalse(eligible.isVisible())
        app.hotkeys.triggered.emit("toggle_pins")
        self.assertTrue(eligible.isVisible())
        self.assertFalse(hidden.isVisible())

    def test_disabling_startup_show_keeps_the_last_visibility_state(self):
        app = self.application
        eligible_id, _ = self.open_pin(QPoint(90, 100))
        hidden_id, hidden = self.open_pin(QPoint(160, 100))
        QTest.keyClick(hidden, Qt.Key.Key_Escape)
        app.workspace.set("restore_pins", False)
        self.close_application()
        self.start_application()

        app = self.application
        eligible = app.pin_windows[eligible_id]
        hidden = app.pin_windows[hidden_id]
        self.assertTrue(eligible.state["visible"])
        self.assertFalse(eligible.isVisible())
        app.hotkeys.triggered.emit("toggle_pins")
        self.assertTrue(eligible.isVisible())
        self.assertFalse(hidden.isVisible())

    def test_tray_click_through_rescue_is_independent_of_group(self):
        _, current = self.open_pin(QPoint(90, 100), state={"click_through": True})
        _, other = self.open_pin(QPoint(90, 100), state={"click_through": True, "group": "Review"})
        self.choose_tray_item("Disable click-through for all pins", "关闭所有贴图的鼠标穿透")
        self.assertTrue(current.isVisible())
        self.assertFalse(other.isVisible())
        for pin in (current, other):
            self.assertFalse(pin.state["click_through"])
            self.assertFalse(pin.windowFlags() & Qt.WindowType.WindowTransparentForInput)

    def test_shift_escape_hides_and_explicit_delete_can_be_cancelled(self):
        pin_id, pin = self.open_pin(QPoint(90, 100))
        QTest.keyClick(pin, Qt.Key.Key_Escape, Qt.KeyboardModifier.ShiftModifier)
        self.assertFalse(pin.isVisible())
        self.assertEqual([row[0] for row in self.application.workspace.pins()], [pin_id])
        self.choose_tray_item("Restore last hidden pin", "恢复最近隐藏的贴图")
        self.assertTrue(pin.isVisible())

        def answer(choice):
            dialog = self.qt.activeModalWidget()
            QTest.mouseClick(dialog.button(choice), Qt.MouseButton.LeftButton)

        for choice in (QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes):
            self.show_menu(pin)
            QTimer.singleShot(0, lambda choice=choice: answer(choice))
            self.choose_menu_item(pin, "Delete pin", "删除贴图")
            self.assertEqual(pin_id in self.application.pin_windows, choice == QMessageBox.StandardButton.No)
        self.assertEqual(list(self.application.workspace.pins()), [])

    def test_right_drag_selects_thumbnail_and_right_click_opens_menu(self):
        _, pin = self.open_pin(QPoint(100, 110))
        start, end = QPoint(16, 16), QPoint(86, 60)
        QTest.mousePress(pin, Qt.MouseButton.RightButton, pos=start)
        context_event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, start, pin.mapToGlobal(start))
        self.qt.sendEvent(pin, context_event)
        self.assertIsNone(pin.menu)
        QTest.mouseMove(pin, end)
        QTest.mouseRelease(pin, Qt.MouseButton.RightButton, pos=end)
        self.assertTrue(pin.state["thumbnail"])
        self.assertEqual(pin.display_image().size(), QRect(start, end).size())
        self.assertIsNone(pin.menu)
        self.show_menu(pin)
        self.assertIn(self.application.tr("Copy full image", "复制完整图像"),
                      [action.text() for action in pin.menu.actions()])

    def test_horizontal_wheel_is_inert_and_vertical_touchpad_controls_opacity(self):
        _, pin = self.open_pin(QPoint(90, 100), state={"opacity": 80})

        def send_wheel(pixels, angle, modifiers=Qt.KeyboardModifier.NoModifier):
            local = QPoint(30, 30)
            event = QWheelEvent(QPointF(local), QPointF(pin.mapToGlobal(local)), pixels, angle,
                                Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.ScrollUpdate, False)
            self.qt.sendEvent(pin, event)

        send_wheel(QPoint(), QPoint(120, 0))
        self.assertEqual(pin.state["zoom"], 1.0)
        send_wheel(QPoint(0, 14), QPoint(), Qt.KeyboardModifier.MetaModifier)
        self.assertEqual(pin.state["opacity"], 85)
        self.assertEqual(pin.state["zoom"], 1.0)
        send_wheel(QPoint(), QPoint(0, -120), Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(pin.state["opacity"], 80)

    def test_annotating_keeps_appearance_and_cropping_uses_display_orientation(self):
        app = self.application
        _, pin = self.open_pin(QPoint(90, 100), state={"rotation": 90, "horizontal": True, "opacity": 72})
        original = pin.view.copy()
        self.show_menu(pin)
        self.choose_menu_item(pin, "Annotate source image…", "标注原图…")
        dialog, = app.dialogs
        QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
        self.assertEqual(pin.view, original)
        self.assertEqual(pin.state["rotation"], 90)
        self.assertTrue(pin.state["horizontal"])

        self.show_menu(pin)
        self.choose_menu_item(pin, "Crop…", "裁剪…")
        dialog, = app.dialogs
        self.assertEqual(dialog.canvas.snapshot.image.size(), original.size())
        QTest.mouseClick(dialog.apply_button, Qt.MouseButton.LeftButton)
        self.assertEqual(pin.image.size(), original.size())
        self.assertEqual(pin.state["rotation"], 0)
        self.assertFalse(pin.state["horizontal"])
        self.assertEqual(pin.state["opacity"], 72)

    def test_group_navigation_does_not_reopen_a_renamed_or_removed_group(self):
        app = self.application
        app.create_group("Review")
        app.dashboard.tabs.setCurrentIndex(2)
        QTest.mouseClick(self.button(app.dashboard.tabs.currentWidget(), "Manage groups…", "管理分组…"),
                         Qt.MouseButton.LeftButton)
        dialog, = app.dialogs
        groups = dialog.findChild(QListWidget)
        rename = self.button(dialog, "Rename…", "重命名…")
        remove = self.button(dialog, "Remove…", "移除…")
        self.assertEqual(groups.currentItem().text(), "Review")
        with patch.object(QInputDialog, "getText", return_value=("New Review", True)):
            QTest.mouseClick(rename, Qt.MouseButton.LeftButton)
        self.assertEqual(app.group, "New Review")
        self.assertEqual(app.previous_group, "Default")
        self.assertNotIn("Review", app.groups())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            QTest.mouseClick(remove, Qt.MouseButton.LeftButton)
        app.command({"command": "switch-group-prev"})
        self.assertEqual(app.group, "Default")
        self.assertNotIn("New Review", app.groups())
        self.assertFalse(rename.isEnabled())
        self.assertFalse(remove.isEnabled())
        dialog.reject()


if __name__ == "__main__":
    unittest.main()
