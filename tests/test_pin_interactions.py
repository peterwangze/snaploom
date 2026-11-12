import tempfile
import unittest
from base64 import b64decode
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QContextMenuEvent, QEnterEvent, QImage, QMouseEvent, QMovie, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QDialogButtonBox, QFileDialog, QInputDialog, QListWidget,
    QMenu, QMessageBox, QPushButton, QTextEdit, QToolButton,
)

from snaploom.app import Application


class PinInteractionFixture(unittest.TestCase):
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
        for dialog in list(self.application.dialogs):
            dialog.reject()
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

    def click(self, widget, position=None, button=Qt.MouseButton.LeftButton):
        window = widget.window()
        point = widget.rect().center() if position is None else position
        QTest.mouseClick(window.windowHandle(), button, pos=window.mapFromGlobal(widget.mapToGlobal(point)))
        self.qt.processEvents()

    def show_menu(self, pin):
        self.click(pin, QPoint(10, 10), Qt.MouseButton.RightButton)
        self.assertIs(self.qt.activePopupWidget(), pin.menu)
        self.assertIsNotNone(pin.menu)

    def click_menu_item(self, menu, en, zh):
        label = self.application.tr(en, zh)
        action = next(action for action in menu.actions() if action.text() == label)
        self.assertTrue(action.isEnabled())
        self.click(menu, menu.actionGeometry(action).center())

    def choose_menu_item(self, pin, en, zh):
        self.click_menu_item(pin.menu, en, zh)

    def choose_appearance(self, pin, en, zh):
        self.show_menu(pin)
        menu = self.open_submenu(pin.menu, "Appearance", "外观")
        self.click_menu_item(menu, en, zh)

    def open_submenu(self, parent, en, zh):
        label = self.application.tr(en, zh)
        action = next(action for action in parent.actions() if action.text() == label)
        menu = action.menu()
        QTest.mouseMove(parent.windowHandle(), parent.actionGeometry(action).center())
        self.assertIs(parent.activeAction(), action)
        QTest.keyClick(parent.windowHandle(), Qt.Key.Key_Right)
        self.assertTrue(menu.isVisible())
        return menu

    def choose_tray_item(self, en, zh):
        self.click_menu_item(self.show_tray_menu(), en, zh)

    def show_tray_menu(self):
        tray = self.application.tray_menu
        tray.popup(self.qt.primaryScreen().availableGeometry().topLeft() + QPoint(30, 30))
        self.qt.processEvents()
        return tray

    def button(self, parent, en, zh):
        return next(button for button in parent.findChildren(QPushButton) if button.text() == self.application.tr(en, zh))

    def hover(self, widget, position):
        event = QEnterEvent(QPointF(position), QPointF(widget.mapTo(widget.window(), position)),
                            QPointF(widget.mapToGlobal(position)))
        self.qt.sendEvent(widget, event)

    def drag(self, pin, start, end, button=Qt.MouseButton.LeftButton,
             modifiers=Qt.KeyboardModifier.NoModifier):
        window = pin.windowHandle()
        destination = pin.mapToGlobal(end)
        QTest.mousePress(window, button, modifiers, pos=start)
        if modifiers:
            event = QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(destination),
                                Qt.MouseButton.NoButton, button, modifiers)
            self.qt.sendEvent(pin, event)
        else:
            QTest.mouseMove(window, end)
        QTest.mouseRelease(window, button, modifiers, pos=pin.mapFromGlobal(destination))
        self.qt.processEvents()

    def wheel(self, pin, angle, modifiers=Qt.KeyboardModifier.NoModifier, *, pixels=QPoint()):
        local = QPoint(30, 30)
        event = QWheelEvent(QPointF(local), QPointF(pin.mapToGlobal(local)), pixels, angle,
                            Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.ScrollUpdate, False)
        self.qt.sendEvent(pin, event)


class PinInteractionTests(PinInteractionFixture):
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

    def test_a_minimum_size_pin_can_still_be_dragged_and_hidden(self):
        image = QImage(20, 20, QImage.Format.Format_ARGB32)
        image.fill(QColor("#2868bd"))
        pin_id = self.application.open_pin(image, QPoint(120, 135))
        pin = self.application.pin_windows[pin_id]
        self.qt.processEvents()
        start = QPoint(2, 12)
        self.hover(pin, start)
        self.assertFalse(pin.dismiss_button.geometry().contains(start))
        self.drag(pin, start, start + QPoint(28, 36))
        self.assertEqual(pin.pos(), QPoint(148, 171))
        self.hover(pin, start)
        self.click(pin.dismiss_button)
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
        self.wheel(pin, QPoint(120, 0))
        self.assertEqual(pin.state["zoom"], 1.0)
        self.wheel(pin, QPoint(), Qt.KeyboardModifier.MetaModifier, pixels=QPoint(0, 14))
        self.assertEqual(pin.state["opacity"], 85)
        self.assertEqual(pin.state["zoom"], 1.0)
        self.wheel(pin, QPoint(0, -120), Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(pin.state["opacity"], 80)

    def test_mouse_move_resize_zoom_rotation_thumbnail_reset_and_hide(self):
        _, pin = self.open_pin(QPoint(80, 100), width=170, state={"opacity": 72})
        _, neighbor = self.open_pin(QPoint(300, 100), width=85)
        self.drag(pin, QPoint(30, 36), QPoint(77, 36))
        self.assertEqual(pin.frameGeometry().right() + 1, neighbor.frameGeometry().left())
        self.assertEqual(pin.pos().y(), neighbor.pos().y())
        before = pin.size()
        corner = pin.rect().bottomRight() - QPoint(3, 3)
        self.drag(pin, corner, corner + QPoint(22, 22))
        self.assertEqual(pin.width(), before.width() + 22)
        self.assertEqual(pin.pos().x(), neighbor.pos().x() - before.width())

        self.wheel(pin, QPoint(0, 120))
        self.assertGreater(pin.width(), before.width() + 22)
        self.wheel(pin, QPoint(0, 120), Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(pin.state["rotation"], 90)
        self.assertEqual(pin.view.size(), pin.image.size().transposed())

        QTest.mouseDClick(pin.windowHandle(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                          pos=QPoint(35, 35))
        self.assertTrue(pin.state["thumbnail"])
        self.assertLessEqual(max(pin.width(), pin.height()), 132)
        self.click(pin, QPoint(35, 35), Qt.MouseButton.MiddleButton)
        self.assertEqual(pin.state["zoom"], 1.0)
        self.assertEqual(pin.state["opacity"], 100)
        self.assertEqual(pin.state["rotation"], 90)
        self.assertFalse(pin.state["thumbnail"])
        QTest.mouseDClick(pin.windowHandle(), Qt.MouseButton.LeftButton, pos=QPoint(35, 35))
        self.assertFalse(pin.isVisible())
        self.assertFalse(pin.state["visible"])

    def test_shift_drag_locks_the_axis_and_alt_drag_bypasses_snapping(self):
        _, pin = self.open_pin(QPoint(80, 100), width=170)
        _, neighbor = self.open_pin(QPoint(300, 100), width=85)
        start = QPoint(30, 36)
        self.drag(pin, start, start + QPoint(47, 0), modifiers=Qt.KeyboardModifier.AltModifier)
        self.assertEqual(pin.pos(), QPoint(127, 100))
        self.assertEqual(neighbor.pos().x() - (pin.frameGeometry().right() + 1), 3)
        self.drag(pin, start, start + QPoint(-28, 17), modifiers=Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(pin.pos(), QPoint(99, 100))

    def test_appearance_menu_controls_image_transform_background_and_window_flags(self):
        app = self.application
        source = QImage(75, 50, QImage.Format.Format_ARGB32)
        source.fill(QColor("#204060"))
        source.setPixelColor(0, 0, QColor("#80a02030"))
        source.setPixelColor(0, 49, QColor("#306080"))
        pin_id = app.open_pin(source, QPoint(120, 135))
        pin = app.pin_windows[pin_id]
        self.qt.processEvents()

        self.choose_appearance(pin, "Flip horizontally", "水平翻转")
        self.assertEqual(pin.view.pixelColor(74, 0), source.pixelColor(0, 0))
        self.choose_appearance(pin, "Flip vertically", "垂直翻转")
        self.assertEqual(pin.view.pixelColor(74, 49), source.pixelColor(0, 0))
        self.choose_appearance(pin, "Grayscale", "灰度")
        gray = pin.view.pixelColor(74, 49)
        self.assertEqual(gray.red(), gray.green())
        self.assertEqual(gray.green(), gray.blue())
        self.assertEqual(gray.alpha(), source.pixelColor(0, 0).alpha())
        self.choose_appearance(pin, "Grayscale", "灰度")
        self.choose_appearance(pin, "Invert colors", "反色")
        self.assertEqual(pin.view.pixelColor(37, 25), QColor("#dfbf9f"))
        self.choose_appearance(pin, "Rotate clockwise", "顺时针旋转")
        self.assertEqual(pin.view.size(), source.size().transposed())
        self.assertEqual(pin.state["rotation"], 90)

        self.choose_appearance(pin, "Checkerboard background", "棋盘背景")
        self.assertEqual(pin.state["background"], "checkerboard")
        with patch.object(QColorDialog, "getColor", side_effect=[QColor(), QColor("#a0306040")]):
            self.choose_appearance(pin, "Background color…", "背景颜色…")
            self.assertEqual(pin.state["background"], "checkerboard")
            self.choose_appearance(pin, "Background color…", "背景颜色…")
            self.assertEqual(pin.state["background"], "#a0306040")
        self.choose_appearance(pin, "Transparent background", "透明背景")
        self.assertEqual(pin.state["background"], "")

        self.choose_appearance(pin, "Always on top", "始终置顶")
        self.assertFalse(pin.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertTrue(pin.isVisible())
        self.choose_appearance(pin, "Thumbnail", "缩略图")
        self.assertTrue(pin.state["thumbnail"])
        self.choose_appearance(pin, "Reset size and opacity", "重置尺寸与透明度")
        self.assertFalse(pin.state["thumbnail"])
        self.choose_appearance(pin, "Click through", "鼠标穿透")
        self.assertTrue(pin.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        self.choose_tray_item("Disable click-through for all pins", "关闭所有贴图的鼠标穿透")
        self.assertFalse(pin.windowFlags() & Qt.WindowType.WindowTransparentForInput)

    def test_display_width_uses_the_visible_thumbnail_crop(self):
        _, pin = self.open_pin(QPoint(120, 135), width=240)
        with patch.object(QInputDialog, "getInt", side_effect=[(192, True), (86, False), (86, True)]) as width_dialog:
            self.choose_appearance(pin, "Display width…", "显示宽度…")
            self.assertEqual(pin.width(), 192)
            self.assertEqual(width_dialog.call_args.args[-2:], (20, 1920))
            self.drag(pin, QPoint(15, 16), QPoint(85, 58), Qt.MouseButton.RightButton)
            self.assertTrue(pin.state["thumbnail"])
            width_before = pin.width()
            self.choose_appearance(pin, "Display width…", "显示宽度…")
            self.assertEqual(pin.width(), width_before)
            self.choose_appearance(pin, "Display width…", "显示宽度…")
            self.assertEqual(pin.width(), 86)
            self.assertTrue(pin.state["thumbnail"])
            self.assertEqual(width_dialog.call_args.args[-2:], (20, 132))

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

    def test_group_manager_create_reorder_move_pin_and_switch_from_tray(self):
        app = self.application
        app.dashboard.tabs.setCurrentIndex(2)
        self.click(self.button(app.dashboard.tabs.currentWidget(), "Manage groups…", "管理分组…"))
        dialog, = app.dialogs
        groups = dialog.findChild(QListWidget)
        new = self.button(dialog, "New…", "新建…")
        with patch.object(QInputDialog, "getText", side_effect=[("", False), ("Work", True), ("Review", True)]):
            self.click(new)
            self.assertEqual(groups.count(), 1)
            self.click(new)
            self.click(new)
        self.assertEqual(app.groups(), ["Default", "Work", "Review"])
        self.click(self.button(dialog, "Up", "上移"))
        self.assertEqual(app.groups(), ["Default", "Review", "Work"])
        self.click(self.button(dialog, "Down", "下移"))
        self.assertEqual(app.groups(), ["Default", "Work", "Review"])
        self.click(groups.viewport(), groups.visualItemRect(groups.item(1)).center())
        self.click(self.button(dialog, "Switch to group", "切换到该组"))
        self.assertEqual(app.group, "Work")
        self.click(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Close))
        self.assertFalse(app.dialogs)

        pin_id, pin = self.open_pin(QPoint(120, 135))
        with patch.object(QInputDialog, "getItem", return_value=("Review", True)) as group_dialog:
            self.show_menu(pin)
            self.choose_menu_item(pin, "Move to group…", "移至分组…")
        self.assertEqual(group_dialog.call_args.args[4], 1)
        self.assertEqual(pin.state["group"], "Review")
        self.assertFalse(pin.isVisible())
        self.assertEqual(next(state for identifier, _, state in app.workspace.pins() if identifier == pin_id)["group"],
                         "Review")

        groups_menu = self.open_submenu(self.show_tray_menu(), "Pin groups", "贴图分组")
        self.click_menu_item(groups_menu, "Review", "Review")
        self.assertEqual(app.group, "Review")
        self.assertTrue(pin.isVisible())


class PinContentInteractionTests(PinInteractionFixture):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if cls.qt.platformName() != "offscreen":
            raise unittest.SkipTest("Uses an isolated offscreen clipboard")

    def setUp(self):
        super().setUp()
        self.clipboard = self.qt.clipboard()
        self.clipboard.clear()
        self.addCleanup(self.clipboard.clear)
        self.application.workspace.set("save_folder", str(self.directory))

    def click_pin_clipboard(self):
        app = self.application
        before = set(app.pin_windows)
        self.click(self.button(app.dashboard.tabs.widget(0), "Pin clipboard content", "悬浮剪贴板内容"))
        pin_id, = set(app.pin_windows) - before
        return app.pin_windows[pin_id]

    def test_clipboard_image_rich_text_color_edit_and_replace(self):
        image = QImage(55, 42, QImage.Format.Format_RGB32)
        image.fill(QColor("#2868bd"))
        self.clipboard.setImage(image)
        pin = self.click_pin_clipboard()
        self.assertEqual(pin.image, image)

        mime = QMimeData()
        mime.setText("Formatted text")
        mime.setHtml("<b>Formatted text</b>")
        self.clipboard.setMimeData(mime)
        rich = self.click_pin_clipboard()
        self.assertEqual(rich.state["source_text"], "Formatted text")
        self.assertFalse(rich.image.isNull())
        self.clipboard.clear()
        self.show_menu(rich)
        self.choose_menu_item(rich, "Copy original text", "复制原始文字")
        self.assertEqual(self.clipboard.text(), "Formatted text")

        self.clipboard.setText("#a14350")
        color = self.click_pin_clipboard()
        self.assertEqual(color.image.pixelColor(10, 10), QColor("#a14350"))
        with patch.object(QColorDialog, "getColor", side_effect=[QColor(), QColor("#2f7f80")]):
            self.show_menu(color)
            self.choose_menu_item(color, "Edit color…", "编辑色值…")
            self.assertEqual(color.state["color_card"], "#A14350")
            self.show_menu(color)
            self.choose_menu_item(color, "Edit color…", "编辑色值…")
            self.assertEqual(color.state["color_card"], "#2F7F80")
            self.assertEqual(color.image.pixelColor(10, 10), QColor("#2f7f80"))

        self.clipboard.clear()
        self.show_menu(color)
        self.choose_menu_item(color, "Replace from clipboard", "用剪贴板内容替换")
        self.assertEqual(color.state["color_card"], "#2F7F80")
        self.clipboard.setImage(image)
        self.show_menu(color)
        self.choose_menu_item(color, "Replace from clipboard", "用剪贴板内容替换")
        self.assertEqual(color.image, image)
        self.assertNotIn("color_card", color.state)
        self.show_menu(color)
        self.assertNotIn(self.application.tr("Edit color…", "编辑色值…"),
                         [action.text() for action in color.menu.actions()])

    def test_clipboard_gif_file_animates_and_pause_survives_hide_restore(self):
        # Two distinct frames with a 300 ms delay and a continuous loop.
        payload = ("R0lGODlhAgACAIEAANweMgAAAAAAAAAAACH/C05FVFNDQVBFMi4wAwEAAAAh+QQAHgAAACwAAAAAAgACAAAIBgABCAQQEAA"
                   "h+QQBHgABACwAAAAAAgACAIEooFoAAAAAAAAAAAAIBgABCAQQEAA7")
        filename = self.directory / "sample.gif"
        filename.write_bytes(b64decode(payload))
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(filename))])
        self.clipboard.setMimeData(mime)
        pin = self.click_pin_clipboard()
        self.assertTrue(pin.movie.isValid())
        self.assertEqual(pin.movie.frameCount(), 2)
        self.show_menu(pin)
        self.choose_menu_item(pin, "Pause animation", "暂停动画")
        self.assertEqual(pin.movie.state(), QMovie.MovieState.Paused)
        self.show_menu(pin)
        self.choose_menu_item(pin, "Hide", "隐藏")
        self.assertFalse(pin.isVisible())
        self.choose_tray_item("Restore last hidden pin", "恢复最近隐藏的贴图")
        self.assertTrue(pin.isVisible())
        self.assertEqual(pin.movie.state(), QMovie.MovieState.Paused)
        self.show_menu(pin)
        self.choose_menu_item(pin, "Pause animation", "暂停动画")
        self.assertEqual(pin.movie.state(), QMovie.MovieState.Running)

    def test_remote_clipboard_opt_in_only_fetches_on_explicit_action(self):
        app = self.application
        url = "https://example.invalid/reference.png"
        self.clipboard.setText(url)
        button = self.button(app.dashboard.tabs.widget(0), "Pin clipboard content", "悬浮剪贴板内容")
        with patch.object(app, "fetch_pin") as fetch:
            pin = self.click_pin_clipboard()
            fetch.assert_not_called()
            self.assertEqual(pin.state["source_text"], url)
            app.workspace.set("allow_remote_images", True)
            QTest.mouseMove(button.window().windowHandle(), button.mapTo(button.window(), button.rect().center()))
            fetch.assert_not_called()
            self.click(button)
            fetch.assert_called_once()
            self.assertEqual(fetch.call_args.args[0], QUrl(url))
        self.assertEqual(len(app.pin_windows), 1)

    def test_pin_keyboard_moves_zooms_changes_opacity_and_picks_color(self):
        _, pin = self.open_pin(QPoint(120, 135), state={"opacity": 80})
        self.click(pin, QPoint(35, 35))
        window = pin.windowHandle()
        start = pin.pos()
        for key, delta in ((Qt.Key.Key_Left, QPoint(-1, 0)), (Qt.Key.Key_Right, QPoint(1, 0)),
                           (Qt.Key.Key_Up, QPoint(0, -1)), (Qt.Key.Key_Down, QPoint(0, 1))):
            QTest.keyClick(window, key)
            start += delta
            self.assertEqual(pin.pos(), start)
        QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(pin.pos(), start + QPoint(10, 0))

        QTest.keyClick(window, Qt.Key.Key_Equal)
        self.assertAlmostEqual(pin.state["zoom"], 1.12)
        QTest.keyClick(window, Qt.Key.Key_Minus)
        self.assertAlmostEqual(pin.state["zoom"], 1.0)
        QTest.keyClick(window, Qt.Key.Key_Plus, Qt.KeyboardModifier.MetaModifier)
        self.assertEqual(pin.state["opacity"], 85)
        QTest.keyClick(window, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(pin.state["opacity"], 80)
        self.assertAlmostEqual(pin.state["zoom"], 1.0)

        with patch("snaploom.pins.QCursor.pos", return_value=pin.mapToGlobal(QPoint(35, 35))):
            QTest.keyPress(window, Qt.Key.Key_Alt)
            self.assertTrue(pin.track_pick)
            QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.AltModifier)
            self.assertEqual(self.clipboard.text(), "#2868BD")
            QTest.keyRelease(window, Qt.Key.Key_Alt)
            self.assertFalse(pin.track_pick)

    def test_copy_save_annotate_and_hide_work_from_pin_menu_and_keys(self):
        app = self.application
        _, pin = self.open_pin(QPoint(120, 135))
        self.show_menu(pin)
        self.choose_menu_item(pin, "Copy image", "复制图像")
        self.assertEqual(self.clipboard.image(), pin.view)
        self.drag(pin, QPoint(16, 16), QPoint(86, 60), Qt.MouseButton.RightButton)
        self.assertTrue(pin.state["thumbnail"])
        self.clipboard.clear()
        self.show_menu(pin)
        self.choose_menu_item(pin, "Copy full image", "复制完整图像")
        self.assertEqual(self.clipboard.image(), pin.view)

        target = self.directory / "complete.png"
        with patch.object(QFileDialog, "getSaveFileName", side_effect=[("", ""), (str(target), "PNG (*.png)")]):
            self.show_menu(pin)
            self.choose_menu_item(pin, "Save full image as…", "另存完整图像…")
            self.assertFalse(target.exists())
            self.click(pin, QPoint(35, 35))
            QTest.keyClick(pin.windowHandle(), Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(QImage(str(target)).size(), pin.view.size())
        self.assertEqual(QImage(str(target)).pixelColor(5, 5), pin.view.pixelColor(5, 5))

        self.clipboard.clear()
        QTest.keyClick(pin.windowHandle(), Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.clipboard.image(), pin.view)
        QTest.keyClick(pin.windowHandle(), Qt.Key.Key_E, Qt.KeyboardModifier.MetaModifier)
        dialog, = app.dialogs
        self.assertEqual(dialog.canvas.snapshot.image.size(), pin.image.size())
        self.click(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Cancel))
        self.assertFalse(app.dialogs)
        QTest.keyClick(pin.windowHandle(), Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier)
        self.assertFalse(pin.isVisible())
        self.assertFalse(pin.state["visible"])

    def test_print_and_recognition_menu_actions_use_the_current_pin_pixels(self):
        app = self.application
        _, pin = self.open_pin(QPoint(120, 135), state={"rotation": 90})
        with patch.object(app, "print_image", side_effect=[False, True]) as print_image:
            for _ in range(2):
                self.show_menu(pin)
                self.choose_menu_item(pin, "Print…", "打印…")
                self.assertTrue(pin.isVisible())
            self.assertEqual([call.args[0] for call in print_image.call_args_list], [pin.view, pin.view])

        with patch("snaploom.app.recognize", side_effect=["<literal text>", "QR value"]) as recognize:
            for en, zh, expected in (("Extract text…", "识别文字…", "<literal text>"),
                                     ("Read QR or barcode…", "识别二维码或条码…", "QR value")):
                self.show_menu(pin)
                self.choose_menu_item(pin, en, zh)
                for _ in range(100):
                    if app.dialogs:
                        break
                    QTest.qWait(10)
                dialog, = app.dialogs
                self.assertEqual(dialog.findChild(QTextEdit).toPlainText(), expected)
                self.click(self.button(dialog, "Copy text", "复制文字"))
                self.assertEqual(self.clipboard.text(), expected)
                self.click(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Close))
                self.assertTrue(pin.isVisible())
        self.assertEqual([call.args for call in recognize.call_args_list], [(pin.view, "ocr"), (pin.view, "barcode")])


if __name__ == "__main__":
    unittest.main()
