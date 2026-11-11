import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QContextMenuEvent, QEnterEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QPushButton, QToolButton

from snaploom.app import Application


class PinInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.qt.setQuitOnLastWindowClosed(False)

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.application = Application(self.qt, Path(directory.name), testing=True)
        self.addCleanup(self.close_application)
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
        menu_event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, pin.mapToGlobal(point))
        self.qt.sendEvent(pin, menu_event)
        self.assertIs(self.qt.activePopupWidget(), pin.menu)

    def hover(self, widget, position):
        event = QEnterEvent(QPointF(position), QPointF(widget.mapTo(widget.window(), position)),
                            QPointF(widget.mapToGlobal(position)))
        self.qt.sendEvent(widget, event)

    def test_hover_does_not_paste_a_link_and_explicit_paste_can_be_hidden_and_restored(self):
        link = "https://example.invalid/capture-help"
        mime = QMimeData()
        mime.setText(link)
        clipboard = SimpleNamespace(mimeData=lambda: mime)
        button = self.application.dashboard.findChild(QPushButton, "Secondary")
        with (
            patch.object(self.application, "fetch_pin") as fetch,
            patch.object(self.qt, "clipboard", return_value=clipboard) as read_clipboard,
        ):
            QTest.mouseMove(button, button.rect().center())
            self.hover(button, button.rect().center())
            QTest.qWait(750)
            self.assertEqual(self.application.pin_windows, {})
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

        with patch.object(self.qt, "clipboard", return_value=clipboard):
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
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


if __name__ == "__main__":
    unittest.main()
